"""Platform-EDITION slicing on the two badge boards (2026-08).

Legacy HD and Ultra HD are different games -- the XP model says so outright, accruing XP per GROUP BADGE
rather than per series -- so "who leads Legacy HD" is a real question the all-editions board cannot answer.

The design follows the call country already made: a filter, not a board, backed by a store shaped so the
slice is a range scan. Country got a denormalized column; edition gets ProfileEditionStanding, whose
columns are NAMED IDENTICALLY to ProfileBadgeStanding's so the read layer swaps a manager instead of
branching every query. What these tests hold down is that the two stores keep agreeing.

The one genuinely surprising property, pinned twice below: editions OVERLAP. A cross-gen game qualifies
for both groups by the engine's own platform-intersection rule, so its trophies count in both and the
editions do not sum to the all-editions row. Anyone "fixing" that sum will break the boards.
"""
import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from trophies.models import ProfileBadgeStanding, ProfileEditionStanding, SeriesBadgeStanding
from trophies.services import badge_leaderboards as lb
from trophies.services.badge_xp import badge_trophy_tallies, edition_platforms, trophy_groups
from tests.factories import (
    ConceptFactory, EarnedTrophyFactory, GameFactory, GroupBadgeFactory, BadgeSeriesFactory,
    PlatformGroupFactory, ProfileFactory, StageFactory, TrophyFactory,
)

pytestmark = pytest.mark.django_db

URL = reverse('overall_badge_leaderboards')

LEGACY = ['PS3', 'PSVITA']
ULTRA = ['PS4', 'PS5']


def _editions():
    """The two real editions, live -- `active_editions` gates on a LIVE group badge, so a bare
    PlatformGroup would not appear in the picker no matter how many rows referenced it."""
    legacy = PlatformGroupFactory(key='legacy-hd', name='Legacy HD', platforms=LEGACY, sort_order=20)
    ultra = PlatformGroupFactory(key='ultra-hd', name='Ultra HD', platforms=ULTRA, sort_order=10)
    for group in (legacy, ultra):
        GroupBadgeFactory(series=BadgeSeriesFactory(), platform_group=group, is_live=True)
    return legacy, ultra


def _badge_game(platforms, slug='ed', stage_number=1):
    """A game inside a badge stage -- i.e. one whose trophies count toward the Badge Trophies board."""
    concept = ConceptFactory()
    stage = StageFactory(series_slug=slug, stage_number=stage_number)
    stage.concepts.add(concept)
    return GameFactory(concept=concept, title_platform=list(platforms))


def _earn(profile, game, tier, n=1):
    for _ in range(n):
        EarnedTrophyFactory(profile=profile, trophy=TrophyFactory(game=game, trophy_type=tier), earned=True)


def _standing(key, *, country='', **kw):
    profile = ProfileFactory(country_code=country)
    ProfileEditionStanding.objects.create(
        profile=profile, platform_group_key=key, country_code=country, **kw)
    return profile


# ---------------------------------------------------------------- the per-edition tally -----------------

def test_trophies_are_split_by_the_platforms_a_game_runs_on():
    _editions()
    profile = ProfileFactory(is_linked=True)
    _earn(profile, _badge_game(['PS5'], slug='a'), 'gold', 3)
    _earn(profile, _badge_game(['PS3'], slug='b', stage_number=2), 'bronze', 5)

    overall, by_edition = badge_trophy_tallies(profile.id)

    assert overall['trophies_total'] == 8
    assert by_edition['ultra-hd'] == {
        'trophies_gold': 3, 'trophies_bronze': 0, 'trophies_silver': 0, 'trophies_platinum': 0,
        'trophies_total': 3,
    }
    assert by_edition['legacy-hd']['trophies_bronze'] == 5
    assert by_edition['legacy-hd']['trophies_gold'] == 0


