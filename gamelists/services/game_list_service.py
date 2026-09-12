"""The only thing that writes a game list.

The old system had no service at all: creating, renaming, adding, removing, reordering, liking and
copying were each implemented inline in `api/game_list_views.py`, and every rule that should have
been shared was therefore repeated or forgotten. It was forgotten. Lists are the one user-content
system on this site with no restriction gate -- `restriction_service.is_restricted_from` is called
by comments, ratings, flags, roadmap notes and the fundraiser, and by nothing in lists -- because
there was no single place to put the call. This module is that place.

Four rules hold everywhere below.

1. **A write refuses before it starts**, and refuses field-by-field where the site already draws
   that line. A restriction stops new WORDS; it does not stop somebody taking their own list down
   or un-publishing it. (`api/rating_views.py` sets this precedent: a restriction on quick takes
   drops the prose and lets the SCORES through, because silently discarding those would rewrite a
   game's averages as a side effect of a decision about somebody's writing.)
2. **The lock comes before the value it protects, and the precondition is re-asserted on the row
   that came back.** Both halves, the way `moderation_service._lock_report` does it. The first cut
   took the lock and then read the pivot off the caller's stale object, which is a silent
   corruption rather than an error.
3. **Denormalized counts move with the rows they count**, inside the same transaction.
   `game_count` is what the browse grid sorts on, so drift silently reorders the page.
4. **`position` stays dense, and is derived from the ROWS rather than from the counter.** The browse
   tile bounds its cover prefetch with `position__lt=4`, so a gap shows three covers on a four-game
   list -- a data bug wearing a rendering bug's clothes. Deriving it from `game_count` made the
   counter load-bearing for correctness and not just for display, which is a much worse trade: a
   merge or an admin delete could then hand a new item a position another row already holds.
"""
from django.db import models, transaction
from django.utils import timezone

from gamelists.models import (
    DESCRIPTION_MAX_LENGTH,
    FREE_MAX_LISTS,
    LIST_TYPE_COLLECTION,
    LIST_TYPES,
    MEMBER_MAX_LISTS,
    NAME_MAX_LENGTH,
    NOTE_MAX_LENGTH,
    GameList,
    GameListFollow,
    GameListItem,
    GameListLike,
)
from trophies.models import Profile
from trophies.services.comment_service import CommentService
from users.services import restriction_service


class ListError(Exception):
    """A refusal a caller is expected to show the hunter. The message is user-facing."""


#: Offered in the UI as one-tap suggestions, NOT as the only choices. Naming a list is how you tell
#: two of your own apart, so it stays free text for everyone, gated by the same banned-word check and
#: `all_ugc` restriction that already govern every other public string a hunter can write.
SUGGESTED_NAMES = (
    'The Backlog',
    'Currently Playing',
    'Platinum Path',
    'Trophy Vault',
    'Hidden Gems',
    'Comfort Games',
    'Co-op Night',
    'One More Try',
)


def max_lists_for(profile):
    """The cap, in one place. Everyone gets lists; members get more of them.

    There is deliberately no matching `max_items_for`. The system this replaces gave members
    unlimited games per list, so capping list SIZE would take a perk back rather than add one --
    and the importer would then be unable to bring a member's own data across. Only the list COUNT
    is tiered.
    """
    return MEMBER_MAX_LISTS if profile.user_is_premium else FREE_MAX_LISTS


# ── gates ────────────────────────────────────────────────────────────────────────────────────────

def _refuse_if_restricted(profile):
    """The call the old system had nowhere to put.

    `all_ugc` is the right scope: a list name, a description and a note are user-submitted content
    shown to other people. Restricting somebody hides nothing they already published -- their lists
    stay up, they simply cannot write more.
    """
    if restriction_service.is_restricted_from(profile, 'all_ugc'):
        raise ListError('Your account is currently restricted from posting.')


def _refuse_if_unlinked(profile):
    """The same bar every other UGC surface sets.

    `CommentService.can_interact` bundles this with the restriction check and argues in its own
    docstring for bundling, precisely so the next writer cannot forget one; the inline views this
    replaces required a linked profile too. Dropping it in the rewrite would have made lists the one
    surface an unlinked account can publish from.
    """
    if profile is None or not profile.is_linked:
        raise ListError('Link your PSN account to build lists.')


# ── text ─────────────────────────────────────────────────────────────────────────────────────────

