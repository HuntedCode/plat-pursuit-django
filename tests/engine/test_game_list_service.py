"""The service that owns every game-list write.

The old system had none, and the cost of that is the first test below: lists were the ONLY
user-content system on the site that never called `restriction_service`, because the writes lived
inline in twelve API views and there was no single place to put the check. So the tests here are
mostly about the rules that only exist once a service does -- the gate, the cap, the dense ordering,
and the counters that the browse grid sorts on.
"""
import pytest
from django.db import IntegrityError, transaction
from django.utils import timezone

from gamelists.models import (
    FREE_MAX_LISTS,
    LIST_TYPE_COLLECTION,
    LIST_TYPE_RANKED,
    MAX_SECTIONS_PER_LIST,
    MEMBER_MAX_LISTS,
    GameList,
    GameListFollow,
    GameListItem,
    GameListLike,
    GameListSection,
)
from gamelists.services import game_list_service as svc
from tests.factories import ConceptFactory, ProfileFactory
from users.models import UserRestriction

pytestmark = pytest.mark.django_db


def _hunter(psn='curator', premium=False):
    profile = ProfileFactory(is_linked=True, psn_username=psn)
    if premium:
        profile.user_is_premium = True
        profile.save(update_fields=['user_is_premium'])
    return profile


def _restrict(profile, scope='all_ugc'):
    UserRestriction.objects.create(
        user=profile.user, profile=profile, scope=scope,
        reason='spam', created_by_label='Admin',
    )


# ── the gate that did not exist ──────────────────────────────────────────────────────────────────

def test_a_restricted_hunter_cannot_create_a_list():
    """THE test this whole module exists for. Every other UGC system calls
    `restriction_service.is_restricted_from`; lists never did, so restricting somebody stopped their
    quick takes and their reports and left them free to publish list after list."""
    profile = _hunter()
    _restrict(profile)

    with pytest.raises(svc.ListError):
        svc.create_list(profile, name='A list')

    assert GameList.objects.count() == 0, 'the refusal still wrote a row'


def test_a_restricted_hunter_cannot_rename_or_add_to_an_existing_list():
    """Restriction stops NEW writing, and editing an old list is new writing."""
    profile = _hunter()
    game_list = svc.create_list(profile, name='Before')
    concept = ConceptFactory()
    _restrict(profile)

    with pytest.raises(svc.ListError):
        svc.update_list(game_list, profile, name='After')
    with pytest.raises(svc.ListError):
        svc.add_concept(game_list, profile, concept)

    game_list.refresh_from_db()
    assert game_list.name == 'Before'
    assert game_list.game_count == 0


def test_a_restriction_hides_nothing_already_published():
    """The promise the restrict form makes out loud: their lists stay up, they just cannot add.

    Asserted as "still readable AND still refusing writes" in one test. The first version only
    checked the list was still public, which nothing in the codebase mutates on restriction -- so it
    passed with the entire restriction feature deleted. An absence assertion needs a matching
    presence assertion or it is measuring nothing.
    """
    profile = _hunter()
    game_list = svc.create_list(profile, name='Still here', is_public=True)
    svc.add_concept(game_list, profile, ConceptFactory())
    _restrict(profile)

    assert GameList.objects.public().filter(pk=game_list.pk).exists()
    assert GameListItem.objects.filter(game_list=game_list).count() == 1
    with pytest.raises(svc.ListError):
        svc.add_concept(game_list, profile, ConceptFactory())


def test_deleting_is_still_allowed_while_restricted():
    """Restriction is a write ban on new CONTENT, not a lock on your own account. Somebody taking
    down their own list is the opposite of the thing being prevented."""
    profile = _hunter()
    game_list = svc.create_list(profile, name='Regret')
    _restrict(profile)

    svc.delete_list(game_list, profile)

    game_list.refresh_from_db()
    assert game_list.is_deleted is True


# ── the cap ──────────────────────────────────────────────────────────────────────────────────────

def test_a_free_hunter_is_capped_and_a_member_is_capped_higher():
    """Everyone gets lists; members get more of them. Same shape as the shipped sync perk, which is
    what keeps this a dial rather than a door."""
    free, member = _hunter('freehunter'), _hunter('memberhunter', premium=True)

    for n in range(FREE_MAX_LISTS):
        svc.create_list(free, name=f'List {n}')
    with pytest.raises(svc.ListError, match='limit'):
        svc.create_list(free, name='One too many')

    for n in range(MEMBER_MAX_LISTS):
        svc.create_list(member, name=f'List {n}')
    with pytest.raises(svc.ListError, match='limit'):
        svc.create_list(member, name='One too many')

    assert GameList.objects.owned_by(free).count() == FREE_MAX_LISTS
    assert GameList.objects.owned_by(member).count() == MEMBER_MAX_LISTS


def test_a_deleted_list_frees_a_slot():
    """Soft delete keeps the ROW, so a cap counted straight off the table would trap somebody under
    their own limit forever with no way to see why."""
    profile = _hunter()
    lists = [svc.create_list(profile, name=f'List {n}') for n in range(FREE_MAX_LISTS)]

    svc.delete_list(lists[0], profile)

    assert svc.create_list(profile, name='Room again')


# ── ownership ────────────────────────────────────────────────────────────────────────────────────

