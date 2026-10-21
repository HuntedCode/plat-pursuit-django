"""The address a fresh sign-up is waiting on, kept in its own session (FV-08, onboarding rebuild).

"Check your inbox" used to be a dead end: no address, no resend, and a button back to Sign in. It now
says where the link went, can send another, and can undo a mistyped address. All three read ONE
session record, so they only ever act on the account this browser just made or signed in to.

WHO WRITES IT. The sign-up itself (`CustomAccountAdapter.save_user`, `created=True`), so the page knows
the address even when allauth's rate limit skipped the email; and every confirmation email that really
sends (`send_confirmation_mail`, which stamps `sent_at`), including the one a sign-in to an unconfirmed
account triggers (`created=False`). A signed-in sender, such as staff resending from the admin, is never
recorded. A completed sign-in clears it (`users/signals.py`), so a later visit does not show a stale
address.

WHY THE SESSION AND NOT A FORM FIELD. Resend and fix-it take no address from the request. A POST that
named an email would let anyone re-send mail to, or delete, an account they typed in; one that reads the
session can only touch the sign-up that happened here.
"""
import re
import time

from allauth.account import app_settings as allauth_settings
from allauth.account.internal import stagekit
from allauth.account.internal.flows.email_verification import send_verification_email_to_address
from allauth.account.models import EmailAddress
from allauth.core import ratelimit
from django.db import transaction

from users.models import CustomUser

SESSION_KEY = 'pp_pending_signup'
_DEFAULT_COOLDOWN_SECONDS = 180


def resend_cooldown_seconds():
    """allauth's own `confirm_email` limit ("1/180s/key", from ACCOUNT_EMAIL_CONFIRMATION_COOLDOWN), read
    rather than copied, so the wait the page shows is the wait allauth enforces."""
    match = re.fullmatch(r'1/(\d+)s/key', str((allauth_settings.RATE_LIMITS or {}).get('confirm_email') or ''))
    return int(match.group(1)) if match else _DEFAULT_COOLDOWN_SECONDS


def record_signup(request, user):
    """The sign-up just made `user`. `sent_at` stays empty until an email really goes."""
    request.session[SESSION_KEY] = {'user_id': user.pk, 'email': user.email, 'created': True, 'sent_at': None}


def record_send(request, user, email, *, created):
    """A confirmation email just went to `email`. A send for the account this session made keeps
    `created`, so fix-it still works after a resend."""
    previous = pending(request)
    same_account = previous is not None and previous['user_id'] == user.pk
    request.session[SESSION_KEY] = {
        'user_id': user.pk,
        'email': email,
        'created': bool(created or (same_account and previous.get('created'))),
        'sent_at': time.time(),
    }


def pending(request):
    record = request.session.get(SESSION_KEY)
    return record if isinstance(record, dict) and record.get('user_id') and record.get('email') else None


def resend_wait(request, now=None):
    """Seconds until another email can go (0 when it can, or when none has gone yet)."""
    record = pending(request)
    if record is None or not record.get('sent_at'):
        return 0
    elapsed = (now if now is not None else time.time()) - float(record['sent_at'])
    return max(0, int(resend_cooldown_seconds() - elapsed + 0.999))


def resend(request):
    """Send the confirmation again. Returns 'sent', 'wait' (allauth's limit), 'confirmed' (nothing to do)
    or 'none' (no sign-up in this session, or its account is gone)."""
    record = pending(request)
    if record is None:
        return 'none'
    if not CustomUser.objects.filter(pk=record['user_id']).exists():
        forget(request)
        return 'none'
    address = (EmailAddress.objects
               .filter(user_id=record['user_id'], email__iexact=record['email'], verified=False)
               .select_related('user').first())
    if address is None:
        return 'confirmed'
    # The adapter's send stamps the new sent_at, so the wait restarts from this send.
    return 'sent' if send_verification_email_to_address(request, address) else 'wait'


def _discardable(user, record):
    """Only the account THIS session made, only while it could never have been used: no confirmed address,
    never signed in, not staff. Everything a mistyped sign-up is, and nothing a real account is."""
    return bool(
        record and record.get('created')
        and user is not None
        and user.email.lower() == record['email'].lower()
        and user.last_login is None
        and not user.is_staff and not user.is_superuser
        and not any(address.verified for address in user.emailaddress_set.all())
    )


def can_discard(record):
    if not record:
        return False
    return _discardable(CustomUser.objects.filter(pk=record['user_id']).first(), record)


def discard(request):
    """Delete the mistyped sign-up and forget it. Returns the address it used, or None if it may not go.

    The check runs inside the transaction, against locked rows, so a confirmation link opened at the same
    moment cannot verify an account between the check and the delete."""
    record = pending(request)
    if not record:
        return None
    with transaction.atomic():
        user = CustomUser.objects.select_for_update().filter(pk=record['user_id']).first()
        if user is not None:
            list(EmailAddress.objects.select_for_update().filter(user=user))
        if not _discardable(user, record):
            return None
        user.delete()
    # The address may be typed again straight away (it was right after all): allauth's per-address
    # cooldown would otherwise skip the new account's first email.
    ratelimit.clear(request, action='confirm_email', key=record['email'].lower())
    forget(request)
    return record['email']


def forget(request):
    """Drop the record and allauth's own pending login for the same account (left behind, it would try to
    resume a user that is gone)."""
    request.session.pop(SESSION_KEY, None)
    stagekit.clear_login(request)
