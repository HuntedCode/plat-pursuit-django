from core.services.tracking import track_page_view
from django.db.models import Count, Subquery, OuterRef, Q, Exists, IntegerField
from django.db.models.functions import Lower
from django.urls import reverse_lazy
from django.views.generic import ListView, DetailView

from trophies.mixins import ProfileHotbarMixin, HtmxListMixin
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


# Browse / detail cover-art filter. With is_main gone, every non-excluded link
# is equal — we just need to skip admin-excluded rows and collection spin-offs
# (so a series' cover isn't anchored by a spin-off). is_spinoff is always False
# on franchise-type links, so the spinoff clause is a no-op for franchises.
#
# BEST-EFFORT for cover art only: this filter is applied to a Game queryset as a SECOND
# .filter() after the OuterRef franchise correlation, so Django joins a separate
# ConceptFranchise row -- the flags aren't guaranteed to come from the same membership
# that ties the game to this franchise. The exact suppression (same-row) lives where
# it matters: the detail member list (FranchiseDetailView.links_qs) and the browse
# counts (visible_link_filter below), both of which filter ConceptFranchise rows directly.
# Cover art is cosmetic, so the residual leak is acceptable.
_VISIBLE_LINK_FILTER = Q(
    concept__concept_franchises__is_excluded=False,
    concept__concept_franchises__is_spinoff=False,
)


def _franchise_cover_annotations():
    """Build the cover-art Subquery annotations for Franchise rows.

    All non-excluded, non-spin-off links contribute. Tiebreak comes from
    `_MOST_RECENT_RELEASE_ORDER` inside the grouping subqueries.
    """
    path = 'concept__concept_franchises__franchise'
    extra = _VISIBLE_LINK_FILTER
    return {
        'representative_title_image': grouping.representative_title_image_subquery(
            through_path=path, extra_filter=extra,
        ),
        'representative_concept_icon': grouping.representative_concept_icon_subquery(
            through_path=path, extra_filter=extra,
        ),
        'representative_igdb_cover_id': grouping.representative_igdb_cover_id_subquery(
            through_path=path, extra_filter=extra,
        ),
        'representative_title_icon': grouping.representative_title_icon_subquery(
            through_path=path, extra_filter=extra,
        ),
    }


