"""
Review API views.

Handles all REST endpoints for community reviews: CRUD, voting, replies, reporting,
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
            {'error': 'Community Hub is not available for this game.'},
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
    return {
        'profile_id': profile.id,
        'psn_username': profile.psn_username,
        'display_psn_username': profile.display_psn_username,
        'avatar_url': profile.avatar_url,
    }


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
            ).select_related('profile').order_by(*sort_map[sort])

            # Get user profile and their own review (if authenticated)
            profile = None
            user_review_data = None
            if request.user.is_authenticated:
                profile = getattr(request.user, 'profile', None)

            # Find user's own review for this group (shown separately at top)
            if profile:
                user_review = qs.filter(profile=profile).first()
                if user_review:
                    user_stats = ReviewService.get_reviewer_stats(profile, concept)
                    user_review_data = _serialize_review(
                        user_review,
                        reviewer_stats=user_stats,
                        is_own=True,
                    )
                    # Exclude the user's own review from the main feed
                    qs = qs.exclude(id=user_review.id)

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
            ).select_related('profile').order_by('created_at')

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
