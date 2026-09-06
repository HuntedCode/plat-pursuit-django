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


def _submit_the_confirm_page(client, url, action, rows, reason):
    """Click through the confirmation page by POSTING THE FORM IT RENDERED.

    Hand-building the second POST -- which the other tests here do, for brevity -- leaves the page's
    own hidden inputs completely untested: delete the `action` field from the template and all of
    them still pass, while a browser gets Django's "No action selected." and the sweep silently does
    nothing. This one parses the rendered form and submits exactly what it contains.
    """
    import re

    page = client.post(url, {'action': action,
                             '_selected_action': [str(r.pk) for r in rows]})
    html = page.content.decode()
    assert 'Why?' in html, 'the confirmation page did not render'

    # The close tag AFTER our form's start, not the first one in the document -- the admin shell
    # renders its own forms above ours, so slicing to `index('</form>')` produced an empty span.
    start = html.index('<form method="post"')
    form = html[start:html.index('</form>', start)]
    fields = dict(re.findall(r'<input type="hidden" name="([^"]+)" value="([^"]*)"', form))
    selected = re.findall(r'<input type="hidden" name="_selected_action" value="([^"]+)"', form)
    fields.pop('_selected_action', None)
    fields.pop('csrfmiddlewaretoken', None)

    assert selected, 'the page carries no selected rows, so the submit would act on nothing'
    assert fields.get('action') == action, 'the page does not round-trip which action to run'

    return client.post(url, {**fields, '_selected_action': selected, 'reason': reason},
                       follow=True)


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

    # The PAGE, not the phrase. "A reason is required" has two producers -- this template's
    # errornote and the service's own refusal, which surfaces as a message on the redirect -- so
    # asserting the string alone passed with the admin-side gate deleted entirely.
    assert 'Why?' in resp.content.decode(), 'it did not come back to ask'
    assert not resp.redirect_chain, 'it ran the action and redirected instead of asking again'
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


# ── the page's own form, and the race it exists to report ────────────────────────────────────────

def test_submitting_the_rendered_confirmation_page_works(client):
    """The round trip a human actually performs."""
    flags = [_flag(), _flag()]
    client.force_login(_owner())

    _submit_the_confirm_page(client, FLAG_CHANGELIST, 'approve_selected', flags,
                             reason='confirmed, both delisted')

    assert ModerationAction.objects.count() == 2
    for flag in flags:
        flag.refresh_from_db()
        assert flag.status == 'approved'


def test_a_row_handled_while_the_page_was_open_is_named_not_dropped(client):
    """THE race this module exists to report, and the one it got wrong.

    Django hands an action the ChangeList queryset, and the confirm page posts back to the same URL
    with its query string -- so on a FILTERED changelist the second submit re-applies the filter. A
    row a colleague decided in between stopped matching, was dropped before the service saw it, and
    the success line counted a smaller denominator: the page listed two, the result said "1 of 1".
    """
    from trophies.services import moderation_service

    mine, theirs = _flag(), _flag()
    moderator = UserFactory()
    moderator.role = 'moderator'
    moderator.save()
    client.force_login(_owner())

    filtered = FLAG_CHANGELIST + '?status=pending'
    page = client.post(filtered, {'action': 'approve_selected',
                                  '_selected_action': [str(mine.pk), str(theirs.pk)]})
    assert 'Why?' in page.content.decode()

    # ...and while the admin is reading it, somebody else decides one of them.
    moderation_service.dismiss_game_flag(theirs, moderator, 'got there first')

    body = client.post(filtered, {'action': 'approve_selected',
                                  '_selected_action': [str(mine.pk), str(theirs.pk)],
                                  '_reasoned_confirm': '1', 'reason': 'sweeping'},
                       follow=True).content.decode()

    assert '1 of 2' in body, 'the count silently dropped the row instead of reporting it'
    assert 'Already handled' in body, 'the admin was not told which row was skipped'
    theirs.refresh_from_db()
    assert theirs.status == 'dismissed', "the sweep overwrote somebody else's decision"


def test_the_admin_gate_uses_the_same_reason_length_as_the_service():
    """They were two hardcoded 3s. On drift the admin would pass a reason the service then refuses
    for every row -- N warnings, the typed reason gone, and no page left to retype it in."""
    from core.services.audit import MIN_REASON_LENGTH
    from trophies import admin_reasoned_actions

    assert admin_reasoned_actions.MIN_REASON_LENGTH is MIN_REASON_LENGTH


def test_a_long_run_of_refusals_is_summarised(client):
    """One message per row is unbounded: a full changelist page of already-handled rows renders a
    wall of near-identical warnings and grows the session row holding them."""
    from trophies.admin_reasoned_actions import MAX_NAMED_REFUSALS
    from trophies.services import moderation_service

    moderator = UserFactory()
    moderator.role = 'moderator'
    moderator.save()
    flags = [_flag() for _ in range(MAX_NAMED_REFUSALS + 4)]
    for flag in flags:
        moderation_service.dismiss_game_flag(flag, moderator, 'all already done')
    client.force_login(_owner())

    body = _sweep(client, FLAG_CHANGELIST, 'approve_selected', flags,
                  reason='sweeping').content.decode()

    assert 'and 4 more already handled' in body
    assert body.count('Already handled') <= MAX_NAMED_REFUSALS

