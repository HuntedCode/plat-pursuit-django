"""Spine tests for Concept.absorb().

absorb() migrates all related data from a doomed concept onto a survivor before
the doomed one is deleted (called by Game.add_concept on reassignment). It is the
highest-blast-radius method in the engine: a mistake here silently corrupts or
destroys user data, and it has done so historically.

The most important cases here are the CASCADE-survival regressions: ratings and
reviews FK ConceptTrophyGroup with on_delete=CASCADE, so absorb() must re-point
their concept_trophy_group onto the SURVIVOR's equivalent CTG (matched by
trophy_group_id), not merely change `concept`. If it doesn't, the row keeps
pointing at the doomed concept's duplicate CTG and rides its cascade-delete into
oblivion when the doomed concept is removed. That was the historical review/rating
loss bug (see CLAUDE.md "absorb() CTG-Cascade Trap").

Each test ends by actually deleting the doomed concept, so the cascade really
fires — a test that only checks the re-point without deleting would pass even if
the bug regressed.
"""

import pytest

from trophies.models import Concept, UserConceptRating
from tests.factories import (
    CommentFactory,
    ConceptFactory,
    ConceptTrophyGroupFactory,
    ProfileFactory,
    ReviewFactory,
    UserConceptRatingFactory,
)

pytestmark = pytest.mark.django_db


# --- basic migration behavior -------------------------------------------------


def test_absorb_into_self_is_noop():
    concept = ConceptFactory(title_ids=["CUSA00001_00"])
    concept.absorb(concept)
    concept.refresh_from_db()
    assert concept.title_ids == ["CUSA00001_00"]


def test_absorb_migrates_comments():
    survivor = ConceptFactory()
    doomed = ConceptFactory()
    comment = CommentFactory(concept=doomed)

    survivor.absorb(doomed)

    comment.refresh_from_db()
    assert comment.concept_id == survivor.id


def test_absorb_merges_and_dedups_title_ids():
    survivor = ConceptFactory(title_ids=["A", "B"])
    doomed = ConceptFactory(title_ids=["B", "C"])

    survivor.absorb(doomed)

    survivor.refresh_from_db()
    assert survivor.title_ids == ["A", "B", "C"]


def test_absorb_repoints_a_unique_ctg():
    survivor = ConceptFactory()
    doomed = ConceptFactory()
    ctg = ConceptTrophyGroupFactory(
        concept=doomed, trophy_group_id="001", display_name="DLC 1"
    )

    survivor.absorb(doomed)

    ctg.refresh_from_db()
    assert ctg.concept_id == survivor.id


def test_absorb_leaves_duplicate_ctg_on_doomed_concept():
    # Both concepts have a 'default' CTG. The doomed one is a duplicate
    # (survivor already has that trophy_group_id), so it is left on the doomed
    # concept to cascade-delete; the survivor keeps exactly one.
    survivor = ConceptFactory()
    doomed = ConceptFactory()
    ConceptTrophyGroupFactory(concept=survivor, trophy_group_id="default")
    doomed_ctg = ConceptTrophyGroupFactory(concept=doomed, trophy_group_id="default")

    survivor.absorb(doomed)

    doomed_ctg.refresh_from_db()
    assert doomed_ctg.concept_id == doomed.id  # not moved
    assert survivor.concept_trophy_groups.filter(trophy_group_id="default").count() == 1


# --- CTG-cascade regressions (the crown jewel) --------------------------------


def test_rating_on_duplicate_ctg_survives_cascade():
    """A DLC rating on the doomed concept's duplicate CTG must be re-pointed onto
    the survivor's equivalent CTG and survive the doomed concept's deletion."""
    profile = ProfileFactory()
    survivor = ConceptFactory()
    doomed = ConceptFactory()
    survivor_ctg = ConceptTrophyGroupFactory(
        concept=survivor, trophy_group_id="001", display_name="DLC"
    )
    doomed_ctg = ConceptTrophyGroupFactory(
        concept=doomed, trophy_group_id="001", display_name="DLC"
    )
    rating = UserConceptRatingFactory(
        profile=profile, concept=doomed, concept_trophy_group=doomed_ctg
    )

    survivor.absorb(doomed)
    doomed.delete()  # fires the CASCADE that would kill a mis-pointed rating

    rating.refresh_from_db()
    assert rating.concept_id == survivor.id
    assert rating.concept_trophy_group_id == survivor_ctg.id


