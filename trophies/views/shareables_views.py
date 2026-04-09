"""
Shareables views.

Houses ``MyShareablesView``, the personal landing page for share-image
generation. The page lets a user pick any platinum trophy they've earned
and generate a share image, plus surfaces a CTA into the Platinum Grid
wizard for grouped/themed share images.

Historically this view lived in ``checklist_views.py`` because the legacy
checklist system included a "share your platinums" feature. The Phase 10a
URL audit promoted the page into the Dashboard hub at ``/dashboard/shareables/``
and pulled the view into its own module to break the (now-misleading)
checklist association.
"""
import logging

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import redirect
from django.urls import reverse_lazy
from django.utils import timezone
from django.views.generic import TemplateView

from core.services.tracking import track_page_view
from trophies.mixins import ProfileHotbarMixin
from trophies.models import EarnedTrophy
from trophies.themes import get_available_themes_for_grid

logger = logging.getLogger(__name__)


class MyShareablesView(LoginRequiredMixin, ProfileHotbarMixin, TemplateView):
    """
    My Shareables hub - centralized page for all shareable content.

    Allows users to generate share images for any platinum trophy they've
    earned, not just those that triggered a notification. Designed for
    extensibility to support future shareable types (trophy cabinet,
    calendar, etc.).

    Shows platinum trophies grouped by year with "Share" buttons. Requires
    a linked PSN account; users without one are redirected to the PSN
    linking flow.
    """
    template_name = 'shareables/my_shareables.html'
    login_url = reverse_lazy('account_login')

    def dispatch(self, request, *args, **kwargs):
        """Require linked PSN account."""
        if request.user.is_authenticated:
            profile = getattr(request.user, 'profile', None)
            if not profile or not profile.is_linked:
                messages.info(request, "Link your PSN account to create shareables.")
                return redirect('link_psn')
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        profile = user.profile if hasattr(user, 'profile') else None

        if not profile:
            context['platinums_by_year'] = {}
            context['total_platinums'] = 0
            return context

        # Get user's platinum trophies (including shovelware - filtered client-side
        # via the toggle in the page header)
        earned_platinums = EarnedTrophy.objects.filter(
            profile=profile,
            earned=True,
            trophy__trophy_type='platinum',
        ).select_related(
            'trophy__game',
            'trophy__game__concept',
        ).order_by('-earned_date_time')

        # Calculate platinum number for each trophy (for milestone display).
        # Since the queryset is ordered newest-first, the newest plat is
        # #total_count and the oldest is #1.
        platinum_list = list(earned_platinums)
        total_count = len(platinum_list)
        for idx, et in enumerate(platinum_list):
            et.platinum_number = total_count - idx
            et.is_milestone = et.platinum_number % 10 == 0 and et.platinum_number > 0
            et.is_shovelware = et.trophy.game.is_shovelware

        # Count shovelware so the toggle can show "X hidden" affordance
        shovelware_count = sum(1 for et in platinum_list if et.trophy.game.is_shovelware)

        # Group by year (using user's local timezone) for organization
        user_tz = timezone.get_current_timezone()
        platinums_by_year: dict = {}
        for et in platinum_list:
            if et.earned_date_time:
                local_dt = et.earned_date_time.astimezone(user_tz)
                year = local_dt.year
            else:
                year = 'Unknown'
            platinums_by_year.setdefault(year, []).append(et)

        # Sort years descending, with 'Unknown' at the end
        sorted_years = sorted(
            (y for y in platinums_by_year if y != 'Unknown'),
            reverse=True,
        )
        if 'Unknown' in platinums_by_year:
            sorted_years.append('Unknown')

        context['platinums_by_year'] = {year: platinums_by_year[year] for year in sorted_years}
        context['total_platinums'] = total_count
        context['shovelware_count'] = shovelware_count

        # Themes for the color-grid modal (include game art for the platinum cards)
        context['available_themes'] = get_available_themes_for_grid(
            include_game_art=True,
            grouped=True,
        )

        # Breadcrumbs
        context['breadcrumb'] = [
            {'text': 'Home', 'url': reverse_lazy('home')},
            {'text': 'My Shareables'},
        ]

        track_page_view('my_shareables', 'user', self.request)
        return context
