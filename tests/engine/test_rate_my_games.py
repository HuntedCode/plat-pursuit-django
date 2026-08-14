"""Rate My Games -- the rating wizard at /rate-my-games/.

Rebuilt 2026-08. The page was pre-rebuild DaisyUI AND a second hand-rolled copy of the rating form, which
had already drifted: no quick take, no live slider readouts, no field-level errors. What these pin is the
thing that stops it drifting again -- both rating surfaces include ONE field partial and post through ONE
controller -- plus the queue behaviour the page is built around.

The look and the motion were checked in a browser, since neither is visible from here.
"""
import re
from pathlib import Path

import pytest
from django.db import connection
from django.template.loader import render_to_string
from django.test.utils import CaptureQueriesContext

from tests.engine.test_plat_cards import _completed_game
from tests.factories import ConceptTrophyGroupFactory, ProfileFactory
from trophies.models import UserConceptRating

pytestmark = pytest.mark.django_db

ROOT = Path(__file__).resolve().parents[2]
URL = '/rate-my-games/'
QUEUE = '/api/v1/ratings/wizard/queue/'

# The API contract. Renaming any of these silently breaks the POST on BOTH surfaces at once now.
FIELD_NAMES = ('difficulty', 'grindiness', 'hours_to_platinum', 'fun_ranking', 'overall_rating', 'blurb')


def _hunter():
    return ProfileFactory(is_linked=True)


def _page(client, profile):
    client.force_login(profile.user)
    resp = client.get(URL)
    assert resp.status_code == 200, f'expected the page, got {resp.status_code} -> {resp.get("Location", "")}'
    return resp


def _ratable(profile, name=None):
    """A finished game the wizard would queue, with the trophy group the rate endpoint needs."""
    game, _, _ = _completed_game(profile, with_platinum=True, name=name)
    ConceptTrophyGroupFactory(concept=game.concept, trophy_group_id='default', display_name='Base Game')
    return game


# ── Where it lives ────────────────────────────────────────────────────────────────────────────────

def test_the_old_community_path_still_lands(client):
    """It lived at /community/rate-my-games/ for its whole life, and the page is bookmarked by exactly
    the people who use it most. Permanent, because this move is not coming back."""
    resp = client.get('/community/rate-my-games/?queue_type=dlc')

    assert resp.status_code == 301
    assert resp['Location'].startswith(URL)
    assert 'queue_type=dlc' in resp['Location'], 'the deep-link into the DLC queue was dropped'


def test_it_sits_with_the_other_personal_tools():
    """It produces community data, but the act is personal: your library, login-only, noindex. That is
    the same shape as Plat Cards and Recap, which is why it sits beside them rather than in Community."""
    from django.contrib.auth.models import AnonymousUser
    from django.test import RequestFactory
    from django.urls import resolve

    from core.hub_subnav import resolve_hub_subnav

    req = RequestFactory().get(URL)
    req.resolver_match = resolve(URL)
    req.user = AnonymousUser()
    match = resolve_hub_subnav(req)

    assert match['hub'].key == 'my_pursuit', 'the page no longer resolves to the personal hub'
    assert match['active_slug'] == 'rate_my_games'
    tools = [i.slug for i in match['hub'].items if i.group == 'Tools']
    assert tools == ['shareables', 'recap', 'rate_my_games'], tools


# ── One form, two hosts ───────────────────────────────────────────────────────────────────────────

def test_both_rating_surfaces_include_the_same_field_partial():
    """The whole point of the refactor. Two copies of these fields is how the wizard ended up without a
    quick take while the modal had one -- so neither host may own its own copy."""
    for tpl in ('trophies/partials/game_detail/quick_rate_modal.html', 'trophies/rate_my_games.html'):
        src = (ROOT / 'templates' / tpl).read_text(encoding='utf-8')
        assert "'partials/_rating_fields.html'" in src, f'{tpl} does not include the shared fields'


