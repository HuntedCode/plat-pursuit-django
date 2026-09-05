"""The Mod Center surface: the gate, the queues, and acting on a row.

`test_moderation_service` covers what a decision DOES. This covers reaching it: that the pages are
closed to everyone but moderators and admins, that a queue does not query per row, that an action is
a POST, and that acting returns you to the list you were reading.

The gate is the part worth being paranoid about. These URLs write live game data -- one of them sets
`shovelware_lock`, which permanently overrides the automated classifier.
"""
import pathlib
import re

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from tests.factories import ConceptFactory, GameFactory, ProfileFactory, UserFactory
from trophies.models import BlurbReport, GameFlag, ModerationAction, UserConceptRating
from trophies.services import moderation_service

#: Repo root, for the source guards at the bottom of this file.
ROOT = pathlib.Path(__file__).resolve().parents[2]


def _built_css():
    """The compiled Tailwind sheet, as TRACKED.

    `static/css/output.css`, never `staticfiles/`. staticfiles is gitignored and produced by
    collectstatic at deploy time, so CI has no such directory: a test that read it would be red on
    every run for reasons having nothing to do with the code. The tracked build output is what gets
    deployed and then collected, so it is the honest thing to assert on. (Running collectstatic
    locally still matters for SEEING a change -- WhiteNoise serves the collected copy in dev too --
    but that is a dev-loop concern, not something a test can pin.)
    """
    return (ROOT / 'static' / 'css' / 'output.css').read_text(encoding='utf-8')

pytestmark = pytest.mark.django_db

PAGES = ['mod_center', 'mod_quick_takes', 'mod_game_flags']


def _user(role=''):
    user = UserFactory()
    if role:
        user.role = role
        user.save()
    return user


def _report(blurb='some words'):
    concept = ConceptFactory(unified_title='Hollow Knight')
    GameFactory(concept=concept)          # the row links to the game, so it needs one
    rating = UserConceptRating.objects.create(
        profile=ProfileFactory(is_linked=True), concept=concept, concept_trophy_group=None,
        blurb=blurb, difficulty=5, grindiness=5, hours_to_platinum=20,
        fun_ranking=8, overall_rating=4.0,
    )
    return BlurbReport.objects.create(
        rating=rating, reporter=ProfileFactory(is_linked=True), reason='harassment')


def _flag(flag_type='delisted'):
    return GameFlag.objects.create(
        game=GameFactory(), reporter=ProfileFactory(is_linked=True), flag_type=flag_type)


# ── the gate ─────────────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize('name', PAGES)
def test_a_signed_out_visitor_is_sent_to_login(client, name):
    resp = client.get(reverse(name))
    assert resp.status_code == 302 and '/login' in resp.url.lower()


@pytest.mark.parametrize('name', PAGES)
def test_an_ordinary_hunter_is_sent_home_not_told_it_exists(client, name):
    """Redirect rather than 403: a hunter who guesses a mod URL should get the home page, not
    confirmation that something is there."""
    client.force_login(_user())

    resp = client.get(reverse(name))

    assert resp.status_code == 302
    assert resp.url == '/', 'a 403 would confirm the page exists'


@pytest.mark.parametrize('name', PAGES)
@pytest.mark.parametrize('role', ['moderator', 'admin'])
def test_moderators_and_admins_get_in(client, name, role):
    client.force_login(_user(role))
    assert client.get(reverse(name)).status_code == 200


@pytest.mark.parametrize('name', PAGES)
def test_a_deactivated_moderator_is_locked_out(client, name):
    moderator = _user('moderator')
    client.force_login(moderator)
    moderator.is_active = False
    moderator.save()

    assert client.get(reverse(name)).status_code == 302


def test_the_action_urls_are_closed_to_hunters_too(client):
    """The pages being gated is not enough -- these are the URLs that actually write."""
    report, flag = _report(), _flag()
    client.force_login(_user())

    for name, pk in (('mod_hide_blurb', report.pk), ('mod_dismiss_blurb', report.pk),
                     ('mod_approve_flag', flag.pk), ('mod_dismiss_flag', flag.pk)):
        resp = client.post(reverse(name, args=[pk]), {'reason': 'let me in'})
        assert resp.status_code == 302 and resp.url == '/', name

    report.rating.refresh_from_db()
    flag.game.refresh_from_db()
    assert report.rating.blurb_hidden is False
    assert flag.game.is_delisted is False
    assert ModerationAction.objects.count() == 0, 'a hunter wrote to the audit log'


