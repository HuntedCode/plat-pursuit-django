"""Platform-EDITION slicing on the two badge boards (2026-08).

Legacy HD and Ultra HD are different games -- the XP model says so outright, accruing XP per GROUP BADGE
rather than per series -- so "who leads Legacy HD" is a real question the all-editions board cannot answer.

The design follows the call country already made: a filter, not a board, backed by a store shaped so the
slice is a range scan. Country got a denormalized column; edition gets ProfileEditionStanding, whose
columns are NAMED IDENTICALLY to ProfileBadgeStanding's so the read layer swaps a manager instead of
branching every query. What these tests hold down is that the two stores keep agreeing.

Editions do NOT overlap for the figures that remain: a group badge belongs to exactly one platform group,
so per-edition XP and badges-held sum to the all-editions totals. (Per-edition TROPHY counts, which DID
overlap because a cross-gen game qualifies for both groups, were removed with the Badge Trophies board in
2026-08 -- they were the only thing needing a full-library aggregate in the write seam.)
"""
import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from trophies.models import ProfileBadgeStanding, ProfileEditionStanding, SeriesBadgeStanding
from trophies.services import badge_leaderboards as lb
from tests.factories import (
    ConceptFactory, GameFactory, GroupBadgeFactory, BadgeSeriesFactory,
    PlatformGroupFactory, ProfileFactory, StageFactory,
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


def _standing(key, *, country='', **kw):
    profile = ProfileFactory(country_code=country)
    ProfileEditionStanding.objects.create(
        profile=profile, platform_group_key=key, country_code=country, **kw)
    return profile


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


def test_the_seam_writes_no_row_for_an_edition_the_hunter_stands_in_nothing():
    """A row exists only where the hunter has something. `_write_edition_standings` derives its edition set
    from what they HAVE -- xp keys union badge keys -- rather than from the live PlatformGroup table, so an
    edition they have never touched is absent rather than present-and-zero.

    That matters because the boards apply `> 0` as their membership rule: a wall of zero rows would be
    filtered out of the rows and counted into the paginator, which is the count/rows mismatch this section
    keeps rediscovering.
    """
    legacy, ultra = _editions()
    profile = ProfileFactory(is_linked=True)
    _evaluated(profile, 'ultraonly', ULTRA, ultra)

    assert ProfileEditionStanding.objects.filter(
        profile=profile, platform_group_key='ultra-hd').exists()
    assert not ProfileEditionStanding.objects.filter(
        profile=profile, platform_group_key='legacy-hd').exists(), (
        'an edition the hunter stands in nothing was given a row'
    )


def test_badges_held_counts_only_live_badges_so_it_agrees_with_the_points_beside_it():
    """XP is summed over the badges the evaluation was scoped to, which is `is_live=True`. If the badge
    count were taken over every held row the two figures in one row would be counting different sets --
    a curator smoke-testing an unreleased badge against a real profile would leave that hunter permanently
    reading more badges than their points account for."""
    from trophies.models import GroupBadge, UserGroupBadge

    legacy, ultra = _editions()
    profile = ProfileFactory(is_linked=True)
    _evaluated(profile, 'shipped', ULTRA, ultra)

    row = ProfileEditionStanding.objects.get(profile=profile, platform_group_key='ultra-hd')
    assert row.badges_held == 1 and row.total_xp > 0

    # A dormant badge the hunter somehow holds (an authoring smoke-test) must not inflate the count.
    dormant = GroupBadgeFactory(series=BadgeSeriesFactory(series_slug='unreleased'),
                                platform_group=ultra, is_live=False)
    UserGroupBadge.objects.create(profile=profile, group_badge=dormant)

    from trophies.services.badge_apply import evaluate_and_apply
    evaluate_and_apply(profile, list(GroupBadge.objects.filter(is_live=True)))

    row.refresh_from_db()
    assert row.badges_held == 1, (
        f'badges_held is {row.badges_held} -- a dormant badge is being counted beside points it '
        f'contributed nothing to'
    )


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
    _standing('ultra-hd', total_xp=100)
    top = _standing('ultra-hd', total_xp=900)
    legacy_only = _standing('legacy-hd', total_xp=5000)

    assert lb.xp_rows(edition='ultra-hd')[0][0] == top.id
    assert legacy_only.id not in [r[0] for r in lb.xp_rows(edition='ultra-hd')], (
        'a Legacy HD hunter appeared on the Ultra HD board'
    )
    assert lb.xp_rows(edition='legacy-hd')[0][0] == legacy_only.id


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
    ProfileBadgeStanding.objects.create(profile=someone, total_xp=100)

    assert lb.xp_rows() != [], 'fixture is wrong -- the all-editions board must have something to fall back TO'
    assert lb.xp_rows(edition='no-such-edition') == []


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
        p = _standing('ultra-hd', total_xp=100 * i)
        ProfileBadgeStanding.objects.create(profile=p, total_xp=100 * i)

    # Warm the picker caches (viewer-independent, hour-TTL) so the first measurement is not paying
    # cold-start costs the second one skips. The property here is that SLICING is free, not that a cold
    # page equals a warm one.
    client.get(URL, {'tab': 'points'})
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
    assert idx.get('pes_ed_cc_xp_idx') == ['platform_group_key', 'country_code', '-total_xp'], (
        'the combined edition+country index must be (edition, country, ...board order)'
    )
    assert 'pes_ed_troph_idx' not in idx and 'pes_ed_cc_troph_idx' not in idx, (
        'the per-edition TROPHY indexes are back; those columns were removed with the Badge Trophies board'
    )
