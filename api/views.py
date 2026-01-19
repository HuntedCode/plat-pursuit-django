from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.authentication import SessionAuthentication, TokenAuthentication
from .serializers import GenerateCodeSerializer, VerifySerializer, ProfileSerializer, TrophyCaseSerializer, CommentSerializer, CommentCreateSerializer
from trophies.models import Profile, Comment, Concept
from trophies.services.comment_service import CommentService
from django.core.paginator import Paginator
from django.db.models import F
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views.decorators.cache import never_cache
from django_ratelimit.decorators import ratelimit
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
    authentication_classes = [SessionAuthentication, TokenAuthentication]
    permission_classes = []  # Allow unauthenticated users to view comments

    def get(self, request, concept_id, trophy_id=None):
        """
        GET /api/v1/comments/concept/<concept_id>/
        GET /api/v1/comments/concept/<concept_id>/trophy/<trophy_id>/
        Query params:
            - sort (top/new/old)
            - response_format (html/json)
            - limit (default: 5)
            - offset (default: 0)
            - reply_limit (default: 3)
            - parent_id (for loading more replies)
            - reply_offset (for loading more replies)
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

            # Get pagination parameters
            limit = int(request.query_params.get('limit', 5))
            offset = int(request.query_params.get('offset', 0))
            reply_limit = int(request.query_params.get('reply_limit', 3))
            parent_id = request.query_params.get('parent_id', None)

            # Get comments using service
            comments = CommentService.get_comments_for_concept(
                concept,
                profile,
                sort,
                trophy_id
            )

            # Check if this is a request for more replies of a specific comment
            if parent_id:
                return self._handle_reply_pagination(
                    parent_id, comments, profile, request.user, sort, trophy_id
                )

            # Only return top-level comments (replies are nested)
            top_level_comments = comments.filter(parent__isnull=True)
            total_count = top_level_comments.count()

            # Apply pagination to top-level comments
            paginated_comments = top_level_comments[offset:offset + limit]
            has_more = (offset + limit) < total_count

            # Check if HTML format is requested
            response_format = request.query_params.get('response_format', 'json')

            if response_format == 'html':
                # Render comments as HTML using template
                from django.template.loader import render_to_string
                from trophies.models import CommentVote
                import traceback

                try:
                    comments_html = []
                    for comment in paginated_comments:
                        # Build context for each comment with reply pagination
                        comment_data = self._build_comment_context(
                            comment, profile, request.user,
                            reply_offset=0, reply_limit=reply_limit
                        )
                        html = render_to_string('partials/comment.html', {'comment': comment_data})
                        comments_html.append(html)

                    return Response({
                        'html': ''.join(comments_html),
                        'count': total_count,
                        'has_more': has_more,
                        'next_offset': offset + limit,
                        'sort': sort,
                        'trophy_id': trophy_id
                    })
                except Exception as html_error:
                    logger.error(f"HTML rendering error: {html_error}")
                    logger.error(traceback.format_exc())
                    return Response({'error': f'Template rendering failed: {str(html_error)}'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
            else:
                # Return JSON (original behavior with pagination)
                serializer = CommentSerializer(
                    paginated_comments,
                    many=True,
                    context={'request': request}
                )

                return Response({
                    'comments': serializer.data,
                    'count': total_count,
                    'has_more': has_more,
                    'next_offset': offset + limit,
                    'sort': sort,
                    'trophy_id': trophy_id
                })

        except Exception as e:
            logger.error(f"Comment list error: {e}")
            return Response({'error': 'Internal error.'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    def _get_comment_base_context(self, comment, depth=None, parent_id=None):
        """Build base context for a comment without permissions or author indicators."""
        author_profile = comment.profile

        return {
            'id': comment.id,
            'body': comment.display_body,
            'upvote_count': comment.upvote_count,
            'is_edited': comment.is_edited,
            'is_deleted': comment.is_deleted,
            'created_at': comment.created_at,
            'updated_at': comment.updated_at,
            'depth': depth if depth is not None else comment.depth,
            'author': {
                'username': author_profile.display_psn_username or author_profile.psn_username,
                'avatar_url': author_profile.avatar_url,
                'flag': author_profile.flag,
                'user_is_premium': author_profile.user_is_premium,
            },
            'user_has_voted': False,
            'user_is_moderator': False,
            'can_edit': False,
            'can_delete': False,
            'parent_id': parent_id,
            'replies': []
        }

    def _add_permissions(self, context, comment, viewing_profile, user):
        """Add permission flags to comment context."""
        from trophies.models import CommentVote

        if user.is_authenticated and viewing_profile:
            context['user_has_voted'] = CommentVote.objects.filter(
                comment=comment,
                profile=viewing_profile
            ).exists()
            context['is_moderator'] = comment.profile.user.is_staff if comment.profile.user else False
            context['can_edit'] = (comment.profile == viewing_profile and not comment.is_deleted)
            context['can_delete'] = (
                (comment.profile == viewing_profile or user.is_staff) and
                not comment.is_deleted
            )

    def _add_author_context(self, context, comment_profile, concept, trophy_id):
        """Add author context indicators (progress, platinum, trophy earned)."""
        from trophies.models import ProfileGame, EarnedTrophy

        # Concept-level: show game progress
        pg = ProfileGame.objects.filter(
            profile=comment_profile,
            game__concept=concept
        ).order_by('-progress').first()

        if pg:
            context['author_progress'] = pg.progress
            context['author_has_platinum'] = pg.has_plat
        else:
            context['author_progress'] = None
            context['author_has_platinum'] = False

        if not trophy_id is None:
            # Trophy-level: show if earned
            et = EarnedTrophy.objects.filter(
                profile=comment_profile,
                trophy__game__concept=concept,
                trophy__trophy_id=trophy_id,
                earned=True
            ).first()
            context['author_has_trophy'] = bool(et)

    def _build_comment_context(self, comment, viewing_profile, user, reply_offset=0, reply_limit=3):
        """Build context dict for rendering a comment template with reply pagination."""
        concept = comment.concept
        trophy_id = comment.trophy_id

        # Build base context
        context = self._get_comment_base_context(comment)

        # Add permissions
        self._add_permissions(context, comment, viewing_profile, user)

        # Add author context
        self._add_author_context(context, comment.profile, concept, trophy_id)

        # Build flattened replies (all descendants at one level)
        all_descendants = self._get_all_descendants(comment)
        all_descendants.sort(key=lambda x: (-x.upvote_count, -x.created_at.timestamp()))

        # Paginate replies
        total_reply_count = len(all_descendants)
        paginated_replies = all_descendants[reply_offset:reply_offset + reply_limit]

        for reply in paginated_replies:
            # Build reply with depth=1 and track parent_id
            reply_context = self._get_comment_base_context(reply, depth=1, parent_id=reply.parent_id)

            # Add permissions for reply
            self._add_permissions(reply_context, reply, viewing_profile, user)

            # Add author context for reply
            self._add_author_context(reply_context, reply.profile, concept, trophy_id)

            context['replies'].append(reply_context)

        # Add pagination metadata
        context['reply_count'] = total_reply_count
        context['reply_has_more'] = (reply_offset + reply_limit) < total_reply_count
        context['replies_shown'] = reply_offset + len(paginated_replies)

        return context

    def _get_all_descendants(self, comment):
        """Recursively get all descendants of a comment."""
        descendants = list(comment.replies.all())
        for child in comment.replies.all():
            descendants.extend(self._get_all_descendants(child))

        return descendants

    def _handle_reply_pagination(self, parent_id, comments, profile, user, sort, trophy_id):
        """Handle loading more replies for a specific comment."""
        from django.template.loader import render_to_string
        import traceback

        try:
            # Get the parent comment
            parent_comment = comments.filter(id=parent_id).first()
            if not parent_comment:
                return Response({'error': 'Comment not found.'}, status=status.HTTP_404_NOT_FOUND)

            # Get pagination parameters
            reply_offset = int(self.request.query_params.get('reply_offset', 0))
            reply_limit = int(self.request.query_params.get('reply_limit', 3))

            # Build flattened replies with pagination
            all_descendants = self._get_all_descendants(parent_comment)
            all_descendants.sort(key=lambda x: (-x.upvote_count, -x.created_at.timestamp()))

            # Paginate replies
            total_reply_count = len(all_descendants)
            paginated_replies = all_descendants[reply_offset:reply_offset + reply_limit]
            has_more = (reply_offset + reply_limit) < total_reply_count

            # Render only the reply HTML
            replies_html = []
            for reply in paginated_replies:
                reply_context = self._get_comment_base_context(reply, depth=1, parent_id=reply.parent_id)
                self._add_permissions(reply_context, reply, profile, user)
                self._add_author_context(reply_context, reply.profile, parent_comment.concept, trophy_id)

                html = render_to_string('partials/comment.html', {'comment': reply_context})
                replies_html.append(html)

            return Response({
                'html': ''.join(replies_html),
                'parent_id': parent_id,
                'reply_count': total_reply_count,
                'has_more': has_more,
                'shown': reply_offset + len(paginated_replies),
                'sort': sort
            })

        except Exception as e:
            logger.error(f"Reply pagination error: {e}")
            logger.error(traceback.format_exc())
            return Response({'error': 'Failed to load replies.'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class CommentCreateView(APIView):
    """Create a new comment or reply on a Concept or Trophy."""
    authentication_classes = [SessionAuthentication, TokenAuthentication]
    permission_classes = [IsAuthenticated]

    @method_decorator(ratelimit(key='user', rate='10/m', method='POST', block=True))
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
    authentication_classes = [SessionAuthentication, TokenAuthentication]
    permission_classes = [IsAuthenticated]

    @method_decorator(ratelimit(key='user', rate='20/m', method='PUT', block=True))
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
    authentication_classes = [SessionAuthentication, TokenAuthentication]
    permission_classes = [IsAuthenticated]

    @method_decorator(ratelimit(key='user', rate='30/m', method='POST', block=True))
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
    authentication_classes = [SessionAuthentication, TokenAuthentication]
    permission_classes = [IsAuthenticated]

    @method_decorator(ratelimit(key='user', rate='5/h', method='POST', block=True))
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