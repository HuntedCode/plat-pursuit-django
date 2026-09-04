"""Moderator decisions: apply the change, and record who/why/what-changed in one step.

EVERY mod action goes through here rather than through the models directly. The point is that the
change and its log entry cannot come apart: a view that writes `blurb_hidden = True` and then logs
is a view that can write and not log -- on an early return, on an exception, or because whoever adds
the third queue copies the write and misses the logging. Here the log IS the write.

`reason` is enforced in the service, not in a form. A form guarantees nothing about the next caller
(a management command, a shell, the admin dashboard we have not built yet), and a log entry with no
reason answers "what happened" while leaving "why" -- the only question an appeal actually asks --
unanswered.
"""
import logging

from django.db import transaction
from django.utils import timezone

from trophies.models import BlurbReport, GameFlag, ModerationAction

logger = logging.getLogger(__name__)


class ModerationError(Exception):
    """A moderator action that cannot be applied. Carries a message fit to show the moderator."""


def _watched_game_fields():
    """Every `Game` field a flag approval can write, READ OUT of the service that writes them.

    Hand-listing these is how the log goes quietly wrong: I first wrote `has_unobtainable_trophies`
    and `is_delisted` from memory, and only one of those is real (`is_obtainable` is the other). A
    wrong name does not raise -- it just never appears in `changed`, so the log records "approved,
    nothing changed" for an approval that changed something, which is worse than no log at all.
    """
    from trophies.services.game_flag_service import GameFlagService
    import inspect
    import re

    src = inspect.getsource(GameFlagService.approve_flag)
    fields = set(re.findall(r"\(\s*'([a-z_]+)',\s*(?:True|False)\s*\)", src))       # the actions map
    fields |= set(re.findall(r"game\.([a-z_]+)\s*=", src))                          # the elif branches
    return sorted(fields)


#: Resolved once at import. If `approve_flag` grows a field, this picks it up without a second edit.
_WATCHED_GAME_FIELDS = _watched_game_fields()


def _require_reason(reason):
    reason = (reason or '').strip()
    if len(reason) < 3:
        raise ModerationError('A reason is required, and has to say something.')
    return reason


def _label(user):
    """The actor's display name, captured now. `actor` is SET_NULL, so this is what keeps the entry
    readable once a staff account is gone."""
    if user is None:
        return ''
    profile = getattr(user, 'profile', None)
    return (getattr(profile, 'display_psn_username', None) or getattr(profile, 'psn_username', None)
            or user.email or '')[:150]


# ── quick takes ──────────────────────────────────────────────────────────────────────────────────

@transaction.atomic
def hide_blurb(report, moderator, reason):
    """Soft-hide the reported quick take and close the report.

    The RATING survives -- its scores stay in every average, and the hunter keeps their rating. Only
    the free text goes. That is the whole reason `blurb_hidden` is a separate field from the blurb
    itself: hiding words a moderator objects to should not silently rewrite the game's numbers.
    """
    reason = _require_reason(reason)
    rating = report.rating
    was_hidden = rating.blurb_hidden
    if not was_hidden:
        rating.blurb_hidden = True
        rating.save(update_fields=['blurb_hidden'])

    report.status = 'action_taken'
    report.reviewed_by = moderator
    report.reviewed_at = timezone.now()
    report.save(update_fields=['status', 'reviewed_by', 'reviewed_at'])

    return ModerationAction.objects.create(
        actor=moderator, actor_label=_label(moderator), action='blurb_hidden', reason=reason,
        blurb_report=report,
        # The blurb text itself is captured because hiding is reversible and the words are the
        # evidence: an appeal cannot be judged from "a quick take was hidden".
        target_label=f'Quick take on {rating.concept.unified_title}'[:255],
        changed={'blurb_hidden': [was_hidden, True], 'blurb': [rating.blurb, rating.blurb]},
    )


