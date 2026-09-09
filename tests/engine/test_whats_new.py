"""What's New: the entries, who is due one, and the archive page.

Content is code (core/whats_new.py), the "seen" marker is the id of the newest entry a hunter dismissed
(ui_flags['whats_new_seen']), and precedence against the 1.0 greeting is decided in the view that can see
both. Doc: docs/features/whats-new.md.
"""
import os
import re
from datetime import date, timedelta
from pathlib import Path

import pytest
from django.conf import settings as dj_settings
from django.urls import reverse
from django.utils import timezone

from core import whats_new
from tests.factories import ProfileFactory

pytestmark = pytest.mark.django_db

CF = {'HTTP_CF_RAY': '8f0000000000abcd-LHR'}
QUICK = 'api:user-quick-settings'


def _synced_client(client, **user_kwargs):
    """A hunter on the lobby, joined long enough ago to be due the newest entry."""
    profile = ProfileFactory(is_linked=True, sync_status='synced', total_trophies=10)
    user = profile.user
    user.date_joined = timezone.now() - timedelta(days=365)
    for k, v in user_kwargs.items():
        setattr(user, k, v)
    user.save()
    client.force_login(user)
    return client, profile


def _code(path):
    """Comment-stripped source. A comment that NAMES the forbidden string is how this kind of pin
    silently stops guarding, and it has bitten this codebase three times."""
    text = Path(path).read_text(encoding='utf-8')
    text = re.sub(r'/\*.*?\*/', '', text, flags=re.S)
    text = re.sub(r'^\s*//.*$', '', text, flags=re.M)
    text = re.sub(r'{%\s*comment\s*%}.*?{%\s*endcomment\s*%}', '', text, flags=re.S)
    return text


# ── the entries themselves ────────────────────────────────────────────────────────────────────────────

def test_entry_ids_are_unique():
    """The id IS the seen marker. Two entries sharing one means dismissing either silences both, and the
    second announcement is never seen by anybody who read the first."""
    ids = [e.id for e in whats_new.ENTRIES]
    assert len(ids) == len(set(ids)), f'duplicate entry ids: {ids}'


def test_entries_are_newest_first():
    """`latest()` is ENTRIES[0], not a max() -- the order in the file IS the ordering, so a new entry
    appended to the bottom would ship as content nobody is ever due."""
    dates = [e.published for e in whats_new.ENTRIES]
    assert dates == sorted(dates, reverse=True), f'ENTRIES is not newest-first: {dates}'


def test_every_entry_has_something_to_say():
    for e in whats_new.ENTRIES:
        assert e.title.strip(), f'{e.id} has no title'
        assert e.beats, f'{e.id} has no beats, so the modal body would be empty'
        for label, copy in e.beats:
            assert label.strip() and copy.strip(), f'{e.id} has an empty beat'


def test_entry_links_stay_on_our_own_site():
    """The modal is a trusted surface that opens itself, unasked, over the page. A link out of it is the
    exact shape of a phishing lure, and an entry is a one-line edit away from carrying one."""
    for e in whats_new.ENTRIES:
        if not e.link_url:
            continue
        assert e.link_url.startswith('/'), f'{e.id} links off-site: {e.link_url}'
        assert not e.link_url.startswith('//'), f'{e.id} is a protocol-relative link off-site'
        assert e.link_label.strip(), f'{e.id} has a link with no label'


def test_by_id_finds_and_misses():
    assert whats_new.by_id(whats_new.ENTRIES[0].id) is whats_new.ENTRIES[0]
    assert whats_new.by_id('no-such-entry') is None


# ── the seen marker ───────────────────────────────────────────────────────────────────────────────────

def test_the_endpoint_stores_the_entry_id(client):
    """The ID, not a boolean. A boolean would make the FIRST entry the last one anybody ever sees."""
    client, profile = _synced_client(client)
    entry = whats_new.latest()

    resp = client.post(reverse(QUICK),
                       data={'setting': 'whats_new_seen', 'value': entry.id},
                       content_type='application/json')

    assert resp.status_code == 200
    profile.user.refresh_from_db()
    assert profile.user.ui_flags.get('whats_new_seen') == entry.id


