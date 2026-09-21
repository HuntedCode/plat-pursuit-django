"""Reporting a list, and what a moderator can do about it.

Lists ship with free text a stranger reads -- a name and a description -- and the site had no way to
flag either. This is that path end to end: the report, the queue, and the decision.

THE ACTION HIDES THE WORDS, NOT THE LIST (owner's call, 2026-09-20). It mirrors `hide_blurb`, which
keeps the rating and hides only the free text on the reasoning that removing objectionable words
should not silently destroy unrelated data. Here the unrelated data is somebody's two-hundred-game
backlog, which a bad title is not a reason to take away.
"""
import re
from pathlib import Path

import pytest
from django.urls import reverse

from gamelists.models import GameList, GameListReport
from trophies.models import ModerationAction
from gamelists.services import game_list_service as svc
from tests.factories import ConceptFactory, ProfileFactory

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


def test_a_content_restricted_account_can_still_report():
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


def test_an_account_restricted_from_REPORTING_cannot_report():
    """The other half, and it was missing entirely -- so the `reports` scope, which exists for
    exactly this, did nothing here.

    Every other report surface on the site honours it (`comment_service`, `game_flag_service`,
    both roadmap services). Lists ignored it along with `all_ugc`, so an account sanctioned
    specifically for abusing report queues kept one door open. The pair of tests is the point:
    a restriction on WRITING still permits a report, a restriction on REPORTING does not.
    """
    from users.services import restriction_service

    game_list = _list(_hunter('owner'))
    reporter = _hunter('spammer')
    restriction_service.apply_restriction(
        reporter.user, 'reports', _moderator().user, 'filed forty bad reports')

    with pytest.raises(svc.ListError, match='cannot send reports'):
        svc.report_list(game_list, reporter, reason='harassment')

    assert GameListReport.objects.filter(game_list=game_list).count() == 0


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
    owner = _hunter('theauthor')
    game_list = _list(owner, name='Reported thing')
    # DISTINCTIVE NAMES. These were `owner` and `reporter` -- the two most generic words a
    # moderation queue could possibly contain, and the template renders the prose "built by",
    # "reported by" and "deleted by its owner" around them. The assertion could not tell "the
    # username rendered" from "the page contains the English word".
    svc.report_list(game_list, _hunter('theflagger'), reason='harassment', details='look here')

    client.force_login(_moderator().user)
    body = client.get(reverse('mod_list_reports')).content.decode()

    assert 'Reported thing' in body, 'the queue must show what was actually objected to'
    assert 'look here' in body
    assert 'theflagger' in body and 'theauthor' in body, 'both sides are named'


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
        resp = client.get(url)
        assert resp.status_code == 200

    # THE SECOND BATCH ACTUALLY RENDERED. Without this the counts match trivially when the queue
    # renders NOTHING -- a wrong status filter, a broken join, an ordering that drops rows -- and
    # a comparison of zero against zero reports "no N+1" about a page that shows nobody anything.
    assert 'List 11' in resp.content.decode(), (
        'the queue rendered none of the second batch; the comparison below is vacuous')
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
    scanned = []
    for path in (root / 'templates' / 'gamelists').rglob('*.html'):
        scanned.append(path.name)
        for number, line in enumerate(path.read_text(encoding='utf-8').splitlines(), 1):
            # ANY variable, not just `game_list`. The pattern used to be the literal tokens
            # `game_list.name` / `game_list.description`, which the Spotlight walked straight past:
            # it renders through `spotlight.*`, so a regression there to `spotlight.name` was
            # invisible to the one guard written to catch exactly that.
            found = re.search(r'\{\{\s*(\w+)\.(name|description)\b', line)
            if not found:
                continue
            # WHITELISTED BY VARIABLE, not by a marker substring. `section.name` is a
            # `GameListSection`, which carries no `text_hidden` and has no display reader -- there
            # is nothing for it to be hiding. (Worth knowing separately: that means a section
            # header is hunter-written text a reader sees which moderation cannot touch.)
            if found.group(1) == 'section':
                continue
            # The owner's own tools, which must show the real text.
            if any(marker in line for marker in
                   ('data-gl-edit', 'data-list-name', 'value="{{ game_list.name }}"',
                    'placeholder="What is this list for?"', 'is_owner')):
                continue
            offenders.append(f'{path.name}:{number}')

    # A POSITIVE ANCHOR. `rglob` over a directory that moved, got renamed, or was namespaced under
    # an app returns nothing, `offenders` is empty, and this passes having examined no files at
    # all -- the `assert [] == []` shape. The sibling guard in `test_gamelists_detail.py` learned
    # this already ("the scan is broken, not the CSS").
    assert len(scanned) >= 8, f'only scanned {scanned}; the sweep is broken, not the templates'
    assert not offenders, (
        f'reader-facing renders of a raw list name/description: {offenders}. '
        'Use `display_name` / `display_description`.')