def test_somebody_elses_list_refuses_every_write():
    """Asserted against the database after each call, not against the exception, because a refusal
    that raises AFTER writing is the failure worth catching."""
    owner, stranger = _hunter('owner'), _hunter('stranger')
    game_list = svc.create_list(owner, name='Mine', is_public=True)
    concept = ConceptFactory()

    for call in (
        lambda: svc.update_list(game_list, stranger, name='Yours'),
        lambda: svc.add_concept(game_list, stranger, concept),
        lambda: svc.delete_list(game_list, stranger),
    ):
        with pytest.raises(svc.ListError):
            call()

    game_list.refresh_from_db()
    assert game_list.name == 'Mine'
    assert game_list.is_deleted is False
    assert GameListItem.objects.count() == 0


def test_a_deleted_list_cannot_still_be_edited_by_its_owner():
    profile = _hunter()
    game_list = svc.create_list(profile, name='Gone')
    svc.delete_list(game_list, profile)

    with pytest.raises(svc.ListError):
        svc.update_list(game_list, profile, name='Back')


# ── items, and the dense-position contract ───────────────────────────────────────────────────────

def test_removing_from_the_middle_closes_the_gap():
    """The browse tile bounds its cover prefetch with `position__lt=4`, so a hole shows three covers
    on a four-game list and reads as a rendering bug. Dense positions are a data contract."""
    profile = _hunter()
    game_list = svc.create_list(profile, name='Ordered')
    items = [svc.add_concept(game_list, profile, ConceptFactory()) for _ in range(4)]

    svc.remove_concept(game_list, profile, items[1])

    positions = list(
        GameListItem.objects.filter(game_list=game_list).order_by('position')
        .values_list('position', flat=True)
    )
    assert positions == [0, 1, 2], f'positions left a gap: {positions}'
    game_list.refresh_from_db()
    assert game_list.game_count == 3


def test_the_same_game_cannot_be_added_twice():
    profile = _hunter()
    game_list = svc.create_list(profile, name='Dupes')
    concept = ConceptFactory()
    svc.add_concept(game_list, profile, concept)

    with pytest.raises(svc.ListError):
        svc.add_concept(game_list, profile, concept)

    game_list.refresh_from_db()
    assert game_list.game_count == 1, 'the refused add still moved the counter'


def test_there_is_no_cap_on_list_size():
    """Members always had unlimited games per list, so a ceiling here would be a takeaway wearing a
    perk's clothes -- and the per-list importer could then refuse a member's own data."""
    profile = _hunter()
    game_list = svc.create_list(profile, name='Long')

    for _ in range(12):
        svc.add_concept(game_list, profile, ConceptFactory())

    game_list.refresh_from_db()
    assert game_list.game_count == 12
    assert not hasattr(svc, 'max_items_for'), 'a size cap came back'


def test_reorder_refuses_a_partial_order_rather_than_applying_it():
    """A short list means the client and the server disagree about what is on it. Applying it would
    silently drop whatever the client forgot."""
    profile = _hunter()
    game_list = svc.create_list(profile, name='Ordered')
    items = [svc.add_concept(game_list, profile, ConceptFactory()) for _ in range(3)]

    with pytest.raises(svc.ListError):
        svc.reorder(game_list, profile, [items[1].id, items[0].id])

    assert [i.id for i in GameListItem.objects.filter(game_list=game_list).order_by('position')] \
        == [items[0].id, items[1].id, items[2].id]


def test_reorder_applies_a_full_order():
    profile = _hunter()
    game_list = svc.create_list(profile, name='Ordered')
    items = [svc.add_concept(game_list, profile, ConceptFactory()) for _ in range(3)]

    svc.reorder(game_list, profile, [items[2].id, items[0].id, items[1].id])

    assert [i.id for i in GameListItem.objects.filter(game_list=game_list).order_by('position')] \
        == [items[2].id, items[0].id, items[1].id]


# ── names ────────────────────────────────────────────────────────────────────────────────────────

def test_a_blank_name_is_refused_by_the_service_and_by_the_database():
    """Both, deliberately. The service is new; the admin, the shell and a future importer all write
    around it, and a nameless list is indistinguishable from every other nameless list."""
    profile = _hunter()

    with pytest.raises(svc.ListError):
        svc.create_list(profile, name='   ')

    # IntegrityError specifically. `pytest.raises(Exception)` would have passed on a typo, an
    # AttributeError, or the constraint helper being unsupported.
    with pytest.raises(IntegrityError):
        GameList.objects.create(owner=profile, name='')


@pytest.mark.parametrize('raw, stored', [
    ('<script>alert(1)</script>Backlog', 'alert(1)Backlog'),
    ('Plain Backlog', 'Plain Backlog'),
])
def test_a_name_is_stripped_of_markup(raw, stored):
    """Pins the OUTPUT, not just the absence of a '<'.

    The first version asserted `'<' not in name` and `'Backlog' in name`, which three very different
    sanitizer behaviours all satisfy -- including a buggy one.
    """
    profile = _hunter()

    assert svc.create_list(profile, name=raw).name == stored


def test_entity_encoded_markup_is_never_stored_as_live_markup():
    """`CommentService.sanitize_text` is NOT idempotent: it bleaches, then `html.unescape()`s its own
    output, so `&lt;script&gt;` survives the bleach as TEXT and the unescape turns it back into a
    live `<script>`. Verified by running it, not reasoned:

        pass 0: '&lt;script&gt;alert(1)&lt;/script&gt;' -> '<script>alert(1)</script>'
        pass 1: '<script>alert(1)</script>'          -> 'alert(1)'

    So ONE pass stores raw markup. That is safe under Django auto-escaping, and list names are
    headed for `og:title` and the Playwright share cards, which are not `{{ }}` contexts. The
    service runs the sanitizer to a fixpoint, which converges on the harmless text.

    The test this replaces asserted `'<' not in name` and passed only because it happened to pick
    the one input that does not exhibit the bug.
    """
    profile = _hunter()

    game_list = svc.create_list(profile, name='&lt;script&gt;alert(1)&lt;/script&gt;')

    assert game_list.name == 'alert(1)'
    assert '<' not in game_list.name and '>' not in game_list.name


