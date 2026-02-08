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


class RecapSyncGateMixin:
    """
    Returns a gated recap_index response if the user has no linked profile or
    their profile hasn't finished syncing. Add to recap views before other mixins.
    """
    def _get_sync_gate_response(self, request):
        from django.shortcuts import render as _render
        profile = getattr(request.user, 'profile', None)
        if not profile:
            return _render(request, 'recap/recap_index.html', {'sync_gate': 'no_profile'})
        if profile.sync_status != 'synced':
            return _render(request, 'recap/recap_index.html', {
                'sync_gate': profile.sync_status,
                'profile': profile,
            })
        return None


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


class BackgroundContextMixin:
    """
    Mixin for views that display page-specific game image backgrounds.

    Provides a consistent way to build the image_urls context that base.html
    uses to display background images. Page-specific backgrounds (from this mixin)
    take priority over user's premium theme (from context processor).

    Usage:
        class MyView(BackgroundContextMixin, TemplateView):
            def get_context_data(self, **kwargs):
                context = super().get_context_data(**kwargs)
                context['image_urls'] = self.get_background_context(concept=my_concept)
                return context
    """

    def get_background_context(self, concept=None):
        """
        Build image_urls dict for template context.

        Args:
            concept: A Concept model instance with bg_url field

        Returns:
            dict: Contains 'bg_url' if concept has a background, empty dict otherwise
        """
        if concept and getattr(concept, 'bg_url', None):
            return {'bg_url': concept.bg_url}
        return {}