"""The author's write endpoints.

Twelve routes over one service. The service's own rules are tested next door; what these have to get
right is the thin layer above them, and that layer has exactly four jobs:

* refuse anybody outside the beta, on every one of them;
* answer 404 for a prompt the caller cannot see, so an id alone never confirms one exists;
* resolve sub-resources WITHIN the prompt, never by bare id;
* turn a `PromptError` into a 400 a client can display, and nothing else into anything.

The fourth is the one worth watching: every endpoint here catches `PromptError` and lets everything
else through as a 500, which is correct -- but it means any refusal the service forgets to raise as
`PromptError` reaches a JSON client as an HTML error page.
"""
import pytest
from django.urls import reverse

from prompts.models import (MIN_GAMES_TO_PUBLISH, SHAPE_GRID, SHAPE_POLL, SHAPE_TIER, Prompt,
                            PromptBucket, PromptGame)
from prompts.services import prompt_service as svc
from tests.factories import ConceptFactory, ProfileFactory

pytestmark = pytest.mark.django_db


def _member(psn='member', premium=True):
    profile = ProfileFactory(is_linked=True, psn_username=psn)
    if premium:
        profile.user_is_premium = True
        profile.save(update_fields=['user_is_premium'])
    return profile


def _draft(owner, shape=SHAPE_TIER, *, title='Rank them', games=0):
    """A grid arrives as a 2x2 rectangle with its slots named.

    Its slots cannot be added one at a time, and an unnamed one blocks publishing -- the placeholder
    doing its job -- so `_published` would fail for a grid without this.
    """
    prompt = svc.create_prompt(owner, shape=shape, title=title, grid_columns=2, grid_rows=2)
    if shape == SHAPE_GRID:
        for n, bucket in enumerate(prompt.buckets.order_by('position')):
            svc.update_bucket(bucket, owner, label=f'Question {n + 1}')
    for _ in range(games):
        svc.add_concept(prompt, owner, ConceptFactory())
    return prompt


def _published(owner, shape=SHAPE_TIER, **kwargs):
    prompt = _draft(owner, shape, games=MIN_GAMES_TO_PUBLISH[shape], **kwargs)
    svc.update_prompt(prompt, owner, is_public=True)
    prompt.refresh_from_db()
    return prompt


# ── the gate, on every endpoint ───────────────────────────────────────────────────────────────────


def test_every_write_endpoint_is_behind_the_beta_gate(client):
    """Twelve routes is twelve chances to forget the mixin, and a write endpoint that forgets it is
    worse than a page that does: it does not merely show somebody something, it lets them change
    it."""
    owner = _member('owner')
    prompt = _published(owner)
    game = prompt.games.first()
    bucket = prompt.buckets.first()
    client.force_login(ProfileFactory(is_linked=True, psn_username='outsider').user)

    routes = [
        reverse('prompt_create'),
        reverse('prompt_update', args=[prompt.pk]),
        reverse('prompt_close', args=[prompt.pk]),
        reverse('prompt_delete', args=[prompt.pk]),
        reverse('prompt_like', args=[prompt.pk]),
        reverse('prompt_add_game', args=[prompt.pk]),
        reverse('prompt_reorder_games', args=[prompt.pk]),
        reverse('prompt_remove_game', args=[prompt.pk, game.pk]),
        reverse('prompt_create_bucket', args=[prompt.pk]),
        reverse('prompt_reorder_buckets', args=[prompt.pk]),
        reverse('prompt_update_bucket', args=[prompt.pk, bucket.pk]),
        reverse('prompt_delete_bucket', args=[prompt.pk, bucket.pk]),
    ]
    assert len(routes) == 12, 'a route was added without extending this test'

    for route in routes:
        resp = client.post(route)
        assert resp.status_code == 302, f'{route} let an outsider through ({resp.status_code})'
        assert resp['Location'] == '/beta-access/'

    prompt.refresh_from_db()
    assert prompt.is_public is True and prompt.games.count() == MIN_GAMES_TO_PUBLISH[SHAPE_TIER]


