"""A hunter's OWN run trails back to My Challenges (owner, 2026-10-10); everybody else's keeps the public trail.

For the owner a run page is a working surface they swap to and from, so the breadcrumb, the hub rail and a back
button all point at My Challenges. A visitor keeps Home / Challenges and the Community rail: My Pursuit and My
Challenges are login-gated, and a visitor sent there would land on their own runs instead.
"""
import pytest
from django.test import RequestFactory
from django.urls import reverse

from challenges.models import CHALLENGE_TYPE_AZ
from challenges.services import challenge_service as svc
from core.hub_subnav import resolve_hub_subnav
from tests.factories import ProfileFactory, UserFactory

pytestmark = pytest.mark.django_db

BACK = 'Back to My Challenges'


def _run():
    user = UserFactory()
    profile = ProfileFactory(user=user, user_is_premium=True, is_linked=True)
    return user, svc.start(profile, CHALLENGE_TYPE_AZ)


def _crumbs(resp):
    return [c['text'] for c in resp.context['breadcrumb']]


def test_the_owner_trails_back_to_my_challenges(client):
    user, run = _run()
    client.force_login(user)
    resp = client.get(reverse('challenge_detail', args=[run.pk]))

    assert _crumbs(resp) == ['Home', 'My Pursuit', 'My Challenges', run.name]
    assert str(resp.context['breadcrumb'][2]['url']) == reverse('my_challenges')
    assert resp.context['hub_section'] == 'my_pursuit'
    assert resp.context['hub_subnav_active_slug'] == 'my_challenges'
    html = resp.content.decode()
    button = html[html.index(BACK) - 400:html.index(BACK)]
    assert 'href="%s"' % reverse('my_challenges') in button


def test_a_visitor_keeps_the_public_trail_and_gets_no_button(client):
    _, run = _run()
    visitor, _ = _run()
    client.force_login(visitor)
    resp = client.get(reverse('challenge_detail', args=[run.pk]))

    assert _crumbs(resp) == ['Home', 'Challenges', run.name]
    assert resp.context['hub_section'] == 'community'
    assert resp.context['hub_subnav_active_slug'] == 'challenges'
    assert BACK not in resp.content.decode()


def test_a_signed_out_reader_keeps_the_public_trail_too(client):
    _, run = _run()
    resp = client.get(reverse('challenge_detail', args=[run.pk]))
    assert _crumbs(resp) == ['Home', 'Challenges', run.name]
    assert resp.context['hub_section'] == 'community'
    assert BACK not in resp.content.decode()


def test_the_rail_override_wins_and_an_unknown_hub_falls_through():
    """A view's own answer beats the URL rules; a bad key must not blank the rail."""
    request = RequestFactory().get('/community/challenges/')
    request.hub_subnav_override = ('my_pursuit', 'my_challenges')
    assert resolve_hub_subnav(request)['hub'].key == 'my_pursuit'

    request.hub_subnav_override = ('no-such-hub', 'x')
    assert resolve_hub_subnav(request)['hub'].key == 'community'