def test_markup_that_sanitizes_away_to_nothing_is_refused_as_a_blank_name():
    """`&#60;img ...&#62;` decodes to an `<img>` tag, which the next pass strips entirely -- so the
    fixpoint is the empty string rather than something to store."""
    profile = _hunter()

    with pytest.raises(svc.ListError, match='needs a name'):
        svc.create_list(profile, name='&#60;img src=x onerror=alert(1)&#62;')

    assert GameList.objects.count() == 0


# ── social ───────────────────────────────────────────────────────────────────────────────────────

def test_liking_is_idempotent_in_both_directions():
    """A double-tap, or two tabs, must not bank two likes."""
    owner, reader = _hunter('owner'), _hunter('reader')
    game_list = svc.create_list(owner, name='Likeable', is_public=True)

    assert svc.set_like(game_list, reader, liked=True) == 1
    assert svc.set_like(game_list, reader, liked=True) == 1
    assert GameListLike.objects.count() == 1

    assert svc.set_like(game_list, reader, liked=False) == 0
    assert svc.set_like(game_list, reader, liked=False) == 0
    assert GameListLike.objects.count() == 0


def test_following_a_list_you_cannot_see_is_refused():
    """Otherwise liking is an oracle: it confirms a private list exists, and whose it is, from
    nothing but its id."""
    owner, stranger = _hunter('owner'), _hunter('stranger')
    private = svc.create_list(owner, name='Private')

    with pytest.raises(svc.ListError):
        svc.set_follow(private, stranger, following=True)
    with pytest.raises(svc.ListError):
        svc.set_like(private, stranger, liked=True)

    assert GameListFollow.objects.count() == 0
    assert GameListLike.objects.count() == 0


def test_you_cannot_follow_your_own_list():
    profile = _hunter()
    game_list = svc.create_list(profile, name='Mine', is_public=True)

    with pytest.raises(svc.ListError):
        svc.set_follow(game_list, profile, following=True)


def test_the_counters_track_the_rows_they_count():
    """`like_count` is what the browse grid sorts on, so drift silently reorders the page."""
    owner = _hunter('owner')
    game_list = svc.create_list(owner, name='Counted', is_public=True)
    readers = [_hunter(f'reader{n}') for n in range(3)]

    for reader in readers:
        svc.set_like(game_list, reader, liked=True)
        svc.set_follow(game_list, reader, following=True)
    svc.set_like(game_list, readers[0], liked=False)

    game_list.refresh_from_db()
    assert game_list.like_count == GameListLike.objects.filter(game_list=game_list).count() == 2
    assert game_list.follower_count == GameListFollow.objects.filter(game_list=game_list).count() == 3


# ── the managers ─────────────────────────────────────────────────────────────────────────────────

def test_a_soft_deleted_list_is_gone_from_every_read_path():
    owner, stranger = _hunter('owner'), _hunter('stranger')
    game_list = svc.create_list(owner, name='Deleted', is_public=True)
    svc.delete_list(game_list, owner)

    assert not GameList.objects.visible().exists()
    assert not GameList.objects.public().exists()
    assert not GameList.objects.owned_by(owner).exists()
    assert not GameList.objects.readable_by(owner).exists()
    assert not GameList.objects.readable_by(stranger).exists()
    assert GameList.objects.count() == 1, 'the row should still be there to undelete'


def test_readable_by_shows_you_your_own_private_list_and_nobody_elses():
    owner, stranger = _hunter('owner'), _hunter('stranger')
    private = svc.create_list(owner, name='Private')
    public = svc.create_list(owner, name='Public', is_public=True)

    assert set(GameList.objects.readable_by(owner).values_list('pk', flat=True)) == {
        private.pk, public.pk}
    assert set(GameList.objects.readable_by(stranger).values_list('pk', flat=True)) == {public.pk}
    assert set(GameList.objects.readable_by(None).values_list('pk', flat=True)) == {public.pk}


def test_a_signed_out_reader_never_matches_an_owner_row():
    """`readable_by(None)` branches explicitly rather than passing None into `Q(owner=...)`, which
    would be an `owner_id IS NULL` match rather than the no-op it looks like."""
    owner = _hunter('owner')
    svc.create_list(owner, name='Private')

    assert not GameList.objects.readable_by(None).exists()


# -- what the L1 audit found had no test at all ---------------------------------------------------

def test_every_length_limit_is_enforced():
    """One shared branch validates three fields and nothing exercised it."""
    profile = _hunter()

    with pytest.raises(svc.ListError, match='too long'):
        svc.create_list(profile, name='x' * 121)
    with pytest.raises(svc.ListError, match='too long'):
        svc.create_list(profile, name='Fine', description='x' * 1001)

    game_list = svc.create_list(profile, name='Notes')
    with pytest.raises(svc.ListError, match='too long'):
        svc.add_concept(game_list, profile, ConceptFactory(), note='x' * 501)

    assert GameList.objects.count() == 1
    assert GameListItem.objects.count() == 0