def _clean_text(raw, *, field, max_length):
    """Sanitize and validate one hunter-written string.

    Runs `CommentService.sanitize_text` TO A FIXPOINT, which is the part that matters. That function
    bleaches and then `html.unescape()`s its own output, so it is NOT idempotent: `&lt;script&gt;`
    survives the bleach (it is text, not a tag) and the unescape turns it back into a live
    `<script>`. Verified by running it, not assumed. A single pass therefore stores raw markup for
    entity-encoded input, which is safe only under Django auto-escaping -- and list names are headed
    for `og:title` and the Playwright share cards, neither of which is a `{{ }}` context.

    The final check refuses rather than storing a partly-cleaned string, so the guarantee this
    function offers is one a caller can actually rely on.
    """
    text = raw or ''
    for _pass in range(3):
        cleaned = (CommentService.sanitize_text(text) or '').strip()
        if cleaned == text:
            break
        text = cleaned

    if '<' in text or '>' in text:
        raise ListError(f'That {field} contains characters that are not allowed.')
    if len(text) > max_length:
        raise ListError(f'That {field} is too long (max {max_length} characters).')
    return text


def _refuse_banned_words(text, *, field):
    """Every hunter-written string, not just the name.

    The first cut checked only `name`, leaving `description` -- 1000 public characters, the largest
    free-text field the feature ships -- and `note` outside the filter entirely.
    """
    if not text:
        return
    banned, _word = CommentService.check_banned_words(text)
    if banned:
        # The matched word is deliberately not echoed back: it tells somebody probing the filter
        # exactly which term tripped it, which is a list they can then work around.
        raise ListError(f'That {field} is not allowed. Please choose another.')


def _check_name(raw):
    name = _clean_text(raw, field='name', max_length=NAME_MAX_LENGTH)
    if not name:
        raise ListError('A list needs a name.')
    _refuse_banned_words(name, field='name')
    return name


def _check_description(raw):
    text = _clean_text(raw, field='description', max_length=DESCRIPTION_MAX_LENGTH)
    _refuse_banned_words(text, field='description')
    return text


def _check_list_type(raw):
    """Validate against the types that actually RENDER.

    Django's `choices` is a form/admin concern and is not enforced at the database level, so an API
    that passed the value straight through would happily store `list_type='tier'` -- and the detail
    page, which branches on the two it knows, would draw a Collection while the hunter believed they
    had made something else. Checking here keeps the column honest for the shell and the importer
    too, since both go through this service.
    """
    value = (raw or '').strip()
    if value not in LIST_TYPES:
        raise ListError('That is not a list type.')
    return value


def _lock_list(game_list):
    """Re-read the list FOR UPDATE and re-assert the precondition on the row that came back.

    Both halves, the way `moderation_service._lock_report` does it. Checking `is_deleted` on the
    caller's instance and then locking leaves a window where a list deleted in another tab still
    accepts item writes.
    """
    locked = GameList.objects.select_for_update().get(pk=game_list.pk)
    if locked.is_deleted:
        raise ListError('That list no longer exists.')
    return locked


def _lock_item(item, locked_list):
    """The pivot, re-read under the parent's lock and scoped to the parent.

    `remove_concept` used to read `item.position` off the caller's in-memory object. A double-submit
    then re-ran the shift with a stale pivot -- `Model.delete()` on an already-deleted row removes
    nothing and does NOT raise -- leaving two rows sharing a position and corrupting exactly the
    dense ordering this module calls load-bearing.
    """
    fresh = (
        GameListItem.objects.select_for_update()
        .filter(pk=item.pk, game_list=locked_list)
        .first()
    )
    if fresh is None:
        raise ListError('That entry is no longer on this list.')
    return fresh


def _require_owner(game_list, profile):
    """Ownership, asked about the row rather than about the URL."""
    if game_list.is_deleted:
        raise ListError('That list no longer exists.')
    if game_list.owner_id != profile.id:
        raise ListError('That is not your list.')


# ── lists ────────────────────────────────────────────────────────────────────────────────────────

@transaction.atomic
def create_list(profile, *, name, description='', is_public=False,
                list_type=LIST_TYPE_COLLECTION):
    _refuse_if_unlinked(profile)
    _refuse_if_restricted(profile)

    name = _check_name(name)
    description = _check_description(description)
    list_type = _check_list_type(list_type)

    # THE LOCK GOES ON THE PROFILE, not on the lists. `@transaction.atomic` does nothing for this by
    # itself: at READ COMMITTED two requests both COUNT 2, both pass `2 >= 3`, both insert, and the
    # hunter ends up with four. `SELECT ... FOR UPDATE` locks rows that EXIST, so locking a filtered
    # list queryset locks nothing in the case that matters -- the account always exists. Same
    # reasoning `restriction_service.apply_restriction` writes down for the same shape of check.
    Profile.objects.select_for_update().filter(pk=profile.pk).first()

    cap = max_lists_for(profile)
    if GameList.objects.owned_by(profile).count() >= cap:
        raise ListError(
            f'You have reached your limit of {cap} lists. '
            'Delete one to make room, or become a member for more.'
        )

    return GameList.objects.create(
        owner=profile, name=name, description=description, is_public=bool(is_public),
        list_type=list_type)


