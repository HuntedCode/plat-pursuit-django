"""What the database itself promises about Tiers, Grids & Polls.

No views, no service -- the service does not exist yet. That is the point of writing these first: the
whole feature rests on a claim that three shapes are one set of tables, and this is the cheapest place
to find out that they are not.

Every test here drives the models directly and asserts against Postgres. A rule that only the service
enforces is a rule the admin, the shell, a data migration and a future importer all write around; the
ones below are the rules that hold regardless.

The two that carry the most weight:

* **A placement points at the POOL ROW, not at the Concept.** So when an author removes a game, the
  database removes that game from every response, including four hundred belonging to other people.
  `UserChecklistProgress` stored bare item ids in a JSONField and left dangling entries that still
  counted; this is the schema-level answer to that, and `test_removing_a_pool_game_clears_it_from_
  every_response` is the assertion that it works.
* **A poll's bucket is a real row.** With a nullable bucket, `unique(response, bucket)` would not
  constrain two NULL rows -- Postgres treats NULLs as distinct -- and the one-pick guarantee would
  quietly not exist.
"""
import pytest
from django.db import IntegrityError, models, transaction

from prompts.models import (MAX_BUCKETS_PER_PROMPT, MAX_GAMES_PER_PROMPT, MAX_GRID_COLUMNS,
                            SHAPE_GRID, SHAPE_POLL, SHAPE_TIER, SHAPES,
                            SINGLE_SLOT_SHAPES, Prompt,
                            PromptBucket, PromptGame, PromptLike, PromptPlacement, PromptResponse,
                            PromptResponseLike)
from tests.factories import ConceptFactory, ProfileFactory

pytestmark = pytest.mark.django_db


def _prompt(owner, shape=SHAPE_TIER, **kwargs):
    return Prompt.objects.create(owner=owner, shape=shape,
                                 title=kwargs.pop('title', 'Rank the Souls games'), **kwargs)


def _pool(prompt, n=3):
    """`n` games in the pool, dense from 0, as the service will keep them."""
    return [PromptGame.objects.create(prompt=prompt, concept=ConceptFactory(), position=i)
            for i in range(n)]


def _buckets(prompt, *labels):
    return [PromptBucket.objects.create(prompt=prompt, label=label, position=i)
            for i, label in enumerate(labels)]


def _place(response, game, bucket, position=0):
    """`single_slot` comes from the PROMPT, never from the caller.

    Hand-setting it made these tests pass for the wrong reason: the poll test below asserted a second
    vote was refused, but it would have asserted that just as happily against a tier prompt, because
    the only mechanism under test was a boolean the test itself had set to True. Deriving it from
    `Prompt.is_single_slot` -- which is the sole supported source, and which nothing else in the
    codebase reads yet -- means the shape is what decides, which is the actual claim."""
    return PromptPlacement.objects.create(response=response, prompt_game=game, bucket=bucket,
                                          single_slot=bucket.prompt.is_single_slot,
                                          no_duplicates=bucket.prompt.forbids_duplicates,
                                          position=position)


# -- the pool ------------------------------------------------------------------------------------


def test_a_pool_cannot_hold_the_same_game_twice():
    """A pool with one game listed twice is a poll you can vote for twice, and it makes the tray
    (`pool - placements`) ambiguous about which copy came out."""
    prompt = _prompt(ProfileFactory())
    concept = ConceptFactory()
    PromptGame.objects.create(prompt=prompt, concept=concept, position=0)

    with pytest.raises(IntegrityError):
        with transaction.atomic():
            PromptGame.objects.create(prompt=prompt, concept=concept, position=1)


def test_two_prompts_may_hold_the_same_game():
    """The constraint is per prompt. Two hunters ranking the same game is the entire feature."""
    concept = ConceptFactory()
    one, two = _prompt(ProfileFactory()), _prompt(ProfileFactory())

    PromptGame.objects.create(prompt=one, concept=concept, position=0)
    PromptGame.objects.create(prompt=two, concept=concept, position=0)

    assert PromptGame.objects.filter(concept=concept).count() == 2


# -- responses -----------------------------------------------------------------------------------


def test_a_hunter_gets_one_response_per_prompt():
    """The whole social model rests on this: "my answer" is a thing that can be linked to and
    updated, not a stream of attempts."""
    prompt = _prompt(ProfileFactory())
    hunter = ProfileFactory()
    PromptResponse.objects.create(prompt=prompt, profile=hunter)

    with pytest.raises(IntegrityError):
        with transaction.atomic():
            PromptResponse.objects.create(prompt=prompt, profile=hunter)


def test_a_response_is_public_unless_its_author_says_otherwise():
    """Opposite of `GameList.is_public`, deliberately: answering somebody's question IS publishing.
    Asserted because a default that flips silently empties the browse page."""
    response = PromptResponse.objects.create(prompt=_prompt(ProfileFactory()),
                                             profile=ProfileFactory())
    assert response.is_public is True


