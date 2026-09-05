"""Moderator decisions, and the log that has to survive them.

The rule this module exists to hold: the CHANGE and its LOG ENTRY cannot come apart. Every action
goes through `moderation_service`, which writes both in one transaction, so there is no path where a
quick take gets hidden and nothing records who did it or why.

The other rule: a log entry has to still make sense after its target is gone. `blurb_report` and
`game_flag` are SET_NULL, so a deleted report would otherwise leave a row saying somebody did
something to nothing.
"""
import pytest

from tests.factories import ConceptFactory, GameFactory, ProfileFactory, UserFactory
from trophies.mixins import is_mod_or_admin
from trophies.models import BlurbReport, GameFlag, ModerationAction, UserConceptRating
from trophies.services import moderation_service as mod
from trophies.services.game_flag_service import GameFlagService

pytestmark = pytest.mark.django_db


def _moderator():
    user = UserFactory()
    user.role = 'moderator'
    user.save()
    return user


def _admin():
    user = UserFactory()
    user.role = 'admin'
    user.save()
    return user


def _reported_take(blurb='some words', reason='spam'):
    author = ProfileFactory(is_linked=True)
    reporter = ProfileFactory(is_linked=True)
    concept = ConceptFactory(unified_title='Hollow Knight')
    rating = UserConceptRating.objects.create(
        profile=author, concept=concept, concept_trophy_group=None,
        blurb=blurb, difficulty=5, grindiness=5, hours_to_platinum=20,
        fun_ranking=8, overall_rating=4.0,
    )
    return BlurbReport.objects.create(rating=rating, reporter=reporter, reason=reason)


def _flag(flag_type='delisted'):
    return GameFlag.objects.create(
        game=GameFactory(), reporter=ProfileFactory(is_linked=True), flag_type=flag_type)


# ── the reason is not optional ───────────────────────────────────────────────────────────────────

@pytest.mark.parametrize('bad', ['', '   ', 'x'])
def test_every_action_refuses_an_empty_reason(bad):
    """Enforced in the SERVICE, not a form. A form guarantees nothing about the next caller -- a
    management command, a shell, the admin dashboard that does not exist yet -- and a log of
    timestamps with no reasons answers "what happened" while leaving "why" unanswered, which is the
    only question an appeal actually asks."""
    moderator = _moderator()
    for call, arg in (
        (mod.hide_blurb, _reported_take()),
        (mod.dismiss_blurb_report, _reported_take()),
        (mod.approve_game_flag, _flag()),
        (mod.dismiss_game_flag, _flag()),
    ):
        with pytest.raises(mod.ModerationError):
            call(arg, moderator, bad)

    assert ModerationAction.objects.count() == 0, 'a refused action still wrote a log entry'


def test_a_refused_action_changes_nothing():
    """The transaction has to hold: a rejected reason must not leave the take hidden."""
    report = _reported_take()

    with pytest.raises(mod.ModerationError):
        mod.hide_blurb(report, _moderator(), '')

    report.rating.refresh_from_db()
    report.refresh_from_db()
    assert report.rating.blurb_hidden is False
    assert report.status == 'pending'


# ── quick takes ──────────────────────────────────────────────────────────────────────────────────

def test_hiding_a_take_hides_the_words_and_keeps_the_scores():
    """`blurb_hidden` is a separate field from the blurb for exactly this reason: hiding words a
    moderator objects to must not silently rewrite the game's numbers. The rating stays in every
    average it was already in."""
    report = _reported_take()
    moderator = _moderator()

    mod.hide_blurb(report, moderator, 'Slur in the text.')

    report.rating.refresh_from_db()
    assert report.rating.blurb_hidden is True
    assert report.rating.overall_rating == 4.0, 'the score was altered'
    assert report.rating.difficulty == 5
    report.refresh_from_db()
    assert report.status == 'action_taken'
    assert report.reviewed_by == moderator
    assert report.reviewed_at is not None


def test_the_log_records_who_why_and_what_changed():
    report = _reported_take(blurb='the offending words')
    moderator = _moderator()

    action = mod.hide_blurb(report, moderator, 'Harassment of another hunter.')

    assert action.actor == moderator
    assert action.actor_label, 'the actor name was not captured'
    assert action.reason == 'Harassment of another hunter.'
    assert action.changed['blurb_hidden'] == [False, True]
    assert 'Hollow Knight' in action.target_label


