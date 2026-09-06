"""The only thing that writes a game list.

The old system had no service at all: creating, renaming, adding, removing, reordering, liking and
copying were each implemented inline in `api/game_list_views.py`, and every rule that should have
been shared was therefore repeated or forgotten. It was forgotten. Lists are the one user-content
system on this site with no restriction gate -- `restriction_service.is_restricted_from` is called
by comments, ratings, flags, roadmap notes and the fundraiser, and by nothing in lists -- because
there was no single place to put the call. This module is that place.

Three rules hold everywhere below, and they are the reason to route through here:

1. **A write refuses before it starts.** The restriction check and the tier cap run before any row
   is touched, and raise rather than returning a flag nobody checks.
2. **Denormalized counts move with the rows they count**, inside the same transaction, via `F()`.
   `game_count` is what the browse grid sorts on, so drift silently reorders the page.
3. **`position` stays dense.** Removal re-compacts. The browse tile bounds its cover prefetch with
   `position__lt=4`, so a gap shows three covers where there should be four -- a bug that looks like
   a rendering problem and is a data problem.
"""
from django.db import models, transaction
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
from trophies.services.comment_service import CommentService
from users.services import restriction_service


class ListError(Exception):
    """A refusal a caller is expected to show the hunter. The message is user-facing."""


#: Offered in the UI as one-tap suggestions, NOT as the only choices. Naming a list is how you tell
#: two of your own apart, so it stays free text for everyone, gated by the same banned-word check and
#: `all_ugc` restriction that already govern every other public string a hunter can write. Paywalling
#: the field would be a second, different answer to a problem the site already answers.
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
    """The cap, in one place. Everyone gets lists; members get more of them."""
    return MEMBER_MAX_LISTS if profile.user_is_premium else FREE_MAX_LISTS


def _refuse_if_restricted(profile):
    """The call the old system had nowhere to put.

    `all_ugc` is the right scope: a list name and a note are user-submitted content shown to other
    people, which is exactly what that scope exists to stop. Restricting somebody hides nothing they
    already published -- their existing lists stay up, they simply cannot write more.
    """
    if restriction_service.is_restricted_from(profile, 'all_ugc'):
        raise ListError('Your account is currently restricted from posting.')


def _clean_text(raw, *, field, max_length):
    """Sanitize and validate one hunter-written string.

    Reuses `CommentService.sanitize_text` rather than growing a second sanitizer: it strips every
    tag (`ALLOWED_TAGS = []`) and un-escapes entities, so what is stored is plain text that is only
    safe in an auto-escaped `{{ }}` context. Never render these with `|safe`.
    """
    cleaned = (CommentService.sanitize_text(raw or '') or '').strip()
    if len(cleaned) > max_length:
        raise ListError(f'That {field} is too long (max {max_length} characters).')
    return cleaned


def _check_name(profile, raw):
    name = _clean_text(raw, field='name', max_length=120)
    if not name:
        raise ListError('A list needs a name.')
    banned, _word = CommentService.check_banned_words(name)
    if banned:
        # The matched word is deliberately not echoed back: it tells somebody probing the filter
        # exactly which term tripped it, which is a list they can then work around.
        raise ListError('That name is not allowed. Please choose another.')
    return name


# ── lists ────────────────────────────────────────────────────────────────────────────────────────

@transaction.atomic
def create_list(profile, *, name, description='', is_public=False):
    _refuse_if_restricted(profile)

    cap = max_lists_for(profile)
    # Counted under the same transaction as the insert. Two tabs submitting at once is a real way to
    # land on cap + 1, and the cap is the one rule a hunter would notice being wrong.
    current = GameList.objects.owned_by(profile).count()
    if current >= cap:
        raise ListError(
            f'You have reached your limit of {cap} lists. '
            'Delete one to make room, or become a member for more.'
        )

    return GameList.objects.create(
        owner=profile,
        name=_check_name(profile, name),
        description=_clean_text(description, field='description', max_length=1000),
        is_public=is_public,
    )


@transaction.atomic
def update_list(game_list, profile, *, name=None, description=None,
                is_public=None, selected_theme=None):
    """Edit a list you own. Every argument is optional; only what is passed is touched."""
    _require_owner(game_list, profile)
    _refuse_if_restricted(profile)

    changed = []
    if name is not None:
        game_list.name = _check_name(profile, name)
        changed.append('name')
    if description is not None:
        game_list.description = _clean_text(description, field='description', max_length=1000)
        changed.append('description')
    if is_public is not None:
        game_list.is_public = bool(is_public)
        changed.append('is_public')
    if selected_theme is not None:
        # Themes are a member perk, and the check is here rather than in the view because the view
        # is not the only caller and a theme set by an expired member should stop applying.
        if selected_theme and not profile.user_is_premium:
            raise ListError('Themes are a member perk.')
        game_list.selected_theme = selected_theme
        changed.append('selected_theme')

    if changed:
        game_list.save(update_fields=[*changed, 'updated_at'])
    return game_list