@transaction.atomic
def update_list(game_list, profile, *, name=None, description=None, is_public=None,
                list_type=None):
    """Edit a list you own. Every argument is optional; only what is passed is touched.

    The restriction gate is scoped to the acts that PUT WORDS IN FRONT OF PEOPLE, in either of the
    two ways that can happen: writing them, or making already-written ones visible. A restricted
    hunter can still un-publish their own list, still delete it, and still switch its TYPE -- taking
    your own content down is the opposite of the act being restricted, and choosing between a
    Collection and a Ranked presentation submits no content. (This clause used to name the gradient
    theme, which was deleted in 2026-09; `list_type` inherits the reasoning, not just the slot.)
    Gating the whole function trapped a restricted hunter's list in public, which is the same failure
    `api/rating_views.py` documents from the other direction.

    PUBLISHING IS GATED and the first version did not gate it, because the condition only looked at
    `name`/`description` and did not care which way `is_public` moved. A POST carrying nothing but
    `is_public=true` reached the write with no restriction check at all -- so the bypass was: write
    lists privately, get restricted for something else, then publish the lot. The moderator's only
    remaining lever was deletion. Confirmed end to end before fixing: rename answered 400 while
    publish answered 200 and flipped the row.

    `is_public is False` still passes, deliberately. Un-publishing is a hunter taking their OWN
    content down, which restriction exists to encourage rather than prevent.
    """
    _require_owner(game_list, profile)

    # `bool(is_public)` and not `is not None`: None means "not passed" and False means "take it
    # down", and only the third case -- making it public -- is the one restriction speaks to.
    publishing = is_public is not None and bool(is_public)
    if name is not None or description is not None or publishing:
        _refuse_if_restricted(profile)

    changed = []
    if name is not None:
        game_list.name = _check_name(name)
        changed.append('name')
    if description is not None:
        game_list.description = _check_description(description)
        changed.append('description')
    if is_public is not None:
        game_list.is_public = bool(is_public)
        changed.append('is_public')
    if list_type is not None:
        game_list.list_type = _check_list_type(list_type)
        changed.append('list_type')

    if changed:
        game_list.save(update_fields=[*changed, 'updated_at'])
    return game_list


@transaction.atomic
def delete_list(game_list, profile):
    """Soft delete, and idempotent.

    The row stays so a support request can undo it, and so somebody who deletes the wrong list has
    not lost forty games they curated by hand. Deleting twice is a no-op rather than an error: a
    second click should not answer "that list no longer exists" about a list you just removed.
    """
    if game_list.owner_id != profile.id:
        raise ListError('That is not your list.')
    if game_list.is_deleted:
        return game_list

    game_list.is_deleted = True
    game_list.deleted_at = timezone.now()
    game_list.save(update_fields=['is_deleted', 'deleted_at', 'updated_at'])
    return game_list


# ── items ────────────────────────────────────────────────────────────────────────────────────────

@transaction.atomic
def add_concept(game_list, profile, concept, *, note=''):
    """Append a game. No size cap: members always had unlimited, and free hunters keep it too."""
    _require_owner(game_list, profile)
    _refuse_if_unlinked(profile)
    _refuse_if_restricted(profile)

    note = _clean_text(note, field='note', max_length=NOTE_MAX_LENGTH)
    _refuse_banned_words(note, field='note')

    locked = _lock_list(game_list)
    if GameListItem.objects.filter(game_list=locked, concept=concept).exists():
        raise ListError('That game is already on this list.')

    highest = GameListItem.objects.filter(game_list=locked).aggregate(
        top=models.Max('position'))['top']
    item = GameListItem.objects.create(
        game_list=locked, concept=concept, note=note,
        position=0 if highest is None else highest + 1)

    _recount(locked)
    return item


@transaction.atomic
def remove_concept(game_list, profile, item):
    """Remove one entry and CLOSE THE GAP.

    The re-compaction is why this is a service function rather than `item.delete()`. Positions are
    consumed as dense by the cover prefetch (`position__lt=4`), so a hole shows a three-cover mosaic
    on a list with four games and reads as a rendering bug.
    """
    _require_owner(game_list, profile)

    locked = _lock_list(game_list)
    fresh = _lock_item(item, locked)

    removed_position = fresh.position
    fresh.delete()
    GameListItem.objects.filter(game_list=locked, position__gt=removed_position).update(
        position=models.F('position') - 1)
    _recount(locked)