# ── creating ──────────────────────────────────────────────────────────────────────────────────────


def test_creating_one_answers_with_its_id(client):
    owner = _member('owner')
    client.force_login(owner.user)

    resp = client.post(reverse('prompt_create'),
                       {'shape': SHAPE_POLL, 'title': 'Which one?'})

    assert resp.status_code == 200
    body = resp.json()
    prompt = Prompt.objects.get(pk=body['id'])
    assert prompt.shape == SHAPE_POLL
    assert prompt.owner_id == owner.pk
    assert prompt.is_public is False, 'created public; publishing is a second act'
    assert prompt.buckets.count() == 1, "the poll's single bucket was not created"


def test_a_refusal_comes_back_as_a_readable_400(client):
    """Every endpoint turns `PromptError` into a 400 carrying the service's own words, because those
    words are written for the hunter -- the view has nothing to add and should not paraphrase."""
    owner = _member('owner')
    client.force_login(owner.user)

    resp = client.post(reverse('prompt_create'), {'shape': 'bracket', 'title': 'Not yet'})

    assert resp.status_code == 400
    assert 'shapes' in resp.json()['error']
    assert not Prompt.objects.exists()


# ── visibility ────────────────────────────────────────────────────────────────────────────────────


def test_somebody_elses_draft_answers_404_and_not_403(client):
    """A 403 confirms the prompt exists and that it is not yours. A 404 says nothing, which is the
    only honest answer to an id you were never given."""
    draft = _draft(_member('owner'), games=2)
    client.force_login(_member('stranger').user)

    for route in (reverse('prompt_update', args=[draft.pk]),
                  reverse('prompt_delete', args=[draft.pk]),
                  reverse('prompt_add_game', args=[draft.pk])):
        resp = client.post(route)
        assert resp.status_code == 404, route
        assert resp.json()['error']


def test_a_public_prompt_resolves_but_ownership_still_refuses(client):
    """Readable and writable are different questions. The 404 is about the first; the 400 that follows
    is the service answering the second."""
    prompt = _published(_member('owner'))
    client.force_login(_member('stranger').user)

    resp = client.post(reverse('prompt_update', args=[prompt.pk]), {'title': 'Mine now'})

    assert resp.status_code == 400
    prompt.refresh_from_db()
    assert prompt.title == 'Rank them'


def test_a_sub_resource_from_another_prompt_answers_404(client):
    """The schema does not tie a bucket or a pool row to the prompt a caller names, so this lookup is
    the thing that does. Resolving by bare id would let an author edit a row of somebody else's
    prompt through their own prompt's URL."""
    owner = _member('owner')
    mine = _published(owner)
    theirs = _published(_member('other'), title='Theirs')
    client.force_login(owner.user)

    resp = client.post(reverse('prompt_update_bucket', args=[mine.pk, theirs.buckets.first().pk]),
                       {'label': 'Renamed'})
    assert resp.status_code == 404

    resp = client.post(reverse('prompt_remove_game', args=[mine.pk, theirs.games.first().pk]))
    assert resp.status_code == 404

    assert theirs.buckets.first().label != 'Renamed'
    assert theirs.games.count() == MIN_GAMES_TO_PUBLISH[SHAPE_TIER]


# ── the round trip ────────────────────────────────────────────────────────────────────────────────


