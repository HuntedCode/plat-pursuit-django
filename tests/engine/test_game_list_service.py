"""The service that owns every game-list write.

The old system had none, and the cost of that is the first test below: lists were the ONLY
user-content system on the site that never called `restriction_service`, because the writes lived
inline in twelve API views and there was no single place to put the check. So the tests here are
mostly about the rules that only exist once a service does -- the gate, the cap, the dense ordering,
and the counters that the browse grid sorts on.
"""
import pytest
from django.db import connection
from django.utils import timezone

from gamelists.models import (
    FREE_MAX_LISTS,
    MAX_ITEMS_PER_LIST,
    MEMBER_MAX_LISTS,
    GameList,
    GameListFollow,
    GameListItem,
    GameListLike,
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
    """The promise the restrict form makes out loud. Their lists stay up; they just cannot add."""
    profile = _hunter()
    game_list = svc.create_list(profile, name='Still here', is_public=True)
    _restrict(profile)

    assert GameList.objects.public().filter(pk=game_list.pk).exists()


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


def test_a_list_is_capped_at_its_item_limit():
    profile = _hunter()
    game_list = svc.create_list(profile, name='Full')
    GameList.objects.filter(pk=game_list.pk).update(game_count=MAX_ITEMS_PER_LIST)
    game_list.refresh_from_db()

    with pytest.raises(svc.ListError):
        svc.add_concept(game_list, profile, ConceptFactory())


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

    with pytest.raises(Exception):
        with connection.constraint_checks_disabled():
            GameList.objects.create(owner=profile, name='')
            connection.check_constraints()


def test_a_name_is_stripped_of_markup():
    profile = _hunter()

    game_list = svc.create_list(profile, name='<script>alert(1)</script>Backlog')

    assert '<' not in game_list.name
    assert 'Backlog' in game_list.name


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