def test_banned_words_are_refused_in_the_name_the_description_and_a_note():
    """The first cut checked the NAME only, leaving the 1000-character public description and every
    per-item note outside the filter."""
    from trophies.models import BannedWord
    from django.core.cache import cache

    BannedWord.objects.create(word='forbidden', is_active=True)
    cache.delete('banned_words:active')
    profile = _hunter()

    with pytest.raises(svc.ListError, match='not allowed'):
        svc.create_list(profile, name='A forbidden list')
    with pytest.raises(svc.ListError, match='not allowed'):
        svc.create_list(profile, name='Fine', description='something forbidden here')

    game_list = svc.create_list(profile, name='Clean')
    with pytest.raises(svc.ListError, match='not allowed'):
        svc.add_concept(game_list, profile, ConceptFactory(), note='forbidden')

    assert GameListItem.objects.count() == 0
    cache.delete('banned_words:active')


def test_an_unlinked_hunter_cannot_write_at_all():
    """Every other UGC surface requires a linked profile; the inline views this replaces did too."""
    unlinked = ProfileFactory(is_linked=False, psn_username='notlinked')

    with pytest.raises(svc.ListError, match='Link your PSN'):
        svc.create_list(unlinked, name='Nope')

    assert GameList.objects.count() == 0


# -- themes -------------------------------------------------------------------------------------

def test_a_restricted_hunter_can_still_unpublish_their_own_list():
    """A restriction stops new WORDS. Gating the whole of `update_list` trapped somebody's list in
    public as a side effect of a decision about their prose -- the failure `api/rating_views.py`
    documents from the other direction."""
    profile = _hunter()
    game_list = svc.create_list(profile, name='Public', is_public=True)
    _restrict(profile)

    svc.update_list(game_list, profile, is_public=False)

    game_list.refresh_from_db()
    assert game_list.is_public is False


def test_a_restricted_hunter_cannot_like_but_can_unlike():
    """`GameListLike` is the same shape as the four vote models, and `CommentService.toggle_vote`
    already refuses a restricted vote -- so leaving likes open reopens the hole this service exists
    to close, on the one write that feeds a public ranking."""
    owner, reader = _hunter('owner'), _hunter('reader')
    game_list = svc.create_list(owner, name='Ranked', is_public=True)
    svc.set_like(game_list, reader, liked=True)
    _restrict(reader)

    with pytest.raises(svc.ListError):
        svc.set_like(game_list, reader, liked=True)

    svc.set_like(game_list, reader, liked=False)
    assert GameListLike.objects.count() == 0


def test_nobody_can_like_their_own_list():
    """Otherwise an author ranks themselves up the sort `like_count` drives."""
    profile = _hunter()
    game_list = svc.create_list(profile, name='Mine', is_public=True)

    with pytest.raises(svc.ListError):
        svc.set_like(game_list, profile, liked=True)

    assert GameListLike.objects.count() == 0


# -- the stale-pivot corruption -------------------------------------------------------------------

def test_removing_the_same_item_twice_does_not_corrupt_the_order():
    """No concurrency needed -- a double-click did it.

    `remove_concept` read `position` off the CALLER's in-memory item. `Model.delete()` on an
    already-deleted row removes nothing and does not raise, so the replay re-ran the shift with a
    stale pivot and left two rows sharing a position, silently breaking the dense-ordering contract
    the module calls load-bearing.
    """
    profile = _hunter()
    game_list = svc.create_list(profile, name='Ordered')
    items = [svc.add_concept(game_list, profile, ConceptFactory()) for _ in range(4)]

    svc.remove_concept(game_list, profile, items[1])
    with pytest.raises(svc.ListError):
        svc.remove_concept(game_list, profile, items[1])

    surviving = list(
        GameListItem.objects.filter(game_list=game_list).order_by('position')
        .values_list('id', 'position')
    )
    assert [p for _id, p in surviving] == [0, 1, 2], f'positions corrupted: {surviving}'
    assert [i for i, _p in surviving] == [items[0].id, items[2].id, items[3].id]
    game_list.refresh_from_db()
    assert game_list.game_count == 3


def test_an_item_cannot_be_removed_through_another_list():
    """A security-relevant guard with no coverage: removing an entry by passing a list you DO own
    and an item that belongs to somebody else's."""
    owner, other = _hunter('owner'), _hunter('other')
    mine = svc.create_list(owner, name='Mine')
    theirs = svc.create_list(other, name='Theirs')
    their_item = svc.add_concept(theirs, other, ConceptFactory())

    with pytest.raises(svc.ListError):
        svc.remove_concept(mine, owner, their_item)

    assert GameListItem.objects.filter(pk=their_item.pk).exists()


def test_remove_and_reorder_refuse_a_list_that_is_not_yours():
    """`test_somebody_elses_list_refuses_every_write` covered three of the six owner-guarded
    writes; these were the two it missed."""
    owner, stranger = _hunter('owner'), _hunter('stranger')
    game_list = svc.create_list(owner, name='Mine', is_public=True)
    item = svc.add_concept(game_list, owner, ConceptFactory())

    with pytest.raises(svc.ListError):
        svc.remove_concept(game_list, stranger, item)
    with pytest.raises(svc.ListError):
        svc.reorder(game_list, stranger, [item.id])

    assert GameListItem.objects.filter(pk=item.pk).exists()


