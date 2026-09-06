"""Django-admin bulk sweeps, routed through the moderation service.

These actions carry the same two decisions the Mod Center queues do, and used to apply them with
`queryset.update()` and a direct service call: same writes, no audit entry, no reason, and no status
precondition -- so a sweep could overwrite a decision somebody had just made and leave no record.

The collision that shapes all of this: the service requires a REASON, and a Django action has nowhere
to type one. A hardcoded string would satisfy the check and produce exactly the log it exists to
prevent, so the actions render a confirmation page instead.

Only the OWNER can reach any of it (`is_superuser`), which is asserted in
`test_django_admin_is_the_owners.py` -- these tests are about what happens once they are in.
"""
import pytest
from django.urls import reverse

from tests.factories import (ConceptFactory, GameFactory, ProfileFactory, UserFactory)
from trophies.models import BlurbReport, GameFlag, ModerationAction, UserConceptRating

pytestmark = pytest.mark.django_db

FLAG_CHANGELIST = '/admin/trophies/gameflag/'
REPORT_CHANGELIST = '/admin/trophies/blurbreport/'


def _owner():
    user = UserFactory()
    user.is_superuser = user.is_staff = True
    user.save()
    return user


def _flag(flag_type='delisted'):
    return GameFlag.objects.create(
        game=GameFactory(), reporter=ProfileFactory(is_linked=True), flag_type=flag_type)


def _report():
    concept = ConceptFactory(unified_title='Hollow Knight')
    rating = UserConceptRating.objects.create(
        profile=ProfileFactory(is_linked=True), concept=concept, concept_trophy_group=None,
        blurb='some words', difficulty=5, grindiness=5, hours_to_platinum=20,
        fun_ranking=8, overall_rating=4.0)
    return BlurbReport.objects.create(
        rating=rating, reporter=ProfileFactory(is_linked=True), reason='harassment')


def _sweep(client, url, action, rows, reason=None):
    """Run a bulk action. Without `reason` this is the first click, which should stop and ask."""
    data = {'action': action, '_selected_action': [str(r.pk) for r in rows]}
    if reason is not None:
        data['reason'] = reason
        data['_reasoned_confirm'] = '1'
    return client.post(url, data, follow=True)


# ── the confirmation page ────────────────────────────────────────────────────────────────────────

def test_a_sweep_stops_and_asks_why(client):
    """The first click decides nothing. A hardcoded reason would have satisfied the service and
    produced exactly the log the reason requirement exists to prevent."""
    flag = _flag()
    client.force_login(_owner())

    resp = _sweep(client, FLAG_CHANGELIST, 'approve_selected', [flag])

    assert 'Why?' in resp.content.decode()
    flag.refresh_from_db()
    assert flag.status == 'pending', 'the first click already applied the change'
    assert ModerationAction.objects.count() == 0


def test_the_page_lists_what_is_about_to_be_decided(client):
    """A count is not a review. The point of stopping is that somebody reads their selection."""
    flags = [_flag(), _flag()]
    client.force_login(_owner())

    body = _sweep(client, FLAG_CHANGELIST, 'approve_selected', flags).content.decode()

    for flag in flags:
        assert str(flag) in body


def test_a_sweep_with_no_reason_is_refused_and_changes_nothing(client):
    flag = _flag()
    client.force_login(_owner())

    resp = _sweep(client, FLAG_CHANGELIST, 'approve_selected', [flag], reason='  ')

    assert 'A reason is required' in resp.content.decode()
    flag.refresh_from_db()
    assert flag.status == 'pending'
    assert ModerationAction.objects.count() == 0


# ── what a confirmed sweep does ──────────────────────────────────────────────────────────────────

def test_a_confirmed_sweep_writes_one_logged_entry_per_row(client):
    """The whole point. These wrote nothing before."""
    flags = [_flag(), _flag(), _flag()]
    owner = _owner()
    client.force_login(owner)

    _sweep(client, FLAG_CHANGELIST, 'approve_selected', flags, reason='confirmed delisted, all three')

    assert ModerationAction.objects.count() == 3
    for entry in ModerationAction.objects.all():
        assert entry.actor == owner
        assert entry.reason == 'confirmed delisted, all three'
        assert entry.action == 'game_flag_approved'
    for flag in flags:
        flag.game.refresh_from_db()
        assert flag.game.is_delisted is True, 'the sweep logged without applying'


