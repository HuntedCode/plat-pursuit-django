"""
Review Hub views.

The hub itself was archived 2026-05 and its landing and detail views deleted 2026-10.
ReviewsArchivedView: the notice served at the hub's old URL.
RateMyGamesView: Wizard for quickly rating/reviewing platinumed games.
"""
import logging

from django.contrib.auth.mixins import LoginRequiredMixin
from django.urls import reverse
from django.views.generic import TemplateView

from trophies.services.review_hub_service import ReviewHubService

logger = logging.getLogger('psn_api')


class ReviewsArchivedView(TemplateView):
    """Notice page shown where the Review Hub used to live.

    The text-review system was retired in 2026-05 after a data-handling
    bug during the IGDB-anchoring migration caused some reviews to be
    lost. Rather than continue on a compromised system we archived
    reviews (kept dormant for a possible future rebuild) and kept the
    simpler ratings. This page is served at the former review-hub URLs
    so bookmarks / links land on an honest explanation instead of a 404.
    """

    template_name = 'trophies/reviews_archived.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # No Community crumb: that hub was retired 2026-08 and this notice is a tombstone with no
        # section to sit under.
        context['breadcrumb'] = [
            {'text': 'Home', 'url': reverse('home')},
            {'text': 'Reviews'},
        ]
        context['seo_description'] = (
            "PlatPursuit's community reviews are temporarily retired. "
            "Game ratings remain available."
        )
        return context


class RateMyGamesView(LoginRequiredMixin, TemplateView):
    """Wizard for quickly rating finished games, one at a time.

    The page ships as an empty frame: the queue itself comes from
    ``WizardQueueView`` (``/api/v1/ratings/wizard/queue/``) so it can page
    and prefetch. All this needs to render is the header's four counts and
    the guidelines flag the shared rating form reads.
    """

    template_name = 'trophies/rate_my_games.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        # Home -> page, matching the other My Pursuit tools (Plat Cards, Collection): the personal hub is
        # rooted at Home, so there is no intermediate crumb to sit under.
        context['breadcrumb'] = [
            {'text': 'Home', 'url': reverse('home')},
            {'text': 'Rate My Games'},
        ]

        profile = getattr(self.request.user, 'profile', None)
        # One call for all four numbers: each of the old per-count helpers re-derived the ratable set
        # from scratch, and the DLC one did it with a query pair per group.
        progress = (
            ReviewHubService.get_rating_progress(profile) if profile
            else {'games_total': 0, 'games_waiting': 0, 'dlc_total': 0, 'dlc_waiting': 0}
        )
        context['rating_progress'] = progress
        # The wizard's JS still reads these two by their old names.
        context['unrated_count'] = progress['games_waiting']
        context['unrated_dlc_count'] = progress['dlc_waiting']
        context['guidelines_agreed'] = bool(profile and profile.guidelines_agreed)

        return context
