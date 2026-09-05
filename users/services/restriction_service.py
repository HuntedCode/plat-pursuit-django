"""Restricting a hunter from writing, and lifting it again.

Every write here is audited the same way a moderation decision is: a reason is required at the
SERVICE, the change and its `AdminAction` entry go in one transaction, and lifting writes a NEW entry
pointing at the one that applied it rather than editing history.

THE ASYMMETRY WORTH KNOWING. `active_scopes()` is read on ordinary hunters' write paths -- posting a
quick take, filing a flag -- so it is one indexed query and nothing more. Applying and lifting are
rare, admin-only, and carry all the ceremony.
"""
import logging

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from core.models import AdminAction
from core.services import audit
from users.models import CustomUser, UserRestriction

logger = logging.getLogger(__name__)

#: `all_ugc` is not a scope in its own right so much as a shorthand for every other one. Written down
#: rather than inferred at each gate, because "does this restriction cover me" answered two different
#: ways in two files is how somebody stays restricted from one thing and not another.
SCOPE_COVERS = {
    'quick_takes': {'quick_takes', 'all_ugc'},
    'reports': {'reports', 'all_ugc'},
    # `all_ugc` asked about directly is a fair question and used to be a KeyError -- which, inside a
    # DRF view's broad `except`, becomes a 500 rather than a refusal. A landmine for the next gate
    # author, who would reasonably write it.
    'all_ugc': {'all_ugc'},
}

#: The longest a restriction can run and still be a date rather than a joke. `timedelta` raises
#: OverflowError past roughly 2.9 million days, and `OverflowError` is not a `ValueError`, so an
#: unbounded `days` was a 500 from a form field. Ten years is past any plausible sanction; longer
#: than that, an admin means indefinite and should say so.
MAX_RESTRICTION_DAYS = 3650


class RestrictionError(Exception):
    """A restriction that cannot be applied or lifted. Carries a message fit to show an admin."""


def _require_reason(reason):
    return audit.require_reason(reason, error=RestrictionError)


def _live(queryset):
    """Not lifted, and not expired -- evaluated in the DATABASE against `now`.

    Doing the expiry check in Python would load every restriction an account has ever had onto a
    hunter's write path, and would be wrong for anything held across a request boundary, because
    expiry happens by the clock rather than by anybody writing a row.
    """
    return (queryset.filter(lifted_at__isnull=True)
            .filter(Q(expires_at__isnull=True) | Q(expires_at__gt=timezone.now())))


def active_scopes_for(profile):
    """The scopes this hunter is currently restricted from. One query.

    Matches on the account OR the profile, because a restriction remembers both and either half can
    be null: the account is gone after a self-service deletion, the profile is absent for an account
    that never linked PSN. Asking for only one of them is how somebody walks away from a sanction.
    """
    user_id = getattr(profile, 'user_id', None)
    match = Q(profile=profile)
    if user_id:
        match |= Q(user_id=user_id)
    return set(_live(UserRestriction.objects.filter(match)).values_list('scope', flat=True))


def active_scopes(user_id):
    """The scopes one ACCOUNT is restricted from. Kept for callers that hold a user and no profile."""
    if not user_id:
        return set()
    return set(_live(UserRestriction.objects.filter(user_id=user_id))
               .values_list('scope', flat=True))


def is_restricted_from(profile, scope):
    """Whether the hunter behind this profile may not do `scope` right now.

    Takes a PROFILE because that is what every caller has -- the UGC gates all run inside code that
    already loaded one.

    FAILS CLOSED on the wrong kind of object. `getattr(profile, 'user_id', None)` returned None for a
    `CustomUser` -- which has no `user_id` -- and None meant "not restricted", so handing this the
    wrong type silently granted permission. A gate whose default is permissive is not a gate.
    """
    from trophies.models import Profile

    if profile is None:
        return False
    if not isinstance(profile, Profile):
        raise TypeError(
            f'is_restricted_from() takes a Profile, not {type(profile).__name__}. Guessing here '
            f'would fail open.')
    return bool(active_scopes_for(profile) & SCOPE_COVERS[scope])


