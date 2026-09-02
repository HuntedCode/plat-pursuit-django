"""Concept-level context builders, shared by List detail and the concept Game page.

Lifted VERBATIM off GameDetailView (slice 1 of docs/design/games-and-trophy-lists-ia.md): these
builders render the WORK -- IGDB facts, time-to-beat, images, ratings/community tabs, versions,
family, the contract panel -- none of it specific to one trophy list. BOTH pages mix this in for
good: the slim-down (phase 2) did not remove the mixin from GameDetailView, because the List-page
hero and its modals genuinely need the concept-level subset (images, badges, versions, pursuit).
Instead GameDetailView's lane is that subset -- _build_images_context, _build_badges_context,
_build_versions_context, _build_pursuit_context -- while _build_concept_context (which layers the
About facts and the ratings/community assembly on top of the subset) is the GAME page's entry
point.

The acceptance bar for this move was zero edits to test_game_detail_hero/test_game_detail_ratings:
their unit tests call these builders directly on GameDetailView, so passing untouched proves the
move was pure. The only implicit dependency is `self.request`, always reached through getattr
guards so the builders stay callable off an unbound view in tests.

The `game:imageurls:{np}` cache key stays keyed on the HOST game's np id -- deliberately shared
between both pages (same content, one cache; redis_admin --flush-game-page keeps working).
"""

import json
import logging

from django.core.cache import cache
from django.db.models import Case, IntegerField, Prefetch, Subquery, Value, When
from django.db.models.functions import Lower
from django.urls import reverse

from trophies.constants import CACHE_TIMEOUT_IMAGES
from trophies.models import Game, Stage
from trophies.util_modules.constants import ALL_PLATFORMS

logger = logging.getLogger(__name__)


