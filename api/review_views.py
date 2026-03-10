"""
Review API views.

Handles all REST endpoints for reviews: CRUD, voting, replies, reporting,
and DLC group ratings. All business logic lives in ReviewService,
ConceptTrophyGroupService, and RatingService.
"""
import logging

from django.utils.decorators import method_decorator
from django_ratelimit.decorators import ratelimit
from rest_framework import status
from rest_framework.authentication import SessionAuthentication, TokenAuthentication
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from trophies.models import (
    Concept, ConceptTrophyGroup, Review, ReviewReply, ReviewVote,
)
from trophies.services.review_service import ReviewService
from trophies.services.review_hub_service import ReviewHubService
from trophies.services.checklist_service import ChecklistService
from api.utils import safe_int

logger = logging.getLogger('psn_api')


# ------------------------------------------------------------------ #
#  Module-level helpers
# ------------------------------------------------------------------ #

def _get_profile_or_error(request):
    """Return (profile, None) or (None, Response)."""
    profile = getattr(request.user, 'profile', None)
    if not profile:
        return None, Response(
            {'error': 'Linked profile required.'},
            status=status.HTTP_403_FORBIDDEN,
        )
    return profile, None


def _get_concept_and_group(concept_id, group_id_str):
    """Resolve Concept + ConceptTrophyGroup from URL params.

    Includes shovelware gate: if ALL games in concept are shovelware, returns 403.

    Returns:
        (concept, ctg, None) on success
        (None, None, Response) on error
    """
    try:
        concept = Concept.objects.get(id=concept_id)
    except Concept.DoesNotExist:
        return None, None, Response(
            {'error': 'Concept not found.'},
            status=status.HTTP_404_NOT_FOUND,
        )

    # Shovelware gate: reject if every game in the concept is flagged.
    # Concepts with zero games are allowed through (not-yet-synced state).
    games = concept.games.all()
    if games.exists() and not games.exclude(
        shovelware_status__in=['auto_flagged', 'manually_flagged'],
    ).exists():
        return None, None, Response(
            {'error': 'Review Hub is not available for this game.'},
            status=status.HTTP_403_FORBIDDEN,
        )

    try:
        ctg = ConceptTrophyGroup.objects.get(
            concept=concept, trophy_group_id=group_id_str,
        )
    except ConceptTrophyGroup.DoesNotExist:
        return None, None, Response(
            {'error': 'Trophy group not found.'},
            status=status.HTTP_404_NOT_FOUND,
        )

    return concept, ctg, None


def _get_review_or_error(review_id):
    """Return (review, None) or (None, Response). Only returns non-deleted reviews."""
    try:
        review = Review.objects.select_related(
            'profile', 'concept', 'concept_trophy_group',
        ).get(id=review_id, is_deleted=False)
    except Review.DoesNotExist:
        return None, Response(
            {'error': 'Review not found.'},
            status=status.HTTP_404_NOT_FOUND,
        )
    return review, None


# ------------------------------------------------------------------ #
#  Serialization helpers
# ------------------------------------------------------------------ #

def _serialize_author(profile):
    """Serialize a Profile to an author dict."""
    # Use prefetched user_titles if available, otherwise fall back to property
    displayed_title = None
    title_source = None
    if hasattr(profile, '_prefetched_objects_cache') and 'user_titles' in profile._prefetched_objects_cache:
        for ut in profile.user_titles.all():
            if ut.is_displayed:
                displayed_title = ut.title.name
                title_source = _resolve_title_source(ut)
                break
    else:
        ut = profile.user_titles.filter(is_displayed=True).select_related('title').first()
        if ut:
            displayed_title = ut.title.name
            title_source = _resolve_title_source(ut)

    return {
        'profile_id': profile.id,
        'psn_username': profile.psn_username,
        'display_psn_username': profile.display_psn_username,
        'avatar_url': profile.avatar_url,
        'is_premium': profile.user_is_premium,
        'displayed_title': displayed_title,
        'title_source': title_source,
    }


def _resolve_title_source(user_title):
    """Build a human-readable source string for a UserTitle."""
    from trophies.models import Badge, Milestone
    if user_title.source_type == 'badge' and user_title.source_id:
        badge = Badge.objects.filter(id=user_title.source_id).values_list('name', flat=True).first()
        return f"Earned from badge: {badge}" if badge else "Earned from a badge"
    elif user_title.source_type == 'milestone' and user_title.source_id:
        milestone = Milestone.objects.filter(
            id=user_title.source_id
        ).values('name', 'criteria_type', 'required_value').first()
        if not milestone:
            return "Earned from a milestone"
        line = f"Earned from milestone: {milestone['name']}"
        desc = _milestone_description(milestone['criteria_type'], milestone['required_value'])
        if desc:
            line += f" ({desc})"
        return line
    return None


# Map criteria_type to a human-readable template.
# {n} is replaced with required_value. Omitted types get no extra description.
_MILESTONE_DESCRIPTIONS = {
    'plat_count': '{n} platinums earned',
    'trophy_count': '{n} trophies earned',
    'rating_count': '{n} games rated',
    'playtime_hours': '{n} hours played',
    'checklist_upvotes': '{n} checklist upvotes received',
    'badge_count': '{n} badge tiers earned',
    'unique_badge_count': '{n} unique badges earned',
    'completion_count': '{n} games 100% completed',
    'stage_count': '{n} badge stages completed',
    'az_progress': '{n} A-Z challenge letters',
    'genre_progress': '{n} genre challenge genres',
    'subgenre_progress': '{n} subgenre collections',
    'calendar_months_total': '{n} calendar months completed',
    'subscription_months': '{n} months subscribed',
    'review_count': '{n} quality reviews written (150+ words)',
    'review_helpful_count': '{n} helpful votes received on reviews',
}


