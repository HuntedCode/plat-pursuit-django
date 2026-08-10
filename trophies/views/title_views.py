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
  - **All**           -- the full live catalogue, each with what earns it + how many hunters wear it.

Whale-safe by construction: every query is bounded by the badge catalogue or the viewer's own title
count. Nothing iterates trophies.
"""
from bisect import bisect_left

from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Count, Prefetch
from django.db.models.functions import Lower
from django.shortcuts import redirect
from django.urls import reverse, reverse_lazy
from django.views.generic import TemplateView

from ..models import BadgeSeries, GroupBadge, SeriesBadgeStanding, UserTitle
from trophies.services.badge_detail_service import group_medallion_layers

#: Rarity bands, scarcest first, as percentile cut-points over the CLAIMED catalogue. Relative rather
#: than absolute: fixed thresholds ("under 100 holders is rare") rot as the site grows, and rarity as a
#: share of all profiles reproduces the PSN problem where every number reads ultra-rare. A percentile is
#: self-normalising and always produces a spread.
#:
#: These keys never reach the reader -- they select the plate's MATERIAL, while the visible text stays
#: the honest holder count. Invented tier names shouted at the user are anti-reference #4.
TITLE_RARITY_BANDS = (
    ('scarce', 0.10),
    ('uncommon', 0.30),
    ('common', 0.60),
    ('widespread', 1.01),      # > 1 so the last band is a genuine catch-all for pct == 1.0
)

#: Below this many claimed titles a percentile is noise -- with four titles in a dev database, one of
#: them is "the scarcest 10%" by arithmetic alone. Under the floor every plate renders at the base
#: material, which is honest: we don't know enough to rank them yet.
TITLE_RARITY_MIN_CATALOGUE = 8


def rarity_bands(title_ids, holders):
    """title_id -> band key, for the plate's material.

    RELATIVE data, so it is computed live on every render and never stored -- the same rule the badge
    system applies to rank and rarity (materialize facts, keep relative standings live, or they go
    stale and unfair). It costs no query: `holders` is already fetched as one grouped COUNT, and the
    loop is bounded by the badge catalogue, never by anyone's trophies.

    A title nobody holds is `unclaimed`, NOT the rarest band. A newly released series has no holders
    because it is new, and dressing that as the page's most prestigious object would be a lie.
    """
    counts = {tid: holders.get(tid, 0) for tid in title_ids}
    claimed = sorted(c for c in counts.values() if c > 0)
    if len(claimed) < TITLE_RARITY_MIN_CATALOGUE:
        return {tid: '' for tid in counts}

    bands = {}
    for tid, count in counts.items():
        if not count:
            bands[tid] = 'unclaimed'
            continue
        # Share of the claimed catalogue strictly scarcer than this title. bisect_left (not _right)
        # makes ties share a band -- two titles held by 40 hunters are equally rare, and splitting them
        # on list order would hand one a richer plate for no reason.
        pct = bisect_left(claimed, count) / len(claimed)
        bands[tid] = next(key for key, cut in TITLE_RARITY_BANDS if pct < cut)
    return bands


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

        # ── 4. Social proof: how many hunters wear each title. One grouped COUNT over the catalogue.
        holders = dict(
            UserTitle.objects
            .filter(title_id__in=[s.title_id for s in series_list])
            .values('title_id').annotate(c=Count('id'))
            .values_list('title_id', 'c')
        )

        # Rarity band per title -- the plate's material. Derived from `holders` above, so no extra query.
        bands = rarity_bands([s.title_id for s in series_list], holders)

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
                'rarity': bands.get(series.title_id, ''),
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
                    # NOT 'unclaimed' -- the hunter is holding it. These sit outside the live catalogue
                    # (a one-off award, or a series taken off-live), so there is nothing to rank them
                    # against; they render at the base material and `is_special` carries the flavour.
                    'rarity': '',
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
