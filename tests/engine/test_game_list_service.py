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

def test_a_theme_must_be_a_real_theme():
    """The rewrite dropped the whitelist the inline view had, so any 50-character string reached a
    column the renderer looks up by key."""
    profile = _hunter(premium=True)
    game_list = svc.create_list(profile, name='Themed')

    with pytest.raises(svc.ListError, match='not available'):
        svc.update_list(game_list, profile, selected_theme='<script>')

    game_list.refresh_from_db()
    assert game_list.selected_theme == ''


def test_a_real_theme_is_accepted_for_a_member_and_refused_for_everybody_else():
    from trophies.themes import GRADIENT_THEMES

    real = next(k for k, v in GRADIENT_THEMES.items() if not v.get('requires_game_image'))
    member, free = _hunter('member', premium=True), _hunter('free')

    members_list = svc.create_list(member, name='Themed')
    svc.update_list(members_list, member, selected_theme=real)
    members_list.refresh_from_db()
    assert members_list.selected_theme == real

    frees_list = svc.create_list(free, name='Plain')
    with pytest.raises(svc.ListError, match='member perk'):
        svc.update_list(frees_list, free, selected_theme=real)


def test_a_lapsed_member_can_still_clear_a_theme_but_not_set_one():
    """Clearing is not setting. Refusing the empty string would strand a lapsed member's list in a
    theme they can no longer change."""
    from trophies.themes import GRADIENT_THEMES

    real = next(k for k, v in GRADIENT_THEMES.items() if not v.get('requires_game_image'))
    profile = _hunter(premium=True)
    game_list = svc.create_list(profile, name='Themed')
    svc.update_list(game_list, profile, selected_theme=real)

    profile.user_is_premium = False
    profile.save(update_fields=['user_is_premium'])

    svc.update_list(game_list, profile, selected_theme='')
    game_list.refresh_from_db()
    assert game_list.selected_theme == ''


# -- the gate, scoped ------------------------------------------------------------------------------

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

