"""
View for the Platinum Grid share image wizard page.

Staff-only during testing phase. Will be opened to all users later.
"""
import json
import logging

from django.db.models import F
from django.urls import reverse_lazy
from django.utils.safestring import mark_safe

from trophies.mixins import StaffRequiredMixin, ProfileHotbarMixin
from trophies.models import EarnedTrophy, ProfileGame
from trophies.services.dashboard_service import get_effective_premium
from trophies.themes import get_available_themes_for_grid

from django.views.generic import TemplateView

logger = logging.getLogger(__name__)


class PlatinumGridView(StaffRequiredMixin, ProfileHotbarMixin, TemplateView):
    """
    Wizard page for building a shareable platinum trophy grid image.

    Three-step wizard:
    1. Configure: icon type, sort order, filters
    2. Select: checklist of platinums to include/exclude
    3. Preview & Download: layout, theme, generate PNG

    Limits: 100 platinums (premium), 50 (free).
    """
    template_name = 'trophies/platinum_grid.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        profile = self.request.user.profile

        # Breadcrumb
        context['breadcrumb'] = [
            {'text': 'Home', 'url': reverse_lazy('home')},
            {'text': 'My Shareables', 'url': reverse_lazy('my_shareables')},
            {'text': 'Platinum Grid'},
        ]

        # Limits (respects staff preview toggle from dashboard)
        is_premium = get_effective_premium(self.request)
        max_icons = 200 if is_premium else 50
        context['max_icons'] = max_icons
        context['is_premium'] = is_premium

        # All earned platinums (sorted by most recent, the default)
        platinums = (
            EarnedTrophy.objects
            .filter(profile=profile, earned=True, trophy__trophy_type='platinum')
            .select_related('trophy', 'trophy__game', 'trophy__game__concept')
            .order_by(F('earned_date_time').desc(nulls_last=True))
        )

        # Build hidden game IDs set (for client-side filtering)
        hidden_game_ids = set(
            ProfileGame.objects
            .filter(profile=profile, user_hidden=True)
            .values_list('game_id', flat=True)
        )

        # Serialize platinums for JS
        platinum_data = []
        for et in platinums:
            trophy = et.trophy
            game = trophy.game
            concept = game.concept
            family_id = concept.family_id if concept else None
            platinum_data.append({
                'id': et.id,
                'game_name': game.title_name,
                'game_image': game.title_image or '',
                'trophy_icon': trophy.trophy_icon_url or '',
                'earned_date': et.earned_date_time.isoformat() if et.earned_date_time else '',
                'psn_earn_rate': float(trophy.trophy_earn_rate or 0),
                'is_shovelware': game.shovelware_status in ('auto_flagged', 'manually_flagged'),
                'is_hidden': game.id in hidden_game_ids,
                'family_id': family_id,
                'platforms': game.title_platform or [],
            })

        # Escape </script> sequences for safe embedding in <script> tags
        json_str = json.dumps(platinum_data).replace('</', '<\\/')
        context['platinum_data_json'] = mark_safe(json_str)
        context['total_platinums'] = len(platinum_data)

        # Themes for the theme picker (grouped by category)
        context['available_themes'] = get_available_themes_for_grid(
            include_game_art=False, grouped=True,
        )

        context['profile'] = profile

        return context
