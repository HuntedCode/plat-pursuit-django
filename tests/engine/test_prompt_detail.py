"""The prompt detail page, the create dialog, and the adder extracted for both to share.

TWO THINGS GET THE MOST WEIGHT HERE.

The first is that the page's controls come from the SERVICE'S rules and not from a second copy: the
whole freeze table lives in `prompt_service`, and this page asks it through `frozen_acts`. A template
that decided for itself that a poll freezes when published would look right on the day it was written
and drift the first time the rule moved. So the tests assert the correspondence -- a control renders
exactly when the endpoint behind it would accept the press -- rather than asserting the shapes.

The second is that the adder is SHARED. `PP.GameAdder` and `game_search` were extracted so the lists
page and this one cannot diverge, and an extraction is only worth having if a caller cannot quietly
opt out of it. The sharing itself is therefore asserted.
"""
import re

import pytest
from django.core.cache import cache
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from prompts.models import (MAX_BUCKETS_PER_PROMPT, MIN_GAMES_TO_PUBLISH, SHAPE_GRID, SHAPE_POLL,
                            SHAPE_TIER)
from prompts.services import prompt_service as svc
from tests.factories import ConceptFactory, GameFactory, ProfileFactory

pytestmark = pytest.mark.django_db

PROMPT_TABLES = ('prompts_prompt', 'prompts_promptgame', 'prompts_promptbucket',
                 'prompts_promptresponse', 'prompts_promptplacement', 'prompts_promptlike',
                 'prompts_promptresponselike')


def _read(path):
    with open(path, encoding='utf-8') as handle:
        return handle.read()


def _member(psn='member', premium=True, linked=True):
    profile = ProfileFactory(is_linked=linked, psn_username=psn)
    if premium:
        profile.user_is_premium = True
        profile.save(update_fields=['user_is_premium'])
    return profile


def _signed_in(client, profile):
    client.force_login(profile.user)
    return profile


def _draft(owner, shape=SHAPE_TIER, *, title='Rank them', games=0, rows=0):
    prompt = svc.create_prompt(owner, shape=shape, title=title)
    for n in range(rows):
        svc.create_bucket(prompt, owner, label=f'Row {n}')
    for _ in range(games):
        concept = ConceptFactory()
        GameFactory(concept=concept)
        svc.add_concept(prompt, owner, concept)
    prompt.refresh_from_db()
    return prompt


def _published(owner, shape=SHAPE_TIER, **kwargs):
    kwargs.setdefault('games', MIN_GAMES_TO_PUBLISH[shape])
    if shape == SHAPE_GRID:
        kwargs.setdefault('rows', 1)
    prompt = _draft(owner, shape, **kwargs)
    svc.update_prompt(prompt, owner, is_public=True)
    prompt.refresh_from_db()
    return prompt


def _url(prompt):
    return reverse('prompt_detail', args=[prompt.pk])


# ── the gate and visibility ──────────────────────────────────────────────────────────────────────

def test_the_detail_page_is_behind_the_beta_gate(client):
    """The browse pages are gated and a prompt's own page is the obvious way around one. It is the
    same mixin, which means the same redirect BEFORE any queryset runs."""
    owner = _member()
    prompt = _published(owner)

    outsider = _member('outsider', premium=False)
    _signed_in(client, outsider)

    resp = client.get(_url(prompt))
    assert resp.status_code == 302
    assert '/beta-access/' in resp['Location']


def test_the_gate_runs_none_of_this_apps_queries(client):
    """CLAUDE.md's preview rule, asserted as a number. A locked page that still executes its data
    layer is the failure that rule exists for, and both times it happened the lock looked fine."""
    owner = _member()
    prompt = _published(owner)

    _signed_in(client, _member('outsider', premium=False))
    with CaptureQueriesContext(connection) as ctx:
        client.get(_url(prompt))

    touched = [q for q in ctx.captured_queries
               if any(table in q['sql'] for table in PROMPT_TABLES)]
    assert touched == [], f'the gate ran {len(touched)} queries against this feature'


def test_somebody_elses_draft_answers_404_not_403(client):
    """A 403 confirms the prompt exists and is not yours, from nothing but an id. `readable_by` is
    the single supported read precisely so a draft is indistinguishable from a wrong number."""
    prompt = _draft(_member('author'))

    _signed_in(client, _member('stranger'))
    assert client.get(_url(prompt)).status_code == 404


def test_an_author_sees_their_own_draft(client):
    owner = _signed_in(client, _member('author'))
    prompt = _draft(owner, title='Still cooking')

    resp = client.get(_url(prompt))
    assert resp.status_code == 200
    assert 'Still cooking' in resp.content.decode()


def test_a_draft_is_never_indexed(client):
    """`readable_by` 404s a draft for everybody else, but the AUTHOR's render is a 200 with real
    content -- which is the page a signed-in crawler session would be handed."""
    owner = _signed_in(client, _member('author'))
    draft = _draft(owner)
    published = _published(owner, title='Out in the world')

    assert 'noindex' in client.get(_url(draft)).content.decode()
    assert 'noindex' not in client.get(_url(published)).content.decode()


