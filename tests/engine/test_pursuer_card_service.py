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


def test_hero_passthrough_is_used_verbatim():
    """Passing a pre-built Lab hero avoids a second Lab build (the Home already has one)."""
    fake_hero = {'pursuer_name': 'Nightfall', 'avatar_url': None, 'pursuer_level': 999,
                 'pursuer_rank': {'key': 'ascendant', 'label': 'Ascendant'}, 'active_title': 'Sovereign'}

    card = pursuer_card_service.build_pursuer_card(ProfileFactory(), hero=fake_hero)

    assert card['name'] == 'Nightfall' and card['level'] == 999
    assert card['rank']['key'] == 'ascendant' and card['active_title'] == 'Sovereign'
