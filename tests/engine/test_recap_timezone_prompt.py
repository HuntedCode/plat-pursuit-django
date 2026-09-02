"""The timezone control in the recap header, and the first-run prompt behind it.

The timezone decides which month a trophy falls into: one earned at 23:00 on the 31st belongs to the month
the hunter experienced it in, not to UTC's. Getting it wrong quietly mis-files their entire archive.

The problem this solves is that we could not tell who needed asking. `CustomUser.user_timezone` is
`default='UTC'` and non-null, so a London hunter who never touched it is indistinguishable from one who
deliberately chose UTC. `timezone_confirmed_at` answers that question and nothing else: null means never
answered, and only an explicit save sets it.

Placement is the other half. The control sits in the header (so a hunter surprised by a month boundary can
find the reason) while the picker stays at the foot under the archive it governs -- a set-once setting
does not get to be the first thing on a page people came to look at their year on.
"""
from datetime import datetime

import pytest
import pytz
from django.urls import reverse
from django.utils import timezone

from trophies.models import MonthlyRecap
from tests.factories import (
    EarnedTrophyFactory, GameFactory, ProfileFactory, TrophyFactory,
)

pytestmark = pytest.mark.django_db


def _hunter(tz='UTC'):
    profile = ProfileFactory(is_linked=True, sync_status='synced')
    profile.user.user_timezone = tz
    profile.user.save(update_fields=['user_timezone'])
    profile.last_synced = timezone.now()
    profile.save(update_fields=['last_synced'])
    return profile


def _trophy_at(profile, when):
    return EarnedTrophyFactory(
        profile=profile, trophy=TrophyFactory(game=GameFactory()), earned=True, earned_date_time=when,
    )


def _utc(y, m, d, h=12):
    return pytz.UTC.localize(datetime(y, m, d, h))


def _prev_month():
    now = timezone.now()
    return (now.year - 1, 12) if now.month == 1 else (now.year, now.month - 1)


def _page(client, profile):
    client.force_login(profile.user)
    return client.get(reverse('recap_index')).content.decode()


# ── Where the control lives ───────────────────────────────────────────────────


def test_there_is_exactly_one_timezone_control_and_it_is_the_header(client):
    """There were two: a header button opening the prompt, and a utility row with its own `<select>` at
    the foot of the archive. One setting with two controls on one page is a page that cannot say which is
    the real one, and both had to be kept in step."""
    profile = _hunter(tz='America/New_York')
    year, month = _prev_month()
    _trophy_at(profile, _utc(year, month, 15))

    body = _page(client, profile)

    assert 'id="tz-open"' in body[:body.index('rca-hero')], 'no way to reach the timezone from the header'
    assert 'aria-haspopup="dialog"' in body, 'the control does not announce that it opens a dialog'
    assert 'id="recap-timezone-select"' not in body, 'the retired utility row is back on the page'
    assert 'id="timezone-section"' not in body
    # The prompt's own picker is the one that survives, and it lives inside the dialog.
    assert body.count('id="tz-modal-select"') == 1


def test_the_header_control_names_the_zone(client):
    """A bare clock icon says nothing, and "America/New_York" does not fit a 375 header. The city does."""
    profile = _hunter(tz='America/New_York')
    _trophy_at(profile, _utc(*_prev_month(), 15))

    assert 'rca-tzchip__zone">New York<' in _page(client, profile), (
        'the header control does not name the zone -- the view is not supplying the short form'
    )


# ── When the prompt opens by itself ───────────────────────────────────────────


def test_the_prompt_is_armed_for_a_hunter_who_has_never_confirmed(client):
    profile = _hunter(tz='UTC')
    _trophy_at(profile, _utc(*_prev_month(), 15))
    assert profile.user.timezone_confirmed_at is None

    body = _page(client, profile)

    assert 'id="tz-modal"' in body, 'the prompt is not on the page at all'
    assert 'const confirmed = false' in body, 'the prompt would not open for an unconfirmed hunter'