def test_the_wizard_renders_every_field_the_api_expects(client):
    profile = _hunter()
    content = _page(client, profile).content.decode()

    for name in FIELD_NAMES:
        assert f'name="{name}"' in content, f'{name} is missing from the wizard form'
    assert 'data-gd-qr-count' in content, 'the quick take has no character counter'
    assert 'data-gd-qr-slider' in content, 'the sliders have no live-readout hook'


def test_the_wizard_gained_the_quick_take_and_its_guidelines_route(client):
    """The visible product change: the wizard can now leave a public quick take, which means it also owes
    the reader the guidelines notice and a way to read them without losing the take."""
    profile = _hunter()
    content = _page(client, profile).content.decode()

    assert 'data-gd-qr-blurb' in content
    assert 'gd-qr__notice' in content
    assert 'data-gd-guidelines-open' in content
    assert 'id="gd-guidelines-modal"' in content, 'the notice links to a sheet that is not on the page'


def test_the_wizard_does_not_post_the_rating_itself():
    """It drives the shared RatingFields. A second POST site is how the two forms drifted apart."""
    src = (ROOT / 'static' / 'js' / 'rate-my-games.js').read_text(encoding='utf-8')

    assert '/rate/' not in src, 'the wizard posts a rating directly again'
    assert 'RatingFields.attach' in src, 'the wizard no longer uses the shared form controller'


def test_the_form_is_attached_once_not_per_game():
    """The wizard advances through a queue against ONE <form>. Re-attaching per game stacks a fresh set of
    listeners each time, and one submit posts as many times as you have rated."""
    src = (ROOT / 'static' / 'js' / 'rate-my-games.js').read_text(encoding='utf-8')

    assert src.count('RatingFields.attach') == 1
    assert 'setTarget(' in src, 'nothing re-points the form at the next game'


def test_the_shared_controller_loads_before_the_page_controller():
    """`quick-rate.js` defines RatingFields; load it after rate-my-games.js and the form is inert."""
    html = (ROOT / 'templates' / 'trophies' / 'rate_my_games.html').read_text(encoding='utf-8')

    assert html.index('js/quick-rate.js') < html.index('js/rate-my-games.js')


def test_a_rating_saves_with_no_quick_take(client):
    """The blurb is optional and must never gate the save -- this is a BULK flow, and most passes through
    it will be numbers only."""
    profile = _hunter()
    game = _ratable(profile)
    client.force_login(profile.user)

    resp = client.post(
        f'/api/v1/ratings/{game.concept_id}/group/default/rate/',
        {'difficulty': 5, 'grindiness': 4, 'hours_to_platinum': 30,
         'fun_ranking': 8, 'overall_rating': 4.0, 'blurb': ''},
        content_type='application/json',
    )

    assert resp.status_code == 200, resp.content
    rating = UserConceptRating.objects.get(profile=profile, concept=game.concept)
    assert rating.blurb == ''
    assert rating.hours_to_platinum == 30


# ── The queue ─────────────────────────────────────────────────────────────────────────────────────

def test_the_queue_serves_the_hunters_unrated_games(client):
    profile = _hunter()
    _ratable(profile, name='Bloodborne')
    client.force_login(profile.user)

    data = client.get(QUEUE).json()

    assert data['count'] == 1
    assert data['queue'][0]['unified_title']
    assert data['queue'][0]['trophy_group_id'] == 'default'


def test_a_rated_game_leaves_the_queue(client):
    """Which is why the wizard has no 'update your existing rating' branch: both queues serve only unrated
    items, so there is never a rating to prefill."""
    profile = _hunter()
    game = _ratable(profile)
    client.force_login(profile.user)
    assert client.get(QUEUE).json()['count'] == 1

    UserConceptRating.objects.create(
        profile=profile, concept=game.concept, concept_trophy_group=None,
        difficulty=5, grindiness=5, hours_to_platinum=10, fun_ranking=5, overall_rating=3,
    )

    assert client.get(QUEUE).json()['count'] == 0