def test_an_action_cannot_be_performed_by_GET(client):
    """A decision mutates live data. A GET would be followed by a crawler, a prefetcher, or a
    bookmark -- so the action views define no `get` at all."""
    report = _report()
    client.force_login(_user('moderator'))

    resp = client.get(reverse('mod_hide_blurb', args=[report.pk]))

    assert resp.status_code == 405, 'the action answered a GET'
    report.rating.refresh_from_db()
    assert report.rating.blurb_hidden is False


# ── the queues render what a decision needs ──────────────────────────────────────────────────────

def test_the_take_and_both_hunters_are_on_the_row(client):
    """A moderator deciding from a reason code alone is rubber-stamping the reporter. The words
    lead, and BOTH sides are named -- a reporter who reports everything is as much a pattern worth
    seeing as an author who writes slurs."""
    report = _report(blurb='an objectionable sentence')
    client.force_login(_user('moderator'))

    body = client.get(reverse('mod_quick_takes')).content.decode()

    assert 'an objectionable sentence' in body
    assert report.rating.profile.psn_username in body, 'the author is not shown'
    assert report.reporter.psn_username in body, 'the reporter is not shown'
    assert 'Harassment' in body


def test_the_flag_row_says_what_approving_will_change(client):
    """"Game has been delisted" is what was claimed; what a moderator is deciding is what the button
    writes. The shovelware lock especially: it overrides the automated classifier permanently and
    does not look heavier than the others."""
    _flag('is_shovelware')
    client.force_login(_user('moderator'))

    body = client.get(reverse('mod_game_flags')).content.decode()

    assert 'shovelware LOCK' in body
    assert 'overrides the automated classifier' in body


def test_a_no_op_flag_type_does_not_promise_a_change(client):
    """`missing_vr` writes nothing. Saying "approving updates this game's flags" there would be
    false, and a moderator would wonder why nothing happened."""
    _flag('missing_vr')
    client.force_login(_user('moderator'))

    body = client.get(reverse('mod_game_flags')).content.decode()

    assert 'It changes no field' in body


def test_a_handled_row_offers_no_buttons(client):
    """The service refuses a second action anyway; not drawing the button is what stops a moderator
    discovering that by being told off."""
    report = _report()
    report.status = 'dismissed'
    report.save(update_fields=['status'])
    client.force_login(_user('moderator'))

    body = client.get(reverse('mod_quick_takes') + '?status=dismissed').content.decode()

    assert 'Hollow Knight' in body
    assert 'mod_hide_blurb' not in body
    assert '/hide/' not in body, 'a handled row still offers the action'


# ── acting ───────────────────────────────────────────────────────────────────────────────────────

def test_hiding_from_the_queue_writes_the_change_and_the_log(client):
    report = _report()
    moderator = _user('moderator')
    client.force_login(moderator)

    client.post(reverse('mod_hide_blurb', args=[report.pk]), {'reason': 'Targeted abuse.'})

    report.rating.refresh_from_db()
    assert report.rating.blurb_hidden is True
    entry = ModerationAction.objects.get()
    assert entry.actor == moderator and entry.reason == 'Targeted abuse.'


def test_acting_returns_you_to_the_list_you_were_reading(client):
    """Not to the top of `pending`. A moderator working page 3 of `all` should stay there."""
    report = _report()
    client.force_login(_user('moderator'))

    resp = client.post(reverse('mod_hide_blurb', args=[report.pk]),
                       {'reason': 'Abuse.', 'next': '/mod/quick-takes/?status=all&page=3'})

    assert resp.status_code == 302
    assert resp.url == '/mod/quick-takes/?status=all&page=3'


def test_an_offsite_next_is_refused(client):
    """`next` arrives in the POST body. Unvalidated it is an open redirect: a crafted form could
    bounce a signed-in moderator to another origin, which is worth more to an attacker here than
    on most pages because the person following it is staff."""
    report = _report()
    client.force_login(_user('moderator'))

    resp = client.post(reverse('mod_hide_blurb', args=[report.pk]),
                       {'reason': 'Abuse.', 'next': 'https://evil.test/steal'})

    assert resp.url == reverse('mod_quick_takes'), 'followed an off-site next'


def test_a_bare_querystring_next_does_not_500(client):
    """`redirect()` treats a string with no slash as a VIEW NAME, so a relative-looking `next`
    raises NoReverseMatch rather than redirecting -- which is how this was first written."""
    report = _report()
    client.force_login(_user('moderator'))

    resp = client.post(reverse('mod_hide_blurb', args=[report.pk]),
                       {'reason': 'Abuse.', 'next': '?status=all'})

    assert resp.status_code == 302
    assert resp.url == reverse('mod_quick_takes')


