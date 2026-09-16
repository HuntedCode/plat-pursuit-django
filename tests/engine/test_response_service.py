"""The only thing that writes a response.

A response is one hunter's arrangement of somebody else's prompt. This file leads with the five rules
the module owns, because they are the ones that are invisible until they are wrong:

* **`single_slot` comes from the prompt.** The partial unique's predicate is per-ROW, so a placement
  carrying the wrong flag does not collide with the correct row beside it -- one wrong row makes a
  poll accept two votes, and nothing raises.
* **The response is born lazily**, on the first placement, never when an editor opens.
* **Both sides of a placement resolve within the prompt**, because the schema does not tie them.
* **A single-slot bucket evicts rather than refuses** -- which on a poll is "change your vote".
* **`placement_count` is the numerator only**, recomputed from rows.
"""
import pytest

from prompts.models import (MAX_PLACEMENTS_PER_RESPONSE, SHAPE_GRID, SHAPE_POLL, SHAPE_TIER,
                            PromptPlacement, PromptResponse)
from prompts.services import prompt_service as psvc
from prompts.services import response_service as svc
from tests.factories import ConceptFactory, ProfileFactory
from users.models import UserRestriction

pytestmark = pytest.mark.django_db


def _hunter(psn='hunter', premium=False):
    profile = ProfileFactory(is_linked=True, psn_username=psn)
    if premium:
        profile.user_is_premium = True
        profile.save(update_fields=['user_is_premium'])
    return profile


def _restrict(profile):
    UserRestriction.objects.create(user=profile.user, profile=profile, scope='all_ugc',
                                   reason='testing', created_by_label='Admin')


def _built(owner, shape=SHAPE_TIER, games=3, public=True):
    """A prompt with a pool, ready to be answered."""
    prompt = psvc.create_prompt(owner, shape=shape, title='Rank them', is_public=public)
    pool = [psvc.add_concept(prompt, owner, ConceptFactory()) for _ in range(games)]
    if shape == SHAPE_GRID:
        for label in ('Best combat', 'Best story'):
            psvc.create_bucket(prompt, owner, label=label)
    return prompt, pool, list(prompt.buckets.order_by('position'))


# ── the flag ──────────────────────────────────────────────────────────────────────────────────────


def test_the_flag_is_taken_from_the_prompt_and_never_from_a_caller():
    """THE SHARPEST EDGE IN THE SCHEMA, and this module is the only thing standing on it.

    `unique(response, bucket) WHERE single_slot` has a per-ROW predicate: a placement written with
    `single_slot=False` is not merely unconstrained, it is invisible to the index and will not collide
    with the correctly-flagged row beside it. So `place()` takes no flag argument at all."""
    import inspect

    assert 'single_slot' not in inspect.signature(svc.place).parameters

    owner = _hunter()
    for shape, expected in ((SHAPE_TIER, False), (SHAPE_POLL, True), (SHAPE_GRID, True)):
        prompt, pool, buckets = _built(_hunter(f'owner-{shape}'), shape=shape, games=2)
        answerer = _hunter(f'answerer-{shape}')
        placement = svc.place(prompt, answerer, game_id=pool[0].pk, bucket_id=buckets[0].pk)
        assert placement.single_slot is expected, f'{shape} wrote the wrong flag'


# ── lazily born ───────────────────────────────────────────────────────────────────────────────────


def test_no_response_exists_until_the_first_placement():
    """A `get_or_create` on a GET is a write on a GET -- a prefetch, a crawler or a double render all
    trigger it -- and it would fill `response_count`, which browse sorts on, with rows nobody
    authored."""
    owner = _hunter()
    prompt, pool, buckets = _built(owner)
    answerer = _hunter('answerer')

    assert svc.response_for(prompt, answerer) is None
    assert not PromptResponse.objects.filter(prompt=prompt).exists()

    svc.place(prompt, answerer, game_id=pool[0].pk, bucket_id=buckets[0].pk)
    assert svc.response_for(prompt, answerer) is not None
    assert PromptResponse.objects.filter(prompt=prompt).count() == 1