def test_the_queue_query_count_does_not_grow_with_the_page(client):
    """Two sizes, same count. A hunter with a five-figure library pages this endpoint repeatedly, so a
    per-game query here is a per-game query multiplied by the whole queue."""
    def _measure(rows):
        profile = _hunter()
        for i in range(rows):
            _ratable(profile, name=f'Game {i:02d}')
        client.force_login(profile.user)
        client.get(QUEUE)                                  # warm session/auth
        with CaptureQueriesContext(connection) as ctx:
            assert client.get(QUEUE).status_code == 200
        return len(ctx)

    small, full = _measure(2), _measure(12)

    assert small == full, f'query count grew with the queue: {small} -> {full}'


def _dlc_ready(profile, name=None):
    """A finished game that ALSO has a fully-earned DLC group -- what the DLC queue serves."""
    from tests.factories import EarnedTrophyFactory, TrophyFactory, TrophyGroupFactory

    game = _ratable(profile, name=name)
    TrophyGroupFactory(game=game, trophy_group_id='001')
    ConceptTrophyGroupFactory(concept=game.concept, trophy_group_id='001', display_name='The Old Hunters')
    trophy = TrophyFactory(game=game, trophy_type='gold', trophy_group_id='001')
    EarnedTrophyFactory(profile=profile, trophy=trophy, earned=True)
    return game


@pytest.mark.parametrize('queue_type', ['base', 'dlc'])
def test_the_queue_never_drags_the_igdb_blob_along(client, queue_type):
    """`raw_response` is the ~30 KB IGDB API payload and nothing on this page reads it.

    The DLC path shipped without the defer, and it is the worse of the two: it `list()`s EVERY ratable
    concept's DLC groups BEFORE paginating, so for a hunter with a four-figure completed library that is
    tens of MB of JSON loaded to answer a 20-item page -- the exact shape of the whale OOM class."""
    profile = _hunter()
    _dlc_ready(profile)
    client.force_login(profile.user)
    client.get(QUEUE)                                      # warm session/auth

    with CaptureQueriesContext(connection) as ctx:
        assert client.get(QUEUE, {'queue_type': queue_type}).status_code == 200

    offenders = [q['sql'] for q in ctx.captured_queries if 'raw_response' in q['sql']]
    assert not offenders, f'{queue_type} queue selects raw_response:\n' + '\n'.join(o[:300] for o in offenders)


@pytest.mark.parametrize('queue_type,key', [('base', 'queue'), ('dlc', 'groups')])
def test_the_queue_sends_the_cover_and_no_longer_the_landscape_wash(client, queue_type, key):
    """The header's job is reminding you WHICH game this is, and the COVER is what does it.

    It also carried `landscape_url`, painted behind the text as a blurred wash. That was dropped -- it read
    as noise behind the one question the page asks -- and the field went with it rather than being left in
    the payload for nobody: every consumer of a queue row is the wizard's own header.
    """
    profile = _hunter()
    _dlc_ready(profile)
    client.force_login(profile.user)

    data = client.get(QUEUE, {'queue_type': queue_type}).json()

    assert data[key], f'nothing in the {queue_type} queue to check'
    assert 'concept_icon_url' in data[key][0], f'the {queue_type} queue sends no cover for the header'
    assert 'landscape_url' not in data[key][0], (
        f'the {queue_type} queue still pays for landscape art the header no longer draws'
    )


