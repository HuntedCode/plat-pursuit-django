"""The browse page: three URLs, one view, and a gate that must not run the data layer.

The query-count tests are the substance here. A browse grid that is CORRECT and a browse grid that is
FLAT look identical from the outside -- every functional assertion passes either way -- so the shape
has to be asserted as a number, compared across two page sizes rather than against a magic constant.

And the gate's promise is a number too: CLAUDE.md's preview rule exists because a locked UI twice ran
its real data path for people who could not use it, and both times the lock looked fine.
"""
import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from prompts.models import MIN_GAMES_TO_PUBLISH, SHAPE_GRID, SHAPE_POLL, SHAPE_TIER
from prompts.services import prompt_service as svc
from prompts.services import response_service as rsvc
from prompts.services import social_service as social
from tests.factories import ConceptFactory, GameFactory, ProfileFactory

pytestmark = pytest.mark.django_db

TIERS = '/community/tiers/'
GRIDS = '/community/grids/'
POLLS = '/community/polls/'

#: Any query touching this feature's own tables. The gate's promise is that a hunter outside the
#: cohort causes none of them.
PROMPT_TABLES = ('prompts_prompt', 'prompts_promptgame', 'prompts_promptbucket',
                 'prompts_promptresponse', 'prompts_promptplacement', 'prompts_promptlike')


def _member(psn='member', premium=True, staff=False, moderator=False):
    profile = ProfileFactory(is_linked=True, psn_username=psn)
    if premium:
        profile.user_is_premium = True
        profile.save(update_fields=['user_is_premium'])
    user = profile.user
    if staff or moderator:
        user.is_staff = staff
        user.role = 'moderator' if moderator else user.role
        user.save(update_fields=['is_staff', 'role'])
    return profile


def _published(owner, shape=SHAPE_TIER, *, title='Rank them', games=None):
    prompt = svc.create_prompt(owner, shape=shape, title=title)
    if shape == SHAPE_GRID:
        svc.create_bucket(prompt, owner, label='Best combat')
    for _ in range(MIN_GAMES_TO_PUBLISH[shape] if games is None else games):
        concept = ConceptFactory()
        GameFactory(concept=concept)
        svc.add_concept(prompt, owner, concept)
    svc.update_prompt(prompt, owner, is_public=True)
    prompt.refresh_from_db()
    return prompt


def _prompt_queries(client, url):
    with CaptureQueriesContext(connection) as ctx:
        resp = client.get(url)
    assert resp.status_code in (200, 302), f'{url} answered {resp.status_code}; the count proves nothing'
    return [q for q in ctx.captured_queries
            if any(table in q['sql'] for table in PROMPT_TABLES)]


# ── the gate ──────────────────────────────────────────────────────────────────────────────────────


def test_a_hunter_outside_the_beta_reaches_a_page_that_runs_none_of_this(client):
    """THE PINNED PREVIEW RULE, AS A NUMBER. A locked surface must not execute its data layer for
    somebody who cannot use it -- twice now a preview looked correctly locked while its providers ran
    against the viewer's real library, and the second time it took the homepage down for free-tier
    whales.

    Here the gate sits before `get_queryset`, so the promise is zero: not "few", not "cheap"."""
    owner = _member('owner')
    _published(owner)
    client.force_login(ProfileFactory(is_linked=True, psn_username='outsider').user)

    queries = _prompt_queries(client, TIERS)

    assert queries == [], f'the gate ran the data layer anyway: {[q["sql"][:80] for q in queries]}'


def test_the_gate_sends_them_somewhere_real(client):
    client.force_login(ProfileFactory(is_linked=True, psn_username='outsider').user)
    resp = client.get(TIERS)

    assert resp.status_code == 302
    assert resp['Location'] == '/beta-access/'
    assert client.get('/beta-access/').status_code == 200


def test_members_and_the_team_get_in(client):
    owner = _member('owner')
    _published(owner)

    for profile in (_member('paying'),
                    _member('staffer', premium=False, staff=True),
                    _member('mod', premium=False, moderator=True)):
        client.force_login(profile.user)
        assert client.get(TIERS).status_code == 200, f'{profile.psn_username} was locked out'


