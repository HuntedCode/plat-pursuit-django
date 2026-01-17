from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from .serializers import GenerateCodeSerializer, VerifySerializer, ProfileSerializer, TrophyCaseSerializer, CommentSerializer, CommentCreateSerializer
from trophies.models import Profile, Comment, Concept
from trophies.services.comment_service import CommentService
from django.core.paginator import Paginator
from django.db.models import F
from django.utils import timezone
from datetime import timedelta
from trophies.psn_manager import PSNManager
from trophies.services.badge_service import initial_badge_check
import time
import math
import logging

logger = logging.getLogger('psn_api')

class GenerateCodeView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = GenerateCodeSerializer(data=request.data)
        if serializer.is_valid():
            psn_username = serializer.validated_data['psn_username'].lower()
            if not psn_username:
                return Response({'error': 'psn_username required.'}, status=status.HTTP_400_BAD_REQUEST)
            
            profile, created = Profile.objects.get_or_create(psn_username=psn_username)
            profile.generate_verification_code()
            if created:
                PSNManager.initial_sync(profile)
            else:
                profile.attempt_sync()

            return Response({
                "success": True,
                "code": profile.verification_code,
                "message": f"Add '{profile.verification_code}' to your PSN 'About Me' section and run the /verify command!"
            })
        else:
            logger.error(f"Serializer errors: {serializer.errors}")
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
    
class VerifyView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = VerifySerializer(data=request.data)
        if serializer.is_valid():
            discord_id = serializer.validated_data['discord_id']
            psn_username = serializer.validated_data['psn_username'].lower()
            if not all([discord_id, psn_username]):
                return Response({'error': 'discord_id and psn_username required.'}, status=status.HTTP_400_BAD_REQUEST)
            
            try:
                profile = Profile.objects.get(psn_username=psn_username)
                
                start_time = timezone.now()
                timeout_seconds = 30
                poll_interval_seconds = 1

                is_syncing = profile.attempt_sync()
                if not is_syncing:
                    PSNManager.sync_profile_data(profile)

                while (timezone.now() - start_time).total_seconds() < timeout_seconds:
                    profile.refresh_from_db()
                    if profile.last_synced > start_time:
                        logger.info(f"Sync completed for profile {profile.id} after polling.")
                        break
                    time.sleep(poll_interval_seconds)
                
                if profile.last_synced <= start_time:
                    logger.warning(f"Sync timeout for profile {profile.id} after {timeout_seconds}s.")
                    return Response({'success': False, 'message': 'Sync timed out. Try again later.'}, status=status.HTTP_408_REQUEST_TIMEOUT)

                if profile.verify_code(profile.about_me):
                    profile.link_discord(discord_id)
                    initial_badge_check(profile)
                    return Response({'success': True, 'message': 'Verified and linked successfully!'})
                else:
                    return Response({'success': False, 'message': 'Verification failed. Check code and try again.'})
            except Profile.DoesNotExist:
                return Response({'error': 'Profile not found.'}, status=status.HTTP_404_NOT_FOUND)
        else:
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

