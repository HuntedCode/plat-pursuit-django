"""The rebuilt badge engine on the SYNC path (cutover step 5, parts A + C).

Until this landed, `UserGroupBadge` and every standing behind the leaderboards were written ONLY by the
`evaluate_badges` management command, which is on no schedule. The tables were read live by Home, the
Collection, milestones and all four leaderboard surfaces -- so everything the rebuild displays sat at
whatever a manual run last produced, and nothing about the page said so.

`evaluate_for_touched_games` is the seam sync calls. What these tests hold down is the two properties that
would break quietly:

  - the SCOPE is by series, so a touched game brings EVERY edition of its series with it
  - the Discord announcement is opt-in, so a full backfill does not become a webhook storm

See docs/design/rebuild/badge-backend-rebuild.md §6.
"""
from unittest.mock import patch

import pytest
from django.utils import timezone

from trophies.models import (
    GroupBadge, ProfileGame, ProfileTrophyGroup, SeriesBadgeStanding, TrophyGroup, UserGroupBadge,
)
from trophies.services.badge_apply import evaluate_for_touched_games
from tests.factories import (
    BadgeSeriesFactory, ConceptFactory, GameFactory, GroupBadgeFactory, PlatformGroupFactory,
    ProfileFactory, StageFactory,
)

pytestmark = pytest.mark.django_db

ULTRA = ['PS4', 'PS5']
LEGACY = ['PS3', 'PSVITA']


def _editions():
    ultra = PlatformGroupFactory(key='ultra-hd', name='Ultra HD', platforms=ULTRA, sort_order=10)
    legacy = PlatformGroupFactory(key='legacy-hd', name='Legacy HD', platforms=LEGACY, sort_order=20)
    return ultra, legacy


def _series_with_both_editions(slug, platforms_per_game):
    """A series with one stage, one game per platform set, and a live badge for BOTH editions."""
    series = BadgeSeriesFactory(series_slug=slug)
    ultra, legacy = _editions()
    badges = [
        GroupBadgeFactory(series=series, platform_group=ultra, is_live=True),
        GroupBadgeFactory(series=series, platform_group=legacy, is_live=True),
    ]
    stage = StageFactory(series_slug=slug, stage_number=1)
    games = []
    for platforms in platforms_per_game:
        concept = ConceptFactory()
        stage.concepts.add(concept)
        games.append(GameFactory(concept=concept, title_platform=list(platforms)))
    return series, badges, games


def _finish(profile, game):
    """Complete the game's base list -- the bar a gating stage measures."""
    pg, _ = ProfileGame.objects.update_or_create(
        profile=profile, game=game, defaults={'progress': 100, 'has_plat': True})
    tg, _ = TrophyGroup.objects.get_or_create(
        game=game, trophy_group_id='default', defaults={'trophy_group_name': 'Base'})
    ProfileTrophyGroup.objects.update_or_create(
        profile=profile, trophy_group=tg, defaults={'progress': 100, 'last_trophy_at': timezone.now()})
    return pg


# ------------------------------------------------------------------ scope ---------------------------------

def test_touching_one_series_does_not_evaluate_another():
    """Scope is the whole point of this seam: a sync touches a handful of games, and the catalogue is the
    entire badge system. An unscoped call would put the cost of every live badge on every hunter who plays
    anything -- and would award badges for series they did not touch this sync."""
    _series_with_both_editions('touched', [ULTRA])
    _series_with_both_editions('untouched', [ULTRA])

    profile = ProfileFactory(is_linked=True)
    # Qualifies for BOTH series' badges, but only one is touched.
    for slug in ('touched', 'untouched'):
        _finish(profile, _game_in(slug))

    touched_pg = ProfileGame.objects.get(profile=profile, game=_game_in('touched'))
    result = evaluate_for_touched_games(profile, [touched_pg.id], notify=False)

    assert result['awarded'], 'touching a badge game awarded nothing'
    assert set(
        UserGroupBadge.objects.filter(profile=profile)
        .values_list('group_badge__series__series_slug', flat=True)
    ) == {'touched'}, 'an untouched series was evaluated'


def _game_in(slug):
    """The game hanging off `slug`'s stage."""
    from trophies.models import Game, Stage
    stage = Stage.objects.get(series_slug=slug)
    concept_ids = stage.concepts.values_list('id', flat=True)
    return Game.objects.filter(concept_id__in=list(concept_ids)).first()


def test_the_scope_carries_every_edition_of_a_touched_series():
    """The load-bearing one, and the failure is silent.

    `recompute_standing` REPLACES a series' standing from only the editions it is handed -- xp is SUMMED
    over them and `group_progress` is keyed per edition. A scope that caught Ultra HD but not Legacy HD
    would therefore undercount that series' XP and drop the sibling edition's progress entirely, on a
    hunter who did nothing wrong. Scoping by SERIES makes that impossible by construction.
    """
    _series_with_both_editions('both', [ULTRA, LEGACY])
    profile = ProfileFactory(is_linked=True)

    from trophies.models import Game, Stage
    stage = Stage.objects.get(series_slug='both')
    games = list(Game.objects.filter(concept_id__in=list(stage.concepts.values_list('id', flat=True))))
    for g in games:
        _finish(profile, g)

    # Touch ONLY the Ultra HD game.
    ultra_game = next(g for g in games if 'PS5' in g.title_platform)
    evaluate_for_touched_games(profile, [ProfileGame.objects.get(
        profile=profile, game=ultra_game).id], notify=False)

    standing = SeriesBadgeStanding.objects.get(profile=profile, series_slug='both')
    assert set(standing.group_progress) == {'ultra-hd', 'legacy-hd'}, (
        f'the scope dropped an edition: {standing.group_progress}. Scoping by BADGE rather than by SERIES '
        f'silently halves this hunter\'s standing.'
    )
    assert set(standing.group_xp) == {'ultra-hd', 'legacy-hd'}


