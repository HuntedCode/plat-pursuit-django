"""
Shareables views.

Houses the My Shareables landing page and its sub-pages. The landing page
at `/dashboard/shareables/` is a small wayfinder that distributes users
to the various share-image surfaces:

- Platinum Cards (`/dashboard/shareables/platinums/`) — browse every
  platinum trophy and generate a themed share image for any of them
- Platinum Grid (`/dashboard/shareables/platinum-grid/`) — multi-platinum
  collage wizard (lives in trophies/views/platinum_grid_views.py)
- Profile Card (`/dashboard/shareables/profile-card/`) — generate a
  share image showcasing your trophy profile and stats
- Monthly Recap (`/dashboard/recap/`) — Spotify-Wrapped style summary
  card for any month you've hunted (lives in trophies/recap_views.py)
- Challenge Cards (`/dashboard/shareables/challenges/`) — generate share
  images for your A-Z, Calendar, and Genre challenges

Historically the My Shareables page was a single browse-all-platinums
interface. The Phase 10b restructure split it into a landing + sub-pages
so each share type has a dedicated home and the landing serves as a
wayfinder for new users who don't know what's available.
"""
import logging
from collections import defaultdict

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import redirect
from django.urls import reverse_lazy
from django.utils import timezone
from django.views.generic import TemplateView

from core.services.tracking import track_page_view
from trophies.mixins import ProfileHotbarMixin
from trophies.models import EarnedTrophy, Challenge
from trophies.themes import get_available_themes_for_grid

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


class MyShareablesView(LoginRequiredMixin, _RequireLinkedProfileMixin, ProfileHotbarMixin, TemplateView):
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


class MyPlatinumSharesView(LoginRequiredMixin, _RequireLinkedProfileMixin, ProfileHotbarMixin, TemplateView):
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
        # via the toggle in the page header)
        earned_platinums = EarnedTrophy.objects.filter(
            profile=profile,
            earned=True,
            trophy__trophy_type='platinum',
        ).select_related(
            'trophy__game',
            'trophy__game__concept',
            'trophy__game__concept__igdb_match',
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
            {'text': 'My Shareables', 'url': reverse_lazy('my_shareables')},
            {'text': 'Platinum Cards'},
        ]

        track_page_view('my_shareables', 'user', self.request)
        return context


class MyChallengeSharesView(LoginRequiredMixin, _RequireLinkedProfileMixin, ProfileHotbarMixin, TemplateView):
    """
    Challenge share cards page at `/dashboard/shareables/challenges/`.

    Lists every challenge the user has created (A-Z, Calendar, Genre)
    grouped by type, with previews and download buttons for each. This
    is the dedicated home for challenge share cards — users no longer
    need to bounce between individual challenge detail pages to find
    each card. The existing challenge detail pages keep their inline
    share buttons too; this page is additive.

    Each challenge entry uses the same `/api/v1/challenges/<type>/<id>/
    share/html/` and `share/png/` endpoints that the dashboard's
    challenge_share_cards module and the challenge detail pages use,
    so the share-image rendering, theming, and download flows are
    shared across all surfaces.
    """
    template_name = 'shareables/challenges.html'
    login_url = reverse_lazy('account_login')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        profile = user.profile if hasattr(user, 'profile') else None

        if not profile:
            context['challenges_by_type'] = {}
            context['has_challenges'] = False
            return context

        # Pull every non-deleted challenge for this profile, ordered so
        # active challenges come before completed ones, and within each
        # group the most recent are first.
        all_challenges = (
            Challenge.objects
            .filter(profile=profile, is_deleted=False)
            .order_by('is_complete', '-created_at')
        )

        # Group by challenge_type so the template can render one section
        # per type (A-Z / Calendar / Genre)
        grouped: dict[str, list[Challenge]] = defaultdict(list)
        for ch in all_challenges:
            grouped[ch.challenge_type].append(ch)

        # Render in a stable order: A-Z first, then Calendar, then Genre.
        # Use a list of (type_key, type_label, challenges) tuples so the
        # template doesn't have to know the labels.
        TYPE_ORDER = [
            ('az', 'A-Z Platinum Challenge'),
            ('calendar', 'Platinum Calendar'),
            ('genre', 'Genre Challenge'),
        ]
        challenges_sections = [
            {
                'type_key': key,
                'type_label': label,
                'challenges': grouped.get(key, []),
            }
            for key, label in TYPE_ORDER
        ]

        context['challenges_sections'] = challenges_sections
        context['has_challenges'] = any(s['challenges'] for s in challenges_sections)

        # Theme picker grid for the shared color_grid_modal partial.
        # Challenges aren't tied to a single game, so game art themes
        # (which composite a specific game's artwork into the background)
        # are excluded — same call shape as MyProfileCardView.
        context['available_themes'] = get_available_themes_for_grid(
            include_game_art=False,
            grouped=True,
        )

        context['breadcrumb'] = [
            {'text': 'Home', 'url': reverse_lazy('home')},
            {'text': 'My Shareables', 'url': reverse_lazy('my_shareables')},
            {'text': 'Challenge Cards'},
        ]

        track_page_view('my_shareables', 'user', self.request)
        return context


class MyProfileCardView(LoginRequiredMixin, _RequireLinkedProfileMixin, ProfileHotbarMixin, TemplateView):
    """
    Profile card builder page at `/dashboard/shareables/profile-card/`.

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
