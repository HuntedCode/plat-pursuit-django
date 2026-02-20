import logging

from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import JsonResponse
from django.utils.decorators import method_decorator
from django.views.generic import View
from django_ratelimit.decorators import ratelimit

from trophies.psn_manager import PSNManager
from trophies.util_modules.cache import redis_client
from ..models import Profile

logger = logging.getLogger("psn_api")


def _get_queue_position(profile_id):
    """
    Calculate approximate queue position for a syncing profile.

    Counts how many other profiles started syncing before this one and are
    still active. Uses a Redis pipeline to batch lookups into a single
    round-trip. Returns None if position cannot be determined.
    """
    try:
        my_start = redis_client.get(f"sync_started_at:{profile_id}")
        if not my_start:
            return None

        my_start_time = float(my_start.decode() if isinstance(my_start, bytes) else my_start)
        active_ids = redis_client.smembers('active_profiles')

        other_pids = []
        for pid_bytes in active_ids:
            pid = pid_bytes.decode() if isinstance(pid_bytes, bytes) else str(pid_bytes)
            if str(pid) != str(profile_id):
                other_pids.append(pid)

        if not other_pids:
            return 0

        pipe = redis_client.pipeline(transaction=False)
        for pid in other_pids:
            pipe.get(f"sync_started_at:{pid}")
        results = pipe.execute()

        ahead = 0
        for val in results:
            if not val:
                continue
            try:
                other_time = float(val.decode() if isinstance(val, bytes) else val)
                if other_time < my_start_time:
                    ahead += 1
            except (ValueError, TypeError):
                continue

        return ahead
    except Exception:
        return None


class ProfileSyncStatusView(LoginRequiredMixin, View):
    """
    AJAX endpoint for polling profile sync status in navigation hotbar.

    Returns current sync status, progress percentage, cooldown time,
    and queue position when syncing.

    Rate limited to 60 requests per minute per user.
    """
    @method_decorator(ratelimit(key='user', rate='60/m', method='GET'))
    def get(self, request):
        if not hasattr(request.user, 'profile'):
            return JsonResponse({'error': 'No linked profile'}, status=400)

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

        if profile.sync_status == 'syncing':
            data['queue_position'] = _get_queue_position(profile.id)

        return JsonResponse(data)

class TriggerSyncView(LoginRequiredMixin, View):
    """
    AJAX endpoint to manually trigger profile sync from navigation hotbar.

    Validates cooldown period and initiates sync via job queue.
    Returns error if sync is already in progress or cooldown is active.
    """
    def post(self, request):
        if not hasattr(request.user, 'profile'):
            return JsonResponse({'error': 'No linked profile'}, status=400)

        profile = request.user.profile
        is_syncing = profile.attempt_sync()
        if not is_syncing:
            seconds_left = profile.get_seconds_to_next_sync()
            return JsonResponse({'error': f'Cooldown active: {seconds_left} seconds left'}, status=429)
        return JsonResponse({'success': True, 'message': 'Sync started'})

class SearchSyncProfileView(LoginRequiredMixin, View):
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

class AddSyncStatusView(LoginRequiredMixin, View):
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