# -- placements ----------------------------------------------------------------------------------


def test_a_response_cannot_place_one_game_in_two_buckets():
    """Refused wherever `no_duplicates` is set -- which is every tier list and every poll, and a grid
    only when its author turns duplicates off.

    THE RULE THIS USED TO STATE WAS RETRACTED. It read "refused for every shape, including grid,
    where 'wins Best Combat AND Best Story' is a real thing somebody will ask for" -- which was the
    P0 position, overturned by the owner in P3 because on a grid that is the point. The constraint
    went partial; this test kept passing because it uses a tier list, and its docstring went on
    asserting a rule the code no longer holds. Exactly the note-that-stays-believed failure the model
    file warns about, in the file next door."""
    prompt = _prompt(ProfileFactory())
    game = _pool(prompt, 1)[0]
    top, bottom = _buckets(prompt, 'S', 'A')
    response = PromptResponse.objects.create(prompt=prompt, profile=ProfileFactory())
    _place(response, game, top)

    with pytest.raises(IntegrityError):
        with transaction.atomic():
            _place(response, game, bottom)


def test_a_tier_row_holds_many_games_and_a_single_slot_bucket_holds_one():
    """The two halves of the shape rule, as one test because they are one constraint seen from both
    sides. `single_slot` is denormalized from the prompt's shape precisely so the database can tell
    them apart without a join two tables up."""
    owner = ProfileFactory()

    tier = _prompt(owner, SHAPE_TIER)
    a, b = _pool(tier, 2)
    s_row = _buckets(tier, 'S')[0]
    tier_response = PromptResponse.objects.create(prompt=tier, profile=ProfileFactory())
    _place(tier_response, a, s_row, position=0)
    _place(tier_response, b, s_row, position=1)
    assert tier_response.placements.count() == 2, 'a tier row must hold more than one game'

    grid = _prompt(owner, SHAPE_GRID)
    c, d = _pool(grid, 2)
    slot = _buckets(grid, 'Best combat')[0]
    grid_response = PromptResponse.objects.create(prompt=grid, profile=ProfileFactory())
    _place(grid_response, c, slot)

    with pytest.raises(IntegrityError):
        with transaction.atomic():
            _place(grid_response, d, slot)


def test_a_poll_cannot_be_voted_in_twice():
    """The reason a poll's single bucket is a REAL row rather than a synthesized one. Were `bucket`
    nullable for polls, this insert would succeed: Postgres treats NULLs as distinct in a unique
    index, so two null-bucket rows do not collide and the guarantee evaporates at exactly the shape
    that needs it most."""
    poll = _prompt(ProfileFactory(), SHAPE_POLL, title='Best Souls game?')
    left, right = _pool(poll, 2)
    box = _buckets(poll, 'Your pick')[0]
    response = PromptResponse.objects.create(prompt=poll, profile=ProfileFactory())
    _place(response, left, box)

    with pytest.raises(IntegrityError):
        with transaction.atomic():
            _place(response, right, box)


# -- what the author's edits do to other people's answers ----------------------------------------


def test_removing_a_pool_game_clears_it_from_every_response():
    """THE LINE THE WHOLE APP IS BUILT AROUND, asserted against the database rather than trusted.

    The author removes one game; every placement of it disappears from every response, by cascade,
    with no service and nobody to forget. A Concept FK would have looked just as correct here and
    left each of those placements pointing at a game the prompt no longer offers -- rendering it,
    counting it, and tallying it, which is the checklist bug with a foreign key on top."""
    prompt = _prompt(ProfileFactory())
    doomed, kept = _pool(prompt, 2)
    row = _buckets(prompt, 'S')[0]

    responses = [PromptResponse.objects.create(prompt=prompt, profile=ProfileFactory())
                 for _ in range(3)]
    for response in responses:
        _place(response, doomed, row, position=0)
        _place(response, kept, row, position=1)
    assert PromptPlacement.objects.count() == 6

    doomed.delete()

    assert PromptPlacement.objects.count() == 3, 'a removed game survived in somebody\'s response'
    assert not PromptPlacement.objects.filter(prompt_game_id=doomed.pk).exists()
    # ...and everyone else's placement of the game that stayed is untouched.
    for response in responses:
        assert response.placements.get().prompt_game_id == kept.pk