def test_a_swept_hide_records_the_words_as_evidence_like_the_queue_does(client):
    """Routed through the same service, so it gets the same entry -- including the blurb, which is
    what an appeal is judged on and which a `queryset.update()` never captured."""
    report = _report()
    client.force_login(_owner())

    _sweep(client, REPORT_CHANGELIST, 'hide_blurb_and_resolve', [report], reason='slur')

    report.rating.refresh_from_db()
    assert report.rating.blurb_hidden is True
    entry = ModerationAction.objects.get()
    assert entry.evidence['blurb'] == 'some words'
    assert entry.subject_user == report.rating.profile.user


def test_a_sweep_skips_what_somebody_else_already_handled(client):
    """The precondition the old bulk actions had no way to check: `queryset.update()` overwrites a
    decision made a minute ago and leaves no sign it happened."""
    from trophies.services import moderation_service

    mine, theirs = _flag(), _flag()
    moderator = UserFactory()
    moderator.role = 'moderator'
    moderator.save()
    moderation_service.dismiss_game_flag(theirs, moderator, 'got there first')
    client.force_login(_owner())

    _sweep(client, FLAG_CHANGELIST, 'approve_selected', [mine, theirs], reason='sweeping')

    theirs.refresh_from_db()
    assert theirs.status == 'dismissed', "a sweep overwrote somebody else's decision"
    mine.refresh_from_db()
    assert mine.status == 'approved'
    assert ModerationAction.objects.count() == 2, 'one dismissal, one approval'


def test_one_refusal_does_not_roll_back_the_rest(client):
    """Each row is its own transaction. A sweep of forty should not be undone by the one a colleague
    handled a minute ago -- and being refused is an ORDINARY outcome on a queue two people work."""
    from trophies.services import moderation_service

    flags = [_flag() for _ in range(4)]
    moderator = UserFactory()
    moderator.role = 'moderator'
    moderator.save()
    moderation_service.dismiss_game_flag(flags[1], moderator, 'got there first')
    client.force_login(_owner())

    _sweep(client, FLAG_CHANGELIST, 'approve_selected', flags, reason='sweeping the rest')

    approved = GameFlag.objects.filter(status='approved').count()
    assert approved == 3, 'one refusal rolled back the whole sweep'


def test_the_sweep_says_how_many_it_could_not_do(client):
    from trophies.services import moderation_service

    mine, theirs = _flag(), _flag()
    moderator = UserFactory()
    moderator.role = 'moderator'
    moderator.save()
    moderation_service.dismiss_game_flag(theirs, moderator, 'got there first')
    client.force_login(_owner())

    body = _sweep(client, FLAG_CHANGELIST, 'approve_selected', [mine, theirs],
                  reason='sweeping').content.decode()

    assert '1 of 2' in body or 'Already handled' in body, (
        'the sweep did not report what it skipped'
    )


def test_a_swept_dismissal_is_logged_too(client):
    report = _report()
    client.force_login(_owner())

    _sweep(client, REPORT_CHANGELIST, 'mark_as_dismissed', [report], reason='report is retaliatory')

    report.refresh_from_db()
    assert report.status == 'dismissed'
    entry = ModerationAction.objects.get()
    assert entry.action == 'blurb_report_dismissed'
    assert entry.subject_user == report.reporter.user, 'a dismissal is evidence about the reporter'


# ── the action that was removed rather than rerouted ─────────────────────────────────────────────

def test_there_is_no_bulk_unhide(client):
    """`unhide_blurb` was a bare `queryset.update(blurb_hidden=False)` -- a reversal with no record
    that anything was reversed, which is the one thing this log exists to make impossible.

    Un-hiding is `/staff/decisions/`, which finds the decision, undoes what it actually did, and
    writes an entry pointing at it. There is deliberately no bulk equivalent: reversing forty
    decisions for one reason is not something anybody should be able to do quickly.
    """
    from trophies.admin import BlurbReportAdmin

    assert 'unhide_blurb' not in BlurbReportAdmin.actions
    assert not hasattr(BlurbReportAdmin, 'unhide_blurb')