# ── the dialog's manners ─────────────────────────────────────────────────────────────────────────
#
# A polish audit found this dialog was a third pattern beside the two it sits next to, and that
# several of its gaps were invisible from the server: an exit animation that was written and never
# played, announcements that went to an element that was not rendered, and a swipe sheet with no
# grab handle in front of a 500-character textarea. Each guard below pins one of those.

def _decommented_js(source):
    """JS with comments stripped -- several guards here assert a token is PRESENT, and this
    codebase writes comments that quote the code they replaced."""
    no_block = re.sub(r'/\*.*?\*/', '', source, flags=re.S)
    return '\n'.join(re.sub(r'//.*$', '', line) for line in no_block.splitlines())


def _source(relative):
    return (Path(__file__).resolve().parents[2] / relative).read_text(encoding='utf-8')


def test_the_report_dialog_closes_on_a_choreographed_exit():
    """The CSS for this exit shipped with the dialog and was DEAD.

    `.gl-dialog.is-closing` -> `glDialogOut` exists in `gamelists.css`, and the comment above it
    claimed the JS waits for `animationend` before closing. Nothing ever added the class, so every
    close -- Cancel, the x, Escape, success -- was a hard cut, and the dialog also carried a second
    unused exit via `gd-modal`. An exit written and not wired is worse than none: the next reader
    believes the comment.
    """
    js = _source('static/js/list-detail.js')
    css = _source('static/css/components/gamelists.css')

    # DE-COMMENTED, AND THE CALL SPECIFICALLY. `'is-closing' in js` survived a mutation that
    # removed the only line which ADDS the class: the string still appeared in this fix's own
    # comment and in the `contains`/`remove` calls left behind. What matters is that something
    # applies it.
    code = re.sub(r'//[^\n]*', '', re.sub(r'/\*.*?\*/', '', js, flags=re.S))

    assert '.gl-dialog.is-closing' in css, 'the exit animation is gone from the stylesheet'
    assert "classList.add('is-closing')" in code, (
        'the report dialog never plays the exit its stylesheet defines')
    assert 'animationend' in code, 'the close does not wait for the exit to finish'


def test_the_report_sheet_arms_its_drag_only_from_the_header():
    """`dismissableSheet`'s own contract: "Omit on a sheet you READ; pass one on a sheet you
    OPERATE, where an accidental dismiss costs unsaved work."

    This sheet holds up to 500 typed characters and passed no handle, so on touch a downward flick
    starting on the header, a field label or the error box armed the drag and destroyed the draft
    past 90px. The textarea itself is exempt by the helper; the labels around it are not. Both
    sibling dialogs pass a handle and both record fixing exactly this.
    """
    js = _source('static/js/list-detail.js')
    call = js[js.index('dismissableSheet(dialog'):]
    call = call[:call.index(')')]

    assert 'handle' in call, 'the report sheet can still be swiped away from anywhere, losing a draft'


def test_the_reason_select_cannot_file_a_report_nobody_chose(client):
    """The first real option was auto-selected, so opening the dialog and pressing Send with no
    thought filed a Spam report against somebody's list -- and `required` on a select that is never
    empty can never fire. A disabled, selected placeholder gives `required` something to catch."""
    owner = ProfileFactory(is_linked=True, psn_username='author')
    game_list = svc.create_list(owner, name='A list', is_public=True)
    reader = ProfileFactory(is_linked=True, psn_username='reader')
    client.force_login(reader.user)

    body = client.get(reverse('list_detail', args=[game_list.id])).content.decode()
    select = body[body.index('data-gl-report-reason'):]
    select = select[:select.index('</select>')]

    assert 'disabled selected' in select, 'the reason select auto-picks the first real reason'
    # And the server refuses it too, so the guard is not only in the markup.
    with pytest.raises(svc.ListError):
        svc.report_list(game_list, reader, reason='')