@pytest.mark.parametrize('queue_type', ['base', 'dlc'])
def test_the_queue_reports_the_library_not_just_the_backlog(client, queue_type):
    """The progress meter is denominated in the whole ratable library, so it needs both halves.

    Denominating it in the QUEUE was the bug: the queue holds only what is still unrated, so rating a game
    shrank the denominator instead of advancing the numerator -- "Game 1 of 70" became "Game 1 of 69" on
    the next visit -- and the bar could never fill, because you are always at the start of what is left."""
    profile = _hunter()
    for i in range(3):
        _dlc_ready(profile, name=f'Game {i}')
    client.force_login(profile.user)

    before = client.get(QUEUE, {'queue_type': queue_type}).json()
    assert before['ratable_total'] == 3, before
    assert before['rated_total'] == 0, before

    # Rate one of them the way the wizard does.
    item = (before['queue'][0] if queue_type == 'base'
            else dict(before['groups'][0], **before['groups'][0]['items'][0]))
    resp = client.post(
        f"/api/v1/ratings/{item['concept_id']}/group/{item['trophy_group_id']}/rate/",
        {'difficulty': 5, 'grindiness': 5, 'hours_to_platinum': 10,
         'fun_ranking': 5, 'overall_rating': 3, 'blurb': ''},
        content_type='application/json',
    )
    assert resp.status_code == 200, resp.content

    after = client.get(QUEUE, {'queue_type': queue_type}).json()
    assert after['ratable_total'] == 3, 'the denominator shrank -- it must describe the library'
    assert after['rated_total'] == 1, 'the numerator did not move'


def test_the_meter_counts_the_library_and_the_counters_tick_live():
    """Two halves of the same complaint: the bar never filled, and the waiting counts only moved on a
    refresh -- on a page whose entire purpose is emptying them."""
    js = (ROOT / 'static' / 'js' / 'rate-my-games.js').read_text(encoding='utf-8')

    prog = js[js.index('renderProgress() {'):js.index('showDone() {')]
    assert 'this.ratableTotal' in prog and 'this.ratedTotal' in prog, (
        'the meter is denominated in something other than the library'
    )
    assert 'this.total' not in prog, 'the meter still reads the unrated-queue length'

    assert 'tickWaiting()' in js, 'the header counters never move'
    # Rating ticks them; skipping must not -- nothing was rated.
    saved = js[js.index('onSaved: function () {'):js.index('onError: function (msg)')]
    assert 'self.tickWaiting();' in saved
    skip = js[js.index('if (skip) {'):js.index('if (submit) {')]
    assert 'tickWaiting' not in skip, 'skipping decrements a count of things you have rated'


# ── The rebuild itself ────────────────────────────────────────────────────────────────────────────

def test_the_page_is_off_daisyui():
    """The rebuild test. Every one of these was on the old page, and each has a house primitive now:
    .pp-switch for the queue toggle, .pp-horizon for progress, .pp-bgal__chip for the opt-in,
    .pp-gbrowse__spinner for loading."""
    html = _markup()

    for legacy in ('range-error', 'range-accent', 'progress-primary', 'checkbox-warning',
                   'loading-dots', 'input-secondary', 'badge-lg'):
        assert legacy not in html, f'{legacy} survived the rebuild'


def _markup(name='trophies/rate_my_games.html'):
    """The template with its {% comment %} blocks stripped.

    Load-bearing: these comments explain what was replaced and therefore NAME it, so a bare substring
    check against the raw file passes on the prose. `pp-toolbar-card` did exactly that after the switcher
    moved out of one -- the assertion went on passing against a sentence saying the class is not used."""
    src = (ROOT / 'templates' / name).read_text(encoding='utf-8')
    return re.sub(r'\{%\s*comment\s*%\}.*?\{%\s*endcomment\s*%\}', '', src, flags=re.S)


def test_the_page_uses_the_shared_primitives():
    html = _markup()

    for primitive in ('pp-switch', 'pp-horizon', 'pp-bgal__chip', 'pp-gbrowse__spinner',
                      'pp-head-cascade', 'pp-tally', 'scard'):
        assert primitive in html, f'{primitive} is not used -- something was hand-rolled instead'