# ── the controls match the service's rules ───────────────────────────────────────────────────────

def test_a_published_poll_draws_no_tools_and_a_draft_poll_draws_them(client):
    """THE CORRESPONDENCE, not the shapes. `_FROZEN_WHILE_PUBLIC` freezes a poll entirely once it is
    public, so the page must offer no adder -- and the same poll as a draft must offer one. Asserting
    both directions is what makes this a test of the rule rather than of one screenshot."""
    owner = _signed_in(client, _member('author'))

    draft = _draft(owner, SHAPE_POLL, games=2)
    assert 'data-pd-adder' in client.get(_url(draft)).content.decode()

    svc.update_prompt(draft, owner, is_public=True)
    assert 'data-pd-adder' not in client.get(_url(draft)).content.decode()


def test_a_grid_shows_no_pool_panel_at_all(client):
    """A GRID HAS NO AUTHOR POOL (owner's call, 2026-09-19), so the page draws no games panel and no
    adder -- on a draft or published. The page says how it gets answered instead.

    This replaces a test asserting that a published grid KEPT its adder, which was the one edit the
    partial freeze `{'rows', 'pool_remove'}` used to allow. There is no pool to add to now, so the
    freeze is only about slots.
    """
    owner = _signed_in(client, _member('author'))

    draft = _draft(owner, SHAPE_GRID, rows=2)
    body = client.get(_url(draft)).content.decode()
    assert 'data-pd-pool-panel' not in body, 'a grid drew a games panel'
    assert 'data-pd-adder' not in body, 'a grid offered a pool adder'
    assert 'picks their own game for each slot' in body, 'the page never says how a grid is answered'

    svc.update_prompt(draft, owner, is_public=True)
    published = client.get(_url(draft)).content.decode()
    assert 'data-pd-adder' not in published
    assert 'data-pd-row-delete' not in published, 'a published grid must not offer row deletion'


def test_a_tier_list_still_has_its_pool_panel(client):
    """The other half, so the test above cannot pass by the panel disappearing for everybody."""
    owner = _signed_in(client, _member('author'))
    tier = _draft(owner, SHAPE_TIER, rows=1, games=2)

    body = client.get(_url(tier)).content.decode()
    assert 'data-pd-pool-panel' in body
    assert 'data-pd-adder' in body


def test_a_reader_gets_no_tools_at_all(client):
    owner = _member('author')
    prompt = _published(owner, SHAPE_TIER, rows=2)

    _signed_in(client, _member('reader'))
    body = client.get(_url(prompt)).content.decode()

    for marker in ('data-pd-adder', 'data-pd-row-delete', 'data-pd-edit-open',
                   'data-pd-delete', 'data-pd-publish'):
        assert marker not in body, f'a reader was offered {marker}'


def test_the_row_tools_are_gone_for_an_unlinked_owner(client):
    """Every write endpoint refuses an unlinked profile, so the page has to agree. Drawing tools for
    somebody whose every use of them 400s is worse than drawing none."""
    # BUILT LINKED, THEN UNLINKED, because an unlinked profile cannot create one in the first place --
    # `refuse_if_unlinked` is the first thing `create_prompt` does. This is the real-world shape
    # anyway: somebody builds a prompt and later disconnects their PSN account.
    owner = _signed_in(client, _member('author'))
    prompt = _draft(owner, rows=1)
    owner.is_linked = False
    owner.save(update_fields=['is_linked'])

    body = client.get(_url(prompt)).content.decode()
    assert 'data-pd-row-delete' not in body
    assert 'data-pd-edit-open' not in body


def test_a_poll_shows_no_rows_panel_at_all(client):
    """A poll's single bucket is service-created and has no label worth showing, so the whole panel is
    hidden rather than rendered empty.

    THIS IS A PRESENTATION TEST AND NOTHING MORE. An earlier version claimed it proved the bucket CAP
    was doing the work; mutation testing killed that claim -- forcing `can_add_rows` True leaves this
    passing, because the panel is gated on the shape. The guarantee that matters is enforced in
    `create_bucket` and pinned by `test_prompt_service`; the test below states the dependency so this
    one cannot be mistaken for it again."""
    owner = _signed_in(client, _member('author'))
    poll = _draft(owner, SHAPE_POLL, games=2)

    body = client.get(_url(poll)).content.decode()
    assert 'data-pd-rows-panel' not in body
    assert 'data-pd-row-add' not in body


