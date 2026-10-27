import hashlib
import logging

from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.cache import cache
from django.db.models import OuterRef, Prefetch, Q, Subquery
from django.db.models.functions import Lower
from django.http import JsonResponse
from django.urls import reverse
from django.utils.decorators import method_decorator
from django.views.generic import View
from django_ratelimit.core import is_ratelimited
from django_ratelimit.decorators import ratelimit

from core.services.tracking import track_site_event
from gamelists.models import GameList
from trophies.psn_manager import PSNManager
from trophies.services.collection_service import badge_type_label
from trophies.services.flag_badges import work_keys_for_concepts
from trophies.services.game_grouping_service import representative_concept_icon_subquery
from trophies.services.sync_service import RefreshOutcome, SyncService
from trophies.util_modules.cache import redis_client
from ..models import BadgeSeries, Concept, Franchise, Game, GroupBadge, Job, Profile

logger = logging.getLogger("psn_api")


def _refresh_response(outcome, profile=None):
    """One JsonResponse shape for every refresh-request surface.

    503 for an outage (site-wide and temporary) and 429 for the cooldown (the caller asked too soon).
    `reason` is on EVERY body, success included, so a client has one field to switch on rather than
    inferring from the status code -- and `seconds_to_next_sync` rides along so it can tick a countdown
    down instead of re-parsing the sentence.

    `profile` is what stops a refusal being a dead end. A cooldown means the hunter EXISTS and is
    viewable right now; only the refresh was declined. Handing back the canonical name and URL lets a
    caller offer the profile anyway, which matters most on the anonymous landing hero, whose entire job
    is getting a stranger onto a profile page. Without it, making this refusal honest turned a working
    search into a red error with no recourse.
    """
    body = {'reason': outcome.reason, 'seconds_to_next_sync': outcome.seconds}
    if profile is not None:
        body['psn_username'] = profile.psn_username
        body['slug'] = reverse('profile_detail', kwargs={'psn_username': profile.psn_username})

    if outcome.ok:
        body.update({'success': True, 'message': outcome.message})
        return JsonResponse(body)

    body['error'] = outcome.message
    status_code = 503 if outcome.reason == SyncService.REFUSED_OUTAGE else 429
    return JsonResponse(body, status=status_code)


def _claimable(profile):
    """Can a visitor who finds this profile still claim it? The landing hero's "That's you? Sign up free to claim
    it." shows only then: no account on it, the rule the link flow refuses on
    (`profile_views._claimed_by_someone_else`). `user` is set only when verification succeeds
    (`VerificationService.link_profile_to_user`), so a profile mid-link is still claimable, as Link PSN would
    still accept it.

    NOT also gated on `account_id`, though it looks like a guard against typos: a name PSN does not know is
    normally DELETED when its first sync fails (`Profile.set_sync_status('error')` drops a row with no
    `account_id`), so an empty `account_id` almost always means "still in the sync queue", and gating on it hid
    the claim from real hunters who searched twice while the queue was busy (audit-caught, after it was added).
    The exception: a first sync cut short by a PSN outage is set `synced` by the outage recovery, not deleted,
    so a typo searched during an outage stays claimable until the unregistered sweep refreshes it. Link PSN's
    own lookup still refuses a name PSN does not know, so the cost is a wasted sign-up, not a wrong link."""
    return profile.user_id is None


def _get_queue_position(profile_id):
    """
    Calculate approximate queue position for a syncing profile.

    Counts how many other profiles started syncing before this one and are
    still active. Uses a Redis pipeline to batch lookups into a single
    round-trip. Returns None if position cannot be determined.
    """
    try:
        my_start = redis_client.get(f"sync_started_at:{profile_id}")
        if not my_start:
            return None

        my_start_time = float(my_start.decode() if isinstance(my_start, bytes) else my_start)
        active_ids = redis_client.smembers('active_profiles')

        other_pids = []
        for pid_bytes in active_ids:
            pid = pid_bytes.decode() if isinstance(pid_bytes, bytes) else str(pid_bytes)
            if str(pid) != str(profile_id):
                other_pids.append(pid)

        if not other_pids:
            return 0

        pipe = redis_client.pipeline(transaction=False)
        for pid in other_pids:
            pipe.get(f"sync_started_at:{pid}")
        results = pipe.execute()

        ahead = 0
        for val in results:
            if not val:
                continue
            try:
                other_time = float(val.decode() if isinstance(val, bytes) else val)
                if other_time < my_start_time:
                    ahead += 1
            except (ValueError, TypeError):
                continue

        return ahead
    except Exception:
        return None