def test_a_missing_reason_is_refused_and_says_so(client):
    """Required at the service, so the browser's `required` is a mirror rather than the guard. A mod
    who gets past the browser must still be told, not silently ignored."""
    report = _report()
    client.force_login(_user('moderator'))

    resp = client.post(reverse('mod_hide_blurb', args=[report.pk]), {'reason': ''}, follow=True)

    report.rating.refresh_from_db()
    assert report.rating.blurb_hidden is False
    assert any('reason is required' in str(m).lower() for m in resp.context['messages'])


def test_acting_on_something_already_handled_says_so_instead_of_lying(client):
    """Two moderators on one queue is the ordinary case, not a race to engineer. The loser must be
    told, and must not write a second entry claiming a change they did not make."""
    report = _report()
    first = _user('moderator')
    client.force_login(first)
    client.post(reverse('mod_hide_blurb', args=[report.pk]), {'reason': 'Abuse.'})

    client.force_login(_user('moderator'))
    resp = client.post(reverse('mod_dismiss_blurb', args=[report.pk]),
                       {'reason': 'Looks fine to me.'}, follow=True)

    assert any('already handled' in str(m).lower() for m in resp.context['messages'])
    assert ModerationAction.objects.count() == 1
    report.refresh_from_db()
    assert report.reviewed_by == first


def test_approving_a_flag_from_the_queue_changes_the_game(client):
    flag = _flag('delisted')
    client.force_login(_user('moderator'))

    client.post(reverse('mod_approve_flag', args=[flag.pk]), {'reason': 'Confirmed removed.'})

    flag.game.refresh_from_db()
    assert flag.game.is_delisted is True
    assert ModerationAction.objects.get().changed['is_delisted'] == [False, True]


# ── the queue does not query per row ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize('name,make', [('mod_quick_takes', _report), ('mod_game_flags', _flag)])
def test_a_queue_does_not_grow_a_query_per_row(client, name, make):
    """Reports accumulate forever. A row walks report -> rating -> concept -> game and
    report -> reporter, so without select_related this is four queries a row -- the N+1 shape this
    project has a documented history with. Asserted as "does not grow" rather than a magic number."""
    client.force_login(_user('moderator'))
    make()
    with CaptureQueriesContext(connection) as few:
        client.get(reverse(name))

    for _ in range(6):
        make()
    with CaptureQueriesContext(connection) as many:
        client.get(reverse(name))

    assert len(many.captured_queries) <= len(few.captured_queries) + 2, (
        f'{len(few.captured_queries)} queries for 1 row, {len(many.captured_queries)} for 7 -- '
        f'the queue queries per row'
    )


def test_the_status_filter_narrows_and_survives_a_bad_value(client):
    pending = _report(blurb='still waiting')
    handled = _report(blurb='already done')
    handled.status = 'dismissed'
    handled.save(update_fields=['status'])
    client.force_login(_user('moderator'))

    default = client.get(reverse('mod_quick_takes')).content.decode()
    assert 'still waiting' in default and 'already done' not in default

    every = client.get(reverse('mod_quick_takes') + '?status=all').content.decode()
    assert 'still waiting' in every and 'already done' in every

    # A hand-typed status must not 500 or empty the queue silently.
    junk = client.get(reverse('mod_quick_takes') + '?status=nonsense')
    assert junk.status_code == 200
    assert 'still waiting' in junk.content.decode(), 'a bad filter should fall back to pending'

# ── copy and component discipline ────────────────────────────────────────────────────────────────

def _rendered_text(body):
    """Everything on the page a person can end up reading, with entities resolved.

    Asserted against the RENDER rather than the template source, because a template comment is not
    copy and a `{% comment %}` block may say whatever it likes.

    The first cut of this helper was broken three ways at once, and every one of them made it weaker
    than it read:

    - Its script/style strip contained literal CONTROL BYTES. It was authored through a shell
      heredoc, which turned the regex escapes into 0x08 and 0x01, leaving a pattern that matched
      nothing. Script and style bodies counted as page copy.
    - Entities were never decoded, so `&mdash;` -- which lands on screen as an em dash -- sailed
      straight through the assertion named after it. The docstring claimed otherwise.
    - Attribute text was discarded along with the tags. The newest user-visible copy on this branch
      is an `aria-label`, which a screen reader reads out loud, so attributes are kept.

    Keeping attributes puts class names and inline custom properties in scope too. That is safe for
    these three assertions: `--pp-x` never produces a SPACE-hyphen-hyphen-SPACE run.
    """
    import html
    import re

    # Two plain patterns rather than one with a backreference: a backslash-one escape in a NON-raw
    # Python string is an octal escape, and that is what mangled this helper into a control byte
    # the first time, leaving a pattern that matched nothing. Nothing here needs a capture group.
    # is an octal escape, and this helper has now been mangled twice by exactly that -- the
    # first time into a control byte, which left the pattern matching nothing at all. Nothing
    # here needs a capture group, so nothing here uses one.
    for tag in ('script', 'style'):
        body = re.sub(r"(?is)<%s[^>]*>.*?</%s\s*>" % (tag, tag), " ", body)
    body = re.sub(r"(?s)<!--.*?-->", " ", body)
    return html.unescape(body)