def test_the_hidden_text_is_kept_in_the_log():
    """The words are the EVIDENCE. An appeal cannot be judged from "a quick take was hidden", and
    the take itself may be edited or deleted afterwards."""
    report = _reported_take(blurb='the offending words')

    action = mod.hide_blurb(report, _moderator(), 'Inappropriate.')

    assert action.evidence['blurb'] == 'the offending words'
    assert 'blurb' not in action.changed, 'the blurb was not written, so it is not a diff row'


def test_dismissing_a_report_leaves_the_take_alone():
    report = _reported_take()

    mod.dismiss_blurb_report(report, _moderator(), 'Take is fine; report looks retaliatory.')

    report.rating.refresh_from_db()
    report.refresh_from_db()
    assert report.rating.blurb_hidden is False, 'a dismissal hid the take'
    assert report.status == 'dismissed'


# ── game flags ───────────────────────────────────────────────────────────────────────────────────

def test_approving_a_flag_applies_the_games_change_and_logs_it():
    flag = _flag('delisted')

    action = mod.approve_game_flag(flag, _moderator(), 'Confirmed removed from the store.')

    flag.game.refresh_from_db()
    assert flag.game.is_delisted is True
    assert action.changed['is_delisted'] == [False, True]


def test_the_shovelware_lock_is_recorded_because_it_overrides_the_classifier():
    """The heaviest thing a moderator can do here: `shovelware_lock` permanently overrides the
    automated classifier, so the log has to show it was set and by whom."""
    flag = _flag('is_shovelware')

    action = mod.approve_game_flag(flag, _moderator(), 'Asset flip, 12 minutes long.')

    flag.game.refresh_from_db()
    assert flag.game.shovelware_lock is True
    assert action.changed['shovelware_lock'][1] is True
    assert action.changed['shovelware_status'][1] == 'manually_flagged'


def test_an_approval_that_changes_nothing_is_still_a_real_outcome():
    """`missing_vr` and `region_incorrect` are upheld for a human to act on -- they write no field.
    An empty `changed` says exactly that, and must not be mistaken for a failed write."""
    flag = _flag('missing_vr')

    action = mod.approve_game_flag(flag, _moderator(), 'Confirmed, PSVR2 supported.')

    assert action.changed == {}
    assert action.action == 'game_flag_approved'


# ── reversal ─────────────────────────────────────────────────────────────────────────────────────

def test_reversing_restores_the_take_and_is_logged_as_its_own_action():
    """Never by editing or deleting the original. An audit trail that can be rewritten is not one,
    and "who reversed this, and why" is the question asked when a decision is disputed."""
    report = _reported_take()
    original = mod.hide_blurb(report, _moderator(), 'Looked like harassment.')

    admin = UserFactory()
    admin.role = 'admin'
    admin.save()
    reversal = mod.reverse_action(original, admin, 'On review, quoting not endorsing.')

    report.rating.refresh_from_db()
    assert report.rating.blurb_hidden is False
    assert reversal.reverses_id == original.id
    assert reversal.action == 'blurb_restored'
    original.refresh_from_db()
    assert original.is_reversed is True
    assert ModerationAction.objects.count() == 2, 'the original was rewritten instead of added to'


def test_a_decision_cannot_be_reversed_twice():
    report = _reported_take()
    original = mod.hide_blurb(report, _moderator(), 'Hidden.')
    mod.reverse_action(original, _moderator(), 'Restored.')

    with pytest.raises(mod.ModerationError):
        mod.reverse_action(original, _moderator(), 'Restored again.')


def test_a_dismissal_reopens_the_report():
    """REVERSES an earlier decision of this suite's, deliberately.

    This used to assert that a dismissal could not be undone, reasoning that "it changed nothing to
    put back" and that reopening is a queue operation rather than an undo. That was wrong on its own
    terms: a dismissal changed the report's status and took it out of the queue, and putting both
    back is exactly an undo. The `changed` diff it writes is real, not ceremonial.

    What made the old rule look right was that `_UNDO` had one entry, so "not reversible" and "not
    implemented" were the same sentence.
    """
    report = _reported_take()
    dismissal = mod.dismiss_blurb_report(report, _moderator(), 'Fine.')

    reversal = mod.reverse_action(dismissal, _moderator(), 'On reflection it is not fine.')

    report.refresh_from_db()
    assert report.status == 'pending', 'the report did not go back into the queue'
    assert report.reviewed_by is None, 'a pending report still named who dismissed it'
    assert reversal.action == 'blurb_report_reopened'
    assert reversal.changed == {'status': ['dismissed', 'pending']}


