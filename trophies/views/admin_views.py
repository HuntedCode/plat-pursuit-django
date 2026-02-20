import json
import logging
from collections import defaultdict
from datetime import timedelta

from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.db.models import Count, Q
from django.db.models.functions import Lower
from django.http import JsonResponse
from django.shortcuts import redirect, get_object_or_404
from django.urls import reverse_lazy
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views import View
from django.views.generic import ListView, TemplateView
from django.views.generic.edit import FormView

from trophies.services.psn_api_service import PsnApiService
from ..models import CommentReport, GameFamily, GameFamilyProposal, ModerationLog, Trophy
from ..forms import BadgeCreationForm
from trophies.util_modules.cache import redis_client

logger = logging.getLogger("psn_api")


@method_decorator(staff_member_required, name='dispatch')
class TokenMonitoringView(TemplateView):
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
        queues = ['high_priority_jobs', 'medium_priority_jobs', 'low_priority_jobs']
        stats = {}
        for queue in queues:
            try:
                length = redis_client.llen(queue)
                stats[queue] = length
            except Exception as e:
                logger.error(f"Error fetching length for queue {queue}: {e}")
                stats[queue] = 'Error'
        return stats

    def get_profile_queue_stats(self):
        stats = {}
        queues = ['high_priority', 'medium_priority', 'low_priority']
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


@method_decorator(staff_member_required, name='dispatch')
class BadgeCreationView(FormView):
    """
    Admin tool for creating new badge series with multiple tiers.

    Provides form interface for defining:
    - Badge series metadata (name, slug, description)
    - Multiple badge tiers with requirements
    - Associated game concepts and stages
    - Badge icons and visual assets

    Restricted to staff members only.
    """
    template_name = 'trophies/badge_creation.html'
    form_class = BadgeCreationForm
    success_url = reverse_lazy('badge_creation')

    def form_valid(self, form):
        try:
            badge_data = form.get_badge_data()
            PsnApiService.create_badge_group_from_form(badge_data)
            messages.success(self.request, 'Badge group created successfully!')
        except Exception as e:
            logger.exception("Error creating badge")
            messages.error(self.request, 'Error creating badge. Check logs.')
            return self.form_invalid(form)
        return super().form_valid(form)


@method_decorator(staff_member_required, name='dispatch')
class CommentModerationView(ListView):
    """
    Staff-only comment moderation dashboard.

    Displays pending reports with full context and provides actions
    to dismiss, delete, or review reports.
    """
    model = CommentReport
    template_name = 'trophies/moderation/comment_moderation.html'
    context_object_name = 'reports'
    paginate_by = 20

    def get_queryset(self):
        """Return reports based on selected tab/filter."""
        queryset = CommentReport.objects.select_related(
            'comment',
            'comment__profile',
            'comment__concept',
            'reporter',
            'reviewed_by'
        ).prefetch_related(
            'comment__reports'  # All reports for this comment
        )

        # Filter by status (from query params)
        status_filter = self.request.GET.get('status', 'pending')
        if status_filter != 'all':
            queryset = queryset.filter(status=status_filter)

        # Filter by reason
        reason_filter = self.request.GET.get('reason')
        if reason_filter:
            queryset = queryset.filter(reason=reason_filter)

        # Search by comment text or reporter username
        search_query = self.request.GET.get('search')
        if search_query:
            queryset = queryset.filter(
                Q(comment__body__icontains=search_query) |
                Q(reporter__psn_username__icontains=search_query) |
                Q(details__icontains=search_query)
            )

        return queryset.order_by('-created_at')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        # Count by status for tabs (single aggregation query)
        status_counts = {
            row['status']: row['count']
            for row in CommentReport.objects.values('status').annotate(count=Count('id'))
        }
        context['pending_count'] = status_counts.get('pending', 0)
        context['reviewed_count'] = status_counts.get('reviewed', 0)
        context['dismissed_count'] = status_counts.get('dismissed', 0)
        context['action_taken_count'] = status_counts.get('action_taken', 0)

        # Current filters
        context['current_status'] = self.request.GET.get('status', 'pending')
        context['current_reason'] = self.request.GET.get('reason', '')
        context['search_query'] = self.request.GET.get('search', '')

        # Reason choices for filter dropdown
        context['reason_choices'] = CommentReport.REPORT_REASONS

        # Recent moderation activity (last 10 actions)
        context['recent_actions'] = ModerationLog.objects.select_related(
            'moderator',
            'comment_author'
        ).order_by('-timestamp')[:10]

        return context


