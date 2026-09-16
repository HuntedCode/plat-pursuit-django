"""Likes, on both of the two things people react to here.

The owner's call: a prompt AND a response each take likes, because they are different acts. Liking a
prompt says "good question"; liking a response says "good answer", and on a site like this the answer
is usually the more interesting of the two.

ONE MODULE FOR BOTH, rather than a half in each service. A like is one concept with two targets, and
splitting it by target would put the same six rules in two places and let them drift -- which is the
shape `game_list_service._set_social` already avoids by parameterising on the model.

THE SIX RULES, all lifted from that function because they were earned there:

1. **Gated on the way IN, open on the way out.** `all_ugc` refuses a new like; withdrawing one is
   never refused. A like is wordless, which makes it tempting to leave open -- but it is a public
   signal attributed to a profile that feeds a public ranking, which is exactly what the scope covers.
2. **You cannot like what you cannot see.** Otherwise liking by id is an oracle that reports whether a
   private prompt exists, and whose.
3. **You cannot like your own.** Self-liking would let an author push their own work up the ranking
   their own `like_count` drives.
4. **Idempotent in both directions.** A double tap is not an error and does not double-count.
5. **The counter moves with `F()`**, never a read-modify-write: it is contended by definition.
6. **The stored value is re-read and returned**, so a caller renders the number the database holds
   rather than the one it hoped for.
"""
from django.db import models, transaction

from prompts.models import Prompt, PromptLike, PromptResponse, PromptResponseLike
from prompts.services.prompt_service import (
    PromptError,
    refuse_if_restricted,
    refuse_if_unlinked,
)


@transaction.atomic
def set_prompt_like(prompt, profile, *, liked):
    """Like or unlike the QUESTION."""
    if liked:
        refuse_if_restricted(profile)
    refuse_if_unlinked(profile)

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
    if liked:
        refuse_if_restricted(profile)
    refuse_if_unlinked(profile)

    visible = (PromptResponse.objects
               .filter(pk=response.pk, is_public=True, placement_count__gt=0)
               .filter(prompt__in=Prompt.objects.readable_by(profile))
               .exists())
    if not visible:
        raise PromptError('That answer is not available.')
    if response.profile_id == profile.id:
        raise PromptError('That is your own answer.')

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
