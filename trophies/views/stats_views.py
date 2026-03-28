"""Stats page view - /my-stats/ dedicated stats page."""
from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import redirect
from django.urls import reverse
from django.views.generic import TemplateView

from trophies.mixins import StaffRequiredMixin, ProfileHotbarMixin
from trophies.services.dashboard_service import get_effective_premium
from trophies.services.stats_service import (
    get_career_overview,
    get_teaser_records,
)


class MyStatsView(StaffRequiredMixin, ProfileHotbarMixin, TemplateView):
    template_name = 'trophies/my_stats.html'

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and not hasattr(request.user, 'profile'):
            return redirect('link_psn')
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        profile = self.request.user.profile
        is_premium = get_effective_premium(self.request)

        # Career Overview always computed (free section, 0 queries)
        context['career'] = get_career_overview(profile)

        # Free users: compute teaser records server-side (cheap queries)
        # Premium users: stats loaded via AJAX after page shell renders
        if not is_premium:
            context['teaser_records'] = get_teaser_records(profile)

        context['is_premium'] = is_premium
        context['preview_mode'] = self.request.session.get('dashboard_preview_premium') is not None
        context['real_is_premium'] = profile.user_is_premium
        context['breadcrumb'] = [
            {'text': 'Home', 'url': reverse('home')},
            {'text': 'My Stats'},
        ]
        return context
