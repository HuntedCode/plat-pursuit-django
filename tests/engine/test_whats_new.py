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
from django.template.defaultfilters import escapejs
from django.urls import reverse
from django.utils import timezone

from core import whats_new
from tests.factories import ProfileFactory

pytestmark = pytest.mark.django_db

CF = {'HTTP_CF_RAY': '8f0000000000abcd-LHR'}
QUICK = 'api:user-quick-settings'


def _synced_client(client, **user_kwargs):
    """A hunter on the lobby. `date_joined` is set only so tests that care can override it --
    being due an entry does NOT depend on it (test_a_BRAND_NEW_account_is_shown_the_newest_entry)."""
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
    text = re.sub(r'{%\s*comment\s*%}.*?{%\s*endcomment\s*%}', '', text, flags=re.S)
    text = re.sub(r'{#.*?#}', '', text, flags=re.S)
    text = re.sub(r'<!--.*?-->', '', text, flags=re.S)
    # TRAILING line comments too, not just line-leading ones -- utils.js has 50+ of them, and a pin that
    # reads that file was otherwise satisfiable by `var x = y; // mentions the token`. The negative
    # lookbehind spares `https://`, which is the only other way `//` appears in these files.
    text = re.sub(r'(?<!:)//.*$', '', text, flags=re.M)
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
        # Through the same property the templates render, so this cannot drift from what ships.
        assert e.safe_link_url == e.link_url, f'{e.id} has a link the renderer will drop: {e.link_url}'
        assert e.link_label.strip(), f'{e.id} has a link with no label'


def test_entry_ids_are_slugs():
    """The id is interpolated into a JS string literal and into a localStorage key. `|escapejs` makes any
    id safe, but nothing constrained the charset -- and an id containing an apostrophe would render as
    `&#x27;` (HTML entities are not decoded inside a script element), so the POST would carry six literal
    characters instead, 400, and silently fall back to localStorage forever."""
    for e in whats_new.ENTRIES:
        assert re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)*', e.id), (
            f'{e.id!r} is not a lowercase slug; ids are interpolated into JS and a storage key'
        )


def test_a_link_that_only_LOOKS_relative_is_dropped():
    r"""`/\evil.com/login` starts with a single slash and is not protocol-relative, so the original
    assertions passed it -- and browsers resolve it to `https://evil.com/login`, because the WHATWG
    parser treats a backslash exactly as a slash for http(s). The modal's dismissing-link handler then
    calls location.assign on that. The check now runs at render time, not only in this file."""
    from core.whats_new import Entry

    def probe(url):
        return Entry(id='x', published=date(2026, 1, 1), title='t', beats=(('a', 'b'),),
                     link_label='go', link_url=url).safe_link_url

    for hostile in (r'/\evil.com/login', '//evil.com', r'/\/evil.com', '/%2fevil.com', '/%5Cevil.com',
                    'https://evil.com', 'javascript:alert(1)'):
        assert probe(hostile) == '', f'{hostile!r} survived as a link'
    assert probe('/leaderboards/?tab=rarity') == '/leaderboards/?tab=rarity'


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
    """The isinstance check, tested for its own sake.

    `value=True` proved nothing here: `by_id(True)` compares True to each id, finds nothing and returns
    None, so the 400 came from the SECOND clause and deleting the type check left this green. An
    unhashable value is the case that separates them -- it is what would raise rather than return None
    if `by_id` were ever rewritten as a dict lookup.
    """
    client, profile = _synced_client(client)

    for bad in (True, 42, ['a'], {'a': 1}, None):
        resp = client.post(reverse(QUICK),
                           data={'setting': 'whats_new_seen', 'value': bad},
                           content_type='application/json')
        assert resp.status_code == 400, f'{bad!r} was accepted'

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
    # Bounded at the modal's own closing script tag. Splitting only on the id ran to the end of the
    # DOCUMENT -- the whole lobby, footer and every script -- so any future <time> elsewhere on Home
    # would have satisfied this. The entry publishes today, which makes that likelier, not less.
    modal = body.split('id="whats-new"', 1)[1].split('</script>', 1)[0]

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
    # Deferred, not cancelled: dismissing the greeting must leave the entry still due. Asserted by
    # ACTUALLY dismissing it and re-requesting -- the previous version checked that a GET had not
    # written ui_flags, which no GET ever does, on any branch, forever.
    client.post(reverse(QUICK), data={'setting': 'ui_flag', 'value': 'launch_welcome'},
                content_type='application/json')
    assert 'id="whats-new"' in client.get('/', **CF).content.decode(), (
        "the greeting consumed the deferred entry instead of leaving it due"
    )


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
    # _code(), not read_text: a 15-line prose block sits directly above this rule explaining the trap,
    # so a raw read would be satisfied by the warning rather than by the rule it warns about.
    css = _code(Path(dj_settings.BASE_DIR) / 'static' / 'css' / 'components' / 'series-list.css')
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
    # BOUNDED at the export line. Splitting only on the opening ran to end-of-file, which swept in the
    # global `/` + Cmd-K search shortcut near the bottom -- and that contains `e.metaKey`, so half this
    # assertion was true no matter what DetailModal did. Caught by mutation.
    handler = js.split('function DetailModal', 1)[1].split('window.PlatPursuit.DetailModal', 1)[0]

    assert "closest('a[href]')" in handler, 'the controller no longer treats a dismissing link specially'
    assert 'location.assign' in handler, 'a dismissing link is prevented but never navigated'
    for key in ('e.metaKey', 'e.ctrlKey', 'e.shiftKey', 'e.altKey', 'e.button !== 0'):
        assert key in handler, f'{key} dropped: a modified click belongs to the browser, not to us'
    assert 'el.contains(hit)' in handler, (
        'the document-level handler is not scoped to its own modal, so any matching control on the '
        'page dismisses it and, inside a link, navigates on its behalf'
    )