def test_the_report_error_announces_and_arrives(client):
    """A refusal is the only feedback a reporter gets for "you have already reported this list".

    `role="alert"` so it is spoken, and an entrance so it does not shove the footer down a step
    with no transition -- which reads as a layout glitch rather than as an answer.
    """
    owner = ProfileFactory(is_linked=True, psn_username='author')
    game_list = svc.create_list(owner, name='A list', is_public=True)
    reader = ProfileFactory(is_linked=True, psn_username='reader')
    client.force_login(reader.user)

    body = client.get(reverse('list_detail', args=[game_list.id])).content.decode()
    error = body[body.index('gl-dialog__error'):]
    error = error[:error.index('>') + 1]

    assert 'role="alert"' in error, 'a refusal is never announced'
    assert '.gl-dialog__error:not([hidden])' in _source('static/css/components/gamelists.css'), (
        'the error appears with no entrance')


# ── the buttons, through the view ────────────────────────────────────────────────────────────────

def test_every_decision_that_takes_a_lock_opens_its_own_transaction():
    """EVERY MOD DECISION ON A LIST 500'd IN PRODUCTION, and the whole suite was green.

    `hide_list_text`, `dismiss_list_report` and `restore_list_text` all take a
    `select_for_update()` lock, and all three were missing `@transaction.atomic` -- unlike the six
    older decisions in that module. Nothing upstream supplies one: `ATOMIC_REQUESTS` is unset and
    `PostActionMixin.post` calls `self.act()` bare. So Django raised `TransactionManagementError`
    ("select_for_update cannot be used outside of a transaction"), `PostActionMixin` catches only
    `ModerationError`, and it escaped as a 500 on every press of every button.

    NOTHING CAUGHT IT because `pytest.mark.django_db` wraps each test in an atomic block --
    supplying the exact thing production lacks -- and every test called the service function
    directly rather than through the view.

    A `django_db(transaction=True)` test DOES reproduce it, and was the first thing written here.
    It is not what shipped: transactional tests flush every table at teardown, and with
    `--reuse-db` the damage persists into later tests and later runs. `docs/guides/testing.md`
    records that gutting the Job catalog in 2026-08 and says plainly to prefer keeping them out of
    the suite. Writing one here promptly truncated the badge catalog and failed an unrelated test
    two files away, which is the warning arriving on schedule.

    So this pins the RULE instead of one instance of it -- and the rule has to be scanned WHEREVER
    it applies, which is the part the first cut got wrong. It hardcoded this one module, and the
    very same change then created a fresh instance of the bug two files away in
    `game_list_service.py`: a new helper was inserted between `@transaction.atomic` and
    `update_list`, carrying the decorator off it, so every rename, publish and un-publish 500'd.
    The suite stayed green for exactly the reason the paragraph above names. A rule scoped to one
    file is a rule that only covers the bug you already found.
    """
    import ast

    root = Path(__file__).resolve().parents[2]
    modules = [root / 'trophies' / 'services' / 'moderation_service.py',
               root / 'gamelists' / 'services' / 'game_list_service.py']

    offenders = []
    checked = []
    for path in modules:
        source = path.read_text(encoding='utf-8')
        tree = ast.parse(source)
        for node in tree.body:
            # Private `_lock_*` helpers are always reached from a public caller; the entry point is
            # what has to own the transaction, so that is what is checked.
            if not isinstance(node, ast.FunctionDef) or node.name.startswith('_'):
                continue
            body = ast.get_source_segment(source, node) or ''
            if 'select_for_update(' not in body and not re.search(r'_lock_\w+\(', body):
                continue
            checked.append(f'{path.name}:{node.name}')
            decorated = any(getattr(d, 'attr', '') == 'atomic' for d in node.decorator_list)
            # EITHER FORM COUNTS. `roadmap_merge_service` opens an inner `with transaction.atomic():`
            # instead of decorating, which is equally correct -- a scan that only knew about the
            # decorator reported four false positives the first time it was widened.
            inner = any(isinstance(n, ast.With) and 'transaction.atomic' in (
                ast.get_source_segment(source, n) or '')[:80] for n in ast.walk(node))
            if not (decorated or inner):
                offenders.append(f'{path.name}:{node.name}')

    # A POSITIVE ANCHOR FIRST. Every assertion below is a negative, and if the walk ever stops
    # matching -- a reformat, a module renamed or moved -- `checked` empties and `offenders`
    # empties with it, leaving a test that passes because it examined nothing.
    assert len(checked) >= 14, (
        f'only found {len(checked)} lock-taking writers ({checked}); the scan is broken, '
        'not the services'
    )
    assert not offenders, (
        f'these take a row lock with no @transaction.atomic, so they will raise '
        f'TransactionManagementError and 500 on every press: {offenders}'
    )


