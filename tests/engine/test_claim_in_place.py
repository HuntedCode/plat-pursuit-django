"""After a claim, Career refreshes its panels IN PLACE instead of reloading the page.

career.js `refreshAfterClaim` fetches the page again while the claim ceremony plays and swaps each region
in `REFRESH_REGIONS` from it (the page is the one renderer, so nothing re-derives a number client-side).
If a region's count differs between the two pages it falls back to a reload, so a region the template
stops rendering would quietly turn every claim back into a reload. These pins hold the selectors to the
real page and the claim flow to its shape. There is no JS runner: the flow is pinned as source text, and
it was exercised in a real browser (the accept POST intercepted, everything after it live).
"""
import re

import pytest
from django.utils import timezone

from trophies.models import Contract, EarnedContract, Job
from tests.factories import ConceptFactory, GameFactory, IGDBMatchFactory, ProfileFactory

pytestmark = pytest.mark.django_db


def _script():
    from tests.career_sources import career_script

    return career_script()


def _code(start, end):
    from tests.engine.test_career_view import _career_js

    return _career_js(start, end)


# Each selector in career.js's REFRESH_REGIONS (as written in the source), and a regex that finds the element
# it names in the rendered page. Kept equal to the source list, so renaming either side fails here.
_REGIONS = {
    """'[data-career-panel="hero"]'""": r'<section data-career-panel="hero"',
    """'#rank-ladder .prk-now'""": r'id="rank-ladder"[\s\S]*?class="prk-now"',
    """'#rank-ladder .prk-list'""": r'id="rank-ladder"[\s\S]*?class="prk-list"',
    """'[data-career-panel="banner"]'""": r'<div data-career-panel="banner">',
    """'[data-career-panel="summary"]'""": r'data-career-panel="summary"',
    """'[data-career-panel="claim-all"]'""": r'<span data-career-panel="claim-all" class="contents">',
    """'#career-panel-jobs .jdoss'""": r'id="career-panel-jobs"[\s\S]*?class="jobs-layout jdoss"',
    """'#career-panel-jobs .jsheet'""": r'id="career-panel-jobs"[\s\S]*?class="jobs-layout jsheet"',
    """'#element-details'""": r'id="element-details"',
    """'#career-radar'""": r'<script id="career-radar" type="application/json">',
    """'.pp-switch__chip[data-view="contracts"]'""": r'<button[^>]*class="pp-switch__chip[^"]*"[^>]*data-view="contracts"',
    """'a.pp-navhub[href="' + CAREER_PATH + '"]'""": r'<a href="/career/" class="pp-navhub',
    """'a.mobile-tabbar-item[href="' + CAREER_PATH + '"]'""": r'<a href="/career/" class="mobile-tabbar-item',
    """'a.pp-subpill[href="' + CAREER_PATH + '"]'""": r'<a href="/career/" class="pp-subpill',
    """'.pp-sub__mbar'""": r'class="pp-sub__mbar"',
}


def _page(client, *, claimable):
    profile = ProfileFactory(is_linked=True)
    client.force_login(profile.user)
    if claimable:
        contract = Contract.objects.create(name='inplace', slug='inplace', igdb_id=97001, is_live=True)
        contract.jobs.set(Job.objects.filter(slug='gunslinger'))
        concept = ConceptFactory(anchor_migration_completed_at=timezone.now())
        IGDBMatchFactory(concept=concept, igdb_id=97001)
        GameFactory(concept=concept)
        EarnedContract.objects.create(profile=profile, contract=contract, full_reached_at=timezone.now())
    return client.get('/career/').content.decode()


def test_every_refresh_region_is_on_the_page(client):
    """A region missing from the page is a region whose count differs on refresh, so the claim falls back
    to a full reload: silently, on every claim. Every selector must find its element."""
    regions = re.findall(r"\{ sel: (.+?), attrs: \[", _script())
    assert set(regions) == set(_REGIONS), (
        f'REFRESH_REGIONS changed: map {set(regions) ^ set(_REGIONS)} to the element each names')
    body = _page(client, claimable=True)
    missing = [sel for sel, pattern in _REGIONS.items() if not re.search(pattern, body)]
    assert not missing, f'not on the rendered page: {missing}'
    # The board's page 1 is rendered out of the same page (`boardFromPage`), which reads its total and
    # has-next from #rp-list itself.
    rp_list = re.search(r'<div id="rp-list"[^>]*>', body).group(0)
    assert re.search(r'data-total="\d+"', rp_list) and re.search(r'data-has-next="[01]"', rp_list)


