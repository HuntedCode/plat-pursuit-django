from collections import OrderedDict

from core.services.tracking import track_page_view
from django.db.models import Count, Subquery, OuterRef, F, Q, Exists
from django.db.models.functions import Lower
from django.urls import reverse_lazy
from django.views.generic import ListView, DetailView

from trophies.mixins import ProfileHotbarMixin, HtmxListMixin
from ..models import Franchise, ConceptFranchise, Game, ProfileGame


FRANCHISE_SORT_CHOICES = [
    ('alpha', 'Alphabetical'),
    ('alpha_inv', 'Z-A'),
    ('games', 'Most Games'),
    ('games_inv', 'Fewest Games'),
]

DETAIL_SORT_CHOICES = [
    ('release', 'Release Date (Oldest First)'),
    ('release_desc', 'Release Date (Newest First)'),
    ('alpha', 'Alphabetical (A-Z)'),
    ('alpha_desc', 'Alphabetical (Z-A)'),
    ('versions', 'Most Versions'),
    ('trophies', 'Most Trophies'),
]


def _most_recent_release_ordering():
    """Ordering used when picking a representative game for a franchise:
    most recent IGDB release date first, with title_name tiebreak.
    """
    return [
        F('concept__igdb_match__igdb_first_release_date').desc(nulls_last=True),
        'title_name',
    ]


def _base_representative_qs(*, main_only):
    """Common Game queryset used by all three cover-art subqueries.

    ``main_only=True`` (browse + franchise detail header) restricts to games
    where the link is_main=True for franchise-type rows; for collection-type
    rows it accepts any link (collections never have is_main=True). This dual
    behavior keeps a single subquery working across the mixed browse list.

    ``main_only=False`` (Collections tab inside a franchise detail) accepts
    any link unconditionally — every link there is a tie-in by definition.
    """
    qs = Game.objects.filter(
        concept__concept_franchises__franchise=OuterRef('pk'),
    )
    if main_only:
        # Franchise-type rows: require is_main=True. Collection-type rows: any link.
        qs = qs.filter(
            Q(concept__concept_franchises__franchise__source_type='collection')
            | Q(concept__concept_franchises__is_main=True)
        )
    return qs


def _representative_title_image_subquery(*, main_only=True):
    """Most-recent game's PSN ``title_image`` (tier 1 of the cover-art chain)."""
    return Subquery(
        _base_representative_qs(main_only=main_only)
        .exclude(title_image__isnull=True)
        .exclude(title_image='')
        .order_by(*_most_recent_release_ordering())
        .values('title_image')[:1]
    )


def _representative_igdb_cover_id_subquery(*, main_only=True):
    """Most-recent game's ``igdb_cover_image_id`` (tier 2 fallback). Used to
    build an IGDB cover URL in the template when ``title_image`` is missing.
    """
    return Subquery(
        _base_representative_qs(main_only=main_only)
        .filter(concept__igdb_match__isnull=False)
        .exclude(concept__igdb_match__igdb_cover_image_id='')
        .order_by(*_most_recent_release_ordering())
        .values('concept__igdb_match__igdb_cover_image_id')[:1]
    )


