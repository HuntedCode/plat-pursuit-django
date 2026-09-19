"""Guards the prompts services state in prose and nothing was holding.

Every test here exists because an audit found a mutation that survived the suite. They are grouped by
what the mutation would cost, worst first, and each names the line it pins.

The first one is the reason this file exists: deleting three characters from an eviction filter let
one hunter's vote change delete every other hunter's vote in the same bucket, silently, with the whole
suite green.
"""
import pytest

from prompts.models import (MIN_GAMES_TO_PUBLISH, SHAPE_GRID, SHAPE_POLL, SHAPE_TIER, Prompt,
                            PromptGame, PromptPlacement, PromptResponse)
from prompts.services import prompt_service as svc
from prompts.services import response_service as rsvc
from tests.factories import ConceptFactory, ProfileFactory

pytestmark = pytest.mark.django_db


def _hunter(psn='hunter'):
    return ProfileFactory(is_linked=True, psn_username=psn)


def _poll(owner, options=3, public=True):
    poll = svc.create_prompt(owner, shape=SHAPE_POLL, title='Which?')
    pool = [svc.add_concept(poll, owner, ConceptFactory()) for _ in range(options)]
    if public:
        svc.update_prompt(poll, owner, is_public=True)
        poll.refresh_from_db()
    return poll, pool, poll.buckets.get()


def _grid(owner, *, slots=2, picks=0, public=True, allow_duplicates=True):
    """A grid, its slots, and some games to answer it WITH -- free picks, not a pool.

    A grid has no author pool (owner's call, 2026-09-19), so the middle value is a list of plain
    Concepts passed as `concept_pk`, never `PromptGame` rows.
    """
    grid = svc.create_prompt(owner, shape=SHAPE_GRID, title='Pick one each',
                             allow_duplicates=allow_duplicates)
    for i in range(slots):
        svc.create_bucket(grid, owner, label=f'Best {i}')
    chosen = [ConceptFactory() for _ in range(picks)]
    if public:
        svc.update_prompt(grid, owner, is_public=True)
        grid.refresh_from_db()
    return grid, chosen, list(grid.buckets.order_by('position'))


def _tier(owner, public=True):
    tier = svc.create_prompt(owner, shape=SHAPE_TIER, title='Rank them')
    pool = [svc.add_concept(tier, owner, ConceptFactory())
            for _ in range(MIN_GAMES_TO_PUBLISH[SHAPE_TIER])]
    if public:
        svc.update_prompt(tier, owner, is_public=True)
        tier.refresh_from_db()
    return tier, pool, list(tier.buckets.order_by('position'))


# ── the worst one ─────────────────────────────────────────────────────────────────────────────────


def test_changing_your_vote_does_not_touch_anybody_elses():
    """THE EVICTION IS SCOPED TO ONE RESPONSE, and nothing was holding that.

    A single-slot bucket evicts its occupant when a new card lands, which is what makes "change your
    vote" work. Drop `response=locked` from that filter and the eviction becomes global: one hunter
    re-voting deletes EVERY hunter's placement in that bucket, and `_recount` then repairs only the
    voter's own count, so every other response keeps a number that no longer matches its rows.

    No test in the suite placed two responses in the same bucket of a single-slot prompt, so the
    mutation was free. This is that test."""
    owner = _hunter()
    poll, pool, box = _poll(owner)

    voters = [_hunter(f'voter-{i}') for i in range(3)]
    for voter in voters:
        rsvc.place(poll, voter, game_id=pool[0].pk, bucket_id=box.pk)
    assert PromptPlacement.objects.filter(bucket=box).count() == 3

    # The third voter changes their mind. The other two must not notice.
    rsvc.place(poll, voters[2], game_id=pool[1].pk, bucket_id=box.pk)

    assert PromptPlacement.objects.filter(bucket=box).count() == 3, (
        "one hunter's change of mind deleted somebody else's vote"
    )
    for voter in voters[:2]:
        response = rsvc.response_for(poll, voter)
        assert response.placements.get().prompt_game_id == pool[0].pk
        assert response.placement_count == 1
    assert rsvc.response_for(poll, voters[2]).placements.get().prompt_game_id == pool[1].pk


# ── withdrawal is never blocked ───────────────────────────────────────────────────────────────────


