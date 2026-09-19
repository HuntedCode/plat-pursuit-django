"""The only thing that writes a response.

A response is one hunter's arrangement of somebody else's prompt: which of its games sit in which of
its buckets. It is the thing most people will actually make, and it is the half with other people's
content on the other side of every write.

FIVE RULES THIS MODULE OWNS.

1. **`single_slot` comes from `Prompt.is_single_slot`, here and nowhere else.** The partial unique
   `unique(response, bucket) WHERE single_slot` has a per-ROW predicate, so a placement written with
   the wrong flag is not merely unconstrained -- it is invisible to the index and will not collide
   with the correctly-flagged row beside it. One wrong row makes a poll accept two votes. This module
   is the only writer of `PromptPlacement`, and it never takes the flag from a caller.

2. **The response is created LAZILY, on the first placement.** Not when the editor opens: a
   `get_or_create` on a GET is a write on a GET, which a prefetch, a crawler or a double render all
   trigger -- and it would fill `response_count`, which browse sorts on, with rows nobody authored.
   An absent response and an empty one render identically, because the tray is `pool - placements`
   and that is empty either way.

3. **Both sides of a placement are resolved WITHIN the prompt.** Nothing in the schema ties a
   placement's three foreign keys to one prompt (see `PromptPlacement`'s docstring), so a bucket id
   from somebody else's prompt would satisfy every constraint and produce a row that is invisible to
   the editor while still being counted. `prompt_service.get_game` / `get_bucket` are the only
   supported lookups and they take the prompt.

4. **A single-slot bucket EVICTS rather than refuses.** Dropping a game into an occupied grid slot
   returns the current occupant to the tray; on a poll that same rule is "change your vote", which is
   what a hunter expects a second tap to do. One rule covers both shapes, and neither needs the
   editor to ask first.

5. **`placement_count` is the numerator only.** Completeness is derived at render against a
   denominator that belongs to the author and MOVES -- that is exactly how `UserChecklistProgress`
   came to report over 100%. Recomputed from rows here, never nudged.

WHAT IS GATED, and the reasoning, because a response carries no words at all and that makes it
tempting to skip: placing IS posting. It publishes a public artifact attributed to a profile, and the
default is public. The house precedent is `game_list_service.set_like`, which gates LIKING -- a
wordless public signal -- for the same reason. Taking your own answer down is never gated.
"""
from django.db import models, transaction
from django.utils import timezone

from prompts.models import (
    MAX_PLACEMENTS_PER_RESPONSE,
    SHAPE_GRID,
    Prompt,
    PromptPlacement,
    PromptResponse,
)
from trophies.models import Concept
from prompts.services.prompt_service import (
    PromptError,
    refuse_if_restricted,
    refuse_if_unlinked,
    get_bucket,
    get_game,
)


# ── reading ──────────────────────────────────────────────────────────────────────────────────────

def response_for(prompt, profile):
    """The hunter's answer, or None. NEVER creates one -- see rule 2.

    `None` and an empty response are the same thing to a template, which is what makes the lazy
    creation free rather than a branch everybody has to remember.
    """
    if profile is None:
        return None
    return PromptResponse.objects.filter(prompt=prompt, profile=profile).first()


def listed_responses(prompt):
    """The answers a reader may see: public, and actually answered.

    NOT a completeness rule. "Three of nine slots" is a legitimate answer on a grid, a poll is complete
    at one, and two of fifty on a tier list is genuinely unfinished -- so a threshold would be a
    product rule that changes, encoded where it cannot. One placement is the floor; the sort does the
    rest.
    """
    return (PromptResponse.objects
            .filter(prompt=prompt, is_public=True, placement_count__gt=0))


# ── gates ────────────────────────────────────────────────────────────────────────────────────────

def _refuse_if_unreadable(prompt, profile):
    """You can only answer something you can see.

    Asked of the ROW rather than of the URL, and it is the same `readable_by` the detail view uses --
    so a private prompt cannot be answered by id, which would otherwise be a write-shaped oracle
    telling a stranger the prompt exists.
    """
    if prompt.is_deleted:
        raise PromptError('That no longer exists.')
    if not prompt.is_public and prompt.owner_id != profile.id:
        raise PromptError('That no longer exists.')


