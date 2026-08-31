from django.db.models import Subquery, OuterRef, Exists
from django.db.models.functions import Lower
from django.urls import reverse_lazy
from django.views.generic import ListView, DetailView

from trophies.mixins import HtmxListMixin
from trophies.services import game_grouping_service as grouping
from ..models import Franchise, ConceptFranchise, Game


FRANCHISE_SORT_CHOICES = [
    ('alpha', 'Alphabetical'),
    ('alpha_inv', 'Z-A'),
    ('games', 'Most Games'),
    ('games_inv', 'Fewest Games'),
]

# Detail-page sort shared between franchise and company pages.
DETAIL_SORT_CHOICES = grouping.SORT_CHOICES


class FranchiseListView(HtmxListMixin, ListView):
    """Browse page for game franchises and collections."""
    model = Franchise
    template_name = 'trophies/franchise_list.html'
    partial_template_name = 'trophies/partials/franchise_list/browse_results.html'
    paginate_by = 32

    # User-facing language for source_type='collection' rows is "Series" (aligns
    # with the badge system's series_slug terminology). The DB still says
    # 'collection' (IGDB's term), but the URL value, chip label, card badge,
    # and About-card line all read "Series".
    _VALID_TYPE_VALUES = ('franchise', 'series', 'all')
    _DEFAULT_TYPE_VALUE = 'franchise'

    def _selected_type(self):
        """Clamped ?type= value used by both get_queryset and get_context_data.
        Junk values (and a missing param) fall through to the default chip
        so the toolbar always renders one chip as selected and the queryset
        never silently drops to no-filter for unknown inputs."""
        raw = self.request.GET.get('type', self._DEFAULT_TYPE_VALUE)
        return raw if raw in self._VALID_TYPE_VALUES else self._DEFAULT_TYPE_VALUE

    def get_queryset(self):
        # The browse page surfaces every visible franchise + every visible
        # series (source_type='collection' in the DB; "Series" everywhere
        # the user sees it). Default chip on page load is "Franchise" so
        # first-time visitors land in the familiar franchise-only view; the
        # Series and All chips reveal the rest.
        #
        # No orphan-concept rule any more: the per-card type badge already
        # prevents franchise/series confusion, and silently hiding entries
        # was burning users searching for name-shared pairs (e.g.
        # "Spider-Man franchise" + "Spider-Man series" both legitimately
        # exist on IGDB).
        type_val = self._selected_type()

        # game_count / version_count are MATERIALIZED columns (recompute_tag_covers, nightly)
        # as of 2026-08-31 -- the live version put two correlated aggregate subqueries in the
        # WHERE clause, so the paginator COUNT + the page + every scroll fetch evaluated them
        # for every one of ~1.5k franchise rows (the browse-backend audit's finding). The
        # columns carry the same semantics: distinct IGDB ids / distinct Game rows over VISIBLE
        # links -- which also makes version_count > 0 the whole eligibility rule (a franchise
        # with only excluded links denorms to 0).
        qs = super().get_queryset().filter(version_count__gt=0)

        query = self.request.GET.get('query', '').strip()
        sort_val = self.request.GET.get('sort', 'alpha')
        show_solo = self.request.GET.get('show_solo') == '1'

        if query:
            qs = qs.filter(name__icontains=query)

        # Type filter: IGDB classifies franchises and series (collections in
        # IGDB's namespace) separately. The toolbar chips let users narrow
        # to one type at a time; the default is 'franchise'.
        if type_val == 'franchise':
            qs = qs.filter(source_type='franchise')
        elif type_val == 'series':
            qs = qs.filter(source_type='collection')

        # By default, hide entries with only a single game (regardless of how
        # many versions it has) — these are usually collection-of-one noise
        # where IGDB created a collection around a single standalone title.
        # Users can opt in via the "Show single-game entries" toggle.
        if not show_solo:
            qs = qs.filter(game_count__gte=2)

        if sort_val == 'alpha_inv':
            order = [Lower('name').desc()]
        elif sort_val == 'games':
            order = ['-game_count', Lower('name')]
        elif sort_val == 'games_inv':
            order = ['game_count', Lower('name')]
        else:
            order = [Lower('name')]

        # Materialized tile cover (recompute_tag_covers) read O(1) via select_related -- no live cover subquery.
        return qs.select_related(
            'representative_game', 'representative_game__concept', 'representative_game__concept__igdb_match',
        ).defer('representative_game__concept__igdb_match__raw_response').order_by(*order)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['breadcrumb'] = [
            {'text': 'Home', 'url': reverse_lazy('home')},
            {'text': 'Franchises'},
        ]

        # Header substance scards from the hourly heartbeat (the browse-family standard): full
        # page only (guard mirrors HtmxListMixin's template selection). None until the cron
        # warms the cache; the template hides the grid.
        is_xhr = self.request.headers.get('X-Requested-With') == 'XMLHttpRequest'
        if not getattr(self.request, 'htmx', False) and not is_xhr:
            from core.services.site_heartbeat import heartbeat_values
            stats = heartbeat_values(
                'franchises_total', 'series_total', 'franchise_games', 'franchise_spinoffs')
            context['franchise_stats'] = stats if stats['franchises_total'] is not None else None

        context['sort_choices'] = FRANCHISE_SORT_CHOICES
        context['current_sort'] = self.request.GET.get('sort', 'alpha')
        context['show_solo'] = self.request.GET.get('show_solo') == '1'
        # Ordered Franchise / Series / All so the default chip ('franchise')
        # is first and the most-permissive ('all') is last.
        context['type_choices'] = (
            ('franchise', 'Franchise'),
            ('series', 'Series'),
            ('all', 'All'),
        )
        context['current_type'] = self._selected_type()
        context['seo_description'] = (
            "Browse PlayStation game franchises and series on Platinum Pursuit. "
            "Explore umbrella IPs like Resident Evil and Final Fantasy and the "
            "specific series within them."
        )
        return context


