"""
Game rating aggregation service.

This module handles the calculation and caching of community rating averages
for game concepts, including difficulty, grindiness, fun, and time estimates.
Supports both base game ratings (concept_trophy_group=NULL) and DLC group ratings.
"""
from django.db.models import Avg, Count, Max, Q, Sum
from django.db.models.functions import Lower
from django.core.cache import cache
from trophies.util_modules.language import calculate_trimmed_mean


class RatingService:
    """Handles game rating aggregation and statistics."""

    # Cache timeout for rating averages (1 hour)
    RATING_CACHE_TIMEOUT = 3600

    @staticmethod
    def _compute_averages(ratings_qs):
        """Shared aggregation logic for a filtered ratings queryset.

        Args:
            ratings_qs: QuerySet of UserConceptRating

        Returns:
            dict or None
        """
        from trophies.models import UserConceptRating

        if not ratings_qs.exists():
            return None

        aggregates = ratings_qs.aggregate(
            avg_difficulty=Avg('difficulty'),
            avg_grindiness=Avg('grindiness'),
            avg_fun=Avg('fun_ranking'),
            avg_rating=Avg('overall_rating'),
            count=Count('id'),
        )

        # overall_rating is 0.5-5.0 on a 0.5 grid == 10 exact values; give each its own bucket (no rounding,
        # so 3.5 is 3.5, not "4"). The DISTRIBUTION shows the SHAPE a single average hides (polarizing vs.
        # consensus). Group-by returns <=10 rows (whale-safe); we snap any off-grid legacy value to the grid.
        # Bucket key is the integer half-step 1..10 (0.5->1 ... 5.0->10) so the template data-* and the JSON the
        # live-update reads match exactly (a float like 1.0 renders "1.0" in one place, "1" in the other).
        raw = ratings_qs.values_list('overall_rating').annotate(c=Count('id')).values_list('overall_rating', 'c')
        counts = {step: 0 for step in range(1, 11)}
        for value, c in raw:
            counts[min(10, max(1, round(value * 2)))] += c
        total = aggregates['count'] or 1
        peak = max(counts.values()) or 1   # scale bar heights to the tallest column so the shape reads clearly
        aggregates['distribution'] = [
            {'step': step, 'value': step / 2,
             'starnum': step // 2 if step % 2 == 0 else None,   # labeled only under whole stars (2,4,6,8,10)
             'count': counts[step], 'pct': round(counts[step] / total * 100),
             'bar': round(counts[step] / peak * 100)}
            for step in range(1, 11)   # 0.5 (left) -> 5.0 (right)
        ]

        # The RECOMMENDATION split -- the one aggregate that is a verdict rather than an average. Grouped
        # in the database (<=5 rows back), and it rides the caller's existing cache entry because this
        # whole dict is what gets cached: no new key, no new invalidation path.
        #
        # `recommend_pct` is `worth_it` alone. The middle option says the GAME is worth playing and the
        # platinum is not, so folding it in would report the opposite of what those raters meant -- this
        # figure is about the platinum, which is what the field rates.
        by_rec = dict(
            ratings_qs.exclude(recommendation='')
            .values_list('recommendation')
            .annotate(c=Count('id'))
        )
        answered = sum(by_rec.values())
        aggregates['recommendation_split'] = {
            'counts': {value: by_rec.get(value, 0) for value, _label in UserConceptRating.RECOMMENDATIONS},
            # The DENOMINATOR is answered ratings, not all of them: every rating written before the field
            # existed carries no answer, and counting those as "would not recommend" would misreport a
            # beloved game as divisive for as long as the backlog takes to clear.
            'answered': answered,
            'recommend_pct': round(by_rec.get('worth_it', 0) / answered * 100) if answered else None,
        }

        hours_list = list(ratings_qs.values_list('hours_to_platinum', flat=True))
        aggregates['avg_hours'] = (
            calculate_trimmed_mean(hours_list, trim_percent=0.1)
            if hours_list
            else None
        )

        return aggregates

    @staticmethod
    def get_community_averages(concept):
        """
        Calculate community rating averages for a game concept (base game only).

        Filters to base game ratings (concept_trophy_group=NULL) so DLC ratings
        do not skew the base game averages.

        Args:
            concept: Concept instance to calculate averages for

        Returns:
            dict or None: Dictionary with rating averages, or None if no ratings exist
                {
                    'avg_difficulty': float,
                    'avg_grindiness': float,
                    'avg_fun': float,
                    'avg_rating': float,
                    'avg_hours': float,
                    'count': int
                }
        """
        ratings = concept.user_ratings.filter(concept_trophy_group__isnull=True)
        return RatingService._compute_averages(ratings)

    @staticmethod
    def get_cached_community_averages(concept):
        """
        Get community rating averages with caching.

        Checks cache first, calculates and caches if not found.

        Args:
            concept: Concept instance

        Returns:
            dict or None: Rating averages dictionary, or None if no ratings

        Example:
            >>> averages = RatingService.get_cached_community_averages(concept)
        """
        cache_key = f"concept:averages:{concept.id}"
        averages = cache.get(cache_key)

        if averages is None:
            averages = RatingService.get_community_averages(concept)
            if averages:
                cache.set(cache_key, averages, RatingService.RATING_CACHE_TIMEOUT)

        return averages

    @staticmethod
    def invalidate_cache(concept):
        """
        Invalidate cached rating averages for a concept.

        Call this when a new rating is added or an existing rating is updated.

        Args:
            concept: Concept instance to invalidate cache for

        Example:
            >>> # After user submits a rating
            >>> RatingService.invalidate_cache(concept)
        """
        cache_key = f"concept:averages:{concept.id}"
        cache.delete(cache_key)

    @staticmethod
    def update_concept_ratings(concept):
        """
        Recalculate and cache concept ratings.

        Useful for batch updates or when you want to ensure cache is fresh.

        Args:
            concept: Concept instance to update

        Returns:
            dict or None: Updated rating averages

        Example:
            >>> RatingService.update_concept_ratings(concept)
        """
        averages = RatingService.get_community_averages(concept)
        if averages:
            cache_key = f"concept:averages:{concept.id}"
            cache.set(cache_key, averages, RatingService.RATING_CACHE_TIMEOUT)
        return averages

    @staticmethod
    def get_rating_statistics(concept):
        """
        Get detailed rating statistics including distribution.

        Args:
            concept: Concept instance

        Returns:
            dict: Detailed statistics including rating distribution

        Example:
            >>> stats = RatingService.get_rating_statistics(concept)
            >>> print(f"Median difficulty: {stats.get('median_difficulty')}")
        """
        ratings = concept.user_ratings.filter(concept_trophy_group__isnull=True)
        if not ratings.exists():
            return None

        # Get basic averages
        stats = RatingService.get_community_averages(concept)

        # Could add more detailed statistics here:
        # - Median values
        # - Standard deviations
        # - Rating distributions
        # - Recent trends

        return stats

    # ------------------------------------------------------------------ #
    #  DLC / Trophy Group rating methods
    # ------------------------------------------------------------------ #

    @staticmethod
    def get_community_averages_for_group(concept, concept_trophy_group):
        """Calculate community rating averages for a specific trophy group.

        For base game (trophy_group_id='default'): filters where
        concept_trophy_group IS NULL (backward compat with existing rows).
        For DLC: filters by the specific ConceptTrophyGroup FK.

        Args:
            concept: Concept instance
            concept_trophy_group: ConceptTrophyGroup instance

        Returns:
            dict or None: Rating averages dictionary
        """
        if concept_trophy_group.trophy_group_id == 'default':
            # Base game ratings have concept_trophy_group=NULL
            ratings = concept.user_ratings.filter(concept_trophy_group__isnull=True)
        else:
            ratings = concept.user_ratings.filter(
                concept_trophy_group=concept_trophy_group,
            )
        return RatingService._compute_averages(ratings)

    @staticmethod
    def get_cached_community_averages_for_group(concept, concept_trophy_group):
        """Get community averages for a trophy group with caching.

        Args:
            concept: Concept instance
            concept_trophy_group: ConceptTrophyGroup instance

        Returns:
            dict or None
        """
        cache_key = (
            f"concept:averages:{concept.id}:group:{concept_trophy_group.id}"
        )
        averages = cache.get(cache_key)
        if averages is None:
            averages = RatingService.get_community_averages_for_group(
                concept, concept_trophy_group,
            )
            if averages:
                cache.set(cache_key, averages, RatingService.RATING_CACHE_TIMEOUT)
        return averages

    @staticmethod
    def invalidate_group_cache(concept, concept_trophy_group):
        """Invalidate cached rating averages for a specific trophy group.

        Args:
            concept: Concept instance
            concept_trophy_group: ConceptTrophyGroup instance
        """
        cache_key = (
            f"concept:averages:{concept.id}:group:{concept_trophy_group.id}"
        )
        cache.delete(cache_key)


