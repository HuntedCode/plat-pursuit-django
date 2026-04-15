"""
Review Hub service layer.

Aggregate queries for the Review Hub landing page: stats, most-reviewed games,
trending reviews, and unrated/unreviewed platinum counts for the wizard CTA.
"""
import logging

from django.core.cache import cache
from django.db.models import Count, Sum, Q
from django.utils import timezone
from datetime import timedelta

logger = logging.getLogger('psn_api')


class ReviewHubService:
    """Aggregate queries for the Review Hub landing and wizard pages."""

    STATS_CACHE_KEY = 'review_hub:stats'
    STATS_CACHE_TIMEOUT = 900  # 15 minutes

    @staticmethod
    def get_hub_stats():
        """Total reviews, total ratings, unique reviewers, total helpful votes.

        Returns a dict with keys: total_reviews, total_ratings,
        unique_reviewers, total_helpful_votes. All default to 0.
        Cached 15 minutes.
        """
        cached = cache.get(ReviewHubService.STATS_CACHE_KEY)
        if cached:
            return cached

        from trophies.models import Review, UserConceptRating

        review_agg = Review.objects.filter(is_deleted=False).aggregate(
            total_reviews=Count('id'),
            unique_reviewers=Count('profile', distinct=True),
            total_helpful_votes=Sum('helpful_count'),
        )

        total_ratings = UserConceptRating.objects.count()

        stats = {
            'total_reviews': review_agg['total_reviews'] or 0,
            'total_ratings': total_ratings,
            'unique_reviewers': review_agg['unique_reviewers'] or 0,
            'total_helpful_votes': review_agg['total_helpful_votes'] or 0,
        }

        cache.set(ReviewHubService.STATS_CACHE_KEY, stats,
                  ReviewHubService.STATS_CACHE_TIMEOUT)
        return stats

    @staticmethod
    def get_most_reviewed_games(limit=10):
        """Top concepts by non-deleted review count (single query).

        Returns list of dicts: concept_icon_url, unified_title, slug,
        review_count, recommendation_pct.
        """
        from trophies.models import Concept

        concepts = list(
            Concept.objects
            .annotate(
                review_count=Count(
                    'reviews',
                    filter=Q(reviews__is_deleted=False),
                ),
                recommended_count=Count(
                    'reviews',
                    filter=Q(
                        reviews__is_deleted=False,
                        reviews__recommended=True,
                    ),
                ),
            )
            .filter(review_count__gt=0)
            .order_by('-review_count')[:limit]
            .values(
                'unified_title', 'slug', 'concept_icon_url',
                'review_count', 'recommended_count',
            )
        )

        result = []
        for c in concepts:
            total = c['review_count']
            pct = round(c['recommended_count'] / total * 100) if total else 0
            result.append({
                'concept_icon_url': c['concept_icon_url'] or '',
                'unified_title': c['unified_title'],
                'slug': c['slug'],
                'review_count': total,
                'recommendation_pct': pct,
            })

        return result

    @staticmethod
    def get_top_reviewers(limit=10):
        """Top reviewers by total helpful votes received across all their reviews.

        Aggregates `helpful_count` (denormalized on Review) per profile and
        returns the top N. Excludes deleted reviews and profiles whose total
        is zero (newcomers don't surface even if they have a review with no
        votes yet). Used by the Community Hub `top_reviewers` module.

        Returns a list of dicts: profile (Profile instance), total_helpful,
        review_count. Profile is included so the template can render avatar +
        psn_username + flag without an extra query.
        """
        from django.db.models import Count, Sum
        from trophies.models import Profile

        return list(
            Profile.objects
            .annotate(
                total_helpful=Sum(
                    'reviews__helpful_count',
                    filter=Q(reviews__is_deleted=False),
                ),
                review_count=Count(
                    'reviews',
                    filter=Q(reviews__is_deleted=False),
                ),
            )
            .filter(total_helpful__gt=0)
            .order_by('-total_helpful', '-review_count')[:limit]
            .values(
                'id', 'psn_username', 'display_psn_username',
                'avatar_url', 'flag', 'user_is_premium',
                'total_helpful', 'review_count',
            )
        )

    @staticmethod
    def get_trending_reviews(days=7, limit=5):
        """Reviews with most helpful votes created in the last N days.

        Returns Review queryset with select_related for template rendering.
        """
        from trophies.models import Review

        cutoff = timezone.now() - timedelta(days=days)

        return (
            Review.objects
            .filter(
                is_deleted=False,
                created_at__gte=cutoff,
                helpful_count__gt=0,
            )
            .select_related('profile', 'concept')
            .prefetch_related('profile__user_titles__title')
            .order_by('-helpful_count', '-created_at')[:limit]
        )

    @staticmethod
    def search_concepts(query, limit=8):
        """Search concepts by title for the Review Hub search bar.

        Returns list of dicts with review-relevant metadata.
        Excludes PP_ stubs, shovelware-only concepts, and concepts without slugs.
        Results ordered by review count (descending), then title (alphabetical).
        """
        query = (query or '').strip()[:200]
        if len(query) < 2:
            return []

        from trophies.models import Concept

        concepts = list(
            Concept.objects
            .filter(unified_title__icontains=query)
            .exclude(slug__isnull=True)
            .exclude(slug='')
            .exclude(concept_id__startswith='PP_')
            .annotate(
                non_sw_game_count=Count(
                    'games',
                    filter=~Q(
                        games__shovelware_status__in=[
                            'auto_flagged', 'manually_flagged',
                        ],
                    ),
                ),
                review_count=Count(
                    'reviews',
                    filter=Q(reviews__is_deleted=False),
                ),
                recommended_count=Count(
                    'reviews',
                    filter=Q(
                        reviews__is_deleted=False,
                        reviews__recommended=True,
                    ),
                ),
            )
            .filter(non_sw_game_count__gt=0)
            .order_by('-review_count', 'unified_title')[:limit]
            .values(
                'unified_title', 'slug', 'concept_icon_url',
                'review_count', 'recommended_count',
            )
        )

        result = []
        for c in concepts:
            total = c['review_count']
            pct = round(c['recommended_count'] / total * 100) if total else 0
            result.append({
                'unified_title': c['unified_title'],
                'slug': c['slug'],
                'concept_icon_url': c['concept_icon_url'] or '',
                'review_count': total,
                'recommendation_pct': pct,
            })

        return result

    @staticmethod
    def get_ratable_concept_ids(profile):
        """Concept IDs where user is eligible to rate the base game.

        Includes:
        - Concepts where user has earned a platinum (non-shovelware)
        - Concepts where user has 100% progress but no platinum trophy exists

        Returns a list of distinct concept IDs (evaluated, not a queryset).
        """
        from trophies.models import EarnedTrophy, ProfileGame, Trophy

        # 1. Platinumed concepts (non-shovelware)
        plat_concept_ids = set(
            EarnedTrophy.objects.filter(
                profile=profile,
                earned=True,
                trophy__trophy_type='platinum',
            ).exclude(
                trophy__game__shovelware_status__in=['auto_flagged', 'manually_flagged'],
            ).values_list('trophy__game__concept_id', flat=True).distinct()
        )

        # 2. Non-plat 100% concepts: games at 100% progress where the concept
        #    has NO platinum trophy at all (non-shovelware)
        full_completion_concept_ids = set(
            ProfileGame.objects.filter(
                profile=profile,
                progress=100,
                has_plat=False,
            ).exclude(
                game__shovelware_status__in=['auto_flagged', 'manually_flagged'],
            ).values_list('game__concept_id', flat=True).distinct()
        )
        if full_completion_concept_ids:
            # Exclude any concept that actually has a platinum trophy
            # (has_plat=False on ProfileGame means the user hasn't earned it,
            #  but the trophy might still exist)
            concepts_with_plat = set(
                Trophy.objects.filter(
                    game__concept_id__in=full_completion_concept_ids,
                    trophy_type='platinum',
                ).values_list('game__concept_id', flat=True).distinct()
            )
            full_completion_concept_ids -= concepts_with_plat

        return list(plat_concept_ids | full_completion_concept_ids)

    @staticmethod
    def get_unrated_platinum_count(profile):
        """Count of ratable concepts (non-shovelware) not yet base-game rated."""
        from trophies.models import UserConceptRating

        ratable_ids = ReviewHubService.get_ratable_concept_ids(profile)
        if not ratable_ids:
            return 0

        # Rated concept IDs (base game = null concept_trophy_group)
        rated_concept_ids = set(
            UserConceptRating.objects
            .filter(
                profile=profile,
                concept_id__in=ratable_ids,
                concept_trophy_group__isnull=True,
            )
            .values_list('concept_id', flat=True)
        )

        return len(set(ratable_ids) - rated_concept_ids)

    @staticmethod
    def get_unrated_dlc_count(profile):
        """Count of DLC groups where user has 100% completion but no rating.

        Considers DLC groups belonging to any ratable concept (platinumed or
        100%-completed non-plat).
        """
        from trophies.models import (
            ConceptTrophyGroup, EarnedTrophy, Trophy, UserConceptRating,
        )

        ratable_concept_ids = ReviewHubService.get_ratable_concept_ids(profile)
        if not ratable_concept_ids:
            return 0

        # DLC groups for those concepts (exclude base game)
        dlc_groups = ConceptTrophyGroup.objects.filter(
            concept_id__in=ratable_concept_ids,
        ).exclude(trophy_group_id='default')

        # Already-rated DLC group IDs for this profile
        rated_group_ids = set(
            UserConceptRating.objects.filter(
                profile=profile,
                concept_trophy_group__isnull=False,
            ).values_list('concept_trophy_group_id', flat=True)
        )

        unrated_count = 0
        for ctg in dlc_groups.exclude(id__in=rated_group_ids):
            totals = dict(
                Trophy.objects.filter(
                    game__concept_id=ctg.concept_id,
                    trophy_group_id=ctg.trophy_group_id,
                ).values('game_id').annotate(
                    total=Count('id')
                ).values_list('game_id', 'total')
            )
            if not totals:
                continue
            earned = dict(
                EarnedTrophy.objects.filter(
                    profile=profile,
                    trophy__game_id__in=totals.keys(),
                    trophy__trophy_group_id=ctg.trophy_group_id,
                    earned=True,
                ).values('trophy__game_id').annotate(
                    cnt=Count('id')
                ).values_list('trophy__game_id', 'cnt')
            )
            for game_id, total in totals.items():
                if total > 0 and earned.get(game_id, 0) >= total:
                    unrated_count += 1
                    break

        return unrated_count

    @staticmethod
    def get_unreviewed_platinum_count(profile):
        """Count of ratable concepts (non-shovelware) not yet base-game reviewed."""
        from trophies.models import Review

        ratable_ids = ReviewHubService.get_ratable_concept_ids(profile)
        if not ratable_ids:
            return 0

        reviewed_concept_ids = set(
            Review.objects
            .filter(
                profile=profile,
                is_deleted=False,
                concept_trophy_group__trophy_group_id='default',
            )
            .values_list('concept_id', flat=True)
        )

        return len(set(ratable_ids) - reviewed_concept_ids)