class ConceptContextMixin:
    def _build_images_context(self, game):
        """
        Build cached image URLs for game background, screenshots, and content rating.

        Args:
            game: Game instance

        Returns:
            dict: Image URLs or empty dict on error
        """
        images_cache_key = f"game:imageurls:{game.np_communication_id}"
        images_timeout = CACHE_TIMEOUT_IMAGES

        try:
            cached_images = cache.get(images_cache_key)
            if cached_images:
                return json.loads(cached_images)

            if not game.concept:
                return {}

            screenshot_urls = []

            if game.concept.media:
                # Prefer PSN-side screenshots
                for img in game.concept.media:
                    if img.get('type') == 'SCREENSHOT':
                        screenshot_urls.append(img.get('url'))

                # Fallback to other PSN image types if no screenshots
                if len(screenshot_urls) < 1:
                    for img in game.concept.media:
                        img_type = img.get('type')
                        if img_type in ['GAMEHUB_COVER_ART', 'LOGO', 'MASTER']:
                            screenshot_urls.append(img.get('url'))

            # Final fallback: IGDB-side screenshots. For canonical-anchored
            # Concepts (post-migration), PSN media isn't fetched, so the
            # carousel needs IGDB to fill in. Also covers the case where
            # legacy concepts have empty/sparse PSN media but IGDB has data.
            if not screenshot_urls:
                igdb_match = getattr(game.concept, 'igdb_match', None)
                if igdb_match:
                    screenshot_urls = igdb_match.screenshot_urls()

            image_urls = {
                'header_bg_url': game.concept.get_cover_url(),  # Used for frosted glass header only
                'screenshot_urls': screenshot_urls,
            }
            cache.set(images_cache_key, json.dumps(image_urls), timeout=images_timeout)
            return image_urls

        except Exception as e:
            logger.exception(f"Game images cache failed for {game.np_communication_id}")
            return {}

    def _build_concept_context(self, game):
        """
        Build concept-related context including community ratings, badges, and other versions.

        Args:
            game: Game instance

        Returns:
            dict: Concept context data or empty dict if no concept
        """
        if not game.concept:
            return {}

        from trophies.services.rating_service import RatingService

        context = {}

        # Partition the prefetched franchise links into franchise-type and
        # collection-type buckets for the About card. All non-excluded links
        # land in their respective bucket as equal members; admins can hide
        # individual links via ConceptFranchise.is_excluded=True.
        #
        # `cf_row_count` tracks the raw CF row count (including excluded). The igdb.franchise_names denorm
        # fallback in _build_about_facts is gated on it so the fallback only fires for truly un-enriched
        # concepts (zero CF rows) — otherwise an admin exclusion would leak through it, because
        # franchise_names is rewritten from IGDB raw on every enrichment.
        #
        # These stay LOCAL: they feed _build_about_facts only. They used to be context keys read by the old
        # franchise_lines.html partial, which this rebuild deleted.
        franchises = []
        collections = []
        cf_row_count = 0
        for cf in game.concept.concept_franchises.all():
            cf_row_count += 1
            if cf.is_excluded:
                continue
            if cf.franchise.source_type == 'collection':
                collections.append(cf)
            else:
                franchises.append(cf)
        # About panel: the Quick Facts rows plus the gating flags, computed here so the template stays free
        # of gnarly boolean chains. `about_has_info` drives the empty state. Prefetched reads only.
        igdb_match = getattr(game.concept, 'igdb_match', None)
        about_trusted = bool(igdb_match and igdb_match.is_trusted)
        context['about_facts'] = self._build_about_facts(game, franchises, collections, cf_row_count > 0)
        context['about_has_facts'] = bool(context['about_facts'])
        context['about_has_info'] = bool(about_trusted and (
            igdb_match.igdb_summary
            or list(game.concept.concept_genres.all())
            or list(game.concept.concept_themes.all())
            or igdb_match.has_time_to_beat
            or context['about_has_facts']
            or igdb_match.notable_external_urls
        ))

        # Per-CTG community data for the Ratings tab (ratings only; the text
        # review system was archived 2026-05).
        from trophies.models import ConceptTrophyGroup, Trophy, UserConceptRating
        from trophies.services.concept_trophy_group_service import ConceptTrophyGroupService

        # Local only: feeds community_tabs below. (A `concept_trophy_groups` context key used to be
        # set here too -- deleted in the slim-down split, zero consumers anywhere.)
        ctgs = list(
            ConceptTrophyGroup.objects.filter(concept=game.concept)
            .order_by('sort_order', 'trophy_group_id')
        )

        user = self.request.user
        profile = getattr(user, 'profile', None) if user.is_authenticated else None

        # Check once whether this concept has a platinum trophy (for hours label)
        concept_has_plat = Trophy.objects.filter(
            game__concept=game.concept, trophy_type='platinum'
        ).exists()

        # Public "quick take" blurbs shown under each group's ratings. Bounded preview per group, ordered
        # newest-first (model default) and backed by the partial rating_blurb_idx, so it stays whale-safe:
        # one [:N] preview + one index-only COUNT query per group (the profile joined so avatars don't N+1).
        BLURB_PREVIEW_LIMIT = 12

        community_tabs = []
        for ctg in ctgs:
            is_base = ctg.trophy_group_id == 'default'
            has_plat = is_base and concept_has_plat
            ctg_fk = None if is_base else ctg

            tab_data = {
                'ctg': ctg,
                'averages': RatingService.get_cached_community_averages_for_group(game.concept, ctg),
                'hours_label': 'Hours to Plat' if has_plat else 'Hours to Complete',
                'hours_label_long': 'Hours to Platinum' if has_plat else 'Hours to Complete',
                # The recommendation's middle option and its question, worded for THIS group -- "rough
                # platinum" is wrong on a DLC pack and on a game that never had one. Same reason the
                # hours label above varies, and derived from the same `has_plat`.
                **UserConceptRating.recommendation_copy(has_plat),
                'has_platinum': has_plat,
                'can_rate': False,
                'can_rate_reason': None,
                'user_rating': None,
                'blurbs': list(
                    UserConceptRating.visible_blurbs()
                    .filter(concept=game.concept, concept_trophy_group=ctg_fk)
                    .select_related('profile')[:BLURB_PREVIEW_LIMIT]
                ),
                'blurb_count': UserConceptRating.visible_blurbs().filter(
                    concept=game.concept, concept_trophy_group=ctg_fk
                ).count(),
            }
            if profile and profile.is_linked:
                can_rate, rate_reason = ConceptTrophyGroupService.can_rate_group(
                    profile, game.concept, ctg
                )
                tab_data['can_rate'] = can_rate
                tab_data['can_rate_reason'] = rate_reason

                tab_data['user_rating'] = UserConceptRating.objects.filter(
                    profile=profile, concept=game.concept,
                    concept_trophy_group=ctg_fk,
                ).first()

            community_tabs.append(tab_data)
        context['community_tabs'] = community_tabs
        # The BASE group's aggregate, for the VideoGame schema's AggregateRating (SEO Lane 2).
        # community_tabs[0] is the default group by the CTG ordering above; guarded anyway.
        base_tab = next((t for t in community_tabs if t['ctg'].trophy_group_id == 'default'), None)
        context['base_rating_averages'] = base_tab['averages'] if base_tab else None
        # Show a per-group title on the verdict card only when there's DLC to disambiguate (base game only = obvious).
        context['has_dlc'] = len(ctgs) > 1
        # Lets each blurb card mark the viewer's own (You pill, no self-report) without a per-row query.
        context['viewer_profile_id'] = profile.id if profile else None

        context.update(self._build_badges_context(game))
        context.update(self._build_versions_context(game))

        return context

    def _build_badges_context(self, game):
        """Related badges (grouping-badge system) — the specific badge EDITIONS this game is part of.

        Split out of _build_concept_context for the list-detail slim-down: the hero's badge spine
        (and its modal) is a LIST-page keeper, while the ratings/about assembly above is Game-page
        territory — List detail calls this directly without paying for community_tabs.

        A game only gates for the platform groups its own platforms match (the exact routing the
        engine uses: title_platform overlaps the group's platforms --
        badge_detail_service._group_journey), so show exactly those editions: usually one per
        series, both for a cross-generation game. Each links to its edition tab (?group=<key>).
        build_list_cards gives the SAME whale-safe showcase frame the badge list uses.
        """
        if not game.concept:
            return {'badges': []}

        from trophies.models import GroupBadge
        from trophies.services.badge_list_service import build_list_cards
        series_slugs = (
            Stage.objects.filter(concepts__games=game)
            .exclude(series_slug__isnull=True).exclude(series_slug='')
            .values_list('series_slug', flat=True).distinct()
        )
        game_platforms = set(game.title_platform or [])
        badges = []
        if game_platforms:
            gbs = [
                gb for gb in (
                    GroupBadge.objects.filter(series__series_slug__in=Subquery(series_slugs), is_live=True)
                    .select_related('series', 'series__franchise', 'series__collection', 'series__developer',
                                    'series__submitted_by', 'platform_group')
                    .order_by('series__name', 'platform_group__sort_order', 'id')
                )
                if game_platforms & set(gb.platform_group.platforms)   # the game routes to this edition
            ]
            badges = [
                {
                    'frame': c['frame'],
                    'series_slug': c['series'].series_slug,
                    'name': c['series'].display_series or c['series'].name,
                    'type_display': c['series'].get_badge_type_display(),
                    'group_key': c['platform_group'].key,
                    'group_name': c['platform_group'].name,
                }
                for c in build_list_cards(gbs, None)
            ]
        return {'badges': badges}

    def _build_versions_context(self, game):
        """"Other platforms" + "In the same family" cross-links (the versions modal / About card).

        Split out alongside _build_badges_context (same slim-down reasoning): List detail's hero
        versions button + #gd-versions-modal need these three keys without the rest of
        _build_concept_context.
        """
        if not game.concept:
            return {'other_versions': [], 'family_versions': [], 'versions_total': 0}
        other_versions = self._build_other_versions(game)
        family_versions = self._build_family_versions(game)
        return {
            'other_versions': other_versions,
            'family_versions': family_versions,
            'versions_total': len(other_versions) + len(family_versions),
        }

    def _build_about_facts(self, game, franchises, collections, has_cf_rows):
        """Quick Facts rows for the About panel: ONE row per role with its entries grouped, rather than one
        row per company. A game like God of War Ragnarok credits 7 supporting studios -- repeating the label
        for each turned the card into a wall of "Additional dev". Each row is
        {'label', 'items': [{'name', 'url'}]}; the template shows 3 then a "+N more" disclosure.
        Reads prefetched caches only, so no extra queries.
        """
        concept = game.concept
        igdb = getattr(concept, 'igdb_match', None)
        facts = []

        def add(single, plural, items):
            if items:
                facts.append({'label': single if len(items) == 1 else plural, 'items': items})

        # Credits first (who made and shipped it), then the technical + lineage facts. The hero shows the
        # lead developer/publisher as a glance; About repeats them so the panel reads as a complete credit
        # list rather than jumping straight to "Ported by".
        developers, publishers, porting, supporting = [], [], [], []
        for cc in concept.concept_companies.all():
            entry = {'name': cc.company.name,
                     'url': reverse('company_detail', kwargs={'slug': cc.company.slug})}
            if cc.is_developer:
                developers.append(entry)
            if cc.is_publisher:
                publishers.append(entry)
            if cc.is_porting:
                porting.append(entry)
            if cc.is_supporting:
                supporting.append(entry)
        add('Developer', 'Developers', developers)
        add('Publisher', 'Publishers', publishers)
        add('Ported by', 'Ported by', porting)
        add('Additional dev', 'Additional devs', supporting)

        # Engine detail pages were retired -> engines show as plain text (name only, no link).
        engines = [{'name': ce.engine.name, 'url': ''} for ce in concept.concept_engines.all()]
        if not engines and igdb and igdb.game_engine_name:
            engines = [{'name': igdb.game_engine_name, 'url': ''}]
        add('Engine', 'Engines', engines)

        franchise_items = [
            {'name': cf.franchise.name, 'url': reverse('franchise_detail', kwargs={'slug': cf.franchise.slug})}
            for cf in franchises
        ]
        if not franchise_items and igdb and igdb.franchise_names and not has_cf_rows:
            # Denorm fallback, gated on zero ConceptFranchise rows so an admin-excluded link can't leak.
            franchise_items = [{'name': name, 'url': ''} for name in igdb.franchise_names]
        add('Franchise', 'Franchises', franchise_items)

        add('Series', 'Series', [
            {'name': cf.franchise.name, 'url': reverse('franchise_detail', kwargs={'slug': cf.franchise.slug})}
            for cf in collections
        ])

        return facts

    @staticmethod
    def _build_about_ttb(igdb_match, play_duration):
        """Time-to-beat as PROPORTIONS on one shared scale, not three isolated numbers.

        The relationship between the estimates is the actual information -- that completionist is 4x normal
        is what a hunter wants -- and reading that off three formatted strings is mental arithmetic. One
        shared scale makes the shape readable at a glance, and the viewer's own play_duration joins that
        scale as a final row when we have it (PSN doesn't always expose it, so it's optional by design).

        Returns None when there's nothing to plot. `comparative` is False for a lone estimate with no
        playtime, where a single full-width bar would imply a ratio that isn't there -- the template falls
        back to the plain tally in that case.
        """
        if not igdb_match or not igdb_match.has_time_to_beat:
            return None

        specs = (
            ('Speedrun', igdb_match.time_to_beat_hastily, igdb_match.speedrun_time_display),
            ('Normal', igdb_match.time_to_beat_normally, igdb_match.normal_time_display),
            ('Completionist', igdb_match.time_to_beat_completely, igdb_match.completion_time_display),
        )
        played = int(play_duration.total_seconds()) if play_duration else 0
        # Scale to the longest bar INCLUDING the viewer's own time, so someone who has already run past the
        # completionist estimate gets a full bar rather than one overflowing its track.
        scale = max([secs for _, secs, _ in specs if secs] + [played])
        if not scale:
            return None

        rows = [
            {'label': label, 'display': display, 'secs': secs, 'is_you': False}
            for label, secs, display in specs if secs
        ]
        estimates = len(rows)
        # `> 0` not truthiness: play_duration has no non-negative DB constraint, and a negative one is
        # truthy (unlike timedelta(0)), which would render a "You" row with a negative bar and a "-16m"
        # label instead of being dropped like the missing case.
        if played > 0:
            # Reuse IGDBMatch's own formatter so "41h" here matches the estimates' formatting exactly.
            rows.append({'label': 'You', 'display': igdb_match.format_seconds(played),
                         'secs': played, 'is_you': True})
        # Slot the viewer's bar where their time actually FALLS rather than pinning it last, so the column
        # reads as one ascending scale and their position between two estimates is the visible fact. On a
        # tie the viewer sorts after the estimate they've matched.
        rows.sort(key=lambda r: (r['secs'], r['is_you']))
        for row in rows:
            row['pct'] = round(row['secs'] * 100 / scale)
        return {'rows': rows, 'comparative': estimates > 1 or played > 0}

    def _build_other_versions(self, game):
        """Other platform versions -- Games whose Concept shares the EXACT same IGDB id. Being on another
        platform doesn't require the SAME Concept: a PS4 and PS5 edition that IGDB lists as one game share an
        igdb_id across separate Concepts (multi-concept-per-id), so grouping is by igdb_id, not concept.
        Falls back to same-Concept games when the Concept has no IGDB match. Cover-safe (CLAUDE.md rule).
        """
        concept = game.concept
        if not concept:
            return []
        igdb_id = getattr(getattr(concept, 'igdb_match', None), 'igdb_id', None)
        qs = (
            Game.objects
            .select_related('concept', 'concept__igdb_match')
            .defer('concept__igdb_match__raw_response')
            .exclude(pk=game.pk)
        )
        qs = qs.filter(concept__igdb_match__igdb_id=igdb_id) if igdb_id else qs.filter(concept_id=concept.pk)
        platform_order = {plat: idx for idx, plat in enumerate(ALL_PLATFORMS)}
        qs = qs.annotate(
            platform_order=Case(*[When(title_platform__contains=plat, then=Value(idx)) for plat, idx in platform_order.items()], default=999, output_field=IntegerField())
        ).order_by('platform_order', 'title_name')
        return list(qs)

    def _build_family_versions(self, game):
        """Other Concepts in the same GameFamily with a DIFFERENT IGDB id (remasters / remakes / collections).
        Concepts that share this game's exact igdb_id are 'other platforms', not family, so they're excluded
        here. One representative game (most-played) per sibling concept. Bounded, game-level, cover-safe.
        """
        concept = game.concept
        family_id = getattr(concept, 'family_id', None) if game.concept_id else None
        if not family_id:
            return []
        igdb_id = getattr(getattr(concept, 'igdb_match', None), 'igdb_id', None)
        sib_qs = concept.family.concepts.exclude(pk=concept.pk)
        if igdb_id:
            sib_qs = sib_qs.exclude(igdb_match__igdb_id=igdb_id)   # same igdb id -> other platforms, not family
        rep_games = (
            Game.objects.select_related('concept', 'concept__igdb_match')
            .defer('concept__igdb_match__raw_response')
            .order_by('-played_count', 'title_name')
        )
        siblings = sib_qs.prefetch_related(Prefetch('games', queryset=rep_games, to_attr='rep_list')).order_by(Lower('unified_title'))
        out = []
        for sib in siblings:
            rep = sib.rep_list[0] if getattr(sib, 'rep_list', None) else None
            if rep:
                out.append({'concept': sib, 'game': rep})
        return out

    _CONTRACT_STATE_TAG = {
        'available': ('Available', 'todo'),
        'not_started': ('Not Started', 'todo'),
        'pursuing': ('In Progress', 'active'),
        'claimable': ('Claimable', 'claim'),
        'banked': ('Banked', 'done'),
    }

    def _build_pursuit_context(self, game, target_profile):
        """Spine cross-link: the Contract this game belongs to + the Jobs it levels.

        The job list is game-intrinsic (identical for every viewer); only the
        banked/claimable state reflects the target profile. Cheap -- two bounded
        queries via contract_by_concept_map (jobs prefetched), plus one for the
        authed state lookup. Display-only here; the authoritative claim flow lives
        on /career/.
        """
        empty = {'pursuit_contract': None, 'pursuit_jobs': [], 'pursuit_contract_state': None,
                 'pursuit_xp_per_job': 0, 'pursuit_disc_style': ''}
        if not game.concept_id:
            return empty

        from trophies.services import contract_service
        from trophies.services.job_render import job_atom

        contract = contract_service.contract_by_concept_map([game.concept_id]).get(game.concept_id)
        if not contract:
            return empty

        jobs = [job_atom(j) for j in sorted(contract.jobs.all(), key=lambda j: j.display_order)]
        # Ordered-unique disciplines this contract spans, prebuilt into the row's discipline-colour
        # style (a gradient of the disciplines + a primary tint). Built here rather than looping
        # var()s inside a template style attribute.
        disciplines = list(dict.fromkeys(j['disc_slug'] for j in jobs))
        if disciplines:
            stops = [f'var(--disc-{d})' for d in disciplines]
            if len(stops) == 1:
                stops = stops * 2   # a gradient needs >= 2 stops
            disc_style = (
                f'--disc-1: var(--disc-{disciplines[0]}); '
                f'--disc-grad: linear-gradient(180deg, {", ".join(stops)});'
            )
        else:
            disc_style = ''

        # XP per job = the contract's total XP split evenly across its jobs (mirrors
        # contracts_service.xp_each). Cheap; no extra query.
        from trophies.util_modules.constants import CONTRACT_XP_TOTAL
        xp_total = contract.xp_total_override or CONTRACT_XP_TOTAL
        xp_per_job = (xp_total // len(jobs)) if jobs else 0

        # The contract row always carries a status tag (design: consistent presence). For a linked viewer
        # it reflects their EarnedContract; a linked viewer who hasn't started reads "Not Started"; an anon/
        # unlinked viewer (no target_profile) reads the neutral "Available".
        if target_profile:
            from trophies.models import EarnedContract
            ec = EarnedContract.objects.filter(profile=target_profile, contract=contract).first()
            if ec:
                plat_claimable = bool(ec.platinum_reached_at and not ec.platinum_accepted_at)
                full_claimable = bool(ec.full_reached_at and not ec.full_accepted_at)
                fully_banked = bool(ec.full_accepted_at and (not ec.has_platinum or ec.platinum_accepted_at))
                if plat_claimable or full_claimable:
                    status = 'claimable'
                elif fully_banked:
                    status = 'banked'
                else:
                    status = 'pursuing'
            else:
                status = 'not_started'
        else:
            status = 'available'
        label, variant = self._CONTRACT_STATE_TAG[status]
        state = {'status': status, 'label': label, 'variant': variant}
        return {'pursuit_contract': contract, 'pursuit_jobs': jobs, 'pursuit_contract_state': state,
                'pursuit_xp_per_job': xp_per_job, 'pursuit_disc_style': disc_style}
