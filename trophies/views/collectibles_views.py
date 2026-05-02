"""
Collectibles views: public reader page for a game's collectibles guide.

One Collectibles page per Concept (opt-in). Public viewers see only
published pages; staff preview is deferred until the editor lands in
Phase 2. This module is the read-side surface only — editor + lock + API
endpoints come in subsequent phases.
"""
import logging

from django.http import Http404
from django.views.generic import DetailView

from trophies.mixins import ProfileHotbarMixin
from trophies.models import Game
from trophies.services.collectibles_service import CollectiblesService

logger = logging.getLogger('psn_api')


class CollectiblesDetailView(ProfileHotbarMixin, DetailView):
    """Public collectibles page for a game's concept.

    URL: `/games/<np_id>/collectibles/`. Renders nothing if the concept
    has no published Collectibles — 404 in that case (avoids advertising
    "this page exists for every game" when most games won't have one).
    Per-type sub-URLs (`/collectibles/<type>/`) come in Phase 3 along
    with the reader UX layer.
    """
    model = Game
    template_name = 'trophies/collectibles_detail.html'
    slug_field = 'np_communication_id'
    slug_url_kwarg = 'np_communication_id'

    def get_queryset(self):
        return super().get_queryset().select_related('concept')

    def get_object(self, queryset=None):
        game = super().get_object(queryset)
        if not game.concept:
            raise Http404("Game has no concept.")
        return game

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        game = self.object

        collectibles = CollectiblesService.get_collectibles_for_display(game.concept)
        if collectibles is None:
            raise Http404("No published collectibles guide available.")

        # Per-user found state (empty for anonymous viewers — reader-side
        # JS layers in localStorage on top once Phase 3 ships).
        profile = (
            self.request.user.profile if self.request.user.is_authenticated and hasattr(self.request.user, 'profile')
            else None
        )
        found_ids = CollectiblesService.get_user_progress(profile, collectibles)
        progress = CollectiblesService.compute_progress_summary(collectibles, found_ids)

        context['game'] = game
        context['collectibles'] = collectibles
        context['found_ids'] = found_ids
        context['progress'] = progress
        context['breadcrumb'] = [
            {'text': 'Games', 'url': '/games/'},
            {'text': game.title_name, 'url': f'/games/{game.np_communication_id}/'},
            {'text': 'Collectibles', 'url': None},
        ]
        return context