def test_positions_start_at_zero_and_stay_dense_as_games_are_added():
    """Never asserted directly before -- only inferred through the removal test."""
    profile = _hunter()
    game_list = svc.create_list(profile, name='Ordered')

    positions = [svc.add_concept(game_list, profile, ConceptFactory()).position for _ in range(4)]

    assert positions == [0, 1, 2, 3]


def test_a_drifted_counter_does_not_hand_a_new_item_a_taken_position():
    """`position` is derived from the ROWS, not from `game_count`.

    The counter can drift from paths this service does not own (a concept merge dropping a
    colliding entry, an admin deleting a Concept, the importer). Deriving position from it then
    hands a new item a position another row already holds -- silently, since nothing constrains
    position -- which is the exact three-covers-on-a-four-game-list bug the module warns about.
    """
    profile = _hunter()
    game_list = svc.create_list(profile, name='Drifted')
    svc.add_concept(game_list, profile, ConceptFactory())
    svc.add_concept(game_list, profile, ConceptFactory())

    GameList.objects.filter(pk=game_list.pk).update(game_count=0)
    game_list.refresh_from_db()
    fresh = svc.add_concept(game_list, profile, ConceptFactory())

    assert fresh.position == 2, 'the new item took a position another row already holds'
    positions = list(GameListItem.objects.filter(game_list=game_list)
                     .order_by('position').values_list('position', flat=True))
    assert positions == [0, 1, 2]
    game_list.refresh_from_db()
    assert game_list.game_count == 3, 'the counter did not self-heal'


def test_deleting_a_list_twice_is_a_no_op():
    """A second click should not answer "that list no longer exists" about a list you just
    removed."""
    profile = _hunter()
    game_list = svc.create_list(profile, name='Gone')

    svc.delete_list(game_list, profile)
    svc.delete_list(game_list, profile)

    game_list.refresh_from_db()
    assert game_list.is_deleted is True


def test_reorder_refuses_junk_ids_rather_than_raising_a_500():
    """A service that bills itself as the sole writer coerces rather than assuming; string ids from
    a JSON body used to raise TypeError out of `sorted()`."""
    profile = _hunter()
    game_list = svc.create_list(profile, name='Ordered')
    item = svc.add_concept(game_list, profile, ConceptFactory())

    with pytest.raises(svc.ListError):
        svc.reorder(game_list, profile, ['not-an-id'])
    # A string that IS numeric is coerced and accepted -- JSON bodies send those legitimately.
    svc.reorder(game_list, profile, [str(item.id)])


def test_removing_from_the_middle_keeps_the_right_games_in_the_right_order():
    """The shape assertion (`[0, 1, 2]`) passed for an implementation that shuffled. This pins WHICH
    item sits where."""
    profile = _hunter()
    game_list = svc.create_list(profile, name='Ordered')
    items = [svc.add_concept(game_list, profile, ConceptFactory()) for _ in range(4)]

    svc.remove_concept(game_list, profile, items[1])

    assert list(
        GameListItem.objects.filter(game_list=game_list).order_by('position')
        .values_list('id', flat=True)
    ) == [items[0].id, items[2].id, items[3].id]


def test_reorder_leaves_positions_dense():
    """Comparing ids ordered BY position would pass for positions 5, 10, 20."""
    profile = _hunter()
    game_list = svc.create_list(profile, name='Ordered')
    items = [svc.add_concept(game_list, profile, ConceptFactory()) for _ in range(3)]

    svc.reorder(game_list, profile, [items[2].id, items[0].id, items[1].id])

    assert list(
        GameListItem.objects.filter(game_list=game_list).order_by('position')
        .values_list('position', flat=True)
    ) == [0, 1, 2]


# ── list types ───────────────────────────────────────────────────────────────────────────────────

def test_a_new_list_is_a_collection_unless_asked_otherwise():
    profile = _hunter()

    assert svc.create_list(profile, name='Default').list_type == LIST_TYPE_COLLECTION
    assert svc.create_list(
        profile, name='Ranked one', list_type=LIST_TYPE_RANKED).list_type == LIST_TYPE_RANKED


@pytest.mark.parametrize('bogus', ['tier', 'progress', 'RANKED', '', 'collection ranked'])
def test_a_type_that_cannot_be_rendered_is_refused_rather_than_stored(bogus):
    """`choices` is a form-level concern, NOT a database constraint.

    Without the service check these land in the column and the detail page -- which branches on the
    two types it can draw -- renders a Collection while the hunter believes they made something else.
    The capitalised variant is in here on purpose: the column would take 'RANKED' happily and every
    `== 'ranked'` comparison on the site would then be false.
    """
    profile = _hunter()

    with pytest.raises(svc.ListError):
        svc.create_list(profile, name='Bogus', list_type=bogus)
    assert GameList.objects.count() == 0, 'the refusal still wrote a row'

    game_list = svc.create_list(profile, name='Real')
    with pytest.raises(svc.ListError):
        svc.update_list(game_list, profile, list_type=bogus)

    game_list.refresh_from_db()
    assert game_list.list_type == LIST_TYPE_COLLECTION, 'a refused type reached the column'