def test_a_poll_can_never_gain_a_second_bucket():
    """THE REAL GUARANTEE, and the reason it cannot live in the template or in a cap the view reads.

    `unique(response, bucket) WHERE single_slot` allows one placement PER BUCKET, so "a hunter votes
    once" holds only while a poll has exactly one bucket. A second bucket buys a second vote with
    every flag set correctly, and the database cannot catch it because the count lives on the parent.
    """
    assert MAX_BUCKETS_PER_PROMPT[SHAPE_POLL] == 1
    owner = _member('author')
    poll = _draft(owner, SHAPE_POLL, games=2)
    assert poll.buckets.count() == 1

    with pytest.raises(svc.PromptError):
        svc.create_bucket(poll, owner, label='A second box')
    assert poll.buckets.count() == 1


# ── publish_blocker ──────────────────────────────────────────────────────────────────────────────

def test_the_publish_reason_is_the_endpoints_own_words(client):
    """The hint and the refusal come from ONE function, so they cannot word the same rule differently.
    A tier list needs five games; this one has two."""
    owner = _signed_in(client, _member('author'))
    prompt = _draft(owner, SHAPE_TIER, games=2, rows=1)

    body = client.get(_url(prompt)).content.decode()
    # The service's sentence, not a paraphrase written in the template.
    assert svc.publish_blocker(prompt) in body
    assert 'at least 5 games' in body
    assert 'data-pd-blocker' in body


def test_a_publishable_draft_has_no_blocker_and_an_enabled_button(client):
    owner = _signed_in(client, _member('author'))
    prompt = _draft(owner, SHAPE_TIER, games=MIN_GAMES_TO_PUBLISH[SHAPE_TIER], rows=1)

    assert svc.publish_blocker(prompt) is None
    body = client.get(_url(prompt)).content.decode()
    assert 'data-pd-blocker' not in body
    # The button is there and is not refusing. `aria-disabled="false"` rather than the ABSENCE of a
    # `disabled` attribute: the button stays in the tab order in both states, so a keyboard user can
    # reach it and hear why it is off.
    publish = body[body.index('data-pd-publish'):body.index('data-pd-publish') + 200]
    assert 'aria-disabled="false"' in publish


def test_publish_blocker_asks_the_same_function_that_refuses():
    """Not a copy of the floor. Proven by driving both through the same state: while the hint is set,
    the write must refuse with exactly that sentence."""
    owner = _member('author')
    prompt = _draft(owner, SHAPE_TIER, games=1, rows=1)

    hint = svc.publish_blocker(prompt)
    assert hint

    with pytest.raises(svc.PromptError) as caught:
        svc.update_prompt(prompt, owner, is_public=True)
    assert str(caught.value) == hint


def test_frozen_acts_is_empty_for_every_draft_and_matches_the_table():
    """A draft freezes NOTHING, whatever its shape -- that is the escape hatch the freeze rules depend
    on, since an author who spots a typo unpublishes, fixes and republishes."""
    owner = _member('author')
    for shape in (SHAPE_TIER, SHAPE_GRID, SHAPE_POLL):
        prompt = _draft(owner, shape, title=f'A {shape}')
        assert svc.frozen_acts(prompt) == frozenset(), shape

    poll = _published(owner, SHAPE_POLL, title='Live poll')
    assert 'pool_add' in svc.frozen_acts(poll)


# ── the page's shape ─────────────────────────────────────────────────────────────────────────────

def test_the_pool_does_not_n_plus_one_over_its_games(client):
    """Compared across two pool sizes rather than against a constant. A per-game cover lookup is the
    failure this shape invites, and it is invisible to every functional assertion."""
    owner = _signed_in(client, _member('author'))
    small = _draft(owner, SHAPE_TIER, title='Small', games=2, rows=1)
    large = _draft(owner, SHAPE_TIER, title='Large', games=12, rows=1)

    def count(prompt):
        # WARMED FIRST. The site chrome (the fundraiser banner, the art-reveal event, the contract
        # ribbon) caches on first render, so an unwarmed comparison measures which page ran first --
        # it read 16 against 12 in favour of the SMALL pool, which is the opposite of an N+1 and would
        # have made this assertion pass for the wrong reason in whichever order pytest happened to run.
        client.get(_url(prompt))
        with CaptureQueriesContext(connection) as ctx:
            assert client.get(_url(prompt)).status_code == 200
        # EVERY QUERY, deliberately unfiltered. The first cut counted only queries naming `prompts_`
        # or `trophies_game` -- and a per-row concept fetch is `FROM trophies_concept`, which matches
        # neither, so the filter removed exactly the queries this test exists to catch. Deleting the
        # `select_related` left it green. Unfiltered plus the warm-up above is what makes the number
        # mean something; it is also the same blind spot the browse suite recorded once already.
        return len(ctx.captured_queries)

    assert count(large) == count(small), 'the pool grew the query count'


