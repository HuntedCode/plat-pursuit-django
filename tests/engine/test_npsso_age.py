"""NPSSO age on the TokenKeeper dashboard (trophies/services/npsso_age.py).

The dashboard's old "Refresh Expiry" column showed the OAuth refresh token's expiry, which resets on
every login and so never said anything about the cookie that actually expires. These cover the
replacement: an age counted from each cookie's first login, kept across restarts, recorded when the
cookie dies, and never keyed or logged by the token itself.
"""
import ast
import hashlib
import json
import pathlib
import uuid

import pytest
from django.urls import reverse
from psnawp_api.core.psnawp_exceptions import PSNAWPAuthenticationError

from tests.factories import UserFactory
from trophies.services import npsso_age
from trophies.services.npsso_age import (
    EXPIRED_KEY, FIRST_SEEN_KEY, NPSSO_DANGER_DAYS, NPSSO_WARN_DAYS, SECONDS_PER_DAY,
    is_npsso_expired_error, npsso_age_days, npsso_age_level, npsso_first_seen, record_npsso_expired,
    record_npsso_login,
)
from trophies.util_modules.cache import redis_client, token_fingerprint

TOKEN_KEEPER = pathlib.Path(__file__).resolve().parents[2] / 'trophies' / 'token_keeper.py'
EXPIRED_MESSAGE = 'Your npsso code has expired or is incorrect. Please generate a new code!'
DAY = SECONDS_PER_DAY


@pytest.fixture
def token():
    """A fresh fake NPSSO, with its Redis keys removed afterwards."""
    value = f'fake-npsso-{uuid.uuid4().hex}'
    yield value
    fingerprint = token_fingerprint(value)
    redis_client.delete(FIRST_SEEN_KEY.format(fingerprint), EXPIRED_KEY.format(fingerprint))


def test_the_first_login_starts_the_age_and_later_logins_keep_it(token):
    assert record_npsso_login(token, now=1000.0) == 1000.0
    assert record_npsso_login(token, now=1000.0 + 30 * DAY) == 1000.0, 'a later login made the cookie look younger'
    assert npsso_age_days(npsso_first_seen(token), now=1000.0 + 30 * DAY) == pytest.approx(30)


def test_a_cookie_that_never_logged_in_has_no_age(token):
    assert npsso_first_seen(token) is None
    assert npsso_age_days(None) is None
    assert npsso_age_level(None) == 'unknown'


@pytest.mark.parametrize('days, level', [
    (0, 'ok'),
    (NPSSO_WARN_DAYS - 0.01, 'ok'),
    (NPSSO_WARN_DAYS, 'warn'),
    (NPSSO_DANGER_DAYS - 0.01, 'warn'),
    (NPSSO_DANGER_DAYS, 'danger'),
])
def test_the_dashboard_level_follows_the_warning_points(days, level):
    assert npsso_age_level(days) == level


@pytest.mark.parametrize('exc, expired', [
    (PSNAWPAuthenticationError(EXPIRED_MESSAGE), True),
    (PSNAWPAuthenticationError('Something went wrong while authenticating'), False),
    (ValueError('token expired'), False),
])
def test_only_sonys_rejection_of_the_cookie_counts_as_expiry(exc, expired):
    assert is_npsso_expired_error(exc) is expired


def test_an_expiry_records_the_age_once_and_logs_only_the_fingerprint(token, caplog):
    record_npsso_login(token, now=0.0)
    caplog.set_level('WARNING', logger='psn_api')

    assert record_npsso_expired(token, now=91 * DAY) == pytest.approx(91)
    assert record_npsso_expired(token, now=95 * DAY) == pytest.approx(95)

    stored = json.loads(redis_client.get(EXPIRED_KEY.format(token_fingerprint(token))))
    assert stored['age_days'] == pytest.approx(91), 'a second report replaced the first'
    expired_lines = [r.getMessage() for r in caplog.records if 'expired at' in r.getMessage()]
    assert len(expired_lines) == 1
    assert token_fingerprint(token)[:12] in expired_lines[0]
    assert token not in caplog.text


def test_the_token_itself_is_never_stored(token):
    record_npsso_login(token)
    record_npsso_expired(token)

    fingerprint = token_fingerprint(token)
    for key in (FIRST_SEEN_KEY.format(fingerprint), EXPIRED_KEY.format(fingerprint)):
        assert token not in key
        assert token.encode() not in redis_client.get(key)


def test_the_fingerprint_is_the_audit_logs_token_id(token):
    """log_api_call now uses token_fingerprint; APIAuditLog rows written before must still match."""
    assert token_fingerprint(token) == hashlib.sha256(token.encode()).hexdigest()


class _BrokenRedis:
    def __getattr__(self, name):
        def fail(*args, **kwargs):
            raise ConnectionError('redis is down')
        return fail


def test_a_redis_outage_never_costs_a_login(token, monkeypatch, caplog):
    """These run inside the TokenKeeper's login try blocks: raising would mark a working client
    as failed."""
    monkeypatch.setattr(npsso_age, 'redis_client', _BrokenRedis())
    caplog.set_level('WARNING', logger='psn_api')

    assert record_npsso_login(token, now=5.0) == 5.0
    assert npsso_first_seen(token) is None
    assert record_npsso_expired(token) is None
    assert 'expired at an unknown age' in caplog.text


def _logger_calls_in_token_keeper():
    tree = ast.parse(TOKEN_KEEPER.read_text(encoding='utf-8'))
    return [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name) and node.func.value.id == 'logger'
    ]


def test_the_token_keeper_never_logs_a_token():
    """A logged NPSSO is a working PSN login for anyone who can read the logs. One startup warning
    printed it in full."""
    calls = _logger_calls_in_token_keeper()
    assert len(calls) > 50, f'found only {len(calls)} logger calls; the matcher is broken'
    for call in calls:
        for node in ast.walk(call):
            if isinstance(node, ast.FormattedValue):
                value = node.value
                named_token = (isinstance(value, ast.Name) and value.id == 'token') or (
                    isinstance(value, ast.Attribute) and value.attr == 'token'
                )
                assert not named_token, f'token_keeper.py:{call.lineno} logs a token'


@pytest.mark.django_db
def test_the_dashboard_shows_each_cookies_age_and_warning(client):
    key = 'token_keeper_latest_stats:npsso-age-test'
    instances = {
        '0-0': {'npsso_age_days': 12.34, 'npsso_age_level': 'ok'},
        '0-1': {'npsso_age_days': 80.0, 'npsso_age_level': 'warn'},
        '0-2': {'npsso_age_days': 90.0, 'npsso_age_level': 'danger'},
        '0-3': {'npsso_age_days': None, 'npsso_age_level': 'unknown'},
    }
    redis_client.set(key, json.dumps({'machine_id': 'npsso-age-test', 'instances': instances}), ex=60)
    user = UserFactory()
    user.is_superuser = user.is_staff = True
    user.save()
    client.force_login(user)
    try:
        html = client.get(reverse('token_monitoring')).content.decode()
    finally:
        redis_client.delete(key)

    assert 'NPSSO Age (days)' in html
    assert 'Refresh Expiry' not in html
    assert 'badge-ghost">12.3<' in html
    assert 'badge-warning">80.0<' in html
    assert 'badge-error">90.0<' in html
    assert 'Unknown' in html