@pytest.mark.parametrize('url_name,expected', [
    ('mod_hide_list_text', True),
    ('mod_dismiss_list_report', False),
])
def test_a_moderator_can_press_the_buttons(client, url_name, expected):
    """The view half: permissions, the redirect, the decision sticking, and one audit entry.

    It cannot see the missing-transaction bug (see the test above for why), but everything else
    about these two buttons was equally untested -- no test posted to either URL at all.
    """
    owner = _hunter('owner')
    game_list = _list(owner)
    report = svc.report_list(game_list, _hunter('reporter'), reason='harassment')
    client.force_login(_moderator().user)

    resp = client.post(reverse(url_name, args=[report.pk]),
                       {'reason': 'checked it'}, follow=True)

    assert resp.status_code == 200
    report.refresh_from_db()
    game_list.refresh_from_db()
    assert report.status != 'pending', 'the decision did not stick'
    assert game_list.text_hidden is expected
    assert ModerationAction.objects.filter(list_report=report).count() == 1


# ── undoing a list decision ──────────────────────────────────────────────────────────────────────
#
# Hiding a list's words shipped as a ONE-WAY DOOR. `restore_list_text` existed and had no caller:
# no view, no URL, no admin action, and no `_UNDO` entry -- so the Reverse button never rendered
# for one of these, and `reverse_action` refused it by name if it was ever reached. The only exit
# left was a shell write, which bypasses the audit log this whole module exists to guarantee.
#
# The guard that should have caught it hardcoded the four decisions that existed when it was
# written; it is derived from `ModerationAction.ACTIONS` now, so the next decision is covered the
# day it is added rather than the day somebody needs to undo one.

def _hidden_list():
    """A list whose words a moderator hid, and the entry recording it."""
    from trophies.services import moderation_service

    owner = _hunter('theauthor')
    game_list = _list(owner, name='Something awful')
    report = svc.report_list(game_list, _hunter('theflagger'), reason='harassment')
    action = moderation_service.hide_list_text(report, _moderator().user, 'slur in the title')
    return game_list, report, action


def test_hiding_a_lists_words_can_be_undone():
    from trophies.services import moderation_service

    game_list, report, action = _hidden_list()

    reversal = moderation_service.reverse_action(action, _moderator('mod2').user, 'Misread it.')

    game_list.refresh_from_db()
    assert game_list.text_hidden is False, 'the words did not come back'
    assert reversal.action == 'list_text_restored'
    assert reversal.reverses_id == action.pk
    # The original is ADDED TO, never rewritten -- an audit trail that can be edited is not one.
    action.refresh_from_db()
    assert action.is_reversed is True
    assert ModerationAction.objects.count() == 2


def test_undoing_a_hide_hands_the_standing_decision_to_whoever_undid_it():
    """The same call the blurb undo makes: the report is `reviewed` again and the person who
    reversed it owns that state, because they are the one who decided it now."""
    from trophies.services import moderation_service

    _game_list, report, action = _hidden_list()
    second = _moderator('mod2').user

    moderation_service.reverse_action(action, second, 'Misread it.')

    report.refresh_from_db()
    assert report.status == 'reviewed'
    assert report.reviewed_by_id == second.pk


def test_undoing_a_hide_that_hid_nothing_is_refused():
    """`hide_list_text` records `changed={}` when the list was ALREADY hidden. Un-hiding on the
    strength of that entry would reverse a decision it never made -- and would leave a
    `list_text_restored` row crediting somebody with putting back words they did not."""
    from trophies.services import moderation_service

    game_list, _report, _action = _hidden_list()
    second_report = svc.report_list(game_list, _hunter('another'), reason='spam')
    no_op = moderation_service.hide_list_text(second_report, _moderator('mod2').user, 'Again.')
    assert no_op.changed == {}, 'the fixture is wrong; this entry should claim no change'

    with pytest.raises(moderation_service.ModerationError, match='nothing to put back'):
        moderation_service.reverse_action(no_op, _moderator('mod3').user, 'Undo.')

    game_list.refresh_from_db()
    assert game_list.text_hidden is True, 'a refused undo still un-hid the words'


