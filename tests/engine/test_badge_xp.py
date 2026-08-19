"""Badge XP + progress: pure compute + the recompute_standing write seam (ProfileBadgeStanding + SeriesBadgeStanding)."""
import pytest
from django.utils import timezone

from trophies.services.badge_engine import GroupBadgeResult
from trophies.services.badge_xp import (
    compute_badge_xp, compute_series_standings, edition_display_state, XP_PER_STAGE, XP_BADGE_COMPLETION_BONUS,
)


def _res(base_satisfied_count, base_earned, gating_count=None):
    gc = base_satisfied_count if gating_count is None else gating_count
    return GroupBadgeResult(
        base_earned=base_earned, holo=False, gating_count=gc,
        base_satisfied_count=base_satisfied_count, holo_satisfied_count=0,
        earned_date=None, stages=[],
    )


# ------------------------------------------------------------------ pure XP -------------------------------

def test_stage_xp_plus_completion_bonus():
    total, per = compute_badge_xp({'gow': [_res(3, True)]})
    assert per['gow'] == 3 * XP_PER_STAGE + XP_BADGE_COMPLETION_BONUS
    assert total == per['gow']


def test_partial_progress_no_bonus():
    total, per = compute_badge_xp({'gow': [_res(2, False, gating_count=5)]})
    assert per['gow'] == 2 * XP_PER_STAGE      # cleared 2 of 5 -> drip only, no completion bonus
    assert total == 2 * XP_PER_STAGE


def test_two_group_badges_sum_into_series():
    total, per = compute_badge_xp({'gow': [_res(2, True), _res(3, True)]})
    expected = (2 * XP_PER_STAGE + XP_BADGE_COMPLETION_BONUS) + (3 * XP_PER_STAGE + XP_BADGE_COMPLETION_BONUS)
    assert per['gow'] == expected and total == expected


def test_edition_display_state():
    """The shared per-edition derivation both the Collection wall (read-model) and the badge-detail live view
    run, so they can't disagree. Held wins regardless of counts; cleared>0 -> in_progress at cleared/gating;
    else unearned; gating==0 is div-guarded."""
    assert edition_display_state(True, 0, 0) == ('earned', 100)         # held -> earned/100 regardless of counts
    assert edition_display_state(True, 3, 5) == ('earned', 100)
    assert edition_display_state(False, 3, 5) == ('in_progress', 60)
    assert edition_display_state(False, 1, 3) == ('in_progress', 33)
    assert edition_display_state(False, 0, 5) == ('unearned', 0)        # no gating stage cleared -> waiting mount
    assert edition_display_state(False, 5, 5) == ('in_progress', 100)   # fully cleared but not held (transient)
    assert edition_display_state(False, 2, 0) == ('in_progress', 0)     # gating==0 guard -> no ZeroDivisionError


def test_holo_does_not_change_xp():
    plain = compute_badge_xp({'gow': [_res(3, True)]})[0]
    holo_res = GroupBadgeResult(True, True, 3, 3, 3, None, [])
    assert compute_badge_xp({'gow': [holo_res]})[0] == plain


def test_empty():
    assert compute_badge_xp({}) == (0, {})


def test_million_club_calibration():
    # The "1,000,000 Club" target. Over the projected mature catalog (~400 group badges, ~5 gating stages each),
    # a completionist should land ~1.24M so 1M is reachable-but-hard (~80% of the catalog), with headroom above
    # for two-version + holo elites. Pins the XP constants against silent drift -- if you retune them, retune
    # this target too (and confirm the catalog assumption still holds).
    PROJECTED_BADGES, AVG_STAGES = 400, 5
    per_badge = AVG_STAGES * XP_PER_STAGE + XP_BADGE_COMPLETION_BONUS
    assert per_badge == 3100

    completionist = {f's{i}': [_res(AVG_STAGES, True)] for i in range(PROJECTED_BADGES)}
    total, _ = compute_badge_xp(completionist)
    assert total == 1_240_000                       # completionist max, headroom above 1M
    assert total > 1_000_000

    badges_for_million = 1_000_000 / per_badge
    assert 300 <= badges_for_million <= 340         # ~323 badges -> ~81% of the catalog: hard but doable


