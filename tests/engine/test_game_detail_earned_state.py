"""_build_earned_state: the viewer's per-list earned map + DB-side totals, extracted so the
concept Game page's list viewport can price a list switch at exactly this cost.

The query-count guard is the point of this file. The earned map reads e.trophy.trophy_id per row
and the timeline reads e.trophy.trophy_type per row; without select_related('trophy') that was one
query PER EARNED TROPHY -- tolerable once on the old page, multiplied by every list a switcher
fetches on the new one.
"""
import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from tests.factories import EarnedTrophyFactory, GameFactory, ProfileFactory, TrophyFactory
from trophies.views.game_views import GameDetailView

pytestmark = pytest.mark.django_db


def _game_with_earned(n_trophies=8, n_earned=5):
    game = GameFactory()
    profile = ProfileFactory()
    trophies = [
        TrophyFactory(game=game, trophy_id=i, trophy_group_id='default' if i % 2 else '001',
                      trophy_type='bronze' if i else 'platinum')
        for i in range(n_trophies)
    ]
    for t in trophies[:n_earned]:
        EarnedTrophyFactory(profile=profile, trophy=t)
    return game, profile


def test_earned_state_query_count_does_not_scale_with_earned_trophies():
    """Three queries total (map, aggregate, group rows) regardless of how many trophies are
    earned. The mutation that matters: drop the select_related and this becomes 3 + N."""
    small_game, small_profile = _game_with_earned(n_trophies=4, n_earned=2)
    big_game, big_profile = _game_with_earned(n_trophies=12, n_earned=10)
    view = GameDetailView()

    def count(game, profile):
        with CaptureQueriesContext(connection) as ctx:
            view._build_earned_state(game, profile)
        return len(ctx)

    small, big = count(small_game, small_profile), count(big_game, big_profile)

    assert small == big == 3, (
        f'earned-state cost must not scale with earned trophies: {small} vs {big}'
    )


def test_earned_state_shape_matches_the_template_contract():
    """The three context keys the trophy grid + hero read, in the shapes they read them."""
    game, profile = _game_with_earned(n_trophies=4, n_earned=3)
    view = GameDetailView()

    state, ordered_qs, earned_count = view._build_earned_state(game, profile)

    assert set(state) == {'profile_earned', 'profile_trophy_totals', 'profile_group_totals'}
    assert earned_count == 3
    assert set(state['profile_earned']) == {0, 1, 2}
    assert state['profile_earned'][1]['earned'] is True
    assert state['profile_trophy_totals']['platinum'] == 1  # trophy_id 0 is the platinum
    assert state['profile_trophy_totals']['bronze'] == 2
    # Group totals keyed by group id with the four-type buckets. Fixture layout: i%2 puts
    # trophies 0 (the platinum) and 2 in group '001', trophy 1 in 'default'.
    assert state['profile_group_totals']['default']['bronze'] == 1
    assert state['profile_group_totals']['001'] == {'bronze': 1, 'silver': 0, 'gold': 0, 'platinum': 1}
    # The ordered queryset carries the select_related -- the timeline iterates trophy fields off it.
    with CaptureQueriesContext(connection) as ctx:
        types = [e.trophy.trophy_type for e in ordered_qs]
    assert len(types) == 3
    assert len(ctx) == 1, 'iterating the ordered queryset must not N+1 on trophy'