def test_an_author_cannot_strand_answers_by_giving_an_open_grid_a_pool():
    """A composition of three individually-correct behaviours that together made a trap door.

    An open grid's respondents free-pick from the catalogue. A grid's pool may grow while published.
    A free pick is illegal once a pool exists. Put those together and one `add_concept` left every
    existing answer unable to add, unable to change and -- worst -- unable to REMOVE, because
    `unplace` routed through the same legality check. The only escape was destroying the whole answer.

    Two things now hold it: the author is refused, and withdrawal no longer consults that check at
    all."""
    owner = _hunter()
    grid, _picks, slots = _grid(owner)
    answerer = _hunter('answerer')
    chosen = ConceptFactory()
    rsvc.place(grid, answerer, concept_pk=chosen.pk, bucket_id=slots[0].pk)

    # `match`, because TWO guards can raise here: the shape refusal and the size cap (a grid's cap
    # is zero, so `0 >= 0`). Without it, deleting the shape rule left this green.
    with pytest.raises(svc.PromptError, match='set list'):
        svc.add_concept(grid, owner, ConceptFactory())
    grid.refresh_from_db()
    assert grid.game_count == 0


def test_a_card_can_always_be_taken_back_even_if_the_rules_changed_underneath_it():
    """The second half, pinned separately: `unplace` must not ask whether the card could be PLACED.

    ON A TIER LIST, and that is the whole point of the rewrite. This used to run on a grid with a
    pool row bolted on by hand -- and once the grid pool went away, `_resolve_card` stopped consulting
    `PromptGame` on the free-pick branch at all, so the bolted-on row became inert and the test went
    vacuous: flipping `unplace`'s `allow_free_pick=False` to True killed nothing.

    A free-pick placement on a POOLED shape is the state where the argument is still observable. It
    is not reachable through the service -- which is the point: it is what a shell, the admin or a
    restored dump can leave behind, and withdrawal must work on it anyway. With `allow_free_pick=True`
    this raises "This one has its own set of games to choose from", which is a place-time rule
    refusing a removal: precisely the failure this test exists for.
    """
    owner = _hunter()
    tier, _pool, slots = _tier(owner)
    answerer = _hunter('answerer')
    chosen = ConceptFactory()

    response = PromptResponse.objects.create(prompt=tier, profile=answerer, placement_count=1)
    PromptPlacement.objects.create(response=response, bucket=slots[0], prompt_game=None,
                                   concept=chosen, single_slot=tier.is_single_slot,
                                   no_duplicates=tier.forbids_duplicates, position=0)

    rsvc.unplace(tier, answerer, concept_pk=chosen.pk)
    assert rsvc.response_for(tier, answerer).placements.count() == 0


def test_removing_one_copy_leaves_the_others_where_they_are():
    """`unplace`'s `bucket_id` narrowing. A grid may hold the same game in three slots; tapping remove
    on one of them must not empty the other two."""
    owner = _hunter()
    grid, picks, slots = _grid(owner, slots=3, picks=1)
    answerer = _hunter('answerer')
    for slot in slots:
        rsvc.place(grid, answerer, concept_pk=picks[0].pk, bucket_id=slot.pk)
    assert rsvc.response_for(grid, answerer).placements.count() == 3

    rsvc.unplace(grid, answerer, concept_pk=picks[0].pk, bucket_id=slots[1].pk)

    response = rsvc.response_for(grid, answerer)
    assert response.placements.count() == 2
    assert set(response.placements.values_list('bucket_id', flat=True)) == {slots[0].pk, slots[2].pk}
    assert response.placement_count == 2

    # ...and with no bucket named, every copy comes back.
    rsvc.unplace(grid, answerer, concept_pk=picks[0].pk)
    assert rsvc.response_for(grid, answerer).placements.count() == 0


# ── a published prompt's question ─────────────────────────────────────────────────────────────────


def test_a_published_polls_question_cannot_be_rewritten_under_its_voters():
    """A poll carries its question in its TITLE, and nothing was freezing it. "Best platinum of 2026?"
    collects four hundred votes and becomes "Worst platinum of 2026?" -- every vote now says the
    opposite of what its author meant, no row changed, no trace. Verbatim the harm the grid's slot
    freeze exists to prevent."""
    owner = _hunter()
    poll, pool, box = _poll(owner)
    rsvc.place(poll, _hunter('voter'), game_id=pool[0].pk, bucket_id=box.pk)

    with pytest.raises(svc.PromptError):
        svc.update_prompt(poll, owner, title='Worst platinum of 2026?')
    with pytest.raises(svc.PromptError):
        svc.update_prompt(poll, owner, description='Changed the ask')

    poll.refresh_from_db()
    assert poll.title == 'Which?'