class FranchiseListView(HtmxListMixin, ProfileHotbarMixin, ListView):
    """Browse page for game franchises and collections."""
    model = Franchise
    template_name = 'trophies/franchise_list.html'
    partial_template_name = 'trophies/partials/franchise_list/browse_results.html'
    paginate_by = 32

    def get_queryset(self):
        # The browse page surfaces two kinds of entries:
        #
        # 1. Franchises (source_type='franchise') with at least one non-excluded
        #    link. Every IGDB-listed game contributes equally now (no main vs.
        #    tie-in distinction); admins can hide bad links via is_excluded.
        #
        # 2. Collections (source_type='collection') that contain at least one
        #    "orphan" game — a game with no franchise-type link at all. These
        #    are franchises like "Astro Bot" where IGDB only classifies them
        #    via the collections taxonomy, not the franchises taxonomy. Without
        #    surfacing them here, the games would be invisible on browse.
        #    Collections that DON'T have any orphan games (e.g. "Resident Evil
        #    Main Series" — every member already has the Resident Evil franchise)
        #    stay hidden to avoid duplicate-looking entries.

        # Subquery: this collection has at least one concept with zero
        # franchise-type links.
        orphan_concept_exists = Exists(
            ConceptFranchise.objects.filter(
                franchise=OuterRef('pk'),
            ).exclude(
                concept__concept_franchises__franchise__source_type='franchise',
            )
        )

        # Eligibility check via Exists: a franchise row is browse-visible if
        # it's a collection OR has at least one non-excluded link.
        eligible_link_exists = Exists(
            ConceptFranchise.objects.filter(
                franchise=OuterRef('pk'), is_excluded=False,
            )
        )

        # Per-franchise game_count / version_count via Subquery so each row
        # carries its own scoped count instead of joining the outer query
        # against franchise_concepts. This keeps the outer queryset at one
        # row per franchise.
        def _per_franchise_count(field, distinct=True, extra_filter=None):
            qs = ConceptFranchise.objects.filter(franchise=OuterRef('pk'))
            if extra_filter is not None:
                qs = qs.filter(extra_filter)
            return Subquery(
                qs.values('franchise')
                .annotate(c=Count(field, distinct=distinct))
                .values('c')[:1],
                output_field=IntegerField(),
            )

        # Counts include every non-excluded link, minus collection spin-offs
        # (which are hidden from the series, so they must not pad its
        # game/version counts). is_spinoff is always False on franchise-type
        # links, so the spinoff clause is a no-op for franchises.
        visible_link_filter = Q(is_excluded=False, is_spinoff=False)

        qs = super().get_queryset().filter(
            Q(source_type='collection') | eligible_link_exists,
        ).annotate(
            # game_count: distinct IGDB game IDs (the true "game" count).
            # Two concepts sharing the same igdb_id (e.g. PS3 and PS4
            # Stick of Truth) count as ONE game. Concepts without an IGDB
            # match are excluded (NULL igdb_id ignored by COUNT DISTINCT)
            # which slightly undercounts, but in practice nearly all
            # concepts in franchise/collection pages have IGDB matches.
            game_count=_per_franchise_count(
                'concept__igdb_match__igdb_id', extra_filter=visible_link_filter,
            ),
            # version_count: distinct Games, i.e. individual PSN records
            # (a game on both PS4 and PS5 counts as 2 versions of 1 game).
            version_count=_per_franchise_count(
                'concept__games', extra_filter=visible_link_filter,
            ),
            **_franchise_cover_annotations(),
            has_orphan_concept=orphan_concept_exists,
        ).filter(
            # Franchises always pass the type filter above; collections must
            # additionally have at least one orphan concept (a concept with no
            # franchise-type link). Prevents duplicate-looking entries.
            Q(source_type='franchise', version_count__gt=0)
            | Q(source_type='collection', version_count__gt=0, has_orphan_concept=True),
        )

        query = self.request.GET.get('query', '').strip()
        sort_val = self.request.GET.get('sort', 'alpha')
        show_solo = self.request.GET.get('show_solo') == '1'

        if query:
            qs = qs.filter(name__icontains=query)

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

        return qs.order_by(*order)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['breadcrumb'] = [
            {'text': 'Home', 'url': reverse_lazy('home')},
            {'text': 'Franchises'},
        ]
        context['sort_choices'] = FRANCHISE_SORT_CHOICES
        context['current_sort'] = self.request.GET.get('sort', 'alpha')
        context['show_solo'] = self.request.GET.get('show_solo') == '1'
        context['seo_description'] = (
            "Browse PlayStation game franchises on Platinum Pursuit. "
            "Explore series like Resident Evil, Final Fantasy, and more."
        )
        track_page_view('franchises_list', 'list', self.request)
        return context