def test_a_signed_out_visitor_is_sent_to_sign_in_rather_than_told_to_buy(client):
    """They may well be a member. "You need a membership" is a lie told to somebody who only needs to
    sign in."""
    resp = client.get(TIERS)
    assert resp.status_code == 302
    assert '/beta-access/' not in resp['Location']


# ── three URLs, one view ──────────────────────────────────────────────────────────────────────────


def test_each_url_shows_only_its_own_shape(client):
    owner = _member('owner')
    tier = _published(owner, SHAPE_TIER, title='A tier list')
    grid = _published(owner, SHAPE_GRID, title='A grid')
    poll = _published(owner, SHAPE_POLL, title='A poll', games=2)
    client.force_login(_member('reader').user)

    for url, present, absent in ((TIERS, tier, grid), (GRIDS, grid, poll), (POLLS, poll, tier)):
        body = client.get(url).content.decode()
        assert present.title in body
        assert absent.title not in body, f'{url} leaked another shape'


def test_the_tab_strip_links_to_all_three_and_marks_the_current_one(client):
    client.force_login(_member('reader').user)
    body = client.get(GRIDS).content.decode()

    for url in (TIERS, GRIDS, POLLS):
        assert f'href="{url}"' in body
    assert 'aria-current="page"' in body


def test_the_filter_form_posts_back_to_the_tab_you_are_on(client):
    """Hardcoding one route would move the reader to Tiers on every filter change -- a bug that reads
    as the page being broken rather than wrong."""
    client.force_login(_member('reader').user)

    for url in (TIERS, GRIDS, POLLS):
        body = client.get(url).content.decode()
        assert f'hx-get="{url}"' in body


# ── what is visible ───────────────────────────────────────────────────────────────────────────────


def test_only_published_prompts_are_listed(client):
    owner = _member('owner')
    live = _published(owner, title='Published')
    draft = svc.create_prompt(owner, shape=SHAPE_TIER, title='Still a draft')
    client.force_login(_member('reader').user)

    body = client.get(TIERS).content.decode()
    assert live.title in body
    assert draft.title not in body


def test_a_private_prompt_is_absent_from_the_partial_route_too(client):
    """The `profile_views.py` bug class: a flag checked only in the parent template was bypassed
    entirely by an HTMX request, because the partial never renders the parent. The filter here is in
    the QUERYSET, so both routes answer the same -- asserted rather than assumed."""
    owner = _member('owner')
    draft = svc.create_prompt(owner, shape=SHAPE_TIER, title='Still a draft')
    client.force_login(_member('reader').user)

    full = client.get(TIERS).content.decode()
    partial = client.get(TIERS, HTTP_HX_REQUEST='true').content.decode()

    assert draft.title not in full
    assert draft.title not in partial
    assert 'items-grid' in partial
    assert '<html' not in partial.lower(), 'the partial route returned the whole page'


# ── sorts ─────────────────────────────────────────────────────────────────────────────────────────


def test_a_junk_sort_falls_back_rather_than_dropping_the_ordering(client):
    """No ordering at all is how a grid ends up in whatever order the database felt like -- and, with
    pagination, how one row appears on two pages."""
    owner = _member('owner')
    for i in range(3):
        _published(owner, title=f'Prompt {i}')
    client.force_login(_member('reader').user)

    resp = client.get(TIERS, {'sort': 'nonsense'})
    assert resp.status_code == 200
    assert resp.context['current_sort'] == 'answered'


def test_the_default_sort_leads_with_how_many_people_answered(client):
    """On a prompt, how many people bothered is the stronger signal -- which is why the tile shows the
    number the sort is made of rather than one that agrees with nothing."""
    owner, answerer = _member('owner'), _member('answerer')
    quiet = _published(owner, title='Nobody answered this')
    busy = _published(owner, title='Everybody answered this')
    rsvc.place(busy, answerer, game_id=busy.games.first().pk,
               bucket_id=busy.buckets.first().pk)
    client.force_login(_member('reader').user)

    titles = [p.title for p in client.get(TIERS).context['prompts']]
    assert titles.index(busy.title) < titles.index(quiet.title)