def test_the_switcher_follows_the_site_standard():
    """Playbook §3 / design-system (Tab Group): a bare `.pp-switch` in its own RIGHT-ALIGNED row. The first
    cut put it left-aligned inside a `.pp-toolbar-card`, which is a treatment it has nowhere else on the
    site -- a toolbar card is quiet chrome for a search/sort bar (§5), not a frame for the view toggle."""
    html = _markup()

    assert 'pp-toolbar-card' not in html, 'the switcher (or a content panel) is back in a toolbar card'

    # The switcher's OWN opening tag, then the wrapper that encloses it -- one `<div` further back, since
    # rindex from `class="pp-switch"` lands on the switcher itself.
    own = html.rindex('<div', 0, html.index('class="pp-switch"'))
    row = html[html.rindex('<div', 0, own):own]
    assert 'md:justify-between' in row or 'md:justify-end' in row, (
        f'the switcher row is not right-aligned at md: {" ".join(row.split())[:140]}'
    )


def test_the_content_panels_are_content_not_chrome():
    """The three panels are the CONTENT, so they take the content-card depth (§4): base rung, catch a
    highlight, cast a shadow. `.pp-toolbar-card` is the quiet chrome surface and reads flat here."""
    css = (ROOT / 'static' / 'css' / 'components' / 'rate-wizard.css').read_text(encoding='utf-8')
    card = css[css.index('.rmg__card {'):css.index('}', css.index('.rmg__card {'))]

    assert '--pp-bg-1' in card, 'the content card is off the base surface rung'
    assert 'inset 0 1px 0' in card and '0 6px 20px' in card, 'it has no depth-pass shadow'


def test_switching_queues_settles_instead_of_blanking():
    """Playbook §7/§8. The stage used to tear down to a spinner on every switch, which reads as a full-page
    reload even though nothing navigates -- and §7 is explicit that a switcher must never do that. It dims
    in place now and the incoming queue slides in directionally."""
    js = (ROOT / 'static' / 'js' / 'rate-my-games.js').read_text(encoding='utf-8')
    css = (ROOT / 'static' / 'css' / 'components' / 'rate-wizard.css').read_text(encoding='utf-8')

    assert 'slideViewIn' in js, 'the incoming queue does not slide in'
    assert "classList.add('is-swapping')" in js, 'the stage does not settle while the queue loads'
    assert '.rmg__stage.is-swapping' in css, 'the settle class has no rule, so the dim never shows'
    # The dim must be released even when the fetch throws, or the retry button sits under pointer-events:none.
    tail = js[js.index('async load(opts)'):js.index('/** Top up the queue')]
    assert tail.index("classList.remove('is-swapping')") > tail.index('} finally {'), (
        'the settle is cleared before the catch, so a failed switch leaves the stage dimmed and dead'
    )


def test_the_switcher_syncs_the_url_like_every_other_view_toggle():
    """§3: sync with `syncViewParam`. Reload/Back should keep the queue you were in, and the legacy
    `?queue_type=` links (the Community hub's "Rate DLC") must keep working."""
    js = (ROOT / 'static' / 'js' / 'rate-my-games.js').read_text(encoding='utf-8')

    assert 'syncViewParam' in js
    assert "get('queue_type')" in js, 'the older deep-link spelling stopped being read'


def test_the_header_states_both_halves_of_both_queues(client):
    """Four numbers: what is left in each queue, and how much there is to rate at all. The totals are the
    denominators the progress meter is measured in, so without them "Game 12 of 79" is unanchored."""
    profile = _hunter()
    _ratable(profile, name='Rated already')
    _dlc_ready(profile, name='Untouched')
    client.force_login(profile.user)
    # Rate one base game so the totals and the remainders have to differ.
    first = client.get(QUEUE).json()['queue'][0]
    client.post(
        f"/api/v1/ratings/{first['concept_id']}/group/default/rate/",
        {'difficulty': 5, 'grindiness': 5, 'hours_to_platinum': 10,
         'fun_ranking': 5, 'overall_rating': 3, 'blurb': ''},
        content_type='application/json',
    )

    ctx = _page(client, profile).context
    p = ctx['rating_progress']

    assert p['games_total'] == 2, p
    assert p['games_waiting'] == 1, p
    assert p['dlc_total'] == 1, p
    assert p['dlc_waiting'] == 1, p
    # The JS still reads the two remainders by their old names.
    assert ctx['unrated_count'] == p['games_waiting']
    assert ctx['unrated_dlc_count'] == p['dlc_waiting']


