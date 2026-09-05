"""Moderator decisions: apply the change, and record who/why/what-changed in one step.

EVERY mod action goes through here rather than through the models directly. The point is that the
change and its log entry cannot come apart: a view that writes `blurb_hidden = True` and then logs
is a view that can write and not log -- on an early return, on an exception, or because whoever adds
the third queue copies the write and misses the logging. Here the log IS the write.

Two rules the audit of the first cut made explicit, both worth stating because neither is obvious:

  THE LOG MUST SAY WHAT ACTUALLY HAPPENED. Applying the change and logging it atomically is not
  enough on its own. Without a status precondition and a row lock, two moderators acting on one
  report both succeed, and the second writes an entry claiming a change it did not make. For an
  appeal record, an entry saying "moderator B hid this" when B hid nothing is worse than no entry:
  it is affirmatively misleading evidence.

  `reason` IS ENFORCED HERE, not in a form. A form guarantees nothing about the next caller (a
  management command, a shell, the admin dashboard that does not exist yet), and a log of timestamps
  with no reasons answers "what happened" while leaving "why" -- the only question an appeal asks --
  unanswered.
"""
import logging

from django.core.exceptions import ObjectDoesNotExist
from django.db import transaction
from django.db.models import Count, Q
from django.utils import timezone

from core.services import audit
from trophies.models import BlurbReport, GameFlag, ModerationAction
from trophies.services.game_flag_service import GameFlagService

logger = logging.getLogger(__name__)

#: Every `Game` field a flag approval can write, taken from the service that writes them.
#:
#: This was first DERIVED by running a regex over `approve_flag`'s source text, to stop the list
#: drifting. It drifted worse: a reformat, a quoting change, a hoisted constant or a field name with
#: a digit in it each returned FEWER fields, silently -- which is precisely the "an approval that
#: changed something logs as changed nothing" failure the derivation existed to prevent, now with six
#: extra ways to trigger it and none of them visible in a diff. The map is a class attribute on
#: `GameFlagService` now, and `test_every_flag_type_lands_in_the_log` is the real guard: it approves
#: every flag type and fails if a field the DB actually changed is missing from `changed`.
WATCHED_GAME_FIELDS = GameFlagService.WATCHED_FIELDS


class ModerationError(Exception):
    """A moderator action that cannot be applied. Carries a message fit to show the moderator."""


def _require_reason(reason):
    """Delegates to `core.services.audit`, which both logs read.

    The exception CLASS is passed through rather than caught and re-raised: `_ActionView` catches
    `ModerationError` specifically, and handing it a bare `AuditError` would let a missing reason
    escape as a 500 instead of a message the moderator can read.
    """
    return audit.require_reason(reason, error=ModerationError)


def _label(user):
    """The actor's display name, captured NOW. `actor` is SET_NULL, so this is what keeps an entry
    readable once a staff account is gone.

    `CustomUser.display_name` owns the PSN-then-email order and `core.services.audit` owns the
    freezing; this name is kept because the four call sites below read better for it.
    """
    return audit.frozen_label(user)


def _lock_report(report):
    """Re-read the report FOR UPDATE and refuse it if somebody already handled it.

    Both halves matter. The lock serialises two moderators hitting one report; the status check is
    what turns the loser into a clean "already handled" instead of a second, false log entry.
    """
    try:
        fresh = BlurbReport.objects.select_for_update().select_related('rating__concept').get(pk=report.pk)
    except ObjectDoesNotExist:
        raise ModerationError('That report no longer exists.')
    if fresh.status != 'pending':
        raise ModerationError(
            f'Already handled ({fresh.get_status_display()}). Reload the queue to see the current state.')
    return fresh


def _lock_flag(flag):
    try:
        fresh = GameFlag.objects.select_for_update().select_related('game').get(pk=flag.pk)
    except ObjectDoesNotExist:
        raise ModerationError('That flag no longer exists.')
    if fresh.status != 'pending':
        raise ModerationError(
            f'Already handled ({fresh.get_status_display()}). Reload the queue to see the current state.')
    return fresh


