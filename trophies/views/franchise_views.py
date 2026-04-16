from core.services.tracking import track_page_view
from django.db.models import Count, Subquery, OuterRef, Q, Exists
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


# Franchise browse-page cover art has a wrinkle: franchise-type rows need games
# filtered by is_main=True, collection-type rows need any link. The Q clause
# below expresses "franchise-type requires is_main, collection-type doesn't"
# in a single filter so one subquery serves both row types on the mixed browse
# list.
_MAIN_ONLY_FILTER = (
    Q(concept__concept_franchises__franchise__source_type='collection')
    | Q(concept__concept_franchises__is_main=True)
)


def _franchise_cover_annotations(*, main_only: bool):
    """Build the three cover-art Subquery annotations for Franchise rows.

    ``main_only=True`` (browse + franchise detail hero) filters games where
    this franchise is the main. ``main_only=False`` (Collections tab) allows
    any link.
    """
    path = 'concept__concept_franchises__franchise'
    extra = _MAIN_ONLY_FILTER if main_only else None
    return {
        'representative_title_image': grouping.representative_title_image_subquery(
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
        # 1. Franchises (source_type='franchise') that are the MAIN franchise
        #    of at least one game. Tie-in-only franchises like "Frozen" stay
        #    hidden — they're discoverable via the "Also Featured" tab on
        #    whichever franchise IS that game's main.
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
        # franchise-type links. Outer ref = the Franchise (collection) row.
        orphan_concept_exists = Exists(
            ConceptFranchise.objects.filter(
                franchise=OuterRef('pk'),
            ).exclude(
                concept__concept_franchises__franchise__source_type='franchise',
            )
        )

        # The main-link filter. Used twice: once to restrict which links
        # contribute to game_count/version_count, and once in the outer filter.
        main_link_filter = (
            Q(source_type='franchise', franchise_concepts__is_main=True)
            | Q(source_type='collection')
        )

        qs = super().get_queryset().filter(
            Q(source_type='franchise', franchise_concepts__is_main=True)
            | Q(source_type='collection')
        ).annotate(
            # game_count: distinct concepts (IGDB-unified games — one per
            # main entry regardless of platform/region). This is what the
            # card label "X games" means to users.
            game_count=Count(
                'franchise_concepts__concept',
                filter=main_link_filter,
                distinct=True,
            ),
            # version_count: distinct Games, i.e. individual PSN records
            # (a game on both PS4 and PS5 counts as 2 versions of 1 game).
            version_count=Count(
                'franchise_concepts__concept__games',
                filter=main_link_filter,
                distinct=True,
            ),
            **_franchise_cover_annotations(main_only=True),
            has_orphan_concept=orphan_concept_exists,
        ).filter(
            # Franchises always pass the type filter above; collections must
            # additionally have at least one orphan concept (a concept with no
            # franchise-type link). Prevents duplicate-looking entries.
            Q(source_type='franchise', version_count__gt=0)
            | Q(source_type='collection', version_count__gt=0, has_orphan_concept=True),
        ).distinct()

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
    slug_field = 'slug'
    slug_url_kwarg = 'slug'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        franchise = self.object

        context['breadcrumb'] = [
            {'text': 'Home', 'url': reverse_lazy('home')},
            {'text': 'Franchises', 'url': reverse_lazy('franchises_list')},
            {'text': franchise.name},
        ]

        # Fetch all concept links and partition by is_main up-front. The map
        # gets passed to build_igdb_groups via extra_per_group so each group
        # knows whether the link is this franchise's primary identity.
        concept_links = ConceptFranchise.objects.filter(
            franchise=franchise
        ).values('concept_id', 'is_main')
        is_main_by_concept = {
            row['concept_id']: {'is_main': row['is_main']} for row in concept_links
        }
        concept_ids_subq = ConceptFranchise.objects.filter(
            franchise=franchise
        ).values_list('concept_id', flat=True)

        games = list(
            Game.objects.filter(concept_id__in=Subquery(concept_ids_subq))
            .select_related('concept__igdb_match')
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
        # user progress to each game. is_main is attached per concept via
        # extra_per_group so we can partition after grouping.
        all_groups = grouping.build_igdb_groups(
            games,
            user_progress_map=user_progress_map,
            extra_per_group=is_main_by_concept,
        )

        # Partition into main vs also-featured. For collection-type detail
        # pages (rare, direct URL only), is_main is always False so everything
        # ends up in "main" — collections don't have a primary/secondary
        # distinction to express.
        if franchise.source_type == 'franchise':
            main_groups = [g for g in all_groups if g.get('is_main')]
            also_featured_groups = [g for g in all_groups if not g.get('is_main')]
        else:
            main_groups = all_groups
            also_featured_groups = []

        # Aggregate stats come from the MAIN set only — tie-ins shouldn't pad
        # the franchise-wide totals or anchor the hero image.
        total_trophies = sum(g['total_trophies'] for g in main_groups)
        platinums = sum(1 for g in main_groups if g['has_platinum'])
        main_versions_count = sum(len(g['games']) for g in main_groups)

        user_progress_stats = grouping.compute_user_progress_stats(
            main_groups, total_trophies, user_progress_map, profile=profile,
        )

        # Apply user-selected sort to BOTH lists independently.
        sort_val = self.request.GET.get('sort', 'release')
        main_groups = grouping.sort_groups(main_groups, sort_val)
        also_featured_groups = grouping.sort_groups(also_featured_groups, sort_val)

        # Pick hero cover from main groups (newest first). If the franchise has
        # no main games yet (data not re-enriched), fall back to all groups.
        hero_cover = grouping.pick_hero_cover(main_groups) or grouping.pick_hero_cover(all_groups)

        # Related entries of the opposite IGDB source type (collections for a
        # franchise page, or vice versa). Detected via shared concepts: any
        # Franchise row that links to at least one concept in this franchise.
        opposite_type = 'collection' if franchise.source_type == 'franchise' else 'franchise'
        related_entries = list(
            Franchise.objects.filter(
                source_type=opposite_type,
                franchise_concepts__concept_id__in=Subquery(concept_ids_subq),
            )
            .exclude(pk=franchise.pk)
            .annotate(
                related_game_count=Count(
                    'franchise_concepts__concept__games', distinct=True,
                ),
                # Collections never have is_main=True, so allow any link.
                **_franchise_cover_annotations(main_only=False),
            )
            .filter(related_game_count__gt=0)
            .distinct()
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