def test_a_reversal_cannot_itself_be_reversed():
    """Undoing an undo is re-deciding, and it should be done as a decision -- on the record, with
    its own reason -- rather than by walking backwards up a chain of entries."""
    report = _reported_take()
    original = mod.hide_blurb(report, _moderator(), 'Hidden.')
    reversal = mod.reverse_action(original, _moderator(), 'Restored.')

    with pytest.raises(mod.ModerationError) as refused:
        mod.reverse_action(reversal, _moderator(), 'Hide it again.')

    assert 'itself a reversal' in str(refused.value), (
        'the message read as an unimplemented feature rather than a rule')


# ── the log outlives its target ──────────────────────────────────────────────────────────────────

def test_the_entry_still_reads_after_the_report_is_deleted():
    """SET_NULL, so a purge of old reports must not turn the audit trail into rows that say somebody
    did something to nothing."""
    report = _reported_take()
    action = mod.hide_blurb(report, _moderator(), 'Inappropriate.')

    report.delete()

    action.refresh_from_db()
    assert action.blurb_report is None
    assert 'Hollow Knight' in action.target_label, 'the entry lost what it was about'
    assert action.reason == 'Inappropriate.'
    assert str(action), '__str__ must not raise on a null target'


def test_the_entry_still_names_the_actor_after_the_account_is_deleted():
    report = _reported_take()
    moderator = _moderator()
    action = mod.hide_blurb(report, moderator, 'Inappropriate.')

    moderator.delete()

    action.refresh_from_db()
    assert action.actor is None
    assert action.actor_label, 'the log forgot who did it'


# ── the gate ─────────────────────────────────────────────────────────────────────────────────────

def test_the_gate_admits_moderators_and_admins_only():
    from django.contrib.auth.models import AnonymousUser

    plain = UserFactory()
    moderator = _moderator()
    admin = UserFactory()
    admin.role = 'admin'
    admin.save()

    assert is_mod_or_admin(moderator) is True
    assert is_mod_or_admin(admin) is True, 'admins must reach the mod tools'
    assert is_mod_or_admin(plain) is False
    assert is_mod_or_admin(AnonymousUser()) is False
    assert is_mod_or_admin(None) is False


def test_a_superuser_is_admitted_even_with_no_role():
    """Superusers carry no `role` at all, so a `role == 'admin'` gate would lock out the one account
    most likely to be asked to fix the tools."""
    root = UserFactory()
    root.is_superuser = True
    root.is_staff = True
    root.save()

    assert is_mod_or_admin(root) is True


# ── the log must say what ACTUALLY happened ──────────────────────────────────────────────────────
# Applying the change and logging it atomically is not enough on its own. Without a status
# precondition, a second moderator succeeds and writes an entry claiming a change they did not make
# -- which for an appeal record is worse than no entry, because it is misleading evidence.

def test_a_second_moderator_cannot_action_a_handled_report():
    report = _reported_take()
    first = _moderator()
    mod.hide_blurb(report, first, 'Harassment.')

    second = _moderator()
    with pytest.raises(mod.ModerationError, match='Already handled'):
        mod.hide_blurb(report, second, 'Also harassment.')

    report.refresh_from_db()
    assert report.reviewed_by == first, 'the second moderator overwrote who decided'
    assert ModerationAction.objects.count() == 1, 'a second, false entry was written'


def test_a_handled_report_cannot_then_be_dismissed():
    report = _reported_take()
    mod.hide_blurb(report, _moderator(), 'Harassment.')

    with pytest.raises(mod.ModerationError, match='Already handled'):
        mod.dismiss_blurb_report(report, _moderator(), 'Actually fine.')

    report.rating.refresh_from_db()
    assert report.rating.blurb_hidden is True, 'the take was un-hidden by a refused dismissal'


def test_a_handled_flag_cannot_be_actioned_twice():
    """Two mods approving `delisted` and `not_delisted` on one game would otherwise both log that
    they made the change, and one of them would be lying."""
    flag = _flag('delisted')
    mod.approve_game_flag(flag, _moderator(), 'Confirmed.')

    with pytest.raises(mod.ModerationError, match='Already handled'):
        mod.dismiss_game_flag(flag, _moderator(), 'Not confirmed.')

    assert ModerationAction.objects.count() == 1


def test_a_dismissal_records_the_status_it_actually_came_from():
    """The first cut hardcoded the pair without reading the row, so a report already in another
    state logged a transition that never occurred."""
    report = _reported_take()

    action = mod.dismiss_blurb_report(report, _moderator(), 'Report is baseless.')

    assert action.changed['status'] == ['pending', 'dismissed']