def test_the_prompt_is_disarmed_once_the_zone_has_been_confirmed(client):
    """Confirming is durable and cross-device, so it must outrank any per-device dismissal."""
    profile = _hunter(tz='UTC')
    profile.user.timezone_confirmed_at = timezone.now()
    profile.user.save(update_fields=['timezone_confirmed_at'])
    _trophy_at(profile, _utc(*_prev_month(), 15))

    body = _page(client, profile)

    assert 'const confirmed = true' in body, 'the prompt still opens itself after being answered'
    assert 'id="tz-open"' in body, 'but it must still be reachable on purpose'


def test_the_prompt_reaches_a_hunter_with_no_months_yet(client):
    """The hunter with an empty archive is precisely the one most likely never to have set a timezone, and
    the one whose FIRST recap gets mis-filed if it is wrong. Gating on having an archive would skip them."""
    profile = _hunter(tz='UTC')          # no trophies at all

    body = _page(client, profile)

    assert 'No months to wrap yet' in body
    assert 'id="tz-modal"' in body, 'the prompt is hidden from the hunter who most needs it'


def test_a_gated_hunter_is_not_prompted(client):
    """Someone who has not linked cannot have a recap to mis-file; asking them to settle a timezone is a
    question about nothing."""
    profile = ProfileFactory(is_linked=False, sync_status='never')

    body = _page(client, profile)

    assert 'id="tz-modal"' not in body
    assert 'id="tz-open"' not in body


# ── What saving records ───────────────────────────────────────────────────────


def test_saving_records_that_the_zone_was_confirmed(client):
    """Including a save that picks the SAME zone back. The stamp answers "have you ever answered", not
    "what did you pick" -- confirming UTC is an answer, and that hunter must not be asked again."""
    profile = _hunter(tz='UTC')
    client.force_login(profile.user)

    response = client.post(reverse('api:user-timezone-update'),
                           data={'timezone': 'UTC'}, content_type='application/json')

    assert response.status_code == 200
    profile.user.refresh_from_db()
    assert profile.user.timezone_confirmed_at is not None, (
        'confirming the zone you already had leaves you marked as never having answered'
    )
    assert response.json()['changed'] is False


def test_changing_the_zone_reports_it_and_unfinalizes_the_recaps(client):
    """The page around the control was built from the OLD month boundaries, so the caller needs to know
    whether anything actually moved before deciding to reload."""
    profile = _hunter(tz='UTC')
    MonthlyRecap.objects.create(profile=profile, year=2024, month=3, is_finalized=True)
    client.force_login(profile.user)

    response = client.post(reverse('api:user-timezone-update'),
                           data={'timezone': 'America/New_York'}, content_type='application/json')

    assert response.json()['changed'] is True
    assert response.json()['recaps_reset'] == 1
    assert not MonthlyRecap.objects.filter(profile=profile, is_finalized=True).exists()


def test_an_invalid_zone_confirms_nothing(client):
    """A rejected save must not leave the hunter marked as having answered -- they would never be asked
    again, and their months would stay wrong."""
    profile = _hunter(tz='UTC')
    client.force_login(profile.user)

    response = client.post(reverse('api:user-timezone-update'),
                           data={'timezone': 'Mars/Olympus_Mons'}, content_type='application/json')

    assert response.status_code == 400
    profile.user.refresh_from_db()
    assert profile.user.timezone_confirmed_at is None


# ── One picker, not two ───────────────────────────────────────────────────────


