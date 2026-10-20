from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.authentication import SessionAuthentication, TokenAuthentication
from api.permissions import IsDiscordBot
from core.bot_auth import is_discord_bot
from .serializers import GenerateCodeSerializer, VerifySerializer, ProfileSerializer, TrophyCaseSerializer, CommentSerializer
from trophies.models import Profile, Comment, DiscordAlreadyLinkedError
from trophies.services.verification_service import PRIVATE_HISTORY_MESSAGE, VerificationService
from trophies.services.comment_service import CommentService
from django.core.paginator import Paginator
from django.db.models import F
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views.decorators.cache import never_cache
from django_ratelimit.decorators import ratelimit
from datetime import timedelta
from trophies.psn_manager import PSNManager
from trophies.util_modules.cache import redis_client
from trophies.services.discord_roles import sync_discord_roles
import time
import math
import logging
from api.utils import safe_int

logger = logging.getLogger('psn_api')


def bot_exempt_rate(default_rate):
    """Return a rate function that exempts the Discord bot from rate limiting."""
    def rate_fn(group, request):
        return None if is_discord_bot(request) else default_rate
    return rate_fn


class GenerateCodeView(APIView):
    permission_classes = [IsAuthenticated, IsDiscordBot]

    @method_decorator(ratelimit(key='user', rate=bot_exempt_rate('5/m'), method='POST', block=True))
    def post(self, request):
        if redis_client.get('site:psn_outage'):
            return Response(
                {'error': 'PSN is currently unavailable. Try again later.'},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
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

            response_data = {
                "success": True,
                "code": profile.verification_code,
                "message": f"Add '{profile.verification_code}' to your PSN 'About Me' section and run the /verify command!",
            }
            if not created and not profile.psn_history_public:
                response_data["privacy_warning"] = (
                    "This PSN profile currently has its privacy settings enabled."
                    " Please set your gaming history to public before verifying."
                )
            return Response(response_data)
        else:
            logger.error(f"Serializer errors: {serializer.errors}")
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
    
class VerifyView(APIView):
    permission_classes = [IsAuthenticated, IsDiscordBot]

    @method_decorator(ratelimit(key='user', rate=bot_exempt_rate('3/m'), method='POST', block=True))
    def post(self, request):
        if redis_client.get('site:psn_outage'):
            return Response(
                {'error': 'PSN is currently unavailable. Try again later.'},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        serializer = VerifySerializer(data=request.data)
        if serializer.is_valid():
            discord_id = serializer.validated_data['discord_id']
            psn_username = serializer.validated_data['psn_username'].lower()
            if not all([discord_id, psn_username]):
                return Response({'error': 'discord_id and psn_username required.'}, status=status.HTTP_400_BAD_REQUEST)

            try:
                profile = Profile.objects.get(psn_username=psn_username)

                start_time = timezone.now()
                timeout_seconds = 15
                poll_interval_seconds = 1

                VerificationService.start_verification_sync(profile)

                while (timezone.now() - start_time).total_seconds() < timeout_seconds:
                    profile.refresh_from_db()
                    if profile.last_synced > start_time:
                        logger.info(f"Sync completed for profile {profile.id} after polling.")
                        break
                    if profile.sync_status == 'error':
                        # A fresh error: start_verification_sync cleared any older one. On a full
                        # refresh of a still-private profile the privacy job can land before the
                        # summary sync moves last_synced, so say which problem it is.
                        logger.warning(f"Sync error for profile {profile.id} during verify.")
                        if not profile.psn_history_public:
                            return Response({'success': False, 'message': PRIVATE_HISTORY_MESSAGE}, status=status.HTTP_403_FORBIDDEN)
                        return Response(
                            {'success': False, 'message': 'Sync failed. Please try again later.'},
                            status=status.HTTP_502_BAD_GATEWAY,
                        )
                    time.sleep(poll_interval_seconds)

                if profile.last_synced <= start_time:
                    logger.warning(f"Sync timeout for profile {profile.id} after {timeout_seconds}s.")
                    return Response({'success': False, 'message': 'Sync timed out. Try again later.'}, status=status.HTTP_408_REQUEST_TIMEOUT)

                # Privacy is decided in the same save that moves last_synced (the PSN profile summary
                # shows trophy level 0 when Gaming History is private), so this reads what THIS
                # attempt's sync saw. It used to be checked inside the loop instead, where the
                # sync-finished break usually beat it (a private profile linked with no trophies to
                # sync) and where, on a retry, it read the flag the previous private attempt left.
                if not profile.psn_history_public:
                    logger.info(f"Verify refused for profile {profile.id}: Gaming History is private.")
                    return Response({'success': False, 'message': PRIVATE_HISTORY_MESSAGE}, status=status.HTTP_403_FORBIDDEN)

                if profile.verify_code(profile.about_me):
                    profile.link_discord(discord_id)
                    # Group-badge evaluation on link: `notify=True` is safe here because `awarded` is a
                    # TRANSITION. A hunter who has already synced holds their badges, so this awards
                    # nothing and announces nothing. It only speaks up when the link genuinely surfaces
                    # badges for the first time, and then as ONE consolidated message.
                    #
                    # Guarded, like the milestone recompute below it. The Discord link has ALREADY
                    # committed by this point, so an exception here would 500 an account that is in fact
                    # linked -- and skip the milestone recompute and role push that follow. The legacy
                    # `initial_badge_check` was effectively non-raising (per-badge try/except inside), so
                    # the repoint quietly removed insulation the call site never had to provide.
                    try:
                        from trophies.services.badge_apply import evaluate_and_apply
                        evaluate_and_apply(profile, notify=True)
                    except Exception:
                        logger.exception(f"[verify] badge evaluation failed for {psn_username}")
                    # New milestones app: compute + grant the ladder roles they've already earned on link.
                    # Community members (site account OR the Discord link just made) earn milestones -- see
                    # milestones.services.member_q. Guarded so a hiccup never fails an otherwise-good link.
                    from milestones.services import is_member, recompute_milestones
                    if is_member(profile):
                        try:
                            recompute_milestones(profile, reconcile_discord=True)
                        except Exception:
                            logger.exception(f"[verify] milestone recompute failed for {psn_username}")
                    # Guarded like the two calls above: the link has committed, so a failed role push
                    # must not turn it into an error (the user would be linked on the site, refused a
                    # retry as already linked, and never get the Verified role). /sync-roles
                    # reconciles any role this misses.
                    try:
                        sync_discord_roles(profile)
                    except Exception:
                        logger.exception(f"[verify] Discord role sync failed for {psn_username}")
                    return Response({'success': True, 'message': 'Verified and linked successfully!'})
                else:
                    return Response({'success': False, 'message': 'Verification failed. Check code and try again.'})
            except Profile.DoesNotExist:
                return Response({'error': 'Profile not found.'}, status=status.HTTP_404_NOT_FOUND)
            except DiscordAlreadyLinkedError as e:
                return Response({'success': False, 'message': str(e)}, status=status.HTTP_409_CONFLICT)
            except Exception as e:
                logger.exception(f"Verify error for {psn_username}: {e}")
                return Response({'error': 'Internal error.'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        else:
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

class CheckLinkedView(APIView):
    permission_classes = [IsAuthenticated, IsDiscordBot]

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
            logger.exception(f"Check linked error: {e}")
            return Response({'error': 'Internal error.'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

class UnlinkView(APIView):
    permission_classes = [IsAuthenticated, IsDiscordBot]

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
            logger.exception(f"Unlink error: {e}")
            return Response({'error': 'Internal error.'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

class SyncRolesView(APIView):
    """
    Sync all earned Discord roles for a verified user.

    Called by the bot on first verification and via the /sync-roles slash command.
    Assigns badge roles, milestone roles, and premium roles. The bot's /assign-role
    endpoint is idempotent, so re-assigning existing roles is harmless.
    """
    permission_classes = [IsAuthenticated, IsDiscordBot]

    @method_decorator(ratelimit(key='user', rate=bot_exempt_rate('5/m'), method='POST', block=True))
    def post(self, request):
        discord_id = request.data.get('discord_id')
        if not discord_id:
            return Response({'error': 'discord_id required.'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            profile = Profile.objects.select_related('user').get(discord_id=discord_id)
        except Profile.DoesNotExist:
            return Response({'error': 'No linked profile found.'}, status=status.HTTP_404_NOT_FOUND)

        if not profile.is_discord_verified:
            return Response({'error': 'Profile is not Discord verified.'}, status=status.HTTP_400_BAD_REQUEST)

        from trophies.services.discord_roles import sync_discord_roles
        role_counts = sync_discord_roles(profile)

        # New milestones app roles (highest earned per ladder) -- reconcile alongside the legacy sync so
        # /sync-roles re-pushes them too. Community members (site account OR verified Discord); idempotent +
        # self-healing; guarded so it never fails the sync.
        from milestones.services import is_member, reconcile_discord_roles
        if is_member(profile):
            try:
                reconcile_discord_roles(profile)
            except Exception:
                logger.exception(f"[sync-roles] milestone reconcile failed for discord_id={discord_id}")

        total = sum(role_counts.values())
        return Response({
            'success': True,
            'roles_synced': total,
            'breakdown': role_counts,
            'psn_username': profile.display_psn_username,
        })


class RecheckBadgesView(APIView):
    """
    Recheck all badges for a verified user.

    Called by the bot via the /recheck-badges slash command. Runs a full badge
    evaluation against all of the user's games and reports any changes.
    """
    permission_classes = [IsAuthenticated, IsDiscordBot]

    @method_decorator(ratelimit(key='user', rate=bot_exempt_rate('5/m'), method='POST', block=True))
    def post(self, request):
        discord_id = request.data.get('discord_id')
        if not discord_id:
            return Response({'error': 'discord_id required.'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            profile = Profile.objects.select_related('user').get(discord_id=discord_id)
        except Profile.DoesNotExist:
            return Response({'error': 'No linked profile found.'}, status=status.HTTP_404_NOT_FOUND)

        if not profile.is_discord_verified:
            return Response({'error': 'Profile is not Discord verified.'}, status=status.HTTP_400_BAD_REQUEST)

        from trophies.models import GroupBadge
        from trophies.services.badge_apply import evaluate_and_apply

        live_badges = list(
            GroupBadge.objects.filter(is_live=True).select_related('series', 'platform_group')
        )

        try:
            # No notify: this is a user-triggered reconcile, and the bot reports the deltas in its own
            # reply. Announcing as well would double-message the recheck.
            result = evaluate_and_apply(profile, group_badges=live_badges, notify=False)
        except Exception:
            logger.exception(f"Badge recheck failed for {profile.psn_username}")
            return Response({'error': 'Badge recheck failed.'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        # `evaluate_and_apply` returns the deltas directly, so the old before/after UserBadge snapshots
        # are gone along with the two queries they cost.
        names = {gb.id: str(gb) for gb in live_badges}
        return Response({
            'success': True,
            'badges_checked': len(live_badges),
            'awarded': [names.get(bid, f'Badge #{bid}') for bid in result['awarded']],
            'revoked': [names.get(bid, f'Badge #{bid}') for bid in result['revoked']],
            'psn_username': profile.display_psn_username,
        })


class RefreshView(APIView):
    permission_classes = [IsAuthenticated, IsDiscordBot]

    @method_decorator(ratelimit(key='user', rate=bot_exempt_rate('5/m'), method='POST', block=True))
    def post(self, request):
        discord_id = request.data.get('discord_id')
        admin_override = request.data.get('admin_override', False)

        if not discord_id:
            return Response({'error': 'discord_id required.'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            profile = Profile.objects.get(discord_id=discord_id)
            is_syncing = profile.attempt_sync()
            if is_syncing or (admin_override or not profile.psn_history_public):
                if not is_syncing:
                    PSNManager.profile_refresh(profile)

                start_time = timezone.now()
                timeout_seconds = 15
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

                # Sync timed out
                return Response({
                    'linked': True,
                    'success': False,
                    'message': 'Sync timed out. Please try again shortly.'
                }, status=status.HTTP_408_REQUEST_TIMEOUT)

            else:
                total_seconds = profile.get_seconds_to_next_sync()
                minutes = math.ceil(total_seconds / 60)
                return Response({'linked': True, 'success': False, 'message': f"Too many profile refresh requests! Please try again in: {int(minutes)} minutes"})
        except Profile.DoesNotExist:
            return Response({'linked': False, 'message': 'No linked profile found.'})
        except Exception as e:
            logger.exception(f"Refresh error: {e}")
            return Response({'error': 'Internal error.'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

class SummaryView(APIView):
    permission_classes = [IsAuthenticated, IsDiscordBot]

    def get(self, request):
        discord_id = request.query_params.get('discord_id')
        logger.debug(f"Profile request for {discord_id}")
        if not discord_id:
            return Response({'error': 'discord_id required.'}, status=status.HTTP_400_BAD_REQUEST)
        
        try:
            profile = Profile.objects.get(discord_id=discord_id)
            serializer = ProfileSerializer(profile)
            return Response({'linked': True, 'profile': serializer.data})
        except Profile.DoesNotExist:
            return Response({'linked': False, 'message': 'No linked PSN profile found.'})
        except Exception as e:
            logger.exception(f"Trophies fetch error: {e}")
            return Response({'error': 'Internal error.'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

class TrophyCaseView(APIView):
    permission_classes = [IsAuthenticated, IsDiscordBot]

    def get(self, request):
        discord_id = request.query_params.get('discord_id')
        page = safe_int(request.query_params.get('page', 1), 1)
        per_page = safe_int(request.query_params.get('per_page', 10), 10)
        if not discord_id:
            return Response({'error': 'discord_id required.'}, status=status.HTTP_400_BAD_REQUEST)
        
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
            logger.exception(f"Trophy case error: {e}")
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
            logger.exception(f"Comment edit error: {e}")
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
            logger.exception(f"Comment delete error: {e}")
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
            logger.exception(f"Comment vote error: {e}")
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
            logger.exception(f"Comment report error: {e}")
            return Response({'error': 'Internal error.'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class AgreeToGuidelinesView(APIView):
    """
    API endpoint for users to agree to community guidelines.

    POST /api/v1/guidelines/agree/
    """
    authentication_classes = [SessionAuthentication, TokenAuthentication]
    permission_classes = [IsAuthenticated]

    def post(self, request):
        """Record user's agreement to community guidelines."""
        try:
            if not hasattr(request.user, 'profile'):
                return Response(
                    {'error': 'Profile not found.'},
                    status=status.HTTP_404_NOT_FOUND
                )
            profile = request.user.profile

            if not profile.is_linked:
                return Response(
                    {'error': 'You must link your PSN account first.'},
                    status=status.HTTP_400_BAD_REQUEST
                )

            if profile.guidelines_agreed:
                return Response(
                    {'success': True, 'message': 'You have already agreed to the guidelines.'},
                    status=status.HTTP_200_OK
                )

            # Record agreement
            from django.utils import timezone
            profile.guidelines_agreed = True
            profile.guidelines_agreed_at = timezone.now()
            profile.save(update_fields=['guidelines_agreed', 'guidelines_agreed_at'])

            logger.info(f"User {profile.psn_username} agreed to community guidelines")

            return Response({
                'success': True,
                'message': 'Thank you for agreeing to our community guidelines!'
            })

        except Exception as e:
            logger.exception(f"Guidelines agreement error: {e}")
            return Response(
                {'error': 'Internal error.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
