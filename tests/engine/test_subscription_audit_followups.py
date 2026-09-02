"""The subscription-audit follow-ups (2026-08-22): the orphan sweep, the weekly run-report
email, and the two webhook self-heals that close the account-deletion race.

The safety property under test everywhere: NOTHING here may ever touch a paying member. The
orphan sweep and the Stripe self-heal both hinge on the same two-lookup proof (no user row
stores the customer AND djstripe links it to no subscriber) -- the second lookup is what
protects the duplicate-customer [MISMATCH] case, where a live sub sits under a customer id no
user row stores while Customer.subscriber still points at the payer.
"""
from io import StringIO
from unittest.mock import patch

import pytest
import stripe as stripe_lib
from django.core.management import call_command
from djstripe.models import Customer, Subscription

from tests.factories import ProfileFactory
from users.services.paypal_service import PayPalService
from users.services.subscription_service import SubscriptionService

pytestmark = pytest.mark.django_db


def _mk_sub(sub_id, customer_id, status, subscriber=None):
    customer = Customer.objects.create(id=customer_id, subscriber=subscriber)
    return Subscription.objects.create(
        id=sub_id, customer=customer, stripe_data={'status': status})


def _run_audit(**kwargs):
    out = StringIO()
    call_command('audit_subscription_status', stdout=out, **kwargs)
    return out.getvalue()


# ── the orphan sweep ──────────────────────────────────────────────────────────────────────────

def test_a_live_sub_with_no_user_anywhere_is_flagged_as_an_orphan():
    _mk_sub('sub_orphan', 'cus_orphan', 'active')

    out = _run_audit(no_email=True)

    assert '[ORPHAN] sub sub_orphan' in out
    assert 'ORPHANS' in out


def test_the_duplicate_customer_case_is_never_an_orphan():
    """A live sub under a customer id no user ROW stores, but with a djstripe subscriber:
    that is the audit's [MISMATCH] to repoint, and flagging it as an orphan would invite a
    hand-cancel of a PAYING member."""
    profile = ProfileFactory(is_linked=True)
    _mk_sub('sub_dup', 'cus_sibling', 'active', subscriber=profile.user)

    out = _run_audit(no_email=True)

    assert '[ORPHAN]' not in out


def test_a_user_row_pointing_at_the_customer_is_not_an_orphan():
    profile = ProfileFactory(is_linked=True)
    profile.user.stripe_customer_id = 'cus_mine'
    profile.user.save(update_fields=['stripe_customer_id'])
    _mk_sub('sub_mine', 'cus_mine', 'active')

    out = _run_audit(no_email=True)

    assert '[ORPHAN]' not in out


def test_terminal_subs_are_not_orphans():
    _mk_sub('sub_dead', 'cus_dead', 'canceled')

    out = _run_audit(no_email=True)

    assert '[ORPHAN]' not in out


# ── the run-report email ──────────────────────────────────────────────────────────────────────

def test_the_report_emails_the_operator_with_topline_counts(settings, mailoutbox):
    settings.AUDIT_REPORT_EMAIL = 'ops@example.com'
    _mk_sub('sub_orphan2', 'cus_orphan2', 'active')

    _run_audit()

    assert len(mailoutbox) == 1
    msg = mailoutbox[0]
    assert msg.to == ['ops@example.com']
    assert 'ORPHANS 1' in msg.subject, 'the inbox row alone should say something needs attention'
    assert '[ORPHAN] sub sub_orphan2' in msg.body, 'the body carries the full run log'


def test_no_recipient_configured_means_no_email(settings, mailoutbox):
    settings.AUDIT_REPORT_EMAIL = ''

    _run_audit()

    assert len(mailoutbox) == 0


def test_no_email_flag_skips_the_report(settings, mailoutbox):
    settings.AUDIT_REPORT_EMAIL = 'ops@example.com'

    _run_audit(no_email=True)

    assert len(mailoutbox) == 0


# ── the Stripe self-heal ──────────────────────────────────────────────────────────────────────
# The heal needs POSITIVE evidence: a local Customer row that EXISTS with subscriber NULL --
# checkout writes the row with subscriber set, account deletion SET_NULLs it, so that shape is
# the deletion race's exact fingerprint. And it all sits behind the default-off flag.