def test_an_author_can_build_and_publish_a_tier_list_entirely_over_http(client):
    """The path a real author takes, end to end, through the endpoints rather than the service --
    which is the only way to find out that they compose."""
    owner = _member('owner')
    client.force_login(owner.user)

    created = client.post(reverse('prompt_create'),
                          {'shape': SHAPE_TIER, 'title': 'Rank the Souls games'}).json()
    prompt_id = created['id']

    for _ in range(MIN_GAMES_TO_PUBLISH[SHAPE_TIER]):
        resp = client.post(reverse('prompt_add_game', args=[prompt_id]),
                           {'concept_id': ConceptFactory().pk})
        assert resp.status_code == 200, resp.content
        # The remove route comes from the SERVER, never assembled by the client from an id.
        assert resp.json()['remove_url'].endswith('/remove/')

    published = client.post(reverse('prompt_update', args=[prompt_id]), {'is_public': 'true'})
    assert published.status_code == 200
    assert published.json()['is_public'] is True

    prompt = Prompt.objects.get(pk=prompt_id)
    assert prompt.is_public is True
    assert prompt.game_count == MIN_GAMES_TO_PUBLISH[SHAPE_TIER]
    assert prompt.buckets.count() == 5, 'the seeded tier rows went missing'


def test_publishing_too_early_is_refused_with_the_reason(client):
    owner = _member('owner')
    prompt = _draft(owner, games=2)
    client.force_login(owner.user)

    resp = client.post(reverse('prompt_update', args=[prompt.pk]), {'is_public': 'true'})

    assert resp.status_code == 400
    assert '5 games' in resp.json()['error']
    prompt.refresh_from_db()
    assert prompt.is_public is False


def test_one_field_at_a_time_does_not_clear_the_others(client):
    """Every field is forwarded only when PRESENT in the body. A client sending a rename must not
    blank the description, and a client sending a publish must not rename anything."""
    owner = _member('owner')
    prompt = _draft(owner, games=MIN_GAMES_TO_PUBLISH[SHAPE_TIER])
    svc.update_prompt(prompt, owner, description='The original description')
    client.force_login(owner.user)

    client.post(reverse('prompt_update', args=[prompt.pk]), {'title': 'A new title'})

    prompt.refresh_from_db()
    assert prompt.title == 'A new title'
    assert prompt.description == 'The original description'


# ── rows and pool ─────────────────────────────────────────────────────────────────────────────────


def test_rows_can_be_added_renamed_reordered_and_removed(client):
    """ON A TIER LIST, because a grid no longer has per-row add or delete: its slots are a rectangle
    resized as a whole, and those two controls are exactly what make one ragged. The grid's own
    endpoints are covered by `test_a_grid_refuses_the_per_slot_endpoints` below."""
    owner = _member('owner')
    prompt = _draft(owner, SHAPE_TIER, title='Tier')
    client.force_login(owner.user)
    prompt.buckets.all().delete()          # start from nothing, so the ids below are this test's

    made = client.post(reverse('prompt_create_bucket', args=[prompt.pk]),
                       {'label': 'Best combat'}).json()
    second = client.post(reverse('prompt_create_bucket', args=[prompt.pk]),
                         {'label': 'Best story'}).json()

    client.post(reverse('prompt_update_bucket', args=[prompt.pk, made['bucket_id']]),
                {'label': 'Best fights', 'colour': 'red'})
    bucket = PromptBucket.objects.get(pk=made['bucket_id'])
    assert (bucket.label, bucket.colour) == ('Best fights', 'red')

    client.post(reverse('prompt_reorder_buckets', args=[prompt.pk]),
                {'bucket_ids[]': [second['bucket_id'], made['bucket_id']]})
    assert list(prompt.buckets.order_by('position').values_list('pk', flat=True)) == [
        second['bucket_id'], made['bucket_id']]

    client.post(reverse('prompt_delete_bucket', args=[prompt.pk, made['bucket_id']]))
    assert prompt.buckets.count() == 1