def _pursuit_payload(profile):
    """What the first-sync finale says once the sync lands: the contracts the hunter's history already
    earned, and what claiming them makes them (FV-03; `contract_service.claim_projection`).

    ONLY ON THE SYNCED RESPONSE, which navsync receives about once per sync (it stops polling on it), so
    the syncing polls stay free. Contract detection runs inside `sync_complete`, before the profile is
    marked synced, so the claimables already exist when this is read. A failure degrades to "nothing to
    claim" rather than failing the status poll the whole sync UI depends on."""
    from trophies.services import contract_service
    try:
        # The bare count, not `claimable_summary`: its XP total and peek are not shown here, and this runs
        # on every synced response, including a returning hunter's manual refresh with nothing to claim.
        count = contract_service._claimable_rows(profile).count()
        if not count:
            return {'count': 0}
        projection = contract_service.claim_projection(profile)
        return {
            'count': count,
            # The level NOW as well as after, so the finale states a level only when claiming moves it:
            # one three-job contract pays each job 2,000 XP, short of a level, and "Claim it to reach
            # Level 1" would be a promise of nothing.
            'from_level': contract_service._pursuer_level(profile),
            'level': projection['level'] if projection else None,
            'rank': projection['rank']['label'] if projection else None,
            'top': [{'name': j['name'], 'from': j['from'], 'to': j['to']} for j in (projection or {}).get('top', [])],
        }
    except Exception:
        logger.exception('pursuit payload failed for profile %s', profile.pk)
        return {'count': 0}


class ProfileSyncStatusView(LoginRequiredMixin, View):
    """
    AJAX endpoint for polling profile sync status in navigation hotbar.

    Returns current sync status, progress percentage, cooldown time,
    and queue position when syncing.

    Rate limited to 60 requests per minute per user.
    """
    @method_decorator(ratelimit(key='user', rate='60/m', method='GET'))
    def get(self, request):
        if not hasattr(request.user, 'profile'):
            return JsonResponse({'error': 'No linked profile'}, status=400)

        from django.contrib.humanize.templatetags.humanize import naturaltime
        profile = request.user.profile
        seconds_to_next_sync = profile.get_seconds_to_next_sync()
        logger.debug(f"Sync status check for {profile.psn_username}: {seconds_to_next_sync}s until next sync")
        data = {
            'sync_status': profile.sync_status,
            'sync_progress': profile.sync_progress_value,
            'sync_target': profile.sync_progress_target,
            'sync_percentage': profile.sync_percentage,
            'seconds_to_next_sync': seconds_to_next_sync,
            'psn_outage': bool(redis_client.get('site:psn_outage')),
            'is_finalizing': False,
            'finalize_phase': None,
            # Fresh headline stats so the navbar panel updates live on sync completion
            # (the counts + last-synced are server-rendered at page load, else stale).
            'stats': {
                'plats': profile.total_plats, 'golds': profile.total_golds,
                'silvers': profile.total_silvers, 'bronzes': profile.total_bronzes,
            },
            'last_synced': naturaltime(profile.last_synced) if profile.last_synced else None,
        }

        if profile.sync_status == 'synced':
            data['pursuit'] = _pursuit_payload(profile)

        if profile.sync_status == 'syncing':
            # NOTE (audit-caught): the first-sync tally needs NO query of its own. The
            # per-type denorms (total_plats/golds/silvers/bronzes) climb LIVE during the walk
            # via the EarnedTrophy post_save signal -- only total_trophies waits for finalize.
            # The `stats` block above therefore already carries the growing tally; the hero
            # sums its four figures client-side. Zero extra cost, and definitionally
            # consistent with the finale's numbers.

            # PSN's own totals (written seconds into the sync) ride along so a waiting page
            # that loaded BEFORE they landed can upgrade its greeting live. Additive key;
            # None until the summary exists. Zero extra queries -- the profile is loaded.
            data['psn_found'] = {
                'total': profile.get_total_trophies_from_summary() or 0,
                'plats': (profile.earned_trophy_summary or {}).get('platinum', 0),
                'level': profile.trophy_level or 0,
            } if profile.earned_trophy_summary else None

            # Surface the sync_complete in-progress flag so the UI can show
            # "Finalizing..." instead of leaving the bar parked at 100% while
            # _job_sync_complete runs the post-sync pipeline (health check,
            # badges, milestones, challenges, dashboard cache invalidation).
            data['is_finalizing'] = bool(redis_client.get(f'sync_complete_in_progress:{profile.id}'))

            if data['is_finalizing']:
                # Sub-phase string written by _job_sync_complete at each
                # boundary so the UI can show "Verifying...", "Badges...", etc.
                # Same lifetime as sync_complete_in_progress, so we only check
                # for it inside this branch.
                phase_raw = redis_client.get(f'finalize_phase:{profile.id}')
                if phase_raw:
                    data['finalize_phase'] = phase_raw.decode() if isinstance(phase_raw, bytes) else phase_raw
            else:
                # Queue position is only meaningful before finalization. Skip
                # the Redis pipeline lookup once we're past 100% to avoid the
                # cost (and the UI hides the indicator anyway when finalizing).
                data['queue_position'] = _get_queue_position(profile.id)

        return JsonResponse(data)

