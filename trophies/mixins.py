from django.utils import timezone
from django.views.generic import View
from datetime import timedelta

class ProfileHotbarMixin(View):
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        if self.request.user.is_authenticated and hasattr(self.request.user, 'profile'):
            profile = self.request.user.profile
            next_sync = profile.last_synced + timedelta(hours=1)
            if next_sync > timezone.now():
                seconds_to_next_sync = (next_sync - timezone.now()).total_seconds
            else:
                seconds_to_next_sync = 0

            hotbar_data = {
                'active': True,
                'profile': profile,
                'sync_status': profile.sync_status,
                'sync_progress': profile.sync_progress_value,
                'sync_target': profile.sync_progress_target,
                'sync_percentage': profile.sync_progress_value / profile.sync_progress_target * 100 if profile.sync_progress_target > 0 else 0,
                'seconds_to_next_sync': seconds_to_next_sync,
            }
            context['hotbar'] = hotbar_data
        return context