@transaction.atomic
def apply_restriction(user, scope, admin, reason, expires_at=None):
    """Bar an account from writing something, until a date or indefinitely.

    Refuses if an equivalent restriction is already live, using the same row-lock-plus-precondition
    shape the moderation queue uses: without it two admins acting at once both succeed, and the
    second writes an entry claiming it restricted somebody who was already restricted.

    The lock is taken over the account's restriction ROWS rather than the account, so it does not
    serialise unrelated admin work on the same person.
    """
    reason = _require_reason(reason)
    if scope not in dict(UserRestriction.SCOPES):
        raise RestrictionError(f'{scope!r} is not a restriction scope.')
    if expires_at is not None and expires_at <= timezone.now():
        # A restriction born already over. The service accepted it, the log recorded it, and the
        # admin was told "Restriction applied" -- while `expires_at__gt=now` never matched, so it
        # restricted nobody. It showed up only under "ended", as something that lapsed before it
        # existed.
        raise RestrictionError('That restriction would already have expired. Pick a length.')

    # The lock goes on the USER row, not on the restrictions. `SELECT ... FOR UPDATE` locks rows
    # that EXIST, so locking a filtered restriction queryset locks nothing at all in the common case
    # -- a hunter with no live restriction -- and two admins acting at once both saw zero rows and
    # both inserted. That is the phantom-insert the docstring claimed to prevent, prevented only in
    # the case where an unlocked read would have caught it too. The account always exists.
    CustomUser.objects.select_for_update().filter(pk=user.pk).first()

    live = list(_live(UserRestriction.objects.filter(user=user)))
    # Coverage OVERLAP, not membership. `covering = {'all_ugc', scope}` refused broad-then-narrow and
    # waved narrow-then-broad straight through, so `quick_takes` followed by `all_ugc` produced two
    # live rows -- and lifting the one an admin could see left the hunter still barred, with no page
    # saying so. That is the exact state the model docstring says cannot happen.
    clash = next((row for row in live
                  if row.scope == scope or 'all_ugc' in (row.scope, scope)), None)
    if clash is not None:
        until = f' until {clash.expires_at:%d %b %Y}' if clash.expires_at else ', indefinitely'
        raise RestrictionError(
            f'Already restricted ({clash.get_scope_display().lower()}{until}). '
            f'Lift that one first if you mean to change it.')

    restriction = UserRestriction.objects.create(
        user=user, profile=getattr(user, 'profile', None),
        scope=scope, reason=reason, expires_at=expires_at,
        created_by=admin, created_by_label=audit.frozen_label(admin),
    )
    AdminAction.objects.create(
        actor=admin, actor_label=audit.frozen_label(admin),
        action='restriction_applied', reason=reason,
        subject_user=user, subject_label=audit.frozen_label(user),
        target_type='restriction', target_id=str(restriction.pk),
        target_label=f'{restriction.get_scope_display()} for {audit.frozen_label(user)}'[:255],
        changed={'scope': [None, scope],
                 'expires_at': [None, expires_at.isoformat() if expires_at else None]},
    )
    logger.info('Restriction applied: user=%s scope=%s by=%s until=%s',
                user.pk, scope, getattr(admin, 'pk', None), expires_at)
    return restriction


@transaction.atomic
def lift_restriction(restriction, admin, reason):
    """End a restriction early, as a new logged entry.

    Stamps the lift fields and writes an `AdminAction` whose `reverses` points at the entry that
    applied it -- the same grammar as reversing a moderation decision, so the two read alike in the
    log and "who lifted this, and why" has an answer.

    A LAPSED restriction cannot be lifted. It is already over, and saying so beats writing an entry
    claiming somebody freed a hunter who was free already.
    """
    reason = _require_reason(reason)
    locked = UserRestriction.objects.select_for_update().get(pk=restriction.pk)
    if locked.lifted_at is not None:
        raise RestrictionError('That restriction has already been lifted.')
    if not locked.is_live:
        raise RestrictionError('That restriction has already expired on its own.')

    locked.lifted_at = timezone.now()
    locked.lifted_by = admin
    locked.lifted_by_label = audit.frozen_label(admin)
    locked.lift_reason = reason
    locked.save(update_fields=['lifted_at', 'lifted_by', 'lifted_by_label', 'lift_reason'])

    # The entry that applied it, so the lift can point at something. Matched on the restriction's own
    # id rather than by time, because an account can have several and "the most recent" is not the
    # same question.
    applied = (AdminAction.objects
               .filter(action='restriction_applied', target_type='restriction',
                       target_id=str(locked.pk))
               .order_by('created_at').first())

    lift = AdminAction.objects.create(
        actor=admin, actor_label=audit.frozen_label(admin),
        action='restriction_lifted', reason=reason, reverses=applied,
        subject_user=locked.user, subject_label=audit.frozen_label(locked.user),
        target_type='restriction', target_id=str(locked.pk),
        target_label=f'{locked.get_scope_display()} for {audit.frozen_label(locked.user)}'[:255],
        changed={'lifted_at': [None, locked.lifted_at.isoformat()]},
    )
    logger.info('Restriction lifted: id=%s user=%s by=%s', locked.pk, locked.user_id,
                getattr(admin, 'pk', None))
    return lift