class ProfileSuggestView(View):
    """
    AJAX typeahead for the navbar search bar.

    Suggests EXISTING tracked profiles whose PSN username starts with the
    query, ranked by trophy weight (most prominent hunter first) so the
    dropdown surfaces the profile the searcher most likely means. Read-only
    public lookup (profiles are public pages), so it's open to anonymous
    users; rate-limited like the sync search it sits beside. The client falls
    back to the add-and-sync flow (SearchSyncProfileView) when the typed name
    isn't tracked yet.

    Prefix match only (istartswith): the intuitive typeahead behaviour, and it
    bounds the candidate set. (istartswith compiles to a case-insensitive LIKE
    that a plain btree on psn_username can't serve, so Postgres leans on the
    total_plats index scan + filter; a rare prefix may scan a way before
    collecting 8 rows, but the LIMIT keeps this a small, whale-safe query
    regardless of table size.)
    """
    def get(self, request):
        # Read-only + cheap, so a more generous bucket than the sync search:
        # authed users are user-keyed, anon IP-keyed.
        if request.user.is_authenticated:
            limited = is_ratelimited(
                request, group='profile_suggest:user', key='user',
                rate='30/m', method='GET', increment=True,
            )
        else:
            limited = is_ratelimited(
                request, group='profile_suggest:ip', key='ip',
                rate='20/m', method='GET', increment=True,
            )
        if limited:
            return JsonResponse({'results': [], 'throttled': True}, status=429)

        q = request.GET.get('q', '').strip()
        if len(q) < 2:
            return JsonResponse({'results': []})
        # Bound the query so a giant string can't stress the LIKE comparison
        # (PSN usernames max at 16 chars; the slack covers paste accidents).
        if len(q) > 64:
            return JsonResponse({'error': 'Query too long'}, status=400)

        from django.urls import reverse
        matches = (
            Profile.objects
            .filter(psn_username__istartswith=q)
            .order_by('-total_plats', 'psn_username')
            .values('psn_username', 'display_psn_username', 'avatar_url', 'total_plats')
            [:8]
        )
        results = [
            {
                'psn_username': m['psn_username'],
                'display': m['display_psn_username'] or m['psn_username'],
                'avatar_url': m['avatar_url'] or '',
                'plats': m['total_plats'],
                'url': reverse('profile_detail', kwargs={'psn_username': m['psn_username']}),
            }
            for m in matches
        ]
        return JsonResponse({'results': results})


def _badge_art(series):
    """(image url, is_avatar) for the series' representative live edition: its subject art, else ''
    (client falls back to the badge glyph). Resolved on the edition because an edition can override
    its series' image and a series can borrow another's; art_layers() answers both. Its static
    placeholder is dropped rather than shown: the glyph is the dropdown's no-art state. `is_avatar`
    marks a user badge wearing its submitter's avatar, which the client circle-masks like a hunter's."""
    edition = series.representative_group_badge
    if edition is None:
        return '', False
    art = edition.art_layers()
    if not art['has_custom_image']:
        return '', False
    return art['main'], art['is_avatar']