def test_every_way_out_of_the_modal_dismisses_it():
    """All three controls mark it seen. A reader who clicked through to the feature has been told about
    it, and finding the notice waiting again next visit reads as the dismissal having failed."""
    partial = _code(Path(dj_settings.BASE_DIR) / 'templates' / 'trophies' / 'partials' / 'home' / '_whats_new.html')

    # The button, the entry link, the archive link, the scrim and the X.
    # EXACT, not a floor. `>= 5` was reached with only four controls, because `closeSelector:
    # '[data-wn-close]'` in the inline script is itself an occurrence -- the same shape as the
    # `count('is-active') >= 2` guard that shipped green with nothing lit. Five controls + the selector.
    assert partial.count('data-wn-close') == 6, (
        'a control can close this modal without marking it seen (or one was added without a guard)'
    )
    for control in ('pp-detail-modal__scrim', 'pp-detail-modal__close', 'pp-howto__got'):
        chunk = partial.split(control, 1)[1].split('>', 1)[0]
        assert 'data-wn-close' in chunk, f'{control} closes the modal without marking it seen'
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


# ── reachable without the modal ───────────────────────────────────────────────────────────────────────

def test_the_avatar_menu_carries_the_only_way_back(client):
    """The modal is render == armed: a dismissed notice leaves NO markup on the page at all. So without
    a door in the chrome, a hunter who closed it by reflex has no route back to what it said, and the
    archive is a page nothing links to."""
    client, profile = _synced_client(client)
    profile.user.ui_flags = {'whats_new_seen': whats_new.latest().id}
    profile.user.save(update_fields=['ui_flags'])

    body = client.get('/', **CF).content.decode()

    assert 'id="whats-new"' not in body, 'the fixture is wrong: the modal is still on the page'
    menu = body.split('pp-avmenu', 1)[1].split('</div>', 1)[0]
    assert reverse('whats_new') in menu, 'no way back to the archive once the modal is dismissed'


def _footer(body):
    """The footer element only. `split('<footer')[1]` alone runs to </html>, sweeping in the mobile
    tabbar, the toast container and every script -- so the link could be deleted from the footer, added
    to the tabbar, and the assertion would still pass."""
    return body.split('<footer', 1)[1].split('</footer>', 1)[0]