def test_a_signed_out_reader_has_no_response_and_asking_does_not_make_one():
    prompt, _pool, _buckets = _built(_hunter())
    assert svc.response_for(prompt, None) is None
    assert not PromptResponse.objects.exists()


# ── resolution ────────────────────────────────────────────────────────────────────────────────────


def test_a_game_or_row_from_another_prompt_is_refused():
    """Nothing in the schema ties a placement's three foreign keys to one prompt: such a row satisfies
    every constraint and is then invisible to the editor while still being counted. Both sides are
    resolved through the prompt, so neither can come from somewhere else."""
    mine, my_pool, my_buckets = _built(_hunter('a'))
    theirs, their_pool, their_buckets = _built(_hunter('b'))
    answerer = _hunter('answerer')

    with pytest.raises(psvc.PromptError):
        svc.place(mine, answerer, game_id=their_pool[0].pk, bucket_id=my_buckets[0].pk)
    with pytest.raises(psvc.PromptError):
        svc.place(mine, answerer, game_id=my_pool[0].pk, bucket_id=their_buckets[0].pk)

    assert not PromptPlacement.objects.exists()


# ── eviction ──────────────────────────────────────────────────────────────────────────────────────


def test_a_second_vote_replaces_the_first_rather_than_being_refused():
    """On a poll this rule IS "change your vote", which is what a hunter expects a second tap to do.

    The eviction happens before the write, so the partial unique never has two rows to choose
    between."""
    owner = _hunter()
    poll, pool, buckets = _built(owner, shape=SHAPE_POLL, games=3)
    voter = _hunter('voter')

    svc.place(poll, voter, game_id=pool[0].pk, bucket_id=buckets[0].pk)
    svc.place(poll, voter, game_id=pool[1].pk, bucket_id=buckets[0].pk)

    response = svc.response_for(poll, voter)
    assert response.placements.count() == 1, 'the poll took two votes'
    assert response.placements.get().prompt_game_id == pool[1].pk
    response.refresh_from_db()
    assert response.placement_count == 1


def test_filling_an_occupied_grid_slot_returns_the_occupant_to_the_tray():
    """A deck metaphor: the card that was there comes back rather than the drop being rejected. The
    evicted game is unplaced, not removed from the pool -- it is still available to place elsewhere."""
    owner = _hunter()
    grid, pool, buckets = _built(owner, shape=SHAPE_GRID, games=3)
    combat = buckets[0]
    answerer = _hunter('answerer')

    svc.place(grid, answerer, game_id=pool[0].pk, bucket_id=combat.pk)
    svc.place(grid, answerer, game_id=pool[1].pk, bucket_id=combat.pk)

    response = svc.response_for(grid, answerer)
    assert [p.prompt_game_id for p in response.placements.all()] == [pool[1].pk]
    # ...and the evicted game can go somewhere else, because it never left the pool.
    svc.place(grid, answerer, game_id=pool[0].pk, bucket_id=buckets[1].pk)
    assert response.placements.count() == 2


def test_replacing_a_card_where_it_already_is_is_a_no_op_not_a_crash():
    """The `.exclude(prompt_game=game)` on the eviction, which nothing else reaches.

    On a single-slot shape the eviction clears the target bucket first. Without sparing the card being
    placed, a hunter tapping their existing pick again deletes the row and then saves the stale object
    over it -- which Django 5.2 turns into "Save with update_fields did not affect any rows", i.e. a
    500 where the answer should simply not change. Found by a surviving mutant, not by review."""
    owner = _hunter()
    poll, pool, buckets = _built(owner, shape=SHAPE_POLL, games=2)
    voter = _hunter('voter')

    first = svc.place(poll, voter, game_id=pool[0].pk, bucket_id=buckets[0].pk)
    again = svc.place(poll, voter, game_id=pool[0].pk, bucket_id=buckets[0].pk)

    assert again.pk == first.pk, 'the same vote was rewritten as a new row'
    response = svc.response_for(poll, voter)
    assert response.placements.count() == 1
    response.refresh_from_db()
    assert response.placement_count == 1