# --------------------------------------------------------------------------- #
#  One HUNTER's ratings -- the profile Ratings tab
#
#  The class above answers "what does the community think of this game". This half answers the mirror
#  question, "what does this person think of games", and it lives in the same file on purpose: both read
#  UserConceptRating, and a second module would be where the two definitions of "the community average"
#  drift apart.
# --------------------------------------------------------------------------- #

#: A page of the ratings wall. Smaller than the 50 the other tabs use because these cards are tall (cover +
#: score + three stats + an optional quick take), so 50 of them is a very long first paint.
RATINGS_PER_PAGE = 24

#: The orderings offered, and the only ones -- the template's options come from this list, so the control
#: cannot advertise a sort the view does not implement.
#:
#: SIX, not the eight the columns would allow. Grindiness and Fun were dropped, not forgotten: grind is
#: what hours-to-platinum measures in a unit people actually feel, and "most fun" and "highest rated" rank
#: almost the same shelf. A sort list is a list of QUESTIONS, and two of the eight were the same question
#: asked twice.
PROFILE_RATING_SORTS = [
    ('recent', 'Recently rated'),
    ('highest', 'Highest rated'),
    ('lowest', 'Lowest rated'),
    ('hardest', 'Hardest first'),
    ('longest', 'Longest first'),
    ('title', 'Title (A-Z)'),
]

