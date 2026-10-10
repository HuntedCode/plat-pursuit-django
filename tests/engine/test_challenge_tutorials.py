"""The Challenges tutorials: the system intro and the per-type tutorials.

WHAT HAS TO BE TRUE, in the order it would hurt to get wrong:

  ONE AUTO-OPEN, ONCE, FOR THE RIGHT PERSON. The intro opens itself on My Challenges only; a type tutorial
  opens itself for the run's OWNER only; and a Calendar run's opening ceremony goes first, so a page never
  arms two modals at once.

  THE INTRO IS A VERSION MARKER, NOT A BOOLEAN. A hunter who dismissed the beta intro is shown the live
  one once the beta ends, and the endpoint refuses anything that could suppress a version early or rewind
  a later one.

  LOOKING NEVER WRITES. Recall links and team preview doors render without `data-auto`, which is what
  `DetailModal` reads to decide whether a dismissal records.

  THE COPY QUOTES THE RULES' OWN CONSTANTS, so a changed threshold or payout cannot leave the tutorial
  teaching the old one.
"""
import re
from pathlib import Path

import pytest
from django.contrib.auth.models import AnonymousUser
from django.db import connection
from django.test import Client, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from challenges.models import (CHALLENGE_TYPE_AZ, CHALLENGE_TYPE_CALENDAR,
                               CHALLENGE_TYPE_CHOICES, CHALLENGE_TYPE_JOBS, CHALLENGE_TYPE_PITCHES,
                               Challenge)
from challenges.services import challenge_service as svc
from challenges.services import tutorials
from tests.factories import ProfileFactory, UserFactory

pytestmark = pytest.mark.django_db

ROOT = Path(__file__).resolve().parents[2]
SETTINGS_URL = '/api/v1/user/quick-settings/'

beta_over = override_settings(CHALLENGES_BETA_MEMBERS_ONLY=False)
beta_on = override_settings(CHALLENGES_BETA_MEMBERS_ONLY=True)


def _hunter(*, staff=False, flags=None):
    user = UserFactory(is_staff=staff)
    if flags is not None:
        user.ui_flags = flags
        user.save(update_fields=['ui_flags'])
    return ProfileFactory(user=user, is_linked=True, user_is_premium=True)


def _client(profile=None):
    c = Client()
    if profile is not None:
        c.force_login(profile.user)
    return c


def _tag(body, modal_id):
    """The modal's root element, whose attributes are the whole gate."""
    m = re.search(r'<div class="pp-detail-modal pp-howto" id="%s"[^>]*>' % modal_id, body, re.S)
    assert m, '%s did not render' % modal_id
    return m.group(0)


def _modal(body, modal_id):
    """The modal's whole markup, so copy assertions cannot match the same words elsewhere on the page."""
    start = body.index('id="%s"' % modal_id)
    end = body.index('pp-howto__actions', start)
    return body[start:end]


def _armed(tag):
    return re.search(r'\sdata-auto(\s|>|=)', tag) is not None


def _run_page(run):
    return reverse('challenge_detail', args=[run.id])


def _seen_ceremony(run):
    run.opening_seen_at = run.created_at
    run.save(update_fields=['opening_seen_at'])


# ── the version marker ───────────────────────────────────────────────────────────────────────────

@beta_on
def test_the_intro_is_due_until_the_current_version_is_seen():
    assert tutorials.intro_is_due(_hunter().user)
    assert not tutorials.intro_is_due(_hunter(flags={tutorials.INTRO_FLAG: 'beta'}).user)
    # A LATER VERSION SUPERSEDES: a hunter who has seen the live intro is never shown the beta one.
    assert not tutorials.intro_is_due(_hunter(flags={tutorials.INTRO_FLAG: 'live'}).user)
    # A junk value is not a version, so it does not suppress anything.
    assert tutorials.intro_is_due(_hunter(flags={tutorials.INTRO_FLAG: 'whenever'}).user)


