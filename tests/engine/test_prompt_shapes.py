"""What each shape actually promises: publish floors, the freeze, open grids, duplicates.

P0 said three shapes are one engine and P1/P2 built that engine. This file is where the shapes stop
being interchangeable, and it exists because the owner's rules for them are genuinely different:

* **A tier list** needs games before anybody can be asked to rank them, and stays fully editable
  afterwards -- its rows are a ranking its author owns.
* **A grid** has NO author pool at all: the author sets the questions and every respondent
  searches the whole catalogue for each slot. Its slots freeze on publish, because changing "Best
  combat" to "Worst combat" after people answer inverts every existing answer with no row changed.
  (It once had an OPTIONAL pool, and "its pool may still grow" was the rule stated here. Both are
  gone -- owner's call, 2026-09-19.)
* **A poll** freezes entirely. Adding an option mid-vote is the classic way to rig one.

And the rule that reopened a shipped constraint: a grid may use the same game in several slots, so
`unique(response, prompt_game)` stopped being unconditional.
"""
import pytest

from prompts.models import (MIN_GAMES_TO_PUBLISH, SHAPE_GRID, SHAPE_POLL, SHAPE_TIER,
                            PromptPlacement)
from prompts.services import prompt_service as svc
from prompts.services import response_service as rsvc
from tests.factories import ConceptFactory, ProfileFactory

pytestmark = pytest.mark.django_db


def _hunter(psn='hunter'):
    return ProfileFactory(is_linked=True, psn_username=psn)


def _grid(owner, *, slots=2, picks=0, allow_duplicates=True, public=False):
    """A grid, its slots, and some games to answer it WITH.

    THE SECOND RETURN VALUE IS NOT A POOL. It used to be: a grid could carry an author-supplied list
    of games, and these were rows on the prompt. A grid has no pool (owner's call, 2026-09-19), so
    these are plain Concepts -- free picks waiting to happen, passed as `concept_pk`.
    """
    prompt = svc.create_prompt(owner, shape=SHAPE_GRID, title='Pick one each',
                               allow_duplicates=allow_duplicates)
    for i in range(slots):
        svc.create_bucket(prompt, owner, label=f'Best {i}')
    chosen = [ConceptFactory() for _ in range(picks)]
    if public:
        svc.update_prompt(prompt, owner, is_public=True)
        prompt.refresh_from_db()
    return prompt, chosen, list(prompt.buckets.order_by('position'))


# ── the publish floor ─────────────────────────────────────────────────────────────────────────────


def test_a_tier_list_needs_games_before_anybody_can_rank_them():
    """Five, and enforced at the PUBLISH transition rather than at save: an author builds up to it,
    and a draft may sit at one game for as long as they like. The floor exists to stop "two games in
    S" reaching the browse page, which is where a brand-new feature can least afford it."""
    owner = _hunter()
    prompt = svc.create_prompt(owner, shape=SHAPE_TIER, title='Rank them')

    for _ in range(MIN_GAMES_TO_PUBLISH[SHAPE_TIER] - 1):
        svc.add_concept(prompt, owner, ConceptFactory())
    with pytest.raises(svc.PromptError):
        svc.update_prompt(prompt, owner, is_public=True)
    prompt.refresh_from_db()
    assert prompt.is_public is False

    svc.add_concept(prompt, owner, ConceptFactory())
    svc.update_prompt(prompt, owner, is_public=True)
    prompt.refresh_from_db()
    assert prompt.is_public is True


def test_a_poll_needs_something_to_choose_between():
    """One option is not a question."""
    owner = _hunter()
    poll = svc.create_prompt(owner, shape=SHAPE_POLL, title='Which?')
    svc.add_concept(poll, owner, ConceptFactory())

    with pytest.raises(svc.PromptError):
        svc.update_prompt(poll, owner, is_public=True)

    svc.add_concept(poll, owner, ConceptFactory())
    svc.update_prompt(poll, owner, is_public=True)
    poll.refresh_from_db()
    assert poll.is_public is True


def test_a_grid_publishes_with_no_games_at_all():
    """THE OPEN GRID, which is the shape the whole feature is really for: the author asks nine
    questions and every respondent searches the catalogue for each one. There is no pool to be short
    of, so the floor is on the SLOTS instead."""
    owner = _hunter()
    empty = svc.create_prompt(owner, shape=SHAPE_GRID, title='No slots yet')

    with pytest.raises(svc.PromptError):
        svc.update_prompt(empty, owner, is_public=True)

    svc.create_bucket(empty, owner, label='Best combat')
    svc.update_prompt(empty, owner, is_public=True)
    empty.refresh_from_db()
    assert empty.is_public is True
    assert empty.game_count == 0


