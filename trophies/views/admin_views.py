import json
import logging

from django.contrib import messages
from django.db import transaction
from django.urls import reverse_lazy
from django.views.generic import TemplateView
from django.views.generic.edit import FormView

from trophies.mixins import StaffRequiredMixin
from ..forms import BadgeSeriesCreationForm
from trophies.util_modules.cache import redis_client

logger = logging.getLogger("psn_api")


#: The worker job queues, in priority order (workers `brpop` them highest-first).
#:
#: A module constant because the Admin Hub watches the same queues for its backlog number, and a hub
#: quietly watching four of five is worse than a hub watching none: it would report "all clear" while
#: one queue was drowning. One list, two readers.
WORKER_QUEUES = [
    'orchestrator_jobs',
    'high_priority_jobs',
    'medium_priority_jobs',
    'low_priority_jobs',
    'bulk_priority_jobs',
]


class TokenMonitoringView(StaffRequiredMixin, TemplateView):
    """
    Admin dashboard for monitoring PSN API token usage and sync worker machines.

    Displays:
    - Token usage statistics per worker machine
    - Queue depth and processing rates
    - Profile sync queue statistics
    - Error rates and health metrics

    Restricted to staff members only.
    """
    template_name = 'trophies/token_monitoring.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        try:
            aggregated_stats = self.get_aggregated_stats()
            context['machines'] = aggregated_stats
            context['queue_stats'] = self.get_queue_stats()
            context['profile_queue_stats'] = self.get_profile_queue_stats()
        except Exception as e:
            logger.exception("Error fetching aggregated stats for monitoring")
            context['machines'] = {}
            context['queue_stats'] = {}
            context['profile_queue_stats'] = {}
            context['error'] = "Unable to load stats. Check logs for details."
        return context

    def get_aggregated_stats(self):
        aggregated = {}
        keys = list(redis_client.scan_iter(match="token_keeper_latest_stats:*"))
        for key in keys:
            stats_json = redis_client.get(key)
            if stats_json:
                try:
                    stats = json.loads(stats_json)
                    machine_id = stats['machine_id']
                    group_id = stats.get('group_id', 'default')
                    if machine_id not in aggregated:
                        aggregated[machine_id] = {}
                    if group_id not in aggregated[machine_id]:
                        aggregated[machine_id][group_id] = {}
                    aggregated[machine_id][group_id]['instances'] = stats['instances']
                except json.JSONDecodeError:
                    logger.error(f"Invalid JSON in Redis key {key}")
        return aggregated

    def get_queue_stats(self):
        stats = {}
        for queue in WORKER_QUEUES:
            try:
                length = redis_client.llen(queue)
                stats[queue] = length
            except Exception as e:
                logger.error(f"Error fetching length for queue {queue}: {e}")
                stats[queue] = 'Error'
        return stats

    def get_profile_queue_stats(self):
        stats = {}
        queues = ['low_priority', 'medium_priority', 'bulk_priority']
        for queue in queues:
            keys = redis_client.keys(f"profile_jobs:*:{queue}")
            for key in keys:
                profile_id = key.decode().split(':')[1]
                count = int(redis_client.get(key) or 0)
                if profile_id not in stats:
                    stats[profile_id] = {}
                stats[profile_id][queue] = count
        for profile_id in stats:
            stats[profile_id]['total'] = sum(stats[profile_id].values())
        return stats


class BadgeSeriesCreationView(StaffRequiredMixin, FormView):
    """Staff tool: author a badge series and its editions in one submit.

    Restored and rebuilt in 2026-08. The pre-cutover version created four legacy tier `Badge` rows; it was
    deleted in 5b on the reasoning that Django admin covers the new models. It does, but authoring one
    series there is a seven-page-load click-path with three raw-ID popup lookups, which is not the same
    thing as covering it.

    Scope is deliberately series + editions. Stages stay in `StageAdmin`, which already has the concept
    autocomplete and the bundle-overlap validation this page would otherwise have to reimplement.
    """
    template_name = 'trophies/staff/badge_series_create.html'
    form_class = BadgeSeriesCreationForm
    success_url = reverse_lazy('badge_creation')

    def form_valid(self, form):
        from ..models import BadgeSeries, GroupBadge

        data = form.cleaned_data
        editions = list(data['editions'])

        try:
            # One transaction: a series whose editions half-failed is worse than no series, because the
            # slug is then taken and the author has to clean up before retrying.
            with transaction.atomic():
                series = BadgeSeries.objects.create(
                    series_slug=data['series_slug'],
                    name=data['name'],
                    badge_type=data['badge_type'],
                    completion_policy=data['completion_policy'],
                    min_required=data.get('min_required') or 0,
                    description=data.get('description') or '',
                    display_series=data.get('display_series') or '',
                    submitted_by=data.get('submitted_by'),
                )
                for group in editions:
                    GroupBadge.objects.get_or_create(
                        series=series, platform_group=group,
                        defaults={'is_live': data.get('start_live', False)},
                    )
        except Exception:
            logger.exception("Badge series creation failed for slug %s", data.get('series_slug'))
            messages.error(self.request, 'Could not create the series. Check the logs.')
            return self.form_invalid(form)

        edition_names = ', '.join(g.name for g in editions)
        state = 'live' if data.get('start_live') else 'hidden'
        messages.success(
            self.request,
            f'Created "{series.name}" ({series.series_slug}) with {len(editions)} '
            f'{"edition" if len(editions) == 1 else "editions"}: {edition_names} -- {state}. '
            f'Add its stages in the admin next.',
        )
        return super().form_valid(form)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        from ..models import BadgeSeries, Stage

        # Recently authored series, so the page doubles as "did that work, and what did I just make".
        ctx['recent_series'] = (
            BadgeSeries.objects.order_by('-created_at')
            .prefetch_related('group_badges__platform_group')[:8]
        )
        # Slugs that already have stages but no series: the useful case (stages authored first) AND the
        # typo case look identical until you see them listed.
        series_slugs = set(BadgeSeries.objects.values_list('series_slug', flat=True))
        stage_slugs = set(Stage.objects.values_list('series_slug', flat=True).distinct())
        ctx['orphan_stage_slugs'] = sorted(s for s in stage_slugs - series_slugs if s)
        return ctx
