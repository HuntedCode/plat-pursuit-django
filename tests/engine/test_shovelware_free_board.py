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
from django.core.management import call_command

from trophies.models import SHOVELWARE_FLAGGED_STATUSES, Game, ProfileTrophyStanding
from tests.factories import EarnedTrophyFactory, GameFactory, ProfileFactory, TrophyFactory

pytestmark = pytest.mark.django_db


def _earn(profile, game, tier, n=1):
    for _ in range(n):
        EarnedTrophyFactory(profile=profile, trophy=TrophyFactory(game=game, trophy_type=tier),
                            earned=True)


def _standing(profile):
    """Recompute just this profile and hand back the row it wrote."""
    call_command('recompute_clean_standings', '--profile-ids', str(profile.id))
    return ProfileTrophyStanding.objects.get(profile=profile)


#: The statuses that are deliberately NOT disqualifying. Stated here as a second, independent list so the
#: completeness check below is a real cross-check rather than a restatement of the constant.
NOT_FLAGGED = ('clean', 'manually_cleared')


def test_manually_cleared_counts_as_clean():
    """The half of this rule that is easy to get wrong and invisible once it is: the figure is merely
    lower than it should be.

    `manually_cleared` means a human looked at a flagged game and passed it, where `clean` is the
    never-examined default -- so on THIS board a vouched-for game counts. The community trophy tracker
    deliberately takes the narrower `status == 'clean'` reading (see
    docs/features/community-trophy-tracker.md); that is a second POLICY, not a bug, and this test pins
    only what the board does.
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


# ---------------------------------------------------------------- the nightly recompute -----------------

def test_flagged_games_are_excluded_and_cleared_ones_are_not():
    """The whole feature in one assertion. `manually_cleared` counting as clean is the half that is easy
    to get wrong and impossible to see once it is: the figure is merely lower than it should be."""
    profile = ProfileFactory(is_linked=True)
    _earn(profile, GameFactory(shovelware_status='clean'), 'platinum')
    _earn(profile, GameFactory(shovelware_status='manually_cleared'), 'platinum')
    _earn(profile, GameFactory(shovelware_status='auto_flagged'), 'platinum')
    _earn(profile, GameFactory(shovelware_status='manually_flagged'), 'platinum')

    assert _standing(profile).clean_plats == 2, (
        'expected the clean and the manually-cleared platinum, and neither flagged one'
    )


def test_the_tier_breakdown_is_filtered_too():
    """A row pairing filtered platinums with an unfiltered bronze/silver/gold split would describe two
    different libraries on one line."""
    profile = ProfileFactory(is_linked=True)
    clean, junk = GameFactory(shovelware_status='clean'), GameFactory(shovelware_status='auto_flagged')
    _earn(profile, clean, 'bronze', 3)
    _earn(profile, clean, 'silver', 2)
    _earn(profile, clean, 'gold', 1)
    _earn(profile, junk, 'bronze', 50)
    _earn(profile, junk, 'gold', 9)

    row = _standing(profile)
    assert (row.clean_bronzes, row.clean_silvers, row.clean_golds) == (3, 2, 1)
    assert row.clean_trophies == 6, 'clean_trophies must be the sum of the tiers it reports'


def test_unearned_trophies_do_not_count():
    """`EarnedTrophy` rows exist for trophies a hunter has NOT earned -- that is how progress is tracked.
    Counting them would rank hunters by how many games they had opened."""
    profile = ProfileFactory(is_linked=True)
    game = GameFactory(shovelware_status='clean')
    _earn(profile, game, 'platinum')
    EarnedTrophyFactory(profile=profile, trophy=TrophyFactory(game=game, trophy_type='gold'),
                        earned=False)

    row = _standing(profile)
    assert row.clean_plats == 1 and row.clean_trophies == 1


def test_a_wholly_flagged_library_gets_a_ZEROED_row_not_no_row():
    """The row has to exist, because its absence and its zero mean different things to `clean_rank`: no
    row is "never computed", zero is "computed, and they are not on this board"."""
    profile = ProfileFactory(is_linked=True)
    _earn(profile, GameFactory(shovelware_status='auto_flagged'), 'platinum', 4)

    row = _standing(profile)
    assert row.clean_plats == 0 and row.clean_trophies == 0


def test_the_recompute_is_idempotent_and_self_healing():
    """Recompute-from-scratch is what makes materializing this safe: re-running must not double, and a row
    corrupted by anything else must be CORRECTED rather than added to. Same property, viewed twice."""
    profile = ProfileFactory(is_linked=True)
    _earn(profile, GameFactory(shovelware_status='clean'), 'platinum', 2)

    assert _standing(profile).clean_plats == 2
    assert _standing(profile).clean_plats == 2, 'a second run doubled the figures'

    ProfileTrophyStanding.objects.filter(profile=profile).update(clean_plats=999, clean_trophies=999)
    assert _standing(profile).clean_plats == 2, 'a drifted row was not corrected'


def test_updated_at_advances_when_the_figures_do():
    """REGRESSION. `updated_at` is `auto_now`, which Django applies inside `Model.save()` -- and
    `bulk_update` does not call it. So the timestamp sat frozen at row creation while the counts beside it
    moved every night: the one field whose whole job is answering "how stale is this board" was the one
    field guaranteed to be wrong, and wrong in the reassuring direction.
    """
    profile = ProfileFactory(is_linked=True)
    game = GameFactory(shovelware_status='clean')
    _earn(profile, game, 'platinum')
    first = _standing(profile).updated_at

    _earn(profile, game, 'gold')
    row = _standing(profile)

    assert row.clean_trophies == 2, 'the fixture did not actually change the figures'
    assert row.updated_at > first, 'the figures moved but the freshness stamp did not'


def test_an_unchanged_row_is_not_rewritten():
    """Change detection, and it is not just an optimisation. The nightly sweep touches every linked
    hunter, but a hunter earns nothing on most nights -- so writing all of them would churn the whole
    table for no reason, and would make `updated_at` mean "the recompute ran" rather than "this hunter's
    figures changed"."""
    profile = ProfileFactory(is_linked=True)
    _earn(profile, GameFactory(shovelware_status='clean'), 'platinum')
    first = _standing(profile).updated_at

    row = _standing(profile)      # nothing earned in between
    assert row.updated_at == first, 'an unchanged row was rewritten anyway'


