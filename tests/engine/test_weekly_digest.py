"""The weekly digest's preview path.

`--dry-run` is the only part of this command that still runs: sends are disabled
(`WEEKLY_DIGEST_SEND_ENABLED`) pending the email rebuild, and the preview is deliberately let
through so the queryset stays inspectable. Which makes it the half worth a test, and the half that
had none -- it was reading a key `build_digest_data` stopped returning when the challenge section
came out of the digest, so it raised `KeyError` on the first eligible profile.

The lesson is narrower than "test the command": a preview loop reads a payload NOBODY ELSE reads
that way, so it goes stale silently while the send path it previews stays correct.
"""
from datetime import timedelta
from io import StringIO

import pytest
from django.core.management import call_command

from core.services.weekly_digest_service import WeeklyDigestService
from tests.factories import EarnedTrophyFactory, ProfileFactory

pytestmark = pytest.mark.django_db


def _hunter_with_a_week():
    """A profile the preview will actually reach.

    Every earlier `continue` has to be cleared to get as far as the line under test: linked, an
    email, no recent `EmailLog`, digest preference on (the default), and not suppressed. The earned
    trophy does the last one twice over -- it is this profile's week AND the site's, and suppression
    only bites when both are empty.
    """
    profile = ProfileFactory(is_linked=True, psn_username='digestreader')

    # The digest reports the PREVIOUS ISO week, so the factory's `timezone.now()` default lands
    # after the window and the profile gets suppressed as a quiet one. Take the window from the
    # service rather than hardcoding an offset, and aim at the middle of it: the per-profile window
    # is in the hunter's timezone while the community window is UTC, and midweek is inside both
    # whatever the offset.
    week_start, _week_end = WeeklyDigestService.get_week_date_range()
    EarnedTrophyFactory(profile=profile, earned_date_time=week_start + timedelta(days=3))
    return profile


def test_the_preview_runs():
    """It raised `KeyError: 'challenges'` for anybody with a week worth previewing."""
    _hunter_with_a_week()
    out = StringIO()

    call_command('send_weekly_digest', '--dry-run', stdout=out)

    body = out.getvalue()
    assert 'digestreader' in body, 'the eligible profile never reached the preview'
    assert 'DRY RUN' in body


def test_the_preview_line_reports_what_the_digest_actually_contains():
    """Not just "does not crash". The line has to name a section the email still HAS -- pointing it
    at another removed key would pass a crash test and be wrong again."""
    _hunter_with_a_week()
    out = StringIO()

    call_command('send_weekly_digest', '--dry-run', stdout=out)

    # 'plat(s)' and not 'trophies,' -- the site-stats line matches the latter too, and picking it up
    # made this pass on a line that has nothing to do with the per-profile preview.
    line = next(l for l in out.getvalue().splitlines() if 'plat(s)' in l)
    assert 'badge(s)' in line
    assert 'challenge' not in line.lower(), 'the preview is still advertising a retired system'


def test_the_preview_sends_nothing_and_logs_nothing():
    """The reason --dry-run is exempt from the send gate at all."""
    from core.models import EmailLog
    from django.core import mail

    _hunter_with_a_week()

    call_command('send_weekly_digest', '--dry-run', stdout=StringIO())

    assert len(mail.outbox) == 0
    assert EmailLog.objects.filter(email_type='weekly_digest').count() == 0