#: Every ordering ends on `-updated_at`, which is unique enough per profile to be a stable tiebreak. Without
#: one, a wall paged by OFFSET can repeat or skip a card between pages whenever scores tie -- and scores tie
#: constantly here (a 1-10 integer over a few hundred rows).
_RATING_SORT_ORDERS = {
    'recent': ('-updated_at',),
    'highest': ('-overall_rating', '-updated_at'),
    'lowest': ('overall_rating', '-updated_at'),
    'hardest': ('-difficulty', '-updated_at'),
    'longest': ('-hours_to_platinum', '-updated_at'),
    'title': (Lower('concept__unified_title'), '-updated_at'),
}


def profile_rating_summary(profile):
    """This hunter's taste, in one row.

    ONE aggregate over their whole rating set, never a Python pass over the rows: the wall is paged, but
    the summary describes everything they have rated, and "iterate the queryset to build totals" is the
    exact shape that OOMs a big account.

    Returns a dict shaped for `rating_summary` / `rating_verdict` (the same keys the game-detail conditions
    card uses), so the synthesized sentence can be reused verbatim rather than re-worded here. `count` is 0
    for a hunter who has rated nothing; the caller renders nothing at all in that case.
    """
    from trophies.models import UserConceptRating

    row = UserConceptRating.objects.filter(profile=profile).aggregate(
        count=Count('id'),
        avg_rating=Avg('overall_rating'),
        avg_difficulty=Avg('difficulty'),
        avg_grindiness=Avg('grindiness'),
        avg_fun=Avg('fun_ranking'),
        hours=Sum('hours_to_platinum'),
        # The EXTREME, not another average. The synthesized sentence already carries all three averages, so
        # a cell repeating one of them would print the same fact twice in two formats -- where "the hardest
        # thing they have scored" is precisely what an average hides.
        toughest=Max('difficulty'),
        # Quick takes are counted through the SAME predicate `visible_blurbs()` reads by, so the header's
        # figure can never promise takes the cards then withhold as staff-hidden.
        takes=Count('id', filter=~Q(blurb='') & Q(blurb_hidden=False)),
        # How often this hunter sends people after a platinum -- their own recommend rate, on the same
        # value the community split counts. Same one aggregate, so it costs nothing.
        answered=Count('id', filter=~Q(recommendation='')),
        recommends=Count('id', filter=Q(recommendation='worth_it')),
    )
    # Denominated in ANSWERED ratings: everything they rated before the field existed carries no answer,
    # and counting those against them would read as a hunter who recommends almost nothing.
    row['recommend_pct'] = (
        round(row['recommends'] / row['answered'] * 100) if row['answered'] else None
    )
    return row


