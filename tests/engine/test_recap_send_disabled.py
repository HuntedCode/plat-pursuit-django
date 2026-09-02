"""The monthly recap send is OFF while the recap is rebuilt.

There was no test coverage for the recap at all -- the legacy tests were deleted with the pytest harness
migration -- so this is the first pin on the system. It guards the one behaviour that is expensive to get
wrong: mail going out to every linked hunter carrying the design we are replacing.

The in-app notification is dispatched from inside the email loop, so stopping the send stops both. That
coupling is deliberate here (nothing old ships), and is asserted rather than left implicit.
"""
from io import StringIO

import pytest
from django.core.management import call_command
from django.test import override_settings
from django.utils import timezone

from trophies.models import MonthlyRecap
from tests.factories import ProfileFactory

pytestmark = pytest.mark.django_db


def _sendable_recap(**over):
    """A recap that WOULD be emailed: finalized, linked profile, real email, nothing sent yet."""
    profile = ProfileFactory(is_linked=True)
    profile.user.email = 'hunter@example.com'
    profile.user.save(update_fields=['email'])
    now = timezone.now()
    fields = dict(
        profile=profile, year=now.year, month=now.month,
        total_trophies_earned=12, platinums_earned=1,
        is_finalized=True, email_sent=False, notification_sent=False,
    )
    fields.update(over)
    return MonthlyRecap.objects.create(**fields)


def _run(**opts):
    out = StringIO()
    call_command('send_monthly_recap_emails', stdout=out, stderr=StringIO(), **opts)
    return out.getvalue()


@override_settings(MONTHLY_RECAP_SEND_ENABLED=False)
def test_the_send_is_disabled_and_marks_nothing():
    """The guard sits at the top of handle(), so no row is touched -- not email_sent, not
    notification_sent. A guard further down would still fire notifications and mark rows."""
    recap = _sendable_recap()

    output = _run(year=recap.year, month=recap.month)

    recap.refresh_from_db()
    assert recap.email_sent is False
    assert recap.notification_sent is False
    assert 'DISABLED' in output


@override_settings(MONTHLY_RECAP_SEND_ENABLED=False)
def test_no_in_app_notification_either():
    """The notification is dispatched from inside the email loop, so disabling the send stops it too.
    Asserted because it is the non-obvious half: 'disable the emails' silently also means 'no inbox item'."""
    from notifications.models import Notification

    recap = _sendable_recap()

    _run(year=recap.year, month=recap.month)

    assert not Notification.objects.filter(recipient=recap.profile.user).exists()


@override_settings(MONTHLY_RECAP_SEND_ENABLED=False)
def test_dry_run_still_previews():
    """--dry-run writes nothing and sends nothing, so it stays usable while the send is off -- otherwise
    there is no way to inspect what WOULD go out when we turn it back on."""
    recap = _sendable_recap()

    output = _run(year=recap.year, month=recap.month, dry_run=True)

    recap.refresh_from_db()
    assert recap.email_sent is False
    assert 'DISABLED' not in output, 'dry-run should reach the normal preview path'


@override_settings(MONTHLY_RECAP_SEND_ENABLED=True)
def test_the_flag_is_what_gates_it(mailoutbox):
    """The counterpart: with the flag on, the command runs its normal path again. Pins that the guard is
    a FLAG and not an accidental hard stop -- this is how the rebuilt email gets switched back on."""
    recap = _sendable_recap()

    output = _run(year=recap.year, month=recap.month)

    assert 'DISABLED' not in output