# ------------------------------------------------------------------ pure progress ------------------------

def test_progress_is_best_group_fraction():
    # one group 2/5 (40%), another 1/2 (50%) -> best = 50%, and its raw N/M is reported.
    st = compute_series_standings({'s': [_res(2, False, 5), _res(1, False, 2)]})['s']
    assert st.progress_bp == 5000 and st.stages_cleared == 1 and st.stages_total == 2


def test_progress_is_100_when_a_group_is_earned():
    st = compute_series_standings({'s': [_res(3, True)]})['s']
    assert st.progress_bp == 10000 and st.stages_cleared == 3 and st.stages_total == 3


# ------------------------------------------------------------------ store / wiring (DB) ------------------

def _make_series(slug, n_stages):
    from tests.factories import (
        BadgeSeriesFactory, StageFactory, ConceptFactory, GameFactory,
        PlatformGroupFactory, GroupBadgeFactory,
    )
    series = BadgeSeriesFactory(series_slug=slug)
    pg = PlatformGroupFactory(key='ultra-hd', name='Ultra', platforms=['PS4', 'PS5'], exclude_delisted=True)
    gb = GroupBadgeFactory(series=series, platform_group=pg, is_live=True)
    games = []
    for i in range(1, n_stages + 1):
        st = StageFactory(series_slug=slug, stage_number=i)
        c = ConceptFactory()
        st.concepts.add(c)
        games.append(GameFactory(concept=c, title_platform=['PS5']))
    return gb, games


def _complete(profile, game):
    from trophies.models import ProfileGame, TrophyGroup, ProfileTrophyGroup
    ProfileGame.objects.update_or_create(profile=profile, game=game, defaults={'progress': 50})
    tg, _ = TrophyGroup.objects.get_or_create(game=game, trophy_group_id='default', defaults={'trophy_group_name': 'B'})
    ProfileTrophyGroup.objects.update_or_create(
        profile=profile, trophy_group=tg, defaults={'progress': 100, 'last_trophy_at': timezone.now()},
    )


@pytest.mark.django_db
def test_standing_partial_progress_writes_xp_and_progress():
    from trophies.services.badge_apply import evaluate_and_apply
    from trophies.models import ProfileBadgeStanding, SeriesBadgeStanding
    from tests.factories import ProfileFactory
    gb, games = _make_series('gow', 3)
    p = ProfileFactory()
    _complete(p, games[0])                       # 1 of 3 gating stages -> not earned
    evaluate_and_apply(p, [gb])
    sbs = SeriesBadgeStanding.objects.get(profile=p, series_slug='gow')
    assert sbs.xp == XP_PER_STAGE
    assert sbs.stages_cleared == 1 and sbs.stages_total == 3 and sbs.progress_bp == 3333
    assert ProfileBadgeStanding.objects.get(profile=p).total_xp == XP_PER_STAGE


