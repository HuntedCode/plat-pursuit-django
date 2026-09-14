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
    MAX_ITEMS_PER_LIST,
    MAX_SECTIONS_PER_LIST,
    MEMBER_MAX_LISTS,
    NAME_MAX_LENGTH,
    NOTE_MAX_LENGTH,
    SECTION_NAME_MAX_LENGTH,
    GameList,
    GameListFollow,
    GameListItem,
    GameListLike,
    GameListSection,
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
    """The cap on how many lists, in one place. Everyone gets lists; members get more of them.

    There is deliberately no matching `max_items_for`, and the reason CHANGED on 2026-09-14 while the
    conclusion did not. It used to be that capping size would take a perk back from members; lists
    are now capped at `MAX_ITEMS_PER_LIST` for everybody. What stays true is that the SIZE cap is not
    tiered: it is abuse prevention, and a spam limit somebody can pay to raise is not a spam limit.
    Only the list COUNT is a perk, which is the honest thing to sell -- members get more lists.
    """
    return MEMBER_MAX_LISTS if profile.user_is_premium else FREE_MAX_LISTS


def _refuse_if_not_member(profile):
    """Sections are the membership perk (owner's call, 2026-09-13).

    Everyone creates lists, adds games, ranks them, publishes and shares. Members get to ORGANISE
    them. That is the same shape the tiering already uses -- `max_lists_for` above, and the shipped
    `sync` perk of "everyone syncs, members sync more often" -- rather than a capability a free
    hunter cannot reach at all.

    WHAT THIS DOES NOT GATE, which is the part that is easy to get wrong:

    - READING a sectioned list. Sections are the author's tool; nobody needs a membership to read a
      list that has them, and a free hunter's view is identical to anyone else's.
    - A LAPSED member's existing sections. Membership ending must not delete data or reshuffle a
      list -- it keeps rendering exactly as it did. What they lose is making MORE, which is the same
      line the list-size comment in `models.py` draws about taking a perk back.
    - MOVING a game between sections they already have, DELETING one, or REORDERING them. Those are
      arranging and removing your own content, not creating it -- the same split `update_list` draws
      when it lets a restricted hunter un-publish but not publish.
    """
    if not profile.user_is_premium:
        raise ListError('Sections are a member feature. Your lists and their games are unaffected.')


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


def _check_section_name(raw):
    """A section name is PUBLIC TEXT a hunter wrote, so it takes the same treatment a list name does.

    Easy to miss, because a section feels structural rather than editorial -- but the header renders
    on a public page under the author's byline, which is the whole definition of the `all_ugc` scope.
    Skipping the banned-word check here would have left one user-writable public string in the
    feature outside the filter, which is exactly the hole this service exists to close.
    """
    name = _clean_text(raw, field='section name', max_length=SECTION_NAME_MAX_LENGTH)
    if not name:
        raise ListError('A section needs a name.')
    _refuse_banned_words(name, field='section name')
    return name


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


