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
    """The rule of record: has trophy data first (a trophy-less SKU cannot represent the
    concept, whatever its played_count), then the SKU the community actually owns."""
    _, stub, winner, quiet = _family()

    assert stub.canonical_sibling().id == winner.id
    assert quiet.canonical_sibling().id == winner.id
    assert winner.canonical_sibling().id == winner.id


def test_a_conceptless_game_stands_for_itself():
    lone = GameFactory(concept=None)

    assert lone.canonical_sibling().id == lone.id


def test_the_sitemap_and_the_page_agree_on_the_winner():
    """The whole point of the shared ordering: the sitemap's window and the page's per-concept
    pick must elect the SAME sibling, or the sitemap advertises a URL whose page canonicals
    elsewhere."""
    from trophies.models import Game

    _, stub, winner, quiet = _family()
    lone = GameFactory(concept=None)

    elected = set(Game.objects.concept_canonicals().values_list('id', flat=True))

    assert winner.id in elected
    assert stub.id not in elected and quiet.id not in elected
    assert lone.id in elected, 'a concept-less game must stand for itself in the sitemap'


def test_every_sibling_page_canonicalizes_to_the_concept_game_page(client):
    """Games/Trophy Lists IA: the concept Game page subsumed the sibling election -- winner AND
    siblings now consolidate onto ONE URL (harder than the old winner-among-siblings rule). An
    unmatched concept's page is the /games/c/<concept_id>/ form."""
    concept, stub, winner, quiet = _family()
    target = f'rel="canonical" href="http://testserver/games/c/{concept.concept_id}/"'

    body = client.get(f'/games/{quiet.np_communication_id}/', **CF).content.decode()
    assert target in body

    winner_body = client.get(f'/games/{winner.np_communication_id}/', **CF).content.decode()
    assert target in winner_body


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

    assert clean.canonical_sibling().id == clean.id, 'the page elected a shovelware winner'
    elected = set(Game.objects.exclude_shovelware()
                  .filter(np_communication_id__isnull=False)
                  .concept_canonicals().values_list('id', flat=True))
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

    assert f'og:url" content="http://testserver/games/c/{concept.concept_id}/"' in head, (
        'og:url disagrees with rel=canonical'
    )


def test_the_day_page_joins_the_casing_rule(client):
    profile = ProfileFactory(is_linked=True, psn_username='dayhunter')

    resp = client.get('/hunters/DayHunter/day/2026-08-01/', **CF)

    assert resp.status_code == 301
    assert resp['Location'] == '/hunters/dayhunter/day/2026-08-01/'