@pytest.mark.django_db
def test_standing_materializes_per_edition_group_progress():
    """recompute_standing writes the per-edition read-model the Collection reads: group_progress maps each
    edition's platform_group key -> [cleared, gating], for every EARNABLE edition -- started or not.

    An untouched edition is stored as [0, gating] rather than omitted. It used to be omitted, which left the
    Collection wall with no denominator for a chase the hunter had not begun, so "0 / 5 stages" rendered blank.
    The total cannot be recovered from the series: gating is PER EDITION, so any series-level count would lie
    for an edition whose stages don't all run on its platforms. Only cleared > 0 flips the state to in_progress
    (edition_display_state), so storing the zero row changes the denominator, never the state."""
    from trophies.services.badge_apply import evaluate_and_apply
    from trophies.models import SeriesBadgeStanding
    from tests.factories import (
        ProfileFactory, BadgeSeriesFactory, StageFactory, ConceptFactory, GameFactory,
        PlatformGroupFactory, GroupBadgeFactory,
    )
    series = BadgeSeriesFactory(series_slug='gow')
    ultra = GroupBadgeFactory(series=series, is_live=True,
                              platform_group=PlatformGroupFactory(key='ultra-hd', name='Ultra', platforms=['PS4', 'PS5']))
    legacy = GroupBadgeFactory(series=series, is_live=True,
                               platform_group=PlatformGroupFactory(key='legacy-hd', name='Legacy', platforms=['PS3']))
    games = {}   # each stage has a PS5 game (gates Ultra HD) + a PS3 game (gates Legacy HD)
    for i in (1, 2):
        st = StageFactory(series_slug='gow', stage_number=i)
        c = ConceptFactory(); st.concepts.add(c)
        games[('ps5', i)] = GameFactory(concept=c, title_platform=['PS5'])
        games[('ps3', i)] = GameFactory(concept=c, title_platform=['PS3'])
    p = ProfileFactory()
    _complete(p, games[('ps5', 1)])              # Ultra HD 1/2 ; Legacy HD 0/2 (no PS3 game completed)
    evaluate_and_apply(p, [ultra, legacy])

    sbs = SeriesBadgeStanding.objects.get(profile=p, series_slug='gow')
    assert sbs.group_progress == {'ultra-hd': [1, 2], 'legacy-hd': [0, 2]}
    # ...and the zero row must NOT read as started -- state still comes from cleared, not from presence.
    from trophies.services.badge_xp import edition_display_state
    assert edition_display_state(False, *sbs.group_progress['legacy-hd']) == ('unearned', 0)


@pytest.mark.django_db
def test_an_unearnable_edition_is_still_omitted():
    """gating_count == 0 means the badge is not offered in that platform group at all (no stage has a
    qualifying, obtainable game there). It must stay OUT of the read-model: storing [0, 0] would put an
    edition with no path to completion on the wall advertising a chase."""
    from trophies.services.badge_apply import evaluate_and_apply
    from trophies.models import SeriesBadgeStanding
    from tests.factories import (
        ProfileFactory, BadgeSeriesFactory, StageFactory, ConceptFactory, GameFactory,
        PlatformGroupFactory, GroupBadgeFactory,
    )
    series = BadgeSeriesFactory(series_slug='ps5only')
    ultra = GroupBadgeFactory(series=series, is_live=True,
                              platform_group=PlatformGroupFactory(key='ultra-hd', name='Ultra', platforms=['PS4', 'PS5']))
    legacy = GroupBadgeFactory(series=series, is_live=True,
                               platform_group=PlatformGroupFactory(key='legacy-hd', name='Legacy', platforms=['PS3']))
    st = StageFactory(series_slug='ps5only', stage_number=1)
    c = ConceptFactory(); st.concepts.add(c)
    game = GameFactory(concept=c, title_platform=['PS5'])      # nothing this badge needs exists on PS3
    p = ProfileFactory()
    _complete(p, game)
    evaluate_and_apply(p, [ultra, legacy])

    sbs = SeriesBadgeStanding.objects.get(profile=p, series_slug='ps5only')
    assert 'legacy-hd' not in sbs.group_progress, 'an unearnable edition must advertise no chase'
    assert sbs.group_progress['ultra-hd'] == [1, 1]


@pytest.mark.django_db
def test_standing_earned_gets_bonus_and_full_progress():
    from trophies.services.badge_apply import evaluate_and_apply
    from trophies.models import ProfileBadgeStanding, SeriesBadgeStanding
    from tests.factories import ProfileFactory
    gb, games = _make_series('gow', 3)
    p = ProfileFactory()
    for g in games:
        _complete(p, g)                          # all 3 -> earned
    evaluate_and_apply(p, [gb])
    assert ProfileBadgeStanding.objects.get(profile=p).total_xp == 3 * XP_PER_STAGE + XP_BADGE_COMPLETION_BONUS
    assert SeriesBadgeStanding.objects.get(profile=p, series_slug='gow').progress_bp == 10000