def _refuse_if_closed_to_new_answers(prompt, response):
    """Closed stops NEW answers and nothing else.

    Somebody who already answered keeps editing theirs: closing is the author saying "no more of
    these", not "freeze the ones I have". Freezing them would make closing a punishment aimed at
    people who did nothing, and it would strand a half-finished answer forever.
    """
    if prompt.is_closed and response is None:
        raise PromptError('This is closed to new answers.')


def _lock_response(response):
    """Re-read FOR UPDATE, so `placement_count` and the rows it counts move together.

    Two drags landing at once otherwise both read the same count, both write, and the counter is one
    short of the rows -- which is the number the response browse sorts on.
    """
    locked = PromptResponse.objects.select_for_update().filter(pk=response.pk).first()
    if locked is None:
        raise PromptError('Your answer is no longer there.')
    return locked


# ── placing ──────────────────────────────────────────────────────────────────────────────────────

def _as_pk(value, *, what):
    """Coerce an incoming id, or refuse it.

    Every id here arrives from a POST body. Without this, `bucket_id=''` or `game_id='abc'` reaches
    the ORM and raises `ValueError: Field 'id' expected a number` -- which sails past every
    `except PromptError` and reaches the client as a 500 where a 400 was designed. The authoring
    service coerces in three places for exactly this reason; this module faces the more hostile input
    and had none.
    """
    try:
        return int(value)
    except (TypeError, ValueError):
        raise PromptError(f'That is not a {what}.')


def _resolve_card(prompt, *, game_id, concept_pk, allow_free_pick=True):
    """Turn whichever id the caller sent into the thing a placement points at.

    `concept_pk`, NOT `concept_id`, and the rename is not pedantry: `Concept.concept_id` is a
    CharField on this site and is the identifier every game URL routes on, so a view author wiring a
    picker off a game page would hand in `'PP_4821'` and get a `ValueError` 500. The parameter wants
    the primary key and now says so.

    TWO IDENTITIES, ONE PLACEMENT, and which one is decided by the SHAPE. A tier list's or a poll's
    card is a `PromptGame` -- the integrity target that makes an author's removal cascade correctly.
    A GRID has no pool at all: the author sets the questions and every respondent searches the whole
    catalogue, so their card is a `Concept` and there is nothing for it to be integral to.

    THE FREE PICK IS ALLOWED ON A GRID, full stop. A grid has no author pool (owner's call,
    2026-09-19), so there is no "limited to those games" case left; this docstring said there was
    while the code twelve lines down said otherwise.

    `allow_free_pick=False` SKIPS THAT LEGALITY CHECK, and exists for withdrawal. See `unplace`.
    """
    if (game_id is None) == (concept_pk is None):
        raise PromptError('Pick one game.')

    if game_id is not None:
        game = get_game(prompt, _as_pk(game_id, what='game'))
        if game is None:
            raise PromptError('That game is not in this one.')
        return {'prompt_game': game, 'concept': None}

    # SHAPE ALONE DECIDES, since a grid has no pool to be restricted by (owner's call, 2026-09-19).
    # This used to ask `_has_pool` as well, and that second question was the expensive one: it made a
    # free pick legal or illegal depending on a row count that an author could change underneath a
    # respondent mid-answer. A grid is open; a tier list and a poll are not.
    if allow_free_pick and prompt.shape != SHAPE_GRID:
        raise PromptError('This one has its own set of games to choose from.')
    concept = Concept.objects.filter(pk=_as_pk(concept_pk, what='game')).first()
    if concept is None:
        raise PromptError('That game could not be found.')
    return {'prompt_game': None, 'concept': concept}


