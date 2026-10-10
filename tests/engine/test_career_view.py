"""The merged Career view: jobs + the Contracts (job board) browse on one login-gated surface.

Pins that /career/ renders both the job views and the folded-in Contracts browse, that
?view=contracts deep-links the Contracts tab, that the old /research-panel/ 301s into it, and that
the whole surface is linked-profile gated. Plus source-text pins on the board controller's status-chip
re-tap guard (there is no JS runner).
"""
import itertools

import pytest
from django.utils import timezone

from trophies.models import Contract, Job
from tests.factories import ConceptFactory, GameFactory, IGDBMatchFactory, ProfileFactory

pytestmark = pytest.mark.django_db

_igdb_seq = itertools.count(40001)


def _live_contract(slug, jobs=('gunslinger',)):
    igdb_id = next(_igdb_seq)
    c = Contract.objects.create(name=slug, slug=slug, is_live=True, igdb_id=igdb_id)
    c.jobs.set(Job.objects.filter(slug__in=jobs))
    concept = ConceptFactory(anchor_migration_completed_at=timezone.now())
    IGDBMatchFactory(concept=concept, igdb_id=igdb_id)
    GameFactory(concept=concept)   # PS5 by factory default -> passes the current-gen platform default
    return c


def test_career_renders_jobs_and_contracts_on_one_surface(client):
    profile = ProfileFactory(is_linked=True)
    client.force_login(profile.user)

    resp = client.get('/career/')

    assert resp.status_code == 200
    assert b'data-view="jobs"' in resp.content          # the jobs / skills-grid view
    assert b'data-view="contracts"' in resp.content     # the merged Contracts browse
    assert b'id="rp-list"' in resp.content
    # Default tab is the jobs grid.
    assert b'is-active" data-view="jobs"' in resp.content


def test_career_renders_sticky_mini_bar(client):
    profile = ProfileFactory(is_linked=True)
    client.force_login(profile.user)
    html = client.get('/career/').content.decode()
    assert 'class="pp-minibar"' in html               # the reusable condensed page header
    assert 'data-sticky-reveal' in html               # StickyReveal target
    assert 'id="career-minibar-sentinel"' in html     # sentinel after the real tab strip


def test_career_page_embeds_all_facet_dimensions(client):
    # Regression: the view helper once forwarded only status/platform, dropping the popover
    # discipline/job counts, so the dropdown counts never reached the page.
    import json
    profile = ProfileFactory(is_linked=True)
    client.force_login(profile.user)
    _live_contract('facet-check', ('gunslinger', 'mage'))
    resp = client.get('/career/')
    assert resp.status_code == 200
    marker = b'<script id="rp-facets" type="application/json">'
    start = resp.content.index(marker) + len(marker)
    facets = json.loads(resp.content[start:resp.content.index(b'</script>', start)])
    assert set(facets) >= {'status', 'platform', 'discipline', 'job'}   # every dimension the toolbar consumes
    assert facets['job']['gunslinger'] >= 1 and facets['discipline']['combat'] >= 1


def test_career_hero_shows_rank_ladder(client):
    profile = ProfileFactory(is_linked=True)
    client.force_login(profile.user)
    resp = client.get('/career/')
    assert resp.status_code == 200
    assert b'pgl--rank' in resp.content   # the Pursuer rank ladder renders in the hero


def test_view_query_activates_contracts_tab(client):
    profile = ProfileFactory(is_linked=True)
    client.force_login(profile.user)

    resp = client.get('/career/?view=contracts')

    assert resp.status_code == 200
    assert b'is-active" data-view="contracts"' in resp.content


def test_career_renders_board_history_sub_toggle(client):
    profile = ProfileFactory(is_linked=True)
    client.force_login(profile.user)

    resp = client.get('/career/')

    assert resp.status_code == 200
    assert b'data-contract-view="board"' in resp.content       # the Board | History segmented sub-toggle
    assert b'data-contract-view="history"' in resp.content


def test_research_panel_url_redirects_into_career_contracts(client):
    resp = client.get('/research-panel/')
    assert resp.status_code == 301
    assert resp['Location'] == '/career/?view=contracts'


def test_career_is_login_gated(client):
    # Anonymous -> LoginRequiredMixin bounces to login (the whole merged surface is personal).
    resp = client.get('/career/')
    assert resp.status_code == 302


# --- Server-side board endpoints (results partial + lazy modal) ---------------------

