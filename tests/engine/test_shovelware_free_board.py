"""The Shovelware Free board -- the fourth Global Board (2026-09).

Linked hunters ranked by platinums earned on games that are NOT flagged as shovelware. It is a board
rather than a toggle on the Trophies board because that board ranks `Profile.total_plats` /
`total_trophies` -- denormalized integer columns, not a queryset -- so excluding shovelware cannot be a
filter, only a different number. That number is materialized in `ProfileTrophyStanding` and written by a
single nightly seam.

The rank/position invariant and the membership rule live with the boards that share them, in
`test_rank_equals_position.py`. What is here is the rule that defines the population in the first place:
which `shovelware_status` values disqualify a game.

See docs/design/rebuild/leaderboards-rebuild.md.
"""
import pytest

from trophies.models import SHOVELWARE_FLAGGED_STATUSES, Game
from tests.factories import GameFactory

pytestmark = pytest.mark.django_db


#: The statuses that are deliberately NOT disqualifying. Stated here as a second, independent list so the
#: completeness check below is a real cross-check rather than a restatement of the constant.
NOT_FLAGGED = ('clean', 'manually_cleared')


def test_manually_cleared_counts_as_clean():
    """THE trap this constant exists for, and the one a third spelling of the rule already fell into.

    `manually_cleared` means a human looked at a flagged game and said it was fine. `clean` is merely the
    never-examined default. So writing the rule as `shovelware_status == 'clean'` -- which
    `core/services/community_trophy_tracker.py` does -- excludes precisely the games somebody took the
    trouble to vouch for, while admitting every game nobody has ever looked at. Backwards, and silent.
    """
    cleared = GameFactory(shovelware_status='manually_cleared')
    assert cleared.is_shovelware is False
    assert 'manually_cleared' not in SHOVELWARE_FLAGGED_STATUSES


@pytest.mark.parametrize('status,flagged', [
    ('clean', False),
    ('manually_cleared', False),
    ('auto_flagged', True),
    ('manually_flagged', True),
])
def test_the_property_and_the_constant_classify_every_status_the_same_way(status, flagged):
    """`Game.is_shovelware` answers for ONE game; the constant is what querysets filter a whole population
    on. They are the same rule and must not be able to disagree -- a game the property calls clean while
    the board's aggregate counts it as shovelware is a hunter whose figure nobody can reproduce.
    """
    game = GameFactory(shovelware_status=status)
    assert game.is_shovelware is flagged
    assert (status in SHOVELWARE_FLAGGED_STATUSES) is flagged


def test_every_declared_status_is_deliberately_classified():
    """A fifth status must not be able to arrive UNCLASSIFIED.

    Adding one to the field's choices and forgetting this rule does not error: the new status simply is
    not in `SHOVELWARE_FLAGGED_STATUSES`, so it silently counts as clean and its games quietly join the
    board. Read off the FIELD's own choices, so the model is what has to be kept in step rather than a
    list in a test.
    """
    declared = {value for value, _label in Game._meta.get_field('shovelware_status').choices}
    classified = set(SHOVELWARE_FLAGGED_STATUSES) | set(NOT_FLAGGED)

    assert declared == classified, (
        f'these statuses are declared on Game but not classified as flagged or clean: '
        f'{sorted(declared - classified)} (and these are classified but no longer declared: '
        f'{sorted(classified - declared)})'
    )