def test_a_cross_gen_game_counts_in_both_editions():
    """The property that makes the editions NOT sum to the overall row, and the one somebody will
    eventually try to "fix".

    It follows from the engine's own rule -- a game qualifies for a group if its platforms INTERSECT the
    group's -- so a PS3/PS4 release is genuinely earnable in both editions and its trophies genuinely count
    toward both. Splitting it (half each, or first-match-wins) would make an edition's trophy count
    disagree with the badges that edition awards.
    """
    _editions()
    profile = ProfileFactory(is_linked=True)
    _earn(profile, _badge_game(['PS3', 'PS4']), 'platinum', 1)

    overall, by_edition = badge_trophy_tallies(profile.id)

    assert overall['trophies_platinum'] == 1, 'the overall row double-counted a cross-gen game'
    assert by_edition['legacy-hd']['trophies_platinum'] == 1
    assert by_edition['ultra-hd']['trophies_platinum'] == 1
    summed = sum(c['trophies_total'] for c in by_edition.values())
    assert summed != overall['trophies_total'], (
        'the editions summed to the overall total, which means the overlap was lost'
    )


def test_a_game_on_no_edition_platform_counts_overall_but_in_neither_edition():
    """A badge game on a platform outside every group -- PS Vita-era oddities, or a group not yet seeded.
    It still belongs to the all-editions board, because that board is "badge games", not "editions"."""
    _editions()
    profile = ProfileFactory(is_linked=True)
    _earn(profile, _badge_game(['PSPC']), 'silver', 2)

    overall, by_edition = badge_trophy_tallies(profile.id)
    assert overall['trophies_silver'] == 2
    assert by_edition['ultra-hd']['trophies_total'] == 0
    assert by_edition['legacy-hd']['trophies_total'] == 0


def test_the_aggregate_does_not_grow_with_the_library():
    """The load-bearing performance property, and the one the design is easiest to be wrong about.

    Splitting by edition means a Python loop, which is the shape CLAUDE.md forbids -- but what it forbids is
    iterating ROWS (`for et in EarnedTrophy.objects.filter(...)`). This iterates the GROUPED AGGREGATE:
    Postgres counts, and hands back one row per (distinct platform list x tier). That set is bounded by the
    catalogue's platform vocabulary, so a whale with 250k trophies costs the same Python work as a hunter
    with 40.

    Asserted as "does not grow", not as a magic number: the bound is a property, and a per-row rewrite is
    the specific regression -- it would still read like an aggregate and would still pass the query-COUNT
    test next to it, because it is one query either way.
    """
    _editions()
    profile = ProfileFactory(is_linked=True)
    small_game = _badge_game(['PS5'], slug='sm')
    _earn(profile, small_game, 'bronze', 4)
    small = len(trophy_groups(profile.id))

    # 60 more trophies, same platform list, plus a SECOND game on that same list -- the library grows, the
    # vocabulary does not.
    _earn(profile, small_game, 'bronze', 30)
    _earn(profile, _badge_game(['PS5'], slug='sm2', stage_number=2), 'bronze', 30)
    large = len(trophy_groups(profile.id))

    assert small == large == 1, (
        f'{small} aggregate rows at 4 trophies and {large} at 64 -- this is iterating rows, not groups'
    )

    # A genuinely new platform COMBINATION is what adds a row, and each tier within it adds one more.
    _earn(profile, _badge_game(['PS3', 'PS4'], slug='cross', stage_number=3), 'gold', 12)
    assert len(trophy_groups(profile.id)) == 2, 'a new platform combination should add exactly one row'


def test_the_split_stays_one_query_however_many_editions_exist():
    """The reason this GROUPS BY title_platform rather than running a filtered count per edition. A query
    per edition looks free at two and is the whole cost model at six, and it would put the cost of seeding
    a new group on every profile's sync."""
    _editions()
    profile = ProfileFactory(is_linked=True)
    _earn(profile, _badge_game(['PS5']), 'bronze', 6)

    two = edition_platforms()
    with CaptureQueriesContext(connection) as small:
        badge_trophy_tallies(profile.id, two)

    six = dict(two)
    for i in range(4):
        six[f'future-{i}'] = frozenset([f'PS{6 + i}'])
    with CaptureQueriesContext(connection) as large:
        badge_trophy_tallies(profile.id, six)

    assert len(small.captured_queries) == len(large.captured_queries) == 1, (
        f'{len(small.captured_queries)} queries for 2 editions and {len(large.captured_queries)} for 6 -- '
        f'this must be one grouped aggregate regardless'
    )