@transaction.atomic
def dismiss_blurb_report(report, moderator, reason):
    """Close the report and leave the quick take alone -- the report was wrong, or the take is fine."""
    reason = _require_reason(reason)
    report.status = 'dismissed'
    report.reviewed_by = moderator
    report.reviewed_at = timezone.now()
    report.save(update_fields=['status', 'reviewed_by', 'reviewed_at'])

    return ModerationAction.objects.create(
        actor=moderator, actor_label=_label(moderator), action='blurb_report_dismissed',
        reason=reason, blurb_report=report,
        target_label=f'Quick take on {report.rating.concept.unified_title}'[:255],
        changed={'status': ['pending', 'dismissed']},
    )


# ── game flags ───────────────────────────────────────────────────────────────────────────────────

@transaction.atomic
def approve_game_flag(flag, moderator, reason):
    """Uphold the flag and let it change the game.

    Delegates the mutation to `GameFlagService.approve_flag`, which already owns the per-type rules
    (shovelware sets a LOCK that overrides the automated classifier; delisted/obtainable set their
    own fields; missing_vr and region_incorrect change nothing automatically). Reimplementing that
    map here would be a second definition of what a flag MEANS, and the two would drift.

    The before/after is captured around the call rather than inside it, so the log records what the
    service actually did rather than what this function expected it to do.
    """
    from trophies.services.game_flag_service import GameFlagService

    reason = _require_reason(reason)
    game = flag.game
    before = {f: getattr(game, f) for f in _WATCHED_GAME_FIELDS}

    GameFlagService.approve_flag(flag, moderator)

    game.refresh_from_db()
    after = {f: getattr(game, f, None) for f in before}
    changed = {f: [before[f], after[f]] for f in before if before[f] != after[f]}

    return ModerationAction.objects.create(
        actor=moderator, actor_label=_label(moderator), action='game_flag_approved', reason=reason,
        game_flag=flag,
        target_label=f'{flag.get_flag_type_display()} on {game.title_name}'[:255],
        # An approval that changed NOTHING is a real outcome, not a bug: missing_vr and
        # region_incorrect are upheld for a human to act on. An empty dict says exactly that.
        changed=changed,
    )


@transaction.atomic
def dismiss_game_flag(flag, moderator, reason):
    """Reject the flag. The game is untouched."""
    from trophies.services.game_flag_service import GameFlagService

    reason = _require_reason(reason)
    GameFlagService.dismiss_flag(flag, moderator)

    return ModerationAction.objects.create(
        actor=moderator, actor_label=_label(moderator), action='game_flag_dismissed', reason=reason,
        game_flag=flag,
        target_label=f'{flag.get_flag_type_display()} on {flag.game.title_name}'[:255],
        changed={'status': ['pending', 'dismissed']},
    )


# ── reversal ─────────────────────────────────────────────────────────────────────────────────────

#: What each action's undo is. Absent = not reversible from the log (a dismissal changed nothing to
#: put back; re-opening a report is a queue operation, not an undo).
_REVERSIBLE = {'blurb_hidden'}


@transaction.atomic
def reverse_action(action, moderator, reason):
    """Undo an earlier decision, as a NEW logged entry.

    Never by editing or deleting the original. An audit trail that can be rewritten is not one, and
    the question asked when a decision is disputed is "who reversed this, and why" -- which only has
    an answer if the reversal is itself an event.
    """
    reason = _require_reason(reason)
    if action.action not in _REVERSIBLE:
        raise ModerationError(f'{action.get_action_display()} cannot be reversed automatically.')
    if action.is_reversed:
        raise ModerationError('That decision has already been reversed.')

    report = action.blurb_report
    if report is None:
        raise ModerationError('The report behind this decision is gone, so it cannot be undone here.')

    rating = report.rating
    rating.blurb_hidden = False
    rating.save(update_fields=['blurb_hidden'])
    report.status = 'reviewed'
    report.save(update_fields=['status'])

    return ModerationAction.objects.create(
        actor=moderator, actor_label=_label(moderator), action='blurb_restored', reason=reason,
        blurb_report=report, reverses=action,
        target_label=action.target_label,
        changed={'blurb_hidden': [True, False]},
    )