def test_the_footer_carries_it_for_signed_OUT_readers(client):
    """The archive is public, so the footer is the route for somebody who has no avatar menu. Signed
    out is the only case worth a test: the entry is not inside any auth conditional."""
    body = client.get(reverse('about'), **CF).content.decode()

    assert reverse('whats_new') in _footer(body)


# ── the archive's spine ───────────────────────────────────────────────────────────────────────────────

def test_the_archive_draws_the_entries_as_a_timeline(client):
    body = client.get(reverse('whats_new'), **CF).content.decode()

    assert 'wn-tl__node' in body, 'the spine has no nodes'
    # One row per entry, and each carries its index so the spine can stagger.
    assert body.count('wn-tl__row') == len(whats_new.ENTRIES)
    assert '--i: 0;' in body


def test_the_card_keeps_its_OWN_date_beside_the_spine(client):
    """Jeffrey's explicit call: the spine is additive, and the date stays where it was. So the nodes
    carry no text -- printing the date on the spine an inch from the card's own would be printing it
    twice."""
    body = client.get(reverse('whats_new'), **CF).content.decode()

    for entry in whats_new.ENTRIES:
        assert entry.published.strftime('%Y-%m-%d') in body, f'{entry.id} lost its card date'
    assert body.count('wn-entry__date') == len(whats_new.ENTRIES)


def test_the_arrival_choreography_cannot_strand_the_page(client):
    """The three-path degradation the .pp-arrive primitive requires, and the half that is easy to skip.

    The hidden state is armed pre-paint from `extra_head`. If the JS bundle then fails to load, nothing
    is left to reveal it and the page is a header above an empty column -- so the boot script removes the
    arm when the helper is missing. Without that failsafe the page's failure mode is BLANK, not unstyled.
    """
    body = client.get(reverse('whats_new'), **CF).content.decode()

    assert "classList.add('pp-arm')" in body, 'the choreography is never armed'
    assert "classList.remove('pp-arm')" in body, (
        'no failsafe: a bundle that fails to load leaves every entry permanently hidden'
    )
    assert 'arriveOnScroll' in body


def test_the_spine_animations_are_declared_where_they_are_used():
    """A keyframe referenced but never declared is silently inert -- no console error, no visual clue
    beyond "the entrance stopped happening". That has happened here before, which is why
    test_css_animations.py exists; this pins the two the spine adds."""
    css = _code(Path(dj_settings.BASE_DIR) / 'static' / 'css' / 'components' / 'home.css')
    for name in ('wnTlLine', 'wnTlNode'):
        # The trailing delimiter is load-bearing, not tidiness. A bare `in` check passes on any name this
        # one is a PREFIX of: renaming the declaration to `wnTlNodeXX` left the plain version green,
        # caught by mutation. Matching the space and brace that must follow the name pins the whole name.
        assert f'@keyframes {name} {{' in css, f'{name} is used but never declared'
        assert f'animation: {name} ' in css, f'{name} is declared but nothing uses it'


def test_the_gate_ARMS_when_a_modal_is_due(client):
    """The half that was missing, and the most valuable guard on this feature.

    Every other gate test checked the UNARMED case. Dropping `or show_whats_new` from the gate partial
    left the whole suite green while, in production, every What's New visit rendered an unarmed gate --
    home-motion fails open, and the lobby's count-ups, Horizon fills and ring pace all play out behind
    the scrim and are finished before the reader dismisses anything. That is the single failure this
    three-file mechanism exists to prevent.
    """
    client, _ = _synced_client(client)

    body = client.get('/', **CF).content.decode()

    assert 'id="whats-new"' in body, 'the fixture is wrong: no modal is due'
    gate = body.split('ppAfterHomeModal', 1)[0]
    assert 'var pending = true' in gate, 'a modal is on the page but the gate is not holding the motion'