@transaction.atomic
def place(prompt, profile, *, bucket_id, game_id=None, concept_pk=None):
    """Put one game in one bucket, creating the hunter's response if this is their first.

    EXACTLY ONE OF `game_id` / `concept_id`: a pool row for a pooled prompt, a free-picked Concept for
    an open grid. See `_resolve_card`.

    MOVING IS THE SAME ACT AS PLACING, on a prompt that forbids duplicates: a card already placed
    elsewhere is moved rather than refused, because that is what dragging from one row to another IS,
    and unplace-then-place would leave a window where a half-failed drag dropped it out entirely.

    WHERE DUPLICATES ARE ALLOWED, THAT STOPS BEING TRUE, and it is the one behavioural fork in this
    function. On such a grid the same game in two slots is the point, so placing it into a second slot
    ADDS a card rather than moving the first -- and "already here" has to be asked per bucket instead
    of per response, or the second drop would silently empty the first slot.
    """
    refuse_if_unlinked(profile)
    refuse_if_restricted(profile)
    _refuse_if_unreadable(prompt, profile)

    # Resolved through the prompt, never by bare id -- rule 3.
    ident = _resolve_card(prompt, game_id=game_id, concept_pk=concept_pk)
    bucket = get_bucket(prompt, _as_pk(bucket_id, what='row'))
    if bucket is None:
        raise PromptError('That row is not part of this one.')

    response = response_for(prompt, profile)
    _refuse_if_closed_to_new_answers(prompt, response)

    if response is None:
        # THE PROMPT ROW IS LOCKED BEFORE AN ANSWER IS BORN, and this is the other half of a guarantee
        # that lives in the authoring service. `delete_prompt` refuses to remove a prompt somebody has
        # answered -- but that check and its write are only serialized against THIS if both sides take
        # the same lock. Without it: the author deletes a prompt with zero answers, the first response
        # commits a millisecond later, and it ends up hanging off a row that `visible()` filters out of
        # every read. A lock only one side takes serializes nothing.
        #
        # Re-asserted from the LOCKED row rather than the caller's copy, so a prompt deleted or closed
        # between the page render and this POST is caught here rather than answered.
        locked_prompt = Prompt.objects.select_for_update().filter(pk=prompt.pk).first()
        if locked_prompt is None or locked_prompt.is_deleted:
            raise PromptError('That no longer exists.')
        # `is_public` TOO, and it was missing. `_refuse_if_unreadable` above reads the caller's copy,
        # so a prompt unpublished between the page render and this POST still accepted a stranger's
        # answer -- and that answer then made the prompt permanently undeletable, because
        # `delete_prompt` refuses once anybody else has answered. The author would have had no way
        # out of a draft they had already withdrawn.
        if not locked_prompt.is_public and locked_prompt.owner_id != profile.id:
            raise PromptError('That no longer exists.')
        if locked_prompt.is_closed:
            raise PromptError('This is closed to new answers.')

        # LAZY, and this is the only place a response is born. `get_or_create` rather than `create`
        # because two fast taps on an untouched prompt both see None, and `unique(prompt, profile)`
        # would turn the loser into a 500 rather than the second placement it meant to be.
        response, _created = PromptResponse.objects.get_or_create(prompt=prompt, profile=profile)

    locked = _lock_response(response)
    # WHAT COUNTS AS "ALREADY HERE" DEPENDS ON THE DUPLICATES RULE. Per response when they are
    # forbidden (so a second drop MOVES the card); per bucket when they are allowed (so a second drop
    # in a different slot ADDS one, while a second drop in the SAME slot is still a no-op).
    scope = dict(response=locked, **ident)
    if not prompt.forbids_duplicates:
        scope['bucket'] = bucket
    existing = PromptPlacement.objects.filter(**scope).first()

    if existing is None:
        # The cap only binds a NEW placement; moving a card that is already here cannot exceed it.
        if PromptPlacement.objects.filter(response=locked).count() >= MAX_PLACEMENTS_PER_RESPONSE:
            raise PromptError(f'An answer holds {MAX_PLACEMENTS_PER_RESPONSE} games.')

    # EVICTION, rule 4. On a grid the occupant goes back to the tray; on a poll this is what "change
    # your vote" means. Done before the write rather than after, so the partial unique never has two
    # rows to choose between -- and scoped to this response, so it is only ever the hunter's own card
    # that moves. The `exclude` spares the card being placed: without it, re-placing a card where it
    # already is deletes the row and then saves the stale object over it.
    if prompt.is_single_slot:
        PromptPlacement.objects.filter(response=locked, bucket=bucket).exclude(
            pk=existing.pk if existing else 0).delete()

    if existing is not None:
        if existing.bucket_id == bucket.pk:
            # ALREADY EXACTLY WHERE IT IS GOING. Re-running the append would hand it
            # `Max(position) + 1` over a bucket that still contains it, so a hunter tapping the same
            # slot repeatedly walks the row's position upward forever -- and on a tier row it leaves a
            # hole at the front while demoting the card to last, which is the opposite of what a drag
            # that changed nothing should do.
            placement = existing
        else:
            existing.bucket = bucket
            existing.position = _next_position(locked, bucket)
            existing.save(update_fields=['bucket', 'position'])
            placement = existing
    else:
        placement = PromptPlacement.objects.create(
            response=locked,
            bucket=bucket,
            # THE FLAGS, from the prompt and never from a caller. See rule 1. `no_duplicates` is the
            # second axis and is just as load-bearing: its partial uniques have per-ROW predicates too.
            single_slot=prompt.is_single_slot,
            no_duplicates=prompt.forbids_duplicates,
            position=_next_position(locked, bucket),
            **ident,
        )

    _recount(locked, prompt)
    return placement