def test_a_grid_and_a_tier_list_keep_their_titles_editable():
    """Only the poll carries its question in the title. A grid's questions are its slot labels, which
    ARE frozen; a tier list is live by design."""
    owner = _hunter()
    grid, _pool, _slots = _grid(owner)
    tier, _tpool, _tslots = _tier(_hunter('tierowner'))

    svc.update_prompt(grid, owner, title='A better name')
    svc.update_prompt(tier, tier.owner, title='A better name')
    grid.refresh_from_db()
    assert grid.title == 'A better name'


def test_a_recolour_is_not_a_change_to_the_question():
    """The freeze is per-FIELD everywhere else in this service; on a bucket it was per-function, so a
    published grid refused a palette change that says nothing to anybody."""
    owner = _hunter()
    grid, _pool, slots = _grid(owner)

    svc.update_bucket(slots[0], owner, colour='purple')
    slots[0].refresh_from_db()
    assert slots[0].colour == 'purple'

    with pytest.raises(svc.PromptError):
        svc.update_bucket(slots[0], owner, label='Renamed')


# ── the duplicates toggle ─────────────────────────────────────────────────────────────────────────


def test_turning_duplicates_off_is_refused_rather_than_crashing():
    """The rewrite flips existing rows INTO the partial unique, so two copies of one card collide
    inside a bulk `.update()` and Postgres raises IntegrityError -- a 500 past every `except
    PromptError`, with nothing telling the author which slot to clear.

    The author needs a refusal in words, which is what this now is."""
    owner = _hunter()
    grid, picks, slots = _grid(owner, slots=3, picks=1, public=False)
    rsvc.place(grid, owner, concept_pk=picks[0].pk, bucket_id=slots[0].pk)
    rsvc.place(grid, owner, concept_pk=picks[0].pk, bucket_id=slots[1].pk)

    with pytest.raises(svc.PromptError) as caught:
        svc.update_prompt(grid, owner, allow_duplicates=False)
    assert 'slot' in str(caught.value).lower()

    grid.refresh_from_db()
    assert grid.allow_duplicates is True, 'the refusal let the flag through anyway'


def test_publishing_and_turning_duplicates_on_in_one_call_is_not_refused():
    """The floor read `forbids_duplicates` off the row while the same call was changing it, so a call
    whose entire purpose was turning duplicates ON was refused with a message about them being off.
    Splitting it in two succeeded -- the signature of a stale read rather than a rule.

    THE STALE READ IS NOW UNREACHABLE: the floor check that consulted the flag went with the grid
    pool, so `_refuse_if_not_publishable` no longer takes it as a parameter. This is kept as a
    regression test for the combined call, which must still land both changes in one go.
    """
    owner = _hunter()
    grid, _picks, _slots = _grid(owner, slots=9, public=False, allow_duplicates=False)

    svc.update_prompt(grid, owner, is_public=True, allow_duplicates=True)

    grid.refresh_from_db()
    assert grid.is_public is True
    assert grid.allow_duplicates is True


# ── the locked re-assertions ──────────────────────────────────────────────────────────────────────


def test_an_answer_cannot_land_on_a_prompt_that_was_just_unpublished():
    """`place()` re-asserts the prompt's state from the LOCKED row, and `is_public` was missing from
    that list. A stranger's answer landing on a withdrawn draft then made it permanently undeletable,
    because deletion is refused once anybody else has answered -- the author locked out of a prompt
    they had already taken down."""
    owner = _hunter()
    grid, picks, slots = _grid(owner, picks=1)
    stranger = _hunter('stranger')
    stale = Prompt.objects.get(pk=grid.pk)          # the stranger's page, rendered while public

    svc.update_prompt(grid, owner, is_public=False)

    with pytest.raises(svc.PromptError):
        rsvc.place(stale, stranger, concept_pk=picks[0].pk, bucket_id=slots[0].pk)

    # ...and the author can still take it away, because nobody else got in.
    svc.delete_prompt(grid, owner)
    grid.refresh_from_db()
    assert grid.is_deleted is True