@pytest.mark.django_db
def test_zero_xp_profile_gets_no_rows():
    from trophies.services.badge_apply import evaluate_and_apply
    from trophies.models import ProfileBadgeStanding, SeriesBadgeStanding
    from tests.factories import ProfileFactory
    gb, _games = _make_series('gow', 3)
    p = ProfileFactory()                         # no progress at all
    evaluate_and_apply(p, [gb])
    assert not ProfileBadgeStanding.objects.filter(profile=p).exists()
    assert not SeriesBadgeStanding.objects.filter(profile=p).exists()


@pytest.mark.django_db
def test_scoped_series_merge_preserves_other_series_and_total():
    from trophies.services.badge_apply import evaluate_and_apply
    from trophies.models import ProfileBadgeStanding, SeriesBadgeStanding
    from tests.factories import ProfileFactory
    gbA, gamesA = _make_series('aaa', 2)
    gbB, gamesB = _make_series('bbb', 1)
    p = ProfileFactory()
    for g in gamesA:
        _complete(p, g)
    _complete(p, gamesB[0])
    evaluate_and_apply(p, [gbA])                 # scoped to A
    evaluate_and_apply(p, [gbB])                 # scoped to B -- must NOT wipe A's row
    slugs = set(SeriesBadgeStanding.objects.filter(profile=p).values_list('series_slug', flat=True))
    assert slugs == {'aaa', 'bbb'}
    total = ProfileBadgeStanding.objects.get(profile=p).total_xp
    assert total == sum(SeriesBadgeStanding.objects.filter(profile=p).values_list('xp', flat=True))


@pytest.mark.django_db
def test_standing_removed_when_progress_regresses_to_zero():
    from trophies.services.badge_apply import evaluate_and_apply
    from trophies.models import ProfileBadgeStanding, SeriesBadgeStanding, ProfileTrophyGroup, ProfileGame
    from tests.factories import ProfileFactory
    gb, games = _make_series('gow', 1)
    p = ProfileFactory()
    _complete(p, games[0])
    evaluate_and_apply(p, [gb])
    assert ProfileBadgeStanding.objects.get(profile=p).total_xp > 0
    # Data regresses (e.g. PSN correction): no longer complete -> the series row + grand total are removed.
    ProfileTrophyGroup.objects.filter(profile=p).update(progress=0)
    ProfileGame.objects.filter(profile=p).update(progress=0)
    evaluate_and_apply(p, [gb])
    assert not SeriesBadgeStanding.objects.filter(profile=p, series_slug='gow').exists()
    assert not ProfileBadgeStanding.objects.filter(profile=p).exists()


def test_an_unearnable_edition_can_never_report_cleared_stages():
    """The invariant the read-model's gate rests on.

    `recompute_standing` now stores an entry per edition with `gating_count > 0`, replacing a gate on
    `base_satisfied_count > 0`. That is only a pure widening if `gating_count == 0` implies
    `base_satisfied_count == 0` -- otherwise editions would have been dropped that used to be stored,
    silently flipping them from in_progress to unearned on the wall.

    It holds by construction: `evaluate_group_badge` computes `base_ok` by summing over the GATING list,
    so an empty gating list can only produce 0. Pinned here because the gate's correctness depends on it
    and the two live far apart."""
    from trophies.services.badge_engine import (
        evaluate_group_badge, GameState, GroupInput, SeriesInput, StageInput,
    )

    series = SeriesInput(completion_policy='all')
    group = GroupInput(platforms=frozenset({'PS3'}), exclude_delisted=False)
    # A stage whose only game is PS5: it cannot gate a PS3 group, and the hunter has FINISHED it -- which
    # is what makes this the interesting case rather than a trivially-zero one.
    stage = StageInput(stage_number=1, games=(
        GameState(game_id=1, platforms=frozenset({'PS5'}), is_obtainable=True, is_delisted=False,
                  base_complete=True, full_complete=True),
    ))

    result = evaluate_group_badge(series, group, [stage])

    assert result.gating_count == 0
    assert result.base_satisfied_count == 0, 'an unearnable edition cannot report cleared gating stages'
    assert result.base_earned is False