def test_removing_a_bucket_returns_its_games_to_the_tray_rather_than_deleting_them():
    """`bucket` is CASCADE where `GameListItem.section` is SET_NULL, and the difference is not an
    inconsistency. SET_NULL exists there so deleting a section never deletes GAMES; here the game is
    not deleted -- it sits in the pool and the card simply becomes unplaced again. A placement is an
    association, not the content."""
    prompt = _prompt(ProfileFactory())
    game = _pool(prompt, 1)[0]
    row = _buckets(prompt, 'S')[0]
    response = PromptResponse.objects.create(prompt=prompt, profile=ProfileFactory())
    _place(response, game, row)

    row.delete()

    assert not PromptPlacement.objects.exists(), 'the placement outlived its bucket'
    assert PromptGame.objects.filter(pk=game.pk).exists(), 'removing a row deleted a pool game'
    assert PromptResponse.objects.filter(pk=response.pk).exists()


def test_deleting_a_response_leaves_the_prompt_and_its_pool_alone():
    """A responder clearing their answer must not edit the author's question."""
    prompt = _prompt(ProfileFactory())
    game = _pool(prompt, 1)[0]
    row = _buckets(prompt, 'S')[0]
    response = PromptResponse.objects.create(prompt=prompt, profile=ProfileFactory())
    _place(response, game, row)

    response.delete()

    assert PromptGame.objects.filter(pk=game.pk).exists()
    assert PromptBucket.objects.filter(pk=row.pk).exists()
    assert Prompt.objects.filter(pk=prompt.pk).exists()


# -- constraints the service is not the only thing holding ----------------------------------------


def test_a_prompt_needs_a_title_and_a_shape_a_template_can_draw():
    """The admin, the shell and a data migration all write around the service. A shape no template
    can draw is worse here than on a list, because `shape` is immutable: there would be no UI path
    back from it."""
    owner = ProfileFactory()

    with pytest.raises(IntegrityError):
        with transaction.atomic():
            Prompt.objects.create(owner=owner, shape=SHAPE_TIER, title='')

    with pytest.raises(IntegrityError):
        with transaction.atomic():
            Prompt.objects.create(owner=owner, shape='bracket', title='Not a shape yet')


def test_grid_columns_stay_inside_what_the_template_can_divide_by():
    owner = ProfileFactory()

    with pytest.raises(IntegrityError):
        with transaction.atomic():
            _prompt(owner, SHAPE_GRID, grid_columns=0)

    with pytest.raises(IntegrityError):
        with transaction.atomic():
            _prompt(owner, SHAPE_GRID, grid_columns=MAX_GRID_COLUMNS + 1)


def test_a_bucket_needs_a_label():
    """Including a poll's, which the author never sees: a CheckConstraint cannot read the parent's
    shape, so "blank only for polls" is not expressible and the service names it instead."""
    prompt = _prompt(ProfileFactory(), SHAPE_POLL)

    with pytest.raises(IntegrityError):
        with transaction.atomic():
            PromptBucket.objects.create(prompt=prompt, label='', position=0)


# -- likes ---------------------------------------------------------------------------------------


def test_each_of_the_two_likeable_things_takes_one_like_per_hunter():
    """Two tables rather than one with two nullable FKs. Liking the question and liking somebody's
    answer are different acts, and there is no GenericForeignKey anywhere in this codebase."""
    prompt = _prompt(ProfileFactory())
    response = PromptResponse.objects.create(prompt=prompt, profile=ProfileFactory())
    fan = ProfileFactory()

    PromptLike.objects.create(prompt=prompt, profile=fan)
    PromptResponseLike.objects.create(response=response, profile=fan)

    with pytest.raises(IntegrityError):
        with transaction.atomic():
            PromptLike.objects.create(prompt=prompt, profile=fan)

    with pytest.raises(IntegrityError):
        with transaction.atomic():
            PromptResponseLike.objects.create(response=response, profile=fan)

    # The same hunter liking both is one of each, not a collision between them.
    assert PromptLike.objects.count() == 1
    assert PromptResponseLike.objects.count() == 1


# -- the visibility vocabulary --------------------------------------------------------------------


def test_a_signed_out_reader_never_matches_a_private_prompt():
    """The three answers `readable_by` gives, which is the read every detail page makes.

    It does NOT pin the `profile is None` branch, and an earlier version of this docstring claimed it
    did. That branch cannot be made to fail: `owner` is NOT NULL, and `Q(owner=None)` compiles to
    `IS NULL`, which matches nothing on such a column -- so deleting the branch entirely returns the
    same rows and this test still passes. Worth saying out loud rather than leaving a test that reads
    like a guard over something unfalsifiable."""
    owner = ProfileFactory()
    private = _prompt(owner, title='Not yet')
    public = _prompt(owner, title='Ready', is_public=True)

    assert list(Prompt.objects.readable_by(None)) == [public]
    assert set(Prompt.objects.readable_by(owner)) == {private, public}
    assert list(Prompt.objects.readable_by(ProfileFactory())) == [public]


