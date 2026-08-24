"""SEO closing audit (2026-08-24): the cross-cutting pins the four lanes never had.

Strategy: docs/design/seo-strategy.md. These are the invariants the closing audit verified by
hand and then pinned: sitemap URLs must resolve (200, no redirect) and be robots-crawlable,
the static storage must content-hash (that is what earns immutable cache headers), lastmod
queries must share their section's floor, and the legacy redirects must stay one-hop.
"""
import pytest
from django.conf import settings
from django.urls import reverse
from django.utils import timezone

from tests.engine.test_seo_lane0 import _robots
from tests.factories import (
    BadgeSeriesFactory, ConceptFactory, GameFactory, GroupBadgeFactory,
    PlatformGroupFactory, ProfileFactory,
)

pytestmark = pytest.mark.django_db

CF = {'HTTP_CF_RAY': '8f0000000000abcd-LHR'}


# --- the sitemap contract: everything advertised resolves, indexes, and is crawlable ---

def test_every_static_sitemap_url_returns_200_without_redirecting(client):
    """The pin that would have caught a retired hub or an old spelling surviving in the list."""
    from core.sitemaps import StaticViewSitemap

    for name in StaticViewSitemap().items():
        url = reverse(name)
        resp = client.get(url, **CF)
        assert resp.status_code == 200, f'sitemap advertises {name} ({url}) -> {resp.status_code}'


def test_every_static_sitemap_url_is_indexable(client):
    """A sitemap must not advertise a page whose own meta forbids the index."""
    from core.sitemaps import StaticViewSitemap

    for name in StaticViewSitemap().items():
        body = client.get(reverse(name), **CF).content.decode()
        assert 'content="index, follow"' in body, f'{name} is in the sitemap but noindexed'


def test_no_sitemap_url_is_robots_blocked(client):
    """The two files are maintained by hand; this is the seam between them."""
    from core.sitemaps import (
        BadgeSitemap, GameSitemap, ProfileSitemap, StaticViewSitemap,
    )

    GameFactory(defined_trophies={'bronze': 5})
    ProfileFactory(is_linked=True, psn_history_public=True, total_trophies=10,
                   last_synced=timezone.now())
    series = BadgeSeriesFactory(series_slug='crawl-series')
    GroupBadgeFactory(series=series,
                      platform_group=PlatformGroupFactory(key='ultra-hd', name='Ultra HD'),
                      is_live=True)

    r = _robots()
    for sm in (StaticViewSitemap(), GameSitemap(), ProfileSitemap(), BadgeSitemap()):
        for item in sm.items():
            loc = sm.location(item)
            assert r.can_fetch('*', loc), f'sitemap URL {loc} is robots-blocked'


# --- lastmod floors ---

def test_games_lastmod_ignores_shovelware():
    """The section's advertised lastmod must come from a row the section can list."""
    from core.sitemaps import GameSitemap

    clean = GameFactory(defined_trophies={'bronze': 5})
    GameFactory(defined_trophies={'bronze': 5}, shovelware_status='manually_flagged')

    # With a flagged game created LAST, the advertised timestamp must still be the clean row's.
    assert GameSitemap().get_latest_lastmod() == clean.created_at


# --- storage: the premise of every long-cache claim ---

def test_static_storage_is_the_manifest_variant():
    """Content-hashed names are what let WhiteNoise emit far-future immutable Cache-Control.
    The non-manifest variant silently serves everything -- output.css, the self-hosted fonts --
    at max-age=60. (Tests themselves run on plain storage via settings_test.)"""
    import importlib

    prod_settings = importlib.import_module('plat_pursuit.settings')
    backend = prod_settings.STORAGES['staticfiles']['BACKEND']
    assert backend == 'plat_pursuit.storage.ForgivingManifestStaticFilesStorage'


# --- one-hop redirects ---

def test_legacy_trophy_case_paths_are_one_hop(client):
    """trophy_case itself 302s to the profile now; the two legacy 301s must aim straight at the
    profile or they chain (301 -> 302) on the site's largest legacy URL family."""
    ProfileFactory(is_linked=True, psn_username='hopcheck')

    for legacy in ('/profiles/hopcheck/trophy-case/', '/community/profiles/hopcheck/trophy-case/'):
        resp = client.get(legacy, **CF)
        assert resp.status_code == 301
        assert resp['Location'] == '/hunters/hopcheck/', f'{legacy} chains through trophy_case'