def test_an_activation_event_for_a_true_orphan_cancels_at_stripe(settings):
    settings.PAYMENT_SELF_HEAL_ENABLED = True
    Customer.objects.create(id='cus_gone', subscriber=None)  # the deletion fingerprint

    with patch.object(stripe_lib.Subscription, 'cancel') as cancel:
        SubscriptionService.handle_webhook_event(
            'customer.subscription.created',
            {'id': 'sub_race', 'customer': 'cus_gone', 'status': 'active'})

    cancel.assert_called_once_with('sub_race')


def test_the_flag_defaults_off_and_blocks_the_heal(settings):
    settings.PAYMENT_SELF_HEAL_ENABLED = False
    Customer.objects.create(id='cus_gone_b', subscriber=None)

    with patch.object(stripe_lib.Subscription, 'cancel') as cancel:
        SubscriptionService.handle_webhook_event(
            'customer.subscription.created',
            {'id': 'sub_race_b', 'customer': 'cus_gone_b', 'status': 'active'})

    cancel.assert_not_called()


def test_a_customer_the_mirror_does_not_know_is_never_cancelled(settings):
    """Absence of knowledge is not proof of orphanhood: a dashboard-created or unmirrored
    customer fails the positive-evidence check and goes to the weekly sweep instead. This is
    the audit's A1 finding, pinned."""
    settings.PAYMENT_SELF_HEAL_ENABLED = True
    # deliberately NO local Customer row

    with patch.object(stripe_lib.Subscription, 'cancel') as cancel:
        SubscriptionService.handle_webhook_event(
            'customer.subscription.created',
            {'id': 'sub_unknown', 'customer': 'cus_never_seen', 'status': 'active'})

    cancel.assert_not_called()


def test_a_djstripe_subscriber_blocks_the_self_heal(settings):
    """The duplicate-customer guard: the event's customer maps to no user ROW, but djstripe
    still links it to a live payer. Cancelling here would be the wrongful-cancellation bug
    the audit's repoint exists to prevent."""
    settings.PAYMENT_SELF_HEAL_ENABLED = True
    profile = ProfileFactory(is_linked=True)
    Customer.objects.create(id='cus_sibling2', subscriber=profile.user)

    with patch.object(stripe_lib.Subscription, 'cancel') as cancel:
        SubscriptionService.handle_webhook_event(
            'customer.subscription.created',
            {'id': 'sub_dup2', 'customer': 'cus_sibling2', 'status': 'active'})

    cancel.assert_not_called()


def test_a_found_user_never_reaches_the_self_heal(settings):
    """The headline safety property: when the customer id resolves to a living user, the
    normal update path runs and cancel is untouchable."""
    settings.PAYMENT_SELF_HEAL_ENABLED = True
    profile = ProfileFactory(is_linked=True)
    profile.user.stripe_customer_id = 'cus_alive'
    profile.user.save(update_fields=['stripe_customer_id'])

    with patch.object(stripe_lib.Subscription, 'cancel') as cancel, \
         patch.object(SubscriptionService, 'update_user_subscription') as update:
        SubscriptionService.handle_webhook_event(
            'customer.subscription.updated',
            {'id': 'sub_alive', 'customer': 'cus_alive', 'status': 'active'})

    cancel.assert_not_called()
    update.assert_called_once()


@pytest.mark.parametrize('event_type, payload', [
    # Terminal status: nothing live to cancel.
    ('customer.subscription.updated', {'id': 'sub_x', 'customer': 'cus_x', 'status': 'canceled'}),
    # incomplete = a brand-new checkout mid-SCA; self-expires, never healed.
    ('customer.subscription.updated', {'id': 'sub_x', 'customer': 'cus_x', 'status': 'incomplete'}),
    # Deletion events never heal (the sub is already ending).
    ('customer.subscription.deleted', {'id': 'sub_x', 'customer': 'cus_x', 'status': 'active'}),
    # checkout.session.completed has no status payload; its subscription.created follows and heals.
    ('checkout.session.completed', {'id': 'cs_x', 'customer': 'cus_x'}),
    # Invoice events are not the self-heal's business.
    ('invoice.payment_failed', {'customer': 'cus_x'}),
])
def test_the_self_heal_stays_narrow(settings, event_type, payload):
    settings.PAYMENT_SELF_HEAL_ENABLED = True
    Customer.objects.create(id='cus_x', subscriber=None)  # even WITH the fingerprint present

    with patch.object(stripe_lib.Subscription, 'cancel') as cancel:
        SubscriptionService.handle_webhook_event(event_type, payload)

    cancel.assert_not_called()