def test_switching_type_keeps_every_game_and_its_order():
    """The type is PRESENTATION. Switching it must not be a data migration in disguise, because a
    hunter trying Ranked and going back to Collection would otherwise lose the sequence they set."""
    profile = _hunter()
    game_list = svc.create_list(profile, name='Switcher')
    items = [svc.add_concept(game_list, profile, ConceptFactory()) for _ in range(4)]
    svc.reorder(game_list, profile, [items[3].id, items[1].id, items[0].id, items[2].id])
    expected = [items[3].id, items[1].id, items[0].id, items[2].id]

    svc.update_list(game_list, profile, list_type=LIST_TYPE_RANKED)
    svc.update_list(game_list, profile, list_type=LIST_TYPE_COLLECTION)

    game_list.refresh_from_db()
    assert game_list.list_type == LIST_TYPE_COLLECTION
    assert game_list.game_count == 4
    assert list(
        GameListItem.objects.filter(game_list=game_list).order_by('position')
        .values_list('id', flat=True)
    ) == expected, 'the round trip through Ranked reshuffled the list'


def test_a_restricted_hunter_can_still_switch_type():
    """The gate is scoped to acts that put WORDS in front of people.

    Choosing between two presentations of your own rows submits no content, so it sits with
    un-publishing and deleting on the allowed side -- the same line `update_list` documents. Gating
    it would freeze a restricted hunter's list in a shape they cannot change while leaving the list
    itself up, which is punishment with no moderation value.
    """
    profile = _hunter()
    game_list = svc.create_list(profile, name='Mine', list_type=LIST_TYPE_RANKED)
    _restrict(profile)

    svc.update_list(game_list, profile, list_type=LIST_TYPE_COLLECTION)

    game_list.refresh_from_db()
    assert game_list.list_type == LIST_TYPE_COLLECTION

    # ...but the restriction is still live on the field beside it, or the test above proves nothing.
    with pytest.raises(svc.ListError):
        svc.update_list(game_list, profile, name='A new name')


def test_switching_type_is_not_smuggled_publishing():
    """`list_type` travels through the same endpoint as `is_public`, so the two must stay separable:
    a type switch alone must never change visibility."""
    profile = _hunter()
    game_list = svc.create_list(profile, name='Private one')
    assert game_list.is_public is False

    svc.update_list(game_list, profile, list_type=LIST_TYPE_RANKED)

    game_list.refresh_from_db()
    assert game_list.is_public is False, 'a type switch published the list'


# ── sections ─────────────────────────────────────────────────────────────────────────────────────

def test_sections_are_a_member_feature():
    """The perk, and the whole reason the gate exists. A free hunter keeps every other capability --
    they create lists, add games, rank them, publish and share."""
    free = _hunter(psn='free')
    game_list = svc.create_list(free, name='Mine')

    with pytest.raises(svc.ListError) as refused:
        svc.create_section(game_list, free, name='Playing')
    assert 'member' in str(refused.value).lower()
    assert GameListSection.objects.count() == 0, 'the refusal still wrote a row'

    # ...and the same hunter can do everything else, which is what makes this a perk rather than a
    # wall: a gate that also blocked the free path would be a different product decision.
    concept = ConceptFactory()
    svc.add_concept(game_list, free, concept)
    svc.update_list(game_list, free, name='Renamed', is_public=True)
    assert game_list.items.count() == 1


def test_a_member_can_section_a_list():
    member = _hunter(psn='member', premium=True)
    game_list = svc.create_list(member, name='Backlog')

    playing = svc.create_section(game_list, member, name='Playing')
    someday = svc.create_section(game_list, member, name='Someday')

    assert [s.name for s in game_list.sections.all()] == ['Playing', 'Someday']
    assert [s.position for s in game_list.sections.all()] == [0, 1], 'positions are not dense'
    # An EMPTY section is a legitimate state -- you make "Someday" and then drag into it. A CharField
    # key on the item could not have expressed this, which is why sections have their own table.
    assert someday.items.count() == 0


def test_a_lapsed_member_keeps_their_sections_and_can_still_arrange_them():
    """THE RULE MOST LIKELY TO BE GOT WRONG. Membership ending must not delete data or reshuffle a
    list -- it keeps rendering exactly as it did. What they lose is making MORE.

    Anything else is a takeback, which is the same argument `models.py` makes about list size."""
    member = _hunter(psn='lapsing', premium=True)
    game_list = svc.create_list(member, name='Backlog')
    finished = svc.create_section(game_list, member, name='Finished')
    playing = svc.create_section(game_list, member, name='Playing')
    item = svc.add_concept(game_list, member, ConceptFactory())
    svc.assign_item(game_list, member, item, finished)

    member.user_is_premium = False
    member.save(update_fields=['user_is_premium'])

    # The data is untouched and the list still reads the same.
    assert game_list.sections.count() == 2
    item.refresh_from_db()
    assert item.section_id == finished.id

    # ...and they can still ARRANGE what they have: move a game, reorder, delete, choose numbering.
    svc.assign_item(game_list, member, item, playing)
    svc.reorder_sections(game_list, member, [playing.id, finished.id])
    svc.update_list(game_list, member, restart_numbering=True)
    svc.delete_section(finished, member)

    # Only CREATING more is closed.
    with pytest.raises(svc.ListError):
        svc.create_section(game_list, member, name='Another')
    with pytest.raises(svc.ListError):
        svc.rename_section(playing, member, name='Renamed')


def test_deleting_a_section_orphans_its_games_rather_than_deleting_them():
    """A cascade here would destroy hand-curated entries because of a structural change the hunter
    made about a HEADER. Same trap `Concept.absorb()`'s list branch documents."""
    member = _hunter(psn='tidy', premium=True)
    game_list = svc.create_list(member, name='Backlog')
    section = svc.create_section(game_list, member, name='Doomed')
    items = [svc.add_concept(game_list, member, ConceptFactory()) for _ in range(3)]
    for item in items:
        svc.assign_item(game_list, member, item, section)

    svc.delete_section(section, member)

    assert GameListItem.objects.filter(game_list=game_list).count() == 3, 'games were deleted'
    assert not GameListItem.objects.filter(section__isnull=False).exists(), 'items kept a dead FK'
    game_list.refresh_from_db()
    assert game_list.game_count == 3