def test_an_unfinished_dlc_is_not_something_you_can_rate(client):
    """`dlc_total` counts COMPLETED groups, the same set the wizard queue paginates -- otherwise the
    header's denominator and the meter's would disagree."""
    from tests.factories import TrophyFactory, TrophyGroupFactory

    profile = _hunter()
    game = _ratable(profile)
    TrophyGroupFactory(game=game, trophy_group_id='001')
    ConceptTrophyGroupFactory(concept=game.concept, trophy_group_id='001', display_name='Unfinished')
    TrophyFactory(game=game, trophy_type='gold', trophy_group_id='001')   # never earned

    client.force_login(profile.user)
    ctx = _page(client, profile).context

    assert ctx['rating_progress']['dlc_total'] == 0, 'an unfinished DLC counted as ratable'


def test_the_header_counts_do_not_scale_with_the_library(client):
    """The DLC count used to run a query PAIR per DLC group of every ratable concept -- hundreds of
    queries to render one header number for a big completed library."""
    def _measure(rows):
        profile = _hunter()
        for i in range(rows):
            _dlc_ready(profile, name=f'Game {i:02d}')
        client.force_login(profile.user)
        client.get(URL)                                     # warm session/auth
        with CaptureQueriesContext(connection) as ctx:
            assert client.get(URL).status_code == 200
        return len(ctx)

    small, big = _measure(2), _measure(10)

    assert small == big, f'header queries grew with the library: {small} -> {big}'


def test_everything_the_wizard_hides_can_actually_be_hidden():
    """Tailwind's `.hidden` sits in `@layer utilities`; component CSS is @imported UNLAYERED, and unlayered
    beats layered whatever the specificity. So a component rule that sets `display` silently defeats the
    `.hidden` the controller toggles.

    This shipped in the first cut: a base-game card wearing a DLC pill AND a shovelware pill, because
    hiding them did nothing at all. Every element the JS hides that has a `display` of its own must have
    the utility restated."""
    css = (ROOT / 'static' / 'css' / 'components' / 'rate-wizard.css').read_text(encoding='utf-8')
    restated = set(re.findall(r'\.([a-z_-]+)\.hidden', css))

    # The elements the controller toggles that carry a display of their own. Add to this list whenever the
    # wizard learns to hide something new.
    for cls in ('rmg__prog', 'rmg__facts', 'rmg__fact', 'rmg__group', 'rmg__flag', 'gd-btn'):
        assert cls in restated, f'.{cls} is toggled hidden but keeps its own display'


def test_the_bulk_flow_has_a_keyboard():
    """Seventy games is the same four movements seventy times, so it earns shortcuts. Two things have to
    be true or they do damage: Enter must not fire inside the quick take (where it is a newline the hunter
    is typing), and the document-level listener must bind ONCE -- onPageReady re-runs element wiring on
    every HTMX history restore, so an unguarded document listener stacks one per restore."""
    js = (ROOT / 'static' / 'js' / 'rate-my-games.js').read_text(encoding='utf-8')
    html = _markup()

    keys = js[js.index('wireKeys() {'):js.index('advance(counts) {')]
    assert "tag === 'TEXTAREA'" in keys, 'Enter would submit from inside the quick take'
    # The shortcut and the autofocus shipped together and cancelled each other out: the card focuses the
    # hours field on arrival, so the hunter is ALWAYS inside an <input> when it lands, and a guard that
    # treats every <input> as typing means S never fires -- while the hint promises it does. Only fields
    # where a letter is content may suppress it.
    assert 'TEXT_ENTRY' in keys, 'every <input> counts as typing again, which kills the S shortcut'
    assert "'number'" not in js[js.index('var TEXT_ENTRY'):js.index('var TEXT_ENTRY') + 200], (
        'the hours field counts as text entry, so S does nothing on a freshly-arrived card'
    )
    assert 'if (first !== false) { this.wireKeys(); }' in js, 'the document listener is not first-load guarded'
    assert 'init(first)' in html or 'RateMyGames.init(first)' in html, 'the page never passes `first` through'
    assert 'rmg__keys' in html, 'the shortcuts are undiscoverable -- no hint is rendered'