def _representative_title_icon_subquery(*, main_only=True):
    """Most-recent game's ``title_icon_url`` (tier 3 fallback, generic PS icon)."""
    return Subquery(
        _base_representative_qs(main_only=main_only)
        .exclude(title_icon_url__isnull=True)
        .exclude(title_icon_url='')
        .order_by(*_most_recent_release_ordering())
        .values('title_icon_url')[:1]
    )


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
        from ..models import ConceptFranchise

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
            representative_title_image=_representative_title_image_subquery(),
            representative_igdb_cover_id=_representative_igdb_cover_id_subquery(),
            representative_title_icon=_representative_title_icon_subquery(),
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

        # Fetch all concept links for this franchise so we know which concepts
        # are "main" (this franchise IS their primary identity) vs "also
        # featured" (this franchise appears in their tie-ins).
        concept_links = ConceptFranchise.objects.filter(
            franchise=franchise
        ).values('concept_id', 'is_main')
        concept_is_main_map = {row['concept_id']: row['is_main'] for row in concept_links}
        concept_ids_subq = ConceptFranchise.objects.filter(
            franchise=franchise
        ).values_list('concept_id', flat=True)

        games = list(
            Game.objects.filter(concept_id__in=Subquery(concept_ids_subq))
            .select_related('concept__igdb_match')
            .order_by('title_name')
        )

        # Fetch the viewer's per-game progress (only when authenticated with a
        # linked profile). Anonymous and unlinked users get the totals-only view.
        profile = None
        if self.request.user.is_authenticated:
            profile = getattr(self.request.user, 'profile', None)

        user_progress_map = {}  # {game_id: ProfileGame}
        if profile and games:
            user_progress_map = {
                pg.game_id: pg
                for pg in ProfileGame.objects.filter(
                    profile=profile, game_id__in=[g.id for g in games],
                )
            }

        # Group games by IGDB ID (same IGDB entry = same game, different versions)
        igdb_groups = OrderedDict()
        ungrouped = []

        for game in games:
            user_pg = user_progress_map.get(game.id)
            # Annotate game with user progress so the template can access it
            # directly on the version row (e.g. {{ game.user_pg.progress }}).
            game.user_pg = user_pg

            is_main_link = concept_is_main_map.get(game.concept_id, False) if game.concept_id else False

            igdb_match = getattr(game.concept, 'igdb_match', None) if game.concept else None
            if igdb_match and igdb_match.igdb_id:
                igdb_id = igdb_match.igdb_id
                if igdb_id not in igdb_groups:
                    igdb_groups[igdb_id] = {
                        'igdb_id': igdb_id,
                        'display_name': igdb_match.igdb_name or game.title_name,
                        'cover_url': self._get_cover_url(game),
                        'release_date': igdb_match.igdb_first_release_date,
                        'is_main': is_main_link,
                        'games': [],
                    }
                igdb_groups[igdb_id]['games'].append(game)
            else:
                ungrouped.append({
                    'igdb_id': None,
                    'display_name': game.title_name,
                    'cover_url': self._get_cover_url(game),
                    'release_date': None,
                    'is_main': is_main_link,
                    'games': [game],
                })

        # All groups, partitioned later. The aggregate stats below cover
        # the main set only — that's the canonical "this franchise" view.
        all_groups = list(igdb_groups.values()) + ungrouped

        # Compute per-group stats for ALL groups (we need them for both tabs).
        for group in all_groups:
            group_trophies = 0
            group_has_platinum = False
            group_user_earned = 0
            group_user_plat = False
            group_user_any = False
            group_best_pct = 0
            for game in group['games']:
                dt = game.defined_trophies or {}
                group_trophies += sum(dt.get(k, 0) for k in ('bronze', 'silver', 'gold', 'platinum'))
                if dt.get('platinum', 0) > 0:
                    group_has_platinum = True
                pg = getattr(game, 'user_pg', None)
                if pg:
                    group_user_any = True
                    group_user_earned += pg.earned_trophies_count or 0
                    if pg.has_plat:
                        group_user_plat = True
                    if pg.progress and pg.progress > group_best_pct:
                        group_best_pct = pg.progress
            group['total_trophies'] = group_trophies
            group['has_platinum'] = group_has_platinum
            group['user_any_progress'] = group_user_any
            group['user_earned_trophies'] = group_user_earned
            group['user_plat_earned'] = group_user_plat
            group['user_best_progress'] = group_best_pct

        # Partition into main vs also-featured. For collection-type detail
        # pages (rare, direct URL only), is_main is always False so everything
        # ends up in "main" — which is correct since collections don't have
        # a primary/secondary distinction to express.
        if franchise.source_type == 'franchise':
            main_groups = [g for g in all_groups if g['is_main']]
            also_featured_groups = [g for g in all_groups if not g['is_main']]
        else:
            main_groups = all_groups
            also_featured_groups = []

        # Aggregate stats / hero cover are derived from the MAIN set only.
        # That's the canonical "this franchise" view; tie-ins shouldn't pad
        # the franchise-wide totals or anchor the hero image.
        total_trophies = sum(g['total_trophies'] for g in main_groups)
        platinums = sum(1 for g in main_groups if g['has_platinum'])
        # game_ids in the main set, used to scope the user stats query below.
        main_game_ids = {g.id for grp in main_groups for g in grp['games']}
        main_user_progress = {
            gid: pg for gid, pg in user_progress_map.items() if gid in main_game_ids
        }
        main_versions_count = sum(len(g['games']) for g in main_groups)

        # Franchise-wide user stats (only when we have a linked profile).
        # Totals are denominator; user values are numerator. Zero-progress
        # profiles still see 0/X values — this is a motivating view, not a hide.
        user_franchise_stats = None
        if profile:
            games_played = sum(1 for g in main_groups if g['user_any_progress'])
            games_platinumed = sum(1 for g in main_groups if g['user_plat_earned'])
            trophies_earned = sum(pg.earned_trophies_count or 0 for pg in main_user_progress.values())
            completion_pct = (
                round((trophies_earned / total_trophies) * 100) if total_trophies else 0
            )
            user_franchise_stats = {
                'games_played': games_played,
                'games_platinumed': games_platinumed,
                'versions_played': len(main_user_progress),
                'trophies_earned': trophies_earned,
                'completion_pct': completion_pct,
            }

        # Apply user-selected sort to BOTH lists independently.
        sort_val = self.request.GET.get('sort', 'release')
        main_groups = self._sort_game_groups(main_groups, sort_val)
        also_featured_groups = self._sort_game_groups(also_featured_groups, sort_val)

        # Pick hero cover from main groups (newest first). If the franchise has
        # no main games yet (data not re-enriched), fall back to all groups.
        hero_cover = self._pick_hero_cover(main_groups) or self._pick_hero_cover(all_groups)

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
                representative_title_image=_representative_title_image_subquery(main_only=False),
                representative_igdb_cover_id=_representative_igdb_cover_id_subquery(main_only=False),
                representative_title_icon=_representative_title_icon_subquery(main_only=False),
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
        context['user_franchise_stats'] = user_franchise_stats

        context['seo_description'] = (
            f"Explore the {franchise.name} franchise on Platinum Pursuit. "
            f"{len(main_groups)} game{'s' if len(main_groups) != 1 else ''}, "
            f"{main_versions_count} version{'s' if main_versions_count != 1 else ''}."
        )

        track_page_view('franchise_detail', franchise.id, self.request)
        return context

    @staticmethod
    def _pick_hero_cover(groups):
        """Return the cover URL of the most-recently-released group with art,
        or empty string if none of the groups have any cover.
        """
        for group in sorted(
            groups,
            key=lambda g: (
                g['release_date'].timestamp() if g['release_date'] else float('-inf')
            ),
            reverse=True,
        ):
            if group['cover_url']:
                return group['cover_url']
        return ''

    @staticmethod
    def _sort_game_groups(groups, sort_val):
        """Sort the grouped games list according to the selected sort key."""
        # Far-future sentinel so games with no release date sort to the end on
        # ascending sorts and to the start on descending sorts (inverted).
        no_date_asc = float('inf')
        no_date_desc = float('-inf')

        def release_key_asc(g):
            return g['release_date'].timestamp() if g['release_date'] else no_date_asc

        def release_key_desc(g):
            return g['release_date'].timestamp() if g['release_date'] else no_date_desc

        if sort_val == 'release_desc':
            return sorted(groups, key=lambda g: (release_key_desc(g), g['display_name'].lower()), reverse=True)
        if sort_val == 'alpha':
            return sorted(groups, key=lambda g: g['display_name'].lower())
        if sort_val == 'alpha_desc':
            return sorted(groups, key=lambda g: g['display_name'].lower(), reverse=True)
        if sort_val == 'versions':
            return sorted(groups, key=lambda g: (-len(g['games']), g['display_name'].lower()))
        if sort_val == 'trophies':
            return sorted(groups, key=lambda g: (-g['total_trophies'], g['display_name'].lower()))
        # Default: release (oldest first)
        return sorted(groups, key=lambda g: (release_key_asc(g), g['display_name'].lower()))

    @staticmethod
    def _get_cover_url(game):
        """Get the best cover image URL for a game, following the standard
        template fallback chain: title_image → concept.cover_url (which
        resolves to IGDB cover for trusted matches) → title_icon_url.
        See docs/reference/design-system.md (Image Handling section).
        """
        if game.title_image and not game.force_title_icon:
            return game.title_image
        if game.concept and game.concept.cover_url:
            return game.concept.cover_url
        return game.title_icon_url or ''