class SiteSuggestView(View):
    """
    Universal navbar typeahead. Suggests Games, Badges, Jobs, Franchises, public Game
    Lists and tracked Hunters matching the query, grouped by type, each linking to its
    detail page. (Jobs and Game Lists joined 2026-10-27, review IA-02: neither could be
    found by name anywhere on the site.)
    Widens the profile-only ProfileSuggestView; the client keeps the add-and-sync
    fallback (SearchSyncProfileView) for Online IDs that aren't tracked yet.

    Whale-safe by construction: this is global reference data, not per-user
    aggregation. Every group query is DB-side, bounded to PER_GROUP rows, and
    the whole response is identical for every viewer -> cached in Redis by
    normalized query for a short TTL, so hot prefixes never touch Postgres.

    Title matching is substring (icontains), served sub-ms by the pg_trgm GIN
    indexes on Concept.unified_title / Franchise.name (migration 0257). BadgeSeries.name
    has no such index: it is one row per badge, small enough to scan; nor do Job (25
    rows) or GameList.name. A query that matches no list scans every public list (the
    LIMIT bounds the rows returned, not the scan): fine at today's list count, and the
    place to add a trigram index if lists grow into the tens of thousands. Hunters stay
    prefix (istartswith), like ProfileSuggestView.
    """
    PER_GROUP = 5
    CACHE_TTL = 60  # seconds; global catalog, a short lag on freshly-synced profiles is fine.

    def get(self, request):
        # Rate-limit BEFORE the cache lookup so a throttled caller still gets 429.
        if request.user.is_authenticated:
            limited = is_ratelimited(
                request, group='site_suggest:user', key='user',
                rate='30/m', method='GET', increment=True,
            )
        else:
            limited = is_ratelimited(
                request, group='site_suggest:ip', key='ip',
                rate='20/m', method='GET', increment=True,
            )
        if limited:
            return JsonResponse({'groups': [], 'throttled': True}, status=429)

        q = request.GET.get('q', '').strip()
        if len(q) < 2:
            return JsonResponse({'groups': []})
        if len(q) > 64:
            return JsonResponse({'error': 'Query too long'}, status=400)

        # Hash the normalized query so spaces/unicode/length can't produce an invalid
        # or oversized cache key (the response is identical for every viewer).
        cache_key = f'sitesuggest:v4:{hashlib.md5(q.lower().encode()).hexdigest()}'
        cached = cache.get(cache_key)
        if cached is not None:
            return JsonResponse(cached)

        # Hunters LAST, beside the add-and-sync row that follows the list.
        groups = [g for g in (
            self._games(q), self._badges(q), self._jobs(q), self._franchises(q), self._lists(q), self._hunters(q),
        ) if g['items']]
        payload = {'groups': groups}
        cache.set(cache_key, payload, self.CACHE_TTL)
        return JsonResponse(payload)

    def _games(self, q):
        # Search at the CONCEPT level (dedups a game's PS4/PS5/regional Game rows to
        # one entry) and link to the concept's most-played Game -- see the click-target
        # rationale in the plan. One correlated subquery yields the link target's
        # np_communication_id, a second its played_count for popularity ordering.
        # Fetch instances (not .values()) so Concept.get_cover_url() gives the same
        # IGDB-first cover the game cards use; select_related the match but DEFER its
        # ~30KB raw_response blob (the whale-OOM guard) -- get_cover_url only reads
        # is_trusted + igdb_cover_image_id.
        rep = Game.objects.filter(concept=OuterRef('pk')).order_by('-played_count')
        concepts = (
            Concept.objects
            .filter(unified_title__icontains=q)
            .annotate(
                npc=Subquery(rep.values('np_communication_id')[:1]),
                pop=Subquery(rep.values('played_count')[:1]),
            )
            .filter(npc__isnull=False)  # only concepts with a linkable Game
            .select_related('igdb_match')
            .defer('igdb_match__raw_response')
            .order_by('-pop', 'unified_title')[:self.PER_GROUP]
        )
        concepts = list(concepts)
        # A result is a whole GAME, so its badges are the ones every version carries (owner, 2026-10-24): one
        # bounded query off the matches already joined, cached with the payload.
        flags = work_keys_for_concepts(concepts)
        items = [
            {
                'label': c.unified_title,
                'image': c.get_cover_url('cover_small') or '',   # IGDB-first, mirrors the game cards
                # The concept Game page is the primary result (Games/Trophy Lists IA): search
                # already dedupes to concepts, so the destination is the concept's own page.
                # game_page_url reads igdb_match, select_related'd above (raw_response deferred).
                'url': c.game_page_url(),
                'flags': list(flags.get(c.pk, ())),
            }
            for c in concepts
        ]
        return {'type': 'game', 'label': 'Games', 'items': items}

    def _badges(self, q):
        # One row per BadgeSeries with a live edition: the set BadgeDetailView serves (it
        # 404s a series with none), and the page is keyed on series_slug. A '' slug can
        # still be written past the SlugField, and would NoReverseMatch the whole endpoint,
        # so it is excluded. The type tag tells same-named sister badges apart (a
        # franchise badge and a series badge for one subject).
        # The editions are prefetched with their platform_group (representative_group_badge
        # picks the live one in Python), and the series carries the borrowed art and a user
        # badge's submitter, so _badge_art reads no further rows: two queries for the group.
        series = (
            BadgeSeries.objects.live()
            .filter(name__icontains=q)
            .exclude(series_slug='')
            .select_related('artwork_source', 'submitted_by')
            .prefetch_related(Prefetch('group_badges',
                                       queryset=GroupBadge.objects.select_related('platform_group')))
            .order_by(Lower('name'), 'id')[:self.PER_GROUP]
        )
        items = []
        for s in series:
            image, is_avatar = _badge_art(s)
            items.append({
                'label': s.name,
                'sublabel': badge_type_label(s.badge_type),
                'image': image,
                'is_avatar': is_avatar,
                'url': reverse('badge_detail', kwargs={'series_slug': s.series_slug}),
            })
        return {'type': 'badge', 'label': 'Badges', 'items': items}

    def _jobs(self, q):
        # The discipline is the type tag, the way a franchise row says Franchise or Series.
        rows = Job.objects.filter(name__icontains=q).order_by('name')[:self.PER_GROUP]
        items = [
            {
                'label': job.name,
                'sublabel': job.get_discipline_display(),
                'url': reverse('job_detail', kwargs={'slug': job.slug}),
            }
            for job in rows
        ]
        return {'type': 'job', 'label': 'Jobs', 'items': items}

    def _franchises(self, q):
        # source_type disambiguates same-named franchise vs collection ("Series").
        # slug is unique + non-null, but guard the empty case for parity so a stray
        # blank slug can't NoReverseMatch the endpoint. `cover` = a member game's PSN
        # portrait cover (the same representative-cover subquery the franchise browse
        # cards use); misses fall back to the type glyph client-side.
        rows = (
            Franchise.objects
            .filter(name__icontains=q)
            .exclude(slug='')
            .annotate(cover=representative_concept_icon_subquery(
                through_path='concept__concept_franchises__franchise',
                # Skip curated-out links so the thumbnail matches the browse-card cover.
                extra_filter=Q(concept__concept_franchises__is_excluded=False)))
            .order_by('name')
            .values('name', 'slug', 'source_type', 'cover')[:self.PER_GROUP]
        )
        items = [
            {
                'label': r['name'],
                'sublabel': 'Series' if r['source_type'] == 'collection' else 'Franchise',
                'image': r['cover'] or '',
                'url': reverse('franchise_detail', kwargs={'slug': r['slug']}),
            }
            for r in rows
        ]
        return {'type': 'franchise', 'label': 'Franchises', 'items': items}

    def _lists(self, q):
        # Public lists only (`.public()`, the one supported read of somebody else's list), and never one a
        # moderator hid the words of: a match would confirm the hidden name contains the query, the leak the
        # browse page's own search closes the same way. The response is cached for every viewer alike, so a
        # hunter's private lists could not be offered here even to them. The cache does lag a change by up to
        # CACHE_TTL: a list made private or deleted can still be offered (its page then 404s), and one a moderator
        # hides can still show its old name for that minute. Accepted: the browse grid has no such lag only
        # because it is not cached. Most-liked first.
        rows = (
            GameList.objects.public()
            .filter(name__icontains=q, text_hidden=False)
            .order_by('-like_count', '-game_count', 'id')[:self.PER_GROUP]
        )
        items = [
            {
                'label': gl.display_name,
                'sublabel': f'{gl.game_count} game{"" if gl.game_count == 1 else "s"}',
                'url': reverse('list_detail', kwargs={'list_id': gl.pk}),
            }
            for gl in rows
        ]
        return {'type': 'list', 'label': 'Game Lists', 'items': items}

    def _hunters(self, q):
        # Prefix match ranked by trophy weight, as ProfileSuggestView does -- but with an EXACT match
        # sorted to the front and flagged.
        #
        # Two things were wrong without that. Searching a hunter's full name could fail to show them:
        # `istartswith` capped at PER_GROUP and ranked by platinums means five `daniel*` hunters bury
        # the actual `dan`. And the client had no way to know a typed name was already tracked, so the
        # add row offered to "Sync X, a new hunter" for hunters we have had for years.
        #
        # TWO queries, deliberately, rather than one sorted by a CASE. Making the exact match the
        # leading sort key put a non-indexable expression first, which stops the planner taking a top-N
        # over `profile_total_plats_idx` and forces it to materialise and sort every prefix match --
        # per keystroke, and worst for the one-character queries that match the most rows. The exact
        # lookup below instead hits the unique index (`.lower()` + exact; `Profile.save()` lowercases
        # this column), and the prefix query keeps the plan it always had.
        fields = ('psn_username', 'display_psn_username', 'avatar_url', 'total_plats')
        needle = q.strip().lower()

        exact = list(Profile.objects.filter(psn_username=needle).values(*fields)[:1])
        prefix = list(
            Profile.objects
            .filter(psn_username__istartswith=q)
            .exclude(psn_username=needle)
            .order_by('-total_plats', 'psn_username')
            .values(*fields)
            [:self.PER_GROUP - len(exact)]
        )
        rows = [dict(r, is_exact=True) for r in exact] + [dict(r, is_exact=False) for r in prefix]
        items = [
            {
                'label': r['display_psn_username'] or r['psn_username'],
                'avatar_url': r['avatar_url'] or '',
                'plats': r['total_plats'],
                'url': reverse('profile_detail', kwargs={'psn_username': r['psn_username']}),
                # The client reads this to label its add row honestly: a tracked hunter gets "Refresh",
                # not "Sync a new hunter". Server-authoritative rather than a client-side string
                # comparison, because `label` is the DISPLAY name and can differ from `psn_username`.
                'exact': r['is_exact'],
            }
            for r in rows
        ]
        return {'type': 'profile', 'label': 'Hunters', 'items': items}