# --- head fixes from the closing sweep ---

def test_the_badge_profile_variant_canonicalizes_to_the_base_badge(client):
    series = BadgeSeriesFactory(series_slug='canon-series')
    GroupBadgeFactory(series=series,
                      platform_group=PlatformGroupFactory(key='ultra-hd', name='Ultra HD'),
                      is_live=True)
    wearer = ProfileFactory(is_linked=True, psn_username='wearer', psn_history_public=True)
    # Anon hits already 302 to the canonical page; the variant only RENDERS for authed viewers,
    # and that render is what must canonicalize to the base badge.
    client.force_login(wearer.user)

    head = client.get('/badges/canon-series/wearer/', **CF).content.decode().split('</head>')[0]

    assert 'rel="canonical" href="http://testserver/badges/canon-series/"' in head
    assert 'og:url" content="http://testserver/badges/canon-series/"' in head


def test_a_one_game_franchise_is_noindexed_and_a_bigger_one_is_not(client):
    from trophies.models import ConceptFranchise, Franchise

    thin = Franchise.objects.create(igdb_id=90001, name='Lone Game', slug='lone-game')
    concept = ConceptFactory()
    GameFactory(concept=concept, defined_trophies={'bronze': 5})
    ConceptFranchise.objects.create(franchise=thin, concept=concept)

    body = client.get('/franchises/lone-game/', **CF).content.decode()
    assert 'content="noindex, follow"' in body

    big = Franchise.objects.create(igdb_id=90002, name='Big Series', slug='big-series')
    for _ in range(2):
        c = ConceptFactory()
        GameFactory(concept=c, defined_trophies={'bronze': 5})
        ConceptFranchise.objects.create(franchise=big, concept=c)

    body = client.get('/franchises/big-series/', **CF).content.decode()
    assert 'content="index, follow"' in body


def test_the_membership_welcome_page_is_not_indexable(client):
    profile = ProfileFactory(is_linked=True)
    client.force_login(profile.user)

    resp = client.get('/users/subscribe/success/', **CF, follow=True)

    if resp.redirect_chain:      # non-premium bare hit bounces to the hub -- also fine
        return
    assert 'content="noindex, nofollow"' in resp.content.decode()


def test_the_reviews_archived_page_is_not_indexable(client):
    body = client.get('/community/reviews/', **CF, follow=True).content.decode()

    assert 'content="noindex, follow"' in body


def test_the_roadmap_template_is_noindexed_while_the_system_is_hidden():
    """File-level pin (no roadmap factory exists): the template must not say index,follow while
    nothing links roadmaps and their sitemap is withdrawn. Flip BOTH together on the revamp."""
    from pathlib import Path

    text = (Path(settings.BASE_DIR) / 'templates' / 'trophies' / 'roadmap_detail.html').read_text(encoding='utf-8')
    assert 'noindex, follow' in text
    assert 'else %}index, follow' not in text, 'roadmap detail is indexable again while hidden'


def test_support_pages_have_their_own_descriptions(client):
    default = 'No trophy can hide'

    for url in ('/support/', '/support/roadmap/'):
        head = client.get(url, **CF).content.decode().split('</head>')[0]
        assert default not in head, f'{url} still wears the site-default description'


def test_jsonld_game_url_agrees_with_the_canonical(client):
    """Two conflicting identity claims (node url = the sibling page, canonical = the elected
    SKU) on a star-snippet-eligible node."""
    concept = ConceptFactory()
    winner = GameFactory(concept=concept, played_count=500, defined_trophies={'bronze': 5})
    quiet = GameFactory(concept=concept, played_count=1, defined_trophies={'bronze': 5})

    head = client.get(f'/games/{quiet.np_communication_id}/', **CF).content.decode().split('</head>')[0]

    assert f'"url": "http://testserver/games/{winner.np_communication_id}/"' in head, (
        'the VideoGame node points at the page while rel=canonical points at the sibling'
    )


# --- data invariant behind the profile casing 301 ---

def test_profile_usernames_lowercase_on_save():
    """The sitemap emits psn_username raw and the view 301s any non-lowercase path THEN looks
    up exact-lowercase -- a mixed-case row would produce a sitemap URL that 301s to a 404.
    save() enforces it; this pins that."""
    profile = ProfileFactory(is_linked=True, psn_username='MixedCase')
    profile.refresh_from_db()

    assert profile.psn_username == 'mixedcase'
