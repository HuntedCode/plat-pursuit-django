"""The merged Lab view: elements + the Research (Projects) browse on one login-gated surface.

Pins that /lab/ renders both the element views and the folded-in Projects browse, that
?view=projects deep-links the Projects tab, that the old /research-panel/ 301s into it, and that
the whole surface is linked-profile gated.
"""
import pytest

from tests.factories import ProfileFactory

pytestmark = pytest.mark.django_db


def test_lab_renders_elements_and_projects_on_one_surface(client):
    profile = ProfileFactory(is_linked=True)
    client.force_login(profile.user)

    resp = client.get('/lab/')

    assert resp.status_code == 200
    assert b'data-view="table"' in resp.content      # the elements / periodic-table view
    assert b'data-view="projects"' in resp.content    # the merged Research browse
    assert b'id="rp-list"' in resp.content
    # Default tab is the elements table.
    assert b'is-active" data-view="table"' in resp.content


def test_view_query_activates_projects_tab(client):
    profile = ProfileFactory(is_linked=True)
    client.force_login(profile.user)

    resp = client.get('/lab/?view=projects')

    assert resp.status_code == 200
    assert b'is-active" data-view="projects"' in resp.content


def test_research_panel_url_redirects_into_lab_projects(client):
    resp = client.get('/research-panel/')
    assert resp.status_code == 301
    assert resp['Location'] == '/lab/?view=projects'


def test_lab_is_login_gated(client):
    # Anonymous -> LoginRequiredMixin bounces to login (the whole merged surface is personal).
    resp = client.get('/lab/')
    assert resp.status_code == 302
