"""
User-facing views for Monthly Recap feature.
"""
import calendar
import json
from django.contrib.auth.mixins import LoginRequiredMixin
from django.urls import reverse_lazy
from django.views.generic import TemplateView
from django.http import Http404
from django.shortcuts import render

from core.services.tracking import track_page_view, track_site_event
from trophies.services.monthly_recap_service import MonthlyRecapService
from trophies.mixins import RecapSyncGateMixin
from trophies.recap_utils import (
    get_user_local_now, get_most_recent_completed_month, check_sync_freshness, MIN_RECAP_YEAR,
)
from trophies.themes import get_available_themes_for_grid


class RecapIndexView(LoginRequiredMixin, RecapSyncGateMixin, TemplateView):
    """The recap's landing page: the latest month up front, the rest of the history under it.

    Not a redirect any more. See `get` for why that made the archive unreachable from its own URL.
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
                'user_timezone': request.user.user_timezone or 'UTC',
                'breadcrumb': [
                    {'text': 'Home', 'url': reverse_lazy('home')},
                    {'text': 'Monthly Recap'},
                ],
            })

        # This used to REDIRECT to the most recent month whenever a recap existed, falling back to the
        # current one -- which meant the page at this URL only ever rendered for a hunter with no trophy
        # activity at all. The archive was unreachable from its own address, and a second month picker
        # had to live at the bottom of the recap page to compensate.
        #
        # It is a landing page now: it leads with the latest month and keeps the rest of the history
        # under it. `get_or_generate_recap` is deliberately NOT called here -- generating every month a
        # hunter merely looked at is work nobody asked for, and opening a month still generates it.
        return super().get(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        profile = self.request.user.profile

        # Every month this hunter earned a trophy in -- no gating. See months_with_activity.
        archive = MonthlyRecapService.get_archive(profile)

        context['archive'] = archive
        context['latest'] = archive['latest']
        context['no_activity'] = archive['month_count'] == 0
        tz_name = self.request.user.user_timezone or 'UTC'
        context['user_timezone'] = tz_name
        # "America/New_York" -> "New York". The header chip has room for the city but not the region on a
        # narrow screen, and a bare clock icon there says nothing at all.
        context['user_timezone_short'] = tz_name.rsplit('/', 1)[-1].replace('_', ' ')

        context['breadcrumb'] = [
            {'text': 'Home', 'url': reverse_lazy('home')},
            {'text': 'Monthly Recap'},
        ]

        return context


class RecapSlideView(LoginRequiredMixin, RecapSyncGateMixin, TemplateView):
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

        # Validate month AND year. The year floor matters as much as the month: `<int:year>` matches 0,
        # and get_month_date_range does `datetime(year, month, 1)`, which raises for year 0 -- a 500 from
        # a URL. The API gained a shared bounds helper for exactly this; the page needs the same floor.
        # MIN_RECAP_YEAR is a sanity bound, not a claim about any hunter: their real floor is their own
        # first trophy, which enforces itself (a month with no activity has no recap).
        if not 1 <= month <= 12 or year < MIN_RECAP_YEAR:
            raise Http404("Invalid year or month")

        # Block access to current month (in-progress) for all users
        # Recaps are only for completed months
        is_current_month = (year == now_local.year and month == now_local.month)
        if is_current_month:
            raise Http404("Cannot view recap for current month (in-progress)")

        # No premium gate. A recap is a record of what this hunter did; charging to look back at your
        # own history was the wrong thing to sell. Every completed month with activity is open.
        recent_year, recent_month = get_most_recent_completed_month(now_local)
        is_recent = (year == recent_year and month == recent_month)

        # Check sync freshness for the most recent completed month
        if is_recent and not check_sync_freshness(profile, now_local):
            return render(request, 'recap/recap_index.html', {
                'sync_gate': 'sync_stale',
                'profile': profile,
                'stale_month_name': calendar.month_name[month],
                'stale_year': year,
                'user_timezone': request.user.user_timezone or 'UTC',
                'breadcrumb': [
                    {'text': 'Home', 'url': reverse_lazy('home')},
                    {'text': 'Monthly Recap'},
                ],
            })

        # Don't allow future months
        if (year > now_local.year) or (year == now_local.year and month > now_local.month):
            raise Http404("Cannot view recap for future months")

        # NB: marking the recap viewed happens in get_context_data, AFTER the recap is generated. It used
        # to run here, before generation -- which meant it filtered against a row that did not exist yet
        # for any month opened for the first time, matched nothing, and left `has_been_viewed` False
        # forever. Harmless while only last month was reachable and its row was always pre-generated by
        # cron; wrong for every historic month now that the whole archive is openable.
        return super().get(request, *args, year=year, month=month, **kwargs)

    def get_context_data(self, year, month, **kwargs):
        context = super().get_context_data(**kwargs)
        profile = self.request.user.profile
        now_local = get_user_local_now(self.request)

        # Always provide base context (needed for calendar month selector on no-activity pages too)
        context['year'] = year
        context['month'] = month
        context['month_name'] = calendar.month_name[month]
        context['user_timezone'] = self.request.user.user_timezone or 'UTC'

        context['breadcrumb'] = [
            {'text': 'Home', 'url': reverse_lazy('home')},
            {'text': 'Monthly Recap', 'url': reverse_lazy('recap_index')},
            {'text': f'{calendar.month_name[month]} {year}'},
        ]

        # Calendar month selector
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

        # Mark it viewed, now that the row is guaranteed to exist. The PRE-update value is what the
        # entrance reads: on a first visit the cover should say "Begin", not "Watch it again".
        from trophies.models import MonthlyRecap
        first_visit = not recap.has_been_viewed
        if first_visit:
            MonthlyRecap.objects.filter(pk=recap.pk).update(has_been_viewed=True)
            from trophies.services.dashboard_service import invalidate_dashboard_cache
            invalidate_dashboard_cache(profile.id)

        # The entrance renders from the recap ITSELF -- its headline numbers, and whether this month has
        # been seen before -- so it can be a real cover rather than a generic "Monthly Recap" header.
        context['recap'] = recap
        context['profile'] = profile
        context['first_visit'] = first_visit

        # NOT available_months here: only recap_index.html renders that list, and computing it on this
        # page meant a SECOND whale-scale aggregate over EarnedTrophy per render, feeding a context
        # variable no template on this page reads. The calendar below is this page's month picker.
        context['is_current_month'] = (year == now_local.year and month == now_local.month)

        # Add available themes for color grid modal (no game art for recaps)
        context['available_themes'] = get_available_themes_for_grid(include_game_art=False, grouped=True)

        return context
