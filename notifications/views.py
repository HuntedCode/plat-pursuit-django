from django.views.generic import TemplateView, ListView, View
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.admin.views.decorators import staff_member_required
from django.utils.decorators import method_decorator
from django.shortcuts import redirect
from django.contrib import messages
from django.utils import timezone
from datetime import datetime

from trophies.mixins import ProfileHotbarMixin
from trophies.themes import GRADIENT_THEMES
from notifications.models import (
    NotificationTemplate, ScheduledNotification, NotificationLog
)
from notifications.services.scheduled_notification_service import ScheduledNotificationService


class NotificationInboxView(LoginRequiredMixin, ProfileHotbarMixin, TemplateView):
    """
    Gmail-style notification inbox with split-pane layout.
    Uses AJAX to load notifications via existing API endpoints.
    """
    template_name = 'notifications/inbox.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = 'Notifications'
        context['breadcrumb'] = [
            {'text': 'Home', 'url': '/'},
            {'text': 'Notifications', 'url': None}
        ]

        # Notification types for filter dropdown
        context['notification_types'] = [
            ('platinum_earned', 'Platinum Trophy'),
            ('badge_awarded', 'Badge Awarded'),
            ('milestone_achieved', 'Milestone Achieved'),
            ('subscription_created', 'Subscription Created'),
            ('subscription_updated', 'Subscription Updated'),
            ('discord_verified', 'Discord Verified'),
            ('admin_announcement', 'Admin Announcement'),
            ('system_alert', 'System Alert'),
        ]

        # Add available themes for color grid modal (used in platinum notifications)
        context['available_themes'] = [
            (key, {
                'name': data['name'],
                'description': data['description'],
                'background_css': data['background'].replace('\n', ' ').replace('  ', ' ').strip(),
            })
            for key, data in sorted(GRADIENT_THEMES.items(),
                                   key=lambda x: (x[0] != 'default', x[1]['name']))
            if not data.get('requires_game_image')
        ]

        return context


