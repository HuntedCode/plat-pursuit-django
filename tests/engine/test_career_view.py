"""The merged Career view: jobs + the Contracts (job board) browse on one login-gated surface.

Pins that /career/ renders both the job views and the folded-in Contracts browse, that
?view=contracts deep-links the Contracts tab, that the old /research-panel/ 301s into it, and that
the whole surface is linked-profile gated.
"""
import pytest

from trophies.models import Contract, ContractMembership, Job
from tests.factories import ConceptFactory, GameFactory, ProfileFactory

pytestmark = pytest.mark.django_db


def _live_contract(slug, jobs=('gunslinger',)):
    c = Contract.objects.create(name=slug, slug=slug, is_live=True)
    c.jobs.set(Job.objects.filter(slug__in=jobs))
    concept = ConceptFactory()
    GameFactory(concept=concept)   # PS5 by factory default -> passes the current-gen platform default
    ContractMembership.objects.create(contract=c, concept=concept)
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