def test_a_write_against_a_stale_instance_of_a_deleted_prompt_is_refused():
    """`_lock_prompt` re-asserts `is_deleted` on the row that came back, and every caller reaches it
    only after `_require_owner` has asked the same question of the CALLER'S copy -- so the lock's own
    check is reachable exactly when the caller's copy is stale, and nothing tested that."""
    owner = _hunter()
    tier, _pool, _slots = _tier(owner, public=False)
    stale = Prompt.objects.get(pk=tier.pk)

    svc.delete_prompt(tier, owner)

    with pytest.raises(svc.PromptError):
        svc.add_concept(stale, owner, ConceptFactory())
    with pytest.raises(svc.PromptError):
        svc.create_bucket(stale, owner, label='Too late')


# ── counters that must be read from rows ──────────────────────────────────────────────────────────


def test_the_pool_cap_counts_rows_and_not_the_counter():
    """`game_count` drifts HIGH when a Concept is deleted by cascade with no service involved, and
    trusting it would lock a hunter out of a prompt that has room -- permanently, with no action that
    clears it. The comment says so; nothing held it."""
    owner = _hunter()
    tier, _pool, _slots = _tier(owner, public=False)
    Prompt.objects.filter(pk=tier.pk).update(game_count=9_999)
    tier.refresh_from_db()

    svc.add_concept(tier, owner, ConceptFactory())

    tier.refresh_from_db()
    assert tier.game_count == MIN_GAMES_TO_PUBLISH[SHAPE_TIER] + 1, (
        'the cap trusted the counter, or the recount did not correct it'
    )


def test_a_published_prompt_cannot_be_emptied_below_its_own_floor():
    """A tier list is live by design, which let its author take a published, ANSWERED one to zero
    games: unanswerable, unpublishable (somebody answered) and undeletable (same reason). A terminal
    state reached one removal at a time."""
    owner = _hunter()
    tier, pool, slots = _tier(owner)
    rsvc.place(tier, _hunter('responder'), game_id=pool[0].pk, bucket_id=slots[0].pk)

    with pytest.raises(svc.PromptError):
        svc.remove_concept(tier, owner, pool[1])

    tier.refresh_from_db()
    assert tier.game_count == MIN_GAMES_TO_PUBLISH[SHAPE_TIER]

    # Adding one first makes room to remove one.
    svc.add_concept(tier, owner, ConceptFactory())
    svc.remove_concept(tier, owner, pool[1])
    tier.refresh_from_db()
    assert tier.game_count == MIN_GAMES_TO_PUBLISH[SHAPE_TIER]


# ── smaller guards with real consequences ─────────────────────────────────────────────────────────


def test_re_dropping_a_card_where_it_already_sits_changes_nothing():
    """`_next_position` appends over a bucket that still contains the card, so re-placing walked its
    position upward forever -- and on a tier row left a hole at the front while demoting the card to
    last, which is the opposite of what a drag that changed nothing should do."""
    owner = _hunter()
    tier, pool, slots = _tier(owner)
    answerer = _hunter('answerer')
    for game in pool[:3]:
        rsvc.place(tier, answerer, game_id=game.pk, bucket_id=slots[0].pk)

    for _ in range(4):
        rsvc.place(tier, answerer, game_id=pool[0].pk, bucket_id=slots[0].pk)

    response = rsvc.response_for(tier, answerer)
    positions = sorted(response.placements.values_list('position', flat=True))
    assert positions == [0, 1, 2], f'position walked: {positions}'


def test_a_bad_id_is_a_refusal_and_not_a_crash():
    """Every id here arrives from a POST body. Without coercion they reach the ORM and raise
    `ValueError`, which sails past every `except PromptError` and lands as a 500 where a 400 was
    designed."""
    owner = _hunter()
    tier, pool, slots = _tier(owner)
    answerer = _hunter('answerer')

    for kwargs in (
        {'game_id': 'abc', 'bucket_id': slots[0].pk},
        {'game_id': pool[0].pk, 'bucket_id': ''},
        {'concept_pk': 'PP_4821', 'bucket_id': slots[0].pk},
    ):
        with pytest.raises(svc.PromptError):
            rsvc.place(tier, answerer, **kwargs)