class FranchiseDetailView(DetailView):
    """Detail page for a single franchise/series showing games grouped by IGDB entry.

    Rebuilt to the Platinum standard: an accented header (cover thumb + .scard totals +
    a completion bar for signed-in viewers), one IGDB-grouped game list, and a related
    franchises/series rail (shared `.pp-gtile`). No tabs -- the legacy "also featured"
    partition is gone and the related entries moved from a tab to a bottom rail. Sort is
    the only interactive control; it swaps just the group list (`#franchise-groups`).
    """
    model = Franchise
    template_name = 'trophies/franchise_detail.html'
    # HTMX sort changes swap only the grouped list; the header + rail stay put.
    partial_template_name = 'trophies/partials/franchise_detail/game_groups_list.html'
    slug_field = 'slug'
    slug_url_kwarg = 'slug'

    def get_template_names(self):
        # On HTMX (the sort dropdown) return just the grouped-list partial so the
        # header, sort toolbar, and rail stay put. Full page otherwise so a
        # deep-linked ?sort=... URL still works for bookmarks / first paint.
        if getattr(self.request, 'htmx', False):
            return [self.partial_template_name]
        return [self.template_name]

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        franchise = self.object

        context['breadcrumb'] = [
            {'text': 'Home', 'url': reverse_lazy('home')},
            {'text': 'Franchises', 'url': reverse_lazy('franchises_list')},
            {'text': franchise.name},
        ]
        context['is_series'] = franchise.source_type == 'collection'

        # Fetch all visible concept links. Every non-excluded link contributes
        # equally to the game list; spin-off members are excluded so a Series
        # doesn't list games IGDB types as spin-offs of it (e.g. Agents of Mayhem
        # under Saints Row). Franchise-type links are never spin-offs, so the
        # spinoff clause is a no-op for them.
        links_qs = ConceptFranchise.objects.filter(
            franchise=franchise, is_excluded=False, is_spinoff=False,
        )
        concept_ids_subq = links_qs.values_list('concept_id', flat=True)

        games = list(
            Game.objects.filter(concept_id__in=Subquery(concept_ids_subq))
            .select_related('concept__igdb_match', 'concept__family')
            # raw_response is the ~30 KB IGDB blob; franchise pages list 30+ versions
            # and cover-art rendering only needs igdb_cover_image_id (CLAUDE.md).
            .defer('concept__igdb_match__raw_response')
            .order_by('title_name')
        )

        # Viewer's per-game progress (only when authenticated with a linked
        # profile). Anonymous / unlinked users get the totals-only view.
        profile = (
            getattr(self.request.user, 'profile', None)
            if self.request.user.is_authenticated else None
        )
        user_progress_map = grouping.fetch_user_progress_map(profile, games)

        # Shared service: group by IGDB id, compute per-group stats, attach
        # user progress to each game.
        groups = grouping.build_igdb_groups(games, user_progress_map=user_progress_map)

        total_trophies = sum(g['total_trophies'] for g in groups)
        platinums = sum(1 for g in groups if g['has_platinum'])
        versions_count = sum(len(g['games']) for g in groups)

        user_progress_stats = grouping.compute_user_progress_stats(
            groups, total_trophies, user_progress_map, profile=profile,
        )

        sort_val = self.request.GET.get('sort', 'release')
        groups = grouping.sort_groups(groups, sort_val)
        hero_cover = grouping.pick_hero_cover(groups)

        # Related entries of the opposite IGDB source type (series for a franchise
        # page, or vice versa), detected via shared concepts. Covers read the
        # materialized `representative_game` FK (recompute_tag_covers) so the rail
        # reuses the shared `.pp-gtile` with no live cover subqueries. game_count /
        # version_count are named to match what the tile expects.
        opposite_type = 'collection' if franchise.source_type == 'franchise' else 'franchise'

        # FULL PAGE ONLY: the rail lives in the header, and the HTMX sort swap renders just the
        # grouped list -- the Exists-annotated rail query was paid on every swap for nothing.
        related_entries = []
        if not getattr(self.request, 'htmx', False):
            related_entries = list(
                Franchise.objects.filter(source_type=opposite_type)
                .filter(Exists(ConceptFranchise.objects.filter(
                    franchise=OuterRef('pk'),
                    concept_id__in=Subquery(concept_ids_subq),
                )))
                .exclude(pk=franchise.pk)
                # game_count / version_count are the materialized columns the browse list also
                # reads (recompute_tag_covers), so a rail tile and the list agree by sharing
                # the same denorm rather than the same subquery.
                .filter(version_count__gt=0)
                .select_related(
                    'representative_game', 'representative_game__concept',
                    'representative_game__concept__igdb_match',
                )
                .defer('representative_game__concept__igdb_match__raw_response')
                .order_by(Lower('name'))
            )

        # `groups` + `empty_message` are the shared game_groups_list.html contract
        # (also fed to that partial standalone on the HTMX sort swap).
        context['groups'] = groups
        # Franchise detail runs staggerReveal on .fgroup, so the shared partial may bake pp-reveal on HTMX
        # swaps here. (Company detail sets its own group_reveal too; any page that includes this partial
        # WITHOUT reveal JS must leave it unset so its cards never stick hidden.)
        context['group_reveal'] = True
        context['empty_message'] = (
            'No games found in this series yet.' if context['is_series']
            else 'No games found in this franchise yet.'
        )
        context['hero_cover'] = hero_cover
        context['total_games'] = len(groups)
        context['total_versions'] = versions_count
        context['total_trophies'] = total_trophies
        context['total_platinums'] = platinums
        context['sort_choices'] = DETAIL_SORT_CHOICES
        context['current_sort'] = sort_val
        context['related_entries'] = related_entries
        context['related_entries_label'] = 'Series' if opposite_type == 'collection' else 'Franchises'
        context['user_progress_stats'] = user_progress_stats

        kind = 'series' if context.get('is_series') else 'franchise'
        context['seo_description'] = (
            f"Explore every PlayStation trophy list in the {franchise.name} {kind}: "
            f"{len(groups)} game{'s' if len(groups) != 1 else ''}, "
            f"{versions_count} version{'s' if versions_count != 1 else ''}. "
            f"Track platinum progress on Platinum Pursuit."
        )

        return context