@beta_over
def test_a_beta_dismissal_does_not_cover_the_live_intro():
    """The whole reason for a marker: the copy changed when creation opened, so the beta hunter is owed it."""
    assert tutorials.current_intro_version() == tutorials.INTRO_LIVE
    assert tutorials.intro_is_due(_hunter(flags={tutorials.INTRO_FLAG: 'beta'}).user)
    assert not tutorials.intro_is_due(_hunter(flags={tutorials.INTRO_FLAG: 'live'}).user)


def test_nothing_is_due_for_an_anonymous_visitor():
    """No account means nowhere to record a dismissal, so an auto-open would come back on every visit."""
    assert not tutorials.intro_is_due(AnonymousUser())
    assert not tutorials.type_tutorial_is_due(AnonymousUser(), CHALLENGE_TYPE_AZ)


def test_a_type_tutorial_is_due_until_its_own_flag_is_set():
    user = _hunter(flags={tutorials.TYPE_FLAGS[CHALLENGE_TYPE_AZ]: True}).user
    assert not tutorials.type_tutorial_is_due(user, CHALLENGE_TYPE_AZ)
    assert tutorials.type_tutorial_is_due(user, CHALLENGE_TYPE_JOBS), 'one type spent another type'
    assert not tutorials.type_tutorial_is_due(user, 'not-a-type')


def test_every_type_has_a_tutorial_flag():
    """A type with no flag would render a tutorial that can never be recorded, so it would reopen on every visit."""
    assert set(tutorials.TYPE_FLAGS) == {key for key, _ in CHALLENGE_TYPE_CHOICES}


def test_the_due_checks_cost_no_queries():
    """They read `ui_flags` off the user row the request already loaded, like every one-shot on the site."""
    user = _hunter().user
    with CaptureQueriesContext(connection) as ctx:
        tutorials.intro_is_due(user)
        tutorials.type_tutorial_is_due(user, CHALLENGE_TYPE_CALENDAR)
    assert len(ctx.captured_queries) == 0


# ── the endpoint ─────────────────────────────────────────────────────────────────────────────────

def _post(profile, setting, value):
    return _client(profile).post(SETTINGS_URL, {'setting': setting, 'value': value},
                                 content_type='application/json')


@beta_on
def test_the_endpoint_records_the_version_shown():
    hunter = _hunter()
    resp = _post(hunter, tutorials.INTRO_FLAG, 'beta')
    assert resp.status_code == 200
    hunter.user.refresh_from_db()
    assert hunter.user.ui_flags[tutorials.INTRO_FLAG] == 'beta'


@beta_on
def test_the_endpoint_refuses_a_version_that_is_not_out_yet():
    """Accepting `live` during the beta would let one POST suppress the live intro before it exists."""
    hunter = _hunter()
    assert _post(hunter, tutorials.INTRO_FLAG, 'live').status_code == 400
    assert _post(hunter, tutorials.INTRO_FLAG, 'someday').status_code == 400
    assert _post(hunter, tutorials.INTRO_FLAG, ['beta']).status_code == 400
    hunter.user.refresh_from_db()
    assert tutorials.INTRO_FLAG not in (hunter.user.ui_flags or {})


@beta_over
def test_the_endpoint_never_rewinds_the_marker():
    """A stale tab that rendered the beta intro, dismissed after the live one was read, keeps `live`."""
    hunter = _hunter(flags={tutorials.INTRO_FLAG: 'live'})
    resp = _post(hunter, tutorials.INTRO_FLAG, 'beta')
    assert resp.status_code == 200
    assert resp.json()['value'] == 'live', 'the response claimed a value other than the one stored'
    hunter.user.refresh_from_db()
    assert hunter.user.ui_flags[tutorials.INTRO_FLAG] == 'live'


