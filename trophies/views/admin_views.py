import json
import logging
from collections import defaultdict
from datetime import timedelta

from django.contrib import messages
from django.db.models import Count, Q
from django.db.models.functions import Lower
from django.http import JsonResponse
from django.shortcuts import redirect, get_object_or_404
from django.urls import reverse_lazy
from django.utils import timezone
from django.views import View
from django.views.generic import DetailView, ListView, TemplateView
from django.views.generic.edit import FormView

from trophies.mixins import (
    HtmxListMixin, StaffOrRoadmapAuthorRequiredMixin, StaffRequiredMixin,
)
from trophies.services.psn_api_service import PsnApiService
from ..models import (
    Checklist, ChecklistItem, ChecklistSection,
    CommentReport, GameFamily, ModerationLog,
    ReviewModerationLog, ReviewReport, Trophy,
)
from ..forms import BadgeCreationForm
from ..services.review_service import ReviewService
from trophies.util_modules.cache import redis_client

logger = logging.getLogger("psn_api")


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
        queues = ['orchestrator_jobs', 'high_priority_jobs', 'medium_priority_jobs', 'low_priority_jobs', 'bulk_priority_jobs']
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


class BadgeCreationView(StaffRequiredMixin, FormView):
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
            # Resolve submitted_by PSN username to Profile if provided
            submitted_by_username = badge_data.pop('submitted_by_username', '')
            if submitted_by_username:
                from trophies.models import Profile
                try:
                    profile = Profile.objects.get(psn_username__iexact=submitted_by_username)
                    badge_data['submitted_by'] = profile
                except Profile.DoesNotExist:
                    messages.error(self.request, f'Profile "{submitted_by_username}" not found.')
                    return self.form_invalid(form)
            PsnApiService.create_badge_group_from_form(badge_data)
            messages.success(self.request, 'Badge group created successfully!')
        except Exception as e:
            logger.exception("Error creating badge")
            messages.error(self.request, 'Error creating badge. Check logs.')
            return self.form_invalid(form)
        return super().form_valid(form)


class CommentModerationView(StaffRequiredMixin, ListView):
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


class ModerationActionView(StaffRequiredMixin, View):
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


class ModerationLogView(StaffRequiredMixin, ListView):
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


class ReviewModerationView(StaffRequiredMixin, ListView):
    """Staff-only review moderation dashboard."""
    model = ReviewReport
    template_name = 'trophies/moderation/review_moderation.html'
    context_object_name = 'reports'
    paginate_by = 20

    def get_queryset(self):
        queryset = ReviewReport.objects.select_related(
            'review',
            'review__profile',
            'review__concept',
            'review__concept_trophy_group',
            'reporter',
            'reviewed_by'
        ).prefetch_related(
            'review__reports'
        )

        status_filter = self.request.GET.get('status', 'pending')
        if status_filter != 'all':
            queryset = queryset.filter(status=status_filter)

        reason_filter = self.request.GET.get('reason')
        if reason_filter:
            queryset = queryset.filter(reason=reason_filter)

        search_query = self.request.GET.get('search')
        if search_query:
            queryset = queryset.filter(
                Q(review__body__icontains=search_query) |
                Q(reporter__psn_username__icontains=search_query) |
                Q(details__icontains=search_query)
            )

        return queryset.order_by('-created_at')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        status_counts = {
            row['status']: row['count']
            for row in ReviewReport.objects.values('status').annotate(count=Count('id'))
        }
        context['pending_count'] = status_counts.get('pending', 0)
        context['reviewed_count'] = status_counts.get('reviewed', 0)
        context['dismissed_count'] = status_counts.get('dismissed', 0)
        context['action_taken_count'] = status_counts.get('action_taken', 0)

        context['current_status'] = self.request.GET.get('status', 'pending')
        context['current_reason'] = self.request.GET.get('reason', '')
        context['search_query'] = self.request.GET.get('search', '')
        context['reason_choices'] = ReviewReport.REPORT_REASONS

        context['recent_actions'] = ReviewModerationLog.objects.select_related(
            'moderator',
            'review_author'
        ).order_by('-timestamp')[:10]

        return context