def test_a_grid_refuses_the_per_slot_endpoints_and_resizes_instead(client):
    """THE DOORS, not just the buttons. Removing the controls from the template is an affordance; a
    hand-posted request still reaches the endpoint, and one added or deleted slot makes the rectangle
    ragged. Both refuse, and the resize route is what a grid has instead."""
    owner = _member('owner')
    prompt = _draft(owner, SHAPE_GRID, title='Grid')
    client.force_login(owner.user)
    first = prompt.buckets.order_by('position').first()

    added = client.post(reverse('prompt_create_bucket', args=[prompt.pk]), {'label': 'Sneaky'})
    assert added.status_code == 400
    assert 'rectangle' in added.json()['error']

    removed = client.post(reverse('prompt_delete_bucket', args=[prompt.pk, first.pk]))
    assert removed.status_code == 400
    assert 'rectangle' in removed.json()['error']

    before = prompt.buckets.count()
    resized = client.post(reverse('prompt_resize_grid', args=[prompt.pk]),
                          {'columns': '3', 'rows': '3'})
    assert resized.status_code == 200
    assert resized.json() == {'grid_columns': 3, 'slots': 9}
    assert prompt.buckets.count() == 9 != before


def test_the_pool_can_be_filled_reordered_and_emptied(client):
    owner = _member('owner')
    prompt = _draft(owner)
    client.force_login(owner.user)

    ids = []
    for _ in range(3):
        ids.append(client.post(reverse('prompt_add_game', args=[prompt.pk]),
                               {'concept_id': ConceptFactory().pk}).json()['game_id'])

    client.post(reverse('prompt_reorder_games', args=[prompt.pk]),
                {'game_ids[]': list(reversed(ids))})
    assert list(prompt.games.order_by('position').values_list('pk', flat=True)) == list(reversed(ids))

    resp = client.post(reverse('prompt_remove_game', args=[prompt.pk, ids[0]]))
    assert resp.json()['game_count'] == 2
    assert not PromptGame.objects.filter(pk=ids[0]).exists()


def test_a_game_that_does_not_exist_is_a_refusal_not_a_crash(client):
    owner = _member('owner')
    prompt = _draft(owner)
    client.force_login(owner.user)

    for body in ({'concept_id': 99999999}, {'concept_id': 'abc'}, {}):
        resp = client.post(reverse('prompt_add_game', args=[prompt.pk]), body)
        assert resp.status_code == 400, body
        assert resp.json()['error']


# ── likes ─────────────────────────────────────────────────────────────────────────────────────────


def test_liking_over_http_moves_the_counter_both_ways(client):
    prompt = _published(_member('owner'))
    fan = _member('fan')
    client.force_login(fan.user)

    assert client.post(reverse('prompt_like', args=[prompt.pk]),
                       {'liked': 'true'}).json()['like_count'] == 1
    assert client.post(reverse('prompt_like', args=[prompt.pk]),
                       {'liked': 'false'}).json()['like_count'] == 0


def test_liking_your_own_is_refused_in_words(client):
    owner = _member('owner')
    prompt = _published(owner)
    client.force_login(owner.user)

    resp = client.post(reverse('prompt_like', args=[prompt.pk]), {'liked': 'true'})

    assert resp.status_code == 400
    assert 'your own' in resp.json()['error']


# ── closing and deleting ──────────────────────────────────────────────────────────────────────────


def test_closing_and_reopening(client):
    owner = _member('owner')
    prompt = _published(owner)
    client.force_login(owner.user)

    assert client.post(reverse('prompt_close', args=[prompt.pk]),
                       {'closed': 'true'}).json()['is_closed'] is True
    assert client.post(reverse('prompt_close', args=[prompt.pk]),
                       {'closed': 'false'}).json()['is_closed'] is False


def test_deleting_is_a_soft_delete_and_the_prompt_leaves_the_browse(client):
    owner = _member('owner')
    prompt = _published(owner, title='Going away')
    client.force_login(owner.user)

    assert client.post(reverse('prompt_delete', args=[prompt.pk])).json()['deleted'] is True

    prompt.refresh_from_db()
    assert prompt.is_deleted is True
    assert 'Going away' not in client.get('/community/tiers/').content.decode()
