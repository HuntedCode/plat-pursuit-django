"""The anonymous landing (2026-08 rebuild).

What needs pinning: the data rule (cached reads and cron artifacts only -- the page must stay
~free per request), the honest hero (search wired to the real endpoints, the sync-delay promise
in copy), the showcase card's fixture fallback, and the truth rule: none of the four retired
surfaces the OLD landing marketed (Dashboard, Challenges, My Stats, Reviews) may come back.
"""
import pytest
from django.core.cache import cache
from django.test import override_settings

from core.services import landing_service
from tests.factories import ProfileFactory

pytestmark = pytest.mark.django_db

CF = {'HTTP_CF_RAY': '8f0000000000abcd-LHR'}


@pytest.fixture(autouse=True)
def _clean_cache():
    cache.clear()
    yield
    cache.clear()


def _get(client):
    return client.get('/', **CF)


# --- The page ---

def test_the_landing_renders_with_the_search_hero(client):
    body = _get(client).content.decode()

    assert 'data-land-search' in body
    assert '/api/search-sync-profile/' in body, 'the hero form does not post to the real endpoint'
    assert '/api/add-sync-status/' in body, 'the poll URL is missing'
    assert 'name="psn_username"' in body
    assert 'csrfmiddlewaretoken' in body, 'the search form cannot post without a token'


def test_the_sync_delay_is_promised_up_front(client):
    """The wait is the one thing the hero must not lie about: a first sync pulls an entire
    history through rate-limited workers, and the copy says so before anyone types a name."""
    body = _get(client).content.decode()

    assert 'entire history' in body, 'the honest sync line left the hero'


def test_no_retired_surface_is_marketed(client):
    """The old landing sold four surfaces that no longer exist. The rebuild's truth rule: if a
    visitor can't reach it, the front door doesn't sell it."""
    body = _get(client).content.decode()

    for ghost in ('dashboard-preview', 'challenge_card_example', 'stats-preview',
                  'Dashboard Modules', 'Challenge Cards', 'badge-showcase.png'):
        assert ghost not in body, f'the landing is marketing a retired surface: {ghost}'


def test_the_landing_carries_its_structured_data_and_motion_arming(client):
    body = _get(client).content.decode()

    assert 'application/ld+json' in body
    assert "classList.add('pp-arm')" in body, 'section arrival is never armed'


def test_the_heartbeat_band_hides_when_the_cron_has_not_run(client, monkeypatch):
    monkeypatch.setattr('core.views.get_cached_heartbeat', lambda: None)

    assert 'land-pulse' not in _get(client).content.decode()


def test_the_heartbeat_band_renders_the_cached_numbers(client, monkeypatch):
    fake = {
        'meta': {'computed_at': 'x', 'is_partial': False},
        'always': {
            'trophies_total': {'value': 123456, 'label': 'Trophies tracked', 'sublabel': ''},
            'trophies_24h': {'value': 789, 'label': 'Earned in last 24h', 'sublabel': ''},
            'profiles_total': {'value': 4321, 'label': 'Hunters tracked', 'sublabel': ''},
            'games_total': {'value': 999, 'label': 'Games in catalog', 'sublabel': ''},
        },
        'expanded': {
            'ratings_total': {'value': 555, 'label': 'Ratings', 'sublabel': ''},
            'rated_games': {'value': 222, 'label': 'Games rated', 'sublabel': ''},
        },
        'flavor': {'tagline': 'Built by trophy hunters, for trophy hunters.', 'numbers': ''},
    }
    monkeypatch.setattr('core.views.get_cached_heartbeat', lambda: fake)

    body = _get(client).content.decode()

    assert '123,456' in body
    assert 'data-countup="123456"' in body, 'the canonical count-up attribute is missing'
    assert '555' in body, 'the ratings figures never rendered'


def test_the_landing_is_free_once_its_caches_are_warm(client, django_assert_num_queries):
    """THE data rule, as a number: with the badge showcase cached, the anon front door costs
    zero SQL. Any regression here is a provider sneaking onto the request path."""
    cache.set(landing_service.BADGE_SHOWCASE_CACHE_KEY, [], landing_service.BADGE_SHOWCASE_TTL)
    # The art-reveal banner's cached pk is site-wide chrome, not the landing's: warm it too
    # (0 = "no live event", its own none-sentinel) so this pin measures only the landing.
    from art_reveal.services import _ACTIVE_CACHE_KEY
    cache.set(_ACTIVE_CACHE_KEY, 0, 60)

    with django_assert_num_queries(0):
        resp = _get(client)

    assert resp.status_code == 200


# --- The showcase card ---

def test_the_fixture_card_renders_when_no_cron_card_exists(client):
    body = _get(client).content.decode()

    assert 'share-image-content' in body, 'the showcase card is missing entirely'
    assert 'PlatinumPursuer' in body, 'the fixture hunter is missing'
    assert 'A sample Profile Card' in body, 'the sample note must say it is a sample'


def test_the_cron_card_replaces_the_fixture(client):
    cache.set(landing_service.SHOWCASE_CARD_CACHE_KEY,
              '<div class="share-image-content">RealHunterCard</div>',
              landing_service.SHOWCASE_CARD_TTL)

    body = _get(client).content.decode()

    # The tag itself, unescaped: the cron path round-trips Redis JSON, which strips SafeString,
    # so without |safe the real card renders as escaped source while 'RealHunterCard' alone
    # would stay green.
    assert '<div class="share-image-content">RealHunterCard</div>' in body
    assert 'PlatinumPursuer' not in body
    assert 'A sample Profile Card' not in body, 'a real card must not be captioned as a sample'


def test_the_fixture_is_literal_and_complete():
    """The fixture must render the real template with zero queries -- it is the request path's
    fallback. Rendered here rather than asserted on, per the recap fixture's rule."""
    html = landing_service._fixture_card_html.__wrapped__()

    assert 'Warden' in html
    assert 'PlatPursuit Backer' in html
    assert 'var(--' not in html


@override_settings(LANDING_SHOWCASE_PSN='')
def test_the_cron_render_skips_cleanly_when_unset():
    assert landing_service.render_showcase_card() is False
    assert cache.get(landing_service.SHOWCASE_CARD_CACHE_KEY) is None


@override_settings(LANDING_SHOWCASE_PSN='showcasehunter')
def test_the_cron_render_caches_a_real_card():
    ProfileFactory(psn_username='showcasehunter', is_linked=True, sync_status='synced')

    assert landing_service.render_showcase_card() is True
    html = cache.get(landing_service.SHOWCASE_CARD_CACHE_KEY)
    assert html and 'share-image-content' in html


# --- The badge showcase ---

def test_the_badge_showcase_dedupes_to_one_edition_per_series():
    from tests.factories import BadgeSeriesFactory, GroupBadgeFactory, PlatformGroupFactory

    series = BadgeSeriesFactory()
    pg1 = PlatformGroupFactory(key='legacy-hd', name='Legacy HD')
    pg2 = PlatformGroupFactory(key='ultra-hd', name='Ultra HD')
    GroupBadgeFactory(series=series, platform_group=pg1, is_live=True, earned_count=50)
    GroupBadgeFactory(series=series, platform_group=pg2, is_live=True, earned_count=40)
    other = BadgeSeriesFactory()
    GroupBadgeFactory(series=other, platform_group=pg1, is_live=True, earned_count=30)

    frames = landing_service._build_badge_showcase()

    names = [f['series_name'] for f in frames]
    assert len(frames) == 2, 'two editions of one series produced two showcase slots'
    assert len(set(names)) == 2
    assert all(f['state'] == 'earned' and f['art_layers'] for f in frames)