@beta_over
def test_a_stale_beta_tab_records_beta_and_leaves_the_live_intro_due():
    """The marker stores what was SHOWN. A beta tab dismissed after the beta ended, by a hunter with no
    marker yet, records `beta` -- storing the current version instead would suppress a live intro nobody saw."""
    hunter = _hunter()
    resp = _post(hunter, tutorials.INTRO_FLAG, 'beta')
    assert resp.status_code == 200 and resp.json()['value'] == 'beta'
    hunter.user.refresh_from_db()
    assert hunter.user.ui_flags[tutorials.INTRO_FLAG] == 'beta'
    assert tutorials.intro_is_due(hunter.user)


def test_the_endpoint_accepts_every_type_flag():
    """The allow-list is built from `TYPE_FLAGS`, so a tutorial can never post a flag the endpoint refuses."""
    hunter = _hunter()
    for flag in tutorials.TYPE_FLAGS.values():
        assert _post(hunter, 'ui_flag', flag).status_code == 200, flag
    hunter.user.refresh_from_db()
    assert all(hunter.user.ui_flags[flag] is True for flag in tutorials.TYPE_FLAGS.values())


# ── the intro on the page ────────────────────────────────────────────────────────────────────────

@beta_on
def test_my_challenges_arms_the_intro_on_a_first_visit_only():
    hunter = _hunter()
    tag = _tag(_client(hunter).get(reverse('my_challenges')).content.decode(), 'challenges-intro')
    assert _armed(tag) and 'data-value="beta"' in tag

    hunter.user.ui_flags = {tutorials.INTRO_FLAG: 'beta'}
    hunter.user.save(update_fields=['ui_flags'])
    tag = _tag(_client(hunter).get(reverse('my_challenges')).content.decode(), 'challenges-intro')
    assert not _armed(tag) and 'data-preview' not in tag


@beta_over
def test_the_live_intro_arms_once_for_a_hunter_who_saw_the_beta():
    hunter = _hunter(flags={tutorials.INTRO_FLAG: 'beta'})
    body = _client(hunter).get(reverse('my_challenges')).content.decode()
    tag = _tag(body, 'challenges-intro')
    assert _armed(tag) and 'data-value="live"' in tag
    assert 'Members are starting runs first' not in _modal(body, 'challenges-intro'), (
        'the live intro still carries the beta line')


@beta_on
def test_the_beta_intro_says_so():
    body = _client(_hunter()).get(reverse('my_challenges')).content.decode()
    assert 'Members are starting runs first' in _modal(body, 'challenges-intro')


@beta_on
def test_the_hub_offers_the_intro_but_never_opens_it():
    """The hub is public and browsed by people who may never start a run: the recall link, nothing more."""
    for client in (_client(_hunter()), Client()):
        body = client.get(reverse('challenges')).content.decode()
        tag = _tag(body, 'challenges-intro')
        assert not _armed(tag)
        assert 'data-ctut-open="challenges-intro"' in body, 'the hub has no way to open the intro'


@beta_on
def test_the_intro_lists_every_type_with_its_pitch():
    body = _modal(_client(_hunter()).get(reverse('my_challenges')).content.decode(), 'challenges-intro')
    for key, _ in CHALLENGE_TYPE_CHOICES:
        assert CHALLENGE_TYPE_PITCHES[key] in body, key
    # The count is the list's, not a word in the copy.
    assert body.count('class="cxp__beat"') >= len(CHALLENGE_TYPE_CHOICES)
    words = {1: 'One', 2: 'Two', 3: 'Three', 4: 'Four', 5: 'Five'}
    assert '%s to choose from' % words[len(CHALLENGE_TYPE_CHOICES)] in body


@beta_on
def test_the_team_can_preview_the_live_intro_during_the_beta():
    staff = _hunter(staff=True, flags={tutorials.INTRO_FLAG: 'beta'})
    body = _client(staff).get(reverse('my_challenges') + '?preview=challenges-intro-live').content.decode()
    tag = _tag(body, 'challenges-intro')
    assert 'data-preview' in tag and not _armed(tag)
    assert 'data-value="live"' in tag
    assert 'Members are starting runs first' not in _modal(body, 'challenges-intro')