@pytest.mark.parametrize('name', PAGES)
def test_no_em_dashes_in_what_a_moderator_reads(client, name):
    """House style, and it is about the RENDERED page, not the source. Both spellings count: a
    literal em dash and the `&mdash;` entity land on screen identically, and a double hyphen reads
    as the same punctuation the rule exists to avoid."""
    client.force_login(_user('moderator'))
    _report()
    _flag('is_shovelware')

    text = _rendered_text(client.get(reverse(name)).content.decode())

    for offender, label in (('—', 'an em dash'), ('–', 'an en dash'),
                            (' -- ', 'a double hyphen reading as an em dash')):
        at = text.find(offender)
        # The context matters more than the verdict: "an em dash reached the page" sends you
        # hunting through three templates and a context processor.
        assert at == -1, f'{label} reached the page: ...{text[max(0, at - 90):at + 90]!r}...'


@pytest.mark.parametrize('name', ['mod_quick_takes', 'mod_game_flags'])
def test_the_status_filter_uses_the_site_wide_switcher(client, name):
    """`pp-switch__chip` is the ONE tab treatment site-wide (components/switcher.css). An invented
    class name has no CSS behind it and fails SILENTLY -- the filters render as bare inline links
    with nothing separating them, which is how this shipped and what the owner reported."""
    import pathlib as _pathlib

    client.force_login(_user('moderator'))

    body = client.get(reverse(name)).content.decode()
    assert 'pp-switch__chip' in body, 'the filter is not using the shared switcher'

    # And the class has to exist in the COMPILED sheet, not just in a source component file.
    # `static/css/output.css` deliberately, not `staticfiles/`: staticfiles is gitignored and
    # generated by collectstatic at deploy time, so CI has no such directory and a test that read
    # it would be red on every run. static/output.css is the tracked artifact that gets deployed
    # and then collected, which makes it the honest thing to assert on.
    assert '.pp-switch__chip' in _built_css(), 'the switcher class is missing from the built CSS'

# ── getting out, and getting sideways ────────────────────────────────────────────────────────────

def _nav_row(body):
    """The queue page's page-level nav row, isolated so an assertion about it cannot be answered by
    something else on the page.

    A helper rather than `body[body.index(a):body.index(b)]` inline. `str.index` raises ValueError
    from inside the test body, which reports as a red traceback about string slicing instead of as
    "the thing this test is about has gone" -- and if the two markers ever swapped order the slice
    would silently become empty, quietly passing every negative assertion made against it.
    """
    start = body.find('flex flex-wrap items-center gap-2 mb-3')
    assert start != -1, 'the queue page has no page-level nav row at all'
    end = body.find('</div>', start)
    assert end != -1, 'the nav row is never closed'
    return body[start:end]


@pytest.mark.parametrize('name', ['mod_quick_takes', 'mod_game_flags'])
def test_a_queue_has_a_real_way_back_not_only_a_breadcrumb(client, name):
    """The breadcrumb is the smallest target on the page, and it was the only route out."""
    client.force_login(_user('moderator'))

    header = _nav_row(client.get(reverse(name)).content.decode())

    assert reverse('mod_center') in header, 'no way back to the Mod Center from the queue header'


def test_a_queue_links_straight_to_the_other_queue(client):
    """Moving between the two went via the landing: two clicks for a move a moderator makes
    constantly."""
    client.force_login(_user('moderator'))

    body = client.get(reverse('mod_quick_takes')).content.decode()

    assert reverse('mod_game_flags') in body, 'no direct link to the sibling queue'
    assert 'Game Flags' in body


def test_the_sibling_link_carries_its_open_count(client):
    """The count is the reason to go there. Without it the link is just another word."""
    _flag()
    _flag()
    client.force_login(_user('moderator'))

    header = _nav_row(client.get(reverse('mod_quick_takes')).content.decode())

    assert '2 waiting' in header, 'the sibling queue does not show how much is waiting in it'


