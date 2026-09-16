"""Likes, on both of the two things people react to here.

The owner's call: a prompt AND a response each take likes, because they are different acts. Liking a
prompt says "good question"; liking a response says "good answer", and on a site like this the answer
is usually the more interesting of the two.

ONE MODULE FOR BOTH, rather than a half in each service. A like is one concept with two targets, and
splitting it by target would put the same six rules in two places and let them drift -- which is the
shape `game_list_service._set_social` already avoids by parameterising on the model.

THE SIX RULES, all lifted from that function because they were earned there:

1. **Gated on the way IN, open on the way out.** Every refusal below except the linked check is
   scoped to `liked` -- not just the restriction. A like is wordless, which makes the gate tempting
   to skip, but it is a public signal attributed to a profile that feeds a public ranking, which is
   exactly what `all_ugc` covers. Withdrawing one is never refused, including after the thing you
   liked has gone private: otherwise a lit heart becomes permanent and keeps counting.
2. **You cannot like what you cannot see.** Otherwise liking by id is an oracle that reports whether a
   private prompt exists, and whose.
3. **You cannot like your own.** Self-liking would let an author push their own work up the ranking
   their own `like_count` drives.
4. **Idempotent in both directions.** A double tap is not an error and does not double-count.
5. **The counter moves with `F()`**, never a read-modify-write: it is contended by definition.
6. **The stored value is re-read and returned**, so a caller renders the number the database holds
   rather than the one it hoped for -- as of that statement. What this does NOT buy: a like and an
   unlike issued concurrently by one profile resolve to whichever transaction's write landed, not to
   whichever the hunter pressed last. The row and the counter always agree (the delta is the rowcount
   of the write itself, never a prediction from an earlier read); the INTENT can still lose a race.
   Closing that needs `SELECT ... FOR UPDATE` on the parent, which is a real cost on a hot row for a
   benefit measured in double-taps. `_set_social` makes the identical trade.
"""
from django.db import models, transaction

from prompts.models import Prompt, PromptLike, PromptResponse, PromptResponseLike
from prompts.services.response_service import listed_responses as listed_responses_for
from prompts.services.prompt_service import (
    PromptError,
    refuse_if_restricted,
    refuse_if_unlinked,
)


@transaction.atomic
def set_prompt_like(prompt, profile, *, liked):
    """Like or unlike the QUESTION.

    EVERY REFUSAL EXCEPT THE LINKED CHECK IS SCOPED TO LIKING, which is what makes rule 1 true rather
    than aspirational. The first cut gated only the restriction and left visibility and self-like
    unconditional, so: a fan likes a published prompt, the author unpublishes it (legal, nobody had
    answered), and the fan's lit heart becomes permanent -- `readable_by` now excludes it, so the
    unlike is refused, the like keeps counting, and it re-enters the popular sort the moment the
    author republishes. Withdrawing a signal you already gave leaks nothing: the row's existence is
    your own information.
    """
    refuse_if_unlinked(profile)

    if liked:
        refuse_if_restricted(profile)
        if not Prompt.objects.readable_by(profile).filter(pk=prompt.pk).exists():
            raise PromptError('That is not available.')
        if prompt.owner_id == profile.id:
            raise PromptError('That is your own.')

    return _toggle(
        PromptLike, {'prompt': prompt}, profile,
        on=liked, parent=Prompt, parent_pk=prompt.pk, field='like_count',
    )


@transaction.atomic
def set_response_like(response, profile, *, liked):
    """Like or unlike somebody's ANSWER.

    VISIBILITY IS TWO QUESTIONS HERE, not one: the answer must be public, and so must the prompt it
    answers. A response inherits nothing automatically -- they carry independent flags by design --
    so asking only about the response would let a like confirm the existence of an answer to a
    withdrawn draft.
    """
    refuse_if_unlinked(profile)

    if liked:
        refuse_if_restricted(profile)
        # THE SAME POPULATION `listed_responses` RETURNS, built from it rather than beside it. Writing
        # the predicate out twice is how the two drift, which is the argument this module opens with.
        visible = (listed_responses_for(response.prompt)
                   .filter(pk=response.pk)
                   .filter(prompt__in=Prompt.objects.readable_by(profile))
                   .exists())
        if not visible:
            raise PromptError('That answer is not available.')
        if response.profile_id == profile.id:
            raise PromptError('That is your own answer.')
        # NOT refused for the PROMPT's author: liking the answers to your own question is one of the
        # things this feature is for. Stated because the rule above looks like it should generalise.

    return _toggle(
        PromptResponseLike, {'response': response}, profile,
        on=liked, parent=PromptResponse, parent_pk=response.pk, field='like_count',
    )


def _toggle(model, target, profile, *, on, parent, parent_pk, field):
    """One implementation for both, because they are the same operation twice.

    `delta` comes from what the write actually DID -- `created` from `get_or_create`, the row count
    from `delete()` -- so a double tap contributes nothing rather than counting twice. That is what
    makes this idempotent without a separate existence check, and without a race between the check
    and the write.
    """
    if on:
        _row, created = model.objects.get_or_create(profile=profile, **target)
        delta = 1 if created else 0
    else:
        deleted, _ = model.objects.filter(profile=profile, **target).delete()
        delta = -1 if deleted else 0

    if delta:
        # Not `save()` on a stale instance: the counter is contended by definition and a
        # read-modify-write loses likes under concurrency. F() pushes the arithmetic to the database.
        parent.objects.filter(pk=parent_pk).update(**{field: models.F(field) + delta})

    return parent.objects.filter(pk=parent_pk).values_list(field, flat=True).first()