class TriggerSyncView(LoginRequiredMixin, View):
    """
    AJAX endpoint to manually trigger profile sync from navigation hotbar.

    Validates cooldown period and initiates sync via job queue.
    Returns error if sync is already in progress or cooldown is active.
    """
    def post(self, request):
        if not hasattr(request.user, 'profile'):
            return JsonResponse({'error': 'No linked profile'}, status=400)

        profile = request.user.profile
        return _refresh_response(SyncService.request_refresh(profile), profile)

class SearchSyncProfileView(View):
    """
    AJAX endpoint to search for and add PSN profiles to the database.

    Creates profile if it doesn't exist and initiates initial sync.
    If profile exists, triggers a sync update.
    Available to everyone via the navbar search dropdown.

    Rate limits differ by auth state to balance accessibility against the
    PSN-token cost of a sync:
      - Authenticated users: 15 requests/min, keyed by user.id.
      - Anonymous users: 3 requests/min, keyed by IP.
    A signed-in user behind a shared/NAT'd IP isn't penalized by the anon
    cap because the user-keyed bucket fires first and skips the IP check.
    """
    def post(self, request):
        if request.user.is_authenticated:
            limited = is_ratelimited(
                request, group='sync_search:user', key='user',
                rate='15/m', method='POST', increment=True,
            )
        else:
            limited = is_ratelimited(
                request, group='sync_search:ip', key='ip',
                rate='3/m', method='POST', increment=True,
            )
        if limited:
            return JsonResponse({
                'error': 'Too many searches. Please wait a minute and try again.',
                'reason': SyncService.REFUSED_RATE_LIMIT,
            }, status=429)

        # Checked here as well as inside `request_refresh`, because it has to guard the CREATE path
        # too -- `initial_sync` is a silent no-op during an outage, so without this a hunter would be
        # told their brand-new profile was queued when nothing had been. Routed through
        # `_refresh_response` so this 503 carries the same `reason` as every other refusal; it was the
        # one body in this view shaped differently from its neighbours.
        if PSNManager.is_psn_outage_active():
            return _refresh_response(
                RefreshOutcome(False, SyncService.REFUSED_OUTAGE, SyncService.OUTAGE_MESSAGE, 0)
            )

        psn_username = request.POST.get('psn_username', '').strip()
        if not psn_username:
            return JsonResponse({'error': 'Username required'}, status=400)

        is_new = False
        try:
            # `.lower()` + exact, not `iexact`: see the note on `AddSyncStatusView` below. Cheaper, and
            # it also makes the lookup agree with the CREATE two lines down, which has always stored the
            # lowercased form -- a mismatch there is how a "not found" can race into a duplicate row.
            profile = Profile.objects.get(psn_username=psn_username.lower())
        except Profile.DoesNotExist:
            profile = Profile.objects.create(
                psn_username=psn_username.lower(),
            )
            is_new = True

        result_tag = 'new' if is_new else 'existing'
        searcher_pid = getattr(getattr(request.user, 'profile', None), 'id', None)
        object_id = f'{psn_username.lower()}|{result_tag}|pid:{searcher_pid}'
        track_site_event('sync_search', object_id, request)

        if is_new:
            # No cooldown on a profile that has never synced, so there is nothing to refuse. `reason`
            # is still on the body: every response from this endpoint carries it, so a client has one
            # field to switch on rather than three shapes to tell apart.
            PSNManager.initial_sync(profile)
            return JsonResponse({
                'success': True,
                'reason': '',
                'message': f'Added and syncing {psn_username}',
                'psn_username': profile.psn_username,
                'slug': reverse('profile_detail', kwargs={'psn_username': profile.psn_username}),
            })

        # Refreshing a hunter we ALREADY TRACK needs an account; adding one we do not stays open. The
        # rule cannot be held half-way: the profile-page control is members-only, so leaving this door
        # open to refresh the same profile would just move it. Adding is the anonymous landing pitch and
        # is untouched, which is why this check sits AFTER the `is_new` branch has already returned.
        #
        # With the slug, for the same reason the cooldown refusal carries it: the hunter exists and is
        # viewable, so a visitor who typed a tracked name must still be handed the profile rather than
        # bounced. That is the anonymous hero's whole promise.
        if not request.user.is_authenticated:
            return JsonResponse({
                'error': 'Sign in to refresh a hunter we already track.',
                'reason': SyncService.REFUSED_SIGN_IN,
                'seconds_to_next_sync': 0,
                'psn_username': profile.psn_username,
                'slug': reverse('profile_detail', kwargs={'psn_username': profile.psn_username}),
                'claimable': _claimable(profile),
            }, status=403)

        # One shape for BOTH outcomes of a refresh, so `already_syncing` (which is an ok) is not
        # smuggled out through a success body with no `reason`. The refusal this view used to swallow
        # lived here: it called `attempt_sync()` and discarded the return, so asking to refresh a
        # hunter synced ten minutes ago reported success for a sync that never started. Passing the
        # profile is what keeps a cooldown refusal from being a dead end -- the anonymous hero's "type
        # a name, get a profile" promise would otherwise break on any account synced in the last hour.
        return _refresh_response(SyncService.request_refresh(profile), profile)