def test_a_quiet_sibling_is_still_linked_but_wears_no_badge(client):
    """A zero badge is noise: it draws the eye to a queue with nothing in it. But the LINK has to
    survive the badge going -- asserting only the absence meant that deleting the entire nav row
    kept this test green."""
    client.force_login(_user('moderator'))

    header = _nav_row(client.get(reverse('mod_quick_takes')).content.decode())

    assert reverse('mod_game_flags') in header, 'the sibling link went with its badge'
    assert 'badge-warning' not in header


def test_the_queue_registry_has_one_definition(client):
    """The landing and both queue headers read the same list, so adding a third queue touches one
    place rather than three that can disagree about what exists."""
    from trophies.views.moderation_views import queue_summaries

    slugs = {q['slug'] for q in queue_summaries()}
    assert slugs == {'quick-takes', 'game-flags'}

    client.force_login(_user('moderator'))
    landing = client.get(reverse('mod_center')).content.decode()
    for queue in queue_summaries():
        assert str(queue['url']) in landing

# ── the way IN: the avatar menu entry and its marker ─────────────────────────────────────────────
#
# The queues are only useful if a moderator knows there is something in them. These cover the one
# route to the Mod Center that is not a bookmark, and the marker that says it is worth taking.

CHROME_PAGE = 'home'          # any page: the navbar is site-wide chrome, which is the point

#: The marker, matched AS the marker. `'>2<' in body` was the first cut, and it was safe only by
#: accident: the landing renders site-stat tallies, so a test that creates 11 games could have had
#: its `'>11<'` answered by a stats tile rather than the avatar -- passing, or failing, for reasons
#: with nothing to do with moderation.
MARKER = re.compile(r'<span class="pp-av__queue"[^>]*>([^<]*)</span>')


def _marker(body):
    """What the avatar's queue marker says, or None when it is not rendered at all."""
    found = MARKER.search(body)
    return found.group(1).strip() if found else None


def _chrome(client):
    return client.get(reverse(CHROME_PAGE)).content.decode()


@pytest.mark.parametrize('role', ['moderator', 'admin'])
def test_a_moderator_is_offered_the_mod_center_in_the_avatar_menu(client, role):
    client.force_login(_user(role))
    assert reverse('mod_center') in _chrome(client)


def test_an_ordinary_hunter_is_not(client):
    """The menu reads the same for every hunter, minus this one entry."""
    client.force_login(_user())
    assert reverse('mod_center') not in _chrome(client)


def test_a_signed_out_visitor_is_not(client):
    assert reverse('mod_center') not in _chrome(client)


def test_the_entry_is_there_when_the_queues_are_empty(client):
    """A link that appears only when there is work is a link nobody can find when they go looking
    for it. The marker is what is conditional, not the entry."""
    client.force_login(_user('moderator'))

    body = _chrome(client)

    assert f'href="{reverse("mod_center")}"' in body, 'the word, but no link'
    assert _marker(body) is None, 'a marker with nothing behind it'


def test_open_reports_put_a_marker_on_the_avatar(client):
    _report()
    _flag()
    client.force_login(_user('moderator'))

    body = _chrome(client)

    assert _marker(body) == '2', 'the marker does not carry how much is waiting'
    assert '2 waiting' in body, 'the menu entry does not carry the count'


def test_the_marker_says_its_number_out_loud(client):
    """The marker itself is aria-hidden, so the avatar's own label has to carry it."""
    _report()
    client.force_login(_user('moderator'))

    assert '1 report waiting to be moderated' in _chrome(client)


def test_a_crowded_queue_does_not_stretch_the_marker(client):
    """Ten reports is still "go and look". A three-digit pill on a 38px avatar is not."""
    for _ in range(11):
        _flag()
    client.force_login(_user('moderator'))

    body = _chrome(client)

    assert _marker(body) == '9+'
    assert '11 waiting' in body, 'the exact number still belongs in the menu, where there is room'


def test_an_ordinary_hunters_page_does_not_count_anything(client):
    """The gate has to come BEFORE the work. This rides every page render on the site, and almost
    nobody who triggers it is a moderator."""
    client.force_login(_user())
    _report()

    with CaptureQueriesContext(connection) as captured:
        client.get(reverse(CHROME_PAGE))

    moderation = [q['sql'] for q in captured.captured_queries
                  if 'blurbreport' in q['sql'].lower() or 'gameflag' in q['sql'].lower()]
    assert moderation == [], f'counted the queues for a non-moderator: {moderation}'