def test_a_tier_row_takes_as_many_as_you_like():
    owner = _hunter()
    prompt, pool, buckets = _built(owner, games=3)
    answerer = _hunter('answerer')

    for game in pool:
        svc.place(prompt, answerer, game_id=game.pk, bucket_id=buckets[0].pk)

    response = svc.response_for(prompt, answerer)
    assert response.placements.count() == 3
    assert list(response.placements.order_by('position').values_list('position', flat=True)) == [0, 1, 2]


# ── moving ────────────────────────────────────────────────────────────────────────────────────────


def test_moving_a_card_between_rows_is_the_same_act_as_placing_it():
    """Dragging from one row to another IS a place. Making the editor unplace-then-place would leave a
    window in which a half-failed drag dropped the card out of the response entirely."""
    owner = _hunter()
    prompt, pool, buckets = _built(owner)
    answerer = _hunter('answerer')

    svc.place(prompt, answerer, game_id=pool[0].pk, bucket_id=buckets[0].pk)
    svc.place(prompt, answerer, game_id=pool[0].pk, bucket_id=buckets[1].pk)

    response = svc.response_for(prompt, answerer)
    assert response.placements.count() == 1, 'the move left a duplicate'
    assert response.placements.get().bucket_id == buckets[1].pk
    response.refresh_from_db()
    assert response.placement_count == 1


def test_unplacing_closes_the_gap_in_the_row_it_left():
    owner = _hunter()
    prompt, pool, buckets = _built(owner, games=3)
    answerer = _hunter('answerer')
    for game in pool:
        svc.place(prompt, answerer, game_id=game.pk, bucket_id=buckets[0].pk)

    svc.unplace(prompt, answerer, game_id=pool[0].pk)

    response = svc.response_for(prompt, answerer)
    positions = list(response.placements.order_by('position').values_list('position', flat=True))
    assert positions == [0, 1], f'positions are not dense: {positions}'
    response.refresh_from_db()
    assert response.placement_count == 2


def test_unplacing_something_that_was_never_placed_is_a_no_op():
    """A second click must not error about a card already back in the tray."""
    owner = _hunter()
    prompt, pool, buckets = _built(owner)
    answerer = _hunter('answerer')

    svc.unplace(prompt, answerer, game_id=pool[0].pk)
    svc.place(prompt, answerer, game_id=pool[0].pk, bucket_id=buckets[0].pk)
    svc.unplace(prompt, answerer, game_id=pool[0].pk)
    svc.unplace(prompt, answerer, game_id=pool[0].pk)

    assert svc.response_for(prompt, answerer).placements.count() == 0


# ── ordering within a row ─────────────────────────────────────────────────────────────────────────


def test_reordering_a_row_refuses_a_partial_order():
    owner = _hunter()
    prompt, pool, buckets = _built(owner, games=3)
    answerer = _hunter('answerer')
    for game in pool:
        svc.place(prompt, answerer, game_id=game.pk, bucket_id=buckets[0].pk)

    ids = [pool[0].pk, pool[1].pk, pool[2].pk]
    with pytest.raises(psvc.PromptError):
        svc.reorder_bucket(prompt, answerer, bucket_id=buckets[0].pk, game_ids=ids[:2])
    with pytest.raises(psvc.PromptError):
        svc.reorder_bucket(prompt, answerer, bucket_id=buckets[0].pk, game_ids=ids + [ids[0]])

    svc.reorder_bucket(prompt, answerer, bucket_id=buckets[0].pk, game_ids=list(reversed(ids)))
    response = svc.response_for(prompt, answerer)
    ordered = list(response.placements.order_by('position').values_list('prompt_game_id', flat=True))
    assert ordered == list(reversed(ids))