def test_dismissing_a_list_report_can_be_undone():
    """Reopening puts it back in the queue for somebody to decide again, with `reviewed_by`
    CLEARED -- a row reading "dismissed by X" while sitting in the pending queue contradicts
    itself on the page, and who dismissed it survives in the entry being reversed."""
    from trophies.services import moderation_service

    game_list = _list(_hunter('theauthor'))
    report = svc.report_list(game_list, _hunter('theflagger'), reason='spam')
    action = moderation_service.dismiss_list_report(report, _moderator().user, 'Looks fine.')

    reversal = moderation_service.reverse_action(action, _moderator('mod2').user, 'Second look.')

    report.refresh_from_db()
    assert report.status == 'pending', 'the report did not go back into the queue'
    assert report.reviewed_by_id is None
    assert report.reviewed_at is None
    assert reversal.action == 'list_report_reopened'
    game_list.refresh_from_db()
    assert game_list.text_hidden is False, 'reopening a dismissal hid the words'


def test_a_reversal_cannot_itself_be_reversed():
    """Undoing an undo is re-deciding, and the service says so rather than ping-ponging the log."""
    from trophies.services import moderation_service

    _game_list, _report, action = _hidden_list()
    reversal = moderation_service.reverse_action(action, _moderator('mod2').user, 'Misread.')

    with pytest.raises(moderation_service.ModerationError, match='itself a reversal'):
        moderation_service.reverse_action(reversal, _moderator('mod3').user, 'Again.')


# ── what the audits found ────────────────────────────────────────────────────────────────────────

def test_hidden_words_are_not_findable_through_search(client):
    """HIDING HAS TO HIDE FROM THE INDEX TOO.

    Browse searched the raw `name`/`description` columns, so a moderated list still MATCHED its own
    hidden text. The tile came back reading "Untitled list", but the match itself confirms the
    string is there -- and `data-result-count` leaks the same signal without rendering a tile at
    all. On an anonymous, IP-rate-limited page that is a slur reconstructable substring by
    substring.
    """
    owner = _hunter('theauthor')
    game_list = _list(owner, name='Unspeakable')
    game_list.text_hidden = True
    game_list.save(update_fields=['text_hidden'])

    body = client.get(reverse('lists_browse') + '?q=Unspeakable').content.decode()

    assert 'data-result-count="0"' in body, 'a hidden name is still matchable by search'
    # And the guard is about HIDING, not about search being broken: the same term finds it again
    # once the words are restored.
    game_list.text_hidden = False
    game_list.save(update_fields=['text_hidden'])
    body = client.get(reverse('lists_browse') + '?q=Unspeakable').content.decode()
    assert 'data-result-count="1"' in body, 'search no longer finds a visible list by name'


def test_a_hidden_list_is_still_findable_by_its_owner(client):
    """The owner's name is not the moderated text, and it is how somebody gets back to a list they
    know exists. Excluding hidden lists from the whole query rather than from the text clauses
    would have taken that away too."""
    game_list = _list(_hunter('findableowner'), name='Unspeakable')
    game_list.text_hidden = True
    game_list.save(update_fields=['text_hidden'])

    body = client.get(reverse('lists_browse') + '?q=findableowner').content.decode()

    assert 'data-result-count="1"' in body, 'a hidden list vanished from its owner-name search too'


def test_the_spotlight_pick_is_deterministic():
    """Two lists featured inside the same timestamp resolved by whatever order Postgres felt like,
    so the band could flip between page loads -- the flicker that reads as a bug and cannot be
    reproduced on request. `covers._sort_key` documents adding a pk tiebreak for exactly this."""
    from django.utils import timezone

    from gamelists.models import GameList

    when = timezone.now()
    for n in range(4):
        game_list = _list(_hunter(f'tie{n}'), name=f'Tied {n}')
        game_list.featured_at = when
        game_list.save(update_fields=['featured_at'])

    picks = {GameList.objects.featured().first().pk for _ in range(5)}

    assert len(picks) == 1, f'the Spotlight picked {len(picks)} different lists from one timestamp'


