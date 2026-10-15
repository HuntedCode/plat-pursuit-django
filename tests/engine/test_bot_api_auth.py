"""The Discord bot's identity, and its own throttle ceiling.

The bot calls PlatPursuit for every Discord member through its one account. DRF's per-user
throttle (60 a minute) therefore made all of them share one bucket, and on a busy minute every
member's commands failed together. The bot now has its own, much higher bucket; it keeps one at
all because it is the bot's only hard limit (a leaked key, a retry loop). `core.bot_auth
.is_discord_bot` is the one answer to "is this the bot?", used by the permission, the
django-ratelimit exemption and the throttle.
"""
import pytest
from django.test import RequestFactory, override_settings
from django.urls import reverse
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from core.bot_auth import is_discord_bot
from api.views import bot_exempt_rate
from core.throttling import BOT_SCOPE, VisitorUserRateThrottle
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


@override_settings(BOT_API_KEY='clé-du-bot')
def test_a_non_ascii_key_compares_instead_of_crashing():
    """hmac.compare_digest refuses non-ASCII str; a bad env value must mismatch, not 500."""
    assert is_discord_bot(_with_token('clé-du-bot'))
    assert not is_discord_bot(_with_token('someone-elses-token'))


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
def small_rates(monkeypatch):
    """Rates small enough to hit in a test: 2 a minute for users, 4 for the bot."""
    from django.core.cache import cache

    cache.clear()
    monkeypatch.setattr(VisitorUserRateThrottle, 'THROTTLE_RATES', {'user': '2/min', BOT_SCOPE: '4/min'})
    yield
    cache.clear()


def _check_linked_statuses(client, count):
    return [client.get(reverse('api:check-linked'), {'discord_id': str(n)}).status_code for n in range(count)]


def test_the_bot_gets_its_own_higher_ceiling(small_rates):
    """Many members' commands in a minute arrive as the bot: past the user rate, they still pass."""
    assert _check_linked_statuses(bot_api_client(), 4) == [200] * 4


def test_the_bot_is_still_capped(small_rates):
    """Its only hard limit: a leaked key or a retry loop in PlatBot must not run unbounded."""
    assert _check_linked_statuses(bot_api_client(), 5)[-1] == 429


def test_everyone_else_keeps_the_user_rate(small_rates):
    client = APIClient()
    url = reverse('api:community-stats-today')
    statuses = [client.get(url, REMOTE_ADDR='9.9.9.9').status_code for _ in range(3)]
    assert statuses[-1] == 429, statuses


def test_the_production_bot_rate_is_well_above_the_user_rate():
    from django.conf import settings

    rates = settings.REST_FRAMEWORK['DEFAULT_THROTTLE_RATES']
    bot, _ = VisitorUserRateThrottle().parse_rate(rates[BOT_SCOPE])
    user, _ = VisitorUserRateThrottle().parse_rate(rates['user'])
    assert bot >= 10 * user