def test_review_on_duplicate_ctg_survives_cascade():
    """Same guard for Reviews (non-null, on_delete=CASCADE concept_trophy_group)."""
    profile = ProfileFactory()
    survivor = ConceptFactory()
    doomed = ConceptFactory()
    survivor_ctg = ConceptTrophyGroupFactory(
        concept=survivor, trophy_group_id="default"
    )
    doomed_ctg = ConceptTrophyGroupFactory(concept=doomed, trophy_group_id="default")
    review = ReviewFactory(
        profile=profile, concept=doomed, concept_trophy_group=doomed_ctg
    )

    survivor.absorb(doomed)
    doomed.delete()

    review.refresh_from_db()
    assert review.concept_id == survivor.id
    assert review.concept_trophy_group_id == survivor_ctg.id


def test_base_game_rating_with_null_ctg_repoints_concept_only():
    """A base-game rating (concept_trophy_group=None) re-points concept only and
    survives — no CTG cascade touches a null-CTG row."""
    profile = ProfileFactory()
    survivor = ConceptFactory()
    doomed = ConceptFactory()
    rating = UserConceptRatingFactory(
        profile=profile, concept=doomed, concept_trophy_group=None
    )

    survivor.absorb(doomed)
    doomed.delete()

    rating.refresh_from_db()
    assert rating.concept_id == survivor.id
    assert rating.concept_trophy_group_id is None


def test_rating_dedups_by_profile_and_group_keeping_survivors():
    """When both concepts hold the same profile's rating for the same
    trophy_group_id, the survivor's wins and the doomed duplicate is dropped
    (deduped by (profile, trophy_group_id), not CTG primary key)."""
    profile = ProfileFactory()
    survivor = ConceptFactory()
    doomed = ConceptFactory()
    survivor_ctg = ConceptTrophyGroupFactory(
        concept=survivor, trophy_group_id="default"
    )
    doomed_ctg = ConceptTrophyGroupFactory(concept=doomed, trophy_group_id="default")
    survivor_rating = UserConceptRatingFactory(
        profile=profile, concept=survivor, concept_trophy_group=survivor_ctg, difficulty=3
    )
    doomed_rating = UserConceptRatingFactory(
        profile=profile, concept=doomed, concept_trophy_group=doomed_ctg, difficulty=9
    )

    survivor.absorb(doomed)
    doomed.delete()

    survivor_rating.refresh_from_db()
    assert survivor_rating.difficulty == 3  # survivor's rating untouched
    assert UserConceptRating.objects.filter(profile=profile, concept=survivor).count() == 1
    # the doomed duplicate was not migrated and died with the cascade
    assert not UserConceptRating.objects.filter(pk=doomed_rating.pk).exists()


# -- game list entries (gamelists.GameListItem, 2026-09) ------------------------------------------
#
# The rebuilt lists moved from Game keying to Concept keying, which put them inside absorb()'s
# contract. CLAUDE.md is blunt about what happens to a new Concept FK that does not get a branch
# here, and lists are a case where the loss would be invisible AND personal: a hunter curated a
# backlog by hand, an admin merged two catalogue rows months later, and the entry is gone with
# nothing anywhere saying why.


def test_absorb_moves_list_entries_to_the_survivor():
    from gamelists.models import GameList, GameListItem

    survivor, doomed = ConceptFactory(), ConceptFactory()
    profile = ProfileFactory(is_linked=True, psn_username='curator')
    backlog = GameList.objects.create(owner=profile, name='Backlog', game_count=1)
    entry = GameListItem.objects.create(game_list=backlog, concept=doomed, position=0)

    survivor.absorb(doomed)
    doomed.delete()

    entry.refresh_from_db()
    assert entry.concept_id == survivor.pk, 'the merge took a curated entry with it'


def test_absorb_drops_a_list_entry_that_would_collide_rather_than_raising():
    """A list already holding the survivor is the case a bare `.update()` cannot survive.

    `unique(game_list, concept)` would raise mid-merge on the first such list, and absorb() is not
    transactional -- so the exception lands after several branches have already committed, skips
    every branch below it, and stops the caller's `other.delete()`. One hunter having both sides of
    a merge on one list would break an admin's reassignment entirely.
    """
    from gamelists.models import GameList, GameListItem

    survivor, doomed = ConceptFactory(), ConceptFactory()
    profile = ProfileFactory(is_linked=True, psn_username='hadboth')
    backlog = GameList.objects.create(owner=profile, name='Backlog', game_count=2)
    kept = GameListItem.objects.create(game_list=backlog, concept=survivor, position=0)
    doomed_entry = GameListItem.objects.create(game_list=backlog, concept=doomed, position=1)

    survivor.absorb(doomed)
    doomed.delete()

    assert GameListItem.objects.filter(pk=kept.pk).exists(), 'the survivor entry was dropped'
    assert not GameListItem.objects.filter(pk=doomed_entry.pk).exists()
    assert GameListItem.objects.filter(game_list=backlog).count() == 1


