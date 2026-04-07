"""
Dashboard views: the personal trophy hunting command center.

DashboardView is the legacy direct entry point. The site's home page (/) now
routes through core.views.HomeView, which dispatches to the dashboard for
fully-synced users by calling build_dashboard_context() directly. This module
exposes that helper so HomeView can render the dashboard without inheriting
from DashboardView.
"""
from datetime import timedelta

from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.cache import cache
from django.urls import reverse_lazy
from django.utils import timezone
from django.views.generic import TemplateView

from trophies.mixins import ProfileHotbarMixin
from trophies.models import DashboardConfig
from trophies.services.dashboard_service import (
    get_dashboard_tabs,
    get_tabs_for_customize,
    get_server_module_data,
    get_effective_premium,
    get_premium_preview_html,
    VALID_TAB_ICONS,
    MAX_FREE_HIDDEN,
)


def _get_site_heartbeat():
    """Read the site heartbeat dict from cache, with previous-hour fallback."""
    now = timezone.now()
    key = f"site_heartbeat_{now.date().isoformat()}_{now.hour:02d}"
    data = cache.get(key)
    if data is None:
        prev = now - timedelta(hours=1)
        prev_key = f"site_heartbeat_{prev.date().isoformat()}_{prev.hour:02d}"
        data = cache.get(prev_key)
    return data


def build_dashboard_context(request, profile):
    """
    Build the full context dict for rendering trophies/dashboard.html.

    Extracted from DashboardView.get_context_data so the home router
    (core.views.HomeView) can render the dashboard for synced users
    without going through view inheritance.

    Args:
        request: HttpRequest (used for session-based premium preview).
        profile: Profile instance for the user being rendered.

    Returns:
        dict: full template context for trophies/dashboard.html.
    """
    is_premium = get_effective_premium(request)

    config, _ = DashboardConfig.objects.get_or_create(profile=profile)

    # Build tabbed module structure
    tabs = get_dashboard_tabs(config, is_premium)

    # Collect all modules across tabs for server-rendering
    all_modules = []
    for tab in tabs:
        all_modules.extend(tab['modules'])

    # Batch-fetch context for server-rendered modules
    server_data = get_server_module_data(profile, all_modules)

    # Tab-grouped modules for the customize panel
    customize_tabs = get_tabs_for_customize(config, is_premium)
    all_tab_options = [{'slug': t['slug'], 'name': t.get('short_name') or t['name']} for t in customize_tabs]

    # Split tabs into system (default) and custom for separate tab bars
    custom_tabs = [t for t in tabs if t.get('is_custom')]

    # Theme picker for share card modules (available to all users)
    from trophies.themes import get_available_themes_for_grid
    available_themes = get_available_themes_for_grid(include_game_art=False, grouped=True)

    # Pre-render premium module previews for free users
    preview_html = {}
    if not is_premium:
        for tab in tabs:
            for module in tab['modules']:
                if module.get('is_preview'):
                    html = get_premium_preview_html(module['slug'])
                    if html:
                        preview_html[module['slug']] = html

    return {
        'profile': profile,
        'dashboard_config': config,
        'tabs': tabs,
        'custom_tabs': custom_tabs,
        'server_module_data': server_data,
        'is_premium': is_premium,
        'available_themes': available_themes,
        'preview_html': preview_html,
        'customize_tabs': customize_tabs,
        'all_tab_options': all_tab_options,
        'valid_tab_icons': sorted(VALID_TAB_ICONS),
        'max_free_hidden': MAX_FREE_HIDDEN,
        'hidden_count': len(config.hidden_modules) if config.hidden_modules else 0,
        'displayed_title': profile.displayed_title,
        'preview_mode': request.session.get('dashboard_preview_premium') is not None,
        'real_is_premium': profile.user_is_premium,
        'site_heartbeat': _get_site_heartbeat(),
        'breadcrumb': [
            {'text': 'Home', 'url': reverse_lazy('home')},
            {'text': 'My Dashboard'},
        ],
    }


class DashboardView(LoginRequiredMixin, ProfileHotbarMixin, TemplateView):
    """
    Personal trophy hunting dashboard with tabbed navigation.

    The site's home page (/) now routes through core.views.HomeView, which
    delegates here only for fully-synced linked users. This class is kept as
    a thin wrapper so the legacy /dashboard/ URL alias and any direct callers
    keep working; all real work lives in build_dashboard_context().
    """
    template_name = 'trophies/dashboard.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(build_dashboard_context(self.request, self.request.user.profile))
        return context