def test_the_marker_counts_live_and_is_never_stale(client):
    """The count was cached for five minutes. Three separate paths could leave the marker claiming
    work against a page saying "nothing waiting", one click apart -- so it is not cached, and this
    is the test that says the number is always the current one."""
    report = _report()
    moderator = _user('moderator')
    client.force_login(moderator)
    assert _marker(_chrome(client)) == '1'

    moderation_service.dismiss_blurb_report(report, moderator, 'not a problem')

    assert _marker(_chrome(client)) is None, 'the marker outlived the work'


def test_an_admin_bulk_sweep_does_not_leave_a_stale_marker(client):
    """The path nothing could have caught. Django admin's bulk actions move these rows out of
    `pending` with `queryset.update()`, which goes through no service and fires no signal."""
    _flag()
    _flag()
    admin = _user('admin')
    client.force_login(admin)
    assert _marker(_chrome(client)) == '2'

    GameFlag.objects.filter(status='pending').update(status='dismissed')

    assert _marker(_chrome(client)) is None


def test_the_marker_and_the_page_body_agree_on_one_render(client):
    """Same response, two surfaces. The Mod Center pays for the exact number; the navbar beside it
    must not be rendering a different one."""
    _report()
    _flag()
    client.force_login(_user('moderator'))

    resp = client.get(reverse('mod_center'))
    body = resp.content.decode()

    assert resp.context['open_total'] == 2
    assert _marker(body) == '2', 'the navbar disagreed with the page it sits on'


def test_the_marker_and_the_mod_center_agree(client):
    """Two surfaces, one definition of "waiting". A marker that counts differently from the page it
    points at is worse than no marker."""
    _report()
    _flag()
    _flag()
    client.force_login(_user('moderator'))

    landing = client.get(reverse('mod_center'))

    assert moderation_service.open_report_count() == landing.context['open_total'] == 3

# ── the gate, enumerated from the URL conf rather than by hand ───────────────────────────────────

def _every_mod_url():
    """Every route under `/mod/`, read off the URL conf.

    Enumerated rather than listed, because a hand-written list is what somebody forgets. The tests
    above name the pages they exercise; this one asks the resolver, so an eighth route added without
    the mixin fails here on the day it is added rather than the day it is found.
    """
    from plat_pursuit import urls as root_urls

    found = []
    for pattern in root_urls.urlpatterns:
        route = getattr(getattr(pattern, 'pattern', None), '_route', '')
        if not route.startswith('mod/'):
            continue
        found.append('/' + route.replace('<int:pk>', '1'))
    return found


def test_the_url_conf_still_has_the_mod_urls_this_file_thinks_it_does():
    """A guard on the guard: if the routes move or are renamed, the sweep below would quietly
    exercise nothing and stay green."""
    urls = _every_mod_url()
    assert len(urls) == 7, f'expected 7 /mod/ routes, found {urls}'


def test_nobody_but_a_moderator_can_reach_anything_under_mod(client):
    """The question asked out loud: can a hunter, or a stranger, see any of this?

    Every route, both audiences, no exceptions carved out. These URLs write live game data -- one of
    them sets `shovelware_lock`, which permanently overrides the automated classifier.
    """
    urls = _every_mod_url()

    for url in urls:                                   # signed out
        resp = client.get(url)
        assert resp.status_code == 302, f'{url} answered a stranger with {resp.status_code}'
        assert '/login' in resp.url.lower(), f'{url} did not send a stranger to login'

    client.force_login(_user())                        # signed in, ordinary hunter
    for url in urls:
        resp = client.get(url)
        assert resp.status_code == 302, f'{url} answered a hunter with {resp.status_code}'
        assert resp.url == '/', f'{url} told a hunter it exists'


def test_a_moderator_who_loses_the_role_loses_the_link_and_the_pages(client):
    """Revoking is the moment this has to work, and it is the one nobody tests."""
    moderator = _user('moderator')
    client.force_login(moderator)
    assert reverse('mod_center') in _chrome(client)

    moderator.role = ''
    moderator.is_staff = False
    moderator.save()

    assert reverse('mod_center') not in _chrome(client)
    assert client.get(reverse('mod_center')).url == '/'

# ── writes, specifically ─────────────────────────────────────────────────────────────────────────
#
# The sweep above proves the pages are unreachable. These prove the same for the calls that actually
# change something -- by asserting on the DATABASE afterwards, not on the status code. A gate that
# redirects but writes first would pass a status-code test.