def test_absorb_only_drops_the_collision_and_moves_everybody_elses_entry():
    """The dedup must be per LIST, not global. Somebody else's list that holds only the doomed
    concept has no collision and must be re-pointed, not swept up with the ones that do."""
    from gamelists.models import GameList, GameListItem

    survivor, doomed = ConceptFactory(), ConceptFactory()
    collider = ProfileFactory(is_linked=True, psn_username='hadboth')
    innocent = ProfileFactory(is_linked=True, psn_username='hadone')

    collided = GameList.objects.create(owner=collider, name='Both', game_count=2)
    GameListItem.objects.create(game_list=collided, concept=survivor, position=0)
    GameListItem.objects.create(game_list=collided, concept=doomed, position=1)

    untouched = GameList.objects.create(owner=innocent, name='Only the doomed one', game_count=1)
    moved = GameListItem.objects.create(game_list=untouched, concept=doomed, position=0)

    survivor.absorb(doomed)
    doomed.delete()

    moved.refresh_from_db()
    assert moved.concept_id == survivor.pk
    assert GameListItem.objects.filter(game_list=collided).count() == 1


def test_absorb_repairs_the_count_and_the_order_it_disturbs():
    """The collision drop is a DELETE out of the middle of a list, and it broke both denormalized
    invariants the lists module calls load-bearing.

    `game_count` is what the browse grid shows and sorts on, and nothing recomputes it -- an
    inflated counter cannot even come back down, because `PositiveIntegerField` is a DB CHECK and
    the service's decrements floor at zero. Positions are consumed as dense by the cover prefetch
    (`position__lt=4`), so the hole shows three covers on a four-game list.

    Neither was asserted by the first three absorb tests: they seeded `game_count` by hand and then
    never looked at it again.
    """
    from gamelists.models import GameList, GameListItem

    survivor, doomed = ConceptFactory(), ConceptFactory()
    profile = ProfileFactory(is_linked=True, psn_username='hadboth')
    other = ConceptFactory()

    backlog = GameList.objects.create(owner=profile, name='Backlog', game_count=3)
    GameListItem.objects.create(game_list=backlog, concept=survivor, position=0)
    GameListItem.objects.create(game_list=backlog, concept=doomed, position=1)
    tail = GameListItem.objects.create(game_list=backlog, concept=other, position=2)

    survivor.absorb(doomed)
    doomed.delete()

    backlog.refresh_from_db()
    assert backlog.game_count == 2, 'the counter is stranded high and nothing can bring it back'

    tail.refresh_from_db()
    assert tail.position == 1, 'the merge left a hole in the dense-position contract'
    assert list(
        GameListItem.objects.filter(game_list=backlog).order_by('position')
        .values_list('position', flat=True)
    ) == [0, 1]


def test_absorb_fixes_the_count_on_a_list_that_only_had_the_doomed_concept():
    """Re-pointing alone changes no counts, but the repair pass must not BREAK the lists it did not
    have to dedup."""
    from gamelists.models import GameList, GameListItem

    survivor, doomed = ConceptFactory(), ConceptFactory()
    profile = ProfileFactory(is_linked=True, psn_username='hadone')
    game_list = GameList.objects.create(owner=profile, name='Only doomed', game_count=1)
    entry = GameListItem.objects.create(game_list=game_list, concept=doomed, position=0)

    survivor.absorb(doomed)
    doomed.delete()

    entry.refresh_from_db()
    game_list.refresh_from_db()
    assert entry.concept_id == survivor.pk
    assert entry.position == 0
    assert game_list.game_count == 1