def test_the_pool_never_drags_the_igdb_blob_along(client):
    """`raw_response` is the ~30 KB IGDB payload behind the May 2026 web-server OOM, and no cover
    template reads it. Asserted by inspecting the SELECT list, because a `defer()` that is deleted
    changes no behaviour and no query COUNT -- only the bytes, which nothing else here would see."""
    owner = _signed_in(client, _member('author'))
    prompt = _draft(owner, SHAPE_TIER, games=3, rows=1)

    with CaptureQueriesContext(connection) as ctx:
        assert client.get(_url(prompt)).status_code == 200

    # EVERY query on the page, not a filtered subset. An earlier test of this exact shape filtered to
    # queries containing `prompts_` and so removed the cover fetch -- which is
    # `FROM trophies_game JOIN trophies_igdbmatch` -- leaving the assertion to run over nothing.
    offenders = [q['sql'] for q in ctx.captured_queries if 'raw_response' in q['sql']]
    assert offenders == [], f'{len(offenders)} queries selected raw_response'


def test_the_breadcrumb_returns_to_this_prompts_own_shape(client):
    owner = _signed_in(client, _member('author'))
    poll = _published(owner, SHAPE_POLL, title='A poll')

    body = client.get(_url(poll)).content.decode()
    assert reverse('prompts_browse_polls') in body
    assert reverse('prompts_browse_tiers') not in body


def test_the_browse_tile_links_to_the_detail_page(client):
    """It shipped as a `<div>` because there was nowhere to go, and `<a href="#">` would have been
    twenty-four dead tab stops per page. Now that there is a destination it must actually be one."""
    owner = _member('author')
    prompt = _published(owner)

    _signed_in(client, _member('reader'))
    body = client.get('/community/tiers/').content.decode()

    # THE ROOT ELEMENT IS THE ANCHOR, not a link nested inside a div: two links to one destination is
    # two tab stops and two screen-reader announcements for one thing, and it leaves the rest of the
    # tile looking clickable while not being.
    assert f'<a class="pp-gtile pp-ptile" data-prompt-id="{prompt.pk}"' in body
    assert _url(prompt) in body
    assert 'href="#"' not in body, 'a placeholder anchor came back'


# ── the create dialog ────────────────────────────────────────────────────────────────────────────

def test_the_create_dialog_is_offered_to_a_member_and_not_to_an_unlinked_one(client):
    """`create_prompt` refuses an unlinked profile, so the page must not offer the button."""
    _signed_in(client, _member('linked'))
    assert 'data-pr-create-open' in client.get('/community/tiers/').content.decode()

    client.logout()
    _signed_in(client, _member('unlinked', linked=False))
    assert 'data-pr-create-open' not in client.get('/community/tiers/').content.decode()


def test_the_dialog_preselects_the_tab_you_came_from(client):
    """Somebody who pressed "Make one" on the Polls tab meant to make a poll."""
    _signed_in(client, _member('member'))
    body = client.get('/community/polls/').content.decode()

    fieldset = body[body.index('pp-pdlg__shapes'):body.index('</fieldset>')]
    checked = re.findall(r'value="(\w+)"[^>]*checked', fieldset)
    assert checked == [SHAPE_POLL]


def test_every_shape_is_offered_with_its_blurb(client):
    """`shape` is immutable -- `update_prompt` takes no `shape` argument at all -- so this dialog is
    the only place the choice is ever made. Each option carries its explanation for that reason."""
    from prompts.models import SHAPE_BLURBS

    _signed_in(client, _member('member'))
    body = client.get('/community/tiers/').content.decode()

    for shape, blurb in SHAPE_BLURBS.items():
        assert blurb in body, f'{shape} is offered with no explanation'


def test_creating_hands_back_a_server_built_url(client):
    """Never an id the client assembles into a path -- that is what breaks silently the day a route
    moves, which is why `AddGameView` returns `remove_url` too."""
    _signed_in(client, _member('member'))

    resp = client.post(reverse('prompt_create'), {'shape': SHAPE_TIER, 'title': 'New one'})
    assert resp.status_code == 200
    data = resp.json()
    assert data['detail_url'] == reverse('prompt_detail', args=[data['id']])
    assert client.get(data['detail_url']).status_code == 200


def test_a_refused_create_comes_back_with_the_reason(client):
    """The dialog shows the service's own words, so a refusal has to carry them."""
    _signed_in(client, _member('member'))

    resp = client.post(reverse('prompt_create'), {'shape': SHAPE_TIER, 'title': '   '})
    assert resp.status_code == 400
    assert resp.json()['error']


# ── the adder, and the fact that it is shared ────────────────────────────────────────────────────