# ------------------------------------------------------------------ the per-edition board store ----------

def _two_edition_series(slug='dual'):
    """One series offered in two editions, each gated by its own platform's copy of every stage.

    Returns `(ultra, legacy, games)` where `games[(plat, stage_no)]` is the game a hunter completes to
    clear that stage in that edition. This is the shape `SeriesEditionStanding` exists for: the same
    series, two independent chases, each with its own points and its own dates.
    """
    from tests.factories import (
        BadgeSeriesFactory, StageFactory, ConceptFactory, GameFactory,
        PlatformGroupFactory, GroupBadgeFactory,
    )
    series = BadgeSeriesFactory(series_slug=slug)
    ultra = GroupBadgeFactory(series=series, is_live=True, platform_group=PlatformGroupFactory(
        key='ultra-hd', name='Ultra', platforms=['PS4', 'PS5']))
    legacy = GroupBadgeFactory(series=series, is_live=True, platform_group=PlatformGroupFactory(
        key='legacy-hd', name='Legacy', platforms=['PS3']))
    games = {}
    for i in (1, 2):
        st = StageFactory(series_slug=slug, stage_number=i)
        c = ConceptFactory()
        st.concepts.add(c)
        games[('ps5', i)] = GameFactory(concept=c, title_platform=['PS5'])
        games[('ps3', i)] = GameFactory(concept=c, title_platform=['PS3'])
    return ultra, legacy, games


def _complete_on(profile, game, when):
    """`_complete`, with the trophy date pinned. `_advanced_at` reads the stage's `base_date` off
    `ProfileTrophyGroup.last_trophy_at`, so a test about DATES has to set them rather than take now()."""
    from trophies.models import ProfileGame, TrophyGroup, ProfileTrophyGroup
    ProfileGame.objects.update_or_create(profile=profile, game=game, defaults={'progress': 50})
    tg, _ = TrophyGroup.objects.get_or_create(game=game, trophy_group_id='default',
                                              defaults={'trophy_group_name': 'B'})
    ProfileTrophyGroup.objects.update_or_create(
        profile=profile, trophy_group=tg, defaults={'progress': 100, 'last_trophy_at': when})


@pytest.mark.django_db
def test_only_a_STARTED_edition_gets_a_board_row():
    """The store's membership rule, and the one place it differs from `group_progress`.

    `group_progress` deliberately keeps an untouched edition as `[0, gating]` so the Collection wall has a
    denominator for a chase not yet begun. A BOARD has no use for that row, and writing one would put
    every chaser of every OTHER edition on this edition's board at zero points -- which is the padding the
    old JSON read had to filter out on every query. Filtered at WRITE now, where it costs nothing.
    """
    from trophies.services.badge_apply import evaluate_and_apply
    from trophies.models import SeriesBadgeStanding, SeriesEditionStanding
    from tests.factories import ProfileFactory

    ultra, legacy, games = _two_edition_series()
    p = ProfileFactory()
    _complete(p, games[('ps5', 1)])                  # Ultra HD 1/2 ; Legacy HD untouched
    evaluate_and_apply(p, [ultra, legacy])

    sbs = SeriesBadgeStanding.objects.get(profile=p, series_slug='dual')
    assert sbs.group_progress == {'ultra-hd': [1, 2], 'legacy-hd': [0, 2]}, 'fixture is not the shape it claims'

    rows = {r.platform_group_key: r for r in SeriesEditionStanding.objects.filter(profile=p)}
    assert set(rows) == {'ultra-hd'}, 'an untouched edition was put on its own board at zero'
    assert rows['ultra-hd'].stages_cleared == 1 and rows['ultra-hd'].gating_count == 2
    assert rows['ultra-hd'].xp == XP_PER_STAGE
    assert rows['ultra-hd'].series_slug == 'dual'