def test_the_banner_region_renders_even_with_nothing_to_claim(client):
    """The last claim empties the banner: there must still be a container on both sides to swap, or the
    counts differ and the refresh reloads instead."""
    body = _page(client, claimable=False)
    assert '<div data-career-panel="banner">' in body
    inside = body.split('<div data-career-panel="banner">', 1)[1]
    assert inside.lstrip().startswith('</div>'), 'the banner region is not empty with nothing to claim'
    # ...and so is Claim all's, which a claim can also bring back.
    assert '<span data-career-panel="claim-all" class="contents"></span>' in body


def test_a_claim_refreshes_in_place_and_only_reloads_as_a_fallback():
    code = _code('function swapRegions(fresh) {', '// ---- Server-driven filter/sort + infinite scroll ----')
    apply = code[code.index('function applyResult('):code.index('async function accept(')]
    assert 'reloadKeepingView' not in apply, 'a claim reloads the page again'
    assert 'refreshAfterClaim(shown, byKeyboard)' in apply

    refresh = code[code.index('function refreshAfterClaim('):code.index('function applyResult(')]
    # The fallbacks, and only those: a failed fetch or a page that changed shape, and anything thrown.
    assert refresh.count('reloadKeepingView()') == 2
    assert re.search(r'if \(!out\[0\] \|\| !swapRegions\(out\[0\]\)\) \{ reloadKeepingView\(\); return; \}', refresh)
    assert re.search(r'\}\)\.catch\(function \(\) \{ reloadKeepingView\(\); \}\);\s*\}?\s*$', refresh.rstrip())
    # The swap (or the fallback reload) WAITS for the ceremony: a failed fetch resolves to null rather than
    # rejecting Promise.all, which would reload while the overlay is still animating.
    assert 'Promise.all([page, shown])' in refresh
    assert re.search(r"\.catch\(function \(\) \{ return null; \}\);", refresh), 'a failed fetch cuts the ceremony off'
    # ONE render per claim: the page is fetched for the BOARD'S state (on Jobs/Radar the address bar has had
    # the board's params stripped), and the board's page 1 comes out of that same page rather than a second
    # /results/ round trip. Nothing on this path writes the URL, so no tab's URL can collect board params.
    assert "fetch(location.pathname + '?' + boardSearch()" in refresh
    assert 'boardFromPage(out[0], asked);' in refresh and 'fetchPage(' not in refresh
    from_page = _code('function boardFromPage(doc, asked) {', 'var sentIO')
    assert from_page.index('if (controller) controller.abort();') < from_page.index('renderBoard(')
    assert '++reqSeq;' in from_page, 'a board fetch in flight could land on top of the refreshed board'
    assert from_page.index('renderBoard(') < from_page.index('boardQuery = asked;'), \
        'the refreshed board is not recorded as the board on screen (the dedupe)'
    # syncURL and the refresh share the one builder, so the page fetched is the board the URL would name.
    assert 'var qs = boardSearch();' in _code('function syncURL() {', 'function updateEmptyStates')
    # A finished refresh releases the claim guard, or every later claim in this page view is ignored.
    assert refresh.index('claiming = false;') > refresh.index('swapRegions(out[0])')
    nothing = apply[apply.index('if (!res.granted)'):]
    assert nothing.index('claiming = false;') < nothing.index('return;'), 'a zero-XP claim locks claiming'


def test_the_swap_replaces_contents_never_elements():
    """Listeners and observers on the region elements survive only because the ELEMENTS stay: contents are
    swapped (innerHTML), never the node itself (outerHTML / replaceWith)."""
    swap = _code('function swapRegions(fresh) {', 'function played(')
    assert "p[0].innerHTML = p[1].innerHTML;" in swap
    assert 'outerHTML' not in _script() and 'replaceWith' not in swap
    assert re.search(r'if \(cur\.length !== next\.length\) return false;', swap), 'a changed page shape is swapped blind'
    assert "fresh.querySelectorAll('.pp-head-cascade')" in swap, 'swapped headers replay their entrance cascade'
    assert "dispatchEvent(new CustomEvent('pp:career-refreshed'))" in swap


def test_one_claim_at_a_time_and_a_ceremony_cannot_cost_the_refresh():
    accept = _code('async function accept(payload, btn, byKeyboard) {', '// `e.detail === 0`')
    assert re.search(r'if \(!API \|\| claiming\) return;\s*claiming = true;', accept), 'two claims can race the refresh'
    assert accept.index('claiming = false;') > accept.index('catch (err)'), 'a failed claim locks claiming for good'
    played = _code('function played(Ceremony, claim) {', 'var claiming = false;')
    assert re.search(r'try \{ return Promise\.resolve\(Ceremony\.play\(claim\)\)\.catch\(', played)
    assert 'catch (e) { return Promise.resolve(); }' in played


