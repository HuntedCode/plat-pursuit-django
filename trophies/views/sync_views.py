import logging

from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import JsonResponse
from django.utils.decorators import method_decorator
from django.views.generic import View
from django_ratelimit.decorators import ratelimit

from trophies.psn_manager import PSNManager
from ..models import Profile

logger = logging.getLogger("psn_api")


class ProfileSyncStatusView(LoginRequiredMixin, View):
    """
    AJAX endpoint for polling profile sync status in navigation hotbar.

    Returns current sync status, progress percentage, and cooldown time.
    Used by frontend to display sync progress bar and enable/disable sync button.

    Rate limited to 60 requests per minute per user.
    """
    @method_decorator(ratelimit(key='user', rate='60/m', method='GET'))
    def get(self, request):
        profile = request.user.profile
        seconds_to_next_sync = profile.get_seconds_to_next_sync()
        logger.debug(f"Sync status check for {profile.psn_username}: {seconds_to_next_sync}s until next sync")
        data = {
            'sync_status': profile.sync_status,
            'sync_progress': profile.sync_progress_value,
            'sync_target': profile.sync_progress_target,
            'sync_percentage': profile.sync_percentage,
            'seconds_to_next_sync': seconds_to_next_sync,
        }
        return JsonResponse(data)

class TriggerSyncView(LoginRequiredMixin, View):
    """
    AJAX endpoint to manually trigger profile sync from navigation hotbar.

    Validates cooldown period and initiates sync via job queue.
    Returns error if sync is already in progress or cooldown is active.
    """
    def post(self, request):
        profile = request.user.profile
        if not profile:
            return JsonResponse({'error': 'No linked profile'}, status=400)

        is_syncing = profile.attempt_sync()
        if not is_syncing:
            seconds_left = profile.get_seconds_to_next_sync()
            return JsonResponse({'error': f'Cooldown active: {seconds_left} seconds left'}, status=429)
        return JsonResponse({'success': True, 'message': 'Sync started'})

class SearchSyncProfileView(View):
    """
    AJAX endpoint to search for and add PSN profiles to the database.

    Creates profile if it doesn't exist and initiates initial sync.
    If profile exists, triggers a sync update.
    Used by admin/moderator tools for adding new profiles.
    """
    def post(self, request):
        psn_username = request.POST.get('psn_username')
        if not psn_username:
            return JsonResponse({'error': 'Username required'}, status=400)

        is_new = False
        try:
            profile = Profile.objects.get(psn_username__iexact=psn_username)
        except Profile.DoesNotExist:
            profile = Profile.objects.create(
                psn_username=psn_username.lower(),
                view_count=0
            )
            is_new = True

        if is_new:
            PSNManager.initial_sync(profile)
        else:
            profile.attempt_sync()
        return JsonResponse({
            'success': True,
            'message': f"{'Added and syncing' if is_new else 'Syncing'} {psn_username}",
            'psn_username': profile.psn_username,
        })

class AddSyncStatusView(View):
    """
    AJAX endpoint to poll sync status after adding a new profile.

    Returns sync status, account ID, and profile URL.
    Used by admin/moderator tools to track sync progress after adding profiles.
    """
    def get(self, request):
        psn_username = request.GET.get('psn_username')
        if not psn_username:
            return JsonResponse({'error': 'Username required'}, status=400)

        try:
            profile = Profile.objects.get(psn_username__iexact=psn_username)
        except Profile.DoesNotExist:
            data = {
                'sync_status': 'error',
                'account_id': '',
            }
            return JsonResponse(data)

        data = {
            'sync_status': profile.sync_status,
            'account_id': profile.account_id,
            'psn_username': profile.psn_username,
            'slug': f"/profiles/{profile.psn_username}/",
        }
        return JsonResponse(data)