@transaction.atomic
def unplace(prompt, profile, *, game_id=None, concept_pk=None, bucket_id=None):
    """Take one card back to the tray. Ungated: withdrawing your own content never is.

    `bucket_id` NARROWS IT TO ONE SLOT, and it only means anything where duplicates are allowed: a
    grid may hold the same game in three slots, and tapping remove on one of them must not empty the
    other two. Left out, every copy of that card comes back, which is the honest reading of "take this
    game out of my answer".
    """
    refuse_if_unlinked(profile)

    # `allow_free_pick=False`, because TAKING YOUR OWN CARD BACK IS NEVER REFUSED and this routed
    # through the check that decides whether a free pick may be MADE. An author adding one game to a
    # published open grid used to flip that answer, and every hunter who had already free-picked was
    # then unable to remove their own card -- a structural refusal doing what no gate is allowed to
    # do, with `clear()` (which destroys the whole answer) as the only escape.
    #
    # THAT STATE IS NOW UNREACHABLE: a grid has no pool, so nothing can flip mid-answer. The argument
    # stays because the principle is not about grids -- a removal must never consult a rule about what
    # may be ADDED, whatever makes that rule change.
    ident = _resolve_card(prompt, game_id=game_id, concept_pk=concept_pk, allow_free_pick=False)

    response = response_for(prompt, profile)
    if response is None:
        return None

    locked = _lock_response(response)
    scope = dict(response=locked, **ident)
    if bucket_id is not None:
        bucket = get_bucket(prompt, _as_pk(bucket_id, what='row'))
        if bucket is None:
            raise PromptError('That row is not part of this one.')
        scope['bucket'] = bucket
    if not PromptPlacement.objects.filter(**scope).exists():
        return None

    # EVERY bucket a copy leaves, not just the first one's. The density contract is per
    # `(response, bucket)`, so each bucket that loses a row has to be closed up.
    #
    # NOT OBSERVABLE TODAY, and mutation-checked: collapsing this loop to its first element kills no
    # test. Duplicates only exist on a grid, a grid is single-slot, so every bucket holding a copy
    # holds exactly one card at position 0 and there is nothing to compact. It is written for the
    # shape rather than for the state -- the day anything non-single-slot allows duplicates, a tier
    # row losing a middle copy would otherwise keep a hole that nothing repairs. Recorded rather than
    # pinned with a test that would have to fake a state the service cannot reach.
    leaving = list(
        PromptPlacement.objects.filter(**scope).values_list('bucket_id', 'position')
    )
    PromptPlacement.objects.filter(**scope).delete()
    for left_bucket_id, removed_position in leaving:
        PromptPlacement.objects.filter(
            response=locked, bucket_id=left_bucket_id, position__gt=removed_position,
        ).update(position=models.F('position') - 1)

    _recount(locked, prompt)
    return None


