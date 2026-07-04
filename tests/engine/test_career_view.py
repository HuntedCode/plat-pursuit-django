"""The merged Career view: jobs + the Contracts (job board) browse on one login-gated surface.

Pins that /career/ renders both the job views and the folded-in Contracts browse, that
?view=contracts deep-links the Contracts tab, that the old /research-panel/ 301s into it, and that
the whole surface is linked-profile gated.
"""
import pytest

from tests.factories import ProfileFactory

pytestmark = pytest.mark.django_db


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
