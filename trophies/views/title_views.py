"""The Titles page (`/titles/`) -- what you've earned the right to be called.

A title belongs to a BADGE SERIES: clear it and the title is yours (see
`badge_adapters.grant_series_title`). Earned through the work, never handed out. So this page is not a
second Collection -- Collection shows the medallions you own; this is the record of what you've proven:
which titles exist, which you hold, which one you're wearing, and which unearned ones you're closest to.

NEW badge system only. Legacy `source_type='badge'` grants are deliberately not surfaced here; they
retire with the badge cutover.

Three views behind the switcher:
  - **Yours**         -- titles you hold, plus any the live catalogue can't describe (a surviving
                         one-off award, or a series whose editions were taken off-live).
  - **Within reach**  -- unearned titles you have real progress toward, CLOSEST FIRST. Ranked off the
                         materialized `SeriesBadgeStanding.progress_bp`, so it's a read, not a computation.
  - **All**           -- the full live catalogue, each with what earns it + how many hunters have
                         EARNED it. Holders, not wearers: only one title per hunter can be worn.

Whale-safe by construction: every query is bounded by the badge catalogue or the viewer's own title
count. Nothing iterates trophies.
"""
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Count, Prefetch
from django.db.models.functions import Lower
from django.shortcuts import redirect
from django.urls import reverse, reverse_lazy
from django.views.generic import TemplateView

from ..models import BadgeSeries, GroupBadge, SeriesBadgeStanding, UserTitle
from trophies.services.badge_detail_service import group_medallion_layers
from trophies.services.badge_rarity import group_rarity
from trophies.services.rarity import community_size