def test_the_backstop_is_cancelled_once_a_modal_appears():
    """The deadline covers "nothing ever appeared", not "the reader is slow".

    The first cut fired unconditionally at 4s. A modal is a title, a date, three beats and three
    controls; after page load and the 450ms auto-open there were about 3.3 seconds left, so on a normal
    visit the motion was released while the modal was still up -- turning the exact failure this gate
    prevents from never happening into happening every time.
    """
    base = Path(dj_settings.BASE_DIR)
    gate = _code(base / 'templates' / 'trophies' / 'partials' / 'home' / '_home_modal_gate.html')
    # Delimited on both sides: a bare substring passes on any name this is a prefix of, which is how
    # `wnTlNodeXX` slipped past the keyframe pin. The publisher and the callers are matched exactly.
    assert 'window.ppHoldHomeModal = function' in gate, 'nothing publishes the backstop hold'
    assert 'clearTimeout' in gate, (
        'the backstop fires even when a modal is open, releasing the motion behind the scrim'
    )
    for partial in ('_whats_new.html', '_launch_welcome.html'):
        src = _code(base / 'templates' / 'trophies' / 'partials' / 'home' / partial)
        assert 'onOpened:' in src, f'{partial} never tells the page it opened'
        assert 'ppHoldHomeModal()' in src, f'{partial} never holds the backstop'


def test_the_spine_nodes_carry_no_text(client):
    """Jeffrey's call: the spine is additive and each card keeps its own date. Printing the date on the
    node an inch to the left of the card's own would be printing it twice."""
    body = client.get(reverse('whats_new'), **CF).content.decode()

    for chunk in body.split('wn-tl__node')[1:]:
        node = chunk.split('>', 1)[1].split('<', 1)[0]
        assert not node.strip(), f'a spine node carries text: {node.strip()!r}'


def test_every_spine_row_carries_its_own_index(client):
    """`--i` drives both the card's stagger and the node/connector delay. A constant would collapse the
    cascade into one beat, and only index 0 was ever asserted."""
    body = client.get(reverse('whats_new'), **CF).content.decode()

    for i in range(len(whats_new.ENTRIES)):
        assert f'--i: {i};' in body, f'row {i} does not carry its index'
    assert body.count('wn-tl__node') == len(whats_new.ENTRIES)


def test_the_modal_posts_THIS_entrys_id_and_scopes_its_device_key(client):
    """Three wirings that are invisible until they break, and each breaks the feature permanently.

    A wrong POST value 400s, so the dismissal is never recorded and the reader meets the same notice on
    every visit forever. A `seenKey` without the entry id is written once on any failed dismissal and
    then suppresses EVERY future entry on that device, with nothing in the UI able to undo it. Without
    `data-auto` the controller is never armed, so no close control records anything at all.
    """
    client, _ = _synced_client(client)
    entry = whats_new.latest()

    body = client.get('/', **CF).content.decode()
    modal = body.split('id="whats-new"', 1)[1]

    assert 'data-auto' in body.split('id="whats-new"', 1)[0][-80:] or 'data-auto' in modal[:80], (
        'the modal root has no data-auto, so dismissing it records nothing'
    )
    # Compared against the ESCAPED form: `|escapejs` renders hyphens as -, which is the same string
    # to a JS parser and a different one to `in`. Asserting the raw id here failed against correct output.
    ident = escapejs(entry.id)
    assert f"value: '{ident}'" in modal, 'the dismissal posts the wrong entry id'
    assert f"pp-whats-new-seen-{ident}" in modal, (
        'the device fallback key is not scoped to this entry, so one failed dismissal kills them all'
    )


def test_the_preview_door_cannot_put_BOTH_modals_on_the_page(client, settings):
    """The precedence invariant has to hold through the staff door too, and this is the case that broke.

    `or is_previewing` used to hang outside the guard, so a staff member who was genuinely due the 1.0
    greeting and hit ?preview=whats-new got both: two scrims, two focus traps on one document, one
    Escape closing both, and the gate settled by whichever closed first while the other still covered
    the page. The launch-welcome door is naturally immune -- forcing that flag suppresses this one -- so
    only this side could contradict what home.html states as impossible.
    """
    settings.PP_LAUNCH_DATE = timezone.now() + timedelta(days=1)   # everyone counts as existing
    client, _ = _synced_client(client, is_staff=True)

    body = client.get('/?preview=whats-new', **CF).content.decode()

    assert not ('id="launch-welcome"' in body and 'id="whats-new"' in body), (
        'both modals rendered on one visit; the gate will settle while one is still on screen'
    )