def test_section_positions_stay_dense_when_one_is_deleted():
    member = _hunter(psn='dense', premium=True)
    game_list = svc.create_list(member, name='Ordered')
    made = [svc.create_section(game_list, member, name=f'S{n}') for n in range(4)]

    svc.delete_section(made[1], member)

    assert list(game_list.sections.values_list('position', flat=True)) == [0, 1, 2]


def test_item_positions_are_untouched_by_sections():
    """THE INVARIANT SECTIONS WERE BUILT AROUND. `position` stays global and dense, because
    `attach_cover_games` bounds the browse mosaic with `position__lt=4` and `reorder` refuses partial
    orderings -- so per-section ordering would have been a rewrite rather than a tweak.

    Assigning games to sections in an order that disagrees with their positions must change nothing.
    """
    member = _hunter(psn='invariant', premium=True)
    game_list = svc.create_list(member, name='Ranked', list_type=LIST_TYPE_RANKED)
    items = [svc.add_concept(game_list, member, ConceptFactory()) for _ in range(4)]
    first = svc.create_section(game_list, member, name='First')
    second = svc.create_section(game_list, member, name='Second')

    # Deliberately interleaved: 0 and 2 into one section, 1 and 3 into the other.
    svc.assign_item(game_list, member, items[0], first)
    svc.assign_item(game_list, member, items[2], first)
    svc.assign_item(game_list, member, items[1], second)
    svc.assign_item(game_list, member, items[3], second)

    positions = list(GameListItem.objects.filter(game_list=game_list)
                     .order_by('position').values_list('position', flat=True))
    assert positions == [0, 1, 2, 3], 'sectioning moved item positions'

    # ...and `reorder` still works on the whole list, unaware that sections exist.
    svc.reorder(game_list, member, [items[3].id, items[2].id, items[1].id, items[0].id])
    assert list(GameListItem.objects.filter(game_list=game_list).order_by('position')
                .values_list('id', flat=True)) == [items[3].id, items[2].id, items[1].id, items[0].id]


def test_a_game_cannot_be_filed_under_another_lists_section():
    """Without this an item takes a section id from a list the caller may not even be able to see:
    it would render nowhere, and the refusal (or its absence) would confirm that section exists."""
    member = _hunter(psn='mine', premium=True)
    stranger = _hunter(psn='theirs', premium=True)
    mine = svc.create_list(member, name='Mine')
    theirs = svc.create_list(stranger, name='Theirs')
    their_section = svc.create_section(theirs, stranger, name='Elsewhere')
    item = svc.add_concept(mine, member, ConceptFactory())

    with pytest.raises(svc.ListError):
        svc.assign_item(mine, member, item, their_section)

    item.refresh_from_db()
    assert item.section_id is None


def test_a_section_name_is_public_text_and_is_treated_as_such():
    """Easy to miss, because a section feels structural rather than editorial -- but the header
    renders on a public page under the author's byline, which is the definition of `all_ugc`."""
    member = _hunter(psn='writer', premium=True)
    game_list = svc.create_list(member, name='Mine')

    with pytest.raises(svc.ListError):
        svc.create_section(game_list, member, name='   ')
    with pytest.raises(svc.ListError):
        svc.create_section(game_list, member, name='x' * 200)

    # Markup is sanitized to a fixpoint, exactly as a list name is.
    section = svc.create_section(game_list, member, name='<b>Bold</b> plans')
    assert '<' not in section.name and '>' not in section.name

    # AND THE BANNED-WORD FILTER, which is the half a section is most likely to slip past: the name
    # reads as structural, so it is the one public string somebody would think of as a label rather
    # than as writing. Both paths, because rename is a second door into the same field.
    from django.core.cache import cache
    from trophies.models import BannedWord
    BannedWord.objects.create(word='forbidden', is_active=True)
    cache.delete('banned_words:active')

    with pytest.raises(svc.ListError, match='not allowed'):
        svc.create_section(game_list, member, name='A forbidden section')
    with pytest.raises(svc.ListError, match='not allowed'):
        svc.rename_section(section, member, name='Still forbidden')

    # ...and a restricted hunter cannot write one, member or not.
    _restrict(member)
    with pytest.raises(svc.ListError):
        svc.create_section(game_list, member, name='After the ban')


def test_sections_are_capped_per_list():
    """Unlike list SIZE, which is uncapped on purpose. A section is a rendered header with its own
    row, so a hundred of them is a page nobody can read."""
    member = _hunter(psn='prolific', premium=True)
    game_list = svc.create_list(member, name='Many')
    for n in range(MAX_SECTIONS_PER_LIST):
        svc.create_section(game_list, member, name=f'S{n}')

    with pytest.raises(svc.ListError) as refused:
        svc.create_section(game_list, member, name='One too many')
    assert str(MAX_SECTIONS_PER_LIST) in str(refused.value)
    assert GameListSection.objects.filter(game_list=game_list).count() == MAX_SECTIONS_PER_LIST