@pytest.mark.parametrize('sent', ['true', 'True', '1', 'on', 'yes'])
def test_the_social_toggles_accept_the_booleans_the_rest_of_the_site_does(client, sent):
    """`UpdateListView` documents at length why `== 'true'` is wrong -- it reads 'True', '1', 'on'
    and 'yes' as FALSE -- and these two toggles were the siblings that never got `safe_bool`.
    Anything but the exact lowercase literal became an UN-like and was answered 200, with the
    response echoing the REQUESTED boolean so no client could tell."""
    game_list = _list(_hunter('theauthor'))
    viewer = _hunter('theliker')
    client.force_login(viewer.user)

    resp = client.post(reverse('list_like', args=[game_list.pk]), {'liked': sent})

    assert resp.status_code == 200
    game_list.refresh_from_db()
    assert game_list.like_count == 1, f'{sent!r} was read as an un-like'


# ── what the VERIFICATION audit found in the fixes ───────────────────────────────────────────────

def test_reversing_one_hide_leaves_another_moderators_hide_standing():
    """THE CHECK A VALUE COMPARISON CANNOT MAKE, which the blurb path has and this did not.

    Two decisions can hide one list. The second finds the words already gone, writes
    `changed={}`, and is deliberately irreversible on that basis. So reversing the FIRST one finds
    exactly the state it left, happily un-hides -- and puts the words back over a standing
    decision nobody disputed, with no entry left that could take them down again.
    """
    from trophies.services import moderation_service

    game_list, _report, first = _hidden_list()
    second_report = svc.report_list(game_list, _hunter('another'), reason='spam')
    moderation_service.hide_list_text(second_report, _moderator('mod2').user, 'Still bad.')

    with pytest.raises(moderation_service.ModerationError, match='has not been reversed'):
        moderation_service.reverse_action(first, _moderator('mod3').user, 'Undo mine.')

    game_list.refresh_from_db()
    assert game_list.text_hidden is True, "a standing decision was overturned by reversing another"


def test_a_hidden_list_does_not_sort_under_its_real_name(client):
    """EXCLUDING IT FROM SEARCH WAS HALF THE CHANNEL.

    A hidden list kept its RAW alphabetical position, wedged between two visible names, so an
    anonymous reader could read its leading characters off by bisection -- one dropdown click from
    the surface that was just closed. It sorts under the placeholder now, so its position says
    nothing about the words.
    """
    # A DISCRIMINATING FIXTURE, which the first version was not. It used only Aardvark and Zebra,
    # and the hidden list's real name ("Abhorrent") sorts between them -- as does the placeholder
    # ("Untitled list"). Both behaviours produced the identical rendered order, so the test passed
    # against the bug. "Middle" sits between the two, which is what makes the orders differ:
    #   raw name        -> Aardvark, [hidden], Middle,   Zebra
    #   placeholder (U) -> Aardvark, Middle,   [hidden], Zebra
    for name in ('Aardvark', 'Middle', 'Zebra'):
        _list(_hunter(f'own{name}'), name=name)
    hidden = _list(_hunter('ownhidden'), name='Abhorrent')
    hidden.text_hidden = True
    hidden.save(update_fields=['text_hidden'])

    body = client.get(reverse('lists_browse') + '?sort=alpha').content.decode()

    assert body.index('Middle') < body.index(GameList.HIDDEN_NAME), (
        'the hidden list still sorts at its real name, so its position leaks the first letters')
    assert body.index(GameList.HIDDEN_NAME) < body.index('Zebra'), (
        'the placeholder did not sort under its own name either')


def test_the_report_dialog_still_confirms_when_a_close_is_already_running():
    """PRESS SEND, THEN ESCAPE. The response lands 180-400ms later and calls `closeReport(cb)` --
    which used to hit its "already closing" early return WITHOUT running the callback, so the
    report was filed and the reporter was told nothing at all. No toast, and no `announce()`:
    exactly the failure the callback refactor was written to fix, re-created inside the fix.

    Pinned in source because it is a timing path no server test can reach: every OTHER exit from
    `closeReport` invokes the queued callbacks, and the queue is what makes that possible.
    """
    js = _decommented_js(_source('static/js/list-detail.js'))
    body = js[js.index('function closeReport('):]
    body = body[:body.index('\n        }')]

    assert 'pendingAfter.push(after)' in body, 'the callback is no longer queued before the guards'
    # Every early return either drains or has already queued; a bare `return` that skips both is
    # the bug. The one permitted bare return is the already-closing branch, whose in-flight
    # `done()` drains the queue it was just added to.
    assert body.count('drain()') >= 3, 'an exit from closeReport stopped running its callbacks'
    assert 'clearTimeout(closeTimer)' in body, (
        'a stale fallback timer can still fire inside a later close')