@method_decorator(staff_member_required, name='dispatch')
class AdminNotificationCenterView(TemplateView):
    """
    Main admin notification center - compose and send notifications.
    Staff-only access.
    """
    template_name = 'notifications/admin/notification_center.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = 'Notification Center'

        # Notification type choices
        context['notification_types'] = NotificationTemplate.NOTIFICATION_TYPES
        context['priority_choices'] = NotificationTemplate.PRIORITY_CHOICES

        # Target type choices
        context['target_types'] = ScheduledNotification.TARGET_TYPE_CHOICES

        # Recent logs for sidebar
        context['recent_logs'] = NotificationLog.objects.select_related(
            'sent_by'
        ).order_by('-sent_at')[:5]

        # Pending scheduled for sidebar
        context['pending_scheduled'] = ScheduledNotification.objects.filter(
            status='pending'
        ).order_by('scheduled_at')[:5]

        return context

    def post(self, request):
        """Handle form submission for sending/scheduling notifications."""
        import json
        from notifications.validators import SectionValidator

        action = request.POST.get('action')  # 'send_now' or 'schedule'
        notification_type = request.POST.get('notification_type', 'admin_announcement')
        title = request.POST.get('title', '').strip()
        message_text = request.POST.get('message', '').strip()
        detail = request.POST.get('detail', '').strip()
        sections_json = request.POST.get('sections', '').strip()
        banner_image = request.FILES.get('banner_image')
        icon = request.POST.get('icon', '📢').strip() or '📢'
        action_url = request.POST.get('action_url', '').strip() or None
        action_text = request.POST.get('action_text', '').strip()
        priority = request.POST.get('priority', 'normal')
        target_type = request.POST.get('target_type', 'all')

        # Build criteria based on target type
        criteria = {}
        if target_type == 'individual':
            user_ids_str = request.POST.get('user_ids', '')
            criteria['user_ids'] = [
                int(x.strip()) for x in user_ids_str.split(',')
                if x.strip().isdigit()
            ]

        # Parse and validate sections (if using structured mode)
        sections = []
        if sections_json:
            try:
                sections = json.loads(sections_json)
                is_valid, error = SectionValidator.validate_sections(sections)
                if not is_valid:
                    messages.error(request, f'Invalid sections: {error}')
                    return redirect('admin_notification_center')
            except json.JSONDecodeError:
                messages.error(request, 'Invalid sections data format.')
                return redirect('admin_notification_center')
        # If no sections, use detail (legacy markdown mode)

        # Validation
        if not title or not message_text:
            messages.error(request, 'Title and message are required.')
            return redirect('admin_notification_center')

        if len(title) > 255:
            messages.error(request, 'Title must be 255 characters or less.')
            return redirect('admin_notification_center')

        if len(message_text) > 1000:
            messages.error(request, 'Message must be 1000 characters or less.')
            return redirect('admin_notification_center')

        if len(detail) > 2500:
            messages.error(request, 'Detail must be 2500 characters or less.')
            return redirect('admin_notification_center')

        # Image validation and optimization
        optimized_banner_image = None
        if banner_image:
            from trophies.image_utils import validate_image, optimize_image
            from django.core.exceptions import ValidationError

            try:
                validate_image(banner_image, max_size_mb=5, image_type='banner image')
                optimized_banner_image = optimize_image(banner_image, max_width=2048, max_height=2048, quality=85)
            except ValidationError as e:
                messages.error(request, str(e))
                return redirect('admin_notification_center')

        if action == 'schedule':
            scheduled_datetime = request.POST.get('scheduled_datetime')
            if not scheduled_datetime:
                messages.error(request, 'Scheduled date/time is required.')
                return redirect('admin_notification_center')

            try:
                # Parse the datetime-local input
                scheduled_at = timezone.make_aware(
                    datetime.fromisoformat(scheduled_datetime)
                )
            except ValueError:
                messages.error(request, 'Invalid date/time format.')
                return redirect('admin_notification_center')

            if scheduled_at <= timezone.now():
                messages.error(request, 'Scheduled time must be in the future.')
                return redirect('admin_notification_center')

            scheduled = ScheduledNotificationService.create_scheduled(
                notification_type=notification_type,
                title=title,
                message=message_text,
                target_type=target_type,
                scheduled_at=scheduled_at,
                created_by=request.user,
                criteria=criteria,
                detail=detail,
                sections=sections,
                banner_image=optimized_banner_image,
                icon=icon,
                action_url=action_url,
                action_text=action_text,
                priority=priority,
            )

            messages.success(
                request,
                f'Notification scheduled for {scheduled_at.strftime("%Y-%m-%d %H:%M")}. '
                f'Estimated recipients: {scheduled.recipient_count:,}'
            )
        else:
            # Send immediately
            log, count = ScheduledNotificationService.send_immediate(
                notification_type=notification_type,
                title=title,
                message=message_text,
                target_type=target_type,
                sent_by=request.user,
                criteria=criteria,
                detail=detail,
                sections=sections,
                banner_image=optimized_banner_image,
                icon=icon,
                action_url=action_url,
                action_text=action_text,
                priority=priority,
            )

            if count > 0:
                messages.success(request, f'Notification sent to {count:,} users.')
            else:
                messages.warning(request, 'No users matched the targeting criteria.')

        return redirect('admin_notification_center')


@method_decorator(staff_member_required, name='dispatch')
class AdminNotificationHistoryView(ListView):
    """View sent notification history. Staff-only access."""
    model = NotificationLog
    template_name = 'notifications/admin/notification_history.html'
    context_object_name = 'logs'
    paginate_by = 20

    def get_queryset(self):
        return NotificationLog.objects.select_related(
            'sent_by', 'scheduled_notification'
        ).order_by('-sent_at')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = 'Notification History'
        return context


@method_decorator(staff_member_required, name='dispatch')
class AdminScheduledNotificationsView(ListView):
    """View and manage scheduled notifications. Staff-only access."""
    model = ScheduledNotification
    template_name = 'notifications/admin/scheduled_notifications.html'
    context_object_name = 'scheduled_list'
    paginate_by = 20

    def get_queryset(self):
        status = self.request.GET.get('status', 'pending')
        qs = ScheduledNotification.objects.select_related('created_by')
        if status != 'all':
            qs = qs.filter(status=status)
        return qs.order_by('-scheduled_at')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = 'Scheduled Notifications'
        context['current_status'] = self.request.GET.get('status', 'pending')
        context['status_counts'] = {
            'pending': ScheduledNotification.objects.filter(status='pending').count(),
            'sent': ScheduledNotification.objects.filter(status='sent').count(),
            'cancelled': ScheduledNotification.objects.filter(status='cancelled').count(),
            'failed': ScheduledNotification.objects.filter(status='failed').count(),
        }
        return context


@method_decorator(staff_member_required, name='dispatch')
class AdminCancelScheduledView(View):
    """Cancel a scheduled notification. Staff-only access."""

    def post(self, request, pk):
        success = ScheduledNotificationService.cancel(pk, request.user)
        if success:
            messages.success(request, 'Scheduled notification cancelled.')
        else:
            messages.error(
                request,
                'Could not cancel notification. It may have already been sent.'
            )
        return redirect('admin_scheduled_notifications')
