"""
Dashboard views: the personal trophy hunting command center.

DashboardView serves the main page at /dashboard/ (staff required).
"""
from django.urls import reverse_lazy
from django.views.generic import TemplateView

from trophies.mixins import ProfileHotbarMixin, StaffRequiredMixin
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


class DashboardView(StaffRequiredMixin, ProfileHotbarMixin, TemplateView):
    """
    Personal trophy hunting dashboard with tabbed navigation.

    Modules are organized into category tabs. Only the active tab's
    lazy modules load on page init. Other tabs load when first activated.
    """
    template_name = 'trophies/dashboard.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        profile = self.request.user.profile
        is_premium = get_effective_premium(self.request)

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

        # Theme picker for premium settings module
        available_themes = []
        if is_premium:
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

        context.update({
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
            'preview_mode': self.request.session.get('dashboard_preview_premium') is not None,
            'real_is_premium': profile.user_is_premium,
            'breadcrumb': [
                {'text': 'Home', 'url': reverse_lazy('home')},
                {'text': 'My Dashboard'},
            ],
        })
        return context