# ---------------------------------------------------------------- the write seam ------------------------

def _evaluated(profile, series_slug, platforms, group):
    """Run a real evaluation of one series for one profile, with the base list finished on its one game."""
    from django.utils import timezone
    from trophies.models import ProfileGame, ProfileTrophyGroup, TrophyGroup
    from trophies.services.badge_apply import evaluate_and_apply

    series = BadgeSeriesFactory(series_slug=series_slug)
    gb = GroupBadgeFactory(series=series, platform_group=group, is_live=True)
    concept = ConceptFactory()
    stage = StageFactory(series_slug=series_slug, stage_number=1)
    stage.concepts.add(concept)
    game = GameFactory(concept=concept, title_platform=list(platforms))

    ProfileGame.objects.update_or_create(profile=profile, game=game, defaults={'progress': 100})
    tg, _ = TrophyGroup.objects.get_or_create(
        game=game, trophy_group_id='default', defaults={'trophy_group_name': 'Base'})
    ProfileTrophyGroup.objects.update_or_create(
        profile=profile, trophy_group=tg, defaults={'progress': 100, 'last_trophy_at': timezone.now()})

    evaluate_and_apply(profile, [gb])
    return gb


def test_the_seam_materializes_a_standing_for_the_edition_that_was_earned_in():
    legacy, ultra = _editions()
    profile = ProfileFactory(is_linked=True)
    _evaluated(profile, 'solo', ULTRA, ultra)

    standing = ProfileEditionStanding.objects.get(profile=profile, platform_group_key='ultra-hd')
    assert standing.total_xp > 0
    assert not ProfileEditionStanding.objects.filter(
        profile=profile, platform_group_key='legacy-hd', total_xp__gt=0).exists(), (
        'XP leaked into an edition the profile has nothing in'
    )


def test_per_edition_xp_sums_across_every_series_the_profile_stands_in():
    """The grand total is re-summed from ALL the profile's series rows, per edition, which is what makes a
    scoped recompute safe. A run scoped to one series only knows about that series; the row it writes is
    profile-wide."""
    legacy, ultra = _editions()
    profile = ProfileFactory(is_linked=True)
    _evaluated(profile, 'first', ULTRA, ultra)
    first_xp = ProfileEditionStanding.objects.get(
        profile=profile, platform_group_key='ultra-hd').total_xp

    _evaluated(profile, 'second', ULTRA, ultra)
    total = ProfileEditionStanding.objects.get(profile=profile, platform_group_key='ultra-hd').total_xp

    assert total == first_xp * 2, (
        f'a second series in the same edition should add to it ({first_xp} -> expected {first_xp * 2}, '
        f'got {total}); the per-edition total is being written from the scoped call rather than re-summed'
    )
    assert total == ProfileBadgeStanding.objects.get(profile=profile).total_xp, (
        'with one edition in play, its total must equal the all-editions total'
    )


def test_a_scoped_recompute_does_not_forget_the_other_series_edition_xp():
    """The invariant stated in `recompute_standing`, exercised. Re-evaluating ONE series must leave the
    other's contribution to the edition intact -- summing only the call's own results would silently halve
    a hunter's edition standing every time a single series was re-run."""
    legacy, ultra = _editions()
    profile = ProfileFactory(is_linked=True)
    _evaluated(profile, 'alpha', ULTRA, ultra)
    gb = _evaluated(profile, 'beta', ULTRA, ultra)
    both = ProfileEditionStanding.objects.get(profile=profile, platform_group_key='ultra-hd').total_xp

    from trophies.services.badge_apply import evaluate_and_apply
    evaluate_and_apply(profile, [gb])       # re-run ONE series, the way `evaluate_badges --series` does

    after = ProfileEditionStanding.objects.get(profile=profile, platform_group_key='ultra-hd').total_xp
    assert after == both, f'a scoped re-run dropped the other series: {both} -> {after}'


