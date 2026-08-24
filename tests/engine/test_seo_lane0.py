"""SEO Lane 0 (2026-08-23): the triage round of the site-wide SEO project.

Strategy of record: docs/design/seo-strategy.md. What these pin is the WRONG-today list -- the
robots wildcards that blocked every canonical detail page, the bot 301 that swallowed real
sub-pages, querystring-dirty canonicals, the badge sitemap reading the retired model, the
roadmap sitemap advertising hidden pages, quality floors, and the noindex guards.
"""
import re
from pathlib import Path

import pytest

from tests.factories import BadgeSeriesFactory, GroupBadgeFactory, PlatformGroupFactory, ProfileFactory

pytestmark = pytest.mark.django_db

ROOT = Path(__file__).resolve().parents[2]
CF = {'HTTP_CF_RAY': '8f0000000000abcd-LHR'}


class _GoogleRobots:
    """A Google-spec matcher for the `User-agent: *` group: `*` matches zero or more
    characters, `$` anchors the end, rules are prefix-anchored. urllib.robotparser speaks the
    1994 spec (literal prefixes only) and silently mis-evaluates every wildcard rule -- which is
    the same class of misreading that shipped the original /games/*/* bug, so the test parses
    the file the way Google does."""

    def __init__(self, text):
        self.disallows = []
        in_star = False
        for line in text.splitlines():
            line = line.split('#')[0].strip()
            if not line:
                continue
            key, _, value = line.partition(':')
            key, value = key.strip().lower(), value.strip()
            if key == 'user-agent':
                in_star = value == '*'
            elif in_star and key == 'disallow' and value:
                pattern = re.escape(value).replace(r'\*', '.*').replace(r'\$', '$')
                self.disallows.append(re.compile('^' + pattern))

    def can_fetch(self, _agent, path):
        # Our * group is `Allow: /` + Disallows; every Disallow is longer than the Allow, so
        # under Google's longest-match precedence any matching Disallow wins.
        return not any(rule.match(path) for rule in self.disallows)


def _robots():
    return _GoogleRobots((ROOT / 'static' / 'robots.txt').read_text(encoding='utf-8'))


# --- robots.txt ---

def test_canonical_detail_pages_are_crawlable():
    """THE headline Lane 0 fix. The old /games/*/* rules matched the canonical pages themselves
    (robots `*` matches zero characters), so every game, badge and jobs detail page was blocked
    while three sitemaps advertised them -- parsed here with Python's own robots parser, not by
    reading the file's comments, which is how the bug shipped."""
    r = _robots()

    assert r.can_fetch('*', '/games/NPWR12345_00/'), 'game detail is robots-blocked again'
    assert r.can_fetch('*', '/badges/some-series/'), 'badge detail is robots-blocked again'
    assert r.can_fetch('*', '/jobs/some-job/'), 'job detail is robots-blocked again'
    assert r.can_fetch('*', '/games/NPWR12345_00/leaderboard/'), 'the game leaderboard is blocked'


def test_fragment_endpoints_are_not_crawlable():
    """Bare HTML fragments (no <html>, no canonical) must never be indexed as pages."""
    r = _robots()

    assert not r.can_fetch('*', '/badge-ranks/some-series/')
    assert not r.can_fetch('*', '/leaderboards/rows/')
    assert not r.can_fetch('*', '/group-badge-peek/123/')
    assert not r.can_fetch('*', '/jobs/some-job/ranks/')


def test_profile_query_variants_stay_throttled():
    """The 2026-08 outage class: ?tab/?page permutations of profile detail stay blocked, on all
    three historical spellings, while the profile INDEX's pagination stays walkable."""
    r = _robots()

    assert not r.can_fetch('*', '/hunters/somebody/?tab=trophies')
    assert not r.can_fetch('*', '/profiles/somebody/?page=2')
    assert not r.can_fetch('*', '/community/profiles/somebody/?tab=badges')
    assert r.can_fetch('*', '/hunters/?page=2'), "the hunters index's pagination must stay walkable"


