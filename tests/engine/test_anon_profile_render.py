"""Regression tests for the anonymous profile-render cost gate.

Background: on 2026-08-09 a crawler enumerating profile-scoped URLs took the site
down. `/community/profiles/<username>/` was the first domino and the only expensive
enumerable page with no guard of any kind -- it is the canonical page, so it had
nothing to redirect to and fell through the bot-redirect rules that protect
/games/<np>/<user>/ and /badges/<slug>/<user>/.

The two unbounded costs on that page are the showcase providers (the rarest-trophies
provider sorts the profile's ENTIRE earned set on a joined column) and the timeline
builder. Both are now skipped for anonymous visitors.

What these tests pin is the thing that actually regressed in production: the gate is
checked BEFORE the provider is invoked, not around its output. A version that renders
the data and then hides it in the template passes a "context is empty" assertion and
still takes the site down, so every test here asserts on the CALL, not the value.
"""
import pytest
from django.contrib.auth.models import AnonymousUser
from django.test import RequestFactory

from tests.factories import ProfileFactory, UserFactory
from trophies.views.profile_views import ProfileDetailView

pytestmark = pytest.mark.django_db


def _build_context(profile, user, monkeypatch):
    """Run ProfileDetailView.get_context_data as `user`, recording the two expensive calls.

    Returns (context, calls) where `calls` is a list of the provider names that ran.
    Goes through the view rather than the test client so the assertion is about the
    data layer alone and cannot pass by accident on a template that hides its output.
    """
    calls = []

    from trophies.services.showcase_service import ProfileShowcaseService

    monkeypatch.setattr(
        ProfileShowcaseService,
        'get_rendered_showcases',
        staticmethod(lambda p: calls.append('showcases') or []),
    )
    monkeypatch.setattr(
        ProfileDetailView,
        '_build_timeline',
        lambda self, p: calls.append('timeline') or [],
    )

    request = RequestFactory().get(f'/community/profiles/{profile.psn_username}/')
    request.user = user
    request.htmx = False

    view = ProfileDetailView()
    view.request = request
    view.object = profile
    view.kwargs = {'psn_username': profile.psn_username}

    return view.get_context_data(object=profile), calls


def test_anonymous_render_does_not_run_showcase_providers(monkeypatch):
    """The whale killer. An anon visitor must not cause a single showcase provider to run."""
    profile = ProfileFactory(psn_history_public=True)

    context, calls = _build_context(profile, AnonymousUser(), monkeypatch)

    assert 'showcases' not in calls
    assert context['rendered_showcases'] == []


def test_anonymous_render_does_not_build_timeline(monkeypatch):
    """The timeline is cached per profile, so a crawler enumerating profiles never hits
    that cache. Gating is the only thing that protects it."""
    profile = ProfileFactory(psn_history_public=True)

    context, calls = _build_context(profile, AnonymousUser(), monkeypatch)

    assert 'timeline' not in calls
    assert not context.get('timeline_events')


def test_authenticated_render_still_runs_both(monkeypatch):
    """The gate keys on the VIEWER, not the profile: a logged-in visitor still gets the
    full page. Without this, the fix would silently delete the feature for everyone."""
    profile = ProfileFactory(psn_history_public=True)
    viewer = UserFactory()

    context, calls = _build_context(profile, viewer, monkeypatch)

    assert 'showcases' in calls
    assert 'timeline' in calls


def test_private_history_still_skips_timeline_when_authenticated(monkeypatch):
    """psn_history_public=False must keep winning for logged-in viewers. The anon gate is
    an ADDITIONAL condition, not a replacement for the privacy one."""
    profile = ProfileFactory(psn_history_public=False)
    viewer = UserFactory()

    _, calls = _build_context(profile, viewer, monkeypatch)

    assert 'showcases' in calls      # showcases are not privacy-gated
    assert 'timeline' not in calls   # ...the timeline is


def test_anonymous_render_keeps_header_stats(monkeypatch):
    """The four Platinum Highlight cards are deliberately NOT gated: they render a "None"
    empty state, so hiding them would misreport the profile to logged-out visitors."""
    profile = ProfileFactory(psn_history_public=True)

    context, _ = _build_context(profile, AnonymousUser(), monkeypatch)

    header = context['header_stats']
    for key in ('recent_platinum', 'rarest_platinum', 'fastest_platinum', 'milestone_platinum'):
        assert key in header, f'{key} must stay in the anonymous header'