def test_reordering_sections_refuses_a_partial_order():
    """Same rule as item `reorder`, and for the same reason: a subset means the client and the server
    disagree about what is on the list, and applying it would silently drop the rest."""
    member = _hunter(psn='orderly', premium=True)
    game_list = svc.create_list(member, name='Ordered')
    made = [svc.create_section(game_list, member, name=f'S{n}') for n in range(3)]

    with pytest.raises(svc.ListError):
        svc.reorder_sections(game_list, member, [made[0].id, made[1].id])

    assert list(game_list.sections.values_list('position', flat=True)) == [0, 1, 2]


def test_the_numbering_toggle_stores_a_choice_and_nothing_else():
    """It is a DISPLAY choice: both modes are render-time derivations of a global `position`, which
    is why it cost a boolean rather than a migration.

    It rides `update_list` rather than having a writer of its own -- it is a property of the list
    exactly as `list_type` is, and the editor saves both in one press. A separate
    `set_section_numbering` existed for one commit and was folded in; two ways to write one field is
    how they drift."""
    member = _hunter(psn='numberer', premium=True)
    game_list = svc.create_list(member, name='Ranked', list_type=LIST_TYPE_RANKED)
    items = [svc.add_concept(game_list, member, ConceptFactory()) for _ in range(3)]

    assert game_list.sections_restart_numbering is False, 'continue-through is the default'
    svc.update_list(game_list, member, restart_numbering=True)

    game_list.refresh_from_db()
    assert game_list.sections_restart_numbering is True
    # Nothing moved.
    assert list(GameListItem.objects.filter(game_list=game_list).order_by('position')
                .values_list('id', flat=True)) == [i.id for i in items]


def test_only_the_owner_can_touch_a_section():
    member = _hunter(psn='owner2', premium=True)
    stranger = _hunter(psn='stranger2', premium=True)
    game_list = svc.create_list(member, name='Mine', is_public=True)
    section = svc.create_section(game_list, member, name='Mine too')
    item = svc.add_concept(game_list, member, ConceptFactory())

    for call in (
        lambda: svc.create_section(game_list, stranger, name='Theirs'),
        lambda: svc.rename_section(section, stranger, name='Theirs'),
        lambda: svc.delete_section(section, stranger),
        lambda: svc.reorder_sections(game_list, stranger, [section.id]),
        lambda: svc.assign_item(game_list, stranger, item, section),
        lambda: svc.update_list(game_list, stranger, restart_numbering=True),
    ):
        with pytest.raises(svc.ListError):
            call()

    assert GameListSection.objects.filter(game_list=game_list).count() == 1


def test_deleting_a_section_twice_does_not_corrupt_the_order(client):
    """`delete_section` read `position` off the object the VIEW fetched, pre-lock, and `Model.delete()`
    on an already-deleted row removes nothing and does NOT raise -- so a double submit ran the shift
    twice against the same pivot and left two sections sharing a position. `Meta.ordering` then goes
    non-deterministic and `create_section`'s `Max(position) + 1` leaves a permanent hole.

    `_lock_item` exists for exactly this on items and carries the story in its docstring; sections
    shipped without the counterpart."""
    owner = _hunter(premium=True)
    game_list = svc.create_list(owner, name='Backlog')
    kept_a = svc.create_section(game_list, owner, name='A')
    doomed = svc.create_section(game_list, owner, name='B')
    kept_c = svc.create_section(game_list, owner, name='C')
    kept_d = svc.create_section(game_list, owner, name='D')

    svc.delete_section(doomed, owner)
    # The SAME stale object again, which is what a second tab (or a double click) posts.
    with pytest.raises(svc.ListError):
        svc.delete_section(doomed, owner)

    positions = list(
        GameListSection.objects.filter(game_list=game_list).order_by('position')
        .values_list('name', 'position'))
    assert positions == [('A', 0), ('C', 1), ('D', 2)], positions
    assert len({p for _n, p in positions}) == 3, 'two sections share a position'

    # ...and the next section still lands at the end rather than in a hole.
    assert svc.create_section(game_list, owner, name='E').position == 3
    assert kept_a.pk and kept_c.pk and kept_d.pk


def test_renaming_a_deleted_section_is_refused_rather_than_a_500(client):
    """It was the one section write that took no lock at all, so `save(update_fields=['name'])` ran
    against zero rows -- which Django turns into `DatabaseError`, i.e. a 500 where the client expects
    the 400 it knows how to display."""
    owner = _hunter(premium=True)
    game_list = svc.create_list(owner, name='Backlog')
    section = svc.create_section(game_list, owner, name='Playing')
    stale = GameListSection.objects.get(pk=section.pk)
    svc.delete_section(section, owner)

    with pytest.raises(svc.ListError):
        svc.rename_section(stale, owner, name='Renamed')


def test_filing_into_a_deleted_section_is_refused_rather_than_a_500(client):
    """`assign_item` checked the section's PARENT and never that it still existed, so writing the FK
    raised IntegrityError -- a 500 HTML body to a JSON caller. `reorder` re-resolved under the lock
    all along; the two cross-section-drop paths must not disagree about how careful they are."""
    owner = _hunter(premium=True)
    game_list = svc.create_list(owner, name='Backlog')
    item = svc.add_concept(game_list, owner, ConceptFactory())
    section = svc.create_section(game_list, owner, name='Playing')
    stale = GameListSection.objects.get(pk=section.pk)
    svc.delete_section(section, owner)

    with pytest.raises(svc.ListError):
        svc.assign_item(game_list, owner, item, stale)

    item.refresh_from_db()
    assert item.section_id is None