# ── the anti-drift guard, in the direction that actually fails ───────────────────────────────────

@pytest.mark.parametrize('flag_type', [c[0] for c in GameFlag.FLAG_TYPES])
def test_every_flag_type_lands_in_the_log(flag_type):
    """THE replacement for deriving the field list by regex over another function's source.

    Approves every flag type, compares the Game row before and after by real inspection, and fails
    when a field the database actually changed is missing from `changed`. That is the direction
    that matters: a missing field is silent (the approval logs "changed nothing"), an extra one is
    loud. The old test only checked that derived names were real Game fields -- the harmless
    direction -- so shrinking the derivation left the whole suite green.
    """
    from trophies.models import Game

    flag = _flag(flag_type)
    tracked = [f.name for f in Game._meta.get_fields()
               if not f.is_relation and f.name not in ('id', 'updated_at', 'shovelware_updated_at')]
    before = {f: getattr(flag.game, f) for f in tracked}

    action = mod.approve_game_flag(flag, _moderator(), 'Confirmed ' + flag_type + '.')

    flag.game.refresh_from_db()
    really_changed = {f for f in tracked if getattr(flag.game, f) != before[f]}
    missing = really_changed - set(action.changed)
    assert not missing, (
        flag_type + ' changed ' + str(sorted(missing)) + ' on the Game and the log does not '
        'mention it -- GameFlagService.WATCHED_FIELDS is out of step with what approve_flag writes'
    )


# ── the log survives the database round trip ─────────────────────────────────────────────────────

def test_the_diff_survives_being_written_and_read_back():
    """Every other assertion here reads the in-memory object the service returned. A value JSONField
    cannot round-trip would pass all of them and still be wrong in the table."""
    flag = _flag('is_shovelware')
    action = mod.approve_game_flag(flag, _moderator(), 'Asset flip.')

    reloaded = ModerationAction.objects.get(pk=action.pk)

    assert reloaded.changed == action.changed
    assert reloaded.changed['shovelware_lock'] == [False, True]
    assert isinstance(reloaded.changed['shovelware_status'][1], str)


def test_the_target_id_outlives_the_label():
    """`target_label` is truncated at 255 and two long-titled games can collide, so the PK is the
    only durable identification once the report is purged."""
    report = _reported_take()
    rating_id = report.rating_id
    action = mod.hide_blurb(report, _moderator(), 'Inappropriate.')

    report.delete()

    action.refresh_from_db()
    assert action.blurb_report is None
    assert action.target_id == rating_id


# ── reversal reads the log rather than guessing ──────────────────────────────────────────────────

def test_reversal_restores_what_the_log_recorded_not_a_hardcoded_default():
    """`changed` is documented as the thing that makes reversal possible without guessing. The first
    cut hardcoded False anyway -- which for a take that was ALREADY hidden when actioned would have
    un-hidden it and called that a restoration."""
    report = _reported_take()
    action = mod.hide_blurb(report, _moderator(), 'Hidden.')
    # Rewrite the log to describe a take that was already hidden before the action.
    action.changed = {'blurb_hidden': [True, True]}
    action.save(update_fields=['changed'])

    mod.reverse_action(action, _moderator(), 'Undo.')

    report.rating.refresh_from_db()
    assert report.rating.blurb_hidden is True, 'the reversal invented a previous state of False'


def test_reversing_hands_the_standing_decision_to_whoever_reversed_it():
    """Leaving `reviewed_by` on the overturned moderator credits the report to a decision that no
    longer stands."""
    report = _reported_take()
    first = _moderator()
    action = mod.hide_blurb(report, first, 'Hidden.')

    admin = _admin()
    mod.reverse_action(action, admin, 'Overturned on review.')

    report.refresh_from_db()
    assert report.reviewed_by == admin


def test_a_hide_can_be_reversed_after_its_report_is_purged():
    """REVERSES an earlier decision of this file's, which pinned the opposite.

    It used to assert that a purged report made the reversal refuse, and called that honest. It was
    not: `blurb_report` is SET_NULL precisely so an entry outlives its report, and refusing left the
    take hidden with no way back through the log -- the same "traceable to nobody" failure
    `subject_user` was added to fix, still sitting on the reversal path.

    `target_id` has held the rating's pk since the log was built, and the proactive undo already
    resolved a rating through it. There was never a reason for the two paths to differ.
    """
    report = _reported_take()
    action = mod.hide_blurb(report, _moderator(), 'Hidden.')
    rating = report.rating
    report.delete()
    action.refresh_from_db()
    assert action.blurb_report is None, 'the report did not actually go'

    mod.reverse_action(action, _admin(), 'On appeal.')

    rating.refresh_from_db()
    assert rating.blurb_hidden is False, 'the take stayed hidden with no route back'


