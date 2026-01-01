from django.views.generic import View

class ProfileHotbarMixin(View):
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        if self.request.user.is_authenticated and hasattr(self.request.user, 'profile'):
            profile = self.request.user.profile
            seconds_to_next_sync = profile.get_seconds_to_next_sync()
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