def test_the_list_repair_does_not_scale_with_list_length():
    """`absorb()` runs inside `Game.add_concept()` and therefore inside SYNC, and list size is
    uncapped and attacker-controlled -- so the repair must not walk the rows.

    The first version materialized every row of every touched list and issued one `save()` per
    shifted item. Removing position 0 of a 50,000-item list was ~50,000 UPDATEs in the sync path.
    Every existing test here passed, because they all use three-item lists where 3 statements and
    30,000 look the same.

    Query COUNT, not wall time: the point is that the number of statements is flat in list length.
    """
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    from gamelists.services import game_list_service as svc

    def merge_with_a_list_of(size):
        owner = ProfileFactory(is_linked=True, psn_username=f'curator-{size}')
        keeper = ConceptFactory(unified_title=f'Keeper {size}')
        doomed = ConceptFactory(unified_title=f'Doomed {size}')
        game_list = svc.create_list(owner, name=f'List of {size}')
        # Both concepts on one list -- the collision the repair exists for -- plus padding after
        # them, so a re-walk has something to walk.
        svc.add_concept(game_list, owner, keeper)
        svc.add_concept(game_list, owner, doomed)
        for n in range(size):
            svc.add_concept(game_list, owner, ConceptFactory(unified_title=f'Pad {size}-{n}'))

        with CaptureQueriesContext(connection) as captured:
            keeper.absorb(doomed)
        return len(captured.captured_queries), game_list

    small, small_list = merge_with_a_list_of(3)
    large, large_list = merge_with_a_list_of(40)

    assert small == large, (
        f'the merge cost grew from {small} to {large} queries between a 5-item and a 42-item list'
    )

    # And it is still CORRECT -- flatness is worthless if the repair stopped repairing.
    for game_list in (small_list, large_list):
        game_list.refresh_from_db()
        positions = list(game_list.items.order_by('position').values_list('position', flat=True))
        assert positions == list(range(len(positions))), f'positions are not dense: {positions}'
        assert game_list.game_count == len(positions), 'game_count drifted from the rows'


# -- prompt pools (prompts.PromptGame, 2026-09) ---------------------------------------------------
#
# The list branch above migrates one hunter's own curation. This one migrates a pool that OTHER
# PEOPLE have already answered, so the same collision that merely drops a row up there cascades into
# strangers' responses down here. These tests delete the doomed concept at the end for the reason the
# module docstring gives: without the delete they would pass over a re-point that never happened.


def _answered(prompt, concept_rows, *, bucket, hunter):
    """One hunter's response placing every given pool row into `bucket`."""
    from prompts.models import PromptPlacement, PromptResponse

    response = PromptResponse.objects.create(prompt=prompt, profile=hunter,
                                             placement_count=len(concept_rows))
    for i, row in enumerate(concept_rows):
        PromptPlacement.objects.create(response=response, prompt_game=row, bucket=bucket,
                                       single_slot=prompt.is_single_slot, position=i)
    return response


def test_absorb_moves_other_peoples_placements_onto_the_surviving_pool_row():
    """The failure this branch exists to prevent: a catalogue merge silently editing a stranger's
    answer.

    A hunter placed the doomed concept and never held the survivor, so their placement is not a
    duplicate of anything -- it must MOVE. Left to the cascade it would simply vanish, and the hunter
    would find a game missing from a tier list they made, with no event anywhere that explains it."""
    from prompts.models import Prompt, PromptBucket, PromptGame, PromptResponse

    survivor, doomed = ConceptFactory(), ConceptFactory()
    author = ProfileFactory(is_linked=True, psn_username='author')
    responder = ProfileFactory(is_linked=True, psn_username='responder')

    prompt = Prompt.objects.create(owner=author, shape='tier', title='Rank them', game_count=2)
    kept_row = PromptGame.objects.create(prompt=prompt, concept=survivor, position=0)
    doomed_row = PromptGame.objects.create(prompt=prompt, concept=doomed, position=1)
    s_tier = PromptBucket.objects.create(prompt=prompt, label='S', position=0)

    response = _answered(prompt, [doomed_row], bucket=s_tier, hunter=responder)

    survivor.absorb(doomed)
    doomed.delete()

    placement = response.placements.get()
    assert placement.prompt_game_id == kept_row.pk, 'a stranger\'s placement was dropped, not moved'
    assert not PromptGame.objects.filter(pk=doomed_row.pk).exists()
    response.refresh_from_db()
    assert response.placement_count == 1