def test_the_search_is_scoped_to_this_prompt(client):
    """`already_added` is the per-prompt half of the answer. A stranger's prompt must 404 rather than
    answer, or the endpoint is an oracle for whether a draft exists."""
    cache.clear()
    owner = _member('author')
    prompt = _draft(owner)
    concept = ConceptFactory(unified_title='Hollow Knight')
    GameFactory(concept=concept)
    svc.add_concept(prompt, owner, concept)

    # A SECOND PROMPT WITHOUT THAT GAME, which is the whole point: the first version of this test
    # asked only whether the game was flagged on the prompt that HAS it, and a membership check with
    # `prompt=prompt` deleted answers that identically. The question is whether the flag is about
    # THIS prompt, so it takes a prompt where the answer differs.
    other = _draft(owner, title='Somewhere else')

    _signed_in(client, _member('stranger'))
    assert client.get(reverse('prompt_game_search', args=[prompt.pk]),
                      {'q': 'Hollow'}).status_code == 404

    client.logout()
    _signed_in(client, owner)

    def flag(target):
        rows = client.get(reverse('prompt_game_search', args=[target.pk]),
                          {'q': 'Hollow'}).json()['results']
        return [row['already_added'] for row in rows if row['concept_id'] == concept.pk]

    assert flag(prompt) == [True]
    assert flag(other) == [False], 'the flag was about the catalogue, not about this prompt'


def test_the_membership_check_is_bounded_by_the_results_not_the_pool(client):
    """`list(qs.values_list(...))` plus Python membership runs on every keystroke at 120 requests a
    minute, against a pool that can hold two hundred.

    ASSERTED ON THE SQL, NOT ON A QUERY COUNT, and that correction came from mutation testing: both
    shapes are exactly ONE query. Replacing the bounded `WHERE concept_id IN (...)` with a read of the
    entire pool changes how many ROWS come back and nothing a counter can see, so the count-based
    version of this test passed over precisely the anti-pattern it names.
    """
    cache.clear()
    owner = _signed_in(client, _member('author'))
    prompt = _draft(owner, title='Big pool')

    match = ConceptFactory(unified_title='Celeste')
    GameFactory(concept=match)
    svc.add_concept(prompt, owner, match)
    for n in range(20):
        extra = ConceptFactory(unified_title=f'Filler {n}')
        GameFactory(concept=extra)
        svc.add_concept(prompt, owner, extra)

    with CaptureQueriesContext(connection) as ctx:
        assert client.get(reverse('prompt_game_search', args=[prompt.pk]),
                          {'q': 'Celeste'}).status_code == 200

    membership = [q['sql'] for q in ctx.captured_queries
                  if 'prompts_promptgame' in q['sql'] and 'concept_id' in q['sql']]
    assert len(membership) == 1, f'expected one membership query, got {len(membership)}'
    # The bound itself: the query names the concepts it is asking about. Without the `IN`, it is
    # asking for the pool.
    assert ' IN (' in membership[0], 'the membership check reads the whole pool'


def test_the_catalogue_half_is_cached_across_callers():
    """The cache key is the normalized query and NOTHING else -- not the caller, not the container,
    not the viewer. That is what makes the expensive half shareable between the lists adder and this
    one, and it is why `already_added` has to be applied after it."""
    from gamelists.services import game_search

    cache.clear()
    concept = ConceptFactory(unified_title='Hades')
    GameFactory(concept=concept)

    first = game_search.search_concepts('Hades')
    assert [row['concept_id'] for row in first] == [concept.pk]

    with CaptureQueriesContext(connection) as ctx:
        again = game_search.search_concepts('  hades  ')      # trimmed and lowered to the same key
    assert again == first
    assert ctx.captured_queries == [], 'the second caller re-ran the catalogue query'


def test_a_short_term_is_empty_and_a_long_one_is_refused():
    """The asymmetry is deliberate: somebody still typing is not an error and must not paint one."""
    from gamelists.services import game_search

    assert game_search.search_concepts('ab') == []
    with pytest.raises(game_search.SearchRefused):
        game_search.search_concepts('x' * (game_search.MAX_QUERY + 1))


def test_both_adders_go_through_the_one_component():
    """The extraction is only worth having if a caller cannot quietly opt out of it. If either page
    grows its own typeahead again, the four bug fixes in `GameAdder` stop reaching it."""
    utils = _read('static/js/utils.js')
    assert 'window.PlatPursuit.GameAdder' in utils

    for path in ('static/js/list-detail.js', 'static/js/prompt-detail.js'):
        js = _read(path)
        assert 'GameAdder(' in js, f'{path} no longer uses the shared adder'
        # The tell-tale of a re-implementation: its own debounced search against the endpoint.
        assert 'API.get(' not in js, f'{path} is running its own typeahead again'


def test_the_prompt_adders_floor_matches_the_endpoint():
    """Two copies of a threshold drift. Below the endpoint's floor the search answers an empty list,
    so a client that keeps firing is pure latency."""
    from gamelists.services import game_search

    js = _read('static/js/prompt-detail.js')
    match = re.search(r'MIN_QUERY\s*=\s*(\d+)', js)
    assert match, 'the adder no longer declares a MIN_QUERY'
    assert int(match.group(1)) == game_search.MIN_QUERY


# ── adding several games from one search ─────────────────────────────────────────────────────────