class ReviewModerationActionView(StaffRequiredMixin, View):
    """Handle review moderation actions (delete, dismiss, review)."""

    def post(self, request, report_id):
        report = get_object_or_404(
            ReviewReport.objects.select_related(
                'review', 'review__profile', 'review__concept',
                'review__concept_trophy_group',
            ),
            id=report_id
        )

        action = request.POST.get('action')
        reason = request.POST.get('reason', '')
        internal_notes = request.POST.get('internal_notes', '')
        review = report.review

        if action == 'delete':
            if review.is_deleted:
                messages.info(request, "Review was already deleted. Report marked as action taken.")
            else:
                ReviewService.delete_review(
                    review, profile=None, is_admin=True,
                    moderator=request.user, reason=reason,
                    request=request, internal_notes=internal_notes,
                )
                messages.success(request, "Review deleted and logged.")

            # Update this report
            report.status = 'action_taken'
            report.reviewed_by = request.user
            report.reviewed_at = timezone.now()
            report.admin_notes = internal_notes
            report.save(update_fields=['status', 'reviewed_by', 'reviewed_at', 'admin_notes'])

            # Auto-resolve sibling reports for the same review
            ReviewReport.objects.filter(
                review=review, status='pending'
            ).exclude(id=report.id).update(
                status='action_taken',
                reviewed_by=request.user,
                reviewed_at=timezone.now(),
                admin_notes=f"Auto-resolved: review deleted via report #{report.id}",
            )

        elif action == 'dismiss':
            ReviewModerationLog.objects.create(
                moderator=request.user,
                action_type='dismiss_report',
                review=review,
                review_id_snapshot=review.id,
                review_author=review.profile,
                original_body=review.body,
                concept=review.concept,
                related_report=report,
                reason=reason,
                internal_notes=internal_notes,
            )

            report.status = 'dismissed'
            report.reviewed_by = request.user
            report.reviewed_at = timezone.now()
            report.admin_notes = internal_notes
            report.save(update_fields=['status', 'reviewed_by', 'reviewed_at', 'admin_notes'])

            messages.success(request, "Report dismissed and logged.")

        elif action == 'review':
            ReviewModerationLog.objects.create(
                moderator=request.user,
                action_type='report_reviewed',
                review=review,
                review_id_snapshot=review.id,
                review_author=review.profile,
                original_body=review.body,
                concept=review.concept,
                related_report=report,
                reason=reason,
                internal_notes=internal_notes,
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
            return redirect('review_moderation')

        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return JsonResponse({'success': True, 'action': action})

        return redirect('review_moderation')


class ReviewModerationLogView(StaffRequiredMixin, ListView):
    """View complete review moderation action history."""
    model = ReviewModerationLog
    template_name = 'trophies/moderation/review_moderation_log.html'
    context_object_name = 'logs'
    paginate_by = 50

    def get_queryset(self):
        queryset = ReviewModerationLog.objects.select_related(
            'moderator',
            'review_author',
            'concept',
            'related_report'
        )

        moderator_filter = self.request.GET.get('moderator')
        if moderator_filter:
            queryset = queryset.filter(moderator_id=moderator_filter)

        action_filter = self.request.GET.get('action_type')
        if action_filter:
            queryset = queryset.filter(action_type=action_filter)

        author_filter = self.request.GET.get('author')
        if author_filter:
            queryset = queryset.filter(review_author_id=author_filter)

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

        context['action_type_choices'] = ReviewModerationLog.ACTION_TYPES
        context['moderators'] = CustomUser.objects.filter(
            is_staff=True
        ).order_by('username')

        context['current_moderator'] = self.request.GET.get('moderator', '')
        context['current_action_type'] = self.request.GET.get('action_type', '')
        context['current_author'] = self.request.GET.get('author', '')
        context['date_from'] = self.request.GET.get('date_from', '')
        context['date_to'] = self.request.GET.get('date_to', '')

        now = timezone.now()
        stats = ReviewModerationLog.objects.aggregate(
            total_actions=Count('id'),
            actions_today=Count('id', filter=Q(timestamp__gte=now.date())),
            actions_this_week=Count('id', filter=Q(timestamp__gte=now - timedelta(days=7))),
        )
        context.update(stats)

        return context


class GameFamilyManagementView(StaffRequiredMixin, TemplateView):
    """Staff-only dashboard for managing GameFamily records.

    Post Phase 2.6, families are primarily created by the IGDB enrichment
    pipeline (keyed on IGDB id). This view exists for admin inspection and
    manual overrides on the edge cases IGDB doesn't cover.
    """
    template_name = 'trophies/game_family_management.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        # Proposal workflow removed in Phase 2.6 — families are now created
        # deterministically from IGDB id. The template still references these
        # keys so they're provided as empty defaults for compatibility until
        # the template is trimmed.
        context['pending_proposals'] = []
        context['concept_icons'] = {}
        context['pending_count'] = 0

        families = list(
            GameFamily.objects.prefetch_related(
                'concepts', 'concepts__games'
            ).annotate(concept_count=Count('concepts')).order_by(Lower('canonical_name'))
        )

        context['families'] = families
        context['family_count'] = len(families)
        context['verified_count'] = sum(1 for f in families if f.is_verified)
        context['unverified_count'] = context['family_count'] - context['verified_count']

        return context


class LegacyChecklistListView(StaffOrRoadmapAuthorRequiredMixin, HtmxListMixin, ListView):
    """Read-only browser for the deprecated Checklist system.

    The Checklist UI was retired when Roadmaps replaced it but the DB tables
    were retained. This view exposes published, draft, and soft-deleted
    checklists so staff AND roadmap authors (writer / editor / publisher,
    not trial) can mine the original authored prose for reuse in Roadmaps
    without fighting the Django admin.
    """
    model = Checklist
    template_name = 'trophies/staff/legacy_checklist_list.html'
    partial_template_name = 'trophies/staff/_legacy_checklist_list_results.html'
    context_object_name = 'checklists'
    paginate_by = 25

    SORT_CHOICES = [
        ('newest', 'Newest first'),
        ('oldest', 'Oldest first'),
        ('most_upvoted', 'Most upvoted'),
        ('most_saved', 'Most saved'),
        ('most_viewed', 'Most viewed'),
        ('most_sections', 'Most sections'),
    ]

    STATUS_CHOICES = [
        ('all', 'All'),
        ('published', 'Published'),
        ('draft', 'Drafts'),
        ('deleted', 'Soft-deleted'),
    ]

    def get_queryset(self):
        # Default manager exposes everything including soft-deleted (the
        # `.active()` helper is opt-in), which is exactly what we want here.
        qs = Checklist.objects.select_related('concept', 'profile').annotate(
            section_count=Count('sections', distinct=True),
            item_count=Count('sections__items', distinct=True),
        )

        status = self.request.GET.get('status', 'all')
        if status == 'published':
            qs = qs.filter(status='published', is_deleted=False)
        elif status == 'draft':
            qs = qs.filter(status='draft', is_deleted=False)
        elif status == 'deleted':
            qs = qs.filter(is_deleted=True)

        search = (self.request.GET.get('search') or '').strip()
        if search:
            qs = qs.filter(
                Q(title__icontains=search) | Q(description__icontains=search)
            )

        author = (self.request.GET.get('author') or '').strip()
        if author:
            qs = qs.filter(profile__psn_username__icontains=author)

        concept = (self.request.GET.get('concept') or '').strip()
        if concept:
            qs = qs.filter(concept__unified_title__icontains=concept)

        sort = self.request.GET.get('sort', 'newest')
        if sort == 'oldest':
            qs = qs.order_by('created_at', 'id')
        elif sort == 'most_upvoted':
            qs = qs.order_by('-upvote_count', '-created_at')
        elif sort == 'most_saved':
            qs = qs.order_by('-progress_save_count', '-created_at')
        elif sort == 'most_viewed':
            qs = qs.order_by('-view_count', '-created_at')
        elif sort == 'most_sections':
            qs = qs.order_by('-section_count', '-created_at')
        else:
            qs = qs.order_by('-created_at', '-id')

        return qs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # Single aggregation pass for the four status tiles.
        counts = Checklist.objects.aggregate(
            count_total=Count('id'),
            count_published=Count('id', filter=Q(status='published', is_deleted=False)),
            count_draft=Count('id', filter=Q(status='draft', is_deleted=False)),
            count_deleted=Count('id', filter=Q(is_deleted=True)),
        )
        context.update(counts)
        context['current_status'] = self.request.GET.get('status', 'all')
        context['current_sort'] = self.request.GET.get('sort', 'newest')
        context['search_query'] = self.request.GET.get('search', '')
        context['author_query'] = self.request.GET.get('author', '')
        context['concept_query'] = self.request.GET.get('concept', '')
        context['status_choices'] = self.STATUS_CHOICES
        context['sort_choices'] = self.SORT_CHOICES
        return context


class LegacyChecklistDetailView(StaffOrRoadmapAuthorRequiredMixin, DetailView):
    """Read-only detail view for a single legacy Checklist.

    Renders the full checklist (title + description + thumbnail), every
    section, and every item including text_area prose blocks. Soft-deleted
    checklists are rendered with a visible banner so they can never be
    mistaken for live content. Access: staff OR writer+ roadmap role.
    """
    model = Checklist
    template_name = 'trophies/staff/legacy_checklist_detail.html'
    context_object_name = 'checklist'

    def get_queryset(self):
        return Checklist.objects.select_related(
            'concept', 'profile', 'selected_game'
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        checklist = self.object

        sections = list(
            ChecklistSection.objects.filter(checklist=checklist)
            .prefetch_related('items')
            .order_by('order', 'id')
        )

        # Build a trophy lookup map for any items that reference a specific
        # trophy. Trophies are scoped to the checklist's selected_game so we
        # can resolve them all in one query and avoid N+1 on the detail page.
        trophy_map = {}
        if checklist.selected_game_id:
            referenced_ids = {
                item.trophy_id
                for section in sections
                for item in section.items.all()
                if item.item_type == 'trophy' and item.trophy_id
            }
            if referenced_ids:
                trophy_map = {
                    t.trophy_id: t
                    for t in Trophy.objects.filter(
                        game_id=checklist.selected_game_id,
                        trophy_id__in=referenced_ids,
                    )
                }

        # Attach the resolved trophy directly to each item so the template
        # doesn't need a custom templatetag to do the lookup.
        for section in sections:
            for item in section.items.all():
                if item.item_type == 'trophy' and item.trophy_id:
                    item.resolved_trophy = trophy_map.get(item.trophy_id)
                else:
                    item.resolved_trophy = None

        # Serializable structure powering the "Copy as Markdown" button in
        # the template. Kept lean — just what the markdown builder needs.
        export_payload = {
            'title': checklist.title,
            'description': checklist.description,
            'concept': checklist.concept.unified_title if checklist.concept_id else '',
            'author': checklist.profile.psn_username if checklist.profile_id else '',
            'sections': [
                {
                    'subtitle': s.subtitle,
                    'description': s.description,
                    'items': [
                        {
                            'type': item.item_type,
                            'text': item.text or '',
                            'trophy_name': (
                                item.resolved_trophy.trophy_name
                                if item.resolved_trophy else ''
                            ),
                            'image_name': (
                                item.image.name.rsplit('/', 1)[-1]
                                if item.image else ''
                            ),
                        }
                        for item in s.items.all()
                    ],
                }
                for s in sections
            ],
        }

        context['sections'] = sections
        context['section_count'] = len(sections)
        # Use the prefetched cache (`len(s.items.all())`) rather than the
        # `total_entry_count` property, which calls `.count()` and fires a
        # fresh COUNT(*) per section.
        context['item_count'] = sum(len(s.items.all()) for s in sections)
        context['export_payload'] = export_payload
        return context
