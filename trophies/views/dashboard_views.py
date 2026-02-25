"""
Dashboard views: the personal trophy hunting command center.

DashboardView serves the main page at /dashboard/ (staff required).
"""
from django.urls import reverse_lazy
from django.views.generic import TemplateView

from trophies.mixins import ProfileHotbarMixin, StaffRequiredMixin
from trophies.models import DashboardConfig
from trophies.services.dashboard_service import (
    get_ordered_modules,
    get_all_modules_for_customize,
    get_server_module_data,
    MAX_FREE_HIDDEN,
)


class DashboardView(StaffRequiredMixin, ProfileHotbarMixin, TemplateView):
    """
    Personal trophy hunting dashboard.

    Server-renders cheap modules immediately and provides module config
    so the JS manager can lazy-load expensive modules via AJAX.
    """
    template_name = 'trophies/dashboard.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        profile = self.request.user.profile
        is_premium = profile.user_is_premium

        config, _ = DashboardConfig.objects.get_or_create(profile=profile)

        # Ordered, visibility-filtered module list for rendering
        modules = get_ordered_modules(config, is_premium)

        # Batch-fetch context for server-rendered modules (zero or minimal queries)
        server_data = get_server_module_data(profile, modules)

        # Category-grouped modules for the customize panel
        categories = get_all_modules_for_customize(config, is_premium)

        context.update({
            'profile': profile,
            'dashboard_config': config,
            'modules': modules,
            'server_module_data': server_data,
            'is_premium': is_premium,
            'module_categories': categories,
            'max_free_hidden': MAX_FREE_HIDDEN,
            'hidden_count': len(config.hidden_modules) if config.hidden_modules else 0,
            'breadcrumb': [
                {'text': 'Home', 'url': reverse_lazy('home')},
                {'text': 'My Dashboard'},
            ],
        })
        return context