def test_adding_a_game_never_reloads_the_page():
    """THE REGRESSION THIS SUITE EXISTS FOR. `GameAdder` deliberately leaves its results panel open
    after an add so several games can be picked from one search -- and the first cut of `onAdded`
    called `window.location.reload()`, which threw the whole page away and took the open panel with
    it. Adding three games meant searching three times.

    Asserted against the two ADD paths specifically, not against the file: publishing and unpublishing
    reload on purpose, because they change which controls may exist and that derivation is the
    server's."""
    js = _read('static/js/prompt-detail.js')

    reloads = js.count('window.location.reload()')
    assert reloads == 1, f'{reloads} reloads; only the publish/unpublish one is intended'
    # And it is the one inside the visibility wiring.
    visibility = js[js.index('function wireVisibility('):js.index('function wireDelete(')]
    assert 'window.location.reload()' in visibility

    # Both add paths go through the partial refresh instead.
    assert js.count("refreshPanel('pool')") >= 1
    assert js.count("refreshPanel('rows')") >= 1


def test_each_panel_refreshes_from_its_own_fragment(client):
    """The panel is re-rendered by the SERVER, so a pool card keeps its server-built URLs and its
    batched cover art rather than being mirrored in JS."""
    owner = _signed_in(client, _member('author'))
    prompt = _draft(owner, SHAPE_TIER, games=2, rows=2)

    pool = client.get(_url(prompt), {'part': 'pool'})
    assert pool.status_code == 200
    body = pool.content.decode()
    assert 'data-pd-game' in body
    # A FRAGMENT, not the page: no chrome, no header, no adder to destroy.
    assert '<html' not in body
    assert 'data-pd-adder' not in body, 'the adder is inside the swap and would be destroyed'

    rows = client.get(_url(prompt), {'part': 'rows'}).content.decode()
    assert 'data-pd-row' in rows
    assert '<html' not in rows
    assert 'data-pd-row-add' not in rows, 'the add form is inside the swap and would lose focus'


def test_an_unknown_part_falls_through_to_the_full_page(client):
    """`?part=` is attacker-controlled. A template name taken from the querystring would render any
    template in the tree with this page's context; the whitelist is what stops that, and an unknown
    value shows the prompt rather than erroring."""
    owner = _signed_in(client, _member('author'))
    prompt = _draft(owner, title='Still here')

    for part in ('base.html', '../../settings.py', 'nope', ''):
        resp = client.get(_url(prompt), {'part': part})
        assert resp.status_code == 200, part
        assert 'Still here' in resp.content.decode(), part


def test_the_fragment_respects_the_same_gate_and_visibility(client):
    """A fragment route is a second door onto the same data, and a second door is where a permission
    check gets forgotten. It is the same view, so it inherits both -- asserted, not assumed."""
    prompt = _draft(_member('author'))

    _signed_in(client, _member('stranger'))
    assert client.get(_url(prompt), {'part': 'pool'}).status_code == 404

    client.logout()
    _signed_in(client, _member('outsider', premium=False))
    assert client.get(_url(prompt), {'part': 'pool'}).status_code == 302


def test_the_pool_carries_the_cap_the_client_compares_against():
    """The adder is retired at the cap between renders by comparing the count to `data-max`. That
    number is rendered by the server from `MAX_GAMES_PER_PROMPT`, so the client never holds the rule."""
    from prompts.models import MAX_GAMES_PER_PROMPT

    markup = _read('templates/prompts/detail.html')
    assert 'data-max="{{ max_games }}"' in markup
    assert 'data-max="{{ max_buckets }}"' in markup
    # And the view supplies it from the model's table rather than a literal.
    assert MAX_GAMES_PER_PROMPT[SHAPE_POLL] == 20


# ── the publish gate stays current ───────────────────────────────────────────────────────────────

def test_adding_a_slot_clears_the_grids_publish_blocker(client):
    """THE REPORTED BUG. A new grid says "Add at least one row"; the author adds slots; the page kept
    saying it, with Publish disabled, because the reason was computed once at load and adds had
    stopped reloading the page. The gate fragment is what the page now re-asks, so it must answer with
    the CURRENT state."""
    owner = _signed_in(client, _member('author'))
    grid = _draft(owner, SHAPE_GRID)
    url = _url(grid)

    before = client.get(url, {'part': 'publish'}).content.decode()
    assert 'data-pd-blocker' in before, 'a slotless grid should be blocked'

    svc.create_bucket(grid, owner, label='Best combat')

    after = client.get(url, {'part': 'publish'}).content.decode()
    assert 'data-pd-blocker' not in after, 'the gate still blocks a grid that now has a slot'
    assert '<html' not in after


