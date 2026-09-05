"""The rules every audit log on this site obeys, in one place.

There are two logs: `ModerationAction` (content decisions -- what a moderator did to a quick take or
a game flag) and `AdminAction` (account, billing and system acts). They are separate TABLES on
purpose, because a billing entry has nothing to point a `blurb_report` FK at and a moderation entry
has nothing to say about a Stripe subscription. Forcing one table to serve both is how a column ends
up meaning two things.

But they are not separate RULES. Both freeze the actor's name at write time, both require a reason,
and both treat a reversal as a new entry rather than an edit. Those three rules are what makes either
log worth keeping, and they were written out twice before this module existed -- which is two places
to drift apart on whether a reason is optional.

Deliberately NOT an abstract model base. Sharing the schema would force `ModerationAction`'s
per-field `help_text` (the best documentation in that model) to go generic, or push
moderation-flavoured wording into a billing log. `tests/engine/test_admin_audit.py` asserts the two
tables keep the same contract by introspection instead, which is a stronger guard than inheritance:
it also catches a `null=True` quietly added to `reason`.
"""

#: The shortest reason that can mean anything. Not zero: a blank reason turns an audit trail into a
#: list of timestamps, which answers "what happened" while leaving "why" -- the only question an
#: appeal actually asks -- unanswered.
MIN_REASON_LENGTH = 3

#: `actor_label` / `subject_label` are CharField(150) on both logs.
MAX_LABEL_LENGTH = 150


class AuditError(Exception):
    """An audited action that cannot be applied. Carries a message fit to show the person acting."""


def require_reason(reason, error=AuditError):
    """Return the cleaned reason, or raise because there isn't one.

    Enforced HERE rather than in a form, because a form guarantees nothing about the next caller: a
    management command, a shell session, or a Django-admin bulk action that never renders one.

    `error` is a parameter so each service raises the exception ITS callers already catch --
    `moderation_service` raises `ModerationError`, and a view catching that must not be handed a
    bare `AuditError` it will let escape into a 500.
    """
    reason = (reason or '').strip()
    if len(reason) < MIN_REASON_LENGTH:
        raise error('A reason is required, and has to say something.')
    return reason


def frozen_label(user):
    """The actor's (or subject's) display name, captured NOW.

    Every actor FK on both logs is SET_NULL, so deleting an account must never erase what it did or
    what was done to it. This is the string that keeps the entry readable afterwards, which is why it
    is stored rather than looked up at render time.

    `CustomUser.display_name` owns the PSN-handle-then-email order; this only freezes it and clips it
    to the column width.
    """
    if user is None:
        return ''
    return (getattr(user, 'display_name', '') or '')[:MAX_LABEL_LENGTH]