def test_absorb_drops_the_placement_that_would_become_a_duplicate():
    """A hunter who placed BOTH concepts cannot keep both: `unique(response, prompt_game)` forbids
    it, and the two rows were always the same game. The genuine duplicate cascades away with the
    doomed pool row; the survivor's placement stays where the hunter put it.

    Without the `exclude` in the branch, this insert is the one that raises mid-merge -- and because
    absorb() is not transactional, that raise would commit the re-points above it, skip every repair
    below it, and stop the caller's `other.delete()`."""
    from prompts.models import Prompt, PromptBucket, PromptGame

    survivor, doomed = ConceptFactory(), ConceptFactory()
    author = ProfileFactory(is_linked=True, psn_username='author2')
    responder = ProfileFactory(is_linked=True, psn_username='hadboth')

    prompt = Prompt.objects.create(owner=author, shape='tier', title='Rank them', game_count=2)
    kept_row = PromptGame.objects.create(prompt=prompt, concept=survivor, position=0)
    doomed_row = PromptGame.objects.create(prompt=prompt, concept=doomed, position=1)
    s_tier = PromptBucket.objects.create(prompt=prompt, label='S', position=0)

    response = _answered(prompt, [kept_row, doomed_row], bucket=s_tier, hunter=responder)

    survivor.absorb(doomed)
    doomed.delete()

    assert response.placements.count() == 1, 'the merge left one game in the response twice'
    assert response.placements.get().prompt_game_id == kept_row.pk
    response.refresh_from_db()
    assert response.placement_count == 1, 'placement_count still counts the dropped duplicate'


def test_absorb_repairs_every_response_of_a_colliding_prompt():
    """`placement_count` is the numerator the editor and the response browse both read, and the rows
    it counts were removed from responses nobody touched.

    The repair is one statement over the prompt's responses rather than a loop, because a popular
    prompt is exactly the one with thousands of them -- and this runs inside `Game.add_concept()`,
    which is to say inside sync."""
    from prompts.models import Prompt, PromptBucket, PromptGame

    survivor, doomed = ConceptFactory(), ConceptFactory()
    author = ProfileFactory(is_linked=True, psn_username='author3')

    prompt = Prompt.objects.create(owner=author, shape='tier', title='Rank them', game_count=3)
    kept_row = PromptGame.objects.create(prompt=prompt, concept=survivor, position=0)
    doomed_row = PromptGame.objects.create(prompt=prompt, concept=doomed, position=1)
    third_row = PromptGame.objects.create(prompt=prompt, concept=ConceptFactory(), position=2)
    s_tier = PromptBucket.objects.create(prompt=prompt, label='S', position=0)

    # Three hunters, three different overlaps with the merge.
    only_doomed = _answered(prompt, [doomed_row],
                            bucket=s_tier, hunter=ProfileFactory(is_linked=True, psn_username='a'))
    both = _answered(prompt, [kept_row, doomed_row],
                     bucket=s_tier, hunter=ProfileFactory(is_linked=True, psn_username='b'))
    neither = _answered(prompt, [third_row],
                        bucket=s_tier, hunter=ProfileFactory(is_linked=True, psn_username='c'))

    survivor.absorb(doomed)
    doomed.delete()

    for response, expected in ((only_doomed, 1), (both, 1), (neither, 1)):
        response.refresh_from_db()
        assert response.placement_count == expected
        assert response.placements.count() == expected, 'the count and the rows disagree'

    prompt.refresh_from_db()
    assert prompt.game_count == 2, 'game_count still counts the dropped pool row'
    positions = list(prompt.games.order_by('position').values_list('position', flat=True))
    assert positions == [0, 1], f'pool positions are not dense: {positions}'


def test_absorb_leaves_a_prompt_that_only_held_the_doomed_concept_whole():
    """Per-prompt dedup, same rule the list branch states: a prompt with no collision loses nothing,
    keeps its positions and keeps its count."""
    from prompts.models import Prompt, PromptGame

    survivor, doomed = ConceptFactory(), ConceptFactory()
    author = ProfileFactory(is_linked=True, psn_username='author4')

    # A REAL COLLISION SOMEWHERE ELSE, which this test did not have and needed. The repair runs inside
    # `if rescued:`, so with no collision anywhere the statement under test never executes at all --
    # widening it to every prompt on the site left this test green, because it was asserting over code
    # that had not run. A colliding prompt belonging to somebody else is what arms it.
    other_author = ProfileFactory(is_linked=True, psn_username='collider4')
    colliding = Prompt.objects.create(owner=other_author, shape='tier', title='Both', game_count=2)
    PromptGame.objects.create(prompt=colliding, concept=survivor, position=0)
    PromptGame.objects.create(prompt=colliding, concept=doomed, position=1)

    # DELIBERATELY DRIFTED. Seeding the correct count made this test unable to fail: a repair that
    # rewrote every prompt on the site would land on 2 as well, so "was it left alone?" and "was it
    # recomputed?" were the same assertion. 99 is wrong, and only the narrowing keeps it wrong.
    prompt = Prompt.objects.create(owner=author, shape='poll', title='Which one?', game_count=99)
    first = PromptGame.objects.create(prompt=prompt, concept=doomed, position=0)
    PromptGame.objects.create(prompt=prompt, concept=ConceptFactory(), position=1)

    survivor.absorb(doomed)
    doomed.delete()

    first.refresh_from_db()
    assert first.concept_id == survivor.pk
    prompt.refresh_from_db()
    assert prompt.game_count == 99, 'an untouched prompt was walked and its count rewritten'
    assert list(prompt.games.order_by('position').values_list('position', flat=True)) == [0, 1]
    # The other half of the same claim: the prompt that DID collide was repaired. Without this, the
    # test would also pass against a repair that had been deleted outright.
    colliding.refresh_from_db()
    assert colliding.game_count == 1, 'the colliding prompt was not repaired'