def test_a_grid_takes_no_pool_at_all():
    """A GRID HAS NO AUTHOR POOL (owner's call, 2026-09-19). Every slot is answered by whoever
    responds, out of the whole catalogue.

    This is the guard that replaces a subtler one. While a grid could be open OR pooled, `add_concept`
    had to refuse the FIRST game on a grid people had already answered, because that flipped the mode
    and stranded every free pick -- and it shipped broken once. A shape with one mode cannot be
    flipped into the other.
    """
    owner = _hunter()
    prompt, _picks, _slots = _grid(owner, slots=3)

    with pytest.raises(svc.PromptError) as caught:
        svc.add_concept(prompt, owner, ConceptFactory())

    # THE MESSAGE, not just the raise, and mutation testing is why. `MAX_GAMES_PER_PROMPT[grid]` is
    # zero, so deleting the shape refusal still raises -- from the size cap, saying "This holds 0
    # games. Remove one to make room." That is true, useless, and tells an author nothing about the
    # shape they picked. Two guards is fine; this pins the one that explains itself.
    assert 'set list' in str(caught.value)

    prompt.refresh_from_db()
    assert prompt.game_count == 0


def test_duplicates_off_never_blocks_publishing_a_grid():
    """The rule this replaces was "with duplicates off, you need at least as many games as slots",
    and it existed only because an author pool could be too small to fill them. With the whole
    catalogue behind every slot there is nothing to be short of, whichever way the toggle is set."""
    owner = _hunter()
    prompt, _picks, _slots = _grid(owner, slots=4, allow_duplicates=False)
    svc.update_prompt(prompt, owner, is_public=True)
    prompt.refresh_from_db()
    assert prompt.is_public is True


# ── the freeze ────────────────────────────────────────────────────────────────────────────────────


def test_a_published_grids_slots_are_frozen():
    """The slots ARE the questions: changing "Best combat" to "Worst combat" after people answer
    inverts what every existing answer says, silently, with no row changed.

    This test used to end by proving the one thing a published grid COULD still gain -- a pool game.
    There is no pool now, so the freeze is total and the tail is gone rather than inverted into a
    second assertion that a grid refuses games, which `test_a_grid_takes_no_pool_at_all` owns.
    """
    owner = _hunter()
    prompt, _picks, slots = _grid(owner, slots=2, public=True)

    for call in (
        lambda: svc.create_bucket(prompt, owner, label='A third'),
        lambda: svc.update_bucket(slots[0], owner, label='Renamed'),
        lambda: svc.delete_bucket(slots[0], owner),
        lambda: svc.reorder_buckets(prompt, owner,
                                    list(prompt.buckets.values_list('pk', flat=True))[::-1]),
    ):
        with pytest.raises(svc.PromptError):
            call()


def test_a_published_poll_is_frozen_end_to_end():
    """Adding an option mid-vote is the classic way to rig one, and the tally is the whole artifact."""
    owner = _hunter()
    poll = svc.create_prompt(owner, shape=SHAPE_POLL, title='Which?')
    options = [svc.add_concept(poll, owner, ConceptFactory()) for _ in range(3)]
    svc.update_prompt(poll, owner, is_public=True)
    poll.refresh_from_db()

    for call in (
        lambda: svc.add_concept(poll, owner, ConceptFactory()),
        lambda: svc.remove_concept(poll, owner, options[0]),
        lambda: svc.reorder_games(poll, owner, [o.pk for o in options][::-1]),
        lambda: svc.update_bucket(poll.buckets.get(), owner, label='Renamed'),
    ):
        with pytest.raises(svc.PromptError):
            call()

    poll.refresh_from_db()
    assert poll.game_count == 3


def test_a_published_tier_list_stays_fully_editable():
    """The locked decision: an author's edits reach existing answers -- an added game appears unplaced
    in everyone's, a removed one drops out. Nothing about a tier list freezes."""
    owner = _hunter()
    prompt = svc.create_prompt(owner, shape=SHAPE_TIER, title='Rank them')
    pool = [svc.add_concept(prompt, owner, ConceptFactory())
            for _ in range(MIN_GAMES_TO_PUBLISH[SHAPE_TIER])]
    svc.update_prompt(prompt, owner, is_public=True)
    prompt.refresh_from_db()

    svc.add_concept(prompt, owner, ConceptFactory())
    svc.remove_concept(prompt, owner, pool[0])
    svc.update_bucket(prompt.buckets.first(), owner, label='Godlike')
    svc.create_bucket(prompt, owner, label='F')
    prompt.refresh_from_db()
    assert prompt.game_count == MIN_GAMES_TO_PUBLISH[SHAPE_TIER]