class FranchiseDetailView(ProfileHotbarMixin, DetailView):
    """Detail page for a single franchise showing games grouped by IGDB entry."""
    model = Franchise
    template_name = 'trophies/franchise_detail.html'
    partial_template_name = 'trophies/partials/franchise_detail/tab_content.html'
    slug_field = 'slug'
    slug_url_kwarg = 'slug'

    def get_template_names(self):
        # On HTMX requests (sort dropdown), return only the tab-content
        # partial so the page header, tabs, and ad slot stay put. Non-HTMX
        # requests render the full page so deep-linked ?sort=... / ?tab=...
        # URLs still work for bookmarks / first paint.
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

        # Fetch all visible concept links. With is_main gone, every non-excluded
        # link contributes equally to the franchise's game list. Spin-off members
        # are still excluded so a Series doesn't list games IGDB types as spin-offs
        # of it (e.g. Agents of Mayhem under Saints Row). Franchise-type links
        # are never spin-offs, so the spinoff clause is a no-op for them.
        links_qs = ConceptFranchise.objects.filter(
            franchise=franchise, is_excluded=False, is_spinoff=False,
        )
        concept_ids_subq = links_qs.values_list('concept_id', flat=True)

        games = list(
            Game.objects.filter(concept_id__in=Subquery(concept_ids_subq))
            .select_related('concept__igdb_match', 'concept__family')
            .defer(
                # See CLAUDE.md: raw_response is the IGDB API blob (~30 KB per
                # row). Franchise pages can list 30+ versions of every entry;
                # cover-art rendering only needs igdb_cover_image_id.
                'concept__igdb_match__raw_response',
            )
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
        all_groups = grouping.build_igdb_groups(
            games,
            user_progress_map=user_progress_map,
        )

        # Single unified game list for both franchise- and collection-type
        # pages. The legacy "main vs also-featured" partition is gone — every
        # IGDB-listed link counts equally now. main_groups is kept as the
        # context-var name for template back-compat; also_featured_groups is
        # an empty list that the template's optional block renders as nothing.
        main_groups = all_groups
        also_featured_groups = []

        # Aggregate stats include every non-excluded linked game.
        total_trophies = sum(g['total_trophies'] for g in main_groups)
        platinums = sum(1 for g in main_groups if g['has_platinum'])
        main_versions_count = sum(len(g['games']) for g in main_groups)

        user_progress_stats = grouping.compute_user_progress_stats(
            main_groups, total_trophies, user_progress_map, profile=profile,
        )

        # Apply user-selected sort.
        sort_val = self.request.GET.get('sort', 'release')
        main_groups = grouping.sort_groups(main_groups, sort_val)

        # Pick hero cover from the unified list.
        hero_cover = grouping.pick_hero_cover(main_groups)

        # Related entries of the opposite IGDB source type (collections for a
        # franchise page, or vice versa). Detected via shared concepts: any
        # Franchise row that links to at least one concept in this franchise.
        opposite_type = 'collection' if franchise.source_type == 'franchise' else 'franchise'

        # Find candidate related franchises via Exists (a row matches if any
        # of its links touches one of this franchise's concepts), then
        # annotate counts as Subqueries to avoid joining the outer row with
        # franchise_concepts a second time.
        def _related_count(field, distinct=True):
            return Subquery(
                ConceptFranchise.objects.filter(franchise=OuterRef('pk'))
                .values('franchise')
                .annotate(c=Count(field, distinct=distinct))
                .values('c')[:1],
                output_field=IntegerField(),
            )

        related_entries = list(
            Franchise.objects.filter(
                source_type=opposite_type,
            ).filter(
                Exists(ConceptFranchise.objects.filter(
                    franchise=OuterRef('pk'),
                    concept_id__in=Subquery(concept_ids_subq),
                ))
            )
            .exclude(pk=franchise.pk)
            .annotate(
                related_game_count=_related_count('concept'),
                related_version_count=_related_count('concept__games'),
                **_franchise_cover_annotations(),
            )
            .filter(related_version_count__gt=0)
            .order_by(Lower('name'))
        )

        # Tab selection. Three tabs:
        #   - games: main_groups (this franchise IS their primary identity)
        #   - also_featured: tie-in groups (franchise pages only)
        #   - collections: related_entries (when present)
        # Tabs auto-fall-back to 'games' when their content is empty so a stale
        # querystring doesn't strand users on a blank tab.
        current_tab = self.request.GET.get('tab', 'games')
        if current_tab == 'collections' and not related_entries:
            current_tab = 'games'
        if current_tab == 'also_featured' and not also_featured_groups:
            current_tab = 'games'

        context['main_groups'] = main_groups
        context['also_featured_groups'] = also_featured_groups
        context['hero_cover'] = hero_cover
        context['total_games'] = len(main_groups)
        context['total_versions'] = main_versions_count
        context['total_trophies'] = total_trophies
        context['total_platinums'] = platinums
        context['also_featured_count'] = len(also_featured_groups)
        context['sort_choices'] = DETAIL_SORT_CHOICES
        context['current_sort'] = sort_val
        context['related_entries'] = related_entries
        context['related_entries_label'] = (
            'Collections' if opposite_type == 'collection' else 'Franchises'
        )
        context['current_tab'] = current_tab
        context['user_progress_stats'] = user_progress_stats

        context['seo_description'] = (
            f"Explore the {franchise.name} franchise on Platinum Pursuit. "
            f"{len(main_groups)} game{'s' if len(main_groups) != 1 else ''}, "
            f"{main_versions_count} version{'s' if main_versions_count != 1 else ''}."
        )

        track_page_view('franchise_detail', franchise.id, self.request)
        return context
