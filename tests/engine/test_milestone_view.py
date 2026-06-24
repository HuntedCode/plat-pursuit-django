"""Milestones page render smoke tests.

The rebuilt page mounts the Tally + Horizon primitives and a stack of includes/filters; these
confirm it renders (no template error) for a guest, an authed user (overview), and a category
tab (the tier ladder + the active tier's band-tone Horizon), and that the primitives appear.
"""
import pytest
from django.urls import reverse

from trophies.models import Milestone, UserMilestone
from tests.factories import ProfileFactory

pytestmark = pytest.mark.django_db


def _plat_tiers():
    return [
        Milestone.objects.create(
            name=f'{n} Platinums', description='Earn platinum trophies.',
            criteria_type='plat_count', required_value=n, criteria_details={'target': n},
        )
        for n in (10, 50, 100)
    ]


def test_overview_renders_for_guest(client):
    _plat_tiers()
    resp = client.get(reverse('milestones_list'))
    assert resp.status_code == 200
    html = resp.content.decode()
    assert 'ms-tabs' in html and 'Milestones' in html


def test_overview_renders_for_authed_user_with_primitives(client):
    profile = ProfileFactory()
    tiers = _plat_tiers()
    UserMilestone.objects.create(profile=profile, milestone=tiers[0])
    client.force_login(profile.user)

    resp = client.get(reverse('milestones_list'))
    assert resp.status_code == 200
    html = resp.content.decode()
    assert 'ms-stats' in html        # overview stat cards
    assert 'pp-tally' in html        # Tally numbers mounted
    assert 'pp-horizon' in html      # overall Horizon mounted


def test_category_tab_renders_tier_ladder_with_band_horizon(client):
    profile = ProfileFactory()
    tiers = _plat_tiers()
    UserMilestone.objects.create(profile=profile, milestone=tiers[0])  # tier 1 earned -> tier 2 active
    client.force_login(profile.user)

    resp = client.get(reverse('milestones_list') + '?cat=trophy_hunting')
    assert resp.status_code == 200
    html = resp.content.decode()
    assert 'ms-ladder' in html
    assert 'ms-tier is-earned' in html       # the earned tier
    assert 'ms-tier is-active' in html       # the next, in-progress tier
    assert 'data-horizon-band' in html       # the active tier's band-tone Horizon
