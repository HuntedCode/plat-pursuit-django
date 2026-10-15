"""NPSSO age on the TokenKeeper dashboard (trophies/services/npsso_age.py).

The dashboard's old "Refresh Expiry" column showed the OAuth refresh token's expiry, which resets on
every login and so never said anything about the cookie that actually expires. These cover the
replacement: an age counted from each cookie's first login, kept across restarts, recorded when Sony
rejects the cookie, and never keyed or logged by the token itself.

token_keeper.py starts the TokenKeeper on import, so its wiring is checked in its source with `ast`.
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
    FIRST_SEEN_KEY, NPSSO_DANGER_DAYS, NPSSO_WARN_DAYS, REJECTED_KEY, SECONDS_PER_DAY,
    is_npsso_rejected_error, npsso_age_days, npsso_age_level, npsso_first_seen, record_npsso_login,
    record_npsso_rejected,
)
from trophies.util_modules.cache import redis_client, token_fingerprint

TROPHIES = pathlib.Path(__file__).resolve().parents[2] / 'trophies'
TOKEN_KEEPER = TROPHIES / 'token_keeper.py'
REJECTED_MESSAGE = 'Your npsso code has expired or is incorrect. Please generate a new code!'
LIVE_CLIENT_LOG = 'initialized with live client'
DAY = SECONDS_PER_DAY


@pytest.fixture
def token():
    """A fresh fake NPSSO, with its Redis keys removed afterwards."""
    value = f'fake-npsso-{uuid.uuid4().hex}'
    yield value
    fingerprint = token_fingerprint(value)
    redis_client.delete(FIRST_SEEN_KEY.format(fingerprint), REJECTED_KEY.format(fingerprint))


# --- the age itself -------------------------------------------------------------------------------

def test_the_first_login_starts_the_age_and_later_logins_keep_it(token):
    assert record_npsso_login(token, now=1000.0) == 1000.0
    assert record_npsso_login(token, now=1000.0 + 30 * DAY) == 1000.0, 'a later login made the cookie look younger'
    assert npsso_age_days(npsso_first_seen(token), now=1000.0 + 30 * DAY) == pytest.approx(30)


def test_a_cookie_first_seen_at_epoch_zero_still_has_an_age(token):
    assert record_npsso_login(token, now=0.0) == 0.0


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
def test_the_warning_level_follows_the_warning_points(days, level):
    assert npsso_age_level(days) == level


# --- rejection ------------------------------------------------------------------------------------

@pytest.mark.parametrize('exc, rejected', [
    (PSNAWPAuthenticationError(REJECTED_MESSAGE), True),
    (PSNAWPAuthenticationError('Something went wrong while authenticating'), False),
    (ValueError('token expired'), False),
])
def test_only_sonys_rejection_of_the_cookie_counts(exc, rejected):
    assert is_npsso_rejected_error(exc) is rejected


def test_a_rejection_records_the_age_once_and_logs_only_the_fingerprint(token, caplog):
    record_npsso_login(token, now=0.0)
    caplog.set_level('WARNING', logger='psn_api')

    assert record_npsso_rejected(token, now=91 * DAY) == pytest.approx(91)
    assert record_npsso_rejected(token, now=95 * DAY) == pytest.approx(95)

    stored = json.loads(redis_client.get(REJECTED_KEY.format(token_fingerprint(token))))
    assert stored['age_days'] == pytest.approx(91), 'a second report replaced the first'
    lines = [r.getMessage() for r in caplog.records if 'rejected by Sony' in r.getMessage()]
    assert len(lines) == 1
    assert token_fingerprint(token)[:12] in lines[0]
    assert token not in caplog.text


# --- the token stays secret -----------------------------------------------------------------------

def test_the_token_itself_is_never_stored(token):
    record_npsso_login(token)
    record_npsso_rejected(token)

    fingerprint = token_fingerprint(token)
    for key in (FIRST_SEEN_KEY.format(fingerprint), REJECTED_KEY.format(fingerprint)):
        assert token not in key
        assert token.encode() not in redis_client.get(key)


def test_the_fingerprint_is_the_audit_logs_token_id(token):
    """log_api_call now uses token_fingerprint; APIAuditLog rows written before must still match."""
    assert token_fingerprint(token) == hashlib.sha256(token.encode()).hexdigest()


SECRET_NAMES = {'token', 'npsso', 'npsso_cookie'}


def _secret_values_logged(path):
    """The logger calls in `path`, and (line, name) for every token-like value one of them uses
    without passing it through token_fingerprint()."""
    tree = ast.parse(path.read_text(encoding='utf-8'))
    calls = [
        n for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
        and isinstance(n.func.value, ast.Name) and n.func.value.id == 'logger'
    ]
    leaks = []
    for call in calls:
        fingerprinted = {
            id(inner)
            for c in ast.walk(call)
            if isinstance(c, ast.Call) and getattr(c.func, 'id', None) == 'token_fingerprint'
            for inner in ast.walk(c)
        }
        for arg in [*call.args, *(k.value for k in call.keywords)]:
            for node in ast.walk(arg):
                name = getattr(node, 'id', None) or getattr(node, 'attr', None)
                if name in SECRET_NAMES and id(node) not in fingerprinted:
                    leaks.append((call.lineno, name))
    return calls, leaks


@pytest.mark.parametrize('path', [TOKEN_KEEPER, TROPHIES / 'services' / 'npsso_age.py'], ids=lambda p: p.name)
def test_no_log_line_carries_a_token(path):
    """A logged NPSSO is a working PSN login for anyone who can read the logs. One startup warning
    in token_keeper.py printed it in full."""
    calls, leaks = _secret_values_logged(path)
    assert calls, f'found no logger calls in {path.name}; the matcher is broken'
    assert not leaks, f'{path.name} logs a token at ' + ', '.join(f'line {line} ({name})' for line, name in leaks)


def test_the_log_check_sees_every_way_of_passing_a_token(tmp_path):
    source = tmp_path / 'leaky.py'
    source.write_text(
        'logger.warning(f"{token}")\n'
        'logger.warning(f"{token[:20]}")\n'
        'logger.warning("%s", token)\n'
        'logger.warning("x %s" % inst.token)\n'
        'logger.warning("{}".format(npsso))\n'
        'logger.info(f"ok {token_fingerprint(token)[:12]}")\n',
        encoding='utf-8',
    )
    _, leaks = _secret_values_logged(source)
    assert [line for line, _ in leaks] == [1, 2, 3, 4, 5]


# --- never costs a login --------------------------------------------------------------------------

class _BrokenRedis:
    def __getattr__(self, name):
        def fail(*args, **kwargs):
            raise ConnectionError('redis is down')
        return fail


def test_a_redis_outage_never_costs_a_login_and_shows_no_age(token, monkeypatch, caplog):
    """These run inside the TokenKeeper's login try blocks: raising would mark a working client
    as failed. With no answer from Redis the age is unknown, not a brand-new 0 days."""
    monkeypatch.setattr(npsso_age, 'redis_client', _BrokenRedis())
    caplog.set_level('WARNING', logger='psn_api')

    assert record_npsso_login(token, now=5.0) is None
    assert npsso_first_seen(token) is None
    assert record_npsso_rejected(token) is None
    assert 'rejected by Sony at an unknown age' in caplog.text


def test_a_missing_token_never_raises():
    """record_npsso_rejected runs inside the TokenKeeper's except handlers, where raising would crash
    startup or the health loop."""
    assert record_npsso_login(None) is None
    assert npsso_first_seen(None) is None
    assert record_npsso_rejected(None) is None


# --- token_keeper.py wiring -----------------------------------------------------------------------

def _function(name):
    tree = ast.parse(TOKEN_KEEPER.read_text(encoding='utf-8'))
    found = next((n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == name), None)
    assert found is not None, f'{name} is gone from token_keeper.py; repoint this test'
    return found


def _names_called(nodes):
    return {
        getattr(c.func, 'id', None) or getattr(c.func, 'attr', None)
        for node in nodes for c in ast.walk(node) if isinstance(c, ast.Call)
    }


def _strings_in(nodes):
    return ' '.join(
        part.value for node in nodes for part in ast.walk(node)
        if isinstance(part, ast.Constant) and isinstance(part.value, str)
    )


def _login_try(function):
    """The one try block in `function` that builds a ProxiedPSNAWP client, and the statements that
    follow it in the same block."""
    for node in ast.walk(function):
        for field in ('body', 'orelse', 'finalbody'):
            statements = getattr(node, field, None)
            if not isinstance(statements, list):
                continue
            for i, stmt in enumerate(statements):
                if isinstance(stmt, ast.Try) and 'ProxiedPSNAWP' in _names_called(stmt.body):
                    return stmt, statements[i + 1:]
    raise AssertionError(f'no client-building try in {function.name}; repoint this test')


def _handler(try_node, exception_name):
    handler = next((h for h in try_node.handlers if getattr(h.type, 'id', None) == exception_name), None)
    assert handler is not None, f'no `except {exception_name}` on the client-building try'
    return handler


@pytest.mark.parametrize('function_name', ['initialize_groups', '_check_and_refresh'])
def test_every_client_build_records_logins_and_rejections(function_name):
    block, _ = _login_try(_function(function_name))
    handler = _handler(block, 'Exception')

    assert 'record_npsso_login' in _names_called(block.body)
    assert {'is_npsso_rejected_error', 'record_npsso_rejected'} <= _names_called(handler.body)


def test_startup_only_reports_a_live_client_when_it_is_one():
    """A failed start used to log "initialized with live client" and a 200 anyway, exactly when an
    NPSSO had died."""
    block, after = _login_try(_function('initialize_groups'))
    handler = _handler(block, 'Exception')

    assert LIVE_CLIENT_LOG in _strings_in(block.body)
    assert LIVE_CLIENT_LOG not in _strings_in(handler.body)
    assert LIVE_CLIENT_LOG not in _strings_in(after), 'the live-client log runs after a failed start again'
    assert 'log_api_call' not in _names_called(after), 'the client_init audit row is written outside the try again'
    assert 'Init error' in _strings_in(handler.body), 'a failed start no longer sets last_error'


# --- the dashboard --------------------------------------------------------------------------------

@pytest.mark.django_db
def test_the_dashboard_shows_each_cookies_age_and_warning(client):
    key = 'token_keeper_latest_stats:npsso-age-test'
    instances = {
        '0-0': {'npsso_age_days': 12.34, 'npsso_age_level': 'ok'},
        '0-1': {'npsso_age_days': 80.0, 'npsso_age_level': 'warn'},
        '0-2': {'npsso_age_days': 90.0, 'npsso_age_level': 'danger'},
        '0-3': {'npsso_age_days': None, 'npsso_age_level': 'unknown'},
        '0-4': {},  # an older worker's stats, from before this column existed
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
    assert html.count('Unknown') >= 2