def _subject(profile):
    """The `subject_user` / `subject_label` pair for the hunter an entry is evidence ABOUT.

    Takes a `Profile` and returns kwargs for the `CustomUser` behind it, because every hunter-shaped
    thing in this service (a rating's author, a report's reporter) is a Profile while the log points
    at accounts. A restriction or a ban is an ACCOUNT fact, and pointing the history at profiles
    would make unlinking and relinking PSN a way to shed it.

    WHO counts as the subject is a rule with exactly one right answer per action, written down on
    the field: hiding a take is evidence about its AUTHOR, dismissing a report is evidence about the
    REPORTER. Left to each call site it would quietly become two rules.
    """
    user = getattr(profile, 'user', None)
    return {'subject_user': user, 'subject_label': audit.frozen_label(user)}


# ── quick takes ──────────────────────────────────────────────────────────────────────────────────

@transaction.atomic
def hide_blurb(report, moderator, reason):
    """Soft-hide the reported quick take and close the report.

    The RATING survives -- its scores stay in every average, and the hunter keeps their rating. Only
    the free text goes. That is the whole reason `blurb_hidden` is a separate field from the blurb
    itself: hiding words a moderator objects to should not silently rewrite the game's numbers.
    """
    reason = _require_reason(reason)
    report = _lock_report(report)
    rating = report.rating
    was_hidden = rating.blurb_hidden

    rating.blurb_hidden = True
    rating.save(update_fields=['blurb_hidden'])
    report.status = 'action_taken'
    report.reviewed_by = moderator
    report.reviewed_at = timezone.now()
    report.save(update_fields=['status', 'reviewed_by', 'reviewed_at'])

    action = ModerationAction.objects.create(
        actor=moderator, actor_label=_label(moderator), action='blurb_hidden', reason=reason,
        # The AUTHOR: hiding somebody's words is evidence about the person who wrote them.
        **_subject(rating.profile),
        blurb_report=report, target_id=rating.pk,
        target_label=f'Quick take on {rating.concept.unified_title}'[:255],
        changed={'blurb_hidden': [was_hidden, True]},
        # The words are the EVIDENCE, kept beside the diff rather than inside it: `changed` means
        # "what this action wrote", and the blurb was not written. Filed under its own key so a
        # generic diff view cannot render a "blurb: unchanged" row, and so "did this action modify
        # field X" never answers yes for the blurb.
        evidence={'blurb': rating.blurb},
    )
    logger.info('Moderation: blurb hidden report=%s rating=%s by=%s', report.pk, rating.pk,
                getattr(moderator, 'pk', None))
    return action


@transaction.atomic
def dismiss_blurb_report(report, moderator, reason):
    """Close the report and leave the quick take alone -- the report was wrong, or the take is fine."""
    reason = _require_reason(reason)
    report = _lock_report(report)
    was = report.status

    report.status = 'dismissed'
    report.reviewed_by = moderator
    report.reviewed_at = timezone.now()
    report.save(update_fields=['status', 'reviewed_by', 'reviewed_at'])

    action = ModerationAction.objects.create(
        actor=moderator, actor_label=_label(moderator), action='blurb_report_dismissed',
        reason=reason,
        # The REPORTER, not the author. A dismissal says the report was wrong, which is evidence
        # about the person who filed it and none at all about the person they filed it against.
        **_subject(report.reporter),
        blurb_report=report, target_id=report.rating_id,
        target_label=f'Quick take on {report.rating.concept.unified_title}'[:255],
        changed={'status': [was, 'dismissed']},   # READ, never assumed to have been 'pending'
    )
    logger.info('Moderation: blurb report dismissed report=%s by=%s', report.pk,
                getattr(moderator, 'pk', None))
    return action


# ── game flags ───────────────────────────────────────────────────────────────────────────────────