def test_the_gate_follows_the_tier_floor_game_by_game(client):
    """Same bug on the other shape: the tier floor is five games, added one at a time."""
    owner = _signed_in(client, _member('author'))
    tier = _draft(owner, SHAPE_TIER, rows=1, games=MIN_GAMES_TO_PUBLISH[SHAPE_TIER] - 1)
    url = _url(tier)

    assert 'data-pd-blocker' in client.get(url, {'part': 'publish'}).content.decode()

    concept = ConceptFactory()
    GameFactory(concept=concept)
    svc.add_concept(tier, owner, concept)
    assert 'data-pd-blocker' not in client.get(url, {'part': 'publish'}).content.decode()


def test_every_structural_change_re_asks_the_publish_gate():
    """Each of these changes what `publish_blocker` would say. One that forgets to re-ask leaves the
    button stuck in whatever state the page loaded with, which is exactly how the bug shipped."""
    js = _read('static/js/prompt-detail.js')

    def body(start, end):
        return js[js.index(start):js.index(end, js.index(start))]

    sites = {
        'add a game': body('onAdded: function (data) {', "refreshPanel('pool')"),
        'remove a game': body("announce('Removed ' + name", '})'),
        # Anchored on the row form's own label field. `post(form.dataset.url, body)` appears FIRST in
        # the settings form, so a slice starting there ran through the settings save, found ITS call,
        # and passed with the row add's call deleted -- mutation testing caught it.
        'add a row': body("querySelector('[data-pd-row-label]')", "refreshPanel('rows')"),
        'delete a row': body("announce('Deleted ' + name", '})'),
        'save settings': body("announce('Saved.');", '})'),
    }
    for what, code in sites.items():
        assert 'refreshPublishGate()' in code, f'{what} leaves the publish button stale'


def test_the_publish_button_follows_the_servers_answer_not_a_rule():
    """The button is disabled exactly when the server sent a reason. No floor, no row count and no
    shape check in the client: those live in `publish_blocker`, stated once."""
    js = _read('static/js/prompt-detail.js')
    gate = js[js.index('function refreshPublishGate('):js.index('function syncAdderVisibility(')]

    # Derived from whether the server's fragment carried any text, and from nothing else.
    assert 'var now = gate.textContent.trim();' in gate
    assert "button.setAttribute('aria-disabled', now !== '' ? 'true' : 'false');" in gate
    for rule in ('MIN_GAMES', 'game_count', 'shape', '>= 5', 'buckets'):
        assert rule not in gate, f'the client is restating the publish rule ({rule})'


# -- what the audits found ------------------------------------------------------------------------

def test_the_pool_fragment_is_not_served_for_a_shape_with_no_pool(client):
    """A route nothing links to is still a route. The pool panel is not rendered for a grid, but
    `?part=pool` stayed live and answered with the TIER LIST's empty copy -- "No games yet. Add the
    ones people will be sorting." -- to anybody who could read the prompt."""
    owner = _signed_in(client, _member('author'))
    grid = _draft(owner, SHAPE_GRID, rows=2)

    body = client.get(_url(grid), {'part': 'pool'}).content.decode()
    assert 'Add the ones people will be sorting' not in body
    # It falls through to the whole page, the same as any unknown `part`.
    assert '<html' in body


def test_the_tile_says_they_pick_the_games_from_the_shape_not_from_missing_art(client):
    """`cover_items` is empty whenever the first four games have no trophy list -- `cover_games_for`'s
    documented contract for a stub or reassigned concept. Deriving the fact from that made a TIER
    LIST with missing art announce "they pick the games", which is false, and hid its real game count
    in the same branch."""
    owner = _member('author')
    # A published tier list whose concepts have NO Game rows, so `cover_items` comes back empty.
    tier = svc.create_prompt(owner, shape=SHAPE_TIER, title='No art here')
    for _ in range(MIN_GAMES_TO_PUBLISH[SHAPE_TIER]):
        svc.add_concept(tier, owner, ConceptFactory())
    svc.update_prompt(tier, owner, is_public=True)

    _signed_in(client, _member('reader'))
    body = client.get('/community/tiers/').content.decode()

    assert 'they pick the games' not in body, 'a tier list claimed its respondents pick the games'
    assert '5 games' in body, 'the real game count was hidden by the same branch'


def test_a_grid_tile_does_say_it(client):
    """The other direction, so the test above cannot pass by the line disappearing for everybody."""
    owner = _member('author')
    grid = svc.create_prompt(owner, shape=SHAPE_GRID, title='Nine questions')
    svc.create_bucket(grid, owner, label='Best combat')
    svc.update_prompt(grid, owner, is_public=True)

    _signed_in(client, _member('reader'))
    assert 'they pick the games' in client.get('/community/grids/').content.decode()


