"""
Dashboard API views for lazy-loading modules and saving user config.
"""
import json
import logging

from django.http import JsonResponse
from django.template.loader import render_to_string
from django.utils.decorators import method_decorator
from django.views import View
from django_ratelimit.decorators import ratelimit

from trophies.mixins import StaffRequiredAPIMixin

from trophies.models import DashboardConfig
from trophies.services.dashboard_service import (
    get_module_by_slug,
    get_lazy_module_data,
    get_valid_slugs,
    get_effective_premium,
    validate_module_size,
    VALID_SIZES,
    MAX_FREE_HIDDEN,
)

logger = logging.getLogger(__name__)


class DashboardModuleDataView(StaffRequiredAPIMixin, View):
    """
    GET /api/v1/dashboard/module/<slug>/

    Returns rendered HTML for a single lazy-loaded dashboard module.
    Response: {"html": "<rendered partial>"}
    """

    def get(self, request, slug):
        profile = request.user.profile
        is_premium = get_effective_premium(request)

        mod = get_module_by_slug(slug)
        if not mod:
            return JsonResponse({'error': 'Unknown module.'}, status=404)

        if mod['requires_premium'] and not is_premium:
            return JsonResponse({'error': 'Premium required.'}, status=403)

        if mod['load_strategy'] != 'lazy':
            return JsonResponse({'error': 'Module is not lazy-loaded.'}, status=400)

        # Resolve effective size
        allowed = mod.get('allowed_sizes', list(VALID_SIZES))
        size_param = request.GET.get('size')
        effective_size = size_param if size_param in allowed else mod.get('default_size', 'medium')

        # Load user's module settings (premium only; free users get defaults)
        config, _ = DashboardConfig.objects.get_or_create(profile=profile)
        module_settings = (config.module_settings or {}) if is_premium else {}

        # Allow inline settings override via query param (for interactive controls like date range)
        import json
        inline_settings = request.GET.get('settings')
        if inline_settings:
            try:
                overrides = json.loads(inline_settings)
                if isinstance(overrides, dict):
                    per_module = module_settings.get(slug, {})
                    per_module.update(overrides)
                    module_settings = {**module_settings, slug: per_module}
            except (json.JSONDecodeError, TypeError):
                pass

        data = get_lazy_module_data(profile, slug, size=effective_size, module_settings=module_settings)
        if data is None:
            return JsonResponse({'error': 'Failed to load module data.'}, status=500)

        try:
            html = render_to_string(mod['template'], {
                'data': data,
                'effective_size': effective_size,
            }, request=request)
        except Exception:
            logger.exception("Failed to render dashboard module %s", slug)
            return JsonResponse({'error': 'Failed to render module.'}, status=500)

        return JsonResponse({'html': html})


class DashboardConfigUpdateView(StaffRequiredAPIMixin, View):
    """
    POST /api/v1/dashboard/config/

    Update the user's dashboard config (hidden modules, module settings, and optionally order).

    Body: {
        "hidden_modules": ["slug1", "slug2"],
        "module_settings": {"slug": {"key": "value"}},
        "module_order": ["slug1", "slug2"]  // optional, premium only
    }
    """

    @method_decorator(ratelimit(key='user', rate='15/m', method='POST', block=True))
    def post(self, request):
        profile = request.user.profile
        is_premium = get_effective_premium(request)

        try:
            body = json.loads(request.body)
        except (json.JSONDecodeError, ValueError):
            return JsonResponse({'error': 'Invalid JSON.'}, status=400)

        config, _ = DashboardConfig.objects.get_or_create(profile=profile)
        valid_slugs = get_valid_slugs()
        fields_to_update = ['updated_at']

        # Update hidden_modules
        if 'hidden_modules' in body:
            hidden = body['hidden_modules']
            if not isinstance(hidden, list):
                return JsonResponse({'error': 'hidden_modules must be a list.'}, status=400)

            # Validate slugs
            hidden = [s for s in hidden if s in valid_slugs]

            # Free users: enforce max hidden limit
            if not is_premium and len(hidden) > MAX_FREE_HIDDEN:
                return JsonResponse({
                    'error': f'Free users can hide up to {MAX_FREE_HIDDEN} modules.',
                    'max_hidden': MAX_FREE_HIDDEN,
                }, status=400)

            config.hidden_modules = hidden
            fields_to_update.append('hidden_modules')

        # Update module_settings
        if 'module_settings' in body:
            settings = body['module_settings']
            if not isinstance(settings, dict):
                return JsonResponse({'error': 'module_settings must be an object.'}, status=400)

            # Empty dict = reset all settings (allowed for all users)
            if not settings:
                config.module_settings = {}
            elif not is_premium:
                return JsonResponse({'error': 'Module settings require premium.'}, status=403)
            else:
                # Premium: merge with existing settings (preserves unaffected keys)
                existing = config.module_settings or {}

                for slug, val in settings.items():
                    if slug not in valid_slugs or not isinstance(val, dict):
                        continue

                    # Validate size if present
                    if 'size' in val:
                        is_valid, error_msg = validate_module_size(slug, val['size'])
                        if not is_valid:
                            return JsonResponse({'error': error_msg}, status=400)

                    if slug not in existing:
                        existing[slug] = {}
                    existing[slug].update(val)

                config.module_settings = existing
            fields_to_update.append('module_settings')

        # Update module_order (premium only, used by atomic reset)
        if 'module_order' in body and is_premium:
            order = body['module_order']
            if isinstance(order, list):
                config.module_order = [s for s in order if s in valid_slugs]
                fields_to_update.append('module_order')

        # Update tab_config
        if 'tab_config' in body:
            tab_data = body['tab_config']
            if isinstance(tab_data, dict):
                from trophies.services.dashboard_service import DEFAULT_TAB_ORDER
                existing_tabs = config.tab_config or {}

                # active_tab: all users can save their last active tab
                if 'active_tab' in tab_data and isinstance(tab_data['active_tab'], str):
                    active = tab_data['active_tab']
                    # Validate against known tabs (defaults + any custom tabs)
                    known_tabs = set(DEFAULT_TAB_ORDER)
                    known_tabs.update((existing_tabs.get('custom_tabs') or {}).keys())
                    if active in known_tabs:
                        existing_tabs['active_tab'] = active

                # Premium-only tab customizations
                if is_premium:
                    if 'tab_order' in tab_data and isinstance(tab_data['tab_order'], list):
                        existing_tabs['tab_order'] = tab_data['tab_order']
                    if 'custom_tabs' in tab_data and isinstance(tab_data['custom_tabs'], dict):
                        from trophies.services.dashboard_service import VALID_TAB_ICONS
                        import re
                        sanitized = {}
                        for k, v in tab_data['custom_tabs'].items():
                            if k in DEFAULT_TAB_ORDER or not isinstance(v, dict):
                                continue
                            # Validate slug format
                            if not re.match(r'^[a-z0-9_]+$', k) or len(k) > 50:
                                continue
                            # Validate tab name
                            name = str(v.get('name', '')).strip()
                            if not name or len(name) > 20:
                                continue
                            # Validate icon
                            icon = v.get('icon', 'star')
                            if icon not in VALID_TAB_ICONS:
                                icon = 'star'
                            sanitized[k] = {'name': name, 'icon': icon}
                        # Max 6 custom tabs
                        if len(sanitized) > 6:
                            sanitized = dict(list(sanitized.items())[:6])
                        existing_tabs['custom_tabs'] = sanitized
                    if 'module_tab_overrides' in tab_data and isinstance(tab_data['module_tab_overrides'], dict):
                        existing_tabs['module_tab_overrides'] = tab_data['module_tab_overrides']

                config.tab_config = existing_tabs
                fields_to_update.append('tab_config')

        config.save(update_fields=fields_to_update)

        return JsonResponse({
            'status': 'ok',
            'hidden_modules': config.hidden_modules,
            'module_settings': config.module_settings,
        })