def test_the_progress_meter_warms_as_the_queue_empties():
    """Horizon's BAND tone is its "how close am I" semantic, not decoration -- and it is the primitive's
    own client API that keeps the band in sync with the fill, so this must not hand-roll the CSS var."""
    js = (ROOT / 'static' / 'js' / 'rate-my-games.js').read_text(encoding='utf-8')
    html = _markup()

    assert 'data-horizon-band' in html, 'the meter is in the flat themed tone, so it never warms'
    assert 'PP.Horizon.update' in js, 'progress is set without the shared Horizon API'
    assert '--horizon-progress' not in js, 'the fill is being written directly again, bypassing the band'
    assert 'js/horizon.js' in html, 'the Horizon client API is never loaded, so the meter would not move'


def test_nothing_is_painted_behind_the_header_text():
    """The header carried the game's landscape art as a blurred wash for recall. It went: it read as noise
    behind the one question the page asks, and the cover alone does the recognising.

    Pinned because the wash was not a single rule. It needed a blur to make 9.5px labels safe over it, a
    flat veil for uniform contrast, a cross-fade so a swap did not hard-cut, and a payload field to feed
    it -- so reintroducing it means reintroducing all of that, and the leftovers are what would rot.
    """
    css = (ROOT / 'static' / 'css' / 'components' / 'rate-wizard.css').read_text(encoding='utf-8')
    js = (ROOT / 'static' / 'js' / 'rate-my-games.js').read_text(encoding='utf-8')
    api = (ROOT / 'api' / 'rating_views.py').read_text(encoding='utf-8')
    rules = re.sub(r'/\*.*?\*/', '', css, flags=re.S)      # the comment explains the removal by name

    assert '.rmg__hero-art' not in rules, 'the backdrop wash is back'
    # ...but the card must still CLIP. `overflow: hidden` reads like a leftover of the art once the art is
    # gone, and it is not: the cover's 18px drop shadow bleeds past the card's rounded corner without it.
    assert re.search(r'\.rmg__hero \{[^}]*overflow:\s*hidden', rules), (
        "the card no longer clips, so the cover's shadow spills past its corner"
    )
    assert 'blur(' not in rules, 'something is blurred behind the header again'
    assert 'renderArt' not in js, 'the backdrop renderer survived its markup'
    assert "'landscape_url'" not in api, 'the queue still pays for art nothing draws'


def test_the_page_opens_with_the_shared_beat():
    """Every rebuilt page enters the same way (playbook §6). This one hard-cut in below the header: the
    switcher row, the meter and the whole card just appeared.

    The card's entrance has to be applied from JS rather than sitting in the markup -- it arrives from a
    fetch, so a CSS animation declared on it would have run and finished while it was still `hidden`."""
    html = _markup()
    js = (ROOT / 'static' / 'js' / 'rate-my-games.js').read_text(encoding='utf-8')
    css = (ROOT / 'static' / 'css' / 'components' / 'rate-wizard.css').read_text(encoding='utf-8')

    assert 'pp-head-cascade' in html, 'the header lost its opening beat'
    assert 'rmg__enter-row' in html, 'the switcher row still hard-cuts in'
    assert "node.classList.add('pp-head-cascade')" in js, 'the card never gets an entrance'
    # Reuses the shared keyframe rather than inventing a second rise on a different curve.
    assert 'animation: ppHeadIn' in css, 'the page opens on its own bespoke motion'
    # One-shot: from here on, arrivals belong to the deal motion.
    assert 'if (this.entered || !node) { return; }' in js, 'the opening beat replays for every game'