# ── visibility and clearing ───────────────────────────────────────────────────────────────────────


def test_an_answer_is_public_by_default_and_can_be_kept_to_yourself():
    owner = _hunter()
    prompt, pool, buckets = _built(owner)
    answerer = _hunter('answerer')
    svc.place(prompt, answerer, game_id=pool[0].pk, bucket_id=buckets[0].pk)

    assert svc.response_for(prompt, answerer).is_public is True
    assert svc.listed_responses(prompt).count() == 1

    svc.set_public(prompt, answerer, is_public=False)
    assert svc.listed_responses(prompt).count() == 0
    prompt.refresh_from_db()
    assert prompt.response_count == 0


def test_an_empty_answer_is_not_listed():
    """One placement is the floor, and it is not a completeness rule: three of nine grid slots is a
    legitimate answer, so a threshold would be a product rule encoded where it cannot change."""
    owner = _hunter()
    prompt, pool, buckets = _built(owner)
    answerer = _hunter('answerer')
    svc.place(prompt, answerer, game_id=pool[0].pk, bucket_id=buckets[0].pk)
    svc.unplace(prompt, answerer, game_id=pool[0].pk)

    assert svc.response_for(prompt, answerer) is not None, 'the row carries a visibility choice'
    assert svc.listed_responses(prompt).count() == 0
    prompt.refresh_from_db()
    assert prompt.response_count == 0


def test_clearing_removes_the_whole_answer_and_is_idempotent():
    """Emptying and clearing are different acts: the row carries a visibility choice and any likes it
    has drawn, so this is the one that says "I never answered"."""
    owner = _hunter()
    prompt, pool, buckets = _built(owner)
    answerer = _hunter('answerer')
    svc.place(prompt, answerer, game_id=pool[0].pk, bucket_id=buckets[0].pk)

    svc.clear(prompt, answerer)
    svc.clear(prompt, answerer)

    assert svc.response_for(prompt, answerer) is None
    assert not PromptPlacement.objects.exists()
    prompt.refresh_from_db()
    assert prompt.response_count == 0


def test_the_prompts_answer_tally_counts_what_a_reader_would_count():
    """Public and non-empty -- the same population `listed_responses` returns. Counting private or
    empty answers would make the browse tile say three about a page showing one."""
    owner = _hunter()
    prompt, pool, buckets = _built(owner)

    public_one = _hunter('public-one')
    private_one = _hunter('private-one')
    empty_one = _hunter('empty-one')

    svc.place(prompt, public_one, game_id=pool[0].pk, bucket_id=buckets[0].pk)
    svc.place(prompt, private_one, game_id=pool[0].pk, bucket_id=buckets[0].pk)
    svc.set_public(prompt, private_one, is_public=False)
    svc.place(prompt, empty_one, game_id=pool[0].pk, bucket_id=buckets[0].pk)
    svc.unplace(prompt, empty_one, game_id=pool[0].pk)

    prompt.refresh_from_db()
    assert prompt.response_count == 1
    assert svc.listed_responses(prompt).count() == 1


# ── gates ─────────────────────────────────────────────────────────────────────────────────────────


def test_placing_is_gated_but_withdrawing_never_is():
    """A response carries no words at all, which makes it tempting to skip this -- but placing IS
    posting: it publishes a public artifact attributed to a profile, and public is the default. The
    house precedent is `game_list_service.set_like`, which gates a wordless public signal for the same
    reason."""
    owner = _hunter()
    prompt, pool, buckets = _built(owner)
    answerer = _hunter('answerer')
    svc.place(prompt, answerer, game_id=pool[0].pk, bucket_id=buckets[0].pk)
    _restrict(answerer)

    with pytest.raises(psvc.PromptError):
        svc.place(prompt, answerer, game_id=pool[1].pk, bucket_id=buckets[0].pk)
    with pytest.raises(psvc.PromptError):
        svc.set_public(prompt, answerer, is_public=True)

    # ...and taking their own answer down still works, in all three ways.
    svc.set_public(prompt, answerer, is_public=False)
    svc.unplace(prompt, answerer, game_id=pool[0].pk)
    svc.clear(prompt, answerer)
    assert svc.response_for(prompt, answerer) is None


