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


def test_the_ratings_demo_card_is_live_html(client):
    """The demo rating is the site's own vocabulary as real markup (shared .pp-stars, tone
    colours, the model's verdict words), captioned as a sample -- not a screenshot."""
    body = _get(client).content.decode()

    assert 'land-rdemo__card' in body
    assert 'pp-stars' in body, 'the shared fractional-star component is missing'
    assert 'Do it' in body, "the verdict pill must use the model's real label"
    assert 'A sample rating. Real ones require the platinum.' in body

    # The words must be the REAL vocabulary for the numbers shown -- the demo teaches the
    # product's language, and 'Breezy' beside a 3 already slipped through once.
    from core.templatetags.custom_filters import rating_verdict
    assert rating_verdict(9, 'difficulty') in body
    assert rating_verdict(3, 'grindiness') in body
    assert rating_verdict(9, 'fun') in body


def test_real_ratings_replace_the_sample_carousel(client):
    """With the cron cache warm, the carousel renders the hunter's real ratings (all slides +
    dots) and drops the sample caption."""
    cache.set(landing_service.SHOWCASE_RATINGS_CACHE_KEY, [
        {'title': f'Real Game {i}', 'stars_pct': 90, 'overall': '4.5',
         'stats': [{'k': 'Fun', 'n': '9', 'of': '/10', 'tone': 'good', 'word': 'A blast'}],
         'take': 'A real quick take.', 'rec': 'worth_it', 'rec_label': 'Do it'}
        for i in range(3)
    ], landing_service.SHOWCASE_RATINGS_TTL)

    body = _get(client).content.decode()

    assert 'Real Game 0' in body and 'Real Game 2' in body
    assert body.count('land-rdemo__dot ') + body.count('land-rdemo__dot"') >= 3, 'the dots are missing'
    assert 'A sample rating.' not in body
    assert 'A real rating. Every one requires the platinum.' in body
    # The fixture SLIDE by its own take -- bare 'Sekiro' also matches the fixture Profile
    # Card's rarest-platinum line, which legitimately renders on this page.
    assert 'Brutal bosses, zero filler.' not in body, 'the fixture slide leaked in beside real ratings'


def test_the_cron_gathers_blurbed_ratings_positive_first():
    """The selection rule: blurbs required, worth_it first, topped up with others, capped."""
    from django.test import override_settings
    from tests.factories import UserConceptRatingFactory

    profile = ProfileFactory(psn_username='ratinghunter', is_linked=True)
    for i in range(4):
        UserConceptRatingFactory(profile=profile, recommendation='worth_it',
                                 blurb=f'Great platinum number {i}.', overall_rating=4.5)
    UserConceptRatingFactory(profile=profile, recommendation='skip',
                             blurb='Not this one.', overall_rating=2.0)
    UserConceptRatingFactory(profile=profile, recommendation='worth_it', blurb='',
                             overall_rating=5.0)   # blurbless: never a slide

    UserConceptRatingFactory(profile=profile, recommendation='worth_it', overall_rating=5.0,
                             blurb='Moderated away.', blurb_hidden=True)   # NEVER a slide

    with override_settings(LANDING_SHOWCASE_PSN='ratinghunter'):
        assert landing_service.render_showcase_ratings() is True

    cards = cache.get(landing_service.SHOWCASE_RATINGS_CACHE_KEY)
    assert len(cards) == 5
    assert [c['rec'] for c in cards[:4]] == ['worth_it'] * 4, 'positive verdicts must lead'
    assert cards[4]['rec'] == 'skip', 'the top-up slide is missing'
    assert all(c['take'] for c in cards)
    assert all(c['take'] != 'Moderated away.' for c in cards), 'a hidden blurb reached the front door'
    # The words are the real filters' output, precomputed cron-side.
    assert all(st['tone'] in ('good', 'warn', 'bad', 'high') for c in cards for st in c['stats'])


def test_the_inspect_modal_shell_ships_with_the_page(client):
    """The badge quick-peek: the same anon-safe machinery badge detail uses, plus the hint that
    makes the affordance discoverable."""
    body = _get(client).content.decode()

    assert 'id="badge-peek"' in body, 'the inspect modal shell is missing'
    assert '/group-badge-peek/0/' in body, 'the anon quick-peek URL template is missing'


def test_the_shelf_slots_are_inspect_triggers(client):
    """With real badges on the shelf, each slot carries the trigger contract (role, key, id)
    and the hint invites the tap."""
    from tests.factories import BadgeSeriesFactory, GroupBadgeFactory, PlatformGroupFactory

    GroupBadgeFactory(series=BadgeSeriesFactory(badge_image='badges/custom-art.png'),
                      platform_group=PlatformGroupFactory(key='ultra-hd', name='Ultra HD'),
                      is_live=True, earned_count=10)
    cache.delete(landing_service.BADGE_SHOWCASE_CACHE_KEY)

    body = _get(client).content.decode()

    assert 'land-medals__slot pp-forge-peek' in body
    assert 'data-badge-id=' in body, 'the trigger has no badge id to open'
    assert 'Click or tap a medallion to inspect it' in body, 'the hint is missing'


# --- The badge showcase ---

def test_the_badge_showcase_dedupes_to_one_edition_per_series():
    from tests.factories import BadgeSeriesFactory, GroupBadgeFactory, PlatformGroupFactory

    series = BadgeSeriesFactory(badge_image='badges/custom-a.png')
    pg1 = PlatformGroupFactory(key='legacy-hd', name='Legacy HD')
    pg2 = PlatformGroupFactory(key='ultra-hd', name='Ultra HD')
    GroupBadgeFactory(series=series, platform_group=pg1, is_live=True, earned_count=50)
    GroupBadgeFactory(series=series, platform_group=pg2, is_live=True, earned_count=40)
    other = BadgeSeriesFactory(badge_image='badges/custom-b.png')
    GroupBadgeFactory(series=other, platform_group=pg1, is_live=True, earned_count=30)

    frames = landing_service._build_badge_showcase()

    names = [f['series_name'] for f in frames]
    assert len(frames) == 2, 'two editions of one series produced two showcase slots'
    assert len(set(names)) == 2
    assert all(f['state'] == 'earned' and f['art_layers'] for f in frames)
    assert all(f['badge_id'] for f in frames), 'a frame without badge_id cannot open its peek'


def test_the_showcase_refuses_default_art_and_non_series_types():
    """The shelf is the handcrafted-art pitch: a default-subject medallion or a
    franchise/developer badge beside "illustrated by hand" undercuts the claim."""
    from tests.factories import BadgeSeriesFactory, GroupBadgeFactory, PlatformGroupFactory

    pg = PlatformGroupFactory(key='ultra-hd', name='Ultra HD')
    # No custom art anywhere: the medallion would draw the static default subject.
    GroupBadgeFactory(series=BadgeSeriesFactory(), platform_group=pg, is_live=True, earned_count=99)
    # Custom art, wrong type.
    GroupBadgeFactory(series=BadgeSeriesFactory(badge_type='franchise', badge_image='badges/f.png'),
                      platform_group=pg, is_live=True, earned_count=98)
    # The one that belongs: series-type, custom art (via the per-group override this time).
    keeper = GroupBadgeFactory(series=BadgeSeriesFactory(),
                               platform_group=pg, is_live=True, earned_count=1,
                               badge_image_override='badges/override.png')

    frames = landing_service._build_badge_showcase()

    assert [f['badge_id'] for f in frames] == [keeper.id]
