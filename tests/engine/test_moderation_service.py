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

pytestmark = pytest.mark.django_db


def _moderator():
    user = UserFactory()
    user.role = 'moderator'
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

    assert 'the offending words' in action.changed['blurb']


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


def test_the_watched_field_list_is_derived_from_the_service_that_writes_them():
    """Hand-listing them is how the log goes quietly wrong: a misremembered field name does not
    raise, it just never appears in `changed`, so an approval that changed something logs as
    "changed nothing". Every derived name must be a real Game field."""
    from trophies.models import Game
    from trophies.services.moderation_service import _WATCHED_GAME_FIELDS

    real = {f.name for f in Game._meta.get_fields()}
    assert _WATCHED_GAME_FIELDS, 'the derivation found no fields at all'
    assert not [f for f in _WATCHED_GAME_FIELDS if f not in real]
    # The two the flag map actually keys on most heavily.
    assert 'shovelware_lock' in _WATCHED_GAME_FIELDS
    assert 'is_delisted' in _WATCHED_GAME_FIELDS


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


def test_a_dismissal_is_not_reversible_from_the_log():
    """It changed nothing to put back. Re-opening a report is a queue operation, not an undo, and
    pretending otherwise would write a reversal entry that reverses nothing."""
    report = _reported_take()
    action = mod.dismiss_blurb_report(report, _moderator(), 'Fine.')

    with pytest.raises(mod.ModerationError):
        mod.reverse_action(action, _moderator(), 'Changed my mind.')


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
    assert 'the log entry' not in str(action)   # __str__ must not raise on a null target
    assert str(action)


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