def test_the_recompute_stamps_the_mirrors_at_birth():
    """Every recompute seam stamps `country_code` and `is_linked` on the rows it writes; the propagation
    signal only covers the edges BETWEEN runs. A row born without them is a hunter missing from their own
    country's board until something else happens to touch them."""
    profile = ProfileFactory(is_linked=True, country_code='GB')
    _earn(profile, GameFactory(shovelware_status='clean'), 'platinum')

    row = _standing(profile)
    assert row.country_code == 'GB' and row.is_linked is True


def test_a_reflagged_game_moves_every_hunter_who_played_it():
    """THE reason this store has no incremental path. Shovelware is a property of the GAME, so flagging
    one invalidates the standing of everybody who ever earned a trophy on it -- a fan-out no per-row
    signal can see. Recompute-from-scratch is what makes that a non-event."""
    game = GameFactory(shovelware_status='clean')
    hunters = [ProfileFactory(is_linked=True) for _ in range(3)]
    for h in hunters:
        _earn(h, game, 'platinum')
        assert _standing(h).clean_plats == 1

    game.shovelware_status = 'auto_flagged'
    game.save(update_fields=['shovelware_status'])
    call_command('recompute_clean_standings')

    for h in hunters:
        assert ProfileTrophyStanding.objects.get(profile=h).clean_plats == 0, (
            'a re-flagged game left stale standings behind'
        )


def test_dry_run_writes_nothing():
    profile = ProfileFactory(is_linked=True)
    _earn(profile, GameFactory(shovelware_status='clean'), 'platinum')

    call_command('recompute_clean_standings', '--dry-run')
    assert not ProfileTrophyStanding.objects.filter(profile=profile).exists()


def test_an_unlinked_profile_with_no_row_is_not_swept_in():
    """The population is linked hunters plus anyone who already has a row. This table serves ONE board and
    nothing else, so a row for an unlinked profile would be computed, stored and never read -- roughly
    250,000 of them at current scale."""
    unlinked = ProfileFactory(is_linked=False)
    _earn(unlinked, GameFactory(shovelware_status='clean'), 'platinum')

    call_command('recompute_clean_standings')
    assert not ProfileTrophyStanding.objects.filter(profile=unlinked).exists()


def test_a_hunter_who_unlinks_keeps_being_corrected():
    """The other half of that population rule, and why it is not linked-only. A hunter who unlinks still
    has a row, and a linked-only sweep would never revisit it -- so their figures would sit there frozen
    at whatever they were the night they left."""
    profile = ProfileFactory(is_linked=True)
    _earn(profile, GameFactory(shovelware_status='clean'), 'platinum')
    assert _standing(profile).is_linked is True

    profile.is_linked = False
    profile.save(update_fields=['is_linked'])
    call_command('recompute_clean_standings')

    assert ProfileTrophyStanding.objects.get(profile=profile).is_linked is False
