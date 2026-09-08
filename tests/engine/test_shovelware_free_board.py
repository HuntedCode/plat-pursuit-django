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

from trophies.models import SHOVELWARE_FLAGGED_STATUSES, EarnedTrophy, Game, ProfileTrophyStanding
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


@pytest.mark.parametrize('status,flagged', [
    ('clean', False),
    # The case that is easy to get wrong and invisible once it is (the figure is merely lower than it
    # should be): `manually_cleared` means a human looked at a flagged game and PASSED it, where `clean`
    # is the never-examined default. The community trophy tracker deliberately takes the narrower
    # `status == 'clean'` reading -- a second POLICY, not a bug; see
    # docs/features/community-trophy-tracker.md.
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


def test_hidden_games_are_excluded_for_a_hunter_who_hides_them():
    """`hide_hiddens` is honoured, the same courtesy `Profile.total_trophies` extends -- so the trophy
    figure beside a hunter's name means the same thing on both trophy boards.

    Every counter obeys it, not just the total: a row pairing a filtered platinum count with an unfiltered
    trophy total would describe two different libraries on one line.
    """
    profile = ProfileFactory(is_linked=True, hide_hiddens=True)
    game = GameFactory(shovelware_status='clean')
    _earn(profile, game, 'platinum')
    _earn(profile, game, 'bronze', 3)
    EarnedTrophy.objects.filter(profile=profile, trophy__trophy_type='bronze').update(user_hidden=True)

    row = _standing(profile)
    assert row.clean_bronzes == 0, 'a hidden game\'s trophies counted for a hunter who hides them'
    assert row.clean_plats == 1 and row.clean_trophies == 1


def test_hidden_games_still_count_for_a_hunter_who_does_not_hide_them():
    """The other direction, so the filter above cannot pass by dropping hidden trophies for everybody.
    `user_hidden` is set by sync on any game PSN stopped returning; it only MEANS "leave this out" for a
    hunter who asked."""
    profile = ProfileFactory(is_linked=True, hide_hiddens=False)
    game = GameFactory(shovelware_status='clean')
    _earn(profile, game, 'platinum')
    _earn(profile, game, 'bronze', 3)
    EarnedTrophy.objects.filter(profile=profile, trophy__trophy_type='bronze').update(user_hidden=True)

    row = _standing(profile)
    assert row.clean_bronzes == 3 and row.clean_trophies == 4


def test_hide_zeros_cannot_move_an_earned_count():
    """Documented, not merely omitted. `hide_zeros` drops games with ZERO earned trophies -- which
    contribute nothing to a count of EARNED trophies, so it cannot change any figure here.

    It cannot move `Profile.total_trophies` either, for the same reason: excluding rows that add 0 from a
    SUM leaves the SUM alone. On a profile it only changes `total_unearned` and the average derived from
    it. Pinned so nobody 'fixes' the omission by adding a filter that does nothing but cost a join.
    """
    game = GameFactory(shovelware_status='clean')
    off = ProfileFactory(is_linked=True, hide_zeros=False)
    on = ProfileFactory(is_linked=True, hide_zeros=True)
    for p in (off, on):
        _earn(p, game, 'platinum')
        # A game they have opened and earned nothing in -- exactly what hide_zeros is about.
        EarnedTrophyFactory(profile=p, trophy=TrophyFactory(game=game, trophy_type='gold'), earned=False)

    assert _standing(off).clean_trophies == _standing(on).clean_trophies == 1


def test_toggling_hide_hiddens_moves_the_figure_on_the_next_run():
    """The setting is read at RECOMPUTE time, so a hunter who changes it sees the board follow on the next
    nightly rather than immediately -- which is the same lag every other figure in this store has, and
    worth pinning so the delay is a known property rather than a bug report."""
    profile = ProfileFactory(is_linked=True, hide_hiddens=False)
    game = GameFactory(shovelware_status='clean')
    _earn(profile, game, 'gold', 2)
    EarnedTrophy.objects.filter(profile=profile).update(user_hidden=True)
    assert _standing(profile).clean_trophies == 2

    profile.hide_hiddens = True
    profile.save(update_fields=['hide_hiddens'])
    assert _standing(profile).clean_trophies == 0, 'the recompute did not pick the setting up'


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
    """The other half of the population rule, and why it is not linked-only. A hunter who unlinks still
    has a row, and a linked-only sweep would never revisit it -- so their FIGURES would sit there frozen
    at whatever they were the night they left.

    The assertion is on the figures, not on `is_linked`. That is the whole test: `Profile.save()` fires
    `_propagate_country_to_standings`, which sets `is_linked` on the row directly, so an earlier version
    asserting only that flag passed with the union half of the population deleted -- the signal satisfied
    it and the recompute was never involved. Corrupting a figure first and watching the sweep repair it is
    what actually proves the unlinked row is still being visited.
    """
    profile = ProfileFactory(is_linked=True)
    _earn(profile, GameFactory(shovelware_status='clean'), 'platinum')
    assert _standing(profile).is_linked is True

    profile.is_linked = False
    profile.save(update_fields=['is_linked'])
    ProfileTrophyStanding.objects.filter(profile=profile).update(clean_plats=888)
    call_command('recompute_clean_standings')

    row = ProfileTrophyStanding.objects.get(profile=profile)
    assert row.clean_plats == 1, 'the unlinked hunter\'s row was never revisited'
    assert row.is_linked is False


def test_the_country_picker_offers_a_country_that_only_this_board_has():
    """The picker reads FOUR sources, and this board had to become the fourth.

    It looks like it need not be: `clean_trophies > 0` seems to imply `total_trophies > 0`, making its
    countries a subset of the Trophies board's. It does not, because the two figures are written at
    different TIMES. `Profile.total_trophies` is updated at `sync_complete` and on the settings POST;
    this store is rebuilt nightly. A linked hunter whose sync wrote EarnedTrophy rows and then failed
    before completing sits on this board with `total_trophies` still 0 -- which is what the fixture below
    represents.

    Left out, their country was unselectable on the very board they appear on, cached for an hour -- word
    for word the failure `active_countries()`' own docstring already records for the badge store.
    """
    from django.core.cache import cache
    from trophies.services import badge_leaderboards as lb

    cache.clear()
    hidden = ProfileFactory(is_linked=True, country_code='NZ', total_trophies=0, total_plats=0)
    ProfileTrophyStanding.objects.create(
        profile=hidden, clean_plats=1, clean_trophies=40, country_code='NZ', is_linked=True)

    assert lb.clean_rows(limit=10, country='NZ'), 'the fixture is not on the clean board'
    assert not lb.trophy_rows(limit=10, country='NZ'), 'the fixture is on the Trophies board too'
    assert 'NZ' in lb.active_countries(), (
        'the picker does not offer a country whose hunters appear only on the Shovelware Free board'
    )
    cache.clear()


def test_a_budget_capped_run_RESUMES_rather_than_restarting():
    """REGRESSION, and the nastiest kind: silent, permanent, and reported as success.

    The sweep restarted at the beginning every run. A budget cap therefore re-processed the same prefix
    every night -- writing nothing, because change detection skips unchanged rows -- burnt the same budget
    in the same place, and never reached the tail. The hunters past the cutoff would have been absent from
    the DEFAULT board forever, while the cron logged a clean run and printed "deferred to the next run",
    which was simply false.

    Two runs with a budget of zero: the first does one chunk and stops, the second must cover profiles the
    first did not. Asserted as "night two reached rows night one did not", never as specific ids -- the
    property is that the sweep ADVANCES.
    """
    profiles = [ProfileFactory(is_linked=True) for _ in range(6)]
    game = GameFactory(shovelware_status='clean')
    for p in profiles:
        _earn(p, game, 'platinum')

    # `--max-minutes 0` puts the deadline in the past, so the loop breaks after its first chunk.
    call_command('recompute_clean_standings', '--chunk-size', '2', '--max-minutes', '0')
    night_one = set(ProfileTrophyStanding.objects.values_list('profile_id', flat=True))

    call_command('recompute_clean_standings', '--chunk-size', '2', '--max-minutes', '0')
    night_two = set(ProfileTrophyStanding.objects.values_list('profile_id', flat=True))

    assert night_one, 'the first capped run wrote nothing at all'
    assert night_two > night_one, (
        f'the second run made no progress -- it re-processed the same prefix. '
        f'night one: {sorted(night_one)}, night two: {sorted(night_two)}'
    )


def test_the_population_is_ordered_so_the_cursor_means_something():
    """A bare UNION has no defined row order in Postgres, which is what this population used to be. The
    cursor resumes by `id`, so an unordered sweep would skip whatever the previous run happened to pass
    over -- and which hunters got stranded would not even be reproducible."""
    from trophies.management.commands.recompute_clean_standings import Command

    made = [ProfileFactory(is_linked=True) for _ in range(5)]
    ids = list(Command._population())
    assert ids == sorted(ids), 'the population is not ordered by id'

    resumed = list(Command._population(after_id=made[1].id))
    assert all(i > made[1].id for i in resumed), 'the resume point does not bound the population'
    assert set(ids) - set(resumed), 'resuming returned the whole population'


def test_a_zero_or_negative_chunk_size_does_not_silently_do_nothing():
    """`--chunk-size 0` raised ZeroDivisionError, which inside `nightly` aborts the step; a negative made
    `range` empty, so the command wrote nothing and still printed SUCCESS. A typo'd flag must not look
    like a clean run."""
    profile = ProfileFactory(is_linked=True)
    _earn(profile, GameFactory(shovelware_status='clean'), 'platinum')

    call_command('recompute_clean_standings', '--chunk-size', '0')
    assert ProfileTrophyStanding.objects.filter(profile=profile).exists(), 'a zero chunk size wrote nothing'

    ProfileTrophyStanding.objects.all().delete()
    call_command('recompute_clean_standings', '--chunk-size', '-5')
    assert ProfileTrophyStanding.objects.filter(profile=profile).exists(), (
        'a negative chunk size wrote nothing and did not complain'
    )