@transaction.atomic
def approve_game_flag(flag, moderator, reason):
    """Uphold the flag and let it change the game.

    Delegates the mutation to `GameFlagService.approve_flag`, which owns the per-type rules
    (shovelware sets a LOCK that overrides the automated classifier; delisted/obtainable set their
    own fields; missing_vr and region_incorrect change nothing automatically). Reimplementing that
    map here would be a second definition of what a flag MEANS, and the two would drift.

    The before/after is captured around the call, so the log records what the service actually did
    rather than what this function expected it to do.
    """
    reason = _require_reason(reason)
    flag = _lock_flag(flag)
    game = flag.game
    before = {f: getattr(game, f) for f in WATCHED_GAME_FIELDS}

    GameFlagService.approve_flag(flag, moderator)

    game.refresh_from_db()
    after = {f: getattr(game, f) for f in WATCHED_GAME_FIELDS}
    changed = {f: [before[f], after[f]] for f in before if before[f] != after[f]}

    action = ModerationAction.objects.create(
        actor=moderator, actor_label=_label(moderator), action='game_flag_approved', reason=reason,
        # The reporter. A game has no hunter behind it, so the only person this is evidence about is
        # the one who raised it -- and "who reports well" is exactly what this history is for.
        **_subject(flag.reporter),
        game_flag=flag, target_id=game.pk,
        target_label=f'{flag.get_flag_type_display()} on {game.title_name}'[:255],
        # An approval that changed NOTHING is a real outcome, not a bug: missing_vr and
        # region_incorrect are upheld for a human to act on. An empty dict says exactly that.
        changed=changed,
    )
    logger.info('Moderation: flag approved flag=%s type=%s game=%s by=%s changed=%s',
                flag.pk, flag.flag_type, game.pk, getattr(moderator, 'pk', None), list(changed))
    return action


@transaction.atomic
def dismiss_game_flag(flag, moderator, reason):
    """Reject the flag. The game is untouched."""
    reason = _require_reason(reason)
    flag = _lock_flag(flag)
    was = flag.status

    GameFlagService.dismiss_flag(flag, moderator)

    action = ModerationAction.objects.create(
        actor=moderator, actor_label=_label(moderator), action='game_flag_dismissed', reason=reason,
        **_subject(flag.reporter),
        game_flag=flag, target_id=flag.game_id,
        target_label=f'{flag.get_flag_type_display()} on {flag.game.title_name}'[:255],
        changed={'status': [was, 'dismissed']},
    )
    logger.info('Moderation: flag dismissed flag=%s by=%s', flag.pk, getattr(moderator, 'pk', None))
    return action


# ── reversal ─────────────────────────────────────────────────────────────────────────────────────

def _report_behind(action):
    """The BlurbReport an entry was about, or a message saying why it cannot be undone."""
    report = action.blurb_report
    if report is None:
        raise ModerationError(
            'The report behind this decision has been deleted, so it cannot be undone here.')
    return report


def _flag_behind(action):
    flag = action.game_flag
    if flag is None:
        raise ModerationError(
            'The flag behind this decision has been deleted, so it cannot be undone here.')
    return flag


def _undo_blurb_hidden(action, moderator):
    """Put a hidden quick take back, using what the ORIGINAL entry recorded rather than assuming."""
    report = _report_behind(action)
    try:
        rating = report.rating
    except ObjectDoesNotExist:
        raise ModerationError('The quick take behind this decision no longer exists.')

    # Read the previous value out of the log instead of hardcoding False. `changed` is documented as
    # the thing that "makes a reversal possible without guessing at the previous state", and the
    # first cut guessed anyway -- which for a take that was already hidden when it was actioned
    # would have UNhidden it, and called that a restoration.
    was_hidden = action.changed.get('blurb_hidden', [False, True])[0]
    rating.blurb_hidden = bool(was_hidden)
    rating.save(update_fields=['blurb_hidden'])
    report.status = 'reviewed'
    report.reviewed_by = moderator          # the standing decision is now this person's
    report.reviewed_at = timezone.now()
    report.save(update_fields=['status', 'reviewed_by', 'reviewed_at'])
    return {'blurb_report': report}, {'blurb_hidden': [True, bool(was_hidden)]}, {}