class RequestProfileRefreshView(View):
    """Ask for ANOTHER hunter's profile to be refreshed from PSN.

    The gap this fills: a hunter looking at a profile could read "Synced 5 days ago" and had no way to
    do anything about it. The capability already existed -- `SearchSyncProfileView` re-syncs any named
    hunter -- but only through the navbar search, whose row reads "Sync X, a new hunter" and so is both
    undiscoverable and actively misdescribed for a profile we already track.

    Why it matters more than impatience: the every-15-minutes cron only picks up an unlinked,
    non-Discord-verified profile once it is SEVEN DAYS stale (`refresh_profiles.py`), and that is most
    of the hunters anyone browses. For those, asking is the only thing that makes them current.

    SIGNED-IN ONLY, and the rule is: open to ADD a hunter nobody tracks yet, signed-in to refresh one
    we already have. Adding is the anonymous landing pitch and stays open; refreshing a tracked profile
    is not part of that pitch, and tying it to an account is what makes a per-caller rate limit mean
    anything at all.

    Abuse is bounded twice, and the important bound is not this view's: the per-profile cooldown on
    `last_synced` means any number of hunters asking about the same profile inside its window produce
    ONE refresh, so the PSN cost per profile is capped however many people ask. The rate limit here
    bounds how many DIFFERENT profiles one account can spend tokens on.
    """
    def post(self, request, psn_username):
        # An explicit JSON 403, NOT `LoginRequiredMixin`. The mixin 302s to the login page, `fetch`
        # follows redirects by default, and the login HTML comes back 200 -- so `API.request` sees an ok
        # response, returns the page as a STRING, and the control reads `data.reason` off it as
        # `undefined`. It then announced "Updating now", polled, read the unchanged status and finished
        # with "Updated just now. Reload to see it." Nothing had been queued, and the hunter was told
        # both that their refresh landed and that the page they were looking at was stale.
        #
        # Reachable by any session that expires between page load and click, which is exactly when the
        # control is on screen: it is rendered only for an authenticated request.
        if not request.user.is_authenticated:
            return JsonResponse({
                'error': 'Sign in to refresh a hunter.',
                'reason': SyncService.REFUSED_SIGN_IN,
                'seconds_to_next_sync': 0,
            }, status=403)

        if is_ratelimited(
            request, group='refresh_request:user', key='user',
            rate='10/m', method='POST', increment=True,
        ):
            return JsonResponse({
                'error': 'Too many refresh requests. Please wait a minute and try again.',
                'reason': SyncService.REFUSED_RATE_LIMIT,
            }, status=429)

        try:
            # `.lower()` + exact, not `iexact`: on Postgres `iexact` compiles to
            # `UPPER(col) = UPPER(%s)`, which neither the unique constraint nor `psn_username_idx`
            # can serve, so every call sequentially scanned the whole Profile table.
            # `profile_views.py` records the same trap for the same column. Correct as well as
            # cheaper, because `Profile.save()` lowercases this field unconditionally.
            profile = Profile.objects.get(psn_username=psn_username.strip().lower())
        except Profile.DoesNotExist:
            # 404, not 400: the name is well-formed, we simply do not track that hunter. Adding them is
            # the navbar search's job, and that is open to everyone.
            return JsonResponse({'error': 'That hunter is not tracked yet.'}, status=404)

        # Recorded like `SearchSyncProfileView`'s own searches, so this surface is not invisible to the
        # funnel the other one feeds. Tagged `profile_page` to tell the two apart: which surface hunters
        # actually use to ask is the question this feature exists to answer.
        track_site_event(
            'sync_search',
            f'{profile.psn_username}|refresh_request|pid:{getattr(getattr(request.user, "profile", None), "id", None)}',
            request,
        )

        # `jump_queue`: a person is sitting looking at the page. The cron sweep lands hundreds of
        # `profile_refresh` jobs in the same orchestrator queue every 15 minutes, so without this a
        # requested refresh waits behind all of them.
        return _refresh_response(SyncService.request_refresh(profile, jump_queue=True), profile)