@beta_on
def test_a_real_unseen_intro_outranks_the_preview():
    """Rendering the live copy for an ARMED intro during the beta would post `live`, which the endpoint
    refuses -- spending the real intro on that device while the server never hears of it."""
    staff = _hunter(staff=True)
    tag = _tag(_client(staff).get(reverse('my_challenges') + '?preview=challenges-intro-live').content.decode(),
               'challenges-intro')
    assert _armed(tag) and 'data-value="beta"' in tag and 'data-preview' not in tag


@beta_on
def test_the_intro_preview_is_team_only_and_writes_nothing():
    hunter = _hunter(flags={tutorials.INTRO_FLAG: 'beta'})
    tag = _tag(_client(hunter).get(reverse('my_challenges') + '?preview=challenges-intro').content.decode(),
               'challenges-intro')
    assert 'data-preview' not in tag and not _armed(tag)

    staff = _hunter(staff=True, flags={tutorials.INTRO_FLAG: 'beta'})
    _client(staff).get(reverse('my_challenges') + '?preview=challenges-intro')
    staff.user.refresh_from_db()
    assert staff.user.ui_flags == {tutorials.INTRO_FLAG: 'beta'}


# ── the type tutorial on a run page ──────────────────────────────────────────────────────────────

def test_the_owner_meets_the_tutorial_on_their_first_run_of_a_type():
    owner = _hunter()
    run = svc.start(owner, CHALLENGE_TYPE_AZ)
    tag = _tag(_client(owner).get(_run_page(run)).content.decode(), 'challenge-tutorial')
    assert _armed(tag) and 'data-value="%s"' % tutorials.TYPE_FLAGS[CHALLENGE_TYPE_AZ] in tag

    owner.user.ui_flags = {tutorials.TYPE_FLAGS[CHALLENGE_TYPE_AZ]: True}
    owner.user.save(update_fields=['ui_flags'])
    tag = _tag(_client(owner).get(_run_page(run)).content.decode(), 'challenge-tutorial')
    assert not _armed(tag)


def test_a_visitor_can_open_the_tutorial_but_is_never_interrupted_by_it():
    run = svc.start(_hunter(), CHALLENGE_TYPE_JOBS)
    for client in (_client(_hunter()), Client()):
        body = client.get(_run_page(run)).content.decode()
        assert not _armed(_tag(body, 'challenge-tutorial'))
        assert 'data-ctut-open="challenge-tutorial"' in body


def test_the_calendar_ceremony_goes_first():
    """One auto-opening modal per load: the ceremony on the first visit, the tutorial on the next."""
    owner = _hunter()
    run = svc.start(owner, CHALLENGE_TYPE_CALENDAR)
    body = _client(owner).get(_run_page(run)).content.decode()
    assert 'data-auto' in re.search(r'<div class="pp-detail-modal" id="cal-opening"[^>]*>', body).group(0)
    assert not _armed(_tag(body, 'challenge-tutorial')), 'two modals armed on one load'

    _seen_ceremony(run)
    assert _armed(_tag(_client(owner).get(_run_page(run)).content.decode(), 'challenge-tutorial'))


def test_a_previewed_ceremony_also_holds_the_tutorial_back():
    staff = _hunter(staff=True)
    run = svc.start(staff, CHALLENGE_TYPE_CALENDAR)
    _seen_ceremony(run)
    body = _client(staff).get(_run_page(run) + '?preview=calendar-opening').content.decode()
    tag = _tag(body, 'challenge-tutorial')
    assert not _armed(tag) and 'data-preview' not in tag


def test_the_team_can_preview_a_tutorial_on_anybodys_run_without_writing():
    run = svc.start(_hunter(), CHALLENGE_TYPE_AZ)
    staff = _hunter(staff=True)
    tag = _tag(_client(staff).get(_run_page(run) + '?preview=challenge-tutorial').content.decode(),
               'challenge-tutorial')
    assert 'data-preview' in tag and not _armed(tag)
    staff.user.refresh_from_db()
    assert not staff.user.ui_flags

    hunter = _client(_hunter()).get(_run_page(run) + '?preview=challenge-tutorial').content.decode()
    assert 'data-preview' not in _tag(hunter, 'challenge-tutorial'), 'the door opened for a non-team reader'


