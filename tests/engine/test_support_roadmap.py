"""/support/roadmap/ -- the site's own platinum roadmap -- and the storefront's roadmap band.

The content rules are the tests that matter: future stages carry no dates, counts or
percentages (order is the only promise), the heartbeat figures vanish wholesale when cold, and
the Horizon strip always pairs with real stage progress, never decoration.
"""
import re

import pytest
from django.urls import reverse

from users.constants import ROADMAP_STAGES

pytestmark = pytest.mark.django_db


def _flat(client, url_name='support_roadmap'):
    body = client.get(reverse(url_name)).content.decode()
    return ' '.join(body.split())


# ------------------------------------------------------------------------- the skeleton ----

def test_the_stage_skeleton_is_well_formed():
    """Both surfaces walk this constant blind; a malformed stage breaks them together."""
    statuses = [st['status'] for st in ROADMAP_STAGES]

    assert statuses.count('now') == 1, 'exactly one stage is where we are'
    assert all(s in ('shipped', 'now', 'next', 'later') for s in statuses)
    # The trail is ordered: everything shipped precedes now, which precedes everything ahead.
    assert statuses == sorted(statuses, key=('shipped', 'now', 'next', 'later').index)
    for stage in ROADMAP_STAGES:
        assert stage['key'] and stage['when'] and stage['title'] and stage['blurb']


def test_future_stages_promise_no_dates():
    """DELIBERATELY NO DATES on unshipped work: the moment one slips, the roadmap becomes a
    promise ledger. 'when' labels ahead of now must be relative words, never months/quarters."""
    ahead = [st for st in ROADMAP_STAGES if st['status'] in ('next', 'later')]
    months = ('january february march april may june july august september '
              'october november december').split()

    for stage in ahead:
        label = (stage['when'] + ' ' + stage['blurb']).lower()
        assert not re.search(r'\b(19|20)\d\d\b', label), f"{stage['key']} promises a year"
        assert not re.search(r'\bq[1-4]\b', label), f"{stage['key']} promises a quarter"
        assert not any(m in label for m in months), f"{stage['key']} promises a month"


# ------------------------------------------------------------------------- the page --------

def test_the_roadmap_page_renders_for_everyone(client):
    response = client.get(reverse('support_roadmap'))
    assert response.status_code == 200
    body = ' '.join(response.content.decode().split())
    assert 'The platinum roadmap for PlatPursuit itself.' in body
    assert 'It started with a Discord server.' in body
    assert 'just me!' in body


def test_the_horizon_strip_carries_real_stage_progress(client):
    """The primitive's own anti-pattern rule: never decorative, always a real fraction. One pip
    per stage; done pips = shipped stages; the current stage takes the active ring."""
    body = _flat(client)
    shipped = sum(1 for st in ROADMAP_STAGES if st['status'] == 'shipped')

    strip = body[body.index('rm__pips'):body.index('rm__progress-k')]
    assert strip.count('pp-horizon__seg') == len(ROADMAP_STAGES)
    assert strip.count('data-state="done"') == shipped
    assert strip.count('data-state="active"') == 1
    assert f'aria-valuenow="{shipped}"' in strip
    assert f'{shipped} of {len(ROADMAP_STAGES)} stages banked' in body


def test_the_numbers_stop_below_the_current_stage(client):
    """The markers go hollow and the numbers stop: no tally, count or percentage anywhere in the
    ahead sections. Order is the only promise."""
    body = client.get(reverse('support_roadmap')).content.decode()
    ahead_start = body.index('is-ahead')

    assert 'pp-tally' not in body[ahead_start:], 'a count leaked below the current stage'
    ahead_text = ' '.join(re.sub(r'<[^>]+>', ' ', body[ahead_start:]).split())
    assert not re.search(r'\d+%', ahead_text), 'a percentage leaked into the future'


def test_cold_heartbeat_omits_the_figures_wholesale(client):
    """'Tracking 0 trophies for 0 hunters' on the page asking you to come along is worse than
    saying nothing."""
    from unittest.mock import patch

    with patch('users.views.SupportStorefrontView._today', return_value=None):
        body = _flat(client)

    assert 'rm-step__figs' not in body
    assert 'trophies tracked' not in body


def test_the_ask_swaps_for_members(client):
    from unittest.mock import patch
    from tests.factories import UserFactory

    body = _flat(client)
    assert 'Come along for the ride' in body

    client.force_login(UserFactory())
    with patch('users.views.SubscriptionService.has_active_subscription',
               return_value=(True, 'stripe')):
        member_body = _flat(client)
    assert 'Come along for the ride' not in member_body
    assert 'You already are.' in member_body


def test_the_current_stage_breathing_is_reduced_motion_safe():
    """The 'we are here' pulse exists only under no-preference -- declared at the source, the
    pattern that cannot lose a cascade fight (the audit's PayPal-flow lesson)."""
    import pathlib
    css = pathlib.Path('static/css/components/support-roadmap.css').read_text(encoding='utf-8')
    css = re.sub(r'/\*.*?\*/', '', css, flags=re.S)

    here_at = css.index('animation: rmHere')
    assert 'prefers-reduced-motion: no-preference' in css[max(0, here_at - 400):here_at]
    entrance_at = css.index('animation: ppRevealIn')
    assert 'prefers-reduced-motion: no-preference' in css[max(0, entrance_at - 600):entrance_at]


# ------------------------------------------------------------------------- the band --------

def test_the_storefront_carries_the_roadmap_band(client):
    body = _flat(client, 'support_hub')

    assert 'data-sup-road' in body
    assert 'Where this is going' in body
    band = body[body.index('data-sup-road'):body.index('data-sup-paid')]
    assert reverse('support_roadmap') in band, 'the band does not link to the full roadmap'
    assert band.count('pp-horizon__seg') == len(ROADMAP_STAGES)


def test_the_band_names_only_the_stages_ahead(client):
    """The band sells what support BUILDS next; the serve band above already brags for the past.
    Shipped and current stages stay off it."""
    body = _flat(client, 'support_hub')
    band = body[body.index('data-sup-road'):body.index('data-sup-paid')]

    for stage in ROADMAP_STAGES:
        if stage['status'] in ('next', 'later'):
            assert stage['blurb'] in band, f"{stage['key']} missing from the band"
        else:
            assert stage['blurb'] not in band, f"{stage['key']} (not ahead) leaked into the band"


def test_the_band_sits_inside_the_pitch(client):
    """Placement is the decision: directly below the header, before the money bands -- part of
    the sell, not a footer afterthought."""
    body = _flat(client, 'support_hub')

    assert body.index('data-sup-road') < body.index('data-sup-paid')
