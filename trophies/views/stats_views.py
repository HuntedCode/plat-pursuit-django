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
    """Personal stats page. PARKED (not routed) for the 1.0 launch.

    /stats/ now answers with a temporary redirect to Home (see plat_pursuit/urls.py) so bookmarks and
    the old /my-stats/ + /tools/stats/ + /dashboard/stats/ paths land somewhere useful. This view is
    kept, unrouted, because the page is coming back as an upgraded tool rather than being deleted:
    see docs/design/stats-page.md + the Data Intelligence arc. Shipping the current 120+-stat dump at
    launch, next to Career and Milestones, would set the wrong bar.

    It was public to all logged-in users between Phase 9 of the Community Hub initiative and this
    change. The staff gate stays on the class so re-routing it during the rebuild can't accidentally
    expose the old page; swap it for LoginRequiredMixin at relaunch.
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