def test_contracts_results_endpoint_returns_cards(client):
    profile = ProfileFactory(is_linked=True)
    client.force_login(profile.user)
    _live_contract('res-a')
    resp = client.get('/career/contracts/results/')
    assert resp.status_code == 200
    assert b'data-slug="res-a"' in resp.content and b'rp-row' in resp.content


def test_contracts_results_respects_status_filter(client):
    profile = ProfileFactory(is_linked=True)
    client.force_login(profile.user)
    _live_contract('res-avail')                       # untouched -> 'available'
    resp = client.get('/career/contracts/results/?status=claimable')
    assert b'data-slug="res-avail"' not in resp.content


def test_contract_modal_endpoint(client):
    profile = ProfileFactory(is_linked=True)
    client.force_login(profile.user)
    _live_contract('res-modal', ('gunslinger', 'mage'))
    resp = client.get('/career/contracts/res-modal/modal/')
    assert resp.status_code == 200 and b'rpm__head' in resp.content
    assert client.get('/career/contracts/does-not-exist/modal/').status_code == 404


def test_contracts_endpoints_login_gated(client):
    assert client.get('/career/contracts/results/').status_code == 302
    assert client.get('/career/contracts/x/modal/').status_code == 302


def test_contracts_endpoints_gated_to_linked_profile(client):
    # Logged in but not PSN-linked -> 404 on both (the whole Career surface is linked-gated).
    profile = ProfileFactory(is_linked=False)
    client.force_login(profile.user)
    _live_contract('res-gate')
    assert client.get('/career/contracts/results/').status_code == 404
    assert client.get('/career/contracts/res-gate/modal/').status_code == 404


def _career_js(start, end):
    """The career.html source between two UNIQUE anchors, comments stripped so prose about a guard
    cannot pass for the guard. The `//` strip is naive: a `//` inside a string literal in the slice
    would be eaten too, which can only make a pin fail (never pass), so a confusing failure here is
    worth checking for that first."""
    import pathlib
    import re

    src = (pathlib.Path(__file__).resolve().parents[2]
           / 'templates' / 'trophies' / 'career.html').read_text(encoding='utf-8')
    assert src.count(start) == 1 and src.count(end) == 1, f'the anchors moved: {start!r} / {end!r}'
    code = src.split(start, 1)[1].split(end, 1)[0]
    code = re.sub(r'/\*.*?\*/', '', code, flags=re.S)
    return re.sub(r'(?m)//.*$', '', code)


def test_tapping_the_active_status_chip_does_not_refetch():
    """Re-tapping the active status chip (or the claim banner's jump, which clicks it for you) refetched
    the board it was already showing, and `fetchPage` aborted the request in flight for an identical
    one: prod logged a run of eleven aborted `?status=claimable&page=1` requests from one user. No JS
    runner, so this is source text. The guard must sit after the `closest` lookup and BEFORE the chip
    takes `is-active` (below that it would block every tap) and before the fetch."""
    import re

    code = _career_js('// Status quick-filter (single-select).', '// Sort control.')
    guard = re.search(
        r"if\s*\(\s*chip\.classList\.contains\('is-active'\)\s*&&\s*!boardStale\s*\)\s*return\s*;", code)
    assert guard, 'tapping the active chip refetches the board again (or never retries a failed one)'
    for later in ("chip.classList.add('is-active')", 'fetchPage('):
        at = code.find(later)
        assert at != -1, f'{later!r} is gone from the handler, so this pin no longer checks the order'
        assert guard.start() < at, f'the guard runs after {later!r}'


def test_a_failed_board_fetch_lets_the_active_chip_retry():
    """The guard's premise is that the active chip names the board on screen. A failed page-1 fetch
    breaks it: the chip is already lit and the grid is still the board from before. `boardStale` marks
    that state so a re-tap retries instead of doing nothing. Set in the failure branch, cleared by the
    next page-1 fetch."""
    code = _career_js('function fetchPage(n, append) {', 'var sentIO')
    failure = code[code.find('.catch('):]
    assert '.catch(' in code, 'fetchPage lost its failure branch, so this pin no longer checks it'
    assert 'if (!append) boardStale = true;' in failure, 'a failed fetch no longer marks the board stale'
    assert 'boardStale = false;' in code[:code.find('fetch(RESULTS_URL')], 'a new fetch never clears it'
