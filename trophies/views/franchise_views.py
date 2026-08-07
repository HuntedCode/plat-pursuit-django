from core.services.tracking import track_page_view
from django.db.models import Count, Subquery, OuterRef, Q, Exists, IntegerField
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

# Visible-link filter for per-franchise counts: skip admin-excluded rows and collection spin-offs (so a
# series' counts aren't padded by games it hides). is_spinoff is always False on franchise-type links, so
# the spinoff clause is a no-op there.
_VISIBLE_LINK_FILTER = Q(is_excluded=False, is_spinoff=False)


def _visible_link_count(field, distinct=True):
    """A per-franchise COUNT(DISTINCT field) over VISIBLE links, scoped to the outer Franchise row via a
    Subquery (keeps the outer queryset at one row per franchise). Shared by the browse list counts and the
    detail related-rail so a rail tile and the browse list report the SAME game/version totals for an entity."""
    return Subquery(
        ConceptFranchise.objects.filter(franchise=OuterRef('pk'))
        .filter(_VISIBLE_LINK_FILTER)
        .values('franchise').annotate(c=Count(field, distinct=distinct)).values('c')[:1],
        output_field=IntegerField(),
    )


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

        # Eligibility check via Exists: a franchise row is browse-visible if
        # it's a series (source_type='collection') OR has at least one
        # non-excluded link.
        eligible_link_exists = Exists(
            ConceptFranchise.objects.filter(
                franchise=OuterRef('pk'), is_excluded=False,
            )
        )

        # Per-franchise game_count / version_count via the shared _visible_link_count Subquery so each row
        # carries its own scoped count (over VISIBLE links) instead of joining the outer query against
        # franchise_concepts. This keeps the outer queryset at one row per franchise, and the detail rail
        # reuses the same helper so its tile counts match this page's for the same entity.
        qs = super().get_queryset().filter(
            Q(source_type='collection') | eligible_link_exists,
        ).annotate(
            # game_count: distinct IGDB game IDs (the true "game" count).
            # Two concepts sharing the same igdb_id (e.g. PS3 and PS4
            # Stick of Truth) count as ONE game. Concepts without an IGDB
            # match are excluded (NULL igdb_id ignored by COUNT DISTINCT)
            # which slightly undercounts, but in practice nearly all
            # concepts in franchise/series pages have IGDB matches.
            game_count=_visible_link_count('concept__igdb_match__igdb_id'),
            # version_count: distinct Games, i.e. individual PSN records
            # (a game on both PS4 and PS5 counts as 2 versions of 1 game).
            version_count=_visible_link_count('concept__games'),
        ).filter(
            Q(source_type='franchise', version_count__gt=0)
            | Q(source_type='collection', version_count__gt=0),
        )

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
        track_page_view('franchises_list', 'list', self.request)
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

        related_entries = list(
            Franchise.objects.filter(source_type=opposite_type)
            .filter(Exists(ConceptFranchise.objects.filter(
                franchise=OuterRef('pk'),
                concept_id__in=Subquery(concept_ids_subq),
            )))
            .exclude(pk=franchise.pk)
            .annotate(
                # Shared with the browse list, over VISIBLE links, so a rail tile and the list agree.
                game_count=_visible_link_count('concept__igdb_match__igdb_id'),
                version_count=_visible_link_count('concept__games'),
            )
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

        context['seo_description'] = (
            f"Explore the {franchise.name} franchise on Platinum Pursuit. "
            f"{len(groups)} game{'s' if len(groups) != 1 else ''}, "
            f"{versions_count} version{'s' if versions_count != 1 else ''}."
        )

        track_page_view('franchise_detail', franchise.id, self.request)
        return context
