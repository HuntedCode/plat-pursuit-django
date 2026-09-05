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
        reason=reason, blurb_report=report, target_id=report.rating_id,
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
        game_flag=flag, target_id=flag.game_id,
        target_label=f'{flag.get_flag_type_display()} on {flag.game.title_name}'[:255],
        changed={'status': [was, 'dismissed']},
    )
    logger.info('Moderation: flag dismissed flag=%s by=%s', flag.pk, getattr(moderator, 'pk', None))
    return action


# ── reversal ─────────────────────────────────────────────────────────────────────────────────────

def _undo_blurb_hidden(action, moderator, reason):
    """Put a hidden quick take back, using what the ORIGINAL entry recorded rather than assuming."""
    report = action.blurb_report
    if report is None:
        raise ModerationError(
            'The report behind this decision has been deleted, so it cannot be undone here.')
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
    return report, {'blurb_hidden': [True, bool(was_hidden)]}


#: action -> the callable that undoes it. A DICT, not a set of names: the first cut gated on a set
#: while the body was hardcoded to the blurb path, so adding a key would have sent a moderator the
#: message "the report behind this decision is gone" for a report that was never involved -- telling
#: them data was lost when the real cause was unimplemented code. Here a key with no handler is a
#: KeyError at edit time.
_UNDO = {'blurb_hidden': _undo_blurb_hidden}


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
    undo = _UNDO.get(locked.action)
    if undo is None:
        raise ModerationError(f'{locked.get_action_display()} cannot be reversed automatically.')
    if locked.is_reversed:
        raise ModerationError('That decision has already been reversed.')

    report, changed = undo(locked, moderator, reason)

    reversal = ModerationAction.objects.create(
        actor=moderator, actor_label=_label(moderator), action='blurb_restored', reason=reason,
        blurb_report=report, reverses=locked,
        target_id=locked.target_id, target_label=locked.target_label,
        changed=changed,
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