def test_the_tutorials_quote_the_rules_they_teach(monkeypatch):
    """Each figure comes from the constant that enforces it, so the copy cannot drift from the run.

    THE CONSTANTS ARE SWAPPED FOR ODD VALUES first, because asserting the real ones cannot tell a template
    that reads the constant from one that happens to hard-code the same number."""
    monkeypatch.setattr('challenges.views.HATCH_THRESHOLD', 7)
    monkeypatch.setattr('challenges.views.CHALLENGE_SLOT_JOB_XP', 4321)
    monkeypatch.setattr('challenges.views.CALENDAR_DAY_MARKERS', (11, 22, 33))
    owner = _hunter()

    jobs_run = svc.start(owner, CHALLENGE_TYPE_JOBS)
    jobs = _modal(_client(owner).get(_run_page(jobs_run)).content.decode(), 'challenge-tutorial')
    assert '4,321 XP' in jobs and '7 or fewer' in jobs
    assert '%d squares' % jobs_run.total_slots in jobs

    az_run = svc.start(owner, CHALLENGE_TYPE_AZ)
    az_run.total_slots = 24
    az_run.save(update_fields=['total_slots'])
    az = _modal(_client(owner).get(_run_page(az_run)).content.decode(), 'challenge-tutorial')
    assert '7 or fewer' in az and '24 squares' in az, 'the square count is hard-coded'
    assert 'XP' not in az, 'A-Z pays no job XP, so its tutorial must not mention any'

    cal = _modal(_client(owner).get(_run_page(svc.start(owner, CHALLENGE_TYPE_CALENDAR))).content.decode(),
                 'challenge-tutorial')
    assert '11, 22 and 33 days' in cal


def test_each_tutorial_is_titled_with_its_own_type():
    owner = _hunter()
    for key, label in CHALLENGE_TYPE_CHOICES:
        body = _client(owner).get(_run_page(svc.start(owner, key))).content.decode()
        assert 'How the %s works' % label in _modal(body, 'challenge-tutorial'), key


def test_a_locked_run_never_opens_the_tutorial_by_itself():
    """A finished or hidden run cannot be edited, so "Pick a game" would teach an action the page cannot take.
    The recall link still opens it."""
    owner = _hunter()
    run = svc.start(owner, CHALLENGE_TYPE_AZ)
    # Flag AND stamp together: the database pairs each flag with its timestamp.
    for flag, stamp in (('is_complete', 'completed_at'), ('is_deleted', 'deleted_at')):
        Challenge.objects.filter(pk=run.pk).update(**{flag: True, stamp: run.created_at})
        body = _client(owner).get(_run_page(run)).content.decode()
        assert not _armed(_tag(body, 'challenge-tutorial')), flag
        assert 'data-ctut-open="challenge-tutorial"' in body
        Challenge.objects.filter(pk=run.pk).update(**{flag: False, stamp: None})
    # The control: the same run, editable again, does arm -- so the loop above is not vacuous.
    assert _armed(_tag(_client(owner).get(_run_page(run)).content.decode(), 'challenge-tutorial'))


# ── the shared pieces ────────────────────────────────────────────────────────────────────────────

def _css_without_comments(name):
    css = (ROOT / 'static' / 'css' / 'components' / name).read_text(encoding='utf-8')
    return re.sub(r'/\*.*?\*/', '', css, flags=re.S)


def test_both_tutorials_close_with_the_shared_fade():
    """`badge-inspect.css` defines `.pp-detail-modal.is-closing` UNSCOPED, and a howto clone without its
    own ID in these lists dissolves its chrome around fully opaque text."""
    css = _css_without_comments('series-list.css')
    for modal_id in ('challenges-intro', 'challenge-tutorial'):
        assert '#%s.is-closing,' % modal_id in css or '#%s.is-closing {' % modal_id in css, modal_id
        assert '#%s.is-closing .pp-detail-modal__dialog' % modal_id in css, modal_id