def _community_scores(rows):
    """The community's score for each (concept, group) on THIS PAGE. One grouped query, whatever the page.

    Not `annotate_community_ratings`: that helper correlates on the concept alone and hard-filters to
    `concept_trophy_group__isnull=True`, so a DLC rating would be scored against the base game's average --
    a comparison that renders convincingly and means something else. Correlating on the group instead is
    not a fix either, because a base-game rating carries a NULL group and `NULL = NULL` never matches in
    SQL, so every base row would silently come back unmatched.

    Grouping in the database and pairing up in Python sidesteps both. The GROUP BY returns one row per
    (concept, group) touched, so it is bounded by the page, not by how heavily rated those games are.
    """
    from trophies.models import UserConceptRating

    if not rows:
        return {}

    wanted = {(r.concept_id, r.concept_trophy_group_id) for r in rows}
    grouped = (
        UserConceptRating.objects
        .filter(concept_id__in={c for c, _ in wanted})
        .values('concept_id', 'concept_trophy_group_id')
        .annotate(avg=Avg('overall_rating'), n=Count('id'))
    )
    return {
        (g['concept_id'], g['concept_trophy_group_id']): g
        for g in grouped
        if (g['concept_id'], g['concept_trophy_group_id']) in wanted
    }


def _games_for_concepts(profile, concept_ids):
    """The Game each rated concept is, for this hunter: one bulk query for the whole page.

    A rating hangs off a CONCEPT, but a cover and a link need a GAME -- `display_image_url` is the site's
    one cover chain and `game_detail_with_profile` is keyed on `np_communication_id`. A concept can span
    several platform SKUs, so the pick is ordered: the one they platinumed, then the one they got furthest
    in, then the id as a stable tiebreak. Anything less deterministic would let a card change which version
    it links to between two loads of the same page.

    `raw_response` is deferred alongside the `igdb_match` join, per the standing rule -- it is ~30 KB of
    unread IGDB JSON per row and the cover template never touches it.
    """
    from trophies.models import ProfileGame

    if not concept_ids:
        return {}

    owned = (
        ProfileGame.objects
        .filter(profile=profile, game__concept_id__in=concept_ids)
        .select_related('game', 'game__concept', 'game__concept__igdb_match')
        .defer('game__concept__igdb_match__raw_response')
        .order_by('game__concept_id', '-has_plat', '-progress', 'game__np_communication_id')
    )
    out = {}
    for pg in owned:
        out.setdefault(pg.game.concept_id, pg.game)
    return out


def build_profile_ratings_page(profile, sort='recent', page=1, per_page=RATINGS_PER_PAGE):
    """One page of a hunter's ratings, with everything each card draws already attached.

    Three queries flat, whatever the page: the ratings themselves, the community scores for the concepts on
    it, and the games behind those concepts. Nothing here scales with the size of the account -- only with
    `per_page`.

    Sliced rather than paginated. `Paginator` runs a `COUNT(*)` over the whole set on every page, and the
    header already knows the total from `profile_rating_summary`, so paying for it again per page buys
    nothing.
    """
    from trophies.models import UserConceptRating

    if sort not in _RATING_SORT_ORDERS:
        sort = PROFILE_RATING_SORTS[0][0]
    page = max(int(page), 1)
    offset = (page - 1) * per_page

    rows = list(
        UserConceptRating.objects
        .filter(profile=profile)
        .select_related('concept', 'concept__igdb_match', 'concept_trophy_group')
        .defer('concept__igdb_match__raw_response')
        .order_by(*_RATING_SORT_ORDERS[sort])
        [offset:offset + per_page]
    )

    community = _community_scores(rows)
    games = _games_for_concepts(profile, {r.concept_id for r in rows})

    for r in rows:
        r.card_game = games.get(r.concept_id)
        # Shown only when someone OTHER than this hunter has rated it too: "you 4.5, community 4.5" against
        # a sample of one is not a comparison, it is the same number printed twice.
        stats = community.get((r.concept_id, r.concept_trophy_group_id))
        r.community_avg = stats['avg'] if stats and stats['n'] > 1 else None
        r.community_n = stats['n'] if stats else 1

    return rows
