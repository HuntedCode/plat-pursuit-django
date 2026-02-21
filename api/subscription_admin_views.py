"""
Staff-only API endpoints for subscription admin dashboard actions.

Provides:
- SubscriptionAdminActionView: resend emails, resend notifications, force deactivate
- SubscriptionAdminUserDetailView: fetch a user's notification + email history
"""
import logging

from rest_framework import status
from rest_framework.authentication import SessionAuthentication, TokenAuthentication
from rest_framework.permissions import IsAuthenticated, IsAdminUser
from rest_framework.response import Response
from rest_framework.views import APIView

from core.models import EmailLog
from notifications.models import Notification
from users.models import CustomUser, SubscriptionPeriod
from users.services.subscription_service import SubscriptionService

logger = logging.getLogger('users.admin')


class SubscriptionAdminActionView(APIView):
    """Staff-only actions: resend emails, resend notifications, force deactivate."""
    authentication_classes = [SessionAuthentication, TokenAuthentication]
    permission_classes = [IsAuthenticated, IsAdminUser]
    throttle_classes = []

    def post(self, request):
        action = request.data.get('action')
        user_id = request.data.get('user_id')

        try:
            user = CustomUser.objects.get(id=user_id)
        except CustomUser.DoesNotExist:
            return Response({'error': 'User not found'}, status=status.HTTP_404_NOT_FOUND)
        except (ValueError, TypeError):
            return Response({'error': 'Invalid user ID'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            if action == 'resend_payment_email':
                sent = SubscriptionService._send_payment_failed_email(
                    user, is_final_warning=False, triggered_by='admin_manual',
                )
                msg = 'Payment warning email sent' if sent else 'Email was suppressed (user preference) or failed'
                return Response({'success': bool(sent), 'message': msg})

            elif action == 'resend_payment_email_final':
                sent = SubscriptionService._send_payment_failed_email(
                    user, is_final_warning=True, triggered_by='admin_manual',
                )
                msg = 'Final warning email sent' if sent else 'Email was suppressed (user preference) or failed'
                return Response({'success': bool(sent), 'message': msg})

            elif action == 'resend_notification':
                attempt = request.data.get('attempt_count', 1)
                is_final = request.data.get('is_final', False)
                SubscriptionService._send_payment_failed_notification(
                    user, attempt_count=attempt, is_final=is_final, triggered_by='admin_manual',
                )
                # Audit trail for admin-triggered notification resend
                EmailLog.objects.create(
                    user=user,
                    recipient_email=user.email,
                    email_type='payment_failed',
                    subject='Payment failed notification resent by admin',
                    status='sent',
                    triggered_by='admin_manual',
                    metadata={
                        'action': 'resend_notification',
                        'admin_user': request.user.email,
                    },
                )
                return Response({'success': True, 'message': 'In-app notification sent'})

            elif action == 'force_deactivate':
                notes = request.data.get('notes', '')
                if not user.premium_tier:
                    return Response({'error': 'User is not currently premium'}, status=status.HTTP_400_BAD_REQUEST)
                provider = user.subscription_provider or 'stripe'
                SubscriptionService.deactivate_subscription(user, provider, 'admin_manual')
                EmailLog.objects.create(
                    user=user,
                    recipient_email=user.email,
                    email_type='subscription_cancelled',
                    subject='Force deactivated by admin',
                    status='sent',
                    triggered_by='admin_manual',
                    metadata={
                        'admin_notes': notes,
                        'admin_user': request.user.email,
                        'force_deactivate': True,
                    },
                )
                return Response({'success': True, 'message': f'Deactivated {user.email}'})

            elif action == 'send_welcome_email':
                tier_name = SubscriptionService.get_tier_display_name(user.premium_tier) if user.premium_tier else 'Premium'
                sent = SubscriptionService._send_subscription_welcome_email(
                    user, tier_name, triggered_by='admin_manual',
                )
                msg = 'Welcome email sent' if sent else 'Email was suppressed (user preference) or failed'
                return Response({'success': bool(sent), 'message': msg})

            elif action == 'send_payment_succeeded_email':
                tier_name = SubscriptionService.get_tier_display_name(user.premium_tier) if user.premium_tier else 'Premium'
                sent = SubscriptionService._send_payment_succeeded_email(
                    user, tier_name, triggered_by='admin_manual',
                )
                msg = 'Payment succeeded email sent' if sent else 'Email was suppressed (user preference) or failed'
                return Response({'success': bool(sent), 'message': msg})

            elif action == 'resend_action_required_email':
                from django.conf import settings
                latest = Notification.objects.filter(
                    recipient=user,
                    notification_type='payment_action_required',
                ).order_by('-created_at').first()
                invoice_url = (latest.metadata or {}).get('invoice_url', '') if latest else ''
                if not invoice_url:
                    invoice_url = f"{settings.SITE_URL}/users/subscription-management/"
                sent = SubscriptionService._send_payment_action_required_email(
                    user, invoice_url, triggered_by='admin_manual',
                )
                msg = 'Action required email sent' if sent else 'Email was suppressed (user preference) or failed'
                return Response({'success': bool(sent), 'message': msg})

            return Response({'error': f'Unknown action: {action}'}, status=status.HTTP_400_BAD_REQUEST)

        except Exception as e:
            logger.exception(f"Subscription admin action '{action}' failed for user {user_id}: {e}")
            return Response({'error': 'Internal error.'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class SubscriptionAdminUserDetailView(APIView):
    """Staff-only: fetch a user's notification + email history for the detail modal."""
    authentication_classes = [SessionAuthentication, TokenAuthentication]
    permission_classes = [IsAuthenticated, IsAdminUser]
    throttle_classes = []

    def get(self, request, user_id):
        try:
            user = CustomUser.objects.select_related('profile').get(id=user_id)
        except CustomUser.DoesNotExist:
            return Response({'error': 'User not found'}, status=status.HTTP_404_NOT_FOUND)

        try:
            psn = user.profile.psn_username
        except Exception:
            psn = 'N/A'

        try:
            # In-app notifications (payment_failed + subscription_updated)
            notifications = Notification.objects.filter(
                recipient=user,
                notification_type__in=['payment_failed', 'payment_action_required', 'subscription_updated'],
            ).order_by('-created_at')[:20]

            # Email logs
            email_logs = EmailLog.objects.filter(user=user).order_by('-created_at')[:20]

            # Subscription periods
            periods = SubscriptionPeriod.objects.filter(user=user).order_by('-started_at')[:10]

            return Response({
                'user': {
                    'email': user.email,
                    'psn_username': psn,
                    'tier': user.premium_tier,
                    'tier_display': SubscriptionService.get_tier_display_name(user.premium_tier) if user.premium_tier else None,
                    'provider': user.subscription_provider,
                },
                'notifications': [
                    {
                        'type': n.notification_type,
                        'title': n.title,
                        'priority': n.priority,
                        'is_read': n.is_read,
                        'created_at': n.created_at.isoformat(),
                    }
                    for n in notifications
                ],
                'email_logs': [
                    {
                        'email_type': log.get_email_type_display(),
                        'status': log.status,
                        'triggered_by': log.triggered_by,
                        'created_at': log.created_at.isoformat(),
                    }
                    for log in email_logs
                ],
                'periods': [
                    {
                        'started_at': p.started_at.isoformat(),
                        'ended_at': p.ended_at.isoformat() if p.ended_at else None,
                        'provider': p.provider,
                        'duration_days': (p.ended_at - p.started_at).days if p.ended_at else None,
                        'notes': p.notes,
                    }
                    for p in periods
                ],
            })

        except Exception as e:
            logger.exception(f"Failed to fetch subscription details for user {user_id}: {e}")
            return Response({'error': 'Internal error.'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
