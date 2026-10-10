"""The merged Career view: jobs + the Contracts (job board) browse on one login-gated surface.

Pins that /career/ renders both the job views and the folded-in Contracts browse, that
?view=contracts deep-links the Contracts tab, that the old /research-panel/ 301s into it, and that
the whole surface is linked-profile gated. Plus source-text pins (there is no JS runner) on the board
controller's identical-query dedupe in fetchPage and on the claim banner's jump to the board.
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


def _career_src():
    import pathlib

    return (pathlib.Path(__file__).resolve().parents[2]
            / 'templates' / 'trophies' / 'career.html').read_text(encoding='utf-8')


def _career_js(start, end):
    """The career.html source from a UNIQUE `start` anchor to the first `end` after it, comments stripped
    so prose about a guard cannot pass for the guard. The `//` strip is naive: a `//` inside a string
    literal in the slice would be eaten too, which can only make a pin fail (never pass), so a confusing
    failure here is worth checking for that first."""
    import re

    src = _career_src()
    assert src.count(start) == 1, f'the start anchor is missing or no longer unique: {start!r}'
    rest = src.split(start, 1)[1]
    assert end in rest, f'no {end!r} after {start!r}'
    code = rest.split(end, 1)[0]
    code = re.sub(r'/\*.*?\*/', '', code, flags=re.S)
    return re.sub(r'(?m)//.*$', '', code)


def test_an_identical_board_request_is_dropped():
    """A page-1 fetch for the board already on screen, or already on its way, is dropped: a re-tapped
    active chip, a repeated claim-banner jump, Clear filters with nothing set, a repeated radar jump.
    Each refetched an identical board, aborting any request already in flight, and prod logged a
    run of eleven aborted `?status=claimable&page=1` requests from one user. No JS runner, so this is
    source text. The check must run before the abort and the fetch, record the query it lets through,
    and live in the PAGE-1 branch: hoisted out of it, every scroll-append would match the board on
    screen and infinite scroll would silently stop."""
    import re

    code = _career_js('function fetchPage(n, append) {', 'var sentIO')
    at = code.find('if (!append) {')
    assert at != -1, "fetchPage lost its page-1 branch, so this pin no longer checks it"
    page1 = code[at:code.index('}', at)]   # the branch holds no braces of its own
    check = re.search(r'if\s*\(\s*query\s*===\s*boardQuery\s*\)\s*return\s*;', page1)
    assert check, 'an identical board request is fetched again (or the check left the page-1 branch)'
    assert re.search(r'boardQuery\s*=\s*query\s*;', page1[check.end():]), 'the query it lets through is never recorded'
    for later in ('controller.abort()', 'fetch(RESULTS_URL'):
        where = code.find(later)
        assert where != -1, f'{later!r} is gone from fetchPage, so this pin no longer checks the order'
        assert at + check.start() < where, f'the identical-query check runs after {later!r}'


def test_a_failed_board_load_can_be_retried_with_the_same_query():
    """After a failed page-1 load the grid is still the board from before it, so the same query must be
    allowed again (a re-tap is the obvious retry). The failure branch is the ONLY place it is cleared: a
    clear on the success path would let every identical request through again."""
    import re

    code = _career_js('function fetchPage(n, append) {', 'var sentIO')
    at = code.find('.catch(')
    assert at != -1, 'fetchPage lost its failure branch, so this pin no longer checks it'
    assert 'if (!append) boardQuery = null;' in code[at:], 'a failed load blocks retrying the same query'
    assert len(re.findall(r'boardQuery\s*=\s*null', code)) == 1, 'boardQuery is cleared outside the failure branch'


def test_the_server_rendered_board_counts_as_on_screen():
    """The first page comes from the server for the URL's params, so the query seeded from the URL is
    recorded as the board on screen. Without it, the first re-tap after load refetches the SSR board."""
    code = _career_js('seedFromURL();', 'initCards(list);')
    assert code.lstrip().startswith('boardQuery = buildParams(1);'), 'the SSR board is not recorded'


def test_the_claim_banner_jump_opens_the_board_on_every_claimable():
    """The banner's jump clicked the Ready to Claim chip, which kept whatever scope was showing: from
    History it lit a hidden chip, the server dropped the status, and the hunter stayed on History. It now
    hands the board a status filter with every platform lit (the banner counts claimables on every
    platform; the server half is pinned in test_contracts_service). Source text, no JS runner."""
    import re

    jump = _career_js("document.querySelectorAll('[data-lab-goto]')", 'scrollIntoView')
    assert re.search(r"dispatchEvent\(\s*new CustomEvent\(\s*'pp:board-filter',\s*\{\s*detail:\s*"
                     r"\{\s*status:\s*f,\s*allPlatforms:\s*true\s*\}", jump), 'the banner no longer opens the board filter'
    assert '.click()' not in jump, 'the banner clicks a chip again, which keeps a History scope'

    handler = _career_js("document.addEventListener('pp:board-filter'", 'fetchPage(1, false);')
    for needle, why in (("state.scope = 'board';", 'the jump no longer leaves History'),
                        ("state.status = f.status || '';", 'the jump drops the status it was given'),
                        ('f.allPlatforms ? VALID_PLATS.slice() : null', 'the jump no longer asks for every platform'),
                        ('var lit = state.platforms || DEFAULT_PLATS;', 'the platform chips do not show what was asked'),
                        ("(state.status || 'all') === c.dataset.filter", 'the status chips do not show the jump')):
        assert needle in handler, why


def test_the_platform_chips_are_every_platform_the_server_knows():
    """`VALID_PLATS` (what "every platform" means to the banner jump) is read from these chips, while the
    banner's href (`claim_board_url`) is built from ALL_PLATFORMS. A chip missing on one side would drop a
    platform from the jump. ORDER matters too: buildParams emits platforms in state order, so the board
    seeded from the href and the board the jump requests are the same `boardQuery` (a repeat banner tap
    is then a no-op) only while the chips run in ALL_PLATFORMS order."""
    import re

    from trophies.util_modules.constants import ALL_PLATFORMS

    chips = re.findall(r'class="rp-chip rp-plat[^"]*" data-plat="([^"]+)"', _career_src())
    assert chips == list(ALL_PLATFORMS), chips


def test_the_claim_banner_links_to_the_board_it_opens(client):
    """The banner carries the jump's hook, and its href is the same board (Ready to Claim, every platform),
    so a new tab, a copied link or a no-JS tap lands on the N it counts."""
    from django.utils import timezone as tz
    from django.utils.html import escape

    from trophies.models import EarnedContract
    from trophies.util_modules.constants import ALL_PLATFORMS

    profile = ProfileFactory(is_linked=True)
    client.force_login(profile.user)
    c = _live_contract('banner-claim')
    EarnedContract.objects.create(profile=profile, contract=c, has_platinum=True, platinum_reached_at=tz.now())

    body = client.get('/career/').content.decode()
    href = '?view=contracts&status=claimable' + ''.join(f'&platform={p}' for p in ALL_PLATFORMS)
    assert f'href="{escape(href)}" data-lab-goto="contracts" data-goto-filter="claimable"' in body


def test_build_params_encodes_every_field_of_the_board_state():
    """Dropping an identical query is only safe if an identical query asks for the same filters. A field
    of `state` that buildParams never EMITS would let a real change through as a duplicate and be dropped
    (without the scope line, Board and History at their default sorts are both `page=1`). So each field
    must sit on a line that writes its own key, not merely be read somewhere in the function. A new field
    fails here until it is given a key below and an emitting line."""
    import re

    keys = {'status': 'status', 'jobs': 'job', 'disciplines': 'discipline', 'q': 'q', 'sort': 'sort',
            'platforms': 'platform', 'newOnly': 'new', 'scope': 'scope'}
    literal = _career_js('var state = {', '};')
    fields = re.findall(r'(\w+)\s*:', literal)
    assert sorted(fields) == sorted(keys), f'the board state is now {fields}: map every field to its key'
    lines = _career_js('function buildParams(n) {', 'function noFilters').splitlines()
    for field, key in keys.items():
        assert any(f'state.{field}' in ln and (f"p.set('{key}'" in ln or f"p.append('{key}'" in ln)
                   for ln in lines), f'buildParams never emits state.{field} as {key!r}'
