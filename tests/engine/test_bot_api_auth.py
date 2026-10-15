"""The Discord bot's identity, and its exemption from rate limits.

The bot calls PlatPursuit for every Discord member through its one account. DRF's per-user
throttle (60 a minute) therefore made all of them share one bucket, and on a busy minute every
member's commands failed together. `api.permissions.is_discord_bot` is now the one answer to "is
this the bot?", used by the permission, the django-ratelimit exemption and the throttle.
"""
import pytest
from django.contrib.auth.models import AnonymousUser
from django.test import RequestFactory, override_settings
from django.urls import reverse
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from api.permissions import is_discord_bot
from api.views import bot_exempt_rate
from core.throttling import VisitorUserRateThrottle
from tests.factories import UserFactory, bot_api_client

pytestmark = pytest.mark.django_db


def _with_token(key):
    request = RequestFactory().get('/')
    request.auth = type('Token', (), {'key': key})() if key is not None else None
    return request


# ── who is the bot ─────────────────────────────────────────────────────────────────────────────

def test_the_bot_token_is_the_bot(settings):
    assert is_discord_bot(_with_token(settings.BOT_API_KEY))


@pytest.mark.parametrize('key', ['someone-elses-token', '', None])
def test_anything_else_is_not(key):
    assert not is_discord_bot(_with_token(key))


def test_a_request_with_no_auth_attribute_is_not(rf):
    assert not is_discord_bot(rf.get('/'))


@override_settings(BOT_API_KEY=None)
def test_nothing_is_the_bot_when_no_key_is_configured():
    assert not is_discord_bot(_with_token('None'))
    assert not is_discord_bot(_with_token(''))


def test_the_bot_endpoints_still_refuse_other_accounts():
    user = UserFactory()
    token = Token.objects.create(user=user)
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f'Token {token.key}')

    resp = client.get(reverse('api:check-linked'), {'discord_id': '1'})

    assert resp.status_code == 403


def test_the_ratelimit_exemption_uses_the_same_answer(settings):
    rate = bot_exempt_rate('5/m')
    assert rate('group', _with_token(settings.BOT_API_KEY)) is None
    assert rate('group', _with_token('someone-elses-token')) == '5/m'


# ── the throttle ───────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def two_per_minute(monkeypatch):
    from django.core.cache import cache

    cache.clear()
    monkeypatch.setattr(VisitorUserRateThrottle, 'THROTTLE_RATES', {'user': '2/min'})
    yield
    cache.clear()


def test_the_bot_is_never_throttled(two_per_minute):
    """Many members' commands in one minute all arrive as the bot."""
    client = bot_api_client()
    statuses = {client.get(reverse('api:check-linked'), {'discord_id': str(n)}).status_code for n in range(6)}
    assert 429 not in statuses


def test_everyone_else_still_is(two_per_minute):
    client = APIClient()
    url = reverse('api:community-stats-today')
    statuses = [client.get(url, REMOTE_ADDR='9.9.9.9').status_code for _ in range(3)]
    assert statuses[-1] == 429, statuses


def test_the_throttle_asks_the_shared_helper(rf, settings):
    request = _with_token(settings.BOT_API_KEY)
    request.user = AnonymousUser()
    assert VisitorUserRateThrottle().allow_request(request, view=None) is True
