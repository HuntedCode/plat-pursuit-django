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
from django.utils import timezone

from trophies.models import (
    Challenge,
    Concept,
    GenreBonusSlot,
    GenreChallengeSlot,
    UserConceptRating,
)
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


# --- genre challenge slots ----------------------------------------------------
#
# GenreBonusSlot has unique_together (challenge, concept), so it is the one
# Concept relation absorb() cannot bulk-update: a plain .update(concept=self)
# raises IntegrityError the moment both concepts are bonus games in the same
# challenge. Prod hit this approving a ConceptJoinReview in the admin.


def _genre_challenge(profile):
    return Challenge.objects.create(
        profile=profile,
        challenge_type='genre',
        name='Genre Challenge',
    )


def test_bonus_slot_repoints_when_survivor_has_no_slot_in_that_challenge():
    profile = ProfileFactory()
    survivor = ConceptFactory()
    doomed = ConceptFactory()
    challenge = _genre_challenge(profile)
    slot = GenreBonusSlot.objects.create(challenge=challenge, concept=doomed)

    survivor.absorb(doomed)
    doomed.delete()

    slot.refresh_from_db()
    assert slot.concept_id == survivor.id


def test_duplicate_bonus_slot_collapses_instead_of_raising():
    """Both concepts hold a bonus slot in the same challenge — the survivor's
    row is kept and the doomed duplicate is deleted, not left to violate
    (challenge, concept) uniqueness."""
    profile = ProfileFactory()
    survivor = ConceptFactory()
    doomed = ConceptFactory()
    challenge = _genre_challenge(profile)
    survivor_slot = GenreBonusSlot.objects.create(challenge=challenge, concept=survivor)
    doomed_slot = GenreBonusSlot.objects.create(challenge=challenge, concept=doomed)

    survivor.absorb(doomed)
    doomed.delete()

    assert GenreBonusSlot.objects.filter(challenge=challenge).count() == 1
    assert GenreBonusSlot.objects.filter(pk=survivor_slot.pk).exists()
    assert not GenreBonusSlot.objects.filter(pk=doomed_slot.pk).exists()


def test_duplicate_bonus_slot_is_deleted_not_left_as_a_phantom_empty_slot():
    """concept is SET_NULL, so a duplicate merely skipped would survive the
    doomed concept's delete as an assigned-looking-but-empty bonus slot."""
    profile = ProfileFactory()
    survivor = ConceptFactory()
    doomed = ConceptFactory()
    challenge = _genre_challenge(profile)
    GenreBonusSlot.objects.create(challenge=challenge, concept=survivor)
    GenreBonusSlot.objects.create(challenge=challenge, concept=doomed)

    survivor.absorb(doomed)
    doomed.delete()

    assert not GenreBonusSlot.objects.filter(
        challenge=challenge, concept__isnull=True
    ).exists()


def test_collapsed_bonus_slot_carries_its_completion_to_the_survivor():
    """The doomed slot was the completed one — that progress belongs to the
    survivor, since both slots were always the same game."""
    profile = ProfileFactory()
    survivor = ConceptFactory()
    doomed = ConceptFactory()
    challenge = _genre_challenge(profile)
    survivor_slot = GenreBonusSlot.objects.create(
        challenge=challenge, concept=survivor, is_completed=False
    )
    completed_at = timezone.now()
    GenreBonusSlot.objects.create(
        challenge=challenge, concept=doomed,
        is_completed=True, completed_at=completed_at,
    )

    survivor.absorb(doomed)
    doomed.delete()

    survivor_slot.refresh_from_db()
    assert survivor_slot.is_completed is True
    assert survivor_slot.completed_at == completed_at


def test_bonus_count_is_recalculated_after_a_collapse():
    """Collapsing two slots into one must not leave bonus_count reading 2."""
    profile = ProfileFactory()
    survivor = ConceptFactory()
    doomed = ConceptFactory()
    challenge = _genre_challenge(profile)
    GenreBonusSlot.objects.create(challenge=challenge, concept=survivor)
    GenreBonusSlot.objects.create(challenge=challenge, concept=doomed)
    Challenge.objects.filter(pk=challenge.pk).update(bonus_count=2)

    survivor.absorb(doomed)
    doomed.delete()

    challenge.refresh_from_db()
    assert challenge.bonus_count == 1


def test_genre_slot_repoints_without_collapsing():
    """GenreChallengeSlot is keyed on (challenge, genre), so both slots survive
    the merge and simply both point at the survivor."""
    profile = ProfileFactory()
    survivor = ConceptFactory()
    doomed = ConceptFactory()
    challenge = _genre_challenge(profile)
    survivor_slot = GenreChallengeSlot.objects.create(
        challenge=challenge, genre='action', concept=survivor
    )
    doomed_slot = GenreChallengeSlot.objects.create(
        challenge=challenge, genre='rpg', concept=doomed
    )

    survivor.absorb(doomed)
    doomed.delete()

    survivor_slot.refresh_from_db()
    doomed_slot.refresh_from_db()
    assert survivor_slot.concept_id == survivor.id
    assert doomed_slot.concept_id == survivor.id