def test_the_seam_keeps_an_edition_row_for_trophies_without_any_xp():
    """A hunter can own trophies in an edition's games without clearing a gating stage there. That is a
    real state and it belongs on Badge Trophies, so `_write_edition_standings` keeps the row when EITHER
    xp or trophies is positive.

    This goes through the WRITE SEAM. `test_trophies_without_points_stay_on_the_trophies_board_only`
    names the same rule but builds its row by hand and only exercises the read layer, so deleting the
    trophy half of the condition (`if not xp: continue`) passed the entire 2,468-test suite. The
    population the rule exists to include would have silently vanished from every edition board.
    """
    legacy, ultra = _editions()
    profile = ProfileFactory(is_linked=True)

    # Earns XP in ULTRA only...
    _evaluated(profile, 'ultraonly', ULTRA, ultra)
    # ...but holds trophies in a LEGACY badge game, with no stage cleared there.
    _earn(profile, _badge_game(LEGACY, slug='legacygame', stage_number=1), 'gold', 4)

    # Re-run the seam so the trophy tally is picked up.
    from trophies.services.badge_apply import evaluate_and_apply
    from trophies.models import GroupBadge
    evaluate_and_apply(profile, list(GroupBadge.objects.filter(is_live=True)))

    row = ProfileEditionStanding.objects.filter(
        profile=profile, platform_group_key='legacy-hd').first()
    assert row is not None, (
        'the Legacy HD standing was dropped -- a hunter with badge-game trophies but no cleared gating '
        'stage there has vanished from that edition board'
    )
    assert row.total_xp == 0 and row.trophies_gold == 4
    assert [r[0] for r in lb.badge_trophy_rows(edition='legacy-hd')] == [profile.id]
    assert lb.xp_rows(edition='legacy-hd') == [], 'zero points should not put them on Badge Points'


def test_the_seam_drops_an_edition_key_that_falls_out_of_a_series():
    """`group_xp` is written as a full REPLACE, so an edition the hunter no longer stands in disappears
    from the blob. Nothing asserted that: changing `_upsert` to MERGE -- a natural-looking fix for a
    JSONField -- would inflate per-edition XP permanently, only for hunters whose badge set changed, and
    nothing in the UI could falsify it."""
    from django.utils import timezone
    from trophies.models import Game, ProfileGame, ProfileTrophyGroup, TrophyGroup
    from trophies.services.badge_apply import evaluate_and_apply

    legacy, ultra = _editions()
    profile = ProfileFactory(is_linked=True)

    # One series, one stage, two games -- one per edition. Both editions are earnable and the hunter
    # clears both, so `group_xp` carries two keys.
    series = BadgeSeriesFactory(series_slug='shrink')
    gbs = [GroupBadgeFactory(series=series, platform_group=g, is_live=True) for g in (ultra, legacy)]
    stage = StageFactory(series_slug='shrink', stage_number=1)
    games = {}
    for name, platforms in (('ultra', ULTRA), ('legacy', LEGACY)):
        concept = ConceptFactory()
        stage.concepts.add(concept)
        games[name] = GameFactory(concept=concept, title_platform=list(platforms))
        ProfileGame.objects.update_or_create(
            profile=profile, game=games[name], defaults={'progress': 100})
        tg, _ = TrophyGroup.objects.get_or_create(
            game=games[name], trophy_group_id='default', defaults={'trophy_group_name': 'Base'})
        ProfileTrophyGroup.objects.update_or_create(
            profile=profile, trophy_group=tg, defaults={'progress': 100, 'last_trophy_at': timezone.now()})

    evaluate_and_apply(profile, gbs)
    blob = SeriesBadgeStanding.objects.get(profile=profile, series_slug='shrink').group_xp
    assert set(blob) == {'ultra-hd', 'legacy-hd'}, f'fixture is wrong -- expected both editions: {blob}'

    # The Legacy game's platform data is corrected to current-gen, so NO game in the stage qualifies for
    # Legacy HD any more: that edition's gating_count drops to 0 and it stops being earnable here.
    Game.objects.filter(pk=games['legacy'].pk).update(title_platform=list(ULTRA))
    evaluate_and_apply(profile, gbs)

    after = SeriesBadgeStanding.objects.get(profile=profile, series_slug='shrink').group_xp
    assert 'legacy-hd' not in after, (
        f'group_xp kept a stale edition key: {after}. It is a REPLACE, not a merge.'
    )
    assert 'ultra-hd' in after, 'the surviving edition was dropped along with the stale one'