@pytest.mark.django_db
def test_the_edition_rows_cost_two_queries_however_many_series_there_are():
    """The write seam runs on EVERY sync, so its query count must not scale with a hunter's engagement.

    First written as an upsert-and-prune per series, which is `series x (editions + 1)` round trips: a
    hunter engaged with 40 two-edition series paid ~120 queries where two do. The rows are five narrow
    columns, so replacing them all wholesale is cheaper than working out which ones moved -- and it is
    safe only because `recompute_standing` is atomic and holds the per-profile advisory lock, so nobody
    can read the gap between the delete and the insert.

    Asserted at TWO different engagement levels rather than against a magic number: a constant that
    happens to be right for one fixture would go unnoticed, a count that GROWS is the actual failure.
    """
    from django.db import connection
    from django.test.utils import CaptureQueriesContext
    from trophies.services.badge_apply import evaluate_and_apply
    from tests.factories import ProfileFactory

    def edition_queries(n_series):
        badges, games = [], {}
        for n in range(n_series):
            ultra, legacy, g = _two_edition_series(f'q{n_series}s{n}')
            badges += [ultra, legacy]
            games[n] = g
        p = ProfileFactory()
        for n in range(n_series):
            _complete(p, games[n][('ps5', 1)])
            _complete(p, games[n][('ps3', 1)])
        evaluate_and_apply(p, badges)                     # first pass creates the rows...
        with CaptureQueriesContext(connection) as ctx:
            evaluate_and_apply(p, badges)                 # ...this one is the steady state
        return len([q for q in ctx.captured_queries
                    if 'serieseditionstanding' in q['sql'].lower()])

    assert edition_queries(1) == 2, 'the edition store is not one delete plus one insert'
    assert edition_queries(6) == 2, (
        'the edition write scales with engaged series -- it is per-series again, and this seam runs on '
        'every sync'
    )


@pytest.mark.django_db
def test_each_edition_row_carries_its_OWN_advance_date():
    """THE BUG THE STORE EXISTS FOR. `SeriesBadgeStanding.advanced_at` is series-wide -- the furthest-along
    edition's date -- so ranking an edition board on it separated hunters tied on THIS edition by their
    progress in a DIFFERENT one. Advancing on PS5 could drop a rank on Legacy HD.

    The per-edition date was already computed and thrown away: `_advanced_at` takes one edition's
    `GroupBadgeResult` and `compute_series_standings` calls it for the best edition only. Here Ultra HD is
    further along AND more recent, so the series-wide date is Ultra's -- and Legacy's row must not have it.
    """
    import datetime as dt
    from django.utils import timezone as tz
    from trophies.services.badge_apply import evaluate_and_apply
    from trophies.models import SeriesBadgeStanding, SeriesEditionStanding
    from tests.factories import ProfileFactory

    ultra, legacy, games = _two_edition_series()
    old = tz.make_aware(dt.datetime(2024, 3, 4))
    recent = tz.make_aware(dt.datetime(2026, 7, 8))
    p = ProfileFactory()
    _complete_on(p, games[('ps3', 1)], old)          # Legacy HD 1/2, long ago
    _complete_on(p, games[('ps5', 1)], recent)       # Ultra HD  2/2 (both PS5 copies)...
    _complete_on(p, games[('ps5', 2)], recent)
    evaluate_and_apply(p, [ultra, legacy])

    series_wide = SeriesBadgeStanding.objects.get(profile=p, series_slug='dual').advanced_at
    rows = {r.platform_group_key: r.advanced_at for r in SeriesEditionStanding.objects.filter(profile=p)}

    assert series_wide == recent.date(), "the series-wide date is not the furthest-along edition's"
    assert rows['ultra-hd'] == recent.date()
    assert rows['legacy-hd'] == old.date(), (
        f"Legacy HD was dated {rows['legacy-hd']} -- its board would tiebreak on PS5 progress"
    )