def test_the_escape_hatch_is_a_rule_that_already_existed():
    """An author who spots a typo in a slot label with zero responses is not stuck: unpublishing is
    refused only once somebody has ANSWERED, so they can unpublish, fix and republish. No special
    case -- the door closes on the first answer, which is the same test delete and unpublish use."""
    owner = _hunter()
    prompt, _picks, slots = _grid(owner, slots=2, public=True)

    with pytest.raises(svc.PromptError):
        svc.update_bucket(slots[0], owner, label='Fixed')

    svc.update_prompt(prompt, owner, is_public=False)
    prompt.refresh_from_db()
    svc.update_bucket(slots[0], owner, label='Fixed')
    svc.update_prompt(prompt, owner, is_public=True)

    slots[0].refresh_from_db()
    assert slots[0].label == 'Fixed'


def test_once_answered_the_door_closes_for_good():
    """The escape hatch shuts on the first answer: the prompt can no longer be unpublished, so
    the structure is frozen for good rather than until the author feels like changing it.

    ONLY THE UNPUBLISH REFUSAL BELONGS HERE. An earlier version also asserted that relabelling a
    slot raises -- but `update_bucket` never consults the answered rule at all; that raise was the
    ordinary published-grid freeze, which fires whether or not anybody has answered, and the same
    assertion already sits in the escape-hatch test above on a grid with zero responses. It proved
    nothing about "once answered"."""
    owner = _hunter()
    prompt, picks, slots = _grid(owner, slots=2, picks=1, public=True)
    rsvc.place(prompt, _hunter('responder'), concept_pk=picks[0].pk, bucket_id=slots[0].pk)

    with pytest.raises(svc.PromptError):
        svc.update_prompt(prompt, owner, is_public=False)

    # ...and because it cannot be unpublished, the escape hatch that WOULD have unfrozen the
    # slots is gone with it. That is the actual "for good".
    prompt.refresh_from_db()
    assert prompt.is_public is True


# ── open grids ────────────────────────────────────────────────────────────────────────────────────


def test_an_open_grid_takes_any_game_the_respondent_finds():
    """No pool row to point at, so the placement points at the Concept. This does not reopen the
    checklist bug `prompt_game` exists to close: that was a reference whose integrity target could go
    away underneath it, and here there is no pool to be integral to."""
    owner = _hunter()
    prompt, _picks, slots = _grid(owner, slots=2, public=True)
    answerer = _hunter('answerer')
    chosen = ConceptFactory()

    placement = rsvc.place(prompt, answerer, concept_pk=chosen.pk, bucket_id=slots[0].pk)

    assert placement.concept_id == chosen.pk
    assert placement.prompt_game_id is None
    response = rsvc.response_for(prompt, answerer)
    assert response.placement_count == 1


def test_a_grid_always_takes_a_free_pick():
    """The inverse of the test this replaces, which checked that a POOLED grid refused one.

    A free pick used to be legal or illegal depending on whether any pool row existed -- a question
    asked per placement, whose answer an author could change underneath a respondent mid-answer.
    Shape alone decides now, so there is no state in which a grid turns a free pick down.
    """
    owner = _hunter()
    prompt, _picks, slots = _grid(owner, slots=2, public=True)
    answerer = _hunter('answerer')

    rsvc.place(prompt, answerer, concept_pk=ConceptFactory().pk, bucket_id=slots[0].pk)
    assert rsvc.response_for(prompt, answerer).placements.count() == 1


def test_only_a_grid_takes_free_picks():
    """A tier list and a poll are about a set the author chose.

    TESTED ON AN EMPTY DRAFT, which is the only state that actually reaches the shape check. A
    published tier list always has a pool -- the publish floor sees to that -- so "this one has its own
    games" refuses first, and an earlier version of this test passed with the shape guard deleted. The
    guard's real job is the unpublished tier whose pool is still empty.
    """
    owner = _hunter()
    tier = svc.create_prompt(owner, shape=SHAPE_TIER, title='Rank them')
    assert tier.games.count() == 0, 'the pool must be empty or the pool check answers instead'

    with pytest.raises(svc.PromptError):
        rsvc.place(tier, owner, concept_pk=ConceptFactory().pk,
                   bucket_id=tier.buckets.first().pk)

    poll = svc.create_prompt(owner, shape=SHAPE_POLL, title='Which?')
    with pytest.raises(svc.PromptError):
        rsvc.place(poll, owner, concept_pk=ConceptFactory().pk,
                   bucket_id=poll.buckets.get().pk)

    assert not PromptPlacement.objects.exists()


