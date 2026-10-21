"""An account's timezone defaults to its browser's, not UTC (owner, 2026-10-17).

New accounts take the browser's zone at signup; existing accounts still on the untouched UTC default take it once,
silently, on their next visit. Neither is a confirmation: `timezone_confirmed_at` stays null, so the Recap's prompt
still asks. One writer (`users.services.timezone_service`), and a guess can never overwrite a zone a hunter chose.
"""
import json

import pytest
from django.urls import reverse
from django.utils import timezone

from tests.factories import ProfileFactory, UserFactory
from trophies.models import MonthlyRecap
from users.models import CustomUser
from users.services import timezone_service as tz

pytestmark = pytest.mark.django_db


def _user(**fields):
    user = UserFactory()
    CustomUser.objects.filter(pk=user.pk).update(**{'user_timezone': 'UTC', 'timezone_confirmed_at': None, **fields})
    user.refresh_from_db()
    return user


# --- the writer -------------------------------------------------------------------------------------------

def test_a_detected_zone_is_adopted_by_an_untouched_account_without_confirming_it():
    user = _user()
    assert tz.adopt_detected_timezone(user, 'America/New_York') is True
    user.refresh_from_db()
    assert user.user_timezone == 'America/New_York' and user.timezone_confirmed_at is None


@pytest.mark.parametrize('fields', [
    {'timezone_confirmed_at': timezone.now()},                 # they answered (even if the answer was UTC)
    {'user_timezone': 'Europe/London'},                        # they chose a zone already
])
def test_a_guess_never_overwrites_a_choice(fields):
    user = _user(**fields)
    before = user.user_timezone
    assert tz.adopt_detected_timezone(user, 'Asia/Tokyo') is False
    user.refresh_from_db()
    assert user.user_timezone == before


@pytest.mark.parametrize('zone', ['UTC', 'Not/AZone', '', None, 42, 'Etc/GMT+5'])
def test_only_a_real_listed_zone_other_than_utc_is_adopted(zone):
    user = _user()
    assert tz.adopt_detected_timezone(user, zone) is False
    user.refresh_from_db()
    assert user.user_timezone == 'UTC'


def test_the_guard_reads_the_database_not_the_stale_object():
    # Another tab confirmed a zone a moment ago; this request's user object is stale but must still lose.
    user = _user()
    CustomUser.objects.filter(pk=user.pk).update(user_timezone='Europe/Paris', timezone_confirmed_at=timezone.now())
    assert tz.adopt_detected_timezone(user, 'Asia/Tokyo') is False
    assert CustomUser.objects.get(pk=user.pk).user_timezone == 'Europe/Paris'


def test_adopting_a_zone_re_renders_finalized_recaps():
    user = _user()
    profile = ProfileFactory(user=user)
    MonthlyRecap.objects.create(profile=profile, year=2026, month=9, is_finalized=True)
    tz.adopt_detected_timezone(user, 'America/Chicago')
    assert not MonthlyRecap.objects.filter(profile=profile, is_finalized=True).exists()


def test_a_chosen_zone_is_still_a_confirmation():
    user = _user()
    tz.set_user_timezone(user, 'UTC')
    user.refresh_from_db()
    assert user.timezone_confirmed_at is not None
    # ... and a confirmed answer of UTC keeps the detection away for good.
    assert tz.adopt_detected_timezone(user, 'Asia/Tokyo') is False


# --- signup -----------------------------------------------------------------------------------------------

def _signup(client, email, **extra):
    return client.post('/accounts/signup/', {
        'email': email, 'password1': 'a-perfectly-fine-pass-1', 'website': '', **extra,
    })


def test_a_new_account_starts_on_its_browsers_zone_unconfirmed(client):
    resp = _signup(client, 'tz-a@example.com', browser_timezone='Australia/Sydney')
    assert resp.status_code == 302
    user = CustomUser.objects.get(email='tz-a@example.com')
    assert user.user_timezone == 'Australia/Sydney' and user.timezone_confirmed_at is None


@pytest.mark.parametrize('extra', [{}, {'browser_timezone': 'Not/AZone'}, {'browser_timezone': ''}])
def test_a_new_account_without_a_usable_zone_keeps_utc(client, extra):
    assert _signup(client, 'tz-b@example.com', **extra).status_code == 302
    assert CustomUser.objects.get(email='tz-b@example.com').user_timezone == 'UTC'