@pytest.mark.django_db
def test_an_edition_that_stops_being_started_loses_its_row():
    """A full REPLACE per (profile, series), not an upsert-and-forget.

    PSN corrections regress progress, and a stage's only game on a platform can be delisted out of an
    edition. Either way the hunter stops being on that edition's board, and a row nothing prunes would
    keep them ranked there indefinitely -- with the points they had the day it broke.
    """
    from trophies.services.badge_apply import evaluate_and_apply
    from trophies.models import ProfileGame, ProfileTrophyGroup, SeriesEditionStanding
    from tests.factories import ProfileFactory

    ultra, legacy, games = _two_edition_series()
    p = ProfileFactory()
    _complete(p, games[('ps5', 1)])
    _complete(p, games[('ps3', 1)])
    evaluate_and_apply(p, [ultra, legacy])
    assert SeriesEditionStanding.objects.filter(profile=p).count() == 2

    # The PS3 completion regresses; the PS5 one stands.
    ProfileTrophyGroup.objects.filter(profile=p, trophy_group__game=games[('ps3', 1)]).update(progress=0)
    ProfileGame.objects.filter(profile=p, game=games[('ps3', 1)]).update(progress=0)
    evaluate_and_apply(p, [ultra, legacy])

    assert list(SeriesEditionStanding.objects.filter(profile=p)
                .values_list('platform_group_key', flat=True)) == ['ultra-hd']


@pytest.mark.django_db
def test_a_series_dropping_to_zero_takes_its_edition_rows_with_it():
    """Two stores, one truth: the series board drops this hunter, so the edition board must too.

    The mechanism is the SCOPED delete, not a special case -- a zeroed series is still a series this call
    evaluated, so it is in `touched` and its rows go with everything else's. Worth pinning as BEHAVIOUR
    anyway: the reason it needs no branch of its own is an argument about set membership
    (`positive + zeroed == standings`) that a later edit could quietly break."""
    from trophies.services.badge_apply import evaluate_and_apply
    from trophies.models import (
        ProfileGame, ProfileTrophyGroup, SeriesBadgeStanding, SeriesEditionStanding,
    )
    from tests.factories import ProfileFactory

    ultra, legacy, games = _two_edition_series()
    p = ProfileFactory()
    _complete(p, games[('ps5', 1)])
    evaluate_and_apply(p, [ultra, legacy])
    assert SeriesEditionStanding.objects.filter(profile=p).exists()

    ProfileTrophyGroup.objects.filter(profile=p).update(progress=0)
    ProfileGame.objects.filter(profile=p).update(progress=0)
    evaluate_and_apply(p, [ultra, legacy])

    assert not SeriesBadgeStanding.objects.filter(profile=p, series_slug='dual').exists()
    assert not SeriesEditionStanding.objects.filter(profile=p).exists(), (
        'the edition board still ranks a hunter whose series standing is gone'
    )


@pytest.mark.django_db
def test_a_scoped_recompute_leaves_another_series_edition_rows_alone():
    """The delete is scoped to the series this call evaluated. Nothing else asserts that.

    Every other test here passes every badge, so the scope is invisible: rewriting the delete to
    `filter(profile_id=profile_id)` -- dropping the `series_slug__in` -- would pass the whole suite while
    wiping a hunter off every OTHER badge's edition board on any `--series` run or any sync that touched
    one game. It self-heals on the next full evaluation, which is what would make it hard to catch: the
    boards would just be intermittently short.

    `test_badge_sync_wiring` pins that callers scope by SERIES. This pins what the seam does with that.
    """
    from trophies.services.badge_apply import evaluate_and_apply
    from trophies.models import SeriesEditionStanding
    from tests.factories import ProfileFactory

    a_ultra, a_legacy, a_games = _two_edition_series('scoped-a')
    b_ultra, b_legacy, b_games = _two_edition_series('scoped-b')
    p = ProfileFactory()
    _complete(p, a_games[('ps5', 1)])
    _complete(p, b_games[('ps3', 1)])
    evaluate_and_apply(p, [a_ultra, a_legacy, b_ultra, b_legacy])
    assert SeriesEditionStanding.objects.filter(profile=p).count() == 2

    # Re-run scoped to B only, the shape `evaluate_for_sync` produces when a sync touches one B game.
    evaluate_and_apply(p, [b_ultra, b_legacy])

    rows = dict(SeriesEditionStanding.objects.filter(profile=p)
                .values_list('series_slug', 'platform_group_key'))
    assert rows == {'scoped-a': 'ultra-hd', 'scoped-b': 'legacy-hd'}, (
        f'a scoped recompute reached outside its own series: {rows}'
    )


