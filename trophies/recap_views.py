"""
User-facing views for Monthly Recap feature.
"""
import calendar
import json
from django.contrib.auth.mixins import LoginRequiredMixin
from django.views.generic import TemplateView
from django.http import Http404
from django.shortcuts import redirect, render

from core.services.tracking import track_page_view, track_site_event
from trophies.services.monthly_recap_service import MonthlyRecapService
from trophies.mixins import ProfileHotbarMixin, RecapSyncGateMixin
from trophies.recap_utils import (
    get_user_local_now, get_most_recent_completed_month, check_sync_freshness,
)
from trophies.themes import get_available_themes_for_grid


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
        now_local = get_user_local_now(request)

        # Default to the most recent fully completed month (always previous month)
        target_year, target_month = get_most_recent_completed_month(now_local)

        # Check sync freshness: user must have synced within the current month
        if not check_sync_freshness(profile, now_local):
            return render(request, 'recap/recap_index.html', {
                'sync_gate': 'sync_stale',
                'profile': profile,
                'stale_month_name': calendar.month_name[target_month],
                'stale_year': target_year,
            })

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
        now_local = get_user_local_now(request)

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
        recent_year, recent_month = get_most_recent_completed_month(now_local)
        is_recent = (year == recent_year and month == recent_month)  # Most recent completed month only

        if not is_recent and not profile.user_is_premium:
            # Trying to access older month without premium
            return redirect('recap_index')

        # Check sync freshness for the most recent completed month
        if is_recent and not check_sync_freshness(profile, now_local):
            return render(request, 'recap/recap_index.html', {
                'sync_gate': 'sync_stale',
                'profile': profile,
                'stale_month_name': calendar.month_name[month],
                'stale_year': year,
            })

        # Don't allow future months
        if (year > now_local.year) or (year == now_local.year and month > now_local.month):
            raise Http404("Cannot view recap for future months")

        return super().get(request, *args, year=year, month=month, **kwargs)

    def get_context_data(self, year, month, **kwargs):
        context = super().get_context_data(**kwargs)
        profile = self.request.user.profile
        now_local = get_user_local_now(self.request)

        # Always provide base context (needed for calendar month selector on no-activity pages too)
        context['year'] = year
        context['month'] = month
        context['month_name'] = calendar.month_name[month]
        context['is_premium'] = profile.user_is_premium

        # Calendar month selector
        calendar_data = MonthlyRecapService.get_available_months_by_year(
            profile,
            include_premium_only=profile.user_is_premium
        )
        calendar_data['years_json'] = json.dumps(calendar_data['years'])
        context['calendar_data'] = calendar_data

        # Get or generate the recap
        recap = MonthlyRecapService.get_or_generate_recap(profile, year, month)

        if not recap:
            context['no_activity'] = True
            context['recap_json'] = json.dumps({'slides': []})
            return context

        # Track page view
        track_site_event('recap_page_view', f"{year}-{month:02d}", self.request)
        track_page_view('recap', f"{year}-{month:02d}", self.request)

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

        context['recap_json'] = json.dumps(recap_data)

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