def test_a_soft_deleted_prompt_is_gone_from_every_read_but_still_in_the_table():
    """The floor is opt-in and one word long, exactly as it is for lists: `objects.all()` still
    returns it, because a default manager that hides rows makes `count()` disagree with the
    database."""
    owner = ProfileFactory()
    prompt = _prompt(owner, is_public=True, is_deleted=True)

    assert not Prompt.objects.visible().exists()
    assert not Prompt.objects.public().exists()
    assert not Prompt.objects.readable_by(owner).exists()
    assert Prompt.objects.filter(pk=prompt.pk).exists(), 'the floor should be opt-in, not baked in'


# -- rules the file states in prose and nothing else was holding -----------------------------------


def test_a_response_carries_no_user_written_words():
    """THE PREMISE THE WHOLE RESPONSE SURFACE RESTS ON, and until now it lived only in a docstring.

    A response has no title, no description and no per-placement note. That is what lets every write
    on the response side skip text sanitation, the banned-word check and the `all_ugc` restriction
    gate -- none of which it currently calls, because there is nothing to sanitize.

    Add one text field here and that reasoning silently becomes false: a UGC surface opens with no
    gate in front of it, and nothing in the service would fail, because the service was written when
    the premise was true. So the premise is a test."""
    for model in (PromptResponse, PromptPlacement):
        prose = [f.name for f in model._meta.get_fields()
                 if isinstance(f, (models.CharField, models.TextField))]
        assert not prose, (
            f'{model.__name__} grew a text field ({prose}). That opens a UGC surface with no '
            f'sanitation, no banned-word check and no restriction gate in front of it -- see the '
            f'model docstring before adding one.'
        )


def test_every_shape_maps_to_the_flag_the_database_reads():
    """`single_slot` is the only thing standing between a poll and unlimited votes, and the partial
    unique's predicate is PER ROW -- so one placement written with the wrong flag is not merely
    unconstrained, it is invisible to the index and will not collide with the correct row beside it.

    `Prompt.is_single_slot` is the sole supported source of that value. It had no callers and no
    test, which is a poor state for the sharpest edge in the schema."""
    owner = ProfileFactory()
    assert _prompt(owner, SHAPE_TIER).is_single_slot is False
    assert _prompt(owner, SHAPE_GRID).is_single_slot is True
    assert _prompt(owner, SHAPE_POLL).is_single_slot is True

    # ...and every declared shape is accounted for, so a fourth cannot arrive without a decision
    # here. Asserted as a SET IDENTITY rather than by calling the property: the previous version
    # looped the shapes asserting `is_single_slot in (True, False)`, which is true of any boolean and
    # therefore true of every possible implementation. It could not fail.
    assert SINGLE_SLOT_SHAPES | {SHAPE_TIER} == SHAPES, (
        'a shape was added without deciding whether its buckets hold one game or many'
    )


def test_a_placement_points_at_exactly_one_thing_in_the_database_too():
    """`promptplacement_one_identity`, which the service tests cover only through `_resolve_card`.

    Every other constraint on this table has a direct DB-level test in this file; this one was
    reachable only through the service, so dropping it from the model was a green-suite mutation."""
    prompt = _prompt(ProfileFactory(), SHAPE_GRID, title='Slots')
    game = _pool(prompt, 1)[0]
    bucket = _buckets(prompt, 'Best combat')[0]
    response = PromptResponse.objects.create(prompt=prompt, profile=ProfileFactory())

    with pytest.raises(IntegrityError):          # neither
        with transaction.atomic():
            PromptPlacement.objects.create(response=response, bucket=bucket,
                                           single_slot=True, no_duplicates=True)

    with pytest.raises(IntegrityError):          # both
        with transaction.atomic():
            PromptPlacement.objects.create(response=response, bucket=bucket, prompt_game=game,
                                           concept=ConceptFactory(),
                                           single_slot=True, no_duplicates=True)


def test_a_grids_pool_cap_can_always_cover_its_slots():
    """A grid with duplicates OFF needs at least as many pool games as slots -- `create_prompt`
    refuses to publish one that cannot fill itself. So if the pool cap ever fell below the slot cap,
    that combination would become unpublishable by construction: legal to build, impossible to ship,
    with the refusal naming two numbers the author cannot reconcile.

    Written when the slot cap went 12 -> 36 (owner's call, 2026-09-19), because that change moved one
    of these two numbers toward the other for the first time.
    """
    assert MAX_GAMES_PER_PROMPT[SHAPE_GRID] >= MAX_BUCKETS_PER_PROMPT[SHAPE_GRID]


def test_the_grids_slot_cap_is_its_column_ceiling_squared():
    """36 is DERIVED, not picked: `MAX_GRID_COLUMNS` squared, so every column count an author can
    choose can make a full square. Pinned so raising one without the other is a failing test rather
    than a grid whose widest setting cannot fill its last row."""
    assert MAX_BUCKETS_PER_PROMPT[SHAPE_GRID] == MAX_GRID_COLUMNS ** 2
