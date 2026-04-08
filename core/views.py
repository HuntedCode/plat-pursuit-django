import logging
import random

from django.contrib.staticfiles.finders import find
from django.http import HttpResponse
from django.shortcuts import render
from django.views.generic import TemplateView, View

from trophies.mixins import ProfileHotbarMixin
from trophies.views.dashboard_views import build_dashboard_context, _get_site_heartbeat

logger = logging.getLogger('psn_api')


# Rotating "Did You Know?" facts shown on the syncing page so repeat visits stay
# fresh. Picked at random server-side per request. Keep these tight, fun, and
# focused on PlatPursuit features the user can look forward to once their sync
# finishes.
SYNCING_DID_YOU_KNOW = [
    "Every game in your library is auto-tagged with genres, themes, and engines so you can hunt by what you actually love.",
    "PlatPursuit awards over 100 unique badge series, each with its own tiers, XP, and tracking against your real PSN history.",
    "The A-to-Z, Calendar, and Genre Challenges turn your backlog into a structured pursuit, complete with progress tracking.",
    "Your dashboard is yours: rearrange modules, hide what you don't care about, and pin the stats that matter to you.",
    "Earned a platinum? You can generate a shareable card in seconds and post it to your favorite community.",
    "Our Monthly Recap is a Spotify-Wrapped-style trip through your trophy year, including your rarest grabs and biggest sessions.",
    "Roadmaps let you plan a platinum step by step, then watch your progress fill in as you sync.",
    "The community has flagged thousands of broken, unobtainable, or misbehaving trophies so you know what you're getting into.",
    "Reviews and ratings come from people who actually completed the game, not random voters, so they're worth reading.",
    "Game Families group prequels, sequels, and remasters together so your stats reflect the whole journey.",
    "Discord linking lets PlatBot deliver new platinums, badge unlocks, and challenge updates straight to your server.",
    "Every stat on the site updates from real PSN data. No fudging, no estimates, no fake leaderboards.",
    "The Platinum Grid view lets you visualize every plat you have ever earned in one beautiful wall.",
    "Premium themes change the entire site's vibe, including the navbar, cards, and your share images.",
    "Trophy hunting is more fun with friends. Browse profiles, compare stats, and challenge each other for the top spot.",
]


class AdsTxtView(View):
    def get(self, request):
        file_path = find('ads.txt')  # Finders search all STATICFILES_DIRS
        if file_path:
            try:
                with open(file_path, 'r') as f:
                    content = f.read()
                return HttpResponse(content, content_type='text/plain')
            except Exception:
                logger.exception("Error serving ads.txt")
                return HttpResponse("ads.txt not found", status=404)
        else:
            logger.warning("ads.txt not found in static files")
            return HttpResponse("ads.txt not found", status=404)


class RobotsTxtView(View):
    def get(self, request):
        file_path = find('robots.txt')
        if file_path:
            try:
                with open(file_path, 'r') as f:
                    content = f.read()
                return HttpResponse(content, content_type='text/plain')
            except Exception as e:
                logger.error(f"Error serving robots.txt: {e}")
                return HttpResponse("robots.txt not found", status=404)
        else:
            logger.warning("robots.txt not found in static files")
            return HttpResponse("robots.txt not found", status=404)


class PrivacyPolicyView(TemplateView):
    template_name = 'pages/privacy.html'


class TermsOfServiceView(TemplateView):
    template_name = 'pages/terms.html'


class AboutView(TemplateView):
    template_name = 'pages/about.html'


class ContactView(TemplateView):
    template_name = 'pages/contact.html'


class HomeView(ProfileHotbarMixin, TemplateView):
    """
    Site home page router.

    A single entry point at / that branches the response based on user state:

    - Anonymous visitors                  -> home/landing.html (marketing pitch)
    - Logged in, no Profile               -> home/link_psn.html (link your PSN)
    - Logged in, Profile exists, !linked  -> home/link_psn.html
    - Linked, sync_status == 'syncing'    -> home/syncing.html (in-progress page)
    - Linked, sync_status == 'synced'     -> trophies/dashboard.html (full dashboard)
    - Linked, sync_status == 'error'      -> home/syncing.html (we surface the
        error in-page rather than throwing the user into a half-empty dashboard)

    The hotbar polls /api/profile-sync-status/ every 2s while syncing; the
    syncing page listens for a 'platpursuit:sync-status-changed' CustomEvent
    dispatched by hotbar.js and reloads the page when sync transitions to
    'synced', so users automatically advance to the dashboard.
    """
    # template_name is set per-state in get_template_names below.

    def get_template_names(self):
        state = self._resolve_state()
        return {
            'anonymous': 'home/landing.html',
            'no_psn':    'home/link_psn.html',
            'syncing':   'home/syncing.html',
            'synced':    'trophies/dashboard.html',
        }[state]

    def _resolve_state(self):
        """Compute the home-page state for the current request user."""
        request = self.request
        if not request.user.is_authenticated:
            return 'anonymous'
        profile = getattr(request.user, 'profile', None)
        if profile is None or not profile.is_linked:
            return 'no_psn'
        if profile.sync_status != 'synced':
            # Both 'syncing' and 'error' get the in-progress shell rather than
            # an empty dashboard. The shell shows the relevant status messaging.
            return 'syncing'
        return 'synced'

    def get_context_data(self, **kwargs):
        # ProfileHotbarMixin attaches the hotbar context for any logged-in user
        # with a profile, so all four states get a working hotbar where applicable.
        context = super().get_context_data(**kwargs)
        state = self._resolve_state()
        context['home_state'] = state

        if state == 'synced':
            # Render the dashboard exactly as DashboardView would.
            context.update(build_dashboard_context(self.request, self.request.user.profile))
            return context

        # All non-dashboard states share the cached site heartbeat for their
        # community-stats card. Reused directly so we don't recompute on render.
        context['site_heartbeat'] = _get_site_heartbeat()

        if state == 'syncing':
            context['profile'] = self.request.user.profile
            context['did_you_know'] = random.choice(SYNCING_DID_YOU_KNOW)

        return context