def _undo_blurb_report_dismissed(action, moderator):
    """Reopen a dismissed report: it goes back into the queue for somebody to decide again.

    `reviewed_by` and `reviewed_at` are CLEARED rather than reassigned. The report is genuinely
    pending again, and a row saying "dismissed by X" while sitting in the pending queue is a
    contradiction on the page. Who dismissed it is not lost -- it is in the entry being reversed,
    which is the whole reason that entry is not edited or deleted.
    """
    report = _report_behind(action)
    was = report.status
    report.status = 'pending'
    report.reviewed_by = None
    report.reviewed_at = None
    report.save(update_fields=['status', 'reviewed_by', 'reviewed_at'])
    return {'blurb_report': report}, {'status': [was, 'pending']}, {}


def _undo_game_flag_approved(action, moderator):
    """Put back what the approval wrote, and be honest about what it will not put back.

    THE TRAP THIS AVOIDS. `changed` records values as they were at DECISION time. Months can pass
    before a reversal, and `is_delisted` may have been changed since by a sync, another flag, or a
    person -- so blindly writing the old value back would silently discard a legitimate later edit
    and call it a restoration.

    So each field is restored ONLY if the game still holds exactly what this approval left there.
    Anything else is skipped and recorded under the reversal's `evidence` as `not_restored`, because
    a reversal that quietly did three quarters of its job is worse than one that says so.
    """
    flag = _flag_behind(action)
    game = flag.game
    restored, skipped = {}, {}

    for field, (before, after) in (action.changed or {}).items():
        current = getattr(game, field, None)
        if current != after:
            skipped[field] = {'expected': after, 'found': current, 'would_have_written': before}
            continue
        setattr(game, field, before)
        restored[field] = [after, before]

    if restored:
        game.save(update_fields=list(restored))

    was = flag.status
    flag.status = 'pending'
    flag.reviewed_by = None
    flag.reviewed_at = None
    flag.save(update_fields=['status', 'reviewed_by', 'reviewed_at'])

    changed = {**restored, 'status': [was, 'pending']}
    evidence = {'not_restored': skipped} if skipped else {}
    return {'game_flag': flag}, changed, evidence


def _undo_game_flag_dismissed(action, moderator):
    """Reopen a dismissed flag. The game was never touched, so there is nothing to put back."""
    flag = _flag_behind(action)
    was = flag.status
    flag.status = 'pending'
    flag.reviewed_by = None
    flag.reviewed_at = None
    flag.save(update_fields=['status', 'reviewed_by', 'reviewed_at'])
    return {'game_flag': flag}, {'status': [was, 'pending']}, {}


#: action -> (the callable that undoes it, what the resulting REVERSAL is called).
#:
#: A DICT, not a set of names: the first cut gated on a set while the body was hardcoded to the blurb
#: path, so adding a key would have told a moderator "the report behind this decision is gone" for a
#: report that was never involved -- reporting data loss when the real cause was unimplemented code.
#:
#: The pair, not just the callable: the reversal's own `action` used to be hardcoded to
#: `blurb_restored`, so the moment a second undo existed, reopening a game flag would have been
#: logged as a quick take being restored. The name of the result belongs beside the thing producing
#: it. A key with no handler is a KeyError at edit time; a handler with no name is impossible.
_UNDO = {
    'blurb_hidden': (_undo_blurb_hidden, 'blurb_restored'),
    'blurb_report_dismissed': (_undo_blurb_report_dismissed, 'blurb_report_reopened'),
    'game_flag_approved': (_undo_game_flag_approved, 'game_flag_reversed'),
    'game_flag_dismissed': (_undo_game_flag_dismissed, 'game_flag_reopened'),
}


#: The actions a page may offer a Reverse button for, derived from the map that implements them.
#:
#: Public, and derived rather than listed, because the alternative is a page that offers a button the
#: service then refuses -- the worst of both, since the admin has already typed a reason by then. Any
#: undo added to `_UNDO` becomes offerable the same moment it becomes possible.
UNDOABLE_ACTIONS = tuple(_UNDO)


