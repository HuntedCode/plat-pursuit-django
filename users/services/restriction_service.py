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
from users.models import UserRestriction

logger = logging.getLogger(__name__)

#: `all_ugc` is not a scope in its own right so much as a shorthand for every other one. Written down
#: rather than inferred at each gate, because "does this restriction cover me" answered two different
#: ways in two files is how somebody stays restricted from one thing and not another.
SCOPE_COVERS = {
    'quick_takes': {'quick_takes', 'all_ugc'},
    'reports': {'reports', 'all_ugc'},
}


class RestrictionError(Exception):
    """A restriction that cannot be applied or lifted. Carries a message fit to show an admin."""


def _require_reason(reason):
    return audit.require_reason(reason, error=RestrictionError)


def active_scopes(user_id):
    """The scopes this account is currently restricted from. One query, no Python filtering.

    Live means not lifted AND not expired, evaluated in the DATABASE against `now`. Doing the expiry
    check in Python would mean loading every restriction the account has ever had onto a hunter's
    write path -- and would be wrong for anything cached across a request boundary, because expiry
    happens by the clock rather than by anybody writing a row.
    """
    if not user_id:
        return set()
    return set(
        UserRestriction.objects
        .filter(user_id=user_id, lifted_at__isnull=True)
        .filter(Q(expires_at__isnull=True) | Q(expires_at__gt=timezone.now()))
        .values_list('scope', flat=True)
    )


def is_restricted_from(profile, scope):
    """Whether the account behind this profile may not do `scope` right now.

    Takes a PROFILE because that is what every caller has -- the UGC gates all run inside code that
    already loaded one. The restriction hangs off the user, so this is where the hop happens, once,
    rather than at five call sites.
    """
    user_id = getattr(profile, 'user_id', None)
    if not user_id:
        return False
    return bool(active_scopes(user_id) & SCOPE_COVERS[scope])


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

    live = list(
        UserRestriction.objects.select_for_update()
        .filter(user=user, lifted_at__isnull=True)
        .filter(Q(expires_at__isnull=True) | Q(expires_at__gt=timezone.now()))
    )
    covering = {'all_ugc', scope}
    clash = next((row for row in live if row.scope in covering), None)
    if clash is not None:
        until = f' until {clash.expires_at:%d %b %Y}' if clash.expires_at else ', indefinitely'
        raise RestrictionError(
            f'Already restricted ({clash.get_scope_display().lower()}{until}). '
            f'Lift that one first if you mean to change it.')

    restriction = UserRestriction.objects.create(
        user=user, scope=scope, reason=reason, expires_at=expires_at,
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