class DashboardModuleReorderView(StaffRequiredAPIMixin, View):
    """
    POST /api/v1/dashboard/reorder/

    Update module display order (premium only).

    Body: {"module_order": ["slug1", "slug2", ...]}
    """

    @method_decorator(ratelimit(key='user', rate='15/m', method='POST', block=True))
    def post(self, request):
        profile = request.user.profile

        if not get_effective_premium(request):
            return JsonResponse({'error': 'Reordering requires premium.'}, status=403)

        try:
            body = json.loads(request.body)
        except (json.JSONDecodeError, ValueError):
            return JsonResponse({'error': 'Invalid JSON.'}, status=400)

        order = body.get('module_order')
        if not isinstance(order, list):
            return JsonResponse({'error': 'module_order must be a list.'}, status=400)

        # Validate all slugs
        valid_slugs = get_valid_slugs()
        order = [s for s in order if s in valid_slugs]

        config, _ = DashboardConfig.objects.get_or_create(profile=profile)
        config.module_order = order
        config.save(update_fields=['module_order', 'updated_at'])

        return JsonResponse({
            'status': 'ok',
            'module_order': config.module_order,
        })


class DashboardPreviewToggleView(StaffRequiredAPIMixin, View):
    """
    POST /api/v1/dashboard/preview-toggle/

    Toggle premium preview mode for staff testing.
    Sets a session variable that overrides is_premium on the dashboard.
    """

    @method_decorator(ratelimit(key='user', rate='15/m', method='POST', block=True))
    def post(self, request):
        current = request.session.get('dashboard_preview_premium')
        if current is None:
            # First toggle: opposite of real status
            request.session['dashboard_preview_premium'] = not request.user.profile.user_is_premium
        else:
            # Subsequent toggles: flip
            request.session['dashboard_preview_premium'] = not current
        return JsonResponse({
            'status': 'ok',
            'preview_premium': request.session['dashboard_preview_premium'],
        })


class StatsPageDataView(StaffRequiredAPIMixin, View):
    """
    GET /api/v1/stats/premium/

    Returns rendered HTML for all premium stats sections.
    Called via AJAX after the page shell loads to overlay the intro animation
    on top of actual computation time.
    """

    def get(self, request):
        from trophies.services.stats_service import get_premium_stats

        profile = request.user.profile
        is_premium = get_effective_premium(request)

        if not is_premium:
            return JsonResponse({'error': 'Premium required.'}, status=403)

        exclude_shovelware = request.GET.get('exclude_shovelware') == '1'
        exclude_hidden = request.GET.get('exclude_hidden') == '1'
        premium_stats = get_premium_stats(profile, exclude_shovelware, exclude_hidden)

        try:
            html = render_to_string(
                'trophies/partials/stats/premium_sections.html',
                {'premium_stats': premium_stats},
                request=request,
            )
        except Exception:
            logger.exception("Failed to render stats page premium sections")
            return JsonResponse({'error': 'Failed to render stats.'}, status=500)

        return JsonResponse({'html': html})
