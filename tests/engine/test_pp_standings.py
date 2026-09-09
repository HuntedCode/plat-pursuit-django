"""`recompute_pp_standings` -- the nightly writer behind the PP Score board.

The rule it applies is pinned in `test_pp_score.py`. What is pinned HERE is the selection: which 1,000
trophies get summed, what happens at the boundary, and the sweep machinery (population, cursor, change
detection) the board shares with Shovelware Free.

The selection is the part that can be wrong while every individual figure looks right, which is why most
of this file is about the boundary rather than about arithmetic.
"""
import pytest
from django.core.management import call_command

from trophies.models import EarnedTrophy, Profile, ProfilePPStanding
from trophies.services import pp_score
from tests.factories import EarnedTrophyFactory, GameFactory, ProfileFactory, TrophyFactory

pytestmark = pytest.mark.django_db


def _earn(profile, rate, n=1, group='default', earned=True, game=None):
    """`n` earned trophies at one rate. A shared game keeps the fixture cheap."""
    game = game or GameFactory()
    for _ in range(n):
        EarnedTrophyFactory(
            profile=profile,
            trophy=TrophyFactory(game=game, trophy_group_id=group, trophy_earn_rate=rate),
            earned=earned,
        )


def _standing(profile):
    call_command('recompute_pp_standings', '--profile-ids', str(profile.id))
    return ProfilePPStanding.objects.get(profile=profile)


# ---------------------------------------------------------------- the sum -------------------------------

def test_the_score_is_the_summed_points_of_what_was_scored():
    profile = ProfileFactory(is_linked=True)
    _earn(profile, 1.0)          # 100
    _earn(profile, 10.0)         # 10
    _earn(profile, 50.0)         # 2

    row = _standing(profile)
    assert row.pp_score == 112
    assert row.scored_count == 3


def test_the_average_rate_is_across_the_SCORED_set_only():
    """The supporting figure has to explain the number beside it. Averaging the whole library instead
    would describe a different set of trophies from the one that produced the score.

    THE LIBRARY MUST EXCEED THE CAP for this to mean anything. With two trophies both inside the scored
    set, "the scored set" and "the whole library" are the SAME set and the assertion cannot tell the two
    behaviours apart -- verified: averaging over the whole bucket left the old fixture green.

    Here 990 at 1% and 100 at 2% overflow the cap, so only 10 of the second bucket score. The scored mean
    is (990*1.0 + 10*2.0)/1000 = 1.01; averaging the whole library would give 1.09.
    """
    profile = ProfileFactory(is_linked=True)
    game = GameFactory()
    _earn(profile, 1.0, n=pp_score.TOP_N - 10, game=game)
    _earn(profile, 2.0, n=100, game=game)

    row = _standing(profile)
    assert row.scored_count == pp_score.TOP_N
    assert row.avg_earn_rate == pytest.approx(1.01), 'the average is not over the scored set'


def test_the_score_is_ROUNDED_not_truncated():
    """`pp_score` is a PositiveIntegerField and Django's `get_prep_value` is `int(value)`, so an unrounded
    41210.999 stores as 41210 -- a consistent downward bias of about half a point per hunter. Small, but
    it makes the stored figure disagree with the one the formula produces.

    Three trophies at 3% are 33.333... each, summing to 100.0 exactly; at 7% they are 14.2857... each,
    summing to 42.857..., which rounds to 43 and truncates to 42.
    """
    profile = ProfileFactory(is_linked=True)
    _earn(profile, 7.0, n=3)

    assert _standing(profile).pp_score == 43


# ---------------------------------------------------------------- the selection -------------------------

def test_only_the_rarest_TOP_N_are_scored():
    """The whole point of the board. Volume beyond the cap buys nothing, so a hunter cannot climb by
    grinding commons."""
    profile = ProfileFactory(is_linked=True)
    game = GameFactory()
    _earn(profile, 1.0, n=pp_score.TOP_N, game=game)        # 100 each -> exactly fills the cap
    _earn(profile, 50.0, n=50, game=game)                   # 2 each -> must not be reached

    row = _standing(profile)
    assert row.scored_count == pp_score.TOP_N
    assert row.pp_score == pp_score.TOP_N * 100, 'commons past the cap contributed to the score'
    assert row.avg_earn_rate == pytest.approx(1.0), 'commons past the cap moved the average'