def test_a_placement_names_exactly_one_thing():
    """Both set is two claims about one card; neither is a placement of nothing."""
    owner = _hunter()
    prompt, _picks, slots = _grid(owner, slots=2, public=True)
    answerer = _hunter('answerer')

    # `match`, because both halves otherwise pass for the wrong reason. Delete the both-set guard
    # and the first raises from `_as_pk(None)` ("That is not a game.") while the second falls into
    # the `game_id` branch and raises from `get_game` ("That game is not in this one.") -- the old
    # version passed a REAL pool row, which a grid can no longer have.
    with pytest.raises(svc.PromptError, match='Pick one game'):
        rsvc.place(prompt, answerer, bucket_id=slots[0].pk)
    with pytest.raises(svc.PromptError, match='Pick one game'):
        rsvc.place(prompt, answerer, game_id=10 ** 9, concept_pk=ConceptFactory().pk,
                   bucket_id=slots[0].pk)
    assert not PromptPlacement.objects.exists()


# ── duplicates ────────────────────────────────────────────────────────────────────────────────────


def test_a_grid_lets_one_game_win_two_slots():
    """THE RULE THAT REOPENED A SHIPPED CONSTRAINT. `unique(response, prompt_game)` was
    unconditional, and this project argued for refusing "Elden Ring wins Best Combat AND Best Story".
    The owner's call overrode it: on a grid that is the point."""
    owner = _hunter()
    prompt, picks, slots = _grid(owner, slots=2, picks=1, public=True)
    answerer = _hunter('answerer')

    rsvc.place(prompt, answerer, concept_pk=picks[0].pk, bucket_id=slots[0].pk)
    rsvc.place(prompt, answerer, concept_pk=picks[0].pk, bucket_id=slots[1].pk)

    response = rsvc.response_for(prompt, answerer)
    assert response.placements.count() == 2, 'the second slot moved the card instead of adding one'
    assert set(response.placements.values_list('bucket_id', flat=True)) == {slots[0].pk, slots[1].pk}
    assert response.placement_count == 2


def test_with_duplicates_off_a_second_slot_moves_the_card():
    """Which is the behaviour every other shape has, and the reason 'place' has one fork in it."""
    owner = _hunter()
    prompt, picks, slots = _grid(owner, slots=2, picks=1, allow_duplicates=False, public=True)
    answerer = _hunter('answerer')

    rsvc.place(prompt, answerer, concept_pk=picks[0].pk, bucket_id=slots[0].pk)
    rsvc.place(prompt, answerer, concept_pk=picks[0].pk, bucket_id=slots[1].pk)

    response = rsvc.response_for(prompt, answerer)
    assert response.placements.count() == 1
    assert response.placements.get().bucket_id == slots[1].pk


def test_a_tier_list_and_a_poll_forbid_duplicates_whatever_the_flag_says():
    """The flag is grid-only. A game in two tiers is a contradiction and a poll picks one thing."""
    owner = _hunter()
    tier = svc.create_prompt(owner, shape=SHAPE_TIER, title='Rank them')
    assert tier.forbids_duplicates is True
    poll = svc.create_prompt(owner, shape=SHAPE_POLL, title='Which?')
    assert poll.forbids_duplicates is True

    with pytest.raises(svc.PromptError):
        svc.update_prompt(tier, owner, allow_duplicates=True)


def test_the_toggle_is_refused_on_a_published_grid():
    """It changes what every existing answer is allowed to be."""
    owner = _hunter()
    prompt, _picks, _slots = _grid(owner, slots=2, public=True)

    with pytest.raises(svc.PromptError):
        svc.update_prompt(prompt, owner, allow_duplicates=False)


def test_toggling_rewrites_the_flag_on_answers_already_given():
    """`no_duplicates` is denormalized onto every placement because the partial uniques cannot see the
    grandparent -- and unlike `single_slot` it is NOT protected by immutability. A toggle that left old
    rows behind would leave a grid whose answers disagree with its own rule, invisibly, because a
    per-row predicate collides with nothing.

    Bounded: the toggle is refused on a published grid, so the only answers that can exist are the
    author's own."""
    owner = _hunter()
    prompt, picks, slots = _grid(owner, slots=2, picks=1)
    rsvc.place(prompt, owner, concept_pk=picks[0].pk, bucket_id=slots[0].pk)
    assert PromptPlacement.objects.get().no_duplicates is False

    svc.update_prompt(prompt, owner, allow_duplicates=False)

    assert PromptPlacement.objects.get().no_duplicates is True, (
        'an answer kept the old rule while its grid moved to a new one'
    )