class AddSyncStatusView(View):
    """
    AJAX endpoint to poll sync status after adding a new profile via the
    navbar search dropdown.

    Returns sync status, account ID, and profile URL. Read-only DB lookup,
    no PSN tokens consumed, so it's open to anonymous users to pair with
    the open SearchSyncProfileView.

    THE MOST-POLLED ENDPOINT ON THE SITE, which is why the lookup below is written the way it is.
    Three clients hit it on a timer: `navbar-search.js` and `landing.js` every 2.5s (24/min each) and
    `refresh-control.js` every 4s (15/min), and one viewer can have two of them running at once.

    DELIBERATELY NOT RATE-LIMITED, unlike its login-gated sibling `ProfileSyncStatusView` (60/m per
    user). Anonymous polling here is a first-class use, not abuse: the landing hero IS the pitch to a
    stranger, and it polls at 24/min. A cap tight enough to bound scraping would therefore have to be
    keyed on IP, and any household, office or mobile carrier sharing one address would hit it -- the
    front door is the worst thing on the site to break. After the indexed lookup below, a request here
    is one primary-key read of figures the profile page already prints unauthenticated to everybody, so
    scraping it is not materially cheaper than crawling the public, sitemapped profile pages.
    """
    def get(self, request):
        psn_username = request.GET.get('psn_username', '').strip()
        if not psn_username:
            return JsonResponse({'error': 'Username required'}, status=400)

        try:
            # `.lower()` + exact, NOT `iexact`. On Postgres `iexact` compiles to
            # `UPPER("psn_username") = UPPER(%s)`, which neither the unique constraint nor
            # `psn_username_idx` can serve -- so every poll sequentially scanned the entire Profile
            # table, 15-24 times a minute for every viewer with a page open.
            #
            # Correct as well as cheap: `Profile.save()` lowercases this column unconditionally, so a
            # stored value is never anything else. `profile_views.py` records the same trap for the same
            # column, and `RequestProfileRefreshView` above was already written this way.
            profile = Profile.objects.get(psn_username=psn_username.lower())
        except Profile.DoesNotExist:
            data = {
                'sync_status': 'error',
                'account_id': '',
            }
            return JsonResponse(data)

        from django.urls import reverse
        data = {
            'sync_status': profile.sync_status,
            'account_id': profile.account_id,
            'psn_username': profile.psn_username,
            'slug': reverse('profile_detail', kwargs={'psn_username': profile.psn_username}),
            'claimable': _claimable(profile),
            # The live tally, for a page watching a sync it asked for. Zero extra queries -- the
            # profile is already loaded, and these four denorms climb DURING the walk via the
            # EarnedTrophy post_save signal, unlike `total_trophies` which waits for finalize.
            # `ProfileSyncStatusView` sends the same block for the same reason.
            #
            # Nothing here is exposed that the profile page does not already print, except `claimable`: whether a
            # site account holds this name, which the linked-only leaderboards already reveal.
            'stats': {
                'plats': profile.total_plats,
                'golds': profile.total_golds,
                'silvers': profile.total_silvers,
                'bronzes': profile.total_bronzes,
            },
        }
        return JsonResponse(data)
