"""SEO Lane 1 (2026-08-23): technical hygiene at scale.

Strategy: docs/design/seo-strategy.md. The light concept-canonical election (no FK, no cron --
one deterministic ordering, two consumers that cannot drift), the games hub returning 200, the
casing 301, title/description unification.
"""
import pytest

from tests.factories import ConceptFactory, GameFactory, ProfileFactory

pytestmark = pytest.mark.django_db

CF = {'HTTP_CF_RAY': '8f0000000000abcd-LHR'}


# --- the election ---

def _family():
    """One concept, three SKUs: a trophy-less stub, the most-played real SKU, a quieter one."""
    concept = ConceptFactory()
    stub = GameFactory(concept=concept, played_count=999, defined_trophies={})
    winner = GameFactory(concept=concept, played_count=500,
                         defined_trophies={'bronze': 10, 'platinum': 1})
    quiet = GameFactory(concept=concept, played_count=3,
                        defined_trophies={'bronze': 10, 'platinum': 1})
    return concept, stub, winner, quiet


def test_the_election_prefers_trophies_then_played_count():
    """The rule of record: has trophy data first (a trophy-less SKU cannot represent the page,
    whatever its played_count), then the SKU the community actually owns. Pinned via the sitemap's
    window now -- canonical_sibling() was deleted when the concept Game page subsumed per-page
    sibling canonicals (Games/Trophy Lists IA)."""
    from trophies.models import Game

    _, stub, winner, quiet = _family()

    elected = set(Game.objects.game_page_canonicals().values_list('id', flat=True))

    assert winner.id in elected
    assert stub.id not in elected and quiet.id not in elected


def test_a_conceptless_game_stands_for_itself():
    from trophies.models import Game

    lone = GameFactory(concept=None)

    assert lone.id in set(Game.objects.game_page_canonicals().values_list('id', flat=True))


def test_every_sitemap_url_equals_the_canonical_of_the_page_it_advertises(client):
    """The invariant that replaced 'sitemap and page agree on the winner': the sitemap and
    rel=canonical both route through Concept.game_page_url, so each advertised URL must be
    exactly what its own page emits as canonical."""
    from core.sitemaps import GameSitemap

    concept, stub, winner, quiet = _family()
    lone = GameFactory(concept=None, defined_trophies={'bronze': 1})

    for obj in GameSitemap().items():
        url = GameSitemap().location(obj)
        head = client.get(url, **CF).content.decode().split('</head>')[0]
        assert f'rel="canonical" href="http://testserver{url}"' in head, url


def test_every_list_page_is_self_canonical(client):
    """The slim-down flipped the slice-1 interim: List detail has distinct stack content now
    (trophies, Ranks, the community snapshot -- Ratings/About moved up to the concept Game
    page), so EVERY list page canonicalizes to its own bare URL. Winner and quiet sibling alike
    -- the old consolidation direction must be gone."""
    concept, stub, winner, quiet = _family()

    for g in (winner, quiet):
        body = client.get(f'/games/{g.np_communication_id}/', **CF).content.decode()
        assert f'rel="canonical" href="http://testserver/games/{g.np_communication_id}/"' in body
        # Scoped to the canonical LINK element: the Game-page URL legitimately appears elsewhere
        # in the head (the breadcrumb JSON-LD's ListItem) -- only the canonical must not carry it.
        canonical_href = body.split('rel="canonical" href="')[1].split('"')[0]
        assert f'/games/c/{concept.concept_id}/' not in canonical_href, (
            'the interim up-canonical is back'
        )


def test_the_username_variant_canonicalizes_to_the_bare_list_url(client):
    """The D6 trap: base.html's canonical default is request.path, so a block.super revert would
    mint per-viewer canonicals on /games/<np>/<username>/. The explicit page_canonical_url must
    hold the BARE list URL there."""
    concept, stub, winner, quiet = _family()
    viewer = ProfileFactory(is_linked=True)
    client.force_login(viewer.user)

    head = client.get(
        f'/games/{winner.np_communication_id}/{viewer.psn_username}/', **CF
    ).content.decode().split('</head>')[0]

    assert f'rel="canonical" href="http://testserver/games/{winner.np_communication_id}/"' in head
    assert viewer.psn_username not in head.split('rel="canonical"')[1].split('>')[0], (
        'the canonical must not carry the username segment'
    )


def test_aggregate_rating_lives_on_the_game_page_only(client):
    """One star-snippet claim per work: the concept Game page's VideoGame node carries the
    AggregateRating; List detail's (also indexable since the self-canonical flip) must NOT --
    two indexable pages racing for the same rich result is the drift this pins."""
    from tests.factories import ConceptTrophyGroupFactory
    from trophies.models import IGDBMatch, UserConceptRating

    concept = ConceptFactory()
    IGDBMatch.objects.create(concept=concept, igdb_id=61001, status='accepted')
    ConceptTrophyGroupFactory(concept=concept)   # the base community tab the averages hang off
    game = GameFactory(concept=concept, defined_trophies={'bronze': 1})
    UserConceptRating.objects.create(
        profile=ProfileFactory(), concept=concept, concept_trophy_group=None,
        difficulty=5, grindiness=5, hours_to_platinum=20, fun_ranking=7, overall_rating=4.0,
    )

    game_page = client.get('/games/61001/', **CF).content.decode()
    list_page = client.get(f'/games/{game.np_communication_id}/', **CF).content.decode()

    assert 'aggregateRating' in game_page
    assert 'aggregateRating' not in list_page