def test_the_endpoint_rejects_an_unknown_entry(client):
    """Validated against the shipped entries rather than stored as given: an arbitrary string here
    matches no entry ever, which silently suppresses the modal for that account forever with nothing in
    the UI able to undo it."""
    client, profile = _synced_client(client)

    resp = client.post(reverse(QUICK),
                       data={'setting': 'whats_new_seen', 'value': 'made-up-entry'},
                       content_type='application/json')

    assert resp.status_code == 400
    profile.user.refresh_from_db()
    assert profile.user.ui_flags == {}


def test_the_endpoint_rejects_a_non_string(client):
    client, profile = _synced_client(client)

    resp = client.post(reverse(QUICK),
                       data={'setting': 'whats_new_seen', 'value': True},
                       content_type='application/json')

    assert resp.status_code == 400
    profile.user.refresh_from_db()
    assert profile.user.ui_flags == {}


def test_the_endpoint_requires_auth(client):
    resp = client.post(reverse(QUICK),
                       data={'setting': 'whats_new_seen', 'value': whats_new.latest().id},
                       content_type='application/json')
    assert resp.status_code in (401, 403)


def test_marking_seen_does_not_disturb_the_other_flags(client):
    """`ui_flags` is one dict shared with the one-shot education flags. A write that replaced it rather
    than merging would un-dismiss the Career explainer every time an entry was read."""
    client, profile = _synced_client(client)
    profile.user.ui_flags = {'career_explainer': True}
    profile.user.save(update_fields=['ui_flags'])

    client.post(reverse(QUICK),
                data={'setting': 'whats_new_seen', 'value': whats_new.latest().id},
                content_type='application/json')

    profile.user.refresh_from_db()
    assert profile.user.ui_flags.get('career_explainer') is True


# ── who is due ────────────────────────────────────────────────────────────────────────────────────────

def test_a_hunter_who_has_not_seen_it_gets_the_modal(client):
    client, _ = _synced_client(client)
    body = client.get('/', **CF).content.decode()
    assert 'id="whats-new"' in body


def test_a_hunter_who_dismissed_THIS_entry_does_not(client):
    client, profile = _synced_client(client)
    profile.user.ui_flags = {'whats_new_seen': whats_new.latest().id}
    profile.user.save(update_fields=['ui_flags'])

    body = client.get('/', **CF).content.decode()

    assert 'id="whats-new"' not in body


def test_a_hunter_who_dismissed_an_OLDER_entry_is_due_again(client):
    """The whole point of storing an id rather than a boolean, and the thing that makes this feature
    repeatable at all: yesterday's dismissal must not silence tomorrow's entry."""
    client, profile = _synced_client(client)
    profile.user.ui_flags = {'whats_new_seen': 'some-older-entry-id'}
    profile.user.save(update_fields=['ui_flags'])

    body = client.get('/', **CF).content.decode()

    assert 'id="whats-new"' in body


def test_a_BRAND_NEW_account_is_shown_the_newest_entry(client):
    """Signing up after the entry shipped does NOT skip you, and that is deliberate.

    An earlier cut skipped anyone who joined after publication, reasoning that a feature they have
    always had cannot be new to them. True about the feature, wrong about the message: what a new hunter
    takes from the notice is that the site is actively being built, which is worth more than the literal
    accuracy of "new". The date on the entry is what keeps it honest.
    """
    client, profile = _synced_client(client)
    # STRICTLY after the entry, derived from the entry rather than from "now". `timezone.now()` is only
    # after publication if the newest entry happens to be older than today -- so on the day an entry
    # ships this test would pass whether the skip existed or not, which is no test at all. Caught by
    # mutation: reinstating the skip left it green.
    published = whats_new.latest().published
    profile.user.date_joined = timezone.make_aware(
        timezone.datetime.combine(published + timedelta(days=30), timezone.datetime.min.time()))
    profile.user.save(update_fields=['date_joined'])

    assert 'id="whats-new"' in client.get('/', **CF).content.decode()


def test_the_modal_says_WHEN_the_entry_landed(client):
    """The date is what makes "new" checkable rather than something to take on trust -- and it is what
    lets a hunter who joined last week see for themselves that this landed before they did."""
    client, _ = _synced_client(client)
    entry = whats_new.latest()

    body = client.get('/', **CF).content.decode()
    # Everything AFTER the modal's id, so a date rendered somewhere else on the lobby cannot satisfy
    # this. The lobby carries dates (last sync, recent earns) and one of them landing in a body-wide
    # substring check is exactly how this guard would pass while the modal showed nothing.
    assert 'id="whats-new"' in body
    modal = body.split('id="whats-new"', 1)[1]

    assert 'wn__date' in modal, 'the modal renders no date element'
    assert entry.published.strftime('%Y-%m-%d') in modal, 'no machine-readable date'
    assert entry.published.strftime('%B %-d, %Y' if os.name != 'nt' else '%B %#d, %Y') in modal, (
        'no human-readable date'
    )


