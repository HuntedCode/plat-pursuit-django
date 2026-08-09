"""
Shareables views.

Houses the My Shareables surface.

As of 2026-08 this serves **plat cards only** -- the one place a hunter gets a share card for a
completion. Platinum Grid and Profile Card are retired (their views are parked, their URLs bounce to
the landing); Monthly Recap keeps its own home at `/recap/`. See docs/features/share-images.md.

- Plat Cards (`/shareables/platinums/`) -- browse your completions and generate a card for any of
  them. Eligibility is the DEFAULT trophy group at 100%, so 100%-with-no-platinum games qualify too.

History: this page began as a single browse-all-platinums interface, was split into a landing +
four sub-pages by the Phase 10b restructure, and has now been narrowed back to its one job.
"""
import logging
from collections import defaultdict

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import F
from django.shortcuts import redirect
from django.urls import reverse_lazy
from django.utils import timezone
from django.views.generic import TemplateView

from core.services.tracking import track_page_view
from trophies.models import EarnedTrophy
from trophies.themes import get_available_themes_for_grid, get_plat_card_themes

logger = logging.getLogger(__name__)


class _RequireLinkedProfileMixin:
    """Mixin: redirect to the PSN linking flow when the viewer has no linked profile.

    Shared across all shareables sub-pages so each one enforces the same
    "you need a profile to make share images" gating without duplicating
    the dispatch override.
    """

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            profile = getattr(request.user, 'profile', None)
            if not profile or not profile.is_linked:
                messages.info(request, "Link your PSN account to create shareables.")
                return redirect('link_psn')
        return super().dispatch(request, *args, **kwargs)


class MyShareablesView(LoginRequiredMixin, _RequireLinkedProfileMixin, TemplateView):
    """
    My Shareables landing page at `/dashboard/shareables/`.

    A wayfinder grid that distributes users to the dedicated sub-pages
    for each share image type. Each card has an icon, name, tagline,
    example image (or fallback gradient), and a CTA to its sub-page.

    The landing itself queries no per-user data — it's purely a static
    layout of cards. The sub-pages do the heavy lifting. This keeps
    the landing fast and means new users with no platinums yet still
    see a useful "here's what's available" page instead of an empty
    state for each share type.
    """
    template_name = 'shareables/landing.html'
    login_url = reverse_lazy('account_login')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['breadcrumb'] = [
            {'text': 'Home', 'url': reverse_lazy('home')},
            {'text': 'My Shareables'},
        ]
        track_page_view('my_shareables', 'user', self.request)
        return context


class MyPlatinumSharesView(LoginRequiredMixin, _RequireLinkedProfileMixin, TemplateView):
    """
    Platinum share images browse page at `/dashboard/shareables/platinums/`.

    Lists every platinum trophy the user has earned, grouped by year,
    with click-to-share buttons that open the share-image modal. This
    is the experience that used to be the My Shareables page itself
    before the landing-page restructure; the queryset, milestone-numbering,
    shovelware filtering, and year grouping all carried over unchanged.
    """
    template_name = 'shareables/platinums.html'
    login_url = reverse_lazy('account_login')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        profile = user.profile if hasattr(user, 'profile') else None

        if not profile:
            context['platinums_by_year'] = {}
            context['total_platinums'] = 0
            return context

        # Get user's platinum trophies (including shovelware - filtered client-side
        # via the toggle in the page header). `nulls_last=True` puts NULL-date
        # platinums (rare; PSN occasionally returns no timestamp for very old
        # or hidden trophies) at the END of the listing so the newest-by-date
        # plat reliably gets the highest ordinal (`#total_count`). The
        # share-card count in ShareableDataService.get_platinum_share_data is
        # the coupled pair to this ordering: flipping one without the other
        # silently desyncs the listing's ordinal from the rendered card. The
        # `-id` secondary sort breaks ties (PSN sometimes returns identical
        # timestamps for trophies popped the same second).
        earned_platinums = EarnedTrophy.objects.filter(
            profile=profile,
            earned=True,
            trophy__trophy_type='platinum',
        ).select_related(
            'trophy__game',
            'trophy__game__concept',
            'trophy__game__concept__igdb_match',
        ).order_by(F('earned_date_time').desc(nulls_last=True), '-id')

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

        # The CURATED plat-card set, not all ~105 site gradients. The card only renders the six keys in
        # PLAT_CARD_THEME_KEYS and silently falls back to the house ground for anything else, so passing
        # the full grid meant a hunter picked "Sakura", watched the preview turn pink, and downloaded a
        # dark grey card -- every time, for 99 of the 105 options, with no error. Meanwhile the six that
        # DO work weren't offered at all.
        context['available_themes'] = [
            ('plat_card', 'Card Styles', get_plat_card_themes()),
        ]

        # Breadcrumbs
        context['breadcrumb'] = [
            {'text': 'Home', 'url': reverse_lazy('home')},
            {'text': 'My Shareables', 'url': reverse_lazy('my_shareables')},
            {'text': 'Platinum Cards'},
        ]

        track_page_view('my_shareables', 'user', self.request)
        return context


class MyProfileCardView(LoginRequiredMixin, _RequireLinkedProfileMixin, TemplateView):
    """Profile card builder page.

    RETIRED (2026-08): PARKED, not routed. My Shareables now serves plat cards only -- see
    docs/features/share-images.md. /shareables/profile-card/ bounces to the shareables landing;
    this class is kept so the surface can be revived under the new card design instead of rebuilt
    from nothing.

    Was at `/dashboard/shareables/profile-card/`.

    Dedicated page for generating share images of the user's trophy
    profile (landscape, portrait, and tab variants). Loads card HTML
    via the existing `/api/v1/profile-card/html/` endpoint and the
    existing `static/js/profile-card-share.js` controller — this
    page is the long-form home for what the dashboard `profile_card_preview`
    module already shows in compact form.

    Pulls the user's current ProfileCardSettings (theme + public sig
    toggle) so the page can render the correct theme on first load
    without an extra round-trip.
    """
    template_name = 'shareables/profile_card.html'
    login_url = reverse_lazy('account_login')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        profile = user.profile if hasattr(user, 'profile') else None

        if not profile:
            return context

        from trophies.models import ProfileCardSettings

        card_settings, _ = ProfileCardSettings.objects.get_or_create(profile=profile)
        is_premium = profile.user_is_premium

        context['card_theme'] = card_settings.card_theme or 'default'
        context['is_premium'] = is_premium
        context['available_themes'] = get_available_themes_for_grid(
            include_game_art=False,
            grouped=True,
        )

        context['breadcrumb'] = [
            {'text': 'Home', 'url': reverse_lazy('home')},
            {'text': 'My Shareables', 'url': reverse_lazy('my_shareables')},
            {'text': 'Profile Card'},
        ]

        track_page_view('my_shareables', 'user', self.request)
        return context
