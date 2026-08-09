"""The Titles page (`/titles/`) -- the words you can wear.

A title belongs to a BADGE SERIES: earning any edition of that series grants it (see
`badge_adapters.grant_series_title`). So this page is not a second Collection -- Collection shows the
medallions you own; this shows the *vocabulary*: which words exist, which you hold, which one you're
wearing, and (the motivating part) which unheld ones you're closest to.

Three views behind the switcher:
  - **Yours**         -- titles you hold, plus the surviving one-off awards from the retired milestone
                         engine (`source_type='milestone'`, no live source row to describe).
  - **Within reach**  -- unheld titles you have real progress toward, CLOSEST FIRST. Ranked off the
                         materialized `SeriesBadgeStanding.progress_bp`, so it's a read, not a computation.
  - **All**           -- the full live vocabulary, each with what earns it + how many hunters wear it.

Whale-safe by construction: every query is bounded by the badge catalogue or the viewer's own title
count. Nothing iterates trophies.
"""
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Count, Prefetch
from django.db.models.functions import Lower
from django.shortcuts import redirect
from django.urls import reverse_lazy
from django.views.generic import TemplateView

from ..models import BadgeSeries, GroupBadge, SeriesBadgeStanding, UserTitle
from trophies.services.badge_detail_service import group_medallion_layers


class MyTitlesView(LoginRequiredMixin, TemplateView):
    template_name = 'trophies/my_titles.html'
    login_url = '/login/'

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
                          .select_related('platform_group', 'series').order_by('id')),
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

        # ── 4. Social proof: how many hunters wear each title. One grouped COUNT over the catalogue.
        holders = dict(
            UserTitle.objects
            .filter(title_id__in=[s.title_id for s in series_list])
            .values('title_id').annotate(c=Count('id'))
            .values_list('title_id', 'c')
        )

        # ── 5. Build one entry per title in the vocabulary.
        entries = []
        for series in series_list:
            edition = series.live_editions[0] if series.live_editions else None
            ut = held.get(series.title_id)
            standing = standings.get(series.series_slug)
            # progress_bp is basis points (0-10000) over the series' furthest-along edition.
            progress_pct = round((standing.progress_bp / 100)) if standing else 0

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
                'url': reverse_lazy('badge_detail', kwargs={'series_slug': series.series_slug}),
                'frame': self._frame(edition),
                # how close (only meaningful while unheld)
                'progress_pct': progress_pct,
                'stages_cleared': standing.stages_cleared if standing else 0,
                'stages_total': standing.stages_total if standing else 0,
                'holders': holders.get(series.title_id, 0),
            })

        # ── 6. The surviving one-off awards (retired milestone engine). Earned-only, no source row.
        catalogue_title_ids = {s.title_id for s in series_list}
        specials = sorted(
            (
                {
                    'title': ut.title,
                    'name': ut.title.name,
                    'held': True,
                    'earned_at': ut.earned_at,
                    'is_displayed': ut.is_displayed,
                    'is_special': True,
                    'series_name': None,
                    'series_slug': None,
                    'series_description': '',
                    'url': None,
                    'frame': None,
                    'progress_pct': 100,
                    'stages_cleared': 0,
                    'stages_total': 0,
                    'holders': 0,
                }
                for ut in user_titles
                if ut.source_type == 'milestone' and ut.title_id not in catalogue_title_ids
            ),
            key=lambda e: e['earned_at'], reverse=True,
        )

        # ── 7. Partition into the three switcher views.
        # Yours: held, most recent first (specials mixed in -- they're earned words too).
        yours = sorted(
            [e for e in entries if e['held']] + specials,
            key=lambda e: e['earned_at'], reverse=True,
        )
        # Within reach: unheld but started, CLOSEST FIRST -- the motivating slice.
        within_reach = sorted(
            [e for e in entries if not e['held'] and e['progress_pct'] > 0],
            key=lambda e: (-e['progress_pct'], e['name']),
        )
        # All: the full live vocabulary (already name-ordered by the queryset).

        context.update({
            'equipped_title': equipped.title if equipped else None,
            'equipped_title_id': equipped.title_id if equipped else None,
            'yours': yours,
            'within_reach': within_reach,
            'all_titles': entries,
            'yours_count': len(yours),
            'within_reach_count': len(within_reach),
            'all_count': len(entries),
            'held_count': len([e for e in entries if e['held']]),
            'profile': profile,
            'breadcrumb': [
                {'text': 'Home', 'url': reverse_lazy('home')},
                {'text': 'My Pursuit', 'url': reverse_lazy('my_pursuit_hub')},
                {'text': 'Titles'},
            ],
        })
        return context

    @staticmethod
    def _frame(edition):
        """Minimal medallion frame for a title's source badge. Reuses `group_medallion_layers` so the
        art composes identically to Collection / Badge detail. None when the series has no live edition."""
        if edition is None:
            return None
        tier, layers, is_avatar = group_medallion_layers(edition)
        return {
            'tier': tier,
            'state': 'earned',
            'art_layers': layers,
            'is_avatar': is_avatar,
            'is_holographic': False,
            'series_name': edition.series.name,
        }