class MyTitlesView(LoginRequiredMixin, TemplateView):
    template_name = 'trophies/my_titles.html'
    # '/login/' was not a route -- anonymous visitors 302'd into a 404 (the same failure class
    # as LinkPSN's reverse_lazy('login'); this was the hardcoded-string twin the sweep missed).
    login_url = reverse_lazy('account_login')

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and not hasattr(request.user, 'profile'):
            return redirect('link_psn')
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        profile = self.request.user.profile

        # ── 1. The live vocabulary: series that grant a title and have at least one live edition.
        # `live_editions` is prefetched (ordered, so the representative art pick is deterministic) --
        # the medallion needs a GroupBadge, and a series' editions share the subject artwork.
        series_list = list(
            BadgeSeries.objects
            .filter(title__isnull=False, group_badges__is_live=True)
            .distinct()
            .select_related('title')
            .prefetch_related(Prefetch(
                'group_badges',
                queryset=(GroupBadge.objects.filter(is_live=True)
                          .select_related('platform_group', 'series__submitted_by').order_by('id')),
                to_attr='live_editions',
            ))
            .order_by(Lower('title__name'))
        )

        # ── 2. What the viewer holds (one query; also yields the equipped one).
        user_titles = list(
            UserTitle.objects.filter(profile=profile).select_related('title')
        )
        held = {ut.title_id: ut for ut in user_titles}
        equipped = next((ut for ut in user_titles if ut.is_displayed), None)

        # ── 3. How close they are, per series -- the materialized read-model (no live evaluation).
        standings = {
            s.series_slug: s
            for s in SeriesBadgeStanding.objects.filter(profile=profile)
        }

        # ── 4. Social proof: how many hunters have EARNED each title. One grouped COUNT over the
        # catalogue.
        #
        # HOLDERS, not wearers. A UserTitle row is the grant; `is_displayed` is the separate equip flag
        # and only one of a hunter's rows can carry it. So filtering on it would count the far smaller
        # population of people who happen to have this title selected right now -- which says something
        # about fashion, not about achievement, and would make every title look vanishingly rare.
        #
        # `source_type='badge_series'` on purpose: this page surfaces the NEW badge system only (see the
        # module docstring), so counting a legacy 'badge' or one-off 'milestone' grant here would inflate
        # the numerator against a denominator that knows nothing about them -- making the title read
        # more common than the system it belongs to says it is.
        holders = dict(
            UserTitle.objects
            .filter(title_id__in=[s.title_id for s in series_list], source_type='badge_series')
            .values('title_id').annotate(c=Count('id'))
            .values_list('title_id', 'c')
        )

        # ── 4b. Rarity, through the SAME function the badge pages use (`badge_rarity.group_rarity`), so a
        # title's grade here agrees with its series' grade on badge detail and in the browse gallery
        # instead of being a second, private scheme.
        #
        # The denominator is the whole COMMUNITY -- every PSN-linked account -- not the series' pursuers.
        # A pursuer base shrinks when people abandon a series (the standing row is deleted at zero
        # progress), so a title could have become rarer because people gave up on it. One cached scalar,
        # shared with every other gradeable thing.
        #
        # The NUMERATOR is title holders, not the badge's earned_count. A title is granted by earning ANY
        # live edition, so it is strictly easier than any single edition -- and the plate prints "N
        # earned" right next to the grade. Grading a different population from the one displayed is how
        # you end up with a card that reads "Mythic - 44,210 earned".
        community = community_size()

        # ── 5. Build one entry per TITLE. Keyed by title_id, not by series: BadgeSeries.title has no
        # unique constraint, so two series can point at one Title -- one entry each would duplicate the
        # row, inflate the counts, and make the equip toggle flip two rows for a single title.
        entries, seen_titles = [], set()
        for series in series_list:
            if series.title_id in seen_titles:
                continue
            seen_titles.add(series.title_id)

            edition = series.live_editions[0] if series.live_editions else None
            ut = held.get(series.title_id)
            standing = standings.get(series.series_slug)
            # progress_bp is basis points (0-10000). Keep the RAW value for the "started" test: anything
            # under 50bp rounds to 0%, and treating that as untouched tells a hunter who has cleared a
            # stage that they haven't begun.
            progress_bp = standing.progress_bp if standing else 0
            progress_pct = round(progress_bp / 100)
            # ('' class) when the series has no pursuer base yet, or when nobody holds the title --
            # 0 earners is unearned, not an achievement, so it must not wear the prestige grade.
            rarity_pct, rarity_class = group_rarity(holders.get(series.title_id, 0), community)

            entries.append({
                'title': series.title,
                'name': series.title.name,
                'held': ut is not None,
                'earned_at': ut.earned_at if ut else None,
                'is_displayed': bool(ut and ut.is_displayed),
                'is_special': False,
                # what earns it
                'series_name': series.name,
                'series_slug': series.series_slug,
                'series_description': series.description,
                'url': reverse('badge_detail', kwargs={'series_slug': series.series_slug}),
                'frame': self._frame(edition, held=ut is not None, progress_pct=progress_pct),
                # how close (only meaningful while unheld)
                'progress_bp': progress_bp,
                'progress_pct': progress_pct,
                'stages_cleared': standing.stages_cleared if standing else 0,
                'stages_total': standing.stages_total if standing else 0,
                'holders': holders.get(series.title_id, 0),
                'rarity_pct': rarity_pct,
                'rarity_class': rarity_class,
                # Nobody holds it yet -> the "Be the first" nudge instead of a grade. 0 earners is
                # unearned, not an achievement, so it must never wear a prestige grade.
                'unearned': not holders.get(series.title_id, 0),
            })

        # ── 6. Held titles the live vocabulary can't describe, but which the hunter genuinely owns:
        #   - 'milestone'    -- the surviving one-off awards from the retired milestone engine.
        #   - 'badge_series' -- a series title whose editions have ALL been taken off-live. Without this
        #                       a title you earned vanishes the moment staff unlist its series.
        # LEGACY 'badge' grants are deliberately NOT rescued: this page shows the new badge system only,
        # and those titles retire with the badge cutover.
        catalogue_title_ids = {s.title_id for s in series_list}
        _RESCUED = {'milestone': 'Special award', 'badge_series': 'Badge title'}
        uncatalogued = sorted(
            (
                {
                    'title': ut.title,
                    'name': ut.title.name,
                    'held': True,
                    'earned_at': ut.earned_at,
                    'is_displayed': ut.is_displayed,
                    'is_special': ut.source_type == 'milestone',
                    'source_label': _RESCUED[ut.source_type],
                    'series_name': None,
                    'series_slug': None,
                    'series_description': '',
                    'url': None,
                    'frame': None,
                    'progress_bp': 10000,
                    'progress_pct': 100,
                    'stages_cleared': 0,
                    'stages_total': 0,
                    'holders': 0,
                    # No grade: these sit outside the live catalogue (a one-off award, or a series
                    # taken off-live), so there is no pursuer base to grade them against. `is_special`
                    # carries the flavour instead.
                    'rarity_pct': None,
                    'rarity_class': '',
                    # The hunter IS holding this one -- it just sits outside the live catalogue, so
                    # there is no pursuer base to grade it against. Never the "be the first" nudge.
                    'unearned': False,
                }
                for ut in user_titles
                if ut.source_type in _RESCUED and ut.title_id not in catalogue_title_ids
            ),
            key=lambda e: e['earned_at'], reverse=True,
        )

        # ── 7. Partition into the three switcher views.
        # Yours: held, most recent first (specials mixed in -- they're earned words too).
        yours = sorted(
            [e for e in entries if e['held']] + uncatalogued,
            key=lambda e: e['earned_at'], reverse=True,
        )
        # Within reach: unheld but started, CLOSEST FIRST -- the motivating slice.
        within_reach = sorted(
            [e for e in entries if not e['held'] and e['progress_bp'] > 0],
            key=lambda e: (-e['progress_bp'], e['name']),
        )
        # All: the full live vocabulary (already name-ordered by the queryset).

        context.update({
            'equipped_title': equipped.title if equipped else None,
            'yours': yours,
            'within_reach': within_reach,
            'all_titles': entries,
            'yours_count': len(yours),
            'within_reach_count': len(within_reach),
            'all_count': len(entries),
            'profile': profile,
            'breadcrumb': [
                {'text': 'Home', 'url': reverse_lazy('home')},
                {'text': 'My Pursuit', 'url': reverse_lazy('my_pursuit_hub')},
                {'text': 'Titles'},
            ],
        })
        return context

    @staticmethod
    def _frame(edition, held, progress_pct):
        """Minimal medallion frame for a title's source badge. Reuses `group_medallion_layers` so the
        art composes identically to Collection / Badge detail. None when the series has no live edition.

        State follows the VIEWER: rendering every badge as 'earned' gave an unowned one the earned aura
        and hover-lift right next to its own padlock."""
        if edition is None:
            return None
        tier, layers, is_avatar = group_medallion_layers(edition)
        state = 'earned' if held else ('in_progress' if progress_pct else 'unearned')
        return {
            'tier': tier,
            'state': state,
            'progress_pct': progress_pct if state == 'in_progress' else 0,
            'art_layers': layers,
            'is_avatar': is_avatar,
            'is_holographic': False,
            'series_name': edition.series.name,
        }