def _milestone_description(criteria_type, required_value):
    """Return a short description like '300 platinums earned', or None."""
    template = _MILESTONE_DESCRIPTIONS.get(criteria_type)
    if template and required_value:
        return template.format(n=required_value)
    # For boolean-style milestones (psn_linked, is_premium, etc.), use the display label
    from trophies.models import Milestone
    label = dict(Milestone.CRITERIA_TYPES).get(criteria_type)
    return label or None


def _serialize_review(review, reviewer_stats=None, user_voted_helpful=False,
                       user_voted_funny=False, is_own=False):
    """Serialize a Review instance to a dict."""
    stats = reviewer_stats or {}
    hours = stats.get('hours_played')
    hours_val = None
    if hours is not None:
        if hasattr(hours, 'total_seconds'):
            hours_val = round(hours.total_seconds() / 3600, 1)
        else:
            logger.warning(f"Unexpected hours_played type: {type(hours)}")

    return {
        'id': review.id,
        'body': review.body,
        'body_html': ChecklistService.process_markdown(review.body) if review.body else '',
        'recommended': review.recommended,
        'helpful_count': review.helpful_count,
        'funny_count': review.funny_count,
        'reply_count': review.reply_count,
        'is_edited': review.is_edited,
        'created_at': review.created_at.isoformat(),
        'updated_at': review.updated_at.isoformat(),
        'author': _serialize_author(review.profile),
        'reviewer_stats': {
            'completion_pct': stats.get('completion_pct', 0),
            'has_plat': stats.get('has_plat', False),
            'hours_played': hours_val,
        },
        'user_voted_helpful': user_voted_helpful,
        'user_voted_funny': user_voted_funny,
        'is_own': is_own,
    }


def _serialize_reply(reply, is_own=False):
    """Serialize a ReviewReply instance to a dict."""
    return {
        'id': reply.id,
        'body': reply.body,
        'is_edited': reply.is_edited,
        'created_at': reply.created_at.isoformat(),
        'author': _serialize_author(reply.profile),
        'is_own': is_own,
    }


# ------------------------------------------------------------------ #
#  Recent Reviews (Landing Page Feed)
# ------------------------------------------------------------------ #