def test_the_publish_button_stays_reachable_while_it_is_refusing():
    """`disabled` removes a button from the tab order, so a keyboard or screen-reader user cannot
    land on it to discover why it is off -- and the reason sat in an unassociated sibling."""
    markup = _read('templates/prompts/detail.html')

    assert 'aria-describedby="pd-publish-reason"' in markup
    assert 'id="pd-publish-reason"' in markup
    assert 'aria-disabled="{% if publish_blocker %}true{% else %}false{% endif %}"' in markup
    assert '{% if publish_blocker %}disabled{% endif %}' not in markup

    js = _read('static/js/prompt-detail.js')
    gate = js[js.index('function refreshPublishGate('):js.index('function syncAdderVisibility(')]
    assert 'button.disabled' not in gate, 'the refresh put the real disabled attribute back'
    # ...and the handler is what refuses, since the button is still clickable.
    vis = js[js.index('function wireVisibility('):js.index('function wireDelete(')]
    assert "getAttribute('aria-disabled') === 'true'" in vis


def test_a_failed_write_shows_the_services_words_not_a_status_code():
    """`API.request` throws `API request failed: 400` WITHOUT reading the body, so every caller that
    showed `err.message` showed that. Each `PromptError` is written for the hunter -- "a tier list
    needs at least 5 games before it goes up. This one has 3" -- and all of it was being discarded.

    Pinned in `postJson`, which both this page and the lists page go through."""
    utils = _read('static/js/utils.js')
    body = utils[utils.index('function postJson('):utils.index('window.PlatPursuit.postJson')]

    assert 'err.response.json()' in body, 'the refusal body is never read'
    assert 'payload.error' in body, 'the service message is never lifted onto the error'


def test_a_refresh_cannot_land_out_of_order():
    """A sequence NUMBER cannot do this job: htmx swaps INSIDE the request handler and resolves
    afterwards, so a stale response is already in the DOM by the time any `.then` guard runs. Two
    quick adds could leave five cards under a header reading six."""
    js = _read('static/js/prompt-detail.js')

    assert 'refreshChain' in js and 'gateChain' in js
    assert 'refreshSeq' not in js, 'the counter that could not work is back'
    panel = js[js.index('function refreshPanel('):js.index('function refreshPublishGate(')]
    # The chain must be READ, not merely written. Asserting `refreshChain[which]` alone passed with
    # the queueing removed, because the assignment at the end of the function still matched.
    assert 'refreshChain[which].catch(' in panel, 'refreshes are not queued behind each other'
    assert 'refreshChain[which] = run;' in panel, 'the next refresh will not queue behind this one'


def test_a_failed_panel_refresh_never_reports_the_write_as_failed():
    """`refreshPanel` throws by design when the swap does not land. Chained into the write's catch,
    that reported "That could not be added." for a row the server HAD created -- with the field left
    full, so the obvious next move was to press Add again and make a duplicate."""
    js = _read('static/js/prompt-detail.js')
    start = js.index("querySelector('[data-pd-row-label]')")
    add = js[start:js.index('function syncRowAddVisibility(') if
             js.index('function syncRowAddVisibility(') > start else len(js)]
    add = js[start:start + 2500]

    assert 'rows refresh after add' in add, 'the refresh failure has no catch of its own'
    assert 'Reload to see it' in add


def test_the_caps_retire_and_restore_both_add_controls():
    """Both directions, both panels. Hiding the adder at the cap shipped; bringing it back after a
    removal did not, so a filled poll kept a hidden adder until a reload. The rows form had no
    equivalent at all, which raising the grid slot cap to 36 made reachable in one sitting."""
    js = _read('static/js/prompt-detail.js')

    assert 'function syncRowAddVisibility(' in js

    # SLICED TO THE HANDLER, not to the end of the file. The first cut sliced from the selector to
    # EOF, so both assertions matched the call sites further down and passed with the removal-path
    # calls deleted -- the exact half-done state this test names.
    game_remove = js[js.index("closest('[data-pd-game-remove]')"):
                     js.index('var adder = panel.querySelector')]
    assert 'syncAdderVisibility' in game_remove, 'the adder never comes back under the cap'

    row_delete = js[js.index("closest('[data-pd-row-delete]')"):
                    js.index("panel.querySelector('[data-pd-row-add]')")]
    assert 'syncRowAddVisibility' in row_delete, 'the row form never comes back under the cap'


def test_the_status_line_exists_for_a_liker_too():
    """`wireLike` is wired for non-owners, but the live region was `{% if can_edit %}`, so every
    announcement in a liker's error path went to a null element -- and the toast container carries no
    `aria-live`, so the failure was entirely silent for a screen-reader user."""
    markup = _read('templates/prompts/detail.html')
    assert '{% if can_edit or can_like %}' in markup


def test_the_reorder_ids_use_the_managers_own_attribute():
    """`DragReorderManager` builds its id list from `evt.item.dataset.itemId`. A differently-named
    attribute reorders correctly ON SCREEN and posts a list of `undefined` -- a failure with no
    visible symptom until the page is reloaded."""
    for path in ('templates/prompts/partials/detail_row.html',
                 'templates/prompts/partials/detail_game.html'):
        markup = _read(path)
        assert 'data-item-id=' in markup, f'{path} would post undefined ids'