def test_the_launch_greeting_wins_and_whats_new_waits(client, settings):
    """PRECEDENCE. Two modals on one visit is two scrims back to back, and the second arrives while the
    reader is still working out what the first was.

    The 1.0 greeting wins because it fires exactly once in an account's life and cannot be deferred to a
    better moment. What's New can: its entry stays undismissed, so it is simply due next visit.
    """
    settings.PP_LAUNCH_DATE = timezone.now() + timedelta(days=1)   # everyone counts as existing
    client, profile = _synced_client(client)

    body = client.get('/', **CF).content.decode()

    assert 'id="launch-welcome"' in body
    assert 'id="whats-new"' not in body, 'both modals fired on one visit'
    profile.user.refresh_from_db()
    assert 'whats_new_seen' not in (profile.user.ui_flags or {}), 'the deferred entry was spent'


def test_whats_new_arrives_once_the_greeting_is_done(client, settings):
    """The other half of precedence: deferred, not cancelled."""
    settings.PP_LAUNCH_DATE = timezone.now() + timedelta(days=1)
    client, profile = _synced_client(client)
    profile.user.ui_flags = {'launch_welcome': True}      # greeting already dismissed
    profile.user.save(update_fields=['ui_flags'])

    body = client.get('/', **CF).content.decode()

    assert 'id="launch-welcome"' not in body
    assert 'id="whats-new"' in body


def test_is_due_says_no_to_anonymous():
    class Anon:
        is_authenticated = False
        ui_flags = {}
    assert whats_new.is_due(Anon()) is False


# ── the preview door ──────────────────────────────────────────────────────────────────────────────────

def test_staff_can_preview_after_dismissing(client):
    client, profile = _synced_client(client, is_staff=True)
    profile.user.ui_flags = {'whats_new_seen': whats_new.latest().id}
    profile.user.save(update_fields=['ui_flags'])

    body = client.get('/?preview=whats-new', **CF).content.decode()

    assert 'id="whats-new"' in body


def test_the_preview_door_does_not_leak(client):
    client, profile = _synced_client(client)
    profile.user.ui_flags = {'whats_new_seen': whats_new.latest().id}
    profile.user.save(update_fields=['ui_flags'])

    body = client.get('/?preview=whats-new', **CF).content.decode()

    assert 'id="whats-new"' not in body, 'the preview door leaked past the team gate'


# ── the archive page ──────────────────────────────────────────────────────────────────────────────────

def test_the_archive_lists_every_entry(client):
    body = client.get(reverse('whats_new'), **CF).content.decode()
    for entry in whats_new.ENTRIES:
        assert entry.title in body, f'{entry.id} is missing from the archive'


def test_the_archive_is_public(client):
    """The modal is a nudge for hunters already here; the page is the record, and what the site has been
    doing is a fair question for somebody deciding whether to sign up."""
    assert client.get(reverse('whats_new'), **CF).status_code == 200


def test_reading_the_archive_does_not_mark_anything_seen(client):
    """Reading the record is not dismissing the notice. A hunter who arrives here from a link should
    still meet the modal on Home, or the entry they never opened is silently spent."""
    client, profile = _synced_client(client)

    client.get(reverse('whats_new'), **CF)

    profile.user.refresh_from_db()
    assert 'whats_new_seen' not in (profile.user.ui_flags or {})
    assert 'id="whats-new"' in client.get('/', **CF).content.decode()


# ── source pins: the wiring a test client cannot execute ──────────────────────────────────────────────

def test_the_modal_settles_the_lobby_gate():
    """The gate is armed from the server's decision and released by end-of-body JS. A modal that never
    releases it leaves the lobby's count-ups frozen behind a modal the reader already closed."""
    base = Path(dj_settings.BASE_DIR)
    partial = _code(base / 'templates' / 'trophies' / 'partials' / 'home' / '_whats_new.html')
    assert 'ppSettleHomeModal' in partial
    assert 'DetailModal' in partial, 'not using the shared controller'