def test_an_edition_standing_is_removed_when_the_profile_drops_out_of_it():
    """Recompute-from-scratch cuts both ways. A stale row would keep a hunter on an edition board after the
    badge that put them there was retired -- and it would look exactly like a correct row."""
    legacy, ultra = _editions()
    profile = ProfileFactory(is_linked=True)
    _evaluated(profile, 'gone', ULTRA, ultra)
    assert ProfileEditionStanding.objects.filter(profile=profile).exists()

    SeriesBadgeStanding.objects.filter(profile=profile).delete()
    from trophies.services.badge_xp import recompute_standing
    recompute_standing(profile.id, {}, [])

    assert not ProfileEditionStanding.objects.filter(profile=profile).exists(), (
        'a hunter with no badge standing left kept their edition rows'
    )


# ---------------------------------------------------------------- the read layer ------------------------

def test_the_edition_boards_read_the_edition_store():
    _standing('ultra-hd', total_xp=100, trophies_platinum=1, trophies_total=10)
    top = _standing('ultra-hd', total_xp=900, trophies_platinum=9, trophies_total=90)
    legacy_only = _standing('legacy-hd', total_xp=5000, trophies_platinum=50, trophies_total=500)

    assert lb.xp_rows(edition='ultra-hd')[0][0] == top.id
    assert legacy_only.id not in [r[0] for r in lb.xp_rows(edition='ultra-hd')], (
        'a Legacy HD hunter appeared on the Ultra HD board'
    )
    assert lb.badge_trophy_rows(edition='legacy-hd')[0][0] == legacy_only.id


def test_an_edition_rank_is_measured_against_that_edition():
    """The subtle one. Reading the viewer's FIGURE from the all-editions row and counting it against an
    edition's population would produce a rank that is wrong and entirely plausible."""
    _standing('ultra-hd', total_xp=900)
    mine = _standing('ultra-hd', total_xp=100)
    ProfileBadgeStanding.objects.create(profile=mine, total_xp=99999)   # huge OVERALL, small in-edition

    assert lb.xp_rank(mine.id, edition='ultra-hd') == 2, 'the rank was taken from the wrong store'
    assert lb.xp_rank(mine.id) == 1, 'the all-editions rank should still read the all-editions row'


def test_edition_and_country_compose():
    """Both are filters and they stack -- which is why the edition indexes carry country in the middle
    rather than being edition-only."""
    ca_top = _standing('ultra-hd', country='CA', total_xp=500)
    _standing('ultra-hd', country='CA', total_xp=100)
    gb = _standing('ultra-hd', country='GB', total_xp=9000)

    rows = [r[0] for r in lb.xp_rows(country='CA', edition='ultra-hd')]
    assert rows[0] == ca_top.id and gb.id not in rows
    assert lb.xp_rank(ca_top.id, country='CA', edition='ultra-hd') == 1
    assert lb.xp_rank(ca_top.id, edition='ultra-hd') == 2, 'the unsliced edition board lost the GB hunter'


