from django.dispatch import receiver
from djstripe.signals import WEBHOOK_SIGNALS
from djstripe.models import Event
from users.models import CustomUser
import logging

logger = logging.getLogger('psn_api')

@receiver(WEBHOOK_SIGNALS['checkout.session.completed'])
@receiver(WEBHOOK_SIGNALS['customer.subscription.created'])
@receiver(WEBHOOK_SIGNALS['customer.subscription.updated'])
@receiver(WEBHOOK_SIGNALS['invoice.paid'])
def update_user_subscription(sender, event: Event, **kwargs):
    if hasattr(event.data.object, 'customer'):
        customer_id = event.data.object.customer
        user = CustomUser.objects.filter(stripe_customer_id=customer_id).first()
        if user:
            user.update_subscription_status()
            logger.info(f"Updated premium_tier for user {user.id} on event {event.type}")

@receiver(WEBHOOK_SIGNALS['customer.subscription.deleted'])
@receiver(WEBHOOK_SIGNALS['invoice.payment_failed'])
def revoke_user_subscription(sender, event: Event, **kwargs):
    if hasattr(event.data.object, 'customer'):
        customer_id = event.data.object.customer
        user = CustomUser.objects.filter(stripe_customer_id=customer_id).first()
        if user:
            user.premium_tier = None
            user.save()
            logger.info(f"Revoked premium_tier for user {user.id} on event {event.type}")


