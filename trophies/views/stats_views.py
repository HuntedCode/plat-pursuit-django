"""Stats page view - /stats/ dedicated stats page."""
from django.shortcuts import redirect
from django.urls import reverse
from django.views.generic import TemplateView

from trophies.mixins import StaffRequiredMixin
from trophies.services.stats_service import (
    get_career_overview,
    get_teaser_records,
)


class MyStatsView(StaffRequiredMixin, TemplateView):
    """Personal stats page at /stats/.

    HIDDEN FOR 1.0 (2026-08): re-gated to staff. The page is being renovated into an upgraded tool
    (see docs/design/stats-page.md + the Data Intelligence arc), and shipping the current version at
    launch would set the wrong bar. Staff keep access so the rebuild can be worked on in place;
    everyone else is redirected home by StaffRequiredMixin, and the nav/footer entries are gone, so
    nobody lands here by accident.

    It was public to all logged-in users between Phase 9 of the Community Hub initiative and this
    change. Users without a linked profile are still redirected to the PSN linking flow.
    """
    template_name = 'trophies/my_stats.html'

    def get(self, request, *args, **kwargs):
        # Runs only after StaffRequiredMixin has admitted the viewer, so a non-staff user without a
        # linked profile is bounced home (the hide) rather than into the PSN linking flow.
        if not hasattr(request.user, 'profile'):
            return redirect('link_psn')
        return super().get(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        profile = self.request.user.profile
        is_premium = profile.user_is_premium

        # Career Overview always computed (free section, 0 queries)
        context['career'] = get_career_overview(profile)

        # Free users: compute teaser records server-side (cheap queries)
        # Premium users: stats loaded via AJAX after page shell renders
        if not is_premium:
            context['teaser_records'] = get_teaser_records(profile)

        context['is_premium'] = is_premium
        context['breadcrumb'] = [
            {'text': 'Home', 'url': reverse('home')},
            {'text': 'My Stats'},
        ]
        return context