def test_absorb_repairs_only_the_responses_that_lost_a_placement():
    """The narrowing, as a number that cannot be reached by accident.

    The repair used to recompute every response of a colliding prompt. One statement either way, so a
    query-count test sees nothing -- but the row count is the risk: a 50,000-response prompt meant
    50,000 row locks and 50,000 correlated counts inside SYNC, blocking every hunter editing their
    answer to it.

    A response holding neither concept is seeded with a deliberately wrong `placement_count`. If the
    repair still walks it, the wrong number is corrected and this test fails -- which is the only way
    to assert "we did not touch these rows" from the outside."""
    from prompts.models import Prompt, PromptBucket, PromptGame

    survivor, doomed = ConceptFactory(), ConceptFactory()
    author = ProfileFactory(is_linked=True, psn_username='author5')

    prompt = Prompt.objects.create(owner=author, shape='tier', title='Rank them', game_count=3)
    kept_row = PromptGame.objects.create(prompt=prompt, concept=survivor, position=0)
    doomed_row = PromptGame.objects.create(prompt=prompt, concept=doomed, position=1)
    third_row = PromptGame.objects.create(prompt=prompt, concept=ConceptFactory(), position=2)
    s_tier = PromptBucket.objects.create(prompt=prompt, label='S', position=0)

    loser = _answered(prompt, [kept_row, doomed_row],
                      bucket=s_tier, hunter=ProfileFactory(is_linked=True, psn_username='loser'))
    bystander = _answered(prompt, [third_row],
                          bucket=s_tier,
                          hunter=ProfileFactory(is_linked=True, psn_username='bystander'))
    bystander.placement_count = 77
    bystander.save(update_fields=['placement_count'])

    survivor.absorb(doomed)
    doomed.delete()

    loser.refresh_from_db()
    assert loser.placement_count == 1, 'the response that lost a placement was not repaired'
    bystander.refresh_from_db()
    assert bystander.placement_count == 77, (
        'a response that lost nothing was rewritten -- the repair is walking the whole prompt again'
    )


def test_absorb_works_through_a_prompt_whose_buckets_hold_one_game():
    """Every other absorb test here drops into a tier row, so the partial unique
    `unique(response, bucket) WHERE single_slot` was never exercised by a merge at all.

    It is safe for a structural reason worth pinning: the re-point sets `prompt_game_id` only, and
    that constraint is keyed on `(response, bucket)` -- neither column moves, so no index entry
    changes and no collision is possible. If a future version of this branch ever re-files a rescued
    placement into a different bucket, this is the test that stops it."""
    from prompts.models import Prompt, PromptBucket, PromptGame

    survivor, doomed = ConceptFactory(), ConceptFactory()
    author = ProfileFactory(is_linked=True, psn_username='author6')

    poll = Prompt.objects.create(owner=author, shape='poll', title='Best one?', game_count=2)
    kept_row = PromptGame.objects.create(prompt=poll, concept=survivor, position=0)
    doomed_row = PromptGame.objects.create(prompt=poll, concept=doomed, position=1)
    box = PromptBucket.objects.create(prompt=poll, label='Your pick', position=0)

    voted_doomed = _answered(poll, [doomed_row], bucket=box,
                             hunter=ProfileFactory(is_linked=True, psn_username='votedone'))

    survivor.absorb(doomed)
    doomed.delete()

    placement = voted_doomed.placements.get()
    assert placement.prompt_game_id == kept_row.pk, 'a vote was lost to the merge'
    assert placement.single_slot is True, 'the flag that carries the one-pick rule was disturbed'
    assert placement.bucket_id == box.pk
    voted_doomed.refresh_from_db()
    assert voted_doomed.placement_count == 1