def test_a_reversal_whose_take_is_gone_too_says_so_honestly():
    """The genuinely unrecoverable case: nothing left to put back, and saying so beats a traceback
    or a silent no-op."""
    report = _reported_take()
    action = mod.hide_blurb(report, _moderator(), 'Hidden.')
    rating_pk = report.rating.pk
    report.delete()
    UserConceptRating.objects.filter(pk=rating_pk).delete()
    action.refresh_from_db()

    with pytest.raises(mod.ModerationError, match='no longer exists'):
        mod.reverse_action(action, _admin(), 'Undo.')


def test_only_one_reversal_can_exist_per_decision_even_without_the_service():
    """The service takes a row lock; this is the DATABASE backstop, which is what holds if anything
    ever inserts without going through it."""
    from django.db import IntegrityError, transaction as db_transaction

    report = _reported_take()
    original = mod.hide_blurb(report, _moderator(), 'Hidden.')
    mod.reverse_action(original, _moderator(), 'Undo.')

    with pytest.raises(IntegrityError):
        with db_transaction.atomic():
            ModerationAction.objects.create(
                actor=_moderator(), action='blurb_restored', reason='second reversal',
                reverses=original)


# ── the mixin itself, not just the function ──────────────────────────────────────────────────────

def test_the_mixin_gates_the_url_it_is_put_on(rf):
    """The FUNCTION is tested above; this is the thing that will actually guard a URL that mutates
    `shovelware_lock`. Exercised through a real view rather than by reading the source."""
    from django.contrib.auth.models import AnonymousUser
    from django.http import HttpResponse
    from django.views.generic import View

    from trophies.mixins import ModeratorRequiredMixin

    class _Guarded(ModeratorRequiredMixin, View):
        def get(self, request, *args, **kwargs):
            return HttpResponse('ok')

    view = _Guarded.as_view()

    anon = rf.get('/mod/')
    anon.user = AnonymousUser()
    assert view(anon).status_code == 302, 'anonymous must be sent to login'

    plain = rf.get('/mod/')
    plain.user = UserFactory()
    resp = view(plain)
    assert resp.status_code == 302 and resp.url == '/', 'a hunter must be redirected home'

    for user in (_moderator(), _admin()):
        req = rf.get('/mod/')
        req.user = user
        assert view(req).status_code == 200, 'a ' + user.role + ' was refused'


def test_a_deactivated_moderator_loses_access():
    """Revoking access is precisely the moment the gate must not still say yes."""
    moderator = _moderator()
    moderator.is_active = False
    moderator.save()

    assert is_mod_or_admin(moderator) is False


# ── who an entry is evidence ABOUT ───────────────────────────────────────────────────────────────
#
# `subject_user` is not "the owner of the thing acted on". It is the hunter whose BEHAVIOUR the entry
# is evidence about, and those differ for half the actions. One settled rule, or the column means two
# things and the per-person history is wrong for both.

def test_hiding_a_take_records_its_author_as_the_subject():
    report = _reported_take()
    author = report.rating.profile

    action = mod.hide_blurb(report, _moderator(), 'a slur')

    assert action.subject_user == author.user
    assert action.subject_label == author.user.display_name


def test_dismissing_a_report_records_the_REPORTER_not_the_author():
    """The one most likely to be got wrong. A dismissal says the report was wrong: that is evidence
    about the person who filed it, and none at all about the person they filed it against."""
    report = _reported_take()
    author, reporter = report.rating.profile, report.reporter
    assert author.user != reporter.user

    action = mod.dismiss_blurb_report(report, _moderator(), 'nothing wrong with it')

    assert action.subject_user == reporter.user
    assert action.subject_user != author.user, 'a dismissal was filed against the author'


@pytest.mark.parametrize('decide,expected', [
    (mod.approve_game_flag, 'game_flag_approved'),
    (mod.dismiss_game_flag, 'game_flag_dismissed'),
])
def test_a_flag_decision_records_the_reporter(decide, expected):
    """A game has no hunter behind it, so the only person a flag decision is evidence about is the
    one who raised it -- and "who reports well" is what the history is for."""
    flag = _flag()

    action = decide(flag, _moderator(), 'checked, correct')

    assert action.action == expected
    assert action.subject_user == flag.reporter.user