def test_the_dialog_settle_list_targets_dialogs_only():
    """The motion list animates DIALOGS. An entry naming a modal ROOT overrides that root's fade with the
    settle, so the whole overlay slides and then vanishes -- which `#cal-opening` did until this pin."""
    css = _css_without_comments('series-list.css')
    block = css[css.index('@media (prefers-reduced-motion: no-preference)', css.index('ppHowtoFadeOut')):]
    block = block[:block.index('{', block.index('{') + 1)]
    selectors = [s.strip() for s in block.split('{', 1)[1].split(',')]
    assert selectors and all(s.endswith('.pp-detail-modal__dialog') for s in selectors), selectors


def test_the_tutorial_script_records_only_an_armed_dismissal():
    """No JS runner here, so the contract is pinned by source: the preview branch carries no recorder and
    no `seenKey`, and `autoOpenDelay` is set inside the two branches that may open the modal, never above them."""
    js = (ROOT / 'static' / 'js' / 'challenge-tutorials.js').read_text(encoding='utf-8')
    body = js[js.index('function wire(el)'):js.index('var api = PP.DetailModal')]
    auto, _, preview = body.partition("} else if (el.hasAttribute('data-preview')) {")
    assert "el.hasAttribute('data-auto')" in auto and 'onDismiss' in auto and 'seenKey' in auto
    assert 'onDismiss' not in preview and 'seenKey' not in preview and 'autoOpenDelay' in preview
    head = auto[:auto.index("el.hasAttribute('data-auto')")]
    assert 'autoOpenDelay' not in head, 'the delay is set outside the armed branch, so every visit reopens it'


def test_every_page_with_a_tutorial_loads_the_script():
    for name in ('my_challenges.html', 'browse.html', 'challenge_detail.html'):
        src = (ROOT / 'templates' / 'challenges' / name).read_text(encoding='utf-8')
        assert "js/challenge-tutorials.js" in src, name
        assert '_challenges_intro.html' in src or '_type_tutorial.html' in src, name


def test_the_markup_carries_the_hooks_the_script_reads():
    """The script finds modals by `[data-ctut]`, closes them by `[data-ctut-close]` and opens them from
    `[data-ctut-open="<id>"]`. Renaming any one on one side breaks the modal with nothing else failing."""
    js = (ROOT / 'static' / 'js' / 'challenge-tutorials.js').read_text(encoding='utf-8')
    assert "querySelectorAll('[data-ctut]')" in js
    assert "closeSelector: '[data-ctut-close]'" in js
    assert "'[data-ctut-open=\"' + el.id + '\"]'" in js
    assert 'if (!first' in js, 'wiring is no longer first-load only, so a history restore stacks listeners'
    for name in ('_challenges_intro.html', '_type_tutorial.html'):
        src = (ROOT / 'templates' / 'challenges' / 'partials' / name).read_text(encoding='utf-8')
        assert ' data-ctut\n' in src or ' data-ctut ' in src, name
        assert src.count('data-ctut-close') >= 3, name   # scrim, close button, Got it


def test_the_tutorials_render_outside_the_receding_page():
    """A `position: fixed` overlay inside `#page-recede` resolves against that transformed ancestor rather
    than the viewport, so each include must sit in the `fixed_overlays` block -- and not inside a comment."""
    for name, partial in (('my_challenges.html', '_challenges_intro.html'), ('browse.html', '_challenges_intro.html'),
                          ('challenge_detail.html', '_type_tutorial.html')):
        src = (ROOT / 'templates' / 'challenges' / name).read_text(encoding='utf-8')
        src = re.sub(r'{% comment %}.*?{% endcomment %}', '', src, flags=re.S)
        block = src[src.index('{% block fixed_overlays %}'):]
        block = block[:block.index('{% endblock %}')]
        assert "{% include 'challenges/partials/" + partial + "' %}" in block, name