def test_what_lives_inside_a_region_is_delegated_or_listens_for_the_refresh():
    """A swap replaces the nodes inside a region, so nothing may bind to them one by one: the banner jump,
    the job tiles (dossier and sheet) and the dossier tilt are delegated, and the radar and the dossier
    reveal re-read on `pp:career-refreshed`."""
    src = _script()
    for per_node in ("querySelectorAll('[data-lab-goto]').forEach", "querySelectorAll('.ptile').forEach",
                     "querySelectorAll('.jdoss .job').forEach"):
        assert per_node not in src, f'bound per node again: {per_node}'
    assert "e.target.closest('[data-lab-goto]')" in src and "e.target.closest('#rp-accept-all')" in src
    assert "getElementById('rp-accept-all');\n    if (allBtn)" not in src, 'Claim all is bound to a node a claim replaces'
    assert "jobsPanel.addEventListener('click'" in src and "jobsPanel.addEventListener('keydown'" in src
    assert "jdoss.addEventListener('mouseover'" in src
    assert src.count("document.addEventListener('pp:career-refreshed'") == 2, 'the radar or the dossier reveal stopped listening'
    radar = _code("document.addEventListener('pp:career-refreshed', function () {\n            try {", 'paint(currentMode);')
    assert "RMAX = RADAR.max; DATA = RADAR.modes; JOB_SLUGS = RADAR.job_slugs;" in radar


def test_the_refreshed_board_arrives_settled_not_replayed():
    """After a claim the board on screen is the board the hunter was already looking at: its cards must not
    replay the entrance (stagger, XP count-up from 0, ring draw, cell ignite), or every claim makes all 24
    cards flash and recount. Seen frame by frame in a browser before this was fixed. `boardFromPage` renders
    `settled`; a settled render stamps each card revealed (cardReveal skips a revealed card) and skips the
    stagger. A filter change still animates (fetchPage passes no `settled`)."""
    render = _code('function renderBoard(scope, more, total, append, n, settled) {', 'function boardFromPage(')
    assert re.search(r"if \(settled\) cards\.forEach\(function \(c\) \{ c\.dataset\.revealed = '1'; \}\);", render)
    assert render.index("c.dataset.revealed = '1'") < render.index('initCards(list);'), 'stamped after the reveal ran'
    assert 'if (!settled) staggerCards(cards);' in render
    from_page = _code('function boardFromPage(doc, asked) {', 'var sentIO')
    assert re.search(r"parseInt\(freshList\.dataset\.total \|\| '0', 10\), false, 1, true\);", from_page), \
        'the refreshed board is rendered with its entrance animations'
    fetch_page = _code('function fetchPage(n, append) {', 'function renderBoard(')
    assert 'renderBoard(tmp, d.hasNext, d.total, append, n);' in fetch_page, 'a filter change lost its entrance'


def test_a_filter_changed_during_the_refresh_is_not_overwritten_by_the_fetched_board():
    """The ceremony can be dismissed (or, on the toast path, never shown) while the page is still on its way,
    and the hunter can tap a filter in that window. The fetched page holds the board they LEFT: rendering it
    would show the old cards under the new chip, and recording the new query would make the dedupe drop the
    re-tap that should fix it. So the refresh records the state it asked for BEFORE fetching, and renders the
    page's board only if the board still asks for that state; otherwise the new state gets its own fetch."""
    refresh = _code('function refreshAfterClaim(shown, byKeyboard) {', 'function applyResult(')
    assert refresh.index('var asked = buildParams(1);') < refresh.index('fetch(location.pathname'), \
        'the asked-for state is read after the fetch is sent'
    from_page = _code('function boardFromPage(doc, asked) {', 'var sentIO')
    guard = re.search(r'if \(buildParams\(1\) !== asked\) \{ boardQuery = null; fetchPage\(1, false\); return; \}',
                      from_page)
    assert guard, 'a stale fetched board can land on top of a filter the hunter changed'
    assert guard.start() < from_page.index('renderBoard('), 'the state check runs after the render'
    assert 'boardQuery = asked;' in from_page, 'the dedupe records a query the rendered board was not built for'
    # And only the cards and their facets reach renderBoard, not the whole panel they were parsed in.
    assert 'holder.appendChild(freshList);' in from_page and 'renderBoard(holder,' in from_page