def test_the_picker_is_shared_rather_than_duplicated():
    """The utility row and the modal need the same curated zone list, the same browser detection and the
    same save path. A second copy of the zone list is DATA duplicated across two files, which diverges
    quietly and is never noticed until someone reports a missing timezone on one surface only."""
    from pathlib import Path
    root = Path(__file__).resolve().parents[2]
    module = (root / 'static' / 'js' / 'timezone-picker.js').read_text(encoding='utf-8')
    modal = (root / 'templates' / 'recap' / '_timezone_modal_js.html').read_text(encoding='utf-8')

    assert 'America/New_York' in module, 'the shared module carries no zone data'
    assert 'America/New_York' not in modal, 'the prompt keeps its own copy of the zone list'
    assert 'PlatPursuit.TimezonePicker' in modal, 'the prompt does not use the shared picker'
    # The inline utility row that was the module's other consumer is gone; the module stays because the
    # zone list is data and belongs in a file, not inlined into a template that renders one dialog.
    assert not (root / 'templates' / 'recap' / '_timezone_section_js.html').exists()
    assert not (root / 'templates' / 'recap' / '_timezone_section.html').exists()


def test_the_page_actually_DELIVERS_the_picker(client):
    """The gap that let the prompt die silently for a week.

    `test_the_picker_is_shared_rather_than_duplicated` checks that the controller REFERENCES
    `PlatPursuit.TimezonePicker`. Nothing checked that the page loads it. The script tag lived in the
    timezone utility row's partial; that row was removed as a duplicate control and the tag went with it,
    so the controller bailed on its own first line (`if (!TZ ...) return`) -- no error, no console
    warning, just a button that did nothing.

    So this asserts DELIVERY, from the rendered response, and that it arrives before the code that needs
    it. A reference test cannot see a missing <script>."""
    profile = _hunter()
    _trophy_at(profile, _utc(*_prev_month(), 12))
    client.force_login(profile.user)

    html = client.get(reverse('recap_index')).content.decode()

    assert 'timezone-picker.js' in html, 'the prompt has no picker to call, so it will bail silently'
    assert html.index('timezone-picker.js') < html.index('PlatPursuit.TimezonePicker'), (
        'the picker loads after the controller that reads it at parse time'
    )


def test_not_right_now_actually_expires():
    """The local dismissal was a permanent flag, so one close silenced the prompt on that device forever
    -- for a hunter who had never answered the question. It also made the admin's "clearing the stamp
    re-arms the prompt" false on any browser that had closed the dialog once, which is how it was found:
    the stamp was cleared on a dev account and nothing happened.

    Verified in a browser across the window: no flag opens, just-dismissed and 29 days stay quiet, 31 days
    opens, and both a legacy '1' and a garbage value read as expired -- a prompt should fail OPEN."""
    from pathlib import Path
    modal = (Path(__file__).resolve().parents[2] / 'templates' / 'recap' /
             '_timezone_modal_js.html').read_text(encoding='utf-8')

    assert 'DISMISS_DAYS' in modal, 'the dismissal never expires again'
    assert "setItem(DISMISS_KEY, String(Date.now()))" in modal, (
        'the dismissal is stored as a flag rather than a time, so it cannot expire'
    )
    assert "=== '1'" not in modal, 'the permanent-flag comparison is back'
    # Fails OPEN on anything unparseable: an unreadable dismissal must not silence the prompt for good.
    assert 'if (!at) { return false; }' in modal


def test_the_admin_does_not_promise_an_instant_re_prompt():
    """The description said clearing the stamp re-arms the prompt, full stop. On the device that
    dismissed it, that is not true for up to 30 days, and an admin hint that is confidently wrong is
    worse than none -- it sends support down the wrong path."""
    from users.admin import CustomUserAdmin
    personal = next(o for name, o in CustomUserAdmin.fieldsets if name == 'Personal Info')
    assert '30 days' in personal['description'], (
        'the admin still implies the prompt returns immediately everywhere'
    )


def test_the_prompt_falls_through_to_the_picker_when_detection_fails():
    """`Intl` can be unavailable or blocked. A confirmation dialog whose only action is "use this" with
    nothing detected is a dead control, so the absence of a guess has to open the full list instead."""
    from pathlib import Path
    modal = (Path(__file__).resolve().parents[2] / 'templates' / 'recap' /
             '_timezone_modal_js.html').read_text(encoding='utf-8')

    assert 'let picking = !guess;' in modal, 'no fallback when the browser will not report a zone'
