"""Error handlers.

Regression: the custom handler404 was a plain TemplateView, which renders 404.html at HTTP 200 --
so every not-found silently returned 200 (and e.g. a lazy fetch would inject the 404 page). It must
return a real 404 status.
"""
import pytest

pytestmark = pytest.mark.django_db


def test_unknown_url_returns_404_status(client):
    resp = client.get('/definitely-not-a-real-page-zzz9/')
    assert resp.status_code == 404
