"""
Subscription admin dashboard: staff-only view for monitoring subscription health.

Provides:
- Stats cards (active, past_due, deactivated, suppressed)
- Attention Needed tab: past-due users with notification/email history
- All Subscribers tab: full subscriber list
- Recent Activity tab: recently ended subscription periods
"""
import logging
from datetime import datetime, timedelta

from django.contrib.admin.views.decorators import staff_member_required
from django.db.models import Count, Q
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views.generic import TemplateView

from core.models import EmailLog
from notifications.models import Notification
from users.models import CustomUser, SubscriptionPeriod
from users.services.email_preference_service import EmailPreferenceService
from users.services.subscription_service import SubscriptionService

logger = logging.getLogger('users.admin')


def _get_psn(user):
    """Get PSN username from a user with select_related('profile'), or 'N/A'."""
    try:
        return user.profile.psn_username
    except Exception:
        return 'N/A'


@method_decorator(staff_member_required, name='dispatch')
class SubscriptionAdminView(TemplateView):
    template_name = 'users/admin/subscription_dashboard.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        # ── Stats cards ──────────────────────────────────────────────
        all_premium = list(
            CustomUser.objects.filter(
                premium_tier__isnull=False,
            ).select_related('profile').order_by('premium_tier', 'email')
        )

        stripe_count = sum(1 for u in all_premium if u.subscription_provider == 'stripe')
        paypal_count = sum(1 for u in all_premium if u.subscription_provider == 'paypal')
        total_active = stripe_count + paypal_count

        # Past-due users (Stripe only, PayPal suspends immediately)
        past_due_users = self._get_past_due_users()

        # Recently deactivated (last 30 days)
        thirty_days_ago = timezone.now() - timedelta(days=30)
        recent_deactivated = list(
            SubscriptionPeriod.objects.filter(
                ended_at__isnull=False,
                ended_at__gte=thirty_days_ago,
            ).select_related('user', 'user__profile').order_by('-ended_at')[:30]
        )

        # Email suppression count (users with sub_notifications off who are premium)
        suppressed_count = sum(
            1 for u in all_premium
            if not EmailPreferenceService.should_send_email(u, 'subscription_notifications')
        )

        # Tier breakdown
        tier_counts = {}
        for user in all_premium:
            tier_counts[user.premium_tier] = tier_counts.get(user.premium_tier, 0) + 1
        tier_breakdown = [
            {
                'tier': tier,
                'display': SubscriptionService.get_tier_display_name(tier),
                'count': count,
            }
            for tier, count in sorted(tier_counts.items())
        ]

        # ── All subscribers ──────────────────────────────────────────
        all_subscribers = [
            {
                'id': user.id,
                'email': user.email,
                'psn_username': _get_psn(user),
                'tier': user.premium_tier,
                'tier_display': SubscriptionService.get_tier_display_name(user.premium_tier),
                'provider': user.subscription_provider or 'unknown',
            }
            for user in all_premium
        ]

        # ── Recent activity ──────────────────────────────────────────
        recent_activity = [
            {
                'id': period.user.id,
                'psn_username': _get_psn(period.user),
                'email': period.user.email,
                'provider': period.provider,
                'duration_days': (period.ended_at - period.started_at).days if period.ended_at and period.started_at else 0,
                'ended_at': period.ended_at,
                'notes': period.notes,
            }
            for period in recent_deactivated
        ]

        context.update({
            # Stats
            'total_active': total_active,
            'stripe_count': stripe_count,
            'paypal_count': paypal_count,
            'past_due_count': len(past_due_users),
            'deactivated_count': len(recent_deactivated),
            'suppressed_count': suppressed_count,
            # Tier breakdown
            'tier_breakdown': tier_breakdown,
            # Tabs
            'past_due_users': past_due_users,
            'all_subscribers': all_subscribers,
            'recent_activity': recent_activity,
        })

        return context

    def _get_past_due_users(self):
        """
        Find Stripe users whose subscription is past_due.

        Enriches each user with their latest notification and email log info
        for the dashboard display.
        """
        from djstripe.models import Subscription

        # Find all djstripe subscriptions in past_due state
        past_due_subs = list(
            Subscription.objects.filter(
                stripe_data__status='past_due',
            ).select_related('customer')
        )
        if not past_due_subs:
            return []

        # Bulk-fetch users by customer IDs
        customer_ids = [sub.customer_id for sub in past_due_subs]
        users_by_customer = {
            u.stripe_customer_id: u
            for u in CustomUser.objects.filter(
                stripe_customer_id__in=customer_ids,
                premium_tier__isnull=False,
            ).select_related('profile')
        }
        if not users_by_customer:
            return []

        # Bulk-fetch latest payment_failed notifications for these users
        user_ids = [u.id for u in users_by_customer.values()]
        latest_notifications = {}
        for n in Notification.objects.filter(
            recipient_id__in=user_ids,
            notification_type='payment_failed',
        ).order_by('recipient_id', '-created_at'):
            if n.recipient_id not in latest_notifications:
                latest_notifications[n.recipient_id] = n

        # Bulk-fetch latest email logs for these users
        latest_emails = {}
        for log in EmailLog.objects.filter(
            user_id__in=user_ids,
            email_type__in=['payment_failed', 'payment_failed_final'],
        ).order_by('user_id', '-created_at'):
            if log.user_id not in latest_emails:
                latest_emails[log.user_id] = log

        # Build result list
        past_due_list = []
        for sub in past_due_subs:
            user = users_by_customer.get(sub.customer_id)
            if not user:
                continue

            latest_notification = latest_notifications.get(user.id)
            latest_email = latest_emails.get(user.id)

            # Extract retry info from notification metadata
            next_retry_at = None
            attempt_count = 0
            is_final = False
            if latest_notification and latest_notification.metadata:
                next_retry_ts = latest_notification.metadata.get('next_retry_at')
                if next_retry_ts:
                    try:
                        next_retry_at = datetime.fromtimestamp(next_retry_ts, tz=timezone.utc)
                    except (ValueError, OSError, TypeError):
                        pass
                attempt_count = latest_notification.metadata.get('attempt_count', 0)
                is_final = latest_notification.metadata.get('is_final', False)

            past_due_list.append({
                'id': user.id,
                'email': user.email,
                'psn_username': _get_psn(user),
                'tier': user.premium_tier,
                'tier_display': SubscriptionService.get_tier_display_name(user.premium_tier),
                'provider': 'stripe',
                'attempt_count': attempt_count,
                'is_final': is_final,
                'next_retry_at': next_retry_at,
                'last_notification_at': latest_notification.created_at if latest_notification else None,
                'last_email_at': latest_email.created_at if latest_email else None,
                'last_email_status': latest_email.status if latest_email else None,
                'emails_enabled': EmailPreferenceService.should_send_email(user, 'subscription_notifications'),
            })

        return past_due_list