# --- the bot canonical 301 ---

def test_bots_are_bounced_off_profile_variants_but_not_real_subpages(client):
    """The lookahead fix: /games/<np>/<username>/ still 301s bots to the canonical, but the REAL
    sub-pages sharing the two-segment shape (leaderboard, roadmap) reach their views."""
    # CF-Ray rides along: the origin guard runs first and would 302 the request to prod.
    ua = {'HTTP_USER_AGENT': 'Mozilla/5.0 (compatible; Googlebot/2.1)', 'HTTP_CF_RAY': '8f0000000000abcd-LHR'}

    resp = client.get('/games/NPWR12345_00/somehunter/', **ua)
    assert resp.status_code == 301
    assert resp['Location'] == '/games/NPWR12345_00/'

    resp = client.get('/games/NPWR12345_00/leaderboard/', **ua)
    assert resp.status_code != 301, 'Googlebot is being 301d off the leaderboard again'

    resp = client.get('/games/NPWR12345_00/roadmap/', **ua)
    assert resp.status_code != 301, 'Googlebot is being 301d off the roadmap page again'


# --- canonicals ---

def test_canonicals_strip_the_querystring(client):
    body = client.get('/?utm_source=x&fbclid=y', **CF).content.decode()

    assert 'rel="canonical" href="http://testserver/"' in body, 'the canonical carries params again'
    assert 'og:url" content="http://testserver/"' in body


# --- sitemaps ---

def test_the_badge_sitemap_serves_the_live_badge_system():
    """The old sitemap read the RETIRED Badge model: 404 URLs for dormant legacy rows, live
    BadgeSeries missing entirely. Now: exactly the set BadgeDetailView serves."""
    from core.sitemaps import BadgeSitemap

    live = BadgeSeriesFactory(series_slug='live-series')
    GroupBadgeFactory(series=live, platform_group=PlatformGroupFactory(key='ultra-hd', name='Ultra HD'),
                      is_live=True)
    BadgeSeriesFactory(series_slug='dormant-series')   # no live edition -> detail 404s -> not advertised

    slugs = [s.series_slug for s in BadgeSitemap().items()]

    assert 'live-series' in slugs
    assert 'dormant-series' not in slugs


def test_the_game_sitemap_excludes_shovelware():
    from tests.factories import GameFactory
    from core.sitemaps import GameSitemap

    keeper = GameFactory()
    flagged = GameFactory(shovelware_status='manually_flagged')

    ids = [g.np_communication_id for g in GameSitemap().items()]

    assert keeper.np_communication_id in ids
    assert flagged.np_communication_id not in ids, 'shovelware is being advertised to crawlers'


def test_the_profile_sitemap_has_a_quality_floor():
    from core.sitemaps import ProfileSitemap

    good = ProfileFactory(is_linked=True, psn_history_public=True, total_trophies=100)
    private = ProfileFactory(is_linked=True, psn_history_public=False, total_trophies=100)
    empty = ProfileFactory(is_linked=True, psn_history_public=True, total_trophies=0)

    names = [p.psn_username for p in ProfileSitemap().items()]

    assert good.psn_username in names
    assert private.psn_username not in names, 'a header-only private page is being advertised'
    assert empty.psn_username not in names, 'a zero-trophy stub is being advertised'


def test_the_roadmap_sitemap_is_withdrawn():
    """Roadmaps are hidden from the site with no return promised (his call): a sitemap must not
    advertise pages nothing links to."""
    from plat_pursuit.urls import sitemaps

    assert 'roadmaps' not in sitemaps


# --- noindex guards ---

def test_a_private_profile_is_noindexed_and_keeps_its_stats_out_of_the_meta(client):
    profile = ProfileFactory(is_linked=True, psn_history_public=False, total_trophies=4321)

    body = client.get(f'/hunters/{profile.psn_username}/', **CF).content.decode()

    assert 'noindex, follow' in body, 'the header-only private page is indexable again'
    assert '4321' not in body.split('</head>')[0], 'the meta describes stats the page refuses to show'


