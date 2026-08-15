"""Regression tests for the anonymous profile-render cost gate.

Background: on 2026-08-09 a crawler enumerating profile-scoped URLs took the site
down. `/community/profiles/<username>/` was the first domino and the only expensive
enumerable page with no guard of any kind -- it is the canonical page, so it had
nothing to redirect to and fell through the bot-redirect rules that protect
/games/<np>/<user>/ and /badges/<slug>/<user>/.

Two things were unbounded on that page. The Rarest Trophies showcase sorted the
profile's ENTIRE earned set on a joined column; it was removed outright (migration 0275)
rather than gated, because its cost came from "rank everything I own" and not from who
was looking. The timeline was gated instead: cheap per call, but cached per profile, so
a crawler enumerating distinct profiles had a 0% hit rate by construction.

**Both providers are gone from this page as of 2026-08.** The timeline was deleted
outright -- it had rendered nowhere since the header rebuild dropped its include, while
still being built and discarded on every authenticated render of every tab. The
showcases are hidden pending a rebuild of profile customization (see
`test_showcases_hidden.py`, which owns their half of these assertions now). So the page
is strictly cheaper than the version that survived the outage, and the gate that was
added for the timeline has nothing left to gate.

What survives here is the part that is still load-bearing: the deleted provider must
stay deleted, the header stats must stay ungated, and neither retired provider may
quietly come back without a deliberate decision. The style is unchanged and is the point
of the file -- assertions are on the CALL, not on the context value. A version that
renders the data and then hides it in the template passes a "context is empty" check and
still takes the site down.
"""
import pytest
from django.contrib.auth.models import AnonymousUser
from django.test import RequestFactory

from tests.factories import ProfileFactory, UserFactory
from trophies.views.profile_views import ProfileDetailView

pytestmark = pytest.mark.django_db


def _build_context(profile, user, monkeypatch):
    """Run ProfileDetailView.get_context_data as `user`, recording any expensive call that runs.

    Returns (context, calls). Goes through the view rather than the test client so the
    assertions are about the data layer alone and cannot pass by accident on a template
    that hides its output.

    The showcase provider is still monkeypatched even though the view no longer calls it:
    that is what makes "it does not run" an assertion rather than an absence.
    """
    calls = []

    from trophies.services.showcase_service import ProfileShowcaseService

    monkeypatch.setattr(
        ProfileShowcaseService,
        'get_rendered_showcases',
        staticmethod(lambda p: calls.append('showcases') or []),
    )

    request = RequestFactory().get(f'/community/profiles/{profile.psn_username}/')
    request.user = user
    request.htmx = False

    view = ProfileDetailView()
    view.request = request
    view.object = profile
    view.kwargs = {'psn_username': profile.psn_username}

    return view.get_context_data(object=profile), calls


@pytest.mark.parametrize('anonymous', [True, False])
def test_neither_retired_provider_runs_for_any_viewer(monkeypatch, anonymous):
    """The showcase band is hidden and the timeline is deleted, so the two providers this file was
    written to gate now cost nothing for anybody.

    Parametrized over both viewers deliberately. The showcase provider was never auth-gated -- it ran
    for anonymous visitors on purpose, because a shared profile link is mostly opened logged-out -- so
    checking only the anonymous case would prove nothing about it."""
    profile = ProfileFactory(psn_history_public=True)
    viewer = AnonymousUser() if anonymous else UserFactory()

    context, calls = _build_context(profile, viewer, monkeypatch)

    assert calls == [], f'a retired provider still runs: {calls}'
    assert not context.get('rendered_showcases')
    assert not context.get('timeline_events')


def test_the_timeline_is_gone_not_merely_unrendered():
    """It had already been unrendered for a while -- the header rebuild dropped its include and nobody
    noticed, so the view went on building it and throwing it away on every authenticated render of
    every tab, including HTMX swaps. Deleting the template alone would recreate exactly that state."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]

    assert not (root / 'trophies' / 'services' / 'timeline_service.py').exists()
    assert not (root / 'templates' / 'trophies' / 'partials' / 'profile_detail'
                / 'profile_timeline.html').exists()
    assert not hasattr(ProfileDetailView, '_build_timeline'), (
        'the view can build a timeline again'
    )

    # And the sync pipeline no longer invalidates a cache that no longer exists.
    keeper = (root / 'trophies' / 'token_keeper.py').read_text(encoding='utf-8')
    assert 'invalidate_timeline_cache' not in keeper


def test_the_game_detail_timeline_was_not_caught_in_the_crossfire():
    """`game_views._build_timeline_events` and its `timeline_events` context key are a DIFFERENT
    feature -- the game-detail My Stats journey (Started -> First -> 25/50/75% -> Platinum) -- that
    happens to share the name. Two surfaces, one variable name, and only one of them was retired."""
    from trophies.views.game_views import GameDetailView

    assert hasattr(GameDetailView, '_build_timeline_events')


def test_rarest_trophies_showcase_type_is_gone():
    """The unbounded provider must stay deleted, not merely unregistered. It ranked the
    profile's whole earned set on a joined column; re-adding it re-adds the outage.

    Still load-bearing with the showcases hidden: hiding parked the SURFACE, and a rebuild that reads
    this registry must not find the type waiting in it."""
    from trophies.models import ProfileShowcase
    from trophies.services import showcase_service

    assert not hasattr(ProfileShowcase, 'SHOWCASE_RAREST')
    assert 'rarest_trophies' not in dict(ProfileShowcase.SHOWCASE_TYPES)
    assert 'rarest_trophies' not in showcase_service.SHOWCASE_REGISTRY
    assert not hasattr(showcase_service, 'provide_rarest_trophies')


def test_anonymous_render_keeps_header_stats(monkeypatch):
    """The four Platinum Highlight cards are deliberately NOT gated: they render a "None"
    empty state, so hiding them would misreport the profile to logged-out visitors."""
    profile = ProfileFactory(psn_history_public=True)

    context, _ = _build_context(profile, AnonymousUser(), monkeypatch)

    header = context['header_stats']
    for key in ('recent_platinum', 'rarest_platinum', 'fastest_platinum', 'milestone_platinum'):
        assert key in header, f'{key} must stay in the anonymous header'