# --- the hub ---

def test_the_games_hub_returns_200_for_anon(client):
    """The old force-302 to ?platform=... meant the hub's canonical URL never returned a page.
    Anon renders the default view in place now; the defaults still apply (the form binds them)."""
    resp = client.get('/games/', **CF)

    assert resp.status_code == 200
    assert 'content="index, follow"' in resp.content.decode(), 'the bare hub must be indexable'


def test_a_saved_defaults_member_still_gets_their_redirect(client):
    profile = ProfileFactory(is_linked=True)
    profile.user.browse_defaults = {'games': {'platform': ['PS5']}}
    profile.user.save(update_fields=['browse_defaults'])
    client.force_login(profile.user)

    resp = client.get('/games/', **CF)

    assert resp.status_code == 302
    assert 'platform=PS5' in resp['Location']


# --- the casing 301 ---

def test_profile_casing_redirects_to_the_stored_form(client):
    profile = ProfileFactory(is_linked=True, psn_username='casinghunter')

    resp = client.get('/hunters/CasingHunter/?tab=badges', **CF)

    assert resp.status_code == 301
    assert resp['Location'] == '/hunters/casinghunter/?tab=badges'

    assert client.get('/hunters/casinghunter/', **CF).status_code == 200


# --- titles + descriptions (one system) ---

def test_the_profile_title_carries_search_intent(client):
    profile = ProfileFactory(is_linked=True, psn_history_public=True, total_trophies=10)

    body = client.get(f'/hunters/{profile.psn_username}/', **CF).content.decode()

    assert 'PSN Profile &amp; Trophies - Platinum Pursuit</title>' in body


def test_game_detail_descriptions_come_from_the_view(client):
    """The hardcoded template block silently discarded the view's richer value AND still sold
    the hidden Roadmaps. One system now: the view computes, every meta tag reads it."""
    game = GameFactory(defined_trophies={'bronze': 10, 'platinum': 1})

    head = client.get(f'/games/{game.np_communication_id}/', **CF).content.decode().split('</head>')[0]

    assert 'roadmap' not in head.lower(), 'the meta is selling the hidden Roadmaps again'
    assert '11 trophies including 1 platinum' in head, "the view's computed description is discarded"


def test_the_two_electors_share_one_population():
    """The audit's HIGH: the sitemap elected over the shovelware-excluded, np-bearing set while
    the page elected over everything -- a flagged winner made the page canonicalize to a URL the
    sitemap deliberately withheld. One floor now, both sides."""
    from trophies.models import Game

    concept = ConceptFactory()
    flagged_winner = GameFactory(concept=concept, played_count=900,
                                 defined_trophies={'bronze': 5}, shovelware_status='manually_flagged')
    clean = GameFactory(concept=concept, played_count=10, defined_trophies={'bronze': 5})

    elected = set(Game.objects.exclude_shovelware()
                  .filter(np_communication_id__isnull=False)
                  .game_page_canonicals().values_list('id', flat=True))
    assert clean.id in elected and flagged_winner.id not in elected


def test_the_bare_hub_renders_its_defaults_visibly(client):
    """The audit's companion find: the queryset filtered by defaults while the checkboxes
    rendered unchecked and page 2 dropped the filter. The form is the single source now, and
    the template surfaces the params via history.replaceState for everything that reads
    location.search."""
    body = client.get('/games/', **CF).content.decode()

    assert 'history.replaceState' in body, 'the default params never reach the URL'
    # escapejs encodes '=' and '&' as unicode escapes; assert the payload around the call.
    payload = body.split('history.replaceState')[1][:220]
    assert 'PS4' in payload and 'PS5' in payload, 'the replaceState payload lost the defaults'


def test_og_url_follows_the_canonical_on_game_detail(client):
    concept = ConceptFactory()
    winner = GameFactory(concept=concept, played_count=500, defined_trophies={'bronze': 1})
    quiet = GameFactory(concept=concept, played_count=1, defined_trophies={'bronze': 1})

    head = client.get(f'/games/{quiet.np_communication_id}/', **CF).content.decode().split('</head>')[0]

    assert f'og:url" content="http://testserver/games/{quiet.np_communication_id}/"' in head, (
        'og:url disagrees with rel=canonical (self, since the slim-down)'
    )


def test_the_day_page_joins_the_casing_rule(client):
    profile = ProfileFactory(is_linked=True, psn_username='dayhunter')

    resp = client.get('/hunters/DayHunter/day/2026-08-01/', **CF)

    assert resp.status_code == 301
    assert resp['Location'] == '/hunters/dayhunter/day/2026-08-01/'