def test_a_public_profile_stays_indexable(client):
    profile = ProfileFactory(is_linked=True, psn_history_public=True, total_trophies=10)

    body = client.get(f'/hunters/{profile.psn_username}/', **CF).content.decode()

    assert 'index, follow' in body
    assert 'noindex' not in body


def test_the_search_action_advertises_a_working_endpoint(client):
    """The WebSite schema's SearchAction used to point at /search/?q=, a parameter the view never
    read -- the advertised action was a redirect to nowhere. Now it names the real params, and
    this test walks them end to end."""
    body = client.get('/', **CF).content.decode()

    # The JSON-LD hardening (Lane 2) emits & as \\u0026 -- same URL once parsed.
    assert '/search/?type=game\\u0026query=' in body

    resp = client.get('/search/?type=game&query=elden')
    assert resp.status_code == 302
    assert 'query=elden' in resp['Location'], 'the advertised search does not land on results'


def test_filtered_hub_states_are_noindex_follow(client):
    """Decision #3's second clause (the closing audit's catch: shipping clean canonicals WITHOUT
    this is worse than the dirty canonicals they replaced -- page N canonicalizing to page 1 is
    the one thing Google documents as wrong). Bare hub URLs index; any querystring state
    noindexes but keeps following links, so pagination still feeds discovery."""
    bare = client.get('/hunters/', **CF).content.decode()
    # A filter param, not ?page=2: the empty test DB would 404 a second page, and the 404
    # template has its own (different) noindex -- the wrong page passing the right assertion.
    filtered = client.get('/hunters/?sort=level', **CF).content.decode()

    assert 'content="index, follow"' in bare
    assert 'content="noindex, follow"' in filtered, 'a filtered hub state is indexable again'


def test_the_bot_301_spares_the_noslash_subpage_forms(client):
    """The lookahead needs the end-anchor alternative: /games/<np>/leaderboard (no slash) was
    still being 301d while the slashed form was spared."""
    ua = {'HTTP_USER_AGENT': 'Mozilla/5.0 (compatible; Googlebot/2.1)',
          'HTTP_CF_RAY': '8f0000000000abcd-LHR'}

    # The no-slash form 301s -- but to the SLASHED sub-page (CommonMiddleware's APPEND_SLASH,
    # running after our spared lookahead), never to the bare game page.
    resp = client.get('/games/NPWR12345_00/leaderboard', **ua)
    assert resp.status_code == 301 and resp['Location'].endswith('/leaderboard/'), (
        'the no-slash leaderboard was bounced to the game page'
    )
    # And a hunter whose name merely STARTS with a reserved word is still bounced.
    resp = client.get('/games/NPWR12345_00/roadmapfan/', **ua)
    assert resp.status_code == 301 and resp['Location'] == '/games/NPWR12345_00/'


def test_the_trophy_case_door_is_closed(client):
    """Found via this lane's audit (a private hunter's platinum list rendered indexable), then
    retired outright on his call: nothing in the rebuilt site linked the page. 302 not 301 --
    it is the parked showcase system's selection UI, preserved for that rebuild."""
    profile = ProfileFactory(is_linked=True, psn_history_public=True, total_trophies=10)

    resp = client.get(f'/hunters/{profile.psn_username}/trophy-case/', **CF)

    assert resp.status_code == 302
    assert resp['Location'] == f'/hunters/{profile.psn_username}/'



def test_jsonld_urls_agree_with_the_canonical(client):
    """Schema url fields follow the path-only rule too -- a ProfilePage whose url carries ?tab=
    beside a clean rel=canonical is the same page disagreeing with itself."""
    profile = ProfileFactory(is_linked=True, psn_history_public=True, total_trophies=10)

    body = client.get(f'/hunters/{profile.psn_username}/?tab=badges', **CF).content.decode()

    assert f'/hunters/{profile.psn_username}/?tab=' not in body.split('</head>')[0].replace('hx-get', ''), (
        'a head URL still carries the querystring'
    )

