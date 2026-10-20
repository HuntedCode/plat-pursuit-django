"""
REST API views for notification system.
Follows the pattern from api/views.py.
"""
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status as http_status
from rest_framework.permissions import IsAuthenticated, IsAdminUser
from rest_framework.authentication import SessionAuthentication, TokenAuthentication
from django.utils.decorators import method_decorator
from django_ratelimit.decorators import ratelimit
from notifications.services.notification_service import NotificationService
from notifications.services.scheduled_notification_service import ScheduledNotificationService
from notifications.models import Notification
from django.contrib.auth import get_user_model
import logging

logger = logging.getLogger(__name__)
CustomUser = get_user_model()


class NotificationListView(APIView):
    """
    GET /api/v1/notifications/
    Query params: unread_only=true, limit=10, offset=0

    Returns:
    {
        "notifications": [...],
        "unread_count": 5,
        "total_count": 42
    }
    """
    permission_classes = [IsAuthenticated]
    authentication_classes = [SessionAuthentication, TokenAuthentication]

    @method_decorator(ratelimit(key='user', rate='60/m', method='GET'))
    def get(self, request):
        try:
            # Parse query parameters
            unread_only = request.GET.get('unread_only', 'false').lower() == 'true'
            notification_type = request.GET.get('type', '')
            from api.utils import safe_int
            limit = min(safe_int(request.GET.get('limit', 10), 10), 50)
            offset = max(0, min(safe_int(request.GET.get('offset', 0)), 10000))

            # Get notifications using service
            notifications, total_count = NotificationService.get_user_notifications(
                user=request.user,
                unread_only=unread_only,
                notification_type=notification_type,
                limit=limit,
                offset=offset
            )

            # Get unread count
            unread_count = NotificationService.get_unread_count(request.user)

            # Serialize notifications
            notifications_data = []
            for notification in notifications:
                notifications_data.append({
                    'id': notification.id,
                    'notification_type': notification.notification_type,
                    'title': notification.title,
                    'message': notification.message,
                    'detail': notification.detail,  # Rich text detail with markdown
                    'sections': notification.sections,  # Structured sections for admin announcements
                    'banner_image': notification.banner_image.url if notification.banner_image else None,  # Banner image URL
                    'icon': notification.icon,
                    'action_url': notification.action_url,
                    'action_text': notification.action_text,
                    'priority': notification.priority,
                    'metadata': notification.metadata,  # Include metadata for enhanced rendering
                    'is_read': notification.is_read,
                    'created_at': notification.created_at.isoformat(),
                    'read_at': notification.read_at.isoformat() if notification.read_at else None,
                })

            return Response({
                'notifications': notifications_data,
                'unread_count': unread_count,
                'total_count': total_count
            })

        except ValueError as e:
            return Response(
                {'error': f'Invalid parameter: {str(e)}'},
                status=http_status.HTTP_400_BAD_REQUEST
            )
        except Exception as e:
            logger.exception(f"Error fetching notifications: {e}")
            return Response(
                {'error': 'Failed to fetch notifications'},
                status=http_status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class NotificationMarkReadView(APIView):
    """POST /api/v1/notifications/<id>/read/"""
    permission_classes = [IsAuthenticated]
    authentication_classes = [SessionAuthentication, TokenAuthentication]

    @method_decorator(ratelimit(key='user', rate='120/m', method='POST'))
    def post(self, request, pk):
        try:
            success = NotificationService.mark_as_read(pk, request.user)

            if success:
                return Response({'success': True})
            else:
                return Response(
                    {'error': 'Notification not found'},
                    status=http_status.HTTP_404_NOT_FOUND
                )

        except Exception as e:
            logger.exception(f"Error marking notification as read: {e}")
            return Response(
                {'error': 'Failed to mark notification as read'},
                status=http_status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class NotificationMarkAllReadView(APIView):
    """POST /api/v1/notifications/mark-all-read/"""
    permission_classes = [IsAuthenticated]
    authentication_classes = [SessionAuthentication, TokenAuthentication]

    @method_decorator(ratelimit(key='user', rate='30/m', method='POST'))
    def post(self, request):
        try:
            count = NotificationService.mark_all_as_read(request.user)

            return Response({
                'success': True,
                'count': count
            })

        except Exception as e:
            logger.exception(f"Error marking all notifications as read: {e}")
            return Response(
                {'error': 'Failed to mark all notifications as read'},
                status=http_status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class AdminSendNotificationView(APIView):
    """
    POST /api/v1/admin/notifications/send/
    Admin endpoint for sending notifications

    Body: {
        "target_type": "all" | "premium" | "premium_plus" | "individual",
        "notification_type": "admin_announcement",
        "title": "Title",
        "message": "Message",
        "action_url": "/path/",  # optional
        "action_text": "View",  # optional
        "priority": "normal",  # optional
        "icon": "📢",  # optional
        "user_ids": [1, 2, 3]  # required if target_type is "individual"
    }
    """
    permission_classes = [IsAuthenticated, IsAdminUser]
    authentication_classes = [SessionAuthentication, TokenAuthentication]
    throttle_classes = []

    @method_decorator(ratelimit(key='user', rate='10/m', method='POST'))
    def post(self, request):
        try:
            # Validate required fields
            target_type = request.data.get('target_type')
            notification_type = request.data.get('notification_type')
            title = request.data.get('title')
            message = request.data.get('message')

            if not all([target_type, notification_type, title, message]):
                return Response(
                    {'error': 'Missing required fields: target_type, notification_type, title, message'},
                    status=http_status.HTTP_400_BAD_REQUEST
                )

            # Validate notification_type against allowed choices
            valid_types = [t[0] for t in Notification.NOTIFICATION_TYPES]
            if notification_type not in valid_types:
                return Response(
                    {'error': f'Invalid notification type. Must be one of: {", ".join(valid_types)}'},
                    status=http_status.HTTP_400_BAD_REQUEST
                )

            # Validate title and message length
            if len(title) > 255:
                return Response(
                    {'error': 'Title must be 255 characters or less'},
                    status=http_status.HTTP_400_BAD_REQUEST
                )
            if len(message) > 1000:
                return Response(
                    {'error': 'Message must be 1000 characters or less'},
                    status=http_status.HTTP_400_BAD_REQUEST
                )

            # Get optional fields
            action_url = request.data.get('action_url', '')
            action_text = request.data.get('action_text', '')
            priority = request.data.get('priority', 'normal')
            icon = request.data.get('icon', '📢')

            # Get target users
            if target_type == 'individual':
                user_ids = request.data.get('user_ids', [])
                if not user_ids:
                    return Response(
                        {'error': 'user_ids required for individual target_type'},
                        status=http_status.HTTP_400_BAD_REQUEST
                    )
                recipients = NotificationService.get_target_users(
                    target_type=target_type,
                    user_ids=user_ids
                )
            else:
                recipients = NotificationService.get_target_users(target_type=target_type)

            # Check if any recipients found
            recipient_count = recipients.count()
            if recipient_count == 0:
                return Response(
                    {'error': 'No recipients found for target type'},
                    status=http_status.HTTP_400_BAD_REQUEST
                )

            # Send bulk notification
            created_count = NotificationService.send_bulk_notification(
                recipients_queryset=recipients,
                notification_type=notification_type,
                title=title,
                message=message,
                icon=icon,
                action_url=action_url or None,
                action_text=action_text,
                priority=priority,
            )

            logger.info(
                f"Admin (user_id={request.user.id}) sent {created_count} notifications "
                f"(type: {notification_type}, target: {target_type})"
            )

            return Response({
                'success': True,
                'count': created_count,
                'message': f'Sent {created_count} notifications successfully'
            })

        except Exception as e:
            logger.exception(f"Error sending admin notification: {e}")
            return Response(
                {'error': 'Failed to send notifications. Please try again or check the server logs.'},
                status=http_status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class NotificationBulkDeleteView(APIView):
    """POST /api/v1/notifications/bulk-delete/"""
    permission_classes = [IsAuthenticated]
    authentication_classes = [SessionAuthentication, TokenAuthentication]

    @method_decorator(ratelimit(key='user', rate='30/m', method='POST'))
    def post(self, request):
        try:
            notification_ids = request.data.get('notification_ids', [])

            if not notification_ids:
                return Response(
                    {'error': 'No notification IDs provided'},
                    status=http_status.HTTP_400_BAD_REQUEST
                )

            # Delete only user's own notifications
            deleted_count, _ = Notification.objects.filter(
                id__in=notification_ids,
                recipient=request.user
            ).delete()

            logger.info(f"User (user_id={request.user.id}) bulk deleted {deleted_count} notifications")

            return Response({
                'success': True,
                'count': deleted_count
            })

        except Exception as e:
            logger.exception(f"Error bulk deleting notifications: {e}")
            return Response(
                {'error': 'Failed to delete notifications'},
                status=http_status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class NotificationDeleteView(APIView):
    """DELETE /api/v1/notifications/<id>/"""
    permission_classes = [IsAuthenticated]
    authentication_classes = [SessionAuthentication, TokenAuthentication]

    @method_decorator(ratelimit(key='user', rate='60/m', method='DELETE'))
    def delete(self, request, pk):
        try:
            deleted_count, _ = Notification.objects.filter(
                id=pk,
                recipient=request.user
            ).delete()

            if deleted_count > 0:
                logger.info(f"User (user_id={request.user.id}) deleted notification {pk}")
                return Response({'success': True})
            else:
                return Response(
                    {'error': 'Notification not found'},
                    status=http_status.HTTP_404_NOT_FOUND
                )

        except Exception as e:
            logger.exception(f"Error deleting notification: {e}")
            return Response(
                {'error': 'Failed to delete notification'},
                status=http_status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class AdminNotificationPreviewView(APIView):
    """
    POST /api/v1/admin/notifications/preview/
    Preview how notification will look.

    Body: {
        "title": "Title",
        "message": "Message",
        "icon": "📢",
        "action_url": "/path/",
        "action_text": "View",
        "priority": "normal"
    }
    """
    permission_classes = [IsAuthenticated, IsAdminUser]
    authentication_classes = [SessionAuthentication]
    throttle_classes = []

    def post(self, request):
        from django.utils import timezone

        title = request.data.get('title', '')
        message = request.data.get('message', '')
        icon = request.data.get('icon', '📢')
        action_url = request.data.get('action_url', '')
        action_text = request.data.get('action_text', '')
        priority = request.data.get('priority', 'normal')

        return Response({
            'preview': {
                'title': title,
                'message': message,
                'icon': icon,
                'action_url': action_url,
                'action_text': action_text,
                'priority': priority,
                'created_at': timezone.now().isoformat(),
            }
        })


class AdminTargetCountView(APIView):
    """
    POST /api/v1/admin/notifications/target-count/
    Get estimated recipient count for targeting criteria.

    Body: {
        "target_type": "all",
        "criteria": {}
    }
    """
    permission_classes = [IsAuthenticated, IsAdminUser]
    authentication_classes = [SessionAuthentication]
    throttle_classes = []

    def post(self, request):
        target_type = request.data.get('target_type', 'all')
        criteria = request.data.get('criteria', {})

        try:
            count = ScheduledNotificationService.estimate_recipient_count(
                target_type, criteria
            )
            return Response({'count': count})
        except Exception as e:
            logger.exception(f"Error getting target count: {e}")
            return Response(
                {'error': 'Failed to estimate recipient count.'},
                status=http_status.HTTP_400_BAD_REQUEST
            )