def _recount(locked):
    """Set `game_count` from the rows, rather than nudging it by one.

    Recomputed rather than nudged, so an existing error is corrected instead of carried forward.
    `PositiveIntegerField` is a DB CHECK on Postgres, so a counter that drifted high would eventually
    raise IntegrityError out of a `-1` instead of the ListError a caller is catching. Cheap: one
    COUNT under a lock we already hold.

    NOT self-healing, which this docstring used to claim. It named "an admin deleting a Concept" as a
    drift source it recovered from; it does not, because it only ever runs from inside `add_concept`
    and `remove_concept`. `GameListItem.concept` is CASCADE, so deleting a Concept removes rows with
    no service involvement, and an untouched list then carries BOTH a stale `game_count` (which the
    browse grid sorts on) and a GAP in `position` -- which `attach_cover_games` reads through
    `position__lt=4`, so the tile quietly composes a three-cover mosaic for a four-game list.
    `Concept.absorb()` repairs both, but that is the MERGE path; nothing repairs a plain delete.

    Closing that needs a `post_delete` receiver or a reconciliation command. It is recorded in
    docs/features/game-lists.md (Gotchas) rather than fixed here because it wants to be decided
    alongside the same question for the other denormalized counters. (This pointer named
    game-list-types.md, which says nothing about it.)
    """
    GameList.objects.filter(pk=locked.pk).update(
        game_count=GameListItem.objects.filter(game_list=locked).count(),
        updated_at=timezone.now())


@transaction.atomic
def reorder(game_list, profile, item_ids):
    """Set the order to exactly `item_ids`.

    Refuses a partial list rather than accepting one. A drag-reorder that posts a subset means the
    client and the server disagree about what is on the list, and applying it would silently drop
    the entries the client forgot.
    """
    _require_owner(game_list, profile)

    locked = _lock_list(game_list)
    try:
        wanted = [int(i) for i in item_ids]
    except (TypeError, ValueError):
        raise ListError('That order is not valid. Reload and try again.')

    items = {i.id: i for i in GameListItem.objects.filter(game_list=locked)}
    if sorted(wanted) != sorted(items):
        raise ListError('That order does not match the list. Reload and try again.')

    for position, item_id in enumerate(wanted):
        items[item_id].position = position
    GameListItem.objects.bulk_update(items.values(), ['position'])
    GameList.objects.filter(pk=locked.pk).update(updated_at=timezone.now())


# ── social ───────────────────────────────────────────────────────────────────────────────────────

@transaction.atomic
def set_like(game_list, profile, *, liked):
    """Like or unlike. Idempotent in both directions.

    GATED on the way in. `GameListLike` is deliberately the same shape as the four vote models in
    `trophies`, and `CommentService.toggle_vote` already refuses a vote from a restricted hunter --
    so leaving this open would reopen the exact hole this service exists to close, on the one list
    write that feeds a public ranking. Un-liking stays available: withdrawing a signal is not
    publishing one.
    """
    if liked:
        _refuse_if_restricted(profile)
    return _set_social(GameListLike, game_list, profile, on=liked, field='like_count')


@transaction.atomic
def set_follow(game_list, profile, *, following):
    """Follow or unfollow. Ungated: a follow is private and has no public effect.

    NOTE FOR CALLERS: there is no notification surface behind this YET -- the notifications system is
    itself withdrawn -- so today a follow only surfaces under My Lists > Following. The button is
    still labelled "Follow" on purpose, named for where this is going: a social verb is a word people
    learn, and renaming one after they have learned it costs more than the gap. What the UI must not
    do meanwhile is claim an alert that cannot yet arrive; when the notification surface lands there
    should be a feature to add here and nothing to walk back.
    """
    return _set_social(GameListFollow, game_list, profile, on=following, field='follower_count')


def _set_social(model, game_list, profile, *, on, field):
    """One implementation for like and follow, because they are the same operation twice.

    Both refuse a list the hunter cannot see -- otherwise liking is an oracle that tells you a
    private list exists, and whose, from its id alone.
    """
    _refuse_if_unlinked(profile)
    if not GameList.objects.readable_by(profile).filter(pk=game_list.pk).exists():
        raise ListError('That list is not available.')
    if game_list.owner_id == profile.id:
        # Neither is meaningful on your own list, and self-liking would let an author push their own
        # list up the ranking that `like_count` drives.
        raise ListError('That is your own list.')

    if on:
        _row, created = model.objects.get_or_create(game_list=game_list, profile=profile)
        delta = 1 if created else 0
    else:
        deleted, _ = model.objects.filter(game_list=game_list, profile=profile).delete()
        delta = -1 if deleted else 0

    if delta:
        # Not `save()` on a stale instance: the counter is contended by definition, and a read-
        # modify-write here loses likes under concurrency. F() pushes the arithmetic to the DB.
        GameList.objects.filter(pk=game_list.pk).update(**{field: models.F(field) + delta})

    return GameList.objects.filter(pk=game_list.pk).values_list(field, flat=True).first()