def test_a_private_prompt_cannot_be_answered_by_id():
    """A write-shaped oracle: refusing differently for "exists but private" and "does not exist" tells
    a stranger which one it is."""
    owner = _hunter()
    prompt, pool, buckets = _built(owner, public=False)
    stranger = _hunter('stranger')

    with pytest.raises(psvc.PromptError):
        svc.place(prompt, stranger, game_id=pool[0].pk, bucket_id=buckets[0].pk)

    # The author can still answer their own draft.
    svc.place(prompt, owner, game_id=pool[0].pk, bucket_id=buckets[0].pk)
    assert svc.response_for(prompt, owner).placements.count() == 1


def test_closing_stops_new_answers_and_leaves_existing_ones_editable():
    """Closing is the author saying "no more of these", not "freeze the ones I have". Freezing would
    punish people who did nothing and strand a half-finished answer forever."""
    owner = _hunter()
    prompt, pool, buckets = _built(owner)
    early = _hunter('early')
    late = _hunter('late')
    svc.place(prompt, early, game_id=pool[0].pk, bucket_id=buckets[0].pk)

    psvc.set_closed(prompt, owner, closed=True)
    prompt.refresh_from_db()

    with pytest.raises(psvc.PromptError):
        svc.place(prompt, late, game_id=pool[0].pk, bucket_id=buckets[0].pk)

    # The hunter who already answered keeps editing.
    svc.place(prompt, early, game_id=pool[1].pk, bucket_id=buckets[1].pk)
    assert svc.response_for(prompt, early).placements.count() == 2


def test_an_unlinked_hunter_cannot_answer():
    owner = _hunter()
    prompt, pool, buckets = _built(owner)
    stranger = ProfileFactory(is_linked=False, psn_username='unlinked')

    with pytest.raises(psvc.PromptError):
        svc.place(prompt, stranger, game_id=pool[0].pk, bucket_id=buckets[0].pk)


# ── caps ──────────────────────────────────────────────────────────────────────────────────────────


def test_an_answer_is_bounded_and_the_bound_does_not_trap_a_move(monkeypatch):
    """The bound tier's unlimited-per-bucket rows otherwise lack, and the clause that keeps it from
    becoming a cage: it binds a NEW placement only, so a hunter sitting at the ceiling can still drag
    a card they have already placed from one row to another.

    THE CEILING IS REACHED WITH REAL ROWS. The first version of this test set `placement_count` to the
    cap and called that "at the ceiling" -- but the guard counts ROWS, so the response was nowhere
    near it and neither half of this was being tested. A surviving mutant said so. Patching the
    constant is what makes the real thing cheap."""
    monkeypatch.setattr(svc, 'MAX_PLACEMENTS_PER_RESPONSE', 2)
    owner = _hunter()
    prompt, pool, buckets = _built(owner, games=3)
    answerer = _hunter('answerer')

    svc.place(prompt, answerer, game_id=pool[0].pk, bucket_id=buckets[0].pk)
    svc.place(prompt, answerer, game_id=pool[1].pk, bucket_id=buckets[0].pk)

    with pytest.raises(psvc.PromptError):
        svc.place(prompt, answerer, game_id=pool[2].pk, bucket_id=buckets[0].pk)

    # ...and at that same ceiling, moving one that is already placed still works.
    svc.place(prompt, answerer, game_id=pool[0].pk, bucket_id=buckets[1].pk)
    response = svc.response_for(prompt, answerer)
    assert response.placements.get(prompt_game=pool[0]).bucket_id == buckets[1].pk
    assert response.placements.count() == 2