def test_a_stranger_posting_to_every_action_writes_nothing(client):
    """Signed out, straight at the URLs, with a well-formed body."""
    report, flag = _report(), _flag()

    for name, pk in (('mod_hide_blurb', report.pk), ('mod_dismiss_blurb', report.pk),
                     ('mod_approve_flag', flag.pk), ('mod_dismiss_flag', flag.pk)):
        resp = client.post(reverse(name, args=[pk]), {'reason': 'let me in'})
        assert resp.status_code == 302 and '/login' in resp.url.lower(), name

    report.refresh_from_db()
    report.rating.refresh_from_db()
    flag.refresh_from_db()
    flag.game.refresh_from_db()
    assert report.status == 'pending' and report.rating.blurb_hidden is False
    assert flag.status == 'pending' and flag.game.is_delisted is False
    assert ModerationAction.objects.count() == 0


def test_a_deactivated_moderator_posting_to_every_action_writes_nothing(client):
    """Revoking access is the moment the gate has to hold, and a live session is what survives it."""
    moderator = _user('moderator')
    client.force_login(moderator)
    report, flag = _report(), _flag()

    moderator.is_active = False
    moderator.save()

    for name, pk in (('mod_hide_blurb', report.pk), ('mod_approve_flag', flag.pk)):
        client.post(reverse(name, args=[pk]), {'reason': 'still here'})

    report.rating.refresh_from_db()
    flag.game.refresh_from_db()
    assert report.rating.blurb_hidden is False
    assert flag.game.is_delisted is False
    assert ModerationAction.objects.count() == 0


def test_a_forged_cross_site_post_is_refused(client):
    """"Guessing the link" includes a page that makes a logged-in MODERATOR's browser post for it.
    The session alone must not be enough; the form's CSRF token is the second half."""
    from django.test import Client

    strict = Client(enforce_csrf_checks=True)
    strict.force_login(_user('moderator'))
    flag = _flag()

    resp = strict.post(reverse('mod_approve_flag', args=[flag.pk]), {'reason': 'forged'})

    assert resp.status_code == 403
    flag.refresh_from_db()
    assert flag.status == 'pending'
    assert ModerationAction.objects.count() == 0


def test_a_hunter_cannot_submit_a_flag_that_is_already_approved(client):
    """The reporting API is open to every hunter by design. The escalation to check is whether the
    body can carry `status` through to the row -- it decides its own status, and only 'pending' is
    reachable from outside."""
    profile = ProfileFactory(is_linked=True)
    client.force_login(profile.user)
    game = GameFactory()

    resp = client.post(
        f'/api/v1/games/{game.pk}/flag/',
        {'flag_type': 'delisted', 'details': 'x', 'status': 'approved', 'reviewed_by': profile.pk},
        content_type='application/json')

    assert resp.status_code == 200
    flag = GameFlag.objects.get(game=game)
    assert flag.status == 'pending', 'a reporter set their own flag to approved'
    assert flag.reviewed_by is None
    game.refresh_from_db()
    assert game.is_delisted is False, 'submitting a flag applied it'


def test_a_hunter_cannot_report_a_take_straight_into_action_taken(client):
    reporter = ProfileFactory(is_linked=True)
    report = _report()
    rating = report.rating
    client.force_login(reporter.user)

    resp = client.post(
        f'/api/v1/ratings/blurb/{rating.pk}/report/',
        {'reason': 'spam', 'status': 'action_taken'}, content_type='application/json')

    assert resp.status_code == 200
    filed = BlurbReport.objects.get(rating=rating, reporter=reporter)
    assert filed.status == 'pending'
    rating.refresh_from_db()
    assert rating.blurb_hidden is False


def test_the_django_admin_bulk_actions_stay_out_of_a_moderators_reach(client):
    """The admin can approve flags and hide blurbs too, and it does NOT go through
    `moderation_service` -- so it writes no reason and no audit entry. That is acceptable for an
    admin doing a sweep and is not something a moderator should have: their route is the queue,
    which records why. `is_staff` is False for a moderator by the role lockstep, and this is what
    holds the two apart."""
    moderator = _user('moderator')
    assert moderator.is_staff is False, 'the role lockstep changed; the admin is now open to mods'
    client.force_login(moderator)

    for url in ('/admin/trophies/gameflag/', '/admin/trophies/blurbreport/'):
        resp = client.get(url)
        assert resp.status_code == 302, f'{url} let a moderator in'
        assert '/admin/login' in resp.url, url


# -- what a row promises approving will do -------------------------------------------------------

def test_every_no_op_flag_type_says_it_changes_nothing(client):
    """The row's whole job is saying what approving WILL do. The template used to name the no-op
    types itself and named two of the three: an `other` flag was told it would update the game.
    """
    from trophies.services.game_flag_service import GameFlagService

    for flag_type in GameFlagService.NO_OP_FLAG_TYPES:
        GameFlag.objects.all().delete()
        _flag(flag_type)
        client.force_login(_user('moderator'))

        body = client.get(reverse('mod_game_flags')).content.decode()

        assert 'It changes no field' in body, f'{flag_type} promised a change it does not make'
        assert 'updates this game' not in body, f'{flag_type} promised a change it does not make'