@pytest.mark.django_db
def test_a_dormant_series_keeps_its_edition_rows_when_the_profile_total_hits_zero():
    """REGRESSION. The zero-total branch deletes the two PROFILE-wide standing stores, and briefly deleted
    this per-SERIES one alongside them -- profile-wide, reaching past the series the call evaluated.

    The path is a curator's, not an edge case. `_live_standings` deliberately EXCLUDES a series whose
    editions have all been un-lived (so a stage can be re-authored without erasing anyone's history), so a
    hunter whose only points are in a dormant series totals 0 while their rows are deliberately preserved.
    The nightly, evaluating the LIVE catalogue, then deleted the edition rows of a series it had not been
    handed. Re-live the badge and the series board ranks that hunter while its own edition board says they
    are not chasing it -- the two-stores-disagree failure the store's docstring calls out, arrived at from
    the other direction.

    The fixture needs a SECOND, live series the hunter has not started: `evaluate_and_apply` returns early
    on an empty badge list and never reaches the seam, so evaluating "nothing" cannot exercise this and a
    test written that way passes against the bug. The live series contributes 0 XP, which is what drives
    the total to zero while the dormant series' rows sit there untouched.
    """
    from trophies.services.badge_apply import evaluate_and_apply
    from trophies.models import GroupBadge, SeriesBadgeStanding, SeriesEditionStanding
    from tests.factories import ProfileFactory

    dormant_u, dormant_l, dormant_games = _two_edition_series('dormant')
    live_u, live_l, _live_games = _two_edition_series('still-live')
    p = ProfileFactory()
    _complete(p, dormant_games[('ps5', 1)])            # points, in the series about to go dormant
    evaluate_and_apply(p, [dormant_u, dormant_l])
    assert SeriesEditionStanding.objects.filter(profile=p).count() == 1

    # The curator pulls that series to re-author it. The nightly then runs over the live catalogue, which
    # no longer includes it -- and the hunter has done nothing in the series that IS live.
    GroupBadge.objects.filter(series__series_slug='dormant').update(is_live=False)
    evaluate_and_apply(p, [live_u, live_l])

    assert SeriesBadgeStanding.objects.filter(profile=p, series_slug='dormant').exists(), (
        'the fixture is not exercising the dormant path -- the series standing itself was removed'
    )
    assert SeriesEditionStanding.objects.filter(profile=p, series_slug='dormant').count() == 1, (
        'a recompute deleted the edition rows of a series it was never handed'
    )

@pytest.mark.django_db
def test_the_edition_rows_mirror_the_profile_fields_the_board_filters_on():
    """`country_code` and `is_linked` are board PREDICATES, and a predicate on another table cannot go in
    this table's indexes. Stamped by the recompute like every other standing store -- `signals
    .profile_mirrored_standings` covers the edge this misses (verifying, which changes `is_linked` with no
    recompute behind it) and has its own guard test."""
    from trophies.services.badge_apply import evaluate_and_apply
    from trophies.models import SeriesEditionStanding
    from tests.factories import ProfileFactory

    ultra, legacy, games = _two_edition_series()
    p = ProfileFactory(country_code='CA', is_linked=True)
    _complete(p, games[('ps5', 1)])
    evaluate_and_apply(p, [ultra, legacy])

    row = SeriesEditionStanding.objects.get(profile=p)
    assert (row.country_code, row.is_linked) == ('CA', True)
