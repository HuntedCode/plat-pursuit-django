"""
User-facing views for Monthly Recap feature.
"""
import calendar
import json
from django.contrib.auth.mixins import LoginRequiredMixin
from django.views.generic import TemplateView
from django.http import Http404
from django.utils import timezone
from django.shortcuts import redirect

from core.services.tracking import track_site_event
from trophies.services.monthly_recap_service import MonthlyRecapService
from trophies.mixins import ProfileHotbarMixin, RecapSyncGateMixin
from trophies.themes import get_available_themes_for_grid


def _get_user_local_now(request):
    """Get current time in the authenticated user's timezone."""
    import pytz
    now = timezone.now()
    if request.user.is_authenticated:
        try:
            return now.astimezone(pytz.timezone(request.user.user_timezone or 'UTC'))
        except pytz.exceptions.UnknownTimeZoneError:
            pass
    return now


def _get_most_recent_completed_month(now_local):
    """
    Get the (year, month) tuple for the most recent completed month.

    Matches RecapIndexView logic (lines 45-60): The previous calendar month is
    always considered the "featured" recap for non-premium users.

    Args:
        now_local: datetime in user's local timezone

    Returns:
        tuple: (year, month)
    """
    # Previous month is always the featured/accessible recap
    if now_local.month == 1:
        return (now_local.year - 1, 12)
    else:
        return (now_local.year, now_local.month - 1)


class RecapIndexView(LoginRequiredMixin, RecapSyncGateMixin, ProfileHotbarMixin, TemplateView):
    """
    Recap index page - redirects to most recent completed month or shows month picker.
    """
    template_name = 'recap/recap_index.html'

    def get(self, request, *args, **kwargs):
        gate = self._get_sync_gate_response(request)
        if gate:
            return gate
        profile = request.user.profile
        now_local = _get_user_local_now(request)

        # Default to the most recent fully completed month
        # A month is "complete" after the 2nd day of the following month
        # This gives time for final syncs and makes the recap more interesting
        if now_local.day <= 2:
            # We're in the first 2 days of the month, show previous month's recap
            if now_local.month == 1:
                target_year = now_local.year - 1
                target_month = 12
            else:
                target_year = now_local.year
                target_month = now_local.month - 1
        else:
            # After the 2nd, show the previous month (not current month-in-progress)
            if now_local.month == 1:
                target_year = now_local.year - 1
                target_month = 12
            else:
                target_year = now_local.year
                target_month = now_local.month - 1

        # Try to get the target month's recap
        recap = MonthlyRecapService.get_or_generate_recap(
            profile, target_year, target_month
        )

        if recap:
            # Redirect to the completed month recap
            return redirect('recap_view', year=target_year, month=target_month)

        # No recap for target month - try current month as fallback
        current_recap = MonthlyRecapService.get_or_generate_recap(
            profile, now_local.year, now_local.month
        )

        if current_recap:
            return redirect('recap_view', year=now_local.year, month=now_local.month)

        # No activity at all - show index with available months
        return super().get(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        profile = self.request.user.profile

        # Get available months
        available_months = MonthlyRecapService.get_available_months(
            profile,
            include_premium_only=profile.user_is_premium
        )

        context['available_months'] = available_months
        context['is_premium'] = profile.user_is_premium
        context['no_activity'] = len(available_months) == 0

        return context


class RecapSlideView(LoginRequiredMixin, RecapSyncGateMixin, ProfileHotbarMixin, TemplateView):
    """
    Main recap slide presentation view.
    """
    template_name = 'recap/monthly_recap.html'

    def get(self, request, year, month, *args, **kwargs):
        gate = self._get_sync_gate_response(request)
        if gate:
            return gate
        profile = request.user.profile
        now_local = _get_user_local_now(request)

        # Validate month
        if not 1 <= month <= 12:
            raise Http404("Invalid month")

        # Block access to current month (in-progress) for all users
        # Recaps are only for completed months
        is_current_month = (year == now_local.year and month == now_local.month)
        if is_current_month:
            raise Http404("Cannot view recap for current month (in-progress)")

        # Check premium gating for past months
        # Non-premium users can access the most recent completed month only
        # Anything older requires premium
        recent_year, recent_month = _get_most_recent_completed_month(now_local)
        is_recent = (year == recent_year and month == recent_month)  # Most recent completed month only

        if not is_recent and not profile.user_is_premium:
            # Trying to access older month without premium
            return redirect('recap_index')

        # Don't allow future months
        if (year > now_local.year) or (year == now_local.year and month > now_local.month):
            raise Http404("Cannot view recap for future months")

        return super().get(request, *args, year=year, month=month, **kwargs)

    def get_context_data(self, year, month, **kwargs):
        context = super().get_context_data(**kwargs)
        profile = self.request.user.profile
        now_local = _get_user_local_now(self.request)

        # Get or generate the recap
        recap = MonthlyRecapService.get_or_generate_recap(profile, year, month)

        if not recap:
            context['no_activity'] = True
            context['year'] = year
            context['month'] = month
            context['month_name'] = calendar.month_name[month]
            context['recap_json'] = json.dumps({'slides': []})
            return context

        # Track page view
        track_site_event('recap_page_view', f"{year}-{month:02d}", self.request)

        # Build slides response
        slides = MonthlyRecapService.build_slides_response(recap)

        # Build the full recap data for JS
        recap_data = {
            'year': recap.year,
            'month': recap.month,
            'month_name': calendar.month_name[recap.month],
            'username': profile.display_psn_username or profile.psn_username,
            'avatar_url': profile.avatar_url or '',
            'is_finalized': recap.is_finalized,
            'slides': slides,
        }

        context['year'] = year
        context['month'] = month
        context['month_name'] = calendar.month_name[month]
        context['recap_json'] = json.dumps(recap_data)
        context['is_premium'] = profile.user_is_premium

        # Get available months for calendar month selector
        calendar_data = MonthlyRecapService.get_available_months_by_year(
            profile,
            include_premium_only=profile.user_is_premium
        )
        # JSON-encode the years data to ensure proper boolean conversion for JavaScript
        calendar_data['years_json'] = json.dumps(calendar_data['years'])
        context['calendar_data'] = calendar_data

        # Get available months for bottom month picker (backward compatibility)
        is_current_month = (year == now_local.year and month == now_local.month)
        available_months = MonthlyRecapService.get_available_months(
            profile,
            include_premium_only=profile.user_is_premium
        )
        context['available_months'] = available_months
        context['is_current_month'] = is_current_month

        # Add available themes for color grid modal (no game art for recaps)
        context['available_themes'] = get_available_themes_for_grid(include_game_art=False)

        return context