def test_an_unknown_edition_reads_nothing_rather_than_everything():
    """Falling back to the all-editions store would show the global board under an edition heading -- a
    wrong answer that looks like a right one. The VIEW validates the key first, so this path only runs on a
    bug, and it should be loud rather than plausible.

    The all-editions row is POPULATED on purpose. Without it the fallback would return an empty list too,
    and this assertion would pass against exactly the behaviour it exists to forbid -- which is what
    mutation testing caught it doing.
    """
    someone = _standing('ultra-hd', total_xp=100)
    ProfileBadgeStanding.objects.create(profile=someone, total_xp=100, trophies_total=10)

    assert lb.xp_rows() != [], 'fixture is wrong -- the all-editions board must have something to fall back TO'
    assert lb.xp_rows(edition='no-such-edition') == []
    assert lb.badge_trophy_rows(edition='no-such-edition') == []


def test_trophies_without_points_stay_on_the_trophies_board_only():
    """A hunter can hold trophies in an edition's games without clearing a gating stage in it. That belongs
    on Badge Trophies and puts nothing on Badge Points, so the row is kept and each board applies its own
    membership rule."""
    someone = _standing('ultra-hd', total_xp=0, trophies_platinum=2, trophies_total=40)

    assert [r[0] for r in lb.badge_trophy_rows(edition='ultra-hd')] == [someone.id]
    assert lb.xp_rows(edition='ultra-hd') == [], 'a zero-point standing was listed on Badge Points'
    assert lb.xp_rank(someone.id, edition='ultra-hd') is None
    assert lb.badge_trophy_rank(someone.id, edition='ultra-hd') == 1


# ---------------------------------------------------------------- the page ------------------------------

def test_the_edition_picker_renders_on_the_badge_boards(client):
    _editions()
    body = client.get(URL, {'tab': 'trophies'}).content.decode()
    assert 'name="edition"' in body, 'the edition filter is missing from Badge Trophies'
    assert 'Ultra HD' in body and 'Legacy HD' in body

    points = client.get(URL, {'tab': 'points'}).content.decode()
    assert 'name="edition"' in points


def test_the_edition_picker_is_absent_from_career_xp(client):
    """Career XP is the jobs economy and has no platform editions. A control that renders but changes
    nothing is worse than one that is not there -- it promises a slice that does not exist."""
    _editions()
    # The country picker must have a reason to render, or the edition control is "absent" only because the
    # whole form is -- the test would pass against the wrong cause.
    #
    # It has to be a CAREER standing specifically: `active_countries()` reads ProfileBadgeStanding UNION
    # ProfileCareerStanding and never ProfileEditionStanding, so an edition row alone puts no country in
    # the picker. Two earlier lines here set `.country` on an unsaved attribute and created an unranked
    # profile, neither of which did anything -- the test passed for a reason its fixture did not express.
    from trophies.models import ProfileCareerStanding
    ProfileCareerStanding.objects.create(
        profile=ProfileFactory(country_code='CA', country='Canada'), total_xp=50, country_code='CA')

    body = client.get(URL, {'tab': 'career'}).content.decode()
    assert 'name="country"' in body, 'country should still be offered on Career'
    assert 'name="edition"' not in body, 'the edition filter rendered on a board it cannot slice'


def test_selecting_an_edition_slices_the_wall(client):
    _editions()
    ultra = _standing('ultra-hd', total_xp=500, trophies_platinum=5, trophies_total=50)
    legacy = _standing('legacy-hd', total_xp=900, trophies_platinum=9, trophies_total=90)
    ultra.display_psn_username = 'UltraHunter'
    legacy.display_psn_username = 'LegacyHunter'
    ultra.save(); legacy.save()

    body = client.get(URL, {'tab': 'points', 'edition': 'ultra-hd'}).content.decode()
    assert 'UltraHunter' in body and 'LegacyHunter' not in body


def test_an_unknown_edition_falls_back_to_all_editions_on_the_page(client):
    """Same reasoning the country filter uses: an unrecognised key would render an empty board, which reads
    as "nobody plays that edition" rather than "that is not an edition"."""
    _editions()
    somebody = _standing('ultra-hd', total_xp=100)
    ProfileBadgeStanding.objects.create(profile=somebody, total_xp=100, trophies_total=10)
    somebody.display_psn_username = 'Findable'
    somebody.save()

    body = client.get(URL, {'tab': 'points', 'edition': 'nonsense'}).content.decode()
    assert 'Findable' in body, 'an unknown edition emptied the board instead of falling back'


