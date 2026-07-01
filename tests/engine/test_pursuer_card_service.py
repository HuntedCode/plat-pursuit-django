"""Tests for pursuer_card_service.build_pursuer_card (the identity signature data).

Pins: identity from the Lab hero, the rarest-platinum showcase (ordered by global earn-rate,
rarest first) with cover fields, and the hero pass-through that avoids a second Lab build.
"""
import pytest

from trophies.services import pursuer_card_service
from tests.factories import ProfileFactory, TrophyFactory, EarnedTrophyFactory

pytestmark = pytest.mark.django_db


def test_fresh_profile_has_identity_and_empty_showcase():
    card = pursuer_card_service.build_pursuer_card(ProfileFactory())

    assert card['rank']['key'] == 'newbie'      # a fresh account floors to Newbie
    assert card['showcase'] == {'rarest': [], 'recent': []} and card['rarest_pct'] is None
    assert card['platinums'] == 0
    assert len(card['families']) == 5           # the 5 disciplines (from the DNA-ring data)


def test_showcase_is_rarest_first():
    profile = ProfileFactory()
    for rate in (30.0, 1.2, 8.5):               # earn rates: lower = rarer
        EarnedTrophyFactory(profile=profile,
                            trophy=TrophyFactory(trophy_type='platinum', trophy_earn_rate=rate))

    card = pursuer_card_service.build_pursuer_card(profile)

    rarest = card['showcase']['rarest']
    assert [s['earn_rate'] for s in rarest] == [1.2, 8.5, 30.0]   # rarest first
    assert card['rarest_pct'] == 1.2
    assert all({'game_name', 'cover_url', 'earn_rate', 'np_communication_id', 'elements'} <= set(s)
               for s in rarest)
    assert rarest[0]['elements'] == []          # these games aren't in a Contract


def test_recent_renders_one_extra_for_the_slot_in_shift():
    """Recent renders showcase_limit + 1: the extra (oldest shown) is the outgoing cover the
    forge's slot-in shift slides off the end as a new platinum enters. Rarest stays at the limit."""
    profile = ProfileFactory()
    for rate in (5.0, 6.0, 7.0, 8.0, 9.0, 10.0, 11.0):      # 7 platinums, more than the limit
        EarnedTrophyFactory(profile=profile,
                            trophy=TrophyFactory(trophy_type='platinum', trophy_earn_rate=rate))

    card = pursuer_card_service.build_pursuer_card(profile)  # default showcase_limit=5

    assert len(card['showcase']['recent']) == 6             # 5 shown + 1 outgoing
    assert len(card['showcase']['rarest']) == 5             # no extra needed


def test_lab_ctx_passthrough_is_used_verbatim():
    """Passing a pre-built Lab context avoids a second Lab build (the Home already has one)."""
    fake = {'hero': {'pursuer_name': 'Nightfall', 'avatar_url': None, 'pursuer_level': 999,
                     'pursuer_rank': {'key': 'ascendant', 'label': 'Ascendant'}, 'active_title': 'Sovereign'},
            'lab': None}

    card = pursuer_card_service.build_pursuer_card(ProfileFactory(), lab_ctx=fake)

    assert card['name'] == 'Nightfall' and card['level'] == 999
    assert card['rank']['key'] == 'ascendant' and card['active_title'] == 'Sovereign'
    assert card['families'] == []               # the passed hero carries no ring


def test_no_identity_returns_none():
    """A degraded Lab build (no usable hero/rank) yields no card so the surface hides it."""
    assert pursuer_card_service.build_pursuer_card(ProfileFactory(), lab_ctx={}) is None


def test_families_bar_pct_scales_to_strongest():
    """Each discipline's bar fills relative to the strongest family (strongest = 100%), so the
    band reads as a composition; a zero-level family floors to an empty bar without dividing by
    zero."""
    fake = {'hero': {'pursuer_rank': {'key': 'warden', 'label': 'Warden'}, 'pursuer_name': 'N',
                     'ring': [{'label': 'Combat', 'slug': 'combat', 'avg': 40},
                              {'label': 'Mind', 'slug': 'mind', 'avg': 10},
                              {'label': 'Heart', 'slug': 'heart', 'avg': 0}]},
            'lab': None}

    card = pursuer_card_service.build_pursuer_card(ProfileFactory(), lab_ctx=fake)

    assert {f['slug']: f['bar_pct'] for f in card['families']} == {'combat': 100, 'mind': 25, 'heart': 0}


def test_card_partial_applies_rank_chrome_class():
    """The component partial renders and stamps the rank key as the chrome class -- the hook the
    escalating rank styling targets (matte at the floor, radiant cyan at the apex)."""
    from django.template.loader import render_to_string
    base = {'name': 'Nightfall', 'avatar_url': None, 'level': 120, 'active_title': 'The X',
            'platinums': 287, 'avg_completion': 94.2, 'total_trophies': 18402, 'rarest_pct': 0.8,
            'families': [{'label': 'Combat', 'slug': 'combat', 'avg': 48, 'bar_pct': 100}],
            'showcase': {'rarest': [], 'recent': []}}
    for key in ('newbie', 'warden', 'ascendant'):
        html = render_to_string('partials/components/_pursuer_card.html',
                                {'card': {**base, 'rank': {'key': key, 'label': key.title()}}})
        assert f'pursuer-card--{key}' in html