@method_decorator(staff_member_required, name='dispatch')
class ModerationActionView(View):
    """
    Handle moderation actions (delete, dismiss, review).

    POST endpoint for AJAX requests from moderation dashboard.
    """

    def post(self, request, report_id):
        """Process moderation action."""
        report = get_object_or_404(
            CommentReport.objects.select_related('comment', 'comment__profile'),
            id=report_id
        )

        action = request.POST.get('action')
        reason = request.POST.get('reason', '')
        internal_notes = request.POST.get('internal_notes', '')

        if action == 'delete':
            # Soft-delete comment and log action
            report.comment.soft_delete(
                moderator=request.user,
                reason=reason,
                request=request
            )

            # Update report status
            report.status = 'action_taken'
            report.reviewed_by = request.user
            report.reviewed_at = timezone.now()
            report.admin_notes = internal_notes
            report.save(update_fields=['status', 'reviewed_by', 'reviewed_at', 'admin_notes'])

            messages.success(request, f"Comment deleted and logged. Report marked as action taken.")

        elif action == 'dismiss':
            # Dismiss report without action
            ModerationLog.objects.create(
                moderator=request.user,
                action_type='dismiss_report',
                comment=report.comment,
                comment_id_snapshot=report.comment.id,
                comment_author=report.comment.profile,
                original_body=report.comment.body,
                concept=report.comment.concept,
                trophy_id=report.comment.trophy_id,
                related_report=report,
                reason=reason,
                internal_notes=internal_notes
            )

            report.status = 'dismissed'
            report.reviewed_by = request.user
            report.reviewed_at = timezone.now()
            report.admin_notes = internal_notes
            report.save(update_fields=['status', 'reviewed_by', 'reviewed_at', 'admin_notes'])

            messages.success(request, "Report dismissed and logged.")

        elif action == 'review':
            # Mark as reviewed without action
            ModerationLog.objects.create(
                moderator=request.user,
                action_type='report_reviewed',
                comment=report.comment,
                comment_id_snapshot=report.comment.id,
                comment_author=report.comment.profile,
                original_body=report.comment.body,
                concept=report.comment.concept,
                trophy_id=report.comment.trophy_id,
                related_report=report,
                reason=reason,
                internal_notes=internal_notes
            )

            report.status = 'reviewed'
            report.reviewed_by = request.user
            report.reviewed_at = timezone.now()
            report.admin_notes = internal_notes
            report.save(update_fields=['status', 'reviewed_by', 'reviewed_at', 'admin_notes'])

            messages.info(request, "Report marked as reviewed.")

        else:
            messages.error(request, f"Unknown action: {action}")
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return JsonResponse({'success': False, 'error': f'Unknown action: {action}'})
            return redirect('comment_moderation')

        # Return JSON for AJAX or redirect for non-AJAX
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return JsonResponse({'success': True, 'action': action})

        return redirect('comment_moderation')


@method_decorator(staff_member_required, name='dispatch')
class ModerationLogView(ListView):
    """
    View complete moderation action history.

    Filterable by moderator, action type, date range.
    """
    model = ModerationLog
    template_name = 'trophies/moderation/moderation_log.html'
    context_object_name = 'logs'
    paginate_by = 50

    def get_queryset(self):
        queryset = ModerationLog.objects.select_related(
            'moderator',
            'comment_author',
            'concept',
            'related_report'
        )

        # Filter by moderator
        moderator_filter = self.request.GET.get('moderator')
        if moderator_filter:
            queryset = queryset.filter(moderator_id=moderator_filter)

        # Filter by action type
        action_filter = self.request.GET.get('action_type')
        if action_filter:
            queryset = queryset.filter(action_type=action_filter)

        # Filter by author (to see all actions against a user)
        author_filter = self.request.GET.get('author')
        if author_filter:
            queryset = queryset.filter(comment_author_id=author_filter)

        # Date range filter
        date_from = self.request.GET.get('date_from')
        date_to = self.request.GET.get('date_to')
        if date_from:
            queryset = queryset.filter(timestamp__gte=date_from)
        if date_to:
            queryset = queryset.filter(timestamp__lte=date_to)

        return queryset.order_by('-timestamp')

    def get_context_data(self, **kwargs):
        from users.models import CustomUser

        context = super().get_context_data(**kwargs)

        # Filter choices
        context['action_type_choices'] = ModerationLog.ACTION_TYPES
        context['moderators'] = CustomUser.objects.filter(
            is_staff=True
        ).order_by('username')

        # Current filters
        context['current_moderator'] = self.request.GET.get('moderator', '')
        context['current_action_type'] = self.request.GET.get('action_type', '')
        context['current_author'] = self.request.GET.get('author', '')
        context['date_from'] = self.request.GET.get('date_from', '')
        context['date_to'] = self.request.GET.get('date_to', '')

        # Stats (single query with conditional aggregation)
        now = timezone.now()
        stats = ModerationLog.objects.aggregate(
            total_actions=Count('id'),
            actions_today=Count('id', filter=Q(timestamp__gte=now.date())),
            actions_this_week=Count('id', filter=Q(timestamp__gte=now - timedelta(days=7))),
        )
        context.update(stats)

        return context


@method_decorator(staff_member_required, name='dispatch')
class GameFamilyManagementView(TemplateView):
    """Staff-only dashboard for managing GameFamily records and reviewing proposals."""
    template_name = 'trophies/game_family_management.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        proposals = list(
            GameFamilyProposal.objects.filter(
                status='pending'
            ).prefetch_related(
                'concepts', 'concepts__games'
            ).order_by('-confidence', '-created_at')
        )

        # Pre-compute trophy icons for proposal concepts in a single bulk query
        proposal_concept_ids = set()
        for p in proposals:
            for c in p.concepts.all():
                proposal_concept_ids.add(c.id)

        concept_icons = {}
        if proposal_concept_ids:
            all_icons = defaultdict(list)
            for concept_id, trophy_type, url in (
                Trophy.objects.filter(
                    game__concept_id__in=proposal_concept_ids,
                    trophy_icon_url__isnull=False,
                ).exclude(trophy_icon_url='')
                .values_list('game__concept_id', 'trophy_type', 'trophy_icon_url')
            ):
                all_icons[concept_id].append((trophy_type, url))

            for concept_id, icons in all_icons.items():
                plat = next((url for t, url in icons if t == 'platinum'), None)
                concept_icons[concept_id] = plat or icons[0][1]

        context['pending_proposals'] = proposals
        context['concept_icons'] = concept_icons

        families = list(
            GameFamily.objects.prefetch_related(
                'concepts', 'concepts__games'
            ).annotate(concept_count=Count('concepts')).order_by(Lower('canonical_name'))
        )

        context['families'] = families
        context['pending_count'] = len(proposals)
        context['family_count'] = len(families)
        context['verified_count'] = sum(1 for f in families if f.is_verified)
        context['unverified_count'] = context['family_count'] - context['verified_count']

        return context