def test_the_modal_carries_its_ID_SCOPED_exit():
    """badge-inspect.css defines `.pp-detail-modal.is-closing` unscoped, and a clone without its own id
    inherits it -- dissolving the dialog's chrome while the text inside stays fully opaque. Found by
    audit twice already, on two different modals."""
    css = (Path(dj_settings.BASE_DIR) / 'static' / 'css' / 'components' / 'series-list.css').read_text(encoding='utf-8')
    assert '#whats-new.is-closing' in css
    assert '#whats-new.is-closing .pp-detail-modal__dialog' in css


def test_the_gate_arms_only_when_a_modal_is_actually_due(client):
    """An armed gate with no modal to release it would hold the lobby's motion for the full backstop
    timeout on every ordinary visit."""
    client, profile = _synced_client(client)
    profile.user.ui_flags = {'whats_new_seen': whats_new.latest().id}
    profile.user.save(update_fields=['ui_flags'])

    body = client.get('/', **CF).content.decode()

    assert 'ppAfterHomeModal' in body, 'the gate must still publish, or home-motion has nothing to call'
    gate = body.split('ppAfterHomeModal')[0]
    assert 'var pending = false' in gate, 'the gate armed with no modal on the page'


def test_a_dismissing_LINK_still_navigates():
    """The bug this pins was shipped and caught by a question, not by a test.

    Every close control carries the dismiss attribute, and two of them are <a href> links. The
    controller's handler called preventDefault on all of them -- correct while every consumer's only
    control was a <button>, and wrong the moment a link had one: clicking "See the boards" dismissed the
    modal and went NOWHERE.

    Not preventing at all is the opposite failure: an in-flight request is cancelled on unload, so the
    dismissal is lost and the reader meets the same notice again having already clicked through it. The
    handler has to hold the navigation until the write settles, and cap that wait.

    A source pin because this is a click through a live network call -- there is no Django test client
    path to it. What it can pin is that the branch still exists, since the way this regresses is somebody
    tidying the handler back down to one preventDefault.
    """
    js = _code(Path(dj_settings.BASE_DIR) / 'static' / 'js' / 'utils.js')
    handler = js.split('function DetailModal', 1)[1]

    assert "closest('a[href]')" in handler, 'the controller no longer treats a dismissing link specially'
    assert 'location.assign' in handler, 'a dismissing link is prevented but never navigated'
    assert 'e.metaKey' in handler and 'e.button !== 0' in handler, (
        'a ctrl/middle click is being intercepted; it belongs to the browser'
    )


def test_every_way_out_of_the_modal_dismisses_it():
    """All three controls mark it seen. A reader who clicked through to the feature has been told about
    it, and finding the notice waiting again next visit reads as the dismissal having failed."""
    partial = _code(Path(dj_settings.BASE_DIR) / 'templates' / 'trophies' / 'partials' / 'home' / '_whats_new.html')

    # The button, the entry link, the archive link, the scrim and the X.
    assert partial.count('data-wn-close') >= 5, (
        'a control can close this modal without marking it seen'
    )
    # And the links are real links, not buttons wearing a link class -- which is what would make the
    # navigation branch above dead code.
    assert partial.count('<a class="pp-howto__more"') == 2


def test_the_1_0_entry_is_on_the_archive_but_can_never_pop():
    """1.0 has its own greeting modal. An entry that could also fire would show a hunter the same
    announcement twice, once in each -- and nothing in the code says so, because the property comes from
    POSITION: `latest()` is ENTRIES[0], so anything below the top is archive-only by construction.

    That makes it exactly the kind of thing that breaks silently. Someone bumps its date, or drops the
    entry above it, and a duplicate announcement starts popping with every test still green.
    """
    entry = whats_new.by_id('2026-09-platpursuit-1-0')
    assert entry is not None, 'the 1.0 record is gone from the archive'
    assert whats_new.latest() is not entry, (
        'the 1.0 entry is now the newest, so it will fire as a modal AND as the launch greeting'
    )


def test_the_archive_carries_the_1_0_record(client):
    body = client.get(reverse('whats_new'), **CF).content.decode()
    assert 'PlatPursuit 1.0' in body
    assert 'September 1, 2026' in body, 'the 1.0 entry lost its launch date'