def test_a_failed_cancel_never_raises_out_of_the_webhook(settings):
    settings.PAYMENT_SELF_HEAL_ENABLED = True
    Customer.objects.create(id='cus_gone2', subscriber=None)

    with patch.object(stripe_lib.Subscription, 'cancel', side_effect=RuntimeError('api down')):
        SubscriptionService.handle_webhook_event(
            'customer.subscription.created',
            {'id': 'sub_race2', 'customer': 'cus_gone2', 'status': 'active'})
    # Reaching here without an exception IS the assertion; the audit sweep backstops it.


# ── the PayPal self-heal ──────────────────────────────────────────────────────────────────────

def _paypal_event(event_type, resource):
    return PayPalService.handle_webhook_event(event_type, resource)


def test_an_activated_event_for_a_deleted_user_cancels_at_paypal(settings):
    settings.PAYMENT_SELF_HEAL_ENABLED = True

    with patch.object(PayPalService, 'cancel_subscription') as cancel, \
         patch.object(PayPalService, 'bust_subscription_snapshot'):
        _paypal_event('BILLING.SUBSCRIPTION.ACTIVATED',
                      {'id': 'I-RACE123', 'custom_id': '999999'})

    cancel.assert_called_once()
    assert cancel.call_args.args[0] == 'I-RACE123'


def test_the_flag_blocks_the_paypal_heal_too(settings):
    settings.PAYMENT_SELF_HEAL_ENABLED = False

    with patch.object(PayPalService, 'cancel_subscription') as cancel, \
         patch.object(PayPalService, 'bust_subscription_snapshot'):
        _paypal_event('BILLING.SUBSCRIPTION.ACTIVATED',
                      {'id': 'I-RACE124', 'custom_id': '999998'})

    cancel.assert_not_called()


def test_an_unparseable_custom_id_never_cancels(settings):
    """A malformed/foreign event is not proof the subscription is ours-but-orphaned."""
    settings.PAYMENT_SELF_HEAL_ENABLED = True

    with patch.object(PayPalService, 'cancel_subscription') as cancel, \
         patch.object(PayPalService, 'bust_subscription_snapshot'):
        _paypal_event('BILLING.SUBSCRIPTION.ACTIVATED',
                      {'id': 'I-WEIRD456', 'custom_id': 'not-a-user-id'})

    cancel.assert_not_called()


def test_an_activated_event_for_a_living_user_activates_and_heals_nothing(settings):
    """The happy path proves itself: the custom_id lookup finds the user, activation runs,
    and cancel is untouchable."""
    settings.PAYMENT_SELF_HEAL_ENABLED = True
    profile = ProfileFactory(is_linked=True)

    with patch.object(PayPalService, 'cancel_subscription') as cancel, \
         patch.object(PayPalService, 'bust_subscription_snapshot'), \
         patch.object(PayPalService, 'get_tier_from_plan_id', return_value='backer'), \
         patch.object(SubscriptionService, 'activate_subscription') as activate:
        _paypal_event('BILLING.SUBSCRIPTION.ACTIVATED',
                      {'id': 'I-ALIVE789', 'custom_id': str(profile.user.id),
                       'plan_id': 'P-LADDER-BACKER'})

    cancel.assert_not_called()
    assert activate.called, 'the living-user path must actually activate, not silently bail'


def test_a_send_mail_failure_never_fails_the_audit(settings, mailoutbox):
    settings.AUDIT_REPORT_EMAIL = 'ops@example.com'

    with patch('users.management.commands.audit_subscription_status.send_mail',
               side_effect=RuntimeError('smtp down')):
        out = _run_audit()

    assert 'Report email FAILED' in out
    assert 'Summary' in out, 'the audit itself completed'