@transaction.atomic
def reverse_action(action, moderator, reason):
    """Undo an earlier decision, as a NEW logged entry.

    Never by editing or deleting the original. An audit trail that can be rewritten is not one, and
    the question asked when a decision is disputed is "who reversed this, and why" -- which only has
    an answer if the reversal is itself an event.
    """
    reason = _require_reason(reason)
    # FOR UPDATE on the original: `is_reversed` is otherwise a plain read, so two admins reversing
    # the same entry would both see False and both insert. The DB constraint on `reverses` is the
    # backstop; this is what turns the loser into a clean message instead of an IntegrityError.
    locked = ModerationAction.objects.select_for_update().get(pk=action.pk)
    # BEFORE the handler lookup, not after. A reversal's own action has no `_UNDO` key, so the
    # generic "cannot be reversed automatically" would win the race and describe a missing feature
    # rather than the deliberate rule. Undoing an undo is re-deciding: do it as a decision, on the
    # record, with its own reason.
    if locked.reverses_id:
        raise ModerationError(
            'That entry is itself a reversal. To change the outcome again, act on the report.')
    handler = _UNDO.get(locked.action)
    if handler is None:
        raise ModerationError(f'{locked.get_action_display()} cannot be reversed automatically.')
    if locked.is_reversed:
        raise ModerationError('That decision has already been reversed.')

    undo, reversal_action = handler
    links, changed, evidence = undo(locked, moderator)

    reversal = ModerationAction.objects.create(
        actor=moderator, actor_label=_label(moderator), action=reversal_action, reason=reason,
        reverses=locked, **links,
        # Copied from the entry being undone rather than re-derived: a reversal is evidence about the
        # same hunter, and re-deriving it could disagree with the original if the report has since
        # been purged.
        subject_user=locked.subject_user, subject_label=locked.subject_label,
        target_id=locked.target_id, target_label=locked.target_label,
        changed=changed, evidence=evidence,
    )
    logger.info('Moderation: action %s reversed by=%s', locked.pk, getattr(moderator, 'pk', None))
    return reversal


# -- how much is waiting -------------------------------------------------------------------------

def queue_counts():
    """Per queue: how much is waiting, and how much there has ever been.

    Here rather than in the view because the navbar marker and the Mod Center have to agree on what
    "waiting" means -- a marker that counts differently from the page it points at is worse than no
    marker. Two grouped aggregates, not one query per status per queue: this must not grow a query
    per queue as queues are added.
    """
    blurbs = BlurbReport.objects.aggregate(
        open=Count('id', filter=Q(status='pending')), total=Count('id'))
    flags = GameFlag.objects.aggregate(
        open=Count('id', filter=Q(status='pending')), total=Count('id'))
    return {
        'quick-takes': {'open': blurbs['open'] or 0, 'total': blurbs['total'] or 0},
        'game-flags': {'open': flags['open'] or 0, 'total': flags['total'] or 0},
    }


def open_report_count():
    """Total waiting across every queue. What the navbar marker reads. LIVE, deliberately.

    This was cached for five minutes, with a story about the staleness being acceptable in one
    direction and busted in the other. The audit took that story apart three ways:

      - the Django-admin bulk actions move these rows out of `pending` without going through this
        module at all, and `queryset.update()` fires no signal, so nothing could have caught them;
      - the Mod Center computed the true number on the very same render and threw it away;
      - the get / compute / set was racy: a read straddling a bust could reinstate the number the
        bust had just removed, for a full TTL.

    All three end in the same place -- a marker claiming work against a page saying "nothing
    waiting", one click apart, which is the failure the shared definition exists to prevent.

    So it is not cached. Both tables index `status`, so this is two index-served counts, and it runs
    only for moderators and admins: an audience of about ten accounts, not the whole internet. The
    cache was protecting a per-request path that almost nobody takes, and it bought three ways for
    the marker and the page to disagree. A live count cannot disagree with itself.

    If this ever does need caching, cache it where the truth is known (in `queue_counts()`, written
    on every Mod Center render), not here.
    """
    return sum(counts['open'] for counts in queue_counts().values())