def test_the_edition_travels_between_the_badge_boards_but_not_onto_career(client):
    """The tab strip is built from the VALIDATED filters, so switching Badge Trophies -> Badge Points keeps
    the edition and switching to Career drops it. If it rode along to Career, the header's "your standing"
    rank -- computed unsliced there -- would link to a board sliced differently."""
    _editions()
    badge_board = client.get(URL, {'tab': 'trophies', 'edition': 'ultra-hd'}).content.decode()
    assert 'href="?tab=points&amp;edition=ultra-hd"' in badge_board, (
        'the edition did not follow the reader to the other badge board'
    )
    assert 'href="?tab=career"' in badge_board, (
        'the Career link is carrying a filter that board ignores'
    )

    # Scoped to the board links. A bare `'edition=ultra-hd' not in career` also matches the canonical
    # <link>, which reflects the requested URL by design -- so the assertion would fail on something that
    # is not a board link and has nothing to do with this rule.
    career = client.get(URL, {'tab': 'career', 'edition': 'ultra-hd'}).content.decode()
    assert 'href="?tab=trophies"' in career and 'href="?tab=points"' in career, (
        'an edition the Career board ignores is still being carried back to the badge boards'
    )


def test_the_empty_edition_board_names_the_edition_that_emptied_it(client):
    """"This board is still empty" under an active slice is a claim about the board that the slice is
    responsible for.

    Asserted on the EMPTY-STATE SENTENCE, not on the edition name appearing somewhere in the body. The
    first version checked `'Legacy HD' in body`, which the picker's own `<option>` satisfies on every
    badge-board request -- blanking the name out of the empty state left that assertion green. The exact
    substring class this codebase keeps getting bitten by, written into the test named for the behaviour.
    """
    _editions()
    body = client.get(URL, {'tab': 'points', 'edition': 'legacy-hd'}).content.decode()

    assert 'Nobody is on the Legacy HD board yet.' in body, (
        'the empty state does not name the edition responsible for the emptiness'
    )
    assert 'Show every edition' in body
    assert 'still empty' not in body, 'the empty state blamed the board rather than the filter'


def test_badges_held_is_materialized_per_edition_by_the_write_seam():
    """The Badge Points board's supporting figure, sliced the same way the board is.

    Editions do NOT overlap here, unlike the trophy tally: a group badge belongs to exactly one platform
    group, so the per-edition counts sum to the total. That is a different property from the trophies, and
    getting them confused is how a "fix" to one breaks the other.
    """
    legacy, ultra = _editions()
    profile = ProfileFactory(is_linked=True)
    _evaluated(profile, 'ua', ULTRA, ultra)
    _evaluated(profile, 'ub', ULTRA, ultra)
    _evaluated(profile, 'la', LEGACY, legacy)

    overall = ProfileBadgeStanding.objects.get(profile=profile)
    ultra_row = ProfileEditionStanding.objects.get(profile=profile, platform_group_key='ultra-hd')
    legacy_row = ProfileEditionStanding.objects.get(profile=profile, platform_group_key='legacy-hd')

    assert overall.badges_held == 3, f'expected 3 badges held, got {overall.badges_held}'
    assert ultra_row.badges_held == 2
    assert legacy_row.badges_held == 1
    assert ultra_row.badges_held + legacy_row.badges_held == overall.badges_held, (
        'the editions should sum to the total -- a group badge belongs to exactly one platform group'
    )


def test_the_points_board_shows_badges_held_and_slices_it_with_the_board(client):
    """The figure has to follow the slice. A global badge count beside an edition-sliced points total is
    the header-tally category error one column over."""
    legacy, ultra = _editions()
    profile = ProfileFactory(is_linked=True, display_psn_username='Slicer')
    _evaluated(profile, 'ua', ULTRA, ultra)
    _evaluated(profile, 'ub', ULTRA, ultra)
    _evaluated(profile, 'la', LEGACY, legacy)

    everywhere = lb.xp_rows()
    assert everywhere[0][2] == 3, f'the all-editions row should carry 3 badges: {everywhere}'

    ultra_only = lb.xp_rows(edition='ultra-hd')
    assert ultra_only[0][2] == 2, f'the Ultra HD row should carry 2 badges: {ultra_only}'

    body = client.get(URL, {'tab': 'points'}).content.decode()
    assert 'badges' in body, 'the Badge Points board is not labelling its supporting figure'