# ── flatness ──────────────────────────────────────────────────────────────────────────────────────


def test_the_query_count_does_not_move_with_the_number_of_prompts(client):
    """Two sizes compared against each other, never a magic number: counting the whole page measures
    cached site chrome and makes the grid look FASTER as it grows."""
    owner = _member('owner')
    reader = _member('reader')
    client.force_login(reader.user)

    for i in range(2):
        _published(owner, title=f'Few {i}')
    few = len(_prompt_queries(client, TIERS))

    for i in range(10):
        _published(owner, title=f'Many {i}')
    many = len(_prompt_queries(client, TIERS))

    assert few == many, f'the grid is N+1ing: {few} -> {many}'


def test_the_viewers_own_likes_cost_one_query_not_one_per_tile(client):
    """"Did I like this?" is the shape that most invites an N+1, because it is per-viewer and per-row
    at once."""
    owner, reader = _member('owner'), _member('reader')
    prompts = [_published(owner, title=f'Prompt {i}') for i in range(6)]
    for prompt in prompts:
        social.set_prompt_like(prompt, reader, liked=True)
    client.force_login(reader.user)

    liked = len(_prompt_queries(client, TIERS))

    for prompt in prompts:
        social.set_prompt_like(prompt, reader, liked=False)
    unliked = len(_prompt_queries(client, TIERS))

    assert liked == unliked, 'the like state is being fetched per tile'


def test_the_grid_never_drags_the_igdb_blob_along(client):
    """`raw_response` is the ~30 KB IGDB payload behind the May 2026 web-server OOM, and no cover
    render reads it. A byte-size regression no query count can see."""
    owner = _member('owner')
    _published(owner)
    client.force_login(_member('reader').user)

    for query in _prompt_queries(client, TIERS):
        if 'igdb' in query['sql'].lower():
            assert 'raw_response' not in query['sql']


# ── the empty state ───────────────────────────────────────────────────────────────────────────────


def test_each_shape_says_its_own_thing_when_it_is_empty(client):
    """"Nobody has made a tier list" and "nobody has asked a question" are different sentences, and a
    shared one is neither."""
    client.force_login(_member('reader').user)

    seen = set()
    for url in (TIERS, GRIDS, POLLS):
        copy = client.get(url).context['empty_copy']
        assert copy, f'{url} has no empty copy'
        seen.add(copy)
    assert len(seen) == 3, 'two shapes share an empty state'


def test_a_search_that_matches_nothing_offers_to_clear_itself(client):
    """"You narrowed it to nothing" and "there is nothing here yet" are opposite situations, and
    offering the wrong remedy is worse than offering none."""
    owner = _member('owner')
    _published(owner, title='Something')
    client.force_login(_member('reader').user)

    # SCOPED TO THE LINK, not to the words. "Clear search" is also the aria-label on the search
    # box's own clear button, which is present on every render -- so a bare substring assertion is
    # true either way and proves nothing. Matching the anchor is what distinguishes them.
    offer = 'pp-cta pp-cta--ghost">Clear search</a>'

    narrowed = client.get(TIERS, {'q': 'zzzznothing'})
    assert narrowed.context['has_filters'] is True
    assert offer in narrowed.content.decode()

    empty = client.get(POLLS)
    assert empty.context['has_filters'] is False
    assert offer not in empty.content.decode()


def test_a_term_too_short_to_filter_does_not_claim_to_be_filtering(client):
    """Read through the SAME parser the queryset uses. A two-character term is ignored, so the empty
    state must not offer to clear a filter that is not on."""
    client.force_login(_member('reader').user)
    resp = client.get(TIERS, {'q': 'ab'})

    assert resp.context['has_filters'] is False
