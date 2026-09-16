"""What each shape actually promises: publish floors, the freeze, open grids, duplicates.

P0 said three shapes are one engine and P1/P2 built that engine. This file is where the shapes stop
being interchangeable, and it exists because the owner's rules for them are genuinely different:

* **A tier list** needs games before anybody can be asked to rank them, and stays fully editable
  afterwards -- its rows are a ranking its author owns.
* **A grid** may ship with NO games at all: the author sets the questions and every respondent
  searches the whole catalogue. Its slots freeze on publish, because changing "Best combat" to "Worst
  combat" after people answer inverts every existing answer with no row changed. Its pool may still
  grow, because more choices is additive.
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


def _grid(owner, *, slots=2, games=0, allow_duplicates=True, public=False):
    prompt = svc.create_prompt(owner, shape=SHAPE_GRID, title='Pick one each',
                               allow_duplicates=allow_duplicates)
    for i in range(slots):
        svc.create_bucket(prompt, owner, label=f'Best {i}')
    pool = [svc.add_concept(prompt, owner, ConceptFactory()) for _ in range(games)]
    if public:
        svc.update_prompt(prompt, owner, is_public=True)
        prompt.refresh_from_db()
    return prompt, pool, list(prompt.buckets.order_by('position'))


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


def test_a_grid_with_duplicates_off_needs_enough_games_to_fill_its_slots():
    """Otherwise the author ships something nobody can finish: four questions, three answers allowed,
    and the last slot permanently empty through no fault of the respondent."""
    owner = _hunter()
    prompt, _pool, _slots = _grid(owner, slots=3, games=2, allow_duplicates=False)

    with pytest.raises(svc.PromptError):
        svc.update_prompt(prompt, owner, is_public=True)

    svc.add_concept(prompt, owner, ConceptFactory())
    svc.update_prompt(prompt, owner, is_public=True)
    prompt.refresh_from_db()
    assert prompt.is_public is True


def test_an_open_grid_is_unaffected_by_the_duplicates_rule():
    """No pool means nothing to be short of, whichever way the toggle is set."""
    owner = _hunter()
    prompt, _pool, _slots = _grid(owner, slots=4, games=0, allow_duplicates=False)
    svc.update_prompt(prompt, owner, is_public=True)
    prompt.refresh_from_db()
    assert prompt.is_public is True


# ── the freeze ────────────────────────────────────────────────────────────────────────────────────


def test_a_published_grids_slots_are_frozen_but_its_pool_may_still_grow():
    """The slots ARE the questions: changing "Best combat" to "Worst combat" after people answer
    inverts what every existing answer says, silently, with no row changed. More games is additive and
    breaks nothing -- but nothing may LEAVE the pool, because a departing game takes real answers."""
    owner = _hunter()
    prompt, pool, slots = _grid(owner, slots=2, games=3, public=True)

    for call in (
        lambda: svc.create_bucket(prompt, owner, label='A third'),
        lambda: svc.update_bucket(slots[0], owner, label='Renamed'),
        lambda: svc.delete_bucket(slots[0], owner),
        lambda: svc.reorder_buckets(prompt, owner,
                                    list(prompt.buckets.values_list('pk', flat=True))[::-1]),
        lambda: svc.remove_concept(prompt, owner, pool[0]),
    ):
        with pytest.raises(svc.PromptError):
            call()

    # ...and the one thing that IS allowed.
    svc.add_concept(prompt, owner, ConceptFactory())
    prompt.refresh_from_db()
    assert prompt.game_count == 4


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
    prompt, _pool, slots = _grid(owner, slots=2, games=2, public=True)

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
    prompt, pool, slots = _grid(owner, slots=2, games=2, public=True)
    rsvc.place(prompt, _hunter('responder'), game_id=pool[0].pk, bucket_id=slots[0].pk)

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
    prompt, _pool, slots = _grid(owner, slots=2, games=0, public=True)
    answerer = _hunter('answerer')
    chosen = ConceptFactory()

    placement = rsvc.place(prompt, answerer, concept_pk=chosen.pk, bucket_id=slots[0].pk)

    assert placement.concept_id == chosen.pk
    assert placement.prompt_game_id is None
    response = rsvc.response_for(prompt, answerer)
    assert response.placement_count == 1


def test_a_grid_with_a_pool_refuses_a_free_pick():
    """Supplying a pool is the author saying "choose from these", which is the whole reason to supply
    one."""
    owner = _hunter()
    prompt, pool, slots = _grid(owner, slots=2, games=3, public=True)
    answerer = _hunter('answerer')

    with pytest.raises(svc.PromptError):
        rsvc.place(prompt, answerer, concept_pk=ConceptFactory().pk, bucket_id=slots[0].pk)

    rsvc.place(prompt, answerer, game_id=pool[0].pk, bucket_id=slots[0].pk)
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
    prompt, pool, slots = _grid(owner, slots=2, games=2, public=True)
    answerer = _hunter('answerer')

    with pytest.raises(svc.PromptError):
        rsvc.place(prompt, answerer, bucket_id=slots[0].pk)
    with pytest.raises(svc.PromptError):
        rsvc.place(prompt, answerer, game_id=pool[0].pk, concept_pk=ConceptFactory().pk,
                   bucket_id=slots[0].pk)


# ── duplicates ────────────────────────────────────────────────────────────────────────────────────


def test_a_grid_lets_one_game_win_two_slots():
    """THE RULE THAT REOPENED A SHIPPED CONSTRAINT. `unique(response, prompt_game)` was
    unconditional, and this project argued for refusing "Elden Ring wins Best Combat AND Best Story".
    The owner's call overrode it: on a grid that is the point."""
    owner = _hunter()
    prompt, pool, slots = _grid(owner, slots=2, games=2, public=True)
    answerer = _hunter('answerer')

    rsvc.place(prompt, answerer, game_id=pool[0].pk, bucket_id=slots[0].pk)
    rsvc.place(prompt, answerer, game_id=pool[0].pk, bucket_id=slots[1].pk)

    response = rsvc.response_for(prompt, answerer)
    assert response.placements.count() == 2, 'the second slot moved the card instead of adding one'
    assert set(response.placements.values_list('bucket_id', flat=True)) == {slots[0].pk, slots[1].pk}
    assert response.placement_count == 2