def test_a_reversal_is_evidence_about_the_same_person():
    report = _reported_take()
    author = report.rating.profile
    original = mod.hide_blurb(report, _moderator(), 'a slur')

    reversal = mod.reverse_action(original, _moderator(), 'on appeal, it was fine')

    assert reversal.subject_user == original.subject_user == author.user
    assert reversal.subject_label == original.subject_label


def test_the_history_survives_the_report_being_purged():
    """THE reason this column exists. `blurb_report` is SET_NULL, so before it, purging a report
    left an entry that could not be traced to anybody -- losing exactly the old history an appeal is
    about."""
    report = _reported_take()
    author = report.rating.profile
    action = mod.hide_blurb(report, _moderator(), 'a slur')

    report.delete()
    action.refresh_from_db()

    assert action.blurb_report is None, 'the report did not actually go'
    assert action.subject_user == author.user, 'the entry lost the person it was about'
    assert action.subject_label


def test_one_query_answers_everything_about_one_hunter():
    """The claim that made a real FK worth having over anything cleverer."""
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    from trophies.models import ModerationAction

    report = _reported_take()
    author = report.rating.profile
    mod.hide_blurb(report, _moderator(), 'a slur')
    mod.dismiss_game_flag(_flag(), _moderator(), 'not delisted')

    with CaptureQueriesContext(connection) as captured:
        theirs = list(ModerationAction.objects.filter(subject_user=author.user))

    assert len(theirs) == 1
    assert len(captured.captured_queries) == 1


def test_a_subject_with_no_account_behind_the_profile_is_left_null():
    """Honest rather than clever. A profile with no user has nobody to name, and inventing a label
    would put a name on an entry that never had one."""
    report = _reported_take()
    report.rating.profile.user = None
    report.rating.profile.save(update_fields=['user'])
    report.rating.profile.refresh_from_db()

    action = mod.hide_blurb(report, _moderator(), 'a slur')

    assert action.subject_user is None
    assert action.subject_label == ''


# ── reversing a flag decision ────────────────────────────────────────────────────────────────────

def test_reversing_an_approval_puts_the_games_field_back():
    flag = _flag('delisted')
    approval = mod.approve_game_flag(flag, _moderator(), 'Confirmed delisted.')
    flag.game.refresh_from_db()
    assert flag.game.is_delisted is True

    reversal = mod.reverse_action(approval, _admin(), 'Store listing is live again.')

    flag.game.refresh_from_db()
    flag.refresh_from_db()
    assert flag.game.is_delisted is False, 'the approval was not undone'
    assert flag.status == 'pending', 'the flag did not go back into the queue'
    assert reversal.action == 'game_flag_reversed'
    assert reversal.changed['is_delisted'] == [True, False]


def test_reversing_an_approval_does_NOT_clobber_a_later_change():
    """THE trap. `changed` records values at DECISION time, and months can pass before a reversal --
    the field may have moved since, by a sync, another flag, or a person. Writing the old value back
    blindly would discard a legitimate later edit and call it a restoration.
    """
    flag = _flag('delisted')
    approval = mod.approve_game_flag(flag, _moderator(), 'Confirmed delisted.')

    # Somebody puts the game back on sale, months later, for reasons of their own.
    flag.game.refresh_from_db()
    flag.game.is_delisted = False
    flag.game.save(update_fields=['is_delisted'])

    reversal = mod.reverse_action(approval, _admin(), 'The original call was wrong.')

    flag.game.refresh_from_db()
    assert flag.game.is_delisted is False, 'the later change survived, as it must'
    assert 'is_delisted' not in reversal.changed, 'the reversal claimed a write it did not make'
    skipped = reversal.evidence['not_restored']['is_delisted']
    assert skipped['expected'] is True and skipped['found'] is False
    assert skipped['would_have_written'] is False


def test_a_partly_applied_reversal_says_so_rather_than_going_quiet():
    """A reversal that did three quarters of its job silently is worse than one that reports it: the
    admin walks away believing the game is back as it was."""
    flag = _flag('is_shovelware')
    approval = mod.approve_game_flag(flag, _moderator(), 'Asset flip.')

    # One of the two fields it wrote moves on; the other does not.
    flag.game.refresh_from_db()
    flag.game.shovelware_status = 'auto_flagged'
    flag.game.save(update_fields=['shovelware_status'])

    reversal = mod.reverse_action(approval, _admin(), 'Misjudged, it is a real game.')

    flag.game.refresh_from_db()
    assert flag.game.shovelware_lock is False, 'the lock was not lifted'
    assert flag.game.shovelware_status == 'auto_flagged', 'the later status change was clobbered'
    assert 'shovelware_lock' in reversal.changed
    assert 'shovelware_status' in reversal.evidence['not_restored']