@transaction.atomic
def reorder_bucket(prompt, profile, *, bucket_id, placement_ids):
    """Set the order WITHIN one bucket to exactly `placement_ids`.

    KEYED ON PLACEMENTS, NOT ON GAMES, because a card no longer has one identity: a pooled prompt's
    placement points at a pool row and an open grid's at a Concept, and a grid that allows duplicates
    can hold the same game twice in one row. The placement id is the only thing that names exactly one
    card in exactly one slot.

    Refuses a partial list, the same contract every reorder on this site holds: a drag that posts a
    subset means the client and the server disagree about what is in the row.

    Only a tier row gives this meaning -- leftmost reads as best, and hunters will drag it. A grid
    slot and a poll hold one game, so the order is a fact about a single card.
    """
    refuse_if_unlinked(profile)

    bucket = get_bucket(prompt, _as_pk(bucket_id, what='row'))
    if bucket is None:
        raise PromptError('That row is not part of this one.')

    response = response_for(prompt, profile)
    if response is None:
        raise PromptError('You have not answered this yet.')

    locked = _lock_response(response)
    try:
        wanted = [int(value) for value in placement_ids]
    except (TypeError, ValueError):
        raise PromptError('That is not a valid order.')

    here = list(PromptPlacement.objects.filter(response=locked, bucket=bucket)
                .values_list('pk', flat=True))
    if sorted(wanted) != sorted(here):
        raise PromptError('That order does not match what is in this row.')

    by_pk = {p.pk: p for p in PromptPlacement.objects.filter(response=locked, bucket=bucket)}
    for position, placement_id in enumerate(wanted):
        by_pk[placement_id].position = position
    PromptPlacement.objects.bulk_update(by_pk.values(), ['position'])
    PromptResponse.objects.filter(pk=locked.pk).update(updated_at=timezone.now())


# ── the response itself ──────────────────────────────────────────────────────────────────────────

@transaction.atomic
def set_public(prompt, profile, *, is_public):
    """Show it or keep it to yourself.

    PUBLISHING IS GATED, hiding is not -- the same split `update_prompt` draws, and the same bypass it
    documents: a call carrying nothing but `is_public=true` must not reach the write ungated, or the
    route around a restriction is "arrange privately, then publish the lot".
    """
    refuse_if_unlinked(profile)
    if is_public:
        refuse_if_restricted(profile)
        _refuse_if_unreadable(prompt, profile)

    response = response_for(prompt, profile)
    if response is None:
        raise PromptError('You have not answered this yet.')

    locked = _lock_response(response)
    if locked.is_public == bool(is_public):
        return locked
    locked.is_public = bool(is_public)
    locked.save(update_fields=['is_public', 'updated_at'])
    _recount_prompt(prompt)
    return locked


@transaction.atomic
def clear(prompt, profile):
    """Delete the whole answer, not merely its placements.

    The row carries a visibility choice and any likes it has drawn, so emptying it and deleting it are
    genuinely different acts: this is the one that says "I never answered". Idempotent, because a
    second click should not error about something already gone.
    """
    refuse_if_unlinked(profile)

    response = response_for(prompt, profile)
    if response is None:
        return
    response.delete()
    _recount_prompt(prompt)


# ── counters ─────────────────────────────────────────────────────────────────────────────────────

def _next_position(locked, bucket):
    """Append within the bucket. Derived from the ROWS, so a gap cannot hand out a duplicate."""
    highest = PromptPlacement.objects.filter(response=locked, bucket=bucket).aggregate(
        top=models.Max('position'))['top']
    return 0 if highest is None else highest + 1


def _recount(locked, prompt):
    """`placement_count` from the rows, and the prompt's answer tally with it.

    Recomputed rather than nudged: an existing error is corrected instead of carried forward, and
    `PositiveIntegerField` is a DB CHECK on Postgres, so a counter that drifted high would eventually
    raise IntegrityError out of a `-1` rather than the PromptError a caller is catching.
    """
    PromptResponse.objects.filter(pk=locked.pk).update(
        placement_count=PromptPlacement.objects.filter(response=locked).count(),
        updated_at=timezone.now(),
    )
    _recount_prompt(prompt)


def _recount_prompt(prompt):
    """How many answers this prompt has, as a reader would count them.

    PUBLIC AND NON-EMPTY, which is the same population `listed_responses` returns. Counting private or
    empty ones would make the browse tile say forty about a page showing three -- a number that is not
    wrong so much as answering a different question than the one the reader is asking.
    """
    Prompt.objects.filter(pk=prompt.pk).update(
        response_count=PromptResponse.objects.filter(
            prompt=prompt, is_public=True, placement_count__gt=0).count(),
    )
