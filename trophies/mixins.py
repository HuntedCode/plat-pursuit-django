from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import redirect
from django.views.generic import View


class PremiumRequiredMixin(LoginRequiredMixin):
    """
    Mixin that requires the user to be a premium member.
    Redirects non-premium users to the beta access page.
    """
    def dispatch(self, request, *args, **kwargs):
        # First check if user is authenticated (handled by LoginRequiredMixin)
        if not request.user.is_authenticated:
            return self.handle_no_permission()

        # Check if user is premium
        if hasattr(request.user, 'profile') and request.user.profile.user_is_premium:
            return super().dispatch(request, *args, **kwargs)

        # Redirect non-premium users to beta access page
        return redirect('beta_access_required')


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