def test_a_zero_badge_hunter_still_renders_the_figure_rather_than_omitting_it():
    """`secondary` is gated on `is not None`, not on truthiness. A hunter with points but no completed badge
    (all partial progress) must show "0 badges", not a missing cell -- an absent figure reads as "this board
    has no second number", which is a statement about the BOARD."""
    someone = _standing('ultra-hd', total_xp=500, badges_held=0)
    rows = lb.xp_rows(edition='ultra-hd')
    assert rows == [(someone.id, 500, 0)], rows
    assert rows[0][2] is not None, 'a zero badge count must be 0, never None'


def test_each_escape_hatch_clears_only_the_filter_it_names(client):
    """With both filters on and the board empty, the empty state offers two ways out. Each must clear the
    one it names and KEEP the other -- otherwise "Show every edition" quietly drops your country too.

    They were assembled inline in the template, a second copy of the rule `_href` owns, and untested. The
    same shape as the bug that made the Career tab link carry an edition it ignores.
    """
    from trophies.models import ProfileCareerStanding

    _editions()
    ProfileCareerStanding.objects.create(
        profile=ProfileFactory(country_code='CA', country='Canada'), total_xp=10, country_code='CA')

    resp = client.get(URL, {'tab': 'points', 'country': 'CA', 'edition': 'ultra-hd'})
    body = resp.content.decode()
    assert 'Show every country' in body and 'Show every edition' in body, 'fixture did not empty the board'

    assert resp.context['clear_country_href'] == '?tab=points&edition=ultra-hd', (
        'clearing the country dropped the edition too'
    )
    assert resp.context['clear_edition_href'] == '?tab=points&country=CA', (
        'clearing the edition dropped the country too'
    )


def test_the_edition_board_costs_the_same_number_of_queries_as_the_global_one(client):
    """A slice must not become a different cost model. Same two reads plus the same fixed overhead --
    a store swap, not an extra join."""
    _editions()
    for i in range(5):
        p = _standing('ultra-hd', total_xp=100 * i, trophies_platinum=i, trophies_total=i * 10)
        ProfileBadgeStanding.objects.create(profile=p, total_xp=100 * i, trophies_total=i * 10)

    with CaptureQueriesContext(connection) as everywhere:
        client.get(URL, {'tab': 'points'})
    with CaptureQueriesContext(connection) as sliced:
        client.get(URL, {'tab': 'points', 'edition': 'ultra-hd'})

    assert len(sliced.captured_queries) == len(everywhere.captured_queries), (
        f'{len(everywhere.captured_queries)} queries unsliced but {len(sliced.captured_queries)} sliced'
    )


def test_the_edition_indexes_lead_with_the_edition():
    """A composite index only range-scans when its leading columns are the ones filtered. Edition is always
    filtered on these boards, so it has to come first -- and country sits between it and the sort key so
    the combined slice is served too, rather than filtering over a board-ordered scan."""
    idx = {i.name: i.fields for i in ProfileEditionStanding._meta.indexes}

    assert idx.get('pes_ed_xp_idx') == ['platform_group_key', '-total_xp']
    assert idx.get('pes_ed_troph_idx') == [
        'platform_group_key', '-trophies_platinum', '-trophies_total']
    assert idx.get('pes_ed_cc_xp_idx') == ['platform_group_key', 'country_code', '-total_xp']
    assert idx.get('pes_ed_cc_troph_idx') == [
        'platform_group_key', 'country_code', '-trophies_platinum', '-trophies_total'], (
        'the combined edition+country trophy index must be (edition, country, ...board order)'
    )