def test_the_signup_page_fills_the_zone_from_the_browser(client):
    html = client.get('/accounts/signup/').content.decode()
    assert 'name="browser_timezone" id="id_browser_timezone"' in html
    assert "Intl.DateTimeFormat().resolvedOptions().timeZone" in html


# --- existing accounts: the detect door and the base page's script -----------------------------------------

def _detect(client, zone):
    return client.post(reverse('api:user-timezone-detect'), data=json.dumps({'timezone': zone}),
                       content_type='application/json')


def test_the_detect_door_adopts_for_an_untouched_account(client):
    user = _user()
    client.force_login(user)
    resp = _detect(client, 'America/Denver')
    assert resp.status_code == 200 and json.loads(resp.content) == {'adopted': True, 'timezone': 'America/Denver'}
    user.refresh_from_db()
    assert user.timezone_confirmed_at is None


def test_the_detect_door_says_no_without_touching_a_choice(client):
    user = _user(user_timezone='Europe/Berlin', timezone_confirmed_at=timezone.now())
    client.force_login(user)
    body = json.loads(_detect(client, 'America/Denver').content)
    assert body == {'adopted': False, 'timezone': 'Europe/Berlin'}


def test_the_detect_door_needs_a_signed_in_hunter(client):
    assert _detect(client, 'America/Denver').status_code in (401, 403)


def _base_script(client, user):
    ProfileFactory(user=user)   # My Challenges needs a linked profile to render rather than redirect
    client.force_login(user)
    return 'pp-tz-detect-asked' in client.get(reverse('my_challenges')).content.decode()


def test_the_base_page_asks_only_for_an_untouched_account(client):
    assert _base_script(client, _user())
    assert not _base_script(client, _user(user_timezone='Europe/Rome'))
    assert not _base_script(client, _user(timezone_confirmed_at=timezone.now()))


def test_a_signed_out_visitor_carries_no_detection(client):
    assert 'pp-tz-detect-asked' not in client.get('/accounts/login/').content.decode()



@pytest.mark.parametrize('reported, stored', [
    ('Asia/Calcutta', 'Asia/Kolkata'), ('Europe/Kiev', 'Europe/Kyiv'),
    ('America/Buenos_Aires', 'America/Argentina/Buenos_Aires'), ('Asia/Saigon', 'Asia/Ho_Chi_Minh'),
])
def test_an_old_name_a_browser_reports_is_stored_under_its_current_name(reported, stored):
    # Chrome and Safari still report these legacy links; the field's choices only hold the current names.
    user = _user()
    assert tz.adopt_detected_timezone(user, reported) is True
    user.refresh_from_db()
    assert user.user_timezone == stored


def test_every_legacy_mapping_lands_on_a_name_the_field_accepts():
    import pytz
    assert all(new in pytz.common_timezones_set for new in tz.LEGACY_ZONE_NAMES.values())
    assert not any(old in pytz.common_timezones_set for old in tz.LEGACY_ZONE_NAMES)


def test_a_new_account_reporting_an_old_name_gets_the_current_one(client):
    assert _signup(client, 'tz-c@example.com', browser_timezone='Asia/Calcutta').status_code == 302
    assert CustomUser.objects.get(email='tz-c@example.com').user_timezone == 'Asia/Kolkata'


def test_the_asked_flag_is_per_account_so_a_shared_browser_still_asks_the_next(client):
    user = _user()
    ProfileFactory(user=user)
    client.force_login(user)
    html = client.get(reverse('my_challenges')).content.decode()
    assert "var KEY = 'pp-tz-detect-asked:%d';" % user.pk in html



@pytest.mark.parametrize('body', [{'timezone': 42}, {'timezone': ['Asia/Tokyo']}, {'timezone': None}, ['Asia/Tokyo']])
def test_a_crafted_detect_body_is_a_calm_no_never_a_500(client, body):
    user = _user()
    client.force_login(user)
    resp = client.post(reverse('api:user-timezone-detect'), data=json.dumps(body), content_type='application/json')
    assert resp.status_code == 200 and json.loads(resp.content)['adopted'] is False
    user.refresh_from_db()
    assert user.user_timezone == 'UTC'
