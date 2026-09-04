"""The Mod Centre surface: the gate, the queues, and acting on a row.

`test_moderation_service` covers what a decision DOES. This covers reaching it: that the pages are
closed to everyone but moderators and admins, that a queue does not query per row, that an action is
a POST, and that acting returns you to the list you were reading.

The gate is the part worth being paranoid about. These URLs write live game data -- one of them sets
`shovelware_lock`, which permanently overrides the automated classifier.
"""
import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from tests.factories import ConceptFactory, GameFactory, ProfileFactory, UserFactory
from trophies.models import BlurbReport, GameFlag, ModerationAction, UserConceptRating

pytestmark = pytest.mark.django_db

PAGES = ['mod_centre', 'mod_quick_takes', 'mod_game_flags']


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
    """The page's visible text: markup, script and style stripped out.

    Asserted against the RENDER rather than the template source, because a template comment is not
    copy and a `{% comment %}` block may say whatever it likes.
    """
    import re

    body = re.sub(r'(?is)<(script|style).*?</>', ' ', body)
    return re.sub(r'(?s)<[^>]+>', ' ', body)


@pytest.mark.parametrize('name', PAGES)
def test_no_em_dashes_in_what_a_moderator_reads(client, name):
    """House style, and it is about the RENDERED page, not the source. Both spellings count: a
    literal em dash and the `&mdash;` entity land on screen identically, and a double hyphen reads
    as the same punctuation the rule exists to avoid."""
    client.force_login(_user('moderator'))
    _report()
    _flag('is_shovelware')

    text = _rendered_text(client.get(reverse(name)).content.decode())

    assert '—' not in text, 'an em dash reached the page'
    assert '–' not in text, 'an en dash reached the page'
    assert ' -- ' not in text, 'a double hyphen is reading as an em dash on the page'


@pytest.mark.parametrize('name', ['mod_quick_takes', 'mod_game_flags'])
def test_the_status_filter_uses_the_site_wide_switcher(client, name):
    """`pp-switch__chip` is the ONE tab treatment site-wide (components/switcher.css). An invented
    class name has no CSS behind it and fails SILENTLY -- the filters render as bare inline links
    with nothing separating them, which is how this shipped and what the owner reported."""
    import pathlib as _pathlib

    client.force_login(_user('moderator'))

    body = client.get(reverse(name)).content.decode()
    assert 'pp-switch__chip' in body, 'the filter is not using the shared switcher'

    # And the class has to actually exist in the compiled sheet the site serves.
    css = (_pathlib.Path(__file__).resolve().parents[2]
           / 'static' / 'css' / 'output.css').read_text(encoding='utf-8')
    assert '.pp-switch__chip' in css, 'the switcher class is missing from the built CSS'