def test_the_no_op_list_matches_what_approving_actually_writes(client):
    """The round trip, in the direction that fails. Approve EVERY flag type and compare what the
    database did against what the set claims, so a fourteenth flag type cannot be quietly
    mis-described by a page written before it existed.

    Each game is first set to the OPPOSITE of what its flag will write. Without that, `not_delisted`
    looks like a no-op: it writes `is_delisted = False` onto a game that was already False, and
    `changed` records real diffs, not attempted writes. That is a property of the log, not of the
    flag, and reading it as one is how a genuinely-writing type would get called harmless.
    """
    from trophies.services.game_flag_service import GameFlagService

    moderator = _user('moderator')
    for flag_type, _label in GameFlag.FLAG_TYPES:
        flag = _flag(flag_type)
        if flag_type in GameFlagService.FIELD_ACTIONS:
            field, value = GameFlagService.FIELD_ACTIONS[flag_type]
            setattr(flag.game, field, not value)
            flag.game.save(update_fields=[field])

        action = moderation_service.approve_game_flag(flag, moderator, 'checking the contract')

        if flag_type in GameFlagService.NO_OP_FLAG_TYPES:
            assert not action.changed, f'{flag_type} is called a no-op but wrote {action.changed}'
        else:
            assert action.changed, f'{flag_type} wrote nothing but is missing from NO_OP_FLAG_TYPES'


def test_the_shovelware_list_matches_what_sets_the_lock(client):
    """`shovelware_lock` permanently overrides the automated classifier, and the row calls that out
    on its own line. Which types earn that line is not the template's to decide.

    Asserts the LOCK ITSELF rather than the log's diff: a lock that was already set writes no diff,
    and the question here is which types leave a game locked.
    """
    from trophies.services.game_flag_service import GameFlagService

    moderator = _user('moderator')
    for flag_type, _label in GameFlag.FLAG_TYPES:
        flag = _flag(flag_type)
        assert flag.game.shovelware_lock is False

        moderation_service.approve_game_flag(flag, moderator, 'checking the contract')
        flag.game.refresh_from_db()

        assert flag.game.shovelware_lock is (flag_type in GameFlagService.SHOVELWARE_FLAG_TYPES), \
            flag_type


def test_the_queue_marker_is_not_painted_over_by_the_avatar_ring():
    """A source guard, because nothing else in the suite can see it.

    `.pp-av::after` (the ring) and `::before` (the syncing arc) are generated content, so they paint
    after every real child no matter the source order. Without a z-index the ring draws straight over
    the marker -- which is what shipped, and what a human had to spot in a browser.
    """
    css = (ROOT / 'static' / 'css' / 'components' / 'chrome.css').read_text(encoding='utf-8')

    rule = css[css.index('.pp-av__queue {'):]
    rule = rule[:rule.index('}')]
    assert 'z-index' in rule, 'the ring will paint over the marker'

    built = _built_css()
    assert '.pp-av__queue' in built, 'built css is stale: run npm run build'
    built_rule = built[built.index('.pp-av__queue{'):]
    assert 'z-index:1' in built_rule[:built_rule.index('}')], 'z-index missing from the built css'


@pytest.mark.parametrize('css_class', ['pp-av__queue', 'pp-avmenu__count'])
def test_the_navbar_classes_exist_in_the_served_css(css_class):
    """The guard the switcher got, for the two classes this branch introduced.

    An unmatched class fails SILENTLY: the element renders, the markup reads correctly, and the page
    simply disagrees. Asserting the class appears in the rendered BODY proves nothing, because that
    is exactly what a class with no CSS behind it also does.
    """
    assert f'.{css_class}' in _built_css(), f'{css_class} has no CSS behind it in the built sheet'


def test_the_queue_marker_is_not_wearing_a_sync_colour():
    """Sync owns success, warning and error between them. On the first cut the marker was --pp-error
    -- the same red as a failed sync's ring and the LED that ring colours -- so a moderator with a
    broken sync got a red ring, a red halo, a red dot and a red pill, and "your account is broken"
    became indistinguishable from "the site has work"."""
    css = (ROOT / 'static' / 'css' / 'components' / 'chrome.css').read_text(encoding='utf-8')

    rule = css[css.index('.pp-av__queue {'):]
    rule = rule[:rule.index('}')]

    for sync_colour in ('--pp-error', '--pp-warning', '--pp-success'):
        assert sync_colour not in rule, f'the marker is wearing {sync_colour}, a sync state'