def test_a_free_pick_can_be_printed():
    """`prompt_game` became nullable when open grids shipped, and `__str__` still dereferenced it --
    an AttributeError in the admin changelist, in any traceback's repr, and in any message built from
    the object."""
    owner = _hunter()
    grid, _pool, slots = _grid(owner)
    chosen = ConceptFactory(unified_title='Hollow Knight')
    placement = rsvc.place(grid, _hunter('answerer'), concept_pk=chosen.pk, bucket_id=slots[0].pk)

    assert 'Hollow Knight' in str(placement)


def test_the_pool_order_of_a_tier_list_can_still_be_rearranged():
    """`pool_order` is frozen only for a poll. `reorder_games` exists because the pool's first four
    rows are the browse tile's cover mosaic, so silently freezing it on a shape that is allowed it
    would take away the only control over how a prompt presents itself.

    THE GRID HALF IS GONE, and it was `[] == []`. `_grid` stopped returning `PromptGame` rows when
    the grid pool did, so it reordered an empty list and compared two empty lists -- green whatever
    `reorder_games` did. The behaviour it named is not merely untested now, it is unreachable: a grid
    cannot hold a pool row. What a grid should do instead is refuse, which the test below asserts.
    """
    owner = _hunter()
    tier, tpool, _ = _tier(owner)
    svc.reorder_games(tier, owner, [g.pk for g in reversed(tpool)])
    assert list(tier.games.order_by('position').values_list('pk', flat=True)) == [
        g.pk for g in reversed(tpool)]


def test_every_pool_writer_refuses_a_shape_with_no_pool():
    """ALL THREE DOORS, not just the one. "A grid has no pool" went in as a refusal inside
    `add_concept`, and `remove_concept` / `reorder_games` still accepted a grid.

    That was not theoretical. Nothing in the SCHEMA forbids a `PromptGame` on a grid -- a shell, the
    admin or a restored dump can write one -- and `remove_concept`'s other gate is the publish floor,
    which is zero for a grid and short-circuits. So a stray pool row on a PUBLISHED, ANSWERED grid
    could be removed, cascading `PromptPlacement` rows out of other people's answers: exactly what
    freezing `pool_remove` used to prevent before that entry was dropped.

    Driven through the shell-made state the guard exists for, not through the service.
    """
    owner = _hunter()
    grid, _picks, _slots = _grid(owner, public=False)
    stray = PromptGame.objects.create(prompt=grid, concept=ConceptFactory(), position=0)

    with pytest.raises(svc.PromptError, match='set list'):
        svc.add_concept(grid, owner, ConceptFactory())
    with pytest.raises(svc.PromptError, match='set list'):
        svc.remove_concept(grid, owner, stray)
    with pytest.raises(svc.PromptError, match='set list'):
        svc.reorder_games(grid, owner, [stray.pk])

    assert PromptGame.objects.filter(pk=stray.pk).exists(), 'the stray row was removed anyway'


def test_editing_a_bucket_bumps_the_prompt():
    """`_touch` exists because three bucket writers used to skip it, leaving a prompt whose five row
    labels had all been rewritten still reporting itself unmodified. Nothing pinned the fix."""
    owner = _hunter()
    tier, _pool, slots = _tier(owner, public=False)
    before = Prompt.objects.values_list('updated_at', flat=True).get(pk=tier.pk)

    svc.update_bucket(slots[0], owner, label='Godlike')

    after = Prompt.objects.values_list('updated_at', flat=True).get(pk=tier.pk)
    assert after > before, 'a structural edit left the prompt claiming it was unmodified'


def test_grid_columns_can_be_changed_and_are_bounded_when_they_are():
    """`update_prompt(grid_columns=...)` was validated and then never written, and its upper bound was
    only ever tested through `create_prompt`."""
    owner = _hunter()
    grid, _pool, _slots = _grid(owner, public=False)

    svc.update_prompt(grid, owner, grid_columns=4)
    grid.refresh_from_db()
    assert grid.grid_columns == 4

    with pytest.raises(svc.PromptError):
        svc.update_prompt(grid, owner, grid_columns=99)
    grid.refresh_from_db()
    assert grid.grid_columns == 4
