"""
Dashboard API views for lazy-loading modules and saving user config.
"""
import json
import logging

from django.http import JsonResponse
from django.template.loader import render_to_string
from django.views import View

from trophies.mixins import StaffRequiredAPIMixin

from trophies.models import DashboardConfig
from trophies.services.dashboard_service import (
    get_module_by_slug,
    get_lazy_module_data,
    get_valid_slugs,
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
        is_premium = profile.user_is_premium

        mod = get_module_by_slug(slug)
        if not mod:
            return JsonResponse({'error': 'Unknown module.'}, status=404)

        if mod['requires_premium'] and not is_premium:
            return JsonResponse({'error': 'Premium required.'}, status=403)

        if mod['load_strategy'] != 'lazy':
            return JsonResponse({'error': 'Module is not lazy-loaded.'}, status=400)

        data = get_lazy_module_data(profile, slug)
        if data is None:
            return JsonResponse({'error': 'Failed to load module data.'}, status=500)

        # Resolve effective size: prefer client-provided query param to avoid DB hit
        allowed = mod.get('allowed_sizes', list(VALID_SIZES))
        size_param = request.GET.get('size')
        effective_size = size_param if size_param in allowed else mod.get('default_size', 'medium')

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

    def post(self, request):
        profile = request.user.profile
        is_premium = profile.user_is_premium

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

    def post(self, request):
        profile = request.user.profile

        if not profile.user_is_premium:
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
