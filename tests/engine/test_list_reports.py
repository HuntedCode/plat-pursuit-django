"""Reporting a list, and what a moderator can do about it.

Lists ship with free text a stranger reads -- a name and a description -- and the site had no way to
flag either. This is that path end to end: the report, the queue, and the decision.

THE ACTION HIDES THE WORDS, NOT THE LIST (owner's call, 2026-09-20). It mirrors `hide_blurb`, which
keeps the rating and hides only the free text on the reasoning that removing objectionable words
should not silently destroy unrelated data. Here the unrelated data is somebody's two-hundred-game
backlog, which a bad title is not a reason to take away.
"""
import pytest
from django.urls import reverse

from gamelists.models import GameList, GameListReport
from gamelists.services import game_list_service as svc
from tests.factories import ProfileFactory

pytestmark = pytest.mark.django_db


def _hunter(psn='hunter'):
    return ProfileFactory(is_linked=True, psn_username=psn)


def _moderator(psn='mod'):
    profile = ProfileFactory(is_linked=True, psn_username=psn)
    user = profile.user
    user.is_staff = True
    user.role = 'moderator'
    user.save(update_fields=['is_staff', 'role'])
    return profile


def _list(owner, name='Backlog', public=True):
    game_list = svc.create_list(owner, name=name, description='Some words')
    if public:
        svc.update_list(game_list, owner, is_public=True)
        game_list.refresh_from_db()
    return game_list


# ── the report ───────────────────────────────────────────────────────────────────────────────────

def test_a_hunter_can_report_a_list():
    owner = _hunter('owner')
    game_list = _list(owner)
    reporter = _hunter('reporter')

    report = svc.report_list(game_list, reporter, reason='inappropriate', details='the name')

    assert report.status == 'pending'
    assert report.game_list_id == game_list.pk
    assert report.reporter_id == reporter.id


def test_you_cannot_report_your_own_list():
    """An author who dislikes their own name can edit it. A self-report is a mistake or an attempt to
    put a moderator's time somewhere it is not needed."""
    owner = _hunter('owner')
    game_list = _list(owner)

    with pytest.raises(svc.ListError, match='your own list'):
        svc.report_list(game_list, owner, reason='spam')


def test_one_report_per_hunter():
    """`unique(game_list, reporter)` is the real guard; this is it answered in words rather than as
    an IntegrityError 500. Re-reporting is REFUSED rather than silently ignored, because a reporter
    who hears nothing assumes it did not work and tries somewhere louder."""
    owner = _hunter('owner')
    game_list = _list(owner)
    reporter = _hunter('reporter')

    svc.report_list(game_list, reporter, reason='spam')
    with pytest.raises(svc.ListError, match='already reported'):
        svc.report_list(game_list, reporter, reason='harassment')

    assert GameListReport.objects.filter(game_list=game_list).count() == 1


def test_two_hunters_can_report_the_same_list():
    """The uniqueness is per reporter, not per list -- otherwise the first objector silences the
    second, and a moderator loses the only signal that something is widely objected to."""
    owner = _hunter('owner')
    game_list = _list(owner)

    svc.report_list(game_list, _hunter('a'), reason='spam')
    svc.report_list(game_list, _hunter('b'), reason='harassment')

    assert GameListReport.objects.filter(game_list=game_list).count() == 2


def test_a_reason_is_required_and_must_be_one_of_the_offered_ones():
    owner = _hunter('owner')
    game_list = _list(owner)
    reporter = _hunter('reporter')

    for bad in ('', 'because', 'SPAM'):
        with pytest.raises(svc.ListError, match='reason'):
            svc.report_list(game_list, reporter, reason=bad)


def test_an_unlinked_account_cannot_report():
    """Accountability. The same bar every other report on the site sets."""
    owner = _hunter('owner')
    game_list = _list(owner)
    drifter = ProfileFactory(is_linked=False, psn_username='drifter')

    with pytest.raises(svc.ListError):
        svc.report_list(game_list, drifter, reason='spam')


def test_a_restricted_account_can_still_report():
    """A restriction stops somebody WRITING content other people read. Flagging is not that, and an
    account that has been restricted is not thereby disqualified from noticing a slur."""
    from users.services import restriction_service

    owner = _hunter('owner')
    game_list = _list(owner)
    reporter = _hunter('reporter')
    restriction_service.apply_restriction(
        reporter.user, 'all_ugc', _moderator().user, 'restricted for the test')

    report = svc.report_list(game_list, reporter, reason='harassment')
    assert report.status == 'pending'


def test_the_details_are_sanitized_like_any_other_free_text():
    """A moderator reads them in the queue, which is a rendered page. A report body is not exempt
    from the rule the rest of the module follows just because its audience is staff."""
    owner = _hunter('owner')
    game_list = _list(owner)

    report = svc.report_list(game_list, _hunter('reporter'), reason='spam',
                             details='  <b>look</b>  ')
    assert '<b>' not in report.details