def test_the_deal_motion_is_reduced_motion_gated():
    """The card-to-card transition is the page's one signature moment, so it is also the one thing here
    that has to collapse to a plain swap for anyone who asked for less movement."""
    css = (ROOT / 'static' / 'css' / 'components' / 'rate-wizard.css').read_text(encoding='utf-8')
    js = (ROOT / 'static' / 'js' / 'rate-my-games.js').read_text(encoding='utf-8')

    deal = css[css.index('@keyframes rmgDealOut'):]
    assert 'prefers-reduced-motion: no-preference' in deal, 'the deal animation is not gated'
    assert 'prefers-reduced-motion: reduce' in js, 'the JS still runs the timed swap under reduced motion'


def test_the_trophy_list_renderer_is_off_daisyui():
    """It is a SHARED renderer, so its markup is a primitive (.pp-trolist) rather than page classes."""
    src = (ROOT / 'static' / 'js' / 'utils.js').read_text(encoding='utf-8')
    body = src[src.index('const TrophyListRenderer'):src.index('SpoilerToggle')]

    assert 'pp-trolist__row' in body
    for legacy in ('badge badge-xs', 'text-base-content/', 'bg-success/10'):
        assert legacy not in body, f'{legacy} survived in the shared trophy renderer'


def test_the_field_partial_renders_standalone():
    """It is included by two hosts with different context; nothing in it may depend on a variable only one
    of them sets. `user_play_hours` is the optional one -- Game Detail SSRs it, the wizard fills it per
    game from JS."""
    bare = render_to_string('partials/_rating_fields.html', {})
    hinted = render_to_string('partials/_rating_fields.html', {'user_play_hours': 42})

    for name in FIELD_NAMES:
        assert f'name="{name}"' in bare
    assert "don't have your playtime" in bare
    assert '<b>42</b>' in hinted


def test_the_guidelines_link_is_wired_by_the_component_that_renders_it():
    """The notice's "Community Guidelines" button is the ONLY `[data-gd-guidelines-open]` trigger on the
    site, and it lives in the shared field partial -- so every host of those fields renders the link.

    The wiring therefore belongs to the shared controller, not to each host. It used to sit in the page
    controllers (game-detail.js, plat-cards.js), and the wizard -- the one surface built entirely around
    rating games -- included the dialog, rendered the link, and never called the wiring. The link was dead.
    """
    js = (ROOT / 'static' / 'js' / 'quick-rate.js').read_text(encoding='utf-8')
    attach = js[js.index('function attach('):js.index('PP.RatingFields =')]

    assert 'wireGuidelinesSheet' in attach, (
        'the shared fields no longer wire their own guidelines link, so each host must remember to'
    )


def test_the_ledger_rule_turns_with_the_wrap_not_with_a_label():
    """The header's ledger sits beside the title on a wide card and beneath it otherwise, and the rule
    separating it turns with that: vertical when beside, horizontal when beneath.

    The bug this pins is the two coming apart. The beside treatment was first applied at `md`, where the
    row does not actually fit -- so the ledger wrapped while keeping the vertical rule and the
    `margin-left: auto` meant for the un-wrapped case, and floated against an empty half-row. Moving the
    breakpoint alone does not fix it either: the card's inner width is not monotonic in the viewport, since
    the two-column split takes width back. The fix is that the SAME block which turns the rule also brings
    the title's basis in far enough to guarantee the row fits, so one number decides both.
    """
    css = (ROOT / 'static' / 'css' / 'components' / 'rate-wizard.css').read_text(encoding='utf-8')
    css = re.sub(r'/\*.*?\*/', '', css, flags=re.S)

    blocks = re.findall(r'@media \(min-width: (\d+)px\) \{(.*?)\n\}', css, flags=re.S)
    beside = [(int(w), body) for w, body in blocks if 'border-left' in body and '.rmg__facts' in body]
    assert beside, 'the ledger never moves up beside the title'

    for width, body in beside:
        assert 'flex-basis' in body and '.rmg__ident' in body, (
            f'the {width}px block turns the rule vertical without constraining the title, so the row can '
            f'wrap while still styled as though it had not'
        )