def test_reversing_a_dismissed_flag_reopens_it():
    flag = _flag('unobtainable')
    dismissal = mod.dismiss_game_flag(flag, _moderator(), 'Trophies look fine.')

    reversal = mod.reverse_action(dismissal, _admin(), 'Three more reports since.')

    flag.refresh_from_db()
    assert flag.status == 'pending'
    assert flag.reviewed_by is None
    assert reversal.action == 'game_flag_reopened'


def test_reversing_a_no_op_approval_only_reopens_the_flag():
    """`missing_vr` writes no field, so there is nothing to put back -- and the reversal must not
    invent a diff to look busy."""
    flag = _flag('missing_vr')
    approval = mod.approve_game_flag(flag, _moderator(), 'Confirmed, PSVR2.')

    reversal = mod.reverse_action(approval, _admin(), 'Wrong game.')

    flag.refresh_from_db()
    assert flag.status == 'pending'
    assert reversal.changed == {'status': ['approved', 'pending']}
    assert reversal.evidence == {}


@pytest.mark.parametrize('decide,undo_name', [
    (mod.approve_game_flag, 'game_flag_reversed'),
    (mod.dismiss_game_flag, 'game_flag_reopened'),
])
def test_every_flag_reversal_is_its_own_entry_not_an_edit(decide, undo_name):
    flag = _flag()
    original = decide(flag, _moderator(), 'A decision.')

    reversal = mod.reverse_action(original, _admin(), 'A different view.')

    original.refresh_from_db()
    assert ModerationAction.objects.count() == 2, 'the original was rewritten instead of added to'
    assert reversal.reverses_id == original.pk
    assert reversal.action == undo_name
    assert original.is_reversed is True


def test_reversing_a_flag_decision_needs_a_reason_like_any_other():
    flag = _flag()
    approval = mod.approve_game_flag(flag, _moderator(), 'Confirmed.')

    with pytest.raises(mod.ModerationError):
        mod.reverse_action(approval, _admin(), '  ')

    flag.refresh_from_db()
    assert flag.status == 'approved', 'a refused reversal still reopened the flag'
    assert ModerationAction.objects.count() == 1


def test_a_flag_decision_cannot_be_reversed_twice():
    flag = _flag()
    approval = mod.approve_game_flag(flag, _moderator(), 'Confirmed.')
    mod.reverse_action(approval, _admin(), 'Wrong.')

    with pytest.raises(mod.ModerationError):
        mod.reverse_action(approval, _admin(), 'Wrong again.')


def test_a_reversal_whose_flag_is_gone_says_so_honestly():
    """`game_flag` is SET_NULL, so the entry outlives the flag -- but the undo needs the flag, and
    "cannot be undone here" beats a traceback or a silent no-op."""
    flag = _flag()
    approval = mod.approve_game_flag(flag, _moderator(), 'Confirmed.')
    flag.delete()
    approval.refresh_from_db()

    with pytest.raises(mod.ModerationError) as refused:
        mod.reverse_action(approval, _admin(), 'Undo it.')

    assert 'deleted' in str(refused.value)


def test_every_action_that_can_be_reversed_names_its_reversal():
    """The map is (callable, name) pairs because the reversal's own `action` used to be hardcoded to
    `blurb_restored` -- so the moment a second undo existed, reopening a game flag would have been
    logged as a quick take being restored."""
    valid = {choice for choice, _label in ModerationAction.ACTIONS}

    for decided, (undo, reversal_action) in mod._UNDO.items():
        assert decided in valid, f'{decided} is not a real action'
        assert reversal_action in valid, f'{reversal_action} is not a real action'
        assert callable(undo)
        assert reversal_action != decided, f'{decided} logs its reversal as itself'


def test_every_decision_the_service_makes_can_be_reversed():
    """The owner asked for "reverse any decision". A decision the log records but cannot undo is a
    gap that only shows up the day somebody needs it."""
    decisions = {'blurb_hidden', 'blurb_report_dismissed', 'game_flag_approved',
                 'game_flag_dismissed'}

    assert decisions <= set(mod._UNDO), f'no undo for {decisions - set(mod._UNDO)}'