def _lock_section(section, locked_list):
    """The same treatment `_lock_item` gives an item pivot, for the same reason.

    A section arrives here as the object the VIEW fetched, before the list lock was taken -- so by the
    time the write runs it may already be gone, and `Model.delete()` on an already-deleted row removes
    nothing and does NOT raise. `delete_section` read `position` off that stale object and then shifted
    every section above it, so a double-submit ran the shift twice against the same pivot and left two
    sections sharing a position: `Meta.ordering` goes non-deterministic and `create_section`'s
    `Max(position) + 1` leaves a permanent hole that no user action repairs.

    `rename_section` had the milder version of the same problem. It took no lock at all, so a rename
    racing a delete reached `save(update_fields=['name'])` against zero rows -- which Django 5.2 turns
    into `DatabaseError("Save with update_fields did not affect any rows.")`, i.e. a 500 where the
    client expects the 400 it knows how to display.

    Scoped to the parent as well as the pk, so this doubles as the ownership check the two callers
    would otherwise each have to remember.
    """
    fresh = (
        GameListSection.objects.select_for_update()
        .filter(pk=section.pk, game_list=locked_list)
        .first()
    )
    if fresh is None:
        raise ListError('That section is no longer on this list.')
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
                list_type=None, restart_numbering=None):
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
    # Ungated by membership and by restriction, for the same reason `list_type` is: choosing between
    # two renderings of your own rows submits no content. A lapsed member whose list is already
    # sectioned can still choose how it reads.
    if restart_numbering is not None:
        game_list.sections_restart_numbering = bool(restart_numbering)
        changed.append('sections_restart_numbering')

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
    """Append a game, up to `MAX_ITEMS_PER_LIST`.

    THE ONE ENFORCEMENT POINT for the size cap, so the shell, the admin and the future importer are
    all bound by it rather than only the page. `max_lists_for` above owns the other cap the same way.
    """
    _require_owner(game_list, profile)
    _refuse_if_unlinked(profile)
    _refuse_if_restricted(profile)

    note = _clean_text(note, field='note', max_length=NOTE_MAX_LENGTH)
    _refuse_banned_words(note, field='note')

    locked = _lock_list(game_list)
    if GameListItem.objects.filter(game_list=locked, concept=concept).exists():
        raise ListError('That game is already on this list.')

    # COUNTED UNDER THE LOCK, the same shape `create_list` and `create_section` use and for the same
    # reason: `@transaction.atomic` alone does not stop two requests both counting 199 and both
    # inserting. Counted from the ROWS rather than from `game_count`, which drifts HIGH when a
    # Concept is deleted (CASCADE, no service involved) -- trusting it would lock a hunter out of a
    # list that has room, permanently, with no action that clears it.
    if GameListItem.objects.filter(game_list=locked).count() >= MAX_ITEMS_PER_LIST:
        # THE REMEDY IS CHECKED BEFORE IT IS OFFERED. "or start another list" is good advice to a
        # member with room and a dead end to a free hunter already holding three, who would follow it
        # into a second refusal -- the same defect `DeleteListView`'s docstring records, where a cap
        # message named a way out that did not exist. One extra query, on a refusal path.
        remedy = 'Remove one to make room.'
        if GameList.objects.owned_by(profile).count() < max_lists_for(profile):
            remedy = 'Remove one to make room, or start another list.'
        raise ListError(f'A list holds {MAX_ITEMS_PER_LIST} games. {remedy}')

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
def reorder(game_list, profile, item_ids, *, moved_item_id=None, section_id=None):
    """Set the order to exactly `item_ids`, and optionally re-file one item as part of the same write.

    Refuses a partial list rather than accepting one. A drag-reorder that posts a subset means the
    client and the server disagree about what is on the list, and applying it would silently drop
    the entries the client forgot.

    ONE CALL, BECAUSE A CROSS-SECTION DROP IS ONE ACT. Dragging a card from "Playing" to "Finished"
    changes two things -- where it sits in the global order, and which section it belongs to -- and
    sending them as two requests means either can fail alone. The interesting failure is not the loud
    one: it is the card that lands in the right place under the wrong header, which looks correct
    until the page is reloaded. Doing both inside one transaction removes the state rather than
    detecting it.

    `section_id=None` with a `moved_item_id` means UNGROUPED, which is a real destination -- dragging
    a card out of every section and into the loose bucket is how you un-file one. So "no section" and
    "no move" are distinguished by `moved_item_id`, not by `section_id` being falsy.
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

    # THE ASSIGNMENT FIRST, so that a refusal here leaves the order untouched rather than half
    # applied. Validated against THIS list on both sides: an item id from another list is already
    # excluded by the membership check above, and a section id from another list is refused below --
    # without which a card could be filed under a section the caller may not even be able to see.
    if moved_item_id is not None:
        try:
            moved_item_id = int(moved_item_id)
        except (TypeError, ValueError):
            raise ListError('That move is not valid. Reload and try again.')
        if moved_item_id not in items:
            raise ListError('That game is not on this list.')

        section = None
        if section_id is not None:
            section = GameListSection.objects.filter(
                pk=section_id, game_list=locked).first()
            if section is None:
                raise ListError('That section is not on this list.')
        items[moved_item_id].section = section
        items[moved_item_id].save(update_fields=['section'])

    for position, item_id in enumerate(wanted):
        items[item_id].position = position
    GameListItem.objects.bulk_update(items.values(), ['position'])
    GameList.objects.filter(pk=locked.pk).update(updated_at=timezone.now())


# ── sections ─────────────────────────────────────────────────────────────────────────────────────

@transaction.atomic
def create_section(game_list, profile, *, name):
    """Add a section to the end of the list."""
    _require_owner(game_list, profile)
    _refuse_if_unlinked(profile)
    _refuse_if_restricted(profile)
    _refuse_if_not_member(profile)

    name = _check_section_name(name)
    locked = _lock_list(game_list)

    # Same lock-then-count shape `create_list` uses, and for the same reason: `@transaction.atomic`
    # alone does not stop two requests both counting 19 and both inserting.
    if GameListSection.objects.filter(game_list=locked).count() >= MAX_SECTIONS_PER_LIST:
        raise ListError(
            f'A list can have {MAX_SECTIONS_PER_LIST} sections. '
            'Delete one to make room.'
        )

    highest = GameListSection.objects.filter(game_list=locked).aggregate(
        top=models.Max('position'))['top']
    return GameListSection.objects.create(
        game_list=locked, name=name, position=0 if highest is None else highest + 1)


@transaction.atomic
def rename_section(section, profile, *, name):
    """Rename one. Gated exactly like creating one: it is the same act of writing public text."""
    _require_owner(section.game_list, profile)
    _refuse_if_unlinked(profile)
    _refuse_if_restricted(profile)
    _refuse_if_not_member(profile)

    # Locked like every other section write. This was the one that took no lock at all, so a rename
    # racing a delete saved against zero rows and Django turned that into a 500; it was also the one
    # write that could interleave with `delete_section`'s position shift.
    locked = _lock_list(section.game_list)
    fresh = _lock_section(section, locked)
    fresh.name = _check_section_name(name)
    fresh.save(update_fields=['name'])
    # The CALLER's object too, because the view answers with `section.name` and would otherwise echo
    # the name it arrived with rather than the one that was stored.
    section.name = fresh.name
    return fresh


@transaction.atomic
def delete_section(section, profile):
    """Remove a section and ORPHAN its games rather than deleting them.

    `GameListItem.section` is SET_NULL, so the items fall back to the ungrouped bucket and stay on
    the list. A cascade here would destroy hand-curated entries because of a structural change the
    hunter made about the HEADER -- the same trap `Concept.absorb()`'s list branch documents.

    UNGATED by membership. Removing your own content is the opposite of the act the perk covers, and
    a lapsed member who cannot tidy up is being punished rather than up-sold.
    """
    _require_owner(section.game_list, profile)

    locked = _lock_list(section.game_list)
    # RE-READ UNDER THE LOCK. The pivot cannot come off the object the view fetched -- see
    # `_lock_section` for the double-submit that leaves two sections sharing a position.
    fresh = _lock_section(section, locked)
    position = fresh.position
    fresh.delete()
    # Dense, like everything else that orders here.
    GameListSection.objects.filter(game_list=locked, position__gt=position).update(
        position=models.F('position') - 1)
    GameList.objects.filter(pk=locked.pk).update(updated_at=timezone.now())


@transaction.atomic
def reorder_sections(game_list, profile, section_ids):
    """Set the section order to exactly `section_ids`.

    Refuses a partial ordering, the way `reorder` does for items and for the same reason: a subset
    means the client and the server disagree about what is on the list. Ungated by membership --
    arranging what you already have is not creating it.
    """
    _require_owner(game_list, profile)

    locked = _lock_list(game_list)
    try:
        wanted = [int(i) for i in section_ids]
    except (TypeError, ValueError):
        raise ListError('That order is not valid. Reload and try again.')

    sections = {s.id: s for s in GameListSection.objects.filter(game_list=locked)}
    if sorted(wanted) != sorted(sections):
        raise ListError('That order does not match the list. Reload and try again.')

    for position, section_id in enumerate(wanted):
        sections[section_id].position = position
    GameListSection.objects.bulk_update(sections.values(), ['position'])
    GameList.objects.filter(pk=locked.pk).update(updated_at=timezone.now())


@transaction.atomic
def assign_item(game_list, profile, item, section):
    """Move one game into a section, or out of every section when `section` is None.

    UNGATED by membership, deliberately: this is arranging games you already own between headers you
    already have. A lapsed member can still tidy their list; what they cannot do is make another
    header.

    The section must belong to THIS list. Without that check an item could be filed under another
    hunter's section id -- it would render nowhere and leak the existence of a section on a list the
    caller may not be able to see.
    """
    _require_owner(game_list, profile)

    locked = _lock_list(game_list)
    fresh = _lock_item(item, locked)

    # RE-RESOLVED UNDER THE LOCK, not merely checked for the right parent. The caller resolved this
    # section before the transaction, so it may have been deleted while this request waited on the
    # list lock -- and writing the FK then raises IntegrityError, which reaches a JSON client as a 500
    # HTML page instead of the 400 it knows how to display. `reorder` already did it this way; these
    # two are the same act and must not disagree about how careful it is.
    if section is not None:
        section = _lock_section(section, locked)

    fresh.section = section
    fresh.save(update_fields=['section'])
    GameList.objects.filter(pk=locked.pk).update(updated_at=timezone.now())
    return fresh


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