# ── the endpoint ─────────────────────────────────────────────────────────────────────────────────

def test_the_endpoint_reports_and_says_nothing_about_the_pile(client):
    """No counts come back. How many reports a list carries is a moderator's information, and
    showing it tells somebody organising a pile-on whether it is working."""
    owner = _hunter('owner')
    game_list = _list(owner)
    reporter = _hunter('reporter')
    client.force_login(reporter.user)

    resp = client.post(reverse('list_report', args=[game_list.pk]),
                       {'reason': 'inappropriate', 'details': 'the title'})

    assert resp.status_code == 200
    assert resp.json() == {'reported': True}
    assert GameListReport.objects.filter(game_list=game_list).count() == 1


def test_the_endpoint_hands_back_the_services_words(client):
    owner = _hunter('owner')
    game_list = _list(owner)
    reporter = _hunter('reporter')
    client.force_login(reporter.user)
    svc.report_list(game_list, reporter, reason='spam')

    resp = client.post(reverse('list_report', args=[game_list.pk]), {'reason': 'spam'})

    assert resp.status_code == 400
    assert 'already reported' in resp.json()['error']


def test_a_private_list_cannot_be_reported_by_a_stranger(client):
    """`readable_by` resolution, so a private list 404s rather than 403ing -- an id alone must never
    confirm that somebody's private list exists."""
    owner = _hunter('owner')
    game_list = _list(owner, public=False)
    client.force_login(_hunter('stranger').user)

    resp = client.post(reverse('list_report', args=[game_list.pk]), {'reason': 'spam'})
    assert resp.status_code == 404


# ── the moderator's decision ─────────────────────────────────────────────────────────────────────

def test_hiding_the_words_keeps_the_list():
    """THE WHOLE POINT. A bad title is not a reason to destroy a curated backlog."""
    from trophies.services import moderation_service

    owner = _hunter('owner')
    game_list = _list(owner, name='Something awful')
    report = svc.report_list(game_list, _hunter('reporter'), reason='harassment')
    mod = _moderator()

    moderation_service.hide_list_text(report, mod.user, 'slur in the title')

    game_list.refresh_from_db()
    report.refresh_from_db()
    assert game_list.text_hidden is True
    assert report.status == 'action_taken'
    # The list itself is untouched -- including the stored name, so the decision is reversible and
    # an appeal can still see what was reported.
    assert game_list.is_deleted is False
    assert game_list.is_public is True
    assert game_list.name == 'Something awful'


def test_a_hidden_list_renders_a_neutral_name_not_the_reported_one():
    """`display_name` is THE supported read. A flag honoured in one template and forgotten in the
    next is the bug class this project has a documented history with."""
    owner = _hunter('owner')
    game_list = _list(owner, name='Something awful')

    assert game_list.display_name == 'Something awful'
    assert game_list.display_description == 'Some words'

    game_list.text_hidden = True
    assert game_list.display_name == GameList.HIDDEN_NAME
    assert game_list.display_description == ''
    assert game_list.name == 'Something awful', 'the stored name must survive for the appeal'


def test_dismissing_leaves_the_list_alone():
    from trophies.services import moderation_service

    owner = _hunter('owner')
    game_list = _list(owner)
    report = svc.report_list(game_list, _hunter('reporter'), reason='spam')

    moderation_service.dismiss_list_report(report, _moderator().user, 'the name is fine')

    game_list.refresh_from_db()
    report.refresh_from_db()
    assert game_list.text_hidden is False
    assert report.status == 'dismissed'


def test_a_second_moderator_gets_already_handled_not_a_second_log_entry():
    """`_lock_list_report` serialises two moderators, and the status check turns the loser into a
    clean refusal rather than a second, false audit entry."""
    from trophies.services import moderation_service

    owner = _hunter('owner')
    game_list = _list(owner)
    report = svc.report_list(game_list, _hunter('reporter'), reason='spam')
    moderation_service.hide_list_text(report, _moderator('one').user, 'first')

    with pytest.raises(moderation_service.ModerationError, match='Already handled'):
        moderation_service.dismiss_list_report(report, _moderator('two').user, 'second')


def test_hiding_an_already_hidden_list_claims_no_change():
    """A second report on an already-hidden list would otherwise log `text_hidden: [True, True]` --
    an entry claiming a change that did not happen, which the moderation module calls affirmatively
    misleading evidence."""
    from trophies.models import ModerationAction
    from trophies.services import moderation_service

    owner = _hunter('owner')
    game_list = _list(owner)
    first = svc.report_list(game_list, _hunter('a'), reason='spam')
    second = svc.report_list(game_list, _hunter('b'), reason='spam')
    mod = _moderator()

    moderation_service.hide_list_text(first, mod.user, 'first')
    action = moderation_service.hide_list_text(second, mod.user, 'second')

    assert action.changed == {}, 'the log claimed a change that did not happen'
    assert ModerationAction.objects.filter(action='list_text_hidden').count() == 2