@transaction.atomic
def delete_list(game_list, profile):
    """Soft delete. The row stays so a support request can undo it, and so a hunter who deletes the
    wrong list has not lost forty games they curated by hand."""
    _require_owner(game_list, profile)

    game_list.is_deleted = True
    game_list.deleted_at = timezone.now()
    game_list.save(update_fields=['is_deleted', 'deleted_at', 'updated_at'])
    return game_list


def _require_owner(game_list, profile):
    """Ownership, asked as a question about the row rather than about the URL.

    Every write below takes an already-fetched list, so this is the single place that decides
    "yours". Deleted lists refuse too: a soft-deleted list is not a list you can still edit.
    """
    if game_list.is_deleted:
        raise ListError('That list no longer exists.')
    if game_list.owner_id != profile.id:
        raise ListError('That is not your list.')


# ── items ────────────────────────────────────────────────────────────────────────────────────────

@transaction.atomic
def add_game(game_list, profile, game, *, note=''):
    _require_owner(game_list, profile)
    _refuse_if_restricted(profile)

    locked = GameList.objects.select_for_update().get(pk=game_list.pk)
    if locked.game_count >= MAX_ITEMS_PER_LIST:
        raise ListError(f'A list holds up to {MAX_ITEMS_PER_LIST} games.')

    if GameListItem.objects.filter(game_list=locked, game=game).exists():
        raise ListError('That game is already on this list.')

    item = GameListItem.objects.create(
        game_list=locked,
        game=game,
        note=_clean_text(note, field='note', max_length=500),
        # Appended at the end, computed under the row lock taken above so two adds cannot claim the
        # same position and break the dense-ordering contract.
        position=locked.game_count,
    )
    GameList.objects.filter(pk=locked.pk).update(
        game_count=models.F('game_count') + 1, updated_at=timezone.now())
    return item


@transaction.atomic
def remove_game(game_list, profile, item):
    """Remove one entry and CLOSE THE GAP.

    The re-compaction is the whole reason this is a service function rather than `item.delete()`.
    Positions are consumed as dense by the cover prefetch (`position__lt=4`), so leaving a hole
    shows a three-cover mosaic on a list with four games and reads as a rendering bug.
    """
    _require_owner(game_list, profile)

    locked = GameList.objects.select_for_update().get(pk=game_list.pk)
    if item.game_list_id != locked.pk:
        raise ListError('That entry is not on this list.')

    removed_position = item.position
    item.delete()
    GameListItem.objects.filter(game_list=locked, position__gt=removed_position).update(
        position=models.F('position') - 1)
    GameList.objects.filter(pk=locked.pk).update(
        game_count=models.F('game_count') - 1, updated_at=timezone.now())


@transaction.atomic
def reorder(game_list, profile, item_ids):
    """Set the order to exactly `item_ids`.

    Refuses a partial list rather than accepting one. A drag-reorder that posts a subset means the
    client and the server disagree about what is on the list, and applying it would silently drop
    the entries the client forgot -- so the mismatch is an error, not something to paper over.
    """
    _require_owner(game_list, profile)

    locked = GameList.objects.select_for_update().get(pk=game_list.pk)
    existing = list(GameListItem.objects.filter(game_list=locked).values_list('id', flat=True))
    if sorted(item_ids) != sorted(existing):
        raise ListError('That order does not match the list. Reload and try again.')

    items = {i.id: i for i in GameListItem.objects.filter(game_list=locked)}
    for position, item_id in enumerate(item_ids):
        items[item_id].position = position
    GameListItem.objects.bulk_update(items.values(), ['position'])
    GameList.objects.filter(pk=locked.pk).update(updated_at=timezone.now())


# ── social ───────────────────────────────────────────────────────────────────────────────────────

@transaction.atomic
def set_like(game_list, profile, *, liked):
    """Like or unlike. Idempotent in both directions.

    Returns the new count, read back from the row rather than computed, so the caller renders what
    the database holds instead of a number it guessed.
    """
    return _set_social(GameListLike, game_list, profile, on=liked, field='like_count')


@transaction.atomic
def set_follow(game_list, profile, *, following):
    return _set_social(GameListFollow, game_list, profile, on=following, field='follower_count')


def _set_social(model, game_list, profile, *, on, field):
    """One implementation for like and follow, because they are the same operation twice.

    Both refuse on a list the hunter cannot see -- otherwise liking is an oracle that tells you a
    private list exists, and by whom, from its id alone.
    """
    if not GameList.objects.readable_by(profile).filter(pk=game_list.pk).exists():
        raise ListError('That list is not available.')
    if game_list.owner_id == profile.id and model is GameListFollow:
        raise ListError('You already own that list.')

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