def test_nothing_happens_for_games_in_no_badge():
    profile = ProfileFactory(is_linked=True)
    pg = _finish(profile, GameFactory(concept=ConceptFactory(), title_platform=ULTRA))

    assert evaluate_for_touched_games(profile, [pg.id], notify=False) == {
        'awarded': [], 'revoked': [], 'updated': []}
    assert not UserGroupBadge.objects.filter(profile=profile).exists()


def test_an_empty_touch_list_is_a_no_op():
    """A sync that touched nothing must not fall through to 'all live badges'."""
    _series_with_both_editions('quiet', [ULTRA])
    profile = ProfileFactory(is_linked=True)
    assert evaluate_for_touched_games(profile, [], notify=False)['awarded'] == []


def test_the_sync_cost_does_not_grow_with_the_catalogue():
    """The reason this seam is scoped at all, pinned as a property rather than a number.

    `build_catalog` prefetches the stage graph for the badges it is handed. Unscoped, every sync would pay
    for the entire badge catalogue -- and that cost would grow every time a badge is authored, on every
    hunter who plays anything, which is the sort of regression that arrives as "sync got slower" months
    later with no obvious cause.
    """
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    _series_with_both_editions('touched', [ULTRA])
    profile = ProfileFactory(is_linked=True)
    pg = _finish(profile, _game_in('touched'))

    # Reach steady state FIRST. The initial run also does the award writes (create the hold, grant the
    # title, upsert the standings), so measuring it against a later no-op diff compares two different
    # states -- which is what this test did on its first draft, and it read as the scoping being broken.
    evaluate_for_touched_games(profile, [pg.id], notify=False)

    with CaptureQueriesContext(connection) as small:
        evaluate_for_touched_games(profile, [pg.id], notify=False)

    # Twelve more live series, none of them touched.
    for i in range(12):
        _series_with_both_editions(f'other-{i}', [ULTRA])

    with CaptureQueriesContext(connection) as large:
        evaluate_for_touched_games(profile, [pg.id], notify=False)

    assert len(large.captured_queries) == len(small.captured_queries), (
        f'{len(small.captured_queries)} queries with 1 series in the catalogue but '
        f'{len(large.captured_queries)} with 13 -- the sync seam is not scoped'
    )


# ------------------------------------------------------------------ announcements -------------------------

@patch('trophies.discord_utils.discord_notifications.queue_webhook_send')
def test_the_sync_path_announces_once_for_the_whole_run(mock_send):
    """A hunter finishing both editions of a series has done ONE thing. Announcing from inside
    `apply_changes` would ping them once per badge."""
    _series_with_both_editions('both', [ULTRA, LEGACY])
    profile = ProfileFactory(is_linked=True, is_discord_verified=True, discord_id='123')

    from trophies.models import Game, Stage
    stage = Stage.objects.get(series_slug='both')
    games = list(Game.objects.filter(concept_id__in=list(stage.concepts.values_list('id', flat=True))))
    pgs = [_finish(profile, g) for g in games]

    result = evaluate_for_touched_games(profile, [pg.id for pg in pgs], notify=True)

    assert len(result['awarded']) == 2, 'fixture is wrong -- expected both editions to be earned'
    assert mock_send.call_count == 1, (
        f'{mock_send.call_count} Discord sends for one run; it must be one consolidated embed'
    )
    body = str(mock_send.call_args)
    assert 'Ultra HD' in body and 'Legacy HD' in body, (
        'the embed does not name the editions -- two distinct badges would read as a repeated line'
    )


@patch('trophies.discord_utils.discord_notifications.queue_webhook_send')
def test_a_backfill_run_announces_nothing(mock_send):
    """`evaluate_badges --all` calls `evaluate_and_apply` once per profile across the population. A default
    of notify=True would turn a backfill into tens of thousands of webhook sends, at the moment an operator
    is least watching for it."""
    from trophies.services.badge_apply import evaluate_and_apply

    _series_with_both_editions('quiet', [ULTRA])
    profile = ProfileFactory(is_linked=True, is_discord_verified=True, discord_id='123')
    _finish(profile, _game_in('quiet'))

    result = evaluate_and_apply(profile)          # no notify= -- the default is what is being tested
    assert result['awarded'], 'fixture is wrong -- expected an award'
    assert mock_send.call_count == 0, 'the default path announced to Discord'


@patch('trophies.discord_utils.discord_notifications.queue_webhook_send')
def test_an_unlinked_hunter_is_not_announced(mock_send):
    _series_with_both_editions('quiet', [ULTRA])
    profile = ProfileFactory(is_linked=True)      # no Discord
    _finish(profile, _game_in('quiet'))

    evaluate_for_touched_games(profile, list(
        ProfileGame.objects.filter(profile=profile).values_list('id', flat=True)), notify=True)
    assert mock_send.call_count == 0


@patch('trophies.discord_utils.discord_notifications.send_group_badges_earned_notification',
       side_effect=RuntimeError('discord is down'))
def test_a_failed_announcement_never_loses_the_badge(mock_send):
    """The badges are already written by the time the announcement runs. Discord being down must not undo
    a hunter's earn, and must not propagate into the sync."""
    _series_with_both_editions('quiet', [ULTRA])
    profile = ProfileFactory(is_linked=True, is_discord_verified=True, discord_id='123')
    _finish(profile, _game_in('quiet'))

    result = evaluate_for_touched_games(profile, list(
        ProfileGame.objects.filter(profile=profile).values_list('id', flat=True)), notify=True)

    assert result['awarded'], 'the award was rolled back by a failed announcement'
    assert UserGroupBadge.objects.filter(profile=profile).exists()