class CheckLinkedView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        discord_id = request.query_params.get('discord_id')
        if not discord_id:
            return Response({'error': 'discord_id required.'}, status=status.HTTP_400_BAD_REQUEST)
        
        try:
            profile = Profile.objects.get(discord_id=discord_id)
            return Response({'linked': True, 'psn_username': profile.display_psn_username})
        except Profile.DoesNotExist:
            return Response({'linked': False, 'message': 'No linked profile found.'})
        except Exception as e:
            logger.error(f"Check linked error: {e}")
            return Response({'error': 'Internal error.'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

class UnlinkView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        discord_id = request.data.get('discord_id')
        if not discord_id:
            return Response({'error': 'discord_id required.'}, status=status.HTTP_400_BAD_REQUEST)
        
        try:
            profile = Profile.objects.get(discord_id=discord_id)
            profile.unlink_discord()
            return Response({'success': True, 'message': 'Unlinked successfully.'})
        except Profile.DoesNotExist:
            return Response({'success': False, 'message': 'No linked profile found.'})
        except Exception as e:
            logger.error(f"Unlink error: {e}")
            return Response({'error': 'Internal error.'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

class RefreshView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        discord_id = request.data.get('discord_id')
        admin_override = request.data.get('admin_override', False)

        if not discord_id:
            return Response({'error': 'discord_id required.'}, status=status.HTTP_400_BAD_REQUEST)
        
        try:
            profile = Profile.objects.get(discord_id=discord_id)
            time_since_last_sync = profile.get_time_since_last_sync()
            is_syncing = profile.attempt_sync()
            if is_syncing or (admin_override or not profile.psn_history_public):
                if not is_syncing:
                    PSNManager.profile_refresh(profile)

                start_time = timezone.now()
                timeout_seconds = 30
                poll_interval_seconds = 1

                while (timezone.now() - start_time).total_seconds() < timeout_seconds:
                    profile.refresh_from_db()
                    if profile.last_synced > start_time:
                        if profile.psn_history_public:
                            return Response({'linked': True, 'success': True, 'psn_username': profile.display_psn_username})
                        else:
                            logger.warning(f"Permission error for profile {profile.id}.")
                            return Response({'linked': True, 'success': False, 'message': "Permissions error. Please make sure the PSN setting 'Gaming History' is set to 'Anyone' and try again."})
                    time.sleep(poll_interval_seconds)

            else:
                total_seconds = profile.get_seconds_to_next_sync()
                minutes = math.ceil(total_seconds / 60)
                return Response({'linked': True, 'succes': False, 'message': f"Too many profile refresh requests! Please try again in: {int(minutes)} minutes"})
        except Profile.DoesNotExist:
            return Response({'linked': False, 'message': 'No linked profile found.'})
        except Exception as e:
            logger.error(f"Refresh error: {e}")
            return Response({'error': 'Internal error.'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

class SummaryView(APIView):
    permission_classes = [IsAuthenticated]
    
    def get(self, request):
        discord_id = request.query_params.get('discord_id')
        print(f"Profile request for {discord_id}")
        if not discord_id:
            return Response({'error': 'discord_id required.'}, status=status.HTTP_400_BAD_REQUEST)
        
        try:
            profile = Profile.objects.get(discord_id=discord_id)
            serializer = ProfileSerializer(profile)
            return Response({'linked': True, 'profile': serializer.data})
        except Profile.DoesNotExist:
            return Response({'linked': False, 'message': 'No linked PSN profile found.'})
        except Exception as e:
            logger.error(f"Trophies fetch error: {e}")
            return Response({'error': 'Internal error.'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

class TrophyCaseView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        discord_id = request.query_params.get('discord_id')
        page = int(request.query_params.get('page', 1))
        per_page = int(request.query_params.get('per_page', 10))
        if not discord_id:
            return Response({'error', 'discord_id required.'}, status=status.HTTP_400_BAD_REQUEST)
        
        try:
            profile = Profile.objects.get(discord_id=discord_id)
            platinums = profile.earned_trophy_entries.filter(earned=True, trophy__trophy_type='platinum').order_by(F('earned_date_time').desc(nulls_last=True))
            total_plats = profile.total_plats
            paginator = Paginator(platinums, per_page)
            paginated_platinums = paginator.page(page)
            serializer = TrophyCaseSerializer(paginated_platinums, many=True)
            return Response({
                'linked': True,
                'platinums': serializer.data,
                'total_pages': paginator.num_pages,
                'current_page': page,
                'total_plats': total_plats
            })
        except Profile.DoesNotExist:
            return Response({'linked': False, 'message': 'No linked profile found.'})
        except Exception as e:
            logger.error(f"Trophy case error: {e}")
            return Response({'error': 'Internal error.'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class CommentListView(APIView):
    """Get comments for a Concept or Trophy within a Concept."""

    def get(self, request, concept_id, trophy_id=None):
        """
        GET /api/v1/comments/concept/<concept_id>/
        GET /api/v1/comments/concept/<concept_id>/trophy/<trophy_id>/
        Query params: sort (top/new/old)
        """
        try:
            # Get the concept
            try:
                concept = Concept.objects.get(id=concept_id)
            except Concept.DoesNotExist:
                return Response({'error': 'Concept not found.'}, status=status.HTTP_404_NOT_FOUND)

            # Get user profile if authenticated
            profile = None
            if request.user.is_authenticated:
                profile = getattr(request.user, 'profile', None)

            # Get sort parameter
            sort = request.query_params.get('sort', 'top')
            if sort not in ['top', 'new', 'old']:
                sort = 'top'

            # Get comments using service
            comments = CommentService.get_comments_for_concept(
                concept,
                profile,
                sort,
                trophy_id
            )

            # Only return top-level comments (replies are nested in serializer)
            top_level_comments = comments.filter(parent__isnull=True)

            # Serialize
            serializer = CommentSerializer(
                top_level_comments,
                many=True,
                context={'request': request}
            )

            return Response({
                'comments': serializer.data,
                'count': concept.comment_count,
                'sort': sort,
                'trophy_id': trophy_id
            })

        except Exception as e:
            logger.error(f"Comment list error: {e}")
            return Response({'error': 'Internal error.'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class CommentCreateView(APIView):
    """Create a new comment or reply on a Concept or Trophy."""
    permission_classes = [IsAuthenticated]

    def post(self, request, concept_id, trophy_id=None):
        """
        POST /api/v1/comments/concept/<concept_id>/create/
        POST /api/v1/comments/concept/<concept_id>/trophy/<trophy_id>/create/
        Body: { body, parent_id (optional), image (optional) }
        """
        try:
            # Get user profile
            profile = getattr(request.user, 'profile', None)
            if not profile:
                return Response(
                    {'error': 'User does not have a profile.'},
                    status=status.HTTP_400_BAD_REQUEST
                )

            # Get the concept
            try:
                concept = Concept.objects.get(id=concept_id)
            except Concept.DoesNotExist:
                return Response({'error': 'Concept not found.'}, status=status.HTTP_404_NOT_FOUND)

            # Validate input
            serializer = CommentCreateSerializer(data=request.data)
            if not serializer.is_valid():
                return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

            body = serializer.validated_data['body']
            parent_id = serializer.validated_data.get('parent_id')
            image = request.FILES.get('image')

            # Get parent comment if specified
            parent = None
            if parent_id:
                try:
                    parent = Comment.objects.get(id=parent_id)
                    # Verify parent is for the same concept and trophy_id
                    if parent.concept != concept or parent.trophy_id != trophy_id:
                        return Response(
                            {'error': 'Parent comment does not belong to this concept/trophy.'},
                            status=status.HTTP_400_BAD_REQUEST
                        )
                except Comment.DoesNotExist:
                    return Response({'error': 'Parent comment not found.'}, status=status.HTTP_404_NOT_FOUND)

            # Create comment using service
            comment, error = CommentService.create_comment(
                profile=profile,
                concept=concept,
                body=body,
                parent=parent,
                image=image,
                trophy_id=trophy_id
            )

            if error:
                return Response({'error': error}, status=status.HTTP_400_BAD_REQUEST)

            # Serialize and return
            comment_serializer = CommentSerializer(comment, context={'request': request})
            return Response(comment_serializer.data, status=status.HTTP_201_CREATED)

        except Exception as e:
            logger.error(f"Comment create error: {e}")
            return Response({'error': 'Internal error.'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class CommentDetailView(APIView):
    """Update or delete a comment."""
    permission_classes = [IsAuthenticated]

    def put(self, request, comment_id):
        """
        PUT /api/v1/comments/<comment_id>/
        Body: { body }
        """
        try:
            profile = getattr(request.user, 'profile', None)
            if not profile:
                return Response(
                    {'error': 'User does not have a profile.'},
                    status=status.HTTP_400_BAD_REQUEST
                )

            # Get comment
            try:
                comment = Comment.objects.get(id=comment_id)
            except Comment.DoesNotExist:
                return Response({'error': 'Comment not found.'}, status=status.HTTP_404_NOT_FOUND)

            # Validate input
            new_body = request.data.get('body')
            if not new_body:
                return Response({'error': 'body is required.'}, status=status.HTTP_400_BAD_REQUEST)

            # Edit using service
            success, error = CommentService.edit_comment(comment, profile, new_body)

            if not success:
                return Response({'error': error}, status=status.HTTP_400_BAD_REQUEST)

            # Serialize and return
            serializer = CommentSerializer(comment, context={'request': request})
            return Response(serializer.data)

        except Exception as e:
            logger.error(f"Comment edit error: {e}")
            return Response({'error': 'Internal error.'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    def delete(self, request, comment_id):
        """
        DELETE /api/v1/comments/<comment_id>/
        """
        try:
            profile = getattr(request.user, 'profile', None)
            if not profile:
                return Response(
                    {'error': 'User does not have a profile.'},
                    status=status.HTTP_400_BAD_REQUEST
                )

            # Get comment
            try:
                comment = Comment.objects.get(id=comment_id)
            except Comment.DoesNotExist:
                return Response({'error': 'Comment not found.'}, status=status.HTTP_404_NOT_FOUND)

            # Check if user is admin
            is_admin = request.user.is_staff

            # Delete using service
            success, error = CommentService.delete_comment(comment, profile, is_admin)

            if not success:
                return Response({'error': error}, status=status.HTTP_400_BAD_REQUEST)

            return Response({'success': True, 'message': 'Comment deleted.'})

        except Exception as e:
            logger.error(f"Comment delete error: {e}")
            return Response({'error': 'Internal error.'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class CommentVoteView(APIView):
    """Toggle upvote on a comment."""
    permission_classes = [IsAuthenticated]

    def post(self, request, comment_id):
        """
        POST /api/v1/comments/<comment_id>/vote/
        """
        try:
            profile = getattr(request.user, 'profile', None)
            if not profile:
                return Response(
                    {'error': 'User does not have a profile.'},
                    status=status.HTTP_400_BAD_REQUEST
                )

            # Get comment
            try:
                comment = Comment.objects.get(id=comment_id)
            except Comment.DoesNotExist:
                return Response({'error': 'Comment not found.'}, status=status.HTTP_404_NOT_FOUND)

            # Toggle vote using service
            voted, error = CommentService.toggle_vote(comment, profile)

            if error:
                return Response({'error': error}, status=status.HTTP_400_BAD_REQUEST)

            return Response({
                'voted': voted,
                'upvote_count': comment.upvote_count
            })

        except Exception as e:
            logger.error(f"Comment vote error: {e}")
            return Response({'error': 'Internal error.'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class CommentReportView(APIView):
    """Report a comment for moderation."""
    permission_classes = [IsAuthenticated]

    def post(self, request, comment_id):
        """
        POST /api/v1/comments/<comment_id>/report/
        Body: { reason, details (optional) }
        """
        try:
            profile = getattr(request.user, 'profile', None)
            if not profile:
                return Response(
                    {'error': 'User does not have a profile.'},
                    status=status.HTTP_400_BAD_REQUEST
                )

            # Get comment
            try:
                comment = Comment.objects.get(id=comment_id)
            except Comment.DoesNotExist:
                return Response({'error': 'Comment not found.'}, status=status.HTTP_404_NOT_FOUND)

            # Get reason and details
            reason = request.data.get('reason')
            details = request.data.get('details', '')

            if not reason:
                return Response({'error': 'reason is required.'}, status=status.HTTP_400_BAD_REQUEST)

            # Validate reason
            valid_reasons = ['spam', 'harassment', 'inappropriate', 'misinformation', 'other']
            if reason not in valid_reasons:
                return Response(
                    {'error': f'Invalid reason. Must be one of: {", ".join(valid_reasons)}'},
                    status=status.HTTP_400_BAD_REQUEST
                )

            # Create report using service
            success, error = CommentService.report_comment(comment, profile, reason, details)

            if not success:
                return Response({'error': error}, status=status.HTTP_400_BAD_REQUEST)

            return Response({'success': True, 'message': 'Comment reported successfully.'})

        except Exception as e:
            logger.error(f"Comment report error: {e}")
            return Response({'error': 'Internal error.'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)