def test_the_RAREST_are_taken_not_the_first_found():
    """Selection, not just truncation. The commons are created FIRST so a naive implementation that took
    rows in insertion order would score them and miss the rares entirely."""
    profile = ProfileFactory(is_linked=True)
    game = GameFactory()
    _earn(profile, 60.0, n=pp_score.TOP_N, game=game)       # created first, worth ~1.67 each
    _earn(profile, 0.5, n=5, game=game)                     # worth 200 each

    row = _standing(profile)
    assert row.pp_score > 5 * 200, 'the rarest trophies were not selected'
    assert row.avg_earn_rate < 60.0


def test_a_TIED_bucket_is_split_at_the_boundary_not_taken_whole():
    """The boundary bucket contributes only its SHARE. Taking it whole would score more than TOP_N
    trophies; skipping it whole would score fewer, and either way `scored_count` stops meaning what the
    membership rule reads it as.

    Ties are the normal case, not an edge one: PSN reports rates to one decimal, so ~1.03M catalogue
    trophies share about 1,000 distinct values and the boundary lands inside a bucket for essentially
    every qualifying hunter.
    """
    profile = ProfileFactory(is_linked=True)
    game = GameFactory()
    _earn(profile, 1.0, n=pp_score.TOP_N - 10, game=game)   # 100 each
    _earn(profile, 2.0, n=100, game=game)                   # 50 each; only 10 of them fit

    row = _standing(profile)
    assert row.scored_count == pp_score.TOP_N
    assert row.pp_score == (pp_score.TOP_N - 10) * 100 + 10 * 50
    # The average follows the same split, or the supporting figure describes a different set from the
    # score beside it: (990*1.0 + 10*2.0)/1000, not the whole library's 1.09.
    assert row.avg_earn_rate == pytest.approx(1.01)


# ---------------------------------------------------------------- what is excluded ----------------------

@pytest.mark.parametrize('kind,kwargs', [
    ('DLC', {'group': '001'}),
    ('unearned', {'earned': False}),
])
def test_excluded_trophies_do_not_reach_the_score(kind, kwargs):
    """DLC because PSN overstates its rarity; unearned because those rows track PROGRESS, and counting
    them would rank library ownership rather than achievement.

    Both are asserted through the command rather than through the filter alone -- a helper can be correct
    while the caller reaches around it, which is the failure `scorable_earned` exists to prevent.
    """
    profile = ProfileFactory(is_linked=True)
    game = GameFactory()
    _earn(profile, 5.0, game=game)                                   # 20 points, counts
    _earn(profile, 0.1, n=3, game=game, **kwargs)                    # 1,000 each if it leaked

    row = _standing(profile)
    assert row.pp_score == 20, f'{kind} trophies reached the score'
    assert row.scored_count == 1


def test_an_unknown_rate_scores_nothing_even_though_it_sorts_first():
    """THE trap the rate-ordering creates. `trophy_earn_rate` defaults to 0.0 and sync stores 0.0 when PSN
    omits the figure, so unknown rates sort FIRST under a rarest-first ordering -- ahead of every genuine
    ultra-rare. Left in, they would fill the scoring set with 0-point rows while `scored_count` still
    reached TOP_N, so the hunter would pass the membership rule holding a nonsense score.
    """
    profile = ProfileFactory(is_linked=True)
    game = GameFactory()
    _earn(profile, 0.0, n=5, game=game)
    _earn(profile, 2.0, n=2, game=game)

    row = _standing(profile)
    assert row.scored_count == 2, 'unknown-rate trophies entered the scoring set'
    assert row.pp_score == 100


def test_a_hunter_with_nothing_scorable_gets_a_ZEROED_row_not_no_row():
    """Its absence and its zero mean different things, and the sweep has to be able to revisit them."""
    profile = ProfileFactory(is_linked=True)
    _earn(profile, 0.5, n=2, group='001')          # DLC only

    row = _standing(profile)
    assert (row.pp_score, row.scored_count, row.avg_earn_rate) == (0, 0, 0.0)


# ---------------------------------------------------------------- the sweep ----------------------------

def test_the_recompute_is_idempotent_and_self_healing():
    profile = ProfileFactory(is_linked=True)
    _earn(profile, 2.0, n=4)

    assert _standing(profile).pp_score == 200
    assert _standing(profile).pp_score == 200, 'a second run doubled the score'

    ProfilePPStanding.objects.filter(profile=profile).update(pp_score=99999, scored_count=1)
    assert _standing(profile).pp_score == 200, 'a drifted row was not corrected'


def test_an_unchanged_row_is_not_rewritten():
    profile = ProfileFactory(is_linked=True)
    _earn(profile, 2.0)
    first = _standing(profile).updated_at

    assert _standing(profile).updated_at == first, 'an unchanged row was rewritten anyway'


def test_updated_at_advances_when_the_score_does():
    """`auto_now` is applied in `Model.save()`, which `bulk_update` does not call -- so without a manual
    stamp the timestamp freezes at creation while the figures beside it move."""
    profile = ProfileFactory(is_linked=True)
    game = GameFactory()
    _earn(profile, 2.0, game=game)
    first = _standing(profile).updated_at

    _earn(profile, 1.0, game=game)
    row = _standing(profile)

    assert row.pp_score == 150
    assert row.updated_at > first, 'the score moved but the freshness stamp did not'


def test_a_budget_capped_run_RESUMES_rather_than_restarting():
    """Silent, permanent starvation otherwise: the sweep would re-process the same prefix every night,
    write nothing (change detection skips it), and never reach the tail -- while reporting success."""
    profiles = [ProfileFactory(is_linked=True) for _ in range(6)]
    game = GameFactory()
    for p in profiles:
        _earn(p, 5.0, game=game)

    call_command('recompute_pp_standings', '--chunk-size', '2', '--max-minutes', '0')
    night_one = set(ProfilePPStanding.objects.values_list('profile_id', flat=True))
    call_command('recompute_pp_standings', '--chunk-size', '2', '--max-minutes', '0')
    night_two = set(ProfilePPStanding.objects.values_list('profile_id', flat=True))

    assert night_one, 'the first capped run wrote nothing at all'
    assert night_two > night_one, f'no progress: {sorted(night_one)} then {sorted(night_two)}'


def test_an_unlinked_profile_with_no_row_is_not_swept_in():
    unlinked = ProfileFactory(is_linked=False)
    _earn(unlinked, 1.0)

    call_command('recompute_pp_standings')
    assert not ProfilePPStanding.objects.filter(profile=unlinked).exists()


def test_a_hunter_who_unlinks_keeps_being_corrected():
    """The other half of the population rule. Asserted on the FIGURES, not on `is_linked` -- that flag is
    set directly by the propagation signal, so asserting it would pass with the sweep never running."""
    profile = ProfileFactory(is_linked=True)
    _earn(profile, 2.0, n=4)
    assert _standing(profile).pp_score == 200

    profile.is_linked = False
    profile.save(update_fields=['is_linked'])
    ProfilePPStanding.objects.filter(profile=profile).update(pp_score=88888)
    call_command('recompute_pp_standings')

    row = ProfilePPStanding.objects.get(profile=profile)
    assert row.pp_score == 200, 'the unlinked hunter was never revisited'
    assert row.is_linked is False


def test_the_recompute_stamps_the_mirrors_at_birth():
    profile = ProfileFactory(is_linked=True, country_code='GB')
    _earn(profile, 1.0)

    row = _standing(profile)
    assert row.country_code == 'GB' and row.is_linked is True


def test_dry_run_writes_nothing():
    profile = ProfileFactory(is_linked=True)
    _earn(profile, 1.0)

    call_command('recompute_pp_standings', '--dry-run')
    assert not ProfilePPStanding.objects.filter(profile=profile).exists()
