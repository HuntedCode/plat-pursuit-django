import hashlib
import logging

from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.cache import cache
from django.db.models import OuterRef, Subquery
from django.http import JsonResponse
from django.urls import reverse
from django.utils.decorators import method_decorator
from django.views.generic import View
from django_ratelimit.core import is_ratelimited
from django_ratelimit.decorators import ratelimit

from core.services.tracking import track_site_event
from trophies.psn_manager import PSNManager
from trophies.services.game_grouping_service import representative_concept_icon_subquery
from trophies.util_modules.cache import redis_client
from ..models import Badge, Concept, Franchise, Game, Profile

logger = logging.getLogger("psn_api")


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

        if profile.sync_status == 'syncing':
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


def _badge_main_image(badge):
    """The badge medallion's main image URL: its own art, else the inherited base-badge
    art, else '' (client falls back to the badge glyph). Mirrors Badge.get_badge_layers()
    and the Discord notifier -- the base_badge is select_related by the caller."""
    if badge.badge_image:
        return badge.badge_image.url
    if badge.base_badge and badge.base_badge.badge_image:
        return badge.base_badge.badge_image.url
    return ''


class SiteSuggestView(View):
    """
    Universal navbar typeahead. Suggests Games, Badges, Franchises, and tracked
    Hunters matching the query, grouped by type, each linking to its detail page.
    Widens the profile-only ProfileSuggestView; the client keeps the add-and-sync
    fallback (SearchSyncProfileView) for Online IDs that aren't tracked yet.

    Whale-safe by construction: this is global reference data, not per-user
    aggregation. Every group query is DB-side, bounded to PER_GROUP rows, and
    the whole response is identical for every viewer -> cached in Redis by
    normalized query for a short TTL, so hot prefixes never touch Postgres.

    Title matching is substring (icontains), served sub-ms by the pg_trgm GIN
    indexes on Concept.unified_title / Badge.name / Franchise.name (migration
    0257). Hunters stay prefix (istartswith), like ProfileSuggestView.
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
        cache_key = f'sitesuggest:v1:{hashlib.md5(q.lower().encode()).hexdigest()}'
        cached = cache.get(cache_key)
        if cached is not None:
            return JsonResponse(cached)

        groups = [g for g in (
            self._games(q), self._badges(q), self._franchises(q), self._hunters(q),
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
        items = [
            {
                'label': c.unified_title,
                'image': c.get_cover_url('cover_small') or '',   # IGDB-first, mirrors the game cards
                'url': reverse('game_detail', kwargs={'np_communication_id': c.npc}),
            }
            for c in concepts
        ]
        return {'type': 'game', 'label': 'Games', 'items': items}

    def _badges(self, q):
        # One row per series: the tier-1 badge is the series' canonical entry, and
        # badge_detail is keyed on series_slug (all tiers collapse to one page).
        # series_slug is nullable/blank; a null/'' slug would raise NoReverseMatch on
        # badge_detail and 500 the whole endpoint, so exclude those (as the rest of the
        # codebase does) -- an unlinkable badge can't be a suggestion anyway.
        # Fetch instances (not .values()) so the medallion image URL resolves through the
        # ImageField storage; select_related the base_badge for the inherited-icon fallback.
        badges = (
            Badge.objects.live()
            .filter(name__icontains=q, tier=1)
            .exclude(series_slug__isnull=True).exclude(series_slug='')
            .select_related('base_badge')
            .order_by('name')[:self.PER_GROUP]
        )
        items = [
            {
                'label': b.name,
                'image': _badge_main_image(b),
                'url': reverse('badge_detail', kwargs={'series_slug': b.series_slug}),
            }
            for b in badges
        ]
        return {'type': 'badge', 'label': 'Badges', 'items': items}

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
                through_path='concept__concept_franchises__franchise'))
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

    def _hunters(self, q):
        # Same query/shape as ProfileSuggestView (prefix, ranked by trophy weight).
        rows = (
            Profile.objects
            .filter(psn_username__istartswith=q)
            .order_by('-total_plats', 'psn_username')
            .values('psn_username', 'display_psn_username', 'avatar_url', 'total_plats')
            [:self.PER_GROUP]
        )
        items = [
            {
                'label': r['display_psn_username'] or r['psn_username'],
                'avatar_url': r['avatar_url'] or '',
                'plats': r['total_plats'],
                'url': reverse('profile_detail', kwargs={'psn_username': r['psn_username']}),
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

        if redis_client.get('site:psn_outage'):
            return JsonResponse({
                'error': 'PlayStation Network is currently unavailable. '
                         'Syncs will resume automatically when PSN recovers.'
            }, status=503)

        profile = request.user.profile
        is_syncing = profile.attempt_sync()
        if not is_syncing:
            seconds_left = profile.get_seconds_to_next_sync()
            return JsonResponse({'error': f'Cooldown active: {seconds_left} seconds left'}, status=429)
        return JsonResponse({'success': True, 'message': 'Sync started'})

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
                'error': 'Too many searches. Please wait a minute and try again.'
            }, status=429)

        if redis_client.get('site:psn_outage'):
            return JsonResponse({
                'error': 'PlayStation Network is currently unavailable. '
                         'Please try again later.'
            }, status=503)

        psn_username = request.POST.get('psn_username', '').strip()
        if not psn_username:
            return JsonResponse({'error': 'Username required'}, status=400)

        is_new = False
        try:
            profile = Profile.objects.get(psn_username__iexact=psn_username)
        except Profile.DoesNotExist:
            profile = Profile.objects.create(
                psn_username=psn_username.lower(),
                view_count=0
            )
            is_new = True

        result_tag = 'new' if is_new else 'existing'
        searcher_pid = getattr(getattr(request.user, 'profile', None), 'id', None)
        object_id = f'{psn_username.lower()}|{result_tag}|pid:{searcher_pid}'
        track_site_event('sync_search', object_id, request)

        if is_new:
            PSNManager.initial_sync(profile)
        else:
            profile.attempt_sync()
        return JsonResponse({
            'success': True,
            'message': f"{'Added and syncing' if is_new else 'Syncing'} {psn_username}",
            'psn_username': profile.psn_username,
        })

class AddSyncStatusView(View):
    """
    AJAX endpoint to poll sync status after adding a new profile via the
    navbar search dropdown.

    Returns sync status, account ID, and profile URL. Read-only DB lookup,
    no PSN tokens consumed, so it's open to anonymous users to pair with
    the open SearchSyncProfileView.
    """
    def get(self, request):
        psn_username = request.GET.get('psn_username', '').strip()
        if not psn_username:
            return JsonResponse({'error': 'Username required'}, status=400)

        try:
            profile = Profile.objects.get(psn_username__iexact=psn_username)
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
        }
        return JsonResponse(data)