def test_a_reason_is_required_for_every_decision():
    """Recorded with the moderator's name. The audit log is the appeal's only evidence."""
    from trophies.services import moderation_service

    owner = _hunter('owner')
    game_list = _list(owner)
    report = svc.report_list(game_list, _hunter('reporter'), reason='spam')

    with pytest.raises(moderation_service.ModerationError):
        moderation_service.hide_list_text(report, _moderator().user, '')


def test_the_log_points_at_the_right_person_for_each_decision():
    """Hiding is evidence about the AUTHOR; dismissing is evidence about the REPORTER. Left to each
    call site that quietly becomes two rules."""
    from trophies.services import moderation_service

    owner = _hunter('owner')
    reporter = _hunter('reporter')
    mod = _moderator()

    hidden = moderation_service.hide_list_text(
        svc.report_list(_list(owner, name='One'), reporter, reason='spam'),
        mod.user, 'slur in the title')
    assert hidden.subject_user_id == owner.user_id

    dismissed = moderation_service.dismiss_list_report(
        svc.report_list(_list(owner, name='Two'), reporter, reason='spam'),
        mod.user, 'the name is fine')
    assert dismissed.subject_user_id == reporter.user_id


# ── the queue ────────────────────────────────────────────────────────────────────────────────────

def test_the_queue_is_moderators_only(client):
    owner = _hunter('owner')
    svc.report_list(_list(owner), _hunter('reporter'), reason='spam')

    client.force_login(_hunter('nobody').user)
    assert client.get(reverse('mod_list_reports')).status_code in (302, 403, 404)


def test_the_queue_shows_a_pending_report(client):
    owner = _hunter('owner')
    game_list = _list(owner, name='Reported thing')
    svc.report_list(game_list, _hunter('reporter'), reason='harassment', details='look here')

    client.force_login(_moderator().user)
    body = client.get(reverse('mod_list_reports')).content.decode()

    assert 'Reported thing' in body, 'the queue must show what was actually objected to'
    assert 'look here' in body
    assert 'reporter' in body and 'owner' in body, 'both sides are named'


def test_the_queue_counts_feed_the_navbar_marker():
    """`open_report_count` sums `queue_counts()`, so a new queue joins the marker automatically. A
    marker that counts differently from the page it points at is worse than no marker."""
    from trophies.services import moderation_service

    before = moderation_service.open_report_count()
    svc.report_list(_list(_hunter('owner')), _hunter('reporter'), reason='spam')

    counts = moderation_service.queue_counts()
    assert counts['list-reports']['open'] == 1
    assert moderation_service.open_report_count() == before + 1


def test_the_queue_does_not_n_plus_one_over_its_rows(client):
    """A row names the list, its owner, the reporter and the moderator. Without the joins that is
    four queries a row on a page of 25 -- the shape this project has a documented history with."""
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    # ONE LIST EACH, because `FREE_MAX_LISTS` is 3 -- twelve lists under one owner is refused by the
    # cap long before the query count means anything. Separate owners is also the truer shape: a
    # queue page is twelve different people's lists, which is exactly what the joins are for.
    for n in range(6):
        svc.report_list(_list(_hunter(f'o{n}'), name=f'List {n}'), _hunter(f'r{n}'), reason='spam')

    client.force_login(_moderator().user)
    url = reverse('mod_list_reports')
    client.get(url)                                    # warm the chrome caches
    with CaptureQueriesContext(connection) as few:
        assert client.get(url).status_code == 200

    for n in range(6, 12):
        svc.report_list(_list(_hunter(f'o{n}'), name=f'List {n}'), _hunter(f'r{n}'), reason='spam')
    with CaptureQueriesContext(connection) as many:
        assert client.get(url).status_code == 200

    assert len(many.captured_queries) == len(few.captured_queries), (
        'the queue grew a query per row')


def test_no_reader_facing_template_renders_a_lists_raw_name():
    """`text_hidden` honoured in one template and forgotten in the next is the `profile_views.py:670`
    bug class -- a flag checked only where somebody remembered, bypassed by the path that did not
    render it. The OWNER's own surfaces are exempt: the edit form has to show them what to fix, and
    the delete confirm has to name what is being deleted.
    """
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    offenders = []
    for path in (root / 'templates' / 'gamelists').rglob('*.html'):
        for number, line in enumerate(path.read_text(encoding='utf-8').splitlines(), 1):
            if 'game_list.name' not in line and 'game_list.description' not in line:
                continue
            # The owner's own tools, which must show the real text.
            if any(marker in line for marker in
                   ('data-gl-edit', 'data-list-name', 'value="{{ game_list.name }}"',
                    'placeholder="What is this list for?"', 'is_owner')):
                continue
            offenders.append(f'{path.name}:{number}')

    assert not offenders, (
        f'reader-facing renders of a raw list name/description: {offenders}. '
        'Use `display_name` / `display_description`.')