# ── what the audit of P2 found ───────────────────────────────────────────────────────────────────

def test_a_second_report_on_an_already_hidden_take_claims_no_change():
    """`_lock_report` preconditions on the REPORT's status, which says nothing about the take. So a
    second report against a take that is already hidden used to log `blurb_hidden: [True, True]` --
    an entry claiming a change that did not happen, which this module calls affirmatively misleading
    evidence and which `_lock_rating` was written to prevent on the other path only."""
    report = _reported_take()
    mod.hide_blurb(report, _moderator(), 'First.')
    second = BlurbReport.objects.create(
        rating=report.rating, reporter=ProfileFactory(is_linked=True), reason='spam')

    action = mod.hide_blurb(second, _moderator(), 'Second report, same take.')

    assert action.changed == {}, 'the log claimed a write that did not happen'
    assert action.evidence.get('already_hidden') is True, 'the log does not say why it wrote nothing'
    report.rating.refresh_from_db()
    assert report.rating.blurb_hidden is True


def test_reversing_only_your_own_hide_does_not_undo_somebody_elses():
    """The current-value rule, applied to the blurb undos as well as the flag one. Two decisions can
    land on one take; reversing yours must not quietly lift theirs."""
    profile = ProfileFactory(is_linked=True)
    concept = ConceptFactory()
    rating = UserConceptRating.objects.create(
        profile=profile, concept=concept, concept_trophy_group=None, blurb='words',
        difficulty=5, grindiness=5, hours_to_platinum=20, fun_ranking=8, overall_rating=4.0)
    proactive = mod.hide_blurb_without_a_report(rating, _admin(), 'Went looking.')
    queue_report = BlurbReport.objects.create(
        rating=rating, reporter=ProfileFactory(is_linked=True), reason='spam')
    mod.hide_blurb(queue_report, _moderator(), 'And a hunter reported it too.')

    reversal = mod.reverse_action(proactive, _admin(), 'My call was wrong.')

    rating.refresh_from_db()
    assert rating.blurb_hidden is True, "reversing one entry lifted somebody else's decision"
    assert reversal.changed == {}
    assert 'blurb_hidden' in reversal.evidence['not_restored']


def test_reopening_a_flag_refuses_when_the_same_one_is_already_waiting():
    """`submit_flag` dedups on `status='pending'` and lets a reporter file again once a flag is
    decided -- so reopening the old one puts two identical pending rows in the queue, which no DB
    constraint catches. A moderator then sees the same complaint twice."""
    flag = _flag('delisted')
    dismissal = mod.dismiss_game_flag(flag, _moderator(), 'Looks fine.')
    refiled, error = GameFlagService.submit_flag(flag.game, flag.reporter, 'delisted', 'again')
    assert error is None and refiled.pk != flag.pk

    with pytest.raises(mod.ModerationError, match='already filed this flag again'):
        mod.reverse_action(dismissal, _admin(), 'Actually they were right.')

    assert GameFlag.objects.filter(status='pending').count() == 1, 'the queue has a duplicate'


def test_reopening_is_allowed_when_nothing_duplicate_is_waiting():
    flag = _flag('delisted')
    dismissal = mod.dismiss_game_flag(flag, _moderator(), 'Looks fine.')

    mod.reverse_action(dismissal, _admin(), 'Actually they were right.')

    flag.refresh_from_db()
    assert flag.status == 'pending'


def test_the_two_hides_reverse_to_different_names():
    """Both reversing to `blurb_restored` re-created the ambiguity `blurb_hidden_proactive` exists to
    remove: a restored row with a null report would be indistinguishable between "undid a proactive
    hide" and "undid a queue hide whose report was purged"."""
    names = {decided: reversal for decided, (_undo, reversal) in mod._UNDO.items()}

    assert names['blurb_hidden'] != names['blurb_hidden_proactive']


def test_the_flag_undo_locks_the_row_it_compares():
    """A guard that is not serialised is a guard with a window in it: the undo read the game,
    compared, and blind-wrote, so a sync writing in between was silently overwritten with no
    `not_restored` warning -- the exact failure the guard exists to prevent."""
    import inspect

    source = inspect.getsource(mod._flag_behind) + inspect.getsource(mod._undo_game_flag_approved)

    # `.select_for_update()` with the call parens, not the bare name: both functions DISCUSS the lock
    # in their docstrings, so counting the word found two of them with the lock itself removed.
    assert source.count('.select_for_update()') >= 2, (
        'the flag undo compares and writes without locking both the flag and the game')