class RecentReviewsView(APIView):
    """Recent reviews feed across all games for the Review Hub landing page."""
    authentication_classes = [SessionAuthentication, TokenAuthentication]
    permission_classes = []

    def get(self, request):
        """
        GET /api/v1/reviews/recent/?sort=newest|helpful&limit=10&offset=0
        Returns paginated reviews with concept metadata for the landing feed.
        """
        try:
            sort = request.query_params.get('sort', 'newest')
            if sort not in ('newest', 'helpful'):
                sort = 'newest'
            limit = min(safe_int(request.query_params.get('limit', 10), 10), 50)
            offset = max(safe_int(request.query_params.get('offset', 0), 0), 0)

            sort_map = {
                'newest': ('-created_at',),
                'helpful': ('-helpful_count', '-created_at'),
            }

            qs = (
                Review.objects
                .filter(is_deleted=False)
                .select_related('profile', 'concept')
                .prefetch_related('profile__user_titles__title')
                .order_by(*sort_map[sort])
            )

            total_count = qs.count()
            paginated = list(qs[offset:offset + limit])
            has_more = (offset + limit) < total_count

            # Batch-fetch user votes
            profile = None
            if request.user.is_authenticated:
                profile = getattr(request.user, 'profile', None)

            user_votes = {}
            if profile and paginated:
                review_ids = [r.id for r in paginated]
                votes = ReviewVote.objects.filter(
                    review_id__in=review_ids, profile=profile,
                ).values_list('review_id', 'vote_type')
                for rid, vtype in votes:
                    user_votes.setdefault(rid, set()).add(vtype)

            reviews_data = []
            for review in paginated:
                rv = user_votes.get(review.id, set())
                data = _serialize_review(
                    review,
                    user_voted_helpful='helpful' in rv,
                    user_voted_funny='funny' in rv,
                    is_own=(profile and review.profile_id == profile.id),
                )
                # Add concept metadata for landing page cards
                data['concept'] = {
                    'unified_title': review.concept.unified_title,
                    'slug': review.concept.slug,
                    'concept_icon_url': review.concept.concept_icon_url or '',
                }
                reviews_data.append(data)

            return Response({
                'reviews': reviews_data,
                'count': total_count,
                'has_more': has_more,
                'next_offset': offset + limit,
                'sort': sort,
            })
        except Exception as e:
            logger.exception(f"Error loading recent reviews: {e}")
            return Response(
                {'error': 'Failed to load reviews.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


# ------------------------------------------------------------------ #
#  Concept Search (for review hub search bar)
# ------------------------------------------------------------------ #

class ConceptReviewSearchView(APIView):
    """Search concepts by title for the Review Hub search bar."""
    authentication_classes = [SessionAuthentication, TokenAuthentication]
    permission_classes = []

    @method_decorator(ratelimit(key='ip', rate='60/m', method='GET', block=True))
    def get(self, request):
        """
        GET /api/v1/reviews/search/?q=<query>&limit=8
        Returns matching concepts with review-relevant metadata.
        """
        try:
            query = (request.query_params.get('q') or '').strip()
            if len(query) < 2:
                return Response({'results': [], 'count': 0})

            limit = min(safe_int(request.query_params.get('limit', 8), 8), 20)
            results = ReviewHubService.search_concepts(query, limit=limit)

            return Response({
                'results': results,
                'count': len(results),
            })
        except Exception as e:
            logger.exception(f"Error searching concepts: {e}")
            return Response(
                {'error': 'Failed to search games.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


# ------------------------------------------------------------------ #
#  Trophy List (condensed, for review hub sidebar & wizard)
# ------------------------------------------------------------------ #

class TrophyListView(APIView):
    """Condensed trophy list for a concept trophy group."""
    authentication_classes = [SessionAuthentication, TokenAuthentication]
    permission_classes = []

    def get(self, request, concept_id, group_id):
        """
        GET /api/v1/reviews/<concept_id>/group/<group_id>/trophies/

        Returns a deduplicated trophy list for the specified group with
        the authenticated user's earned status (if logged in).
        """
        concept, ctg, err = _get_concept_and_group(concept_id, group_id)
        if err:
            return err

        from trophies.models import Trophy, EarnedTrophy

        trophies_qs = Trophy.objects.filter(
            game__concept=concept,
            trophy_group_id=group_id,
        ).order_by('trophy_id').values(
            'trophy_id', 'trophy_type', 'trophy_name',
            'trophy_detail', 'trophy_icon_url',
        )

        # Deduplicate by trophy_id (same trophy across multi-region stacks)
        seen = set()
        trophies = []
        for t in trophies_qs:
            if t['trophy_id'] not in seen:
                seen.add(t['trophy_id'])
                trophies.append(t)

        # Earned status for authenticated user
        earned_set = set()
        if request.user.is_authenticated:
            profile = getattr(request.user, 'profile', None)
            if profile:
                earned_set = set(
                    EarnedTrophy.objects.filter(
                        profile=profile,
                        earned=True,
                        trophy__game__concept=concept,
                        trophy__trophy_group_id=group_id,
                    ).values_list('trophy__trophy_id', flat=True).distinct()
                )

        result = []
        for t in trophies:
            result.append({
                'trophy_id': t['trophy_id'],
                'trophy_type': t['trophy_type'],
                'trophy_name': t['trophy_name'],
                'trophy_detail': t['trophy_detail'],
                'trophy_icon_url': t['trophy_icon_url'] or '',
                'earned': t['trophy_id'] in earned_set,
            })

        return Response({
            'trophies': result,
            'count': len(result),
            'group_name': ctg.display_name,
        })


# ------------------------------------------------------------------ #
#  Wizard Queue (Rate My Games)
# ------------------------------------------------------------------ #

class WizardQueueView(APIView):
    """Queue of platinumed games waiting to be rated/reviewed for the wizard."""
    authentication_classes = [SessionAuthentication, TokenAuthentication]
    permission_classes = [IsAuthenticated]

    def get(self, request):
        """
        GET /api/v1/reviews/wizard/queue/?filter=unrated|unreviewed|both
            &queue_type=base|dlc&limit=20&offset=0

        queue_type=base: Returns base game concepts missing ratings/reviews.
        queue_type=dlc: Returns DLC groups grouped by parent concept.
        """
        try:
            profile, err = _get_profile_or_error(request)
            if err:
                return err

            from trophies.models import (
                EarnedTrophy, UserConceptRating,
            )
            from django.db.models.functions import Lower

            filter_mode = request.query_params.get('filter', 'unrated')
            if filter_mode not in ('unrated', 'unreviewed', 'both'):
                filter_mode = 'unrated'
            queue_type = request.query_params.get('queue_type', 'base')
            if queue_type not in ('base', 'dlc'):
                queue_type = 'base'
            limit = min(safe_int(request.query_params.get('limit', 20), 20), 50)
            offset = max(safe_int(request.query_params.get('offset', 0), 0), 0)

            # Get all concept IDs where user has a platinum (non-shovelware)
            plat_concept_ids = list(
                EarnedTrophy.objects.filter(
                    profile=profile,
                    earned=True,
                    trophy__trophy_type='platinum',
                ).exclude(
                    trophy__game__shovelware_status__in=['auto_flagged', 'manually_flagged'],
                ).values_list('trophy__game__concept_id', flat=True).distinct()
            )

            if not plat_concept_ids:
                if queue_type == 'dlc':
                    return Response({'groups': [], 'total_items': 0, 'has_more': False})
                return Response({'queue': [], 'count': 0, 'has_more': False})

            if queue_type == 'dlc':
                return self._get_dlc_queue(
                    profile, plat_concept_ids, filter_mode, limit, offset,
                )

            # ── Base game queue ──────────────────────────────────────── #
            rated_concept_ids = set(
                UserConceptRating.objects.filter(
                    profile=profile,
                    concept_id__in=plat_concept_ids,
                    concept_trophy_group__isnull=True,
                ).values_list('concept_id', flat=True)
            )

            reviewed_concept_ids = set(
                Review.objects.filter(
                    profile=profile,
                    concept_id__in=plat_concept_ids,
                    is_deleted=False,
                    concept_trophy_group__trophy_group_id='default',
                ).values_list('concept_id', flat=True)
            )

            # Filter based on mode
            if filter_mode == 'unrated':
                wanted_ids = [cid for cid in plat_concept_ids if cid not in rated_concept_ids]
            elif filter_mode == 'unreviewed':
                wanted_ids = [cid for cid in plat_concept_ids if cid not in reviewed_concept_ids]
            else:  # both: missing EITHER rating OR review
                wanted_ids = [
                    cid for cid in plat_concept_ids
                    if cid not in rated_concept_ids or cid not in reviewed_concept_ids
                ]

            # Fetch concepts ordered alphabetically
            concepts = list(
                Concept.objects.filter(id__in=wanted_ids)
                .order_by(Lower('unified_title'))
                .values(
                    'id', 'unified_title', 'concept_icon_url', 'slug',
                )
            )

            total_count = len(concepts)
            paginated = concepts[offset:offset + limit]
            has_more = (offset + limit) < total_count

            # Pre-fetch existing ratings for games that have been rated
            paginated_ids = [c['id'] for c in paginated]
            existing_ratings = {}
            rated_in_page = [cid for cid in paginated_ids if cid in rated_concept_ids]
            if rated_in_page:
                for r in UserConceptRating.objects.filter(
                    profile=profile,
                    concept_id__in=rated_in_page,
                    concept_trophy_group__isnull=True,
                ).values('concept_id', 'difficulty', 'grindiness', 'hours_to_platinum', 'fun_ranking', 'overall_rating'):
                    existing_ratings[r['concept_id']] = {
                        'difficulty': r['difficulty'],
                        'grindiness': r['grindiness'],
                        'hours_to_platinum': r['hours_to_platinum'],
                        'fun_ranking': r['fun_ranking'],
                        'overall_rating': float(r['overall_rating']),
                    }

            # Pre-fetch existing reviews for games that have been reviewed
            existing_reviews = {}
            reviewed_in_page = [cid for cid in paginated_ids if cid in reviewed_concept_ids]
            if reviewed_in_page:
                for r in Review.objects.filter(
                    profile=profile,
                    concept_id__in=reviewed_in_page,
                    is_deleted=False,
                    concept_trophy_group__trophy_group_id='default',
                ).values('concept_id', 'id', 'body', 'recommended'):
                    existing_reviews[r['concept_id']] = {
                        'id': r['id'],
                        'body': r['body'],
                        'recommended': r['recommended'],
                    }

            # Pre-fetch user's gameplay stats for these concepts
            from trophies.models import ProfileGame
            from django.db.models import Sum, Max
            game_stats = {}
            for row in ProfileGame.objects.filter(
                profile=profile,
                game__concept_id__in=paginated_ids,
            ).values('game__concept_id').annotate(
                max_progress=Max('progress'),
                total_earned=Sum('earned_trophies_count'),
                total_unearned=Sum('unearned_trophies_count'),
                total_play=Sum('play_duration'),
            ):
                cid = row['game__concept_id']
                hours = None
                if row['total_play']:
                    hours = int(row['total_play'].total_seconds()) // 3600
                earned = row['total_earned'] or 0
                unearned = row['total_unearned'] or 0
                game_stats[cid] = {
                    'progress': row['max_progress'] or 0,
                    'earned_trophies': earned,
                    'total_trophies': earned + unearned,
                    'play_hours': hours,
                }

            # Pre-fetch platinum dates
            plat_dates = {}
            for et in EarnedTrophy.objects.filter(
                profile=profile,
                earned=True,
                trophy__trophy_type='platinum',
                trophy__game__concept_id__in=paginated_ids,
            ).values('trophy__game__concept_id', 'earned_date_time'):
                cid = et['trophy__game__concept_id']
                dt = et['earned_date_time']
                if dt and (cid not in plat_dates or dt > plat_dates[cid]):
                    plat_dates[cid] = dt

            queue = []
            for c in paginated:
                cid = c['id']
                item = {
                    'concept_id': cid,
                    'unified_title': c['unified_title'],
                    'concept_icon_url': c['concept_icon_url'] or '',
                    'slug': c['slug'],
                    'has_rating': cid in rated_concept_ids,
                    'has_review': cid in reviewed_concept_ids,
                    'trophy_group_id': 'default',
                    'trophy_group_name': 'Base Game',
                }
                if cid in existing_ratings:
                    item['existing_rating'] = existing_ratings[cid]
                if cid in existing_reviews:
                    item['existing_review'] = existing_reviews[cid]
                if cid in game_stats:
                    item['stats'] = game_stats[cid]
                if cid in plat_dates:
                    item['platinum_date'] = plat_dates[cid].isoformat()
                queue.append(item)

            return Response({
                'queue': queue,
                'count': total_count,
                'has_more': has_more,
                'next_offset': offset + limit,
            })

        except Exception as e:
            logger.exception(f"Wizard queue error: {e}")
            return Response(
                {'error': 'Failed to load game queue.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    def _get_dlc_queue(self, profile, plat_concept_ids, filter_mode, limit, offset):
        """Build DLC queue grouped by parent concept."""
        from trophies.models import Trophy, EarnedTrophy, UserConceptRating
        from django.db.models import Count
        from django.db.models.functions import Lower

        # Find all DLC groups for user's platinumed concepts
        all_dlc_groups = list(
            ConceptTrophyGroup.objects.filter(
                concept_id__in=plat_concept_ids,
            ).exclude(
                trophy_group_id='default',
            ).select_related('concept')
            .order_by(Lower('concept__unified_title'), 'sort_order')
        )

        if not all_dlc_groups:
            return Response({'groups': [], 'total_items': 0, 'has_more': False})

        # Filter to only DLC groups where user has 100% completion in at
        # least one game stack. Two bulk queries instead of 2N.
        dlc_concept_ids = {g.concept_id for g in all_dlc_groups}
        dlc_group_ids = {g.trophy_group_id for g in all_dlc_groups}

        # Total trophies per (game_id, trophy_group_id)
        totals = {}
        for row in Trophy.objects.filter(
            game__concept_id__in=dlc_concept_ids,
            trophy_group_id__in=dlc_group_ids,
        ).values('game_id', 'game__concept_id', 'trophy_group_id').annotate(
            total=Count('id'),
        ):
            totals[(row['game_id'], row['trophy_group_id'])] = (row['total'], row['game__concept_id'])

        # Earned trophies per (game_id, trophy_group_id) for this profile
        earned = {}
        if totals:
            for row in EarnedTrophy.objects.filter(
                profile=profile,
                trophy__game_id__in={k[0] for k in totals},
                trophy__trophy_group_id__in=dlc_group_ids,
                earned=True,
            ).values('trophy__game_id', 'trophy__trophy_group_id').annotate(
                cnt=Count('id'),
            ):
                earned[(row['trophy__game_id'], row['trophy__trophy_group_id'])] = row['cnt']

        # Build set of (concept_id, trophy_group_id) pairs with 100% completion
        completed_pairs = set()
        for (game_id, group_id), (total, concept_id) in totals.items():
            if total > 0 and earned.get((game_id, group_id), 0) >= total:
                completed_pairs.add((concept_id, group_id))

        # Keep only DLC groups where user has 100% in at least one stack
        dlc_groups = [
            g for g in all_dlc_groups
            if (g.concept_id, g.trophy_group_id) in completed_pairs
        ]

        if not dlc_groups:
            return Response({'groups': [], 'total_items': 0, 'has_more': False})

        # Check which DLC groups user has already rated/reviewed
        dlc_ctg_ids = [g.id for g in dlc_groups]
        dlc_rated = set(
            UserConceptRating.objects.filter(
                profile=profile,
                concept_trophy_group_id__in=dlc_ctg_ids,
            ).values_list('concept_trophy_group_id', flat=True)
        )
        dlc_reviewed = set(
            Review.objects.filter(
                profile=profile,
                is_deleted=False,
                concept_trophy_group_id__in=dlc_ctg_ids,
            ).values_list('concept_trophy_group_id', flat=True)
        )

        # Pre-fetch existing DLC ratings
        dlc_existing_ratings = {}
        rated_dlc_ids = [gid for gid in dlc_ctg_ids if gid in dlc_rated]
        if rated_dlc_ids:
            for r in UserConceptRating.objects.filter(
                profile=profile,
                concept_trophy_group_id__in=rated_dlc_ids,
            ).values(
                'concept_trophy_group_id', 'difficulty', 'grindiness',
                'hours_to_platinum', 'fun_ranking', 'overall_rating',
            ):
                dlc_existing_ratings[r['concept_trophy_group_id']] = {
                    'difficulty': r['difficulty'],
                    'grindiness': r['grindiness'],
                    'hours_to_platinum': r['hours_to_platinum'],
                    'fun_ranking': r['fun_ranking'],
                    'overall_rating': float(r['overall_rating']),
                }

        # Pre-fetch existing DLC reviews
        dlc_existing_reviews = {}
        reviewed_dlc_ids = [gid for gid in dlc_ctg_ids if gid in dlc_reviewed]
        if reviewed_dlc_ids:
            for r in Review.objects.filter(
                profile=profile,
                is_deleted=False,
                concept_trophy_group_id__in=reviewed_dlc_ids,
            ).values('concept_trophy_group_id', 'id', 'body', 'recommended'):
                dlc_existing_reviews[r['concept_trophy_group_id']] = {
                    'id': r['id'],
                    'body': r['body'],
                    'recommended': r['recommended'],
                }

        # Build groups dict keyed by concept_id preserving order
        from collections import OrderedDict
        groups_dict = OrderedDict()
        total_items = 0

        for g in dlc_groups:
            has_rating = g.id in dlc_rated
            has_review = g.id in dlc_reviewed

            # Apply filter
            if filter_mode == 'unrated' and has_rating:
                continue
            elif filter_mode == 'unreviewed' and has_review:
                continue
            elif filter_mode == 'both' and has_rating and has_review:
                continue

            cid = g.concept_id
            if cid not in groups_dict:
                groups_dict[cid] = {
                    'concept_id': cid,
                    'unified_title': g.concept.unified_title,
                    'concept_icon_url': g.concept.concept_icon_url or '',
                    'slug': g.concept.slug,
                    'items': [],
                }

            item = {
                'trophy_group_id': g.trophy_group_id,
                'trophy_group_name': g.display_name,
                'has_rating': has_rating,
                'has_review': has_review,
                'is_dlc': True,
            }
            if g.id in dlc_existing_ratings:
                item['existing_rating'] = dlc_existing_ratings[g.id]
            if g.id in dlc_existing_reviews:
                item['existing_review'] = dlc_existing_reviews[g.id]

            groups_dict[cid]['items'].append(item)
            total_items += 1

        all_groups = list(groups_dict.values())

        # Flatten all items, paginate at item level, then re-group
        flat_items = []
        for grp in all_groups:
            for item in grp['items']:
                flat_items.append((grp, item))

        page_items = flat_items[offset:offset + limit]
        has_more = (offset + limit) < len(flat_items)

        # Re-group paginated items
        page_groups_dict = OrderedDict()
        for grp, item in page_items:
            cid = grp['concept_id']
            if cid not in page_groups_dict:
                page_groups_dict[cid] = {
                    k: v for k, v in grp.items() if k != 'items'
                }
                page_groups_dict[cid]['items'] = []
            page_groups_dict[cid]['items'].append(item)

        return Response({
            'groups': list(page_groups_dict.values()),
            'total_items': total_items,
            'has_more': has_more,
            'next_offset': offset + limit,
        })


# ------------------------------------------------------------------ #
#  Review List & Create
# ------------------------------------------------------------------ #

class ReviewListView(APIView):
    """List reviews for a concept trophy group."""
    authentication_classes = [SessionAuthentication, TokenAuthentication]
    permission_classes = []

    def get(self, request, concept_id, group_id):
        """
        GET /api/v1/reviews/<concept_id>/group/<group_id>/
        Query params: sort (helpful/newest/oldest), limit, offset
        """
        try:
            concept, ctg, err = _get_concept_and_group(concept_id, group_id)
            if err:
                return err

            # Parse query params
            sort = request.query_params.get('sort', 'helpful')
            if sort not in ('helpful', 'newest', 'oldest'):
                sort = 'helpful'
            limit = min(safe_int(request.query_params.get('limit', 10), 10), 50)
            offset = max(safe_int(request.query_params.get('offset', 0), 0), 0)

            # Sort mapping
            sort_map = {
                'helpful': ('-helpful_count', '-created_at'),
                'newest': ('-created_at',),
                'oldest': ('created_at',),
            }

            qs = Review.objects.filter(
                concept=concept,
                concept_trophy_group=ctg,
                is_deleted=False,
            ).select_related('profile').prefetch_related(
                'profile__user_titles__title',
            ).order_by(*sort_map[sort])

            # Get user profile and their own review (if authenticated)
            profile = None
            user_review_data = None
            if request.user.is_authenticated:
                profile = getattr(request.user, 'profile', None)

            # Find user's own review for this group
            if profile:
                user_review = qs.filter(profile=profile).first()
                if user_review:
                    user_stats = ReviewService.get_reviewer_stats(profile, concept)
                    user_review_data = _serialize_review(
                        user_review,
                        reviewer_stats=user_stats,
                        is_own=True,
                    )

            total_count = qs.count()
            paginated = list(qs[offset:offset + limit])
            has_more = (offset + limit) < total_count

            # Batch-fetch reviewer stats
            profile_ids = list({r.profile_id for r in paginated})
            bulk_stats = ReviewService.get_reviewer_stats_bulk(profile_ids, concept)

            # Batch-fetch user's votes for visible reviews
            user_votes = {}
            if profile and paginated:
                review_ids = [r.id for r in paginated]
                votes = ReviewVote.objects.filter(
                    review_id__in=review_ids, profile=profile,
                ).values_list('review_id', 'vote_type')
                for rid, vtype in votes:
                    user_votes.setdefault(rid, set()).add(vtype)

            # Serialize reviews
            reviews_data = []
            for review in paginated:
                rv = user_votes.get(review.id, set())
                reviews_data.append(_serialize_review(
                    review,
                    reviewer_stats=bulk_stats.get(review.profile_id, {}),
                    user_voted_helpful='helpful' in rv,
                    user_voted_funny='funny' in rv,
                    is_own=(profile and review.profile_id == profile.id),
                ))

            return Response({
                'reviews': reviews_data,
                'count': total_count,
                'has_more': has_more,
                'next_offset': offset + limit,
                'sort': sort,
                'user_review': user_review_data,
            })

        except Exception as e:
            logger.exception(f"Review list error: {e}")
            return Response(
                {'error': 'Internal error.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class ReviewCreateView(APIView):
    """Create a review for a concept trophy group."""
    authentication_classes = [SessionAuthentication, TokenAuthentication]
    permission_classes = [IsAuthenticated]

    @method_decorator(ratelimit(key='user', rate='10/m', method='POST', block=True))
    def post(self, request, concept_id, group_id):
        """
        POST /api/v1/reviews/<concept_id>/group/<group_id>/create/
        Body: {body: str, recommended: bool}
        """
        try:
            profile, err = _get_profile_or_error(request)
            if err:
                return err

            concept, ctg, err = _get_concept_and_group(concept_id, group_id)
            if err:
                return err

            body = request.data.get('body')
            recommended = request.data.get('recommended')

            if body is None:
                return Response(
                    {'error': 'body is required.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            if recommended is None:
                return Response(
                    {'error': 'recommended is required.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            if not isinstance(recommended, bool):
                return Response(
                    {'error': 'recommended must be a boolean.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            review, error = ReviewService.create_review(
                profile=profile,
                concept=concept,
                concept_trophy_group=ctg,
                body=body,
                recommended=recommended,
            )

            if error:
                return Response({'error': error}, status=status.HTTP_400_BAD_REQUEST)

            reviewer_stats = ReviewService.get_reviewer_stats(profile, concept)
            recommendation_stats = ReviewService.get_recommendation_stats(concept, ctg)

            return Response({
                'review': _serialize_review(
                    review, reviewer_stats=reviewer_stats, is_own=True,
                ),
                'recommendation_stats': recommendation_stats,
            }, status=status.HTTP_201_CREATED)

        except Exception as e:
            logger.exception(f"Review create error: {e}")
            return Response(
                {'error': 'Internal error.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


# ------------------------------------------------------------------ #
#  Review Detail (GET / PUT / DELETE)
# ------------------------------------------------------------------ #

class ReviewDetailView(APIView):
    """Get, edit, or delete a single review."""
    authentication_classes = [SessionAuthentication, TokenAuthentication]
    permission_classes = []

    def get(self, request, review_id):
        """GET /api/v1/reviews/<review_id>/"""
        try:
            review, err = _get_review_or_error(review_id)
            if err:
                return err

            reviewer_stats = ReviewService.get_reviewer_stats(
                review.profile, review.concept,
            )

            profile = None
            user_voted_helpful = False
            user_voted_funny = False
            if request.user.is_authenticated:
                profile = getattr(request.user, 'profile', None)
                if profile:
                    user_vote_types = set(ReviewVote.objects.filter(
                        review=review, profile=profile,
                    ).values_list('vote_type', flat=True))
                    user_voted_helpful = 'helpful' in user_vote_types
                    user_voted_funny = 'funny' in user_vote_types

            return Response({
                'review': _serialize_review(
                    review,
                    reviewer_stats=reviewer_stats,
                    user_voted_helpful=user_voted_helpful,
                    user_voted_funny=user_voted_funny,
                    is_own=(profile and review.profile_id == profile.id),
                ),
            })

        except Exception as e:
            logger.exception(f"Review detail error: {e}")
            return Response(
                {'error': 'Internal error.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    @method_decorator(ratelimit(key='user', rate='20/m', method='PUT', block=True))
    def put(self, request, review_id):
        """
        PUT /api/v1/reviews/<review_id>/
        Body: {body: str (optional), recommended: bool (optional)}
        """
        try:
            profile, err = _get_profile_or_error(request)
            if err:
                return err

            review, err = _get_review_or_error(review_id)
            if err:
                return err

            body = request.data.get('body')
            recommended = request.data.get('recommended')

            if body is None and recommended is None:
                return Response(
                    {'error': 'At least one field (body or recommended) must be provided.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            if recommended is not None and not isinstance(recommended, bool):
                return Response(
                    {'error': 'recommended must be a boolean.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            success, error = ReviewService.update_review(
                review, profile, body=body, recommended=recommended,
            )

            if not success:
                return Response({'error': error}, status=status.HTTP_400_BAD_REQUEST)

            review.refresh_from_db()
            reviewer_stats = ReviewService.get_reviewer_stats(profile, review.concept)

            return Response({
                'review': _serialize_review(
                    review, reviewer_stats=reviewer_stats, is_own=True,
                ),
            })

        except Exception as e:
            logger.exception(f"Review edit error: {e}")
            return Response(
                {'error': 'Internal error.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    @method_decorator(ratelimit(key='user', rate='10/m', method='DELETE', block=True))
    def delete(self, request, review_id):
        """DELETE /api/v1/reviews/<review_id>/"""
        try:
            profile, err = _get_profile_or_error(request)
            if err:
                return err

            review, err = _get_review_or_error(review_id)
            if err:
                return err

            success, error = ReviewService.delete_review(
                review, profile, is_admin=request.user.is_staff,
            )

            if not success:
                return Response({'error': error}, status=status.HTTP_400_BAD_REQUEST)

            return Response({'success': True, 'message': 'Review deleted.'})

        except Exception as e:
            logger.exception(f"Review delete error: {e}")
            return Response(
                {'error': 'Internal error.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


# ------------------------------------------------------------------ #
#  Review Voting
# ------------------------------------------------------------------ #

class ReviewVoteView(APIView):
    """Toggle helpful/funny vote on a review."""
    authentication_classes = [SessionAuthentication, TokenAuthentication]
    permission_classes = [IsAuthenticated]

    @method_decorator(ratelimit(key='user', rate='30/m', method='POST', block=True))
    def post(self, request, review_id):
        """
        POST /api/v1/reviews/<review_id>/vote/
        Body: {vote_type: 'helpful' | 'funny'}
        """
        try:
            profile, err = _get_profile_or_error(request)
            if err:
                return err

            review, err = _get_review_or_error(review_id)
            if err:
                return err

            vote_type = request.data.get('vote_type')
            if not vote_type:
                return Response(
                    {'error': 'vote_type is required.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            voted, error = ReviewService.toggle_vote(review, profile, vote_type)

            if error:
                return Response({'error': error}, status=status.HTTP_400_BAD_REQUEST)

            # Ensure both counts are fresh after the toggle
            review.refresh_from_db(fields=['helpful_count', 'funny_count'])

            return Response({
                'voted': voted,
                'helpful_count': review.helpful_count,
                'funny_count': review.funny_count,
            })

        except Exception as e:
            logger.exception(f"Review vote error: {e}")
            return Response(
                {'error': 'Internal error.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


# ------------------------------------------------------------------ #
#  Review Reporting
# ------------------------------------------------------------------ #

class ReviewReportView(APIView):
    """Report a review for moderation."""
    authentication_classes = [SessionAuthentication, TokenAuthentication]
    permission_classes = [IsAuthenticated]

    VALID_REASONS = ['spam', 'harassment', 'inappropriate', 'spoiler', 'misinformation', 'other']

    @method_decorator(ratelimit(key='user', rate='5/h', method='POST', block=True))
    def post(self, request, review_id):
        """
        POST /api/v1/reviews/<review_id>/report/
        Body: {reason: str, details: str (optional)}
        """
        try:
            profile, err = _get_profile_or_error(request)
            if err:
                return err

            review, err = _get_review_or_error(review_id)
            if err:
                return err

            reason = request.data.get('reason')
            details = request.data.get('details', '')
            if not isinstance(details, str):
                details = ''

            if not reason:
                return Response(
                    {'error': 'reason is required.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            if reason not in self.VALID_REASONS:
                return Response(
                    {'error': f'Invalid reason. Must be one of: {", ".join(self.VALID_REASONS)}'},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            report, error = ReviewService.report_review(review, profile, reason, details)

            if error:
                return Response({'error': error}, status=status.HTTP_400_BAD_REQUEST)

            return Response({
                'success': True,
                'message': 'Review reported successfully.',
            })

        except Exception as e:
            logger.exception(f"Review report error: {e}")
            return Response(
                {'error': 'Internal error.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


# ------------------------------------------------------------------ #
#  Review Replies
# ------------------------------------------------------------------ #

class ReviewReplyListView(APIView):
    """List and create replies on a review."""
    authentication_classes = [SessionAuthentication, TokenAuthentication]
    permission_classes = []

    def get(self, request, review_id):
        """
        GET /api/v1/reviews/<review_id>/replies/
        Query params: limit, offset
        """
        try:
            review, err = _get_review_or_error(review_id)
            if err:
                return err

            limit = min(safe_int(request.query_params.get('limit', 10), 10), 50)
            offset = max(safe_int(request.query_params.get('offset', 0), 0), 0)

            qs = ReviewReply.objects.filter(
                review=review, is_deleted=False,
            ).select_related('profile').prefetch_related(
                'profile__user_titles__title',
            ).order_by('created_at')

            total_count = qs.count()
            paginated = list(qs[offset:offset + limit])
            has_more = (offset + limit) < total_count

            profile = None
            if request.user.is_authenticated:
                profile = getattr(request.user, 'profile', None)

            replies_data = [
                _serialize_reply(
                    reply,
                    is_own=(profile and reply.profile_id == profile.id),
                )
                for reply in paginated
            ]

            return Response({
                'replies': replies_data,
                'count': total_count,
                'has_more': has_more,
                'next_offset': offset + limit,
            })

        except Exception as e:
            logger.exception(f"Reply list error: {e}")
            return Response(
                {'error': 'Internal error.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    @method_decorator(ratelimit(key='user', rate='10/m', method='POST', block=True))
    def post(self, request, review_id):
        """
        POST /api/v1/reviews/<review_id>/replies/
        Body: {body: str}
        """
        try:
            profile, err = _get_profile_or_error(request)
            if err:
                return err

            review, err = _get_review_or_error(review_id)
            if err:
                return err

            body = request.data.get('body')
            if not body:
                return Response(
                    {'error': 'body is required.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            reply, error = ReviewService.create_reply(review, profile, body)

            if error:
                return Response({'error': error}, status=status.HTTP_400_BAD_REQUEST)

            return Response(
                {'reply': _serialize_reply(reply, is_own=True)},
                status=status.HTTP_201_CREATED,
            )

        except Exception as e:
            logger.exception(f"Reply create error: {e}")
            return Response(
                {'error': 'Internal error.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class ReviewReplyDetailView(APIView):
    """Edit or delete a reply."""
    authentication_classes = [SessionAuthentication, TokenAuthentication]
    permission_classes = [IsAuthenticated]

    @method_decorator(ratelimit(key='user', rate='20/m', method='PUT', block=True))
    def put(self, request, reply_id):
        """
        PUT /api/v1/reviews/replies/<reply_id>/
        Body: {body: str}
        """
        try:
            profile, err = _get_profile_or_error(request)
            if err:
                return err

            try:
                reply = ReviewReply.objects.select_related('profile').get(
                    id=reply_id, is_deleted=False,
                )
            except ReviewReply.DoesNotExist:
                return Response(
                    {'error': 'Reply not found.'},
                    status=status.HTTP_404_NOT_FOUND,
                )

            new_body = request.data.get('body')
            if not new_body:
                return Response(
                    {'error': 'body is required.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            success, error = ReviewService.edit_reply(reply, profile, new_body)

            if not success:
                return Response({'error': error}, status=status.HTTP_400_BAD_REQUEST)

            reply.refresh_from_db()
            return Response({'reply': _serialize_reply(reply, is_own=True)})

        except Exception as e:
            logger.exception(f"Reply edit error: {e}")
            return Response(
                {'error': 'Internal error.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    @method_decorator(ratelimit(key='user', rate='10/m', method='DELETE', block=True))
    def delete(self, request, reply_id):
        """DELETE /api/v1/reviews/replies/<reply_id>/"""
        try:
            profile, err = _get_profile_or_error(request)
            if err:
                return err

            try:
                reply = ReviewReply.objects.select_related('profile', 'review').get(
                    id=reply_id, is_deleted=False,
                )
            except ReviewReply.DoesNotExist:
                return Response(
                    {'error': 'Reply not found.'},
                    status=status.HTTP_404_NOT_FOUND,
                )

            success, error = ReviewService.delete_reply(
                reply, profile, is_admin=request.user.is_staff,
            )

            if not success:
                return Response({'error': error}, status=status.HTTP_400_BAD_REQUEST)

            return Response({'success': True, 'message': 'Reply deleted.'})

        except Exception as e:
            logger.exception(f"Reply delete error: {e}")
            return Response(
                {'error': 'Internal error.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


# ------------------------------------------------------------------ #
#  DLC / Group Rating
# ------------------------------------------------------------------ #

class GroupRatingView(APIView):
    """Submit or update a rating for a concept trophy group (base game or DLC)."""
    authentication_classes = [SessionAuthentication, TokenAuthentication]
    permission_classes = [IsAuthenticated]

    @method_decorator(ratelimit(key='user', rate='30/m', method='POST', block=True))
    def post(self, request, concept_id, group_id):
        """
        POST /api/v1/reviews/<concept_id>/group/<group_id>/rate/
        Body: {difficulty, grindiness, hours_to_platinum, fun_ranking, overall_rating}
        """
        try:
            profile, err = _get_profile_or_error(request)
            if err:
                return err

            concept, ctg, err = _get_concept_and_group(concept_id, group_id)
            if err:
                return err

            from trophies.services.concept_trophy_group_service import ConceptTrophyGroupService

            can, reason = ConceptTrophyGroupService.can_rate_group(profile, concept, ctg)
            if not can:
                return Response({'error': reason}, status=status.HTTP_403_FORBIDDEN)

            from trophies.models import UserConceptRating
            from trophies.forms import UserConceptRatingForm
            from trophies.services.rating_service import RatingService

            # Determine the FK value for this group
            # Base game (trophy_group_id='default'): concept_trophy_group=None for backward compat
            ctg_fk = None if ctg.trophy_group_id == 'default' else ctg

            existing_rating = UserConceptRating.objects.filter(
                profile=profile,
                concept=concept,
                concept_trophy_group=ctg_fk,
            ).first()

            form = UserConceptRatingForm(request.data, instance=existing_rating)
            if not form.is_valid():
                return Response(
                    {'success': False, 'errors': form.errors},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            rating = form.save(commit=False)
            rating.profile = profile
            rating.concept = concept
            rating.concept_trophy_group = ctg_fk
            rating.save()

            # Invalidate caches
            RatingService.invalidate_cache(concept)
            RatingService.invalidate_group_cache(concept, ctg)

            # Check rating milestones
            from trophies.services.milestone_service import check_all_milestones_for_user
            check_all_milestones_for_user(profile, criteria_type='rating_count')

            updated_averages = RatingService.get_community_averages_for_group(concept, ctg)

            return Response({
                'success': True,
                'message': 'Rating updated!' if existing_rating else 'Rating submitted successfully!',
                'community_averages': updated_averages,
            })

        except Exception as e:
            logger.exception(f"Group rating error: {e}")
            return Response(
                {'error': 'Internal error.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