def test_the_report_dialog_can_actually_take_focus(client):
    """`dialog.focus()` is a silent no-op on an element that is not a focusable area, so focus
    stayed where `showModal()` put it -- in Firefox and Safari the first focusable descendant,
    i.e. the reason `<select>` the focus move exists to avoid. Chrome 117+ focuses the dialog
    itself, which made it look right on one browser."""
    owner = _hunter('theauthor')
    game_list = _list(owner)
    client.force_login(_hunter('areader').user)

    body = client.get(reverse('list_detail', args=[game_list.pk])).content.decode()
    tag = re.search(r'<dialog[^>]*id="gl-report"[^>]*>', body)

    assert tag, 'the report dialog is gone'
    assert 'tabindex="-1"' in tag.group(0), 'the dialog cannot take focus, so the focus move is inert'


# ── hardening the audits asked for ───────────────────────────────────────────────────────────────

def test_the_list_writes_all_take_the_row_lock():
    """RULE 2 OF THIS MODULE, applied to the two writes that skipped it.

    "The lock comes before the value it protects, and the precondition is re-asserted on the row
    that came back." `update_list` and `delete_list` read `is_deleted`/`owner_id` off the caller's
    stale instance and then saved -- so a POST carrying `is_public=1` racing a delete committed
    `is_deleted=True, is_public=True`, and two renames were last-writer-wins off two stale copies.
    `.public()` hides deleted rows so nothing rendered, which is what kept it invisible.

    Pinned as a source rule rather than as a race, because a race is not reliably reproducible in
    a test and the rule is what has to hold.
    """
    source = (Path(__file__).resolve().parents[2]
              / 'gamelists' / 'services' / 'game_list_service.py').read_text(encoding='utf-8')

    for name in ('update_list', 'delete_list'):
        start = source.index(f'def {name}(')
        body = source[start:source.index('\ndef ', start + 1)]
        assert 'select_for_update' in body or '_lock_list(' in body, (
            f'{name} writes without locking the row first')


def test_deleting_an_empty_concept_cannot_orphan_a_list_entry():
    """`GameListItem.concept` is CASCADE, so deleting a concept somebody has in a list takes the
    row with it -- outside the service, so `game_count` is never recounted and `position` is never
    re-compacted. `PositiveIntegerField` means an inflated count can never be walked back down,
    and the browse grid SORTS and FILTERS on it.

    The command now treats a concept somebody curated into a list as not-empty.
    """
    from django.core.management import call_command
    from io import StringIO

    from trophies.models import Concept

    owner = _hunter('theauthor')
    game_list = _list(owner)
    concept = ConceptFactory(unified_title='Orphan Bait')
    svc.add_concept(game_list, owner, concept)
    game_list.refresh_from_db()
    assert game_list.game_count == 1

    call_command('cleanup_empty_concepts', stdout=StringIO(), stderr=StringIO())

    assert Concept.objects.filter(pk=concept.pk).exists(), (
        'a concept in somebody\'s list was deleted, cascading the entry away')
    game_list.refresh_from_db()
    assert game_list.game_count == 1, 'the denormalised count drifted'


def test_both_doors_to_creating_a_list_share_one_rate_limit_budget():
    """`django_ratelimit` derives its group from module+qualname when `group=` is omitted, so two
    views implementing one act get two independent buckets -- the real create allowance was 60/m,
    not the 30/m each decorator appears to state. `api/game_flag_views.py` shares a group across
    its two doors for exactly this reason."""
    source = (Path(__file__).resolve().parents[2]
              / 'gamelists' / 'views.py').read_text(encoding='utf-8')

    assert source.count('group=CREATE_LIST_RATELIMIT_GROUP') == 2, (
        'the two create endpoints no longer share a rate-limit budget')


def test_the_partial_response_varies_on_both_switch_headers(client):
    """The same URL serves a full page and a bare grid partial, chosen on `HX-Request` as well as
    `X-Requested-With`. Declaring only the latter left a shared cache free to hand a stored grid
    partial -- no chrome, no header, no toolbar -- to somebody loading the page fresh."""
    resp = client.get(reverse('lists_browse'))

    vary = resp.headers.get('Vary', '')
    assert 'X-Requested-With' in vary
    assert 'HX-Request' in vary, 'a cache can serve the partial to a full page load'