def test_with_duplicates_off_a_second_slot_moves_the_card():
    """Which is the behaviour every other shape has, and the reason 'place' has one fork in it."""
    owner = _hunter()
    prompt, pool, slots = _grid(owner, slots=2, games=2, allow_duplicates=False, public=True)
    answerer = _hunter('answerer')

    rsvc.place(prompt, answerer, game_id=pool[0].pk, bucket_id=slots[0].pk)
    rsvc.place(prompt, answerer, game_id=pool[0].pk, bucket_id=slots[1].pk)

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
    prompt, _pool, _slots = _grid(owner, slots=2, games=3, public=True)

    with pytest.raises(svc.PromptError):
        svc.update_prompt(prompt, owner, allow_duplicates=False)


def test_turning_duplicates_off_needs_a_pool_that_can_fill_the_slots():
    owner = _hunter()
    prompt, _pool, _slots = _grid(owner, slots=4, games=2)

    with pytest.raises(svc.PromptError):
        svc.update_prompt(prompt, owner, allow_duplicates=False)

    for _ in range(2):
        svc.add_concept(prompt, owner, ConceptFactory())
    svc.update_prompt(prompt, owner, allow_duplicates=False)
    prompt.refresh_from_db()
    assert prompt.allow_duplicates is False


def test_toggling_rewrites_the_flag_on_answers_already_given():
    """`no_duplicates` is denormalized onto every placement because the partial uniques cannot see the
    grandparent -- and unlike `single_slot` it is NOT protected by immutability. A toggle that left old
    rows behind would leave a grid whose answers disagree with its own rule, invisibly, because a
    per-row predicate collides with nothing.

    Bounded: the toggle is refused on a published grid, so the only answers that can exist are the
    author's own."""
    owner = _hunter()
    prompt, pool, slots = _grid(owner, slots=2, games=2)
    rsvc.place(prompt, owner, game_id=pool[0].pk, bucket_id=slots[0].pk)
    assert PromptPlacement.objects.get().no_duplicates is False

    svc.update_prompt(prompt, owner, allow_duplicates=False)

    assert PromptPlacement.objects.get().no_duplicates is True, (
        'an answer kept the old rule while its grid moved to a new one'
    )
