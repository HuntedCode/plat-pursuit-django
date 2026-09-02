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


def test_a_game_qualifying_only_through_a_bundle_is_still_evaluated():
    """A Stage reaches its games two ways -- `concepts`, and `concept_bundles -> concepts` -- and
    `ConceptBundle`'s membership rule is that a concept must NOT appear in both. They are disjoint by
    construction, so a derivation matching only `concepts` does not occasionally miss a bundled game: it
    misses EVERY one of them.

    The failure is silent end to end. The hunter finishes the game, sync finds no stage, no series, and
    returns early; nothing is awarded and nothing is logged. `build_catalog` walks both paths, so the
    engine would have evaluated it correctly if only it had been asked.

    (The legacy `check_profile_badges` has this same gap at badge_service.py:230 and :892 -- it is where
    this derivation was copied from. It retires with 5b, but it means bundled badges have been
    under-evaluated on the incremental path all along, and only a manual `evaluate_badges --series` or
    `refresh_badge_series` run would have caught them up.)
    """
    from trophies.models import Stage
    from tests.factories import ConceptBundleFactory

    series = BadgeSeriesFactory(series_slug='bundled')
    ultra, _ = _editions()
    GroupBadgeFactory(series=series, platform_group=ultra, is_live=True)
    stage = StageFactory(series_slug='bundled', stage_number=1)

    # The qualifying concept hangs off a BUNDLE on the stage, never off stage.concepts.
    bundle = ConceptBundleFactory(stage=stage)
    concept = ConceptFactory()
    bundle.concepts.add(concept)
    game = GameFactory(concept=concept, title_platform=ULTRA)

    assert not Stage.objects.filter(concepts__id=concept.id).exists(), (
        'fixture is wrong -- the concept must reach the stage ONLY through the bundle'
    )

    profile = ProfileFactory(is_linked=True)
    pg = _finish(profile, game)
    result = evaluate_for_touched_games(profile, [pg.id], notify=False)

    assert result['awarded'], (
        'a game qualifying only through a ConceptBundle was never evaluated -- the scope follows '
        'Stage.concepts but not Stage.concept_bundles'
    )


def test_nothing_happens_for_games_in_no_badge():
    """A live badge exists and the hunter QUALIFIES for it -- so "nothing happens" can only come from the
    touched game being outside every stage, not from an empty catalogue. The first version created no
    badges at all, which made the assertion true for a reason that had nothing to do with scoping."""
    _series_with_both_editions('elsewhere', [ULTRA])
    profile = ProfileFactory(is_linked=True)
    _finish(profile, _game_in('elsewhere'))

    unbadged = _finish(profile, GameFactory(concept=ConceptFactory(), title_platform=ULTRA))
    assert evaluate_for_touched_games(profile, [unbadged.id], notify=False) == {
        'awarded': [], 'revoked': [], 'updated': []}
    assert not UserGroupBadge.objects.filter(profile=profile).exists(), (
        'touching a game in no stage evaluated the catalogue anyway'
    )


def test_an_empty_touch_list_is_a_no_op():
    """A sync that touched nothing must not fall through to 'all live badges'.

    The profile QUALIFIES for the badge, deliberately. The first version of this test created a hunter with
    no completion at all, so the fall-through it names would have awarded nothing either -- it passed
    against the exact implementation it exists to forbid.
    """
    _series_with_both_editions('quiet', [ULTRA])
    profile = ProfileFactory(is_linked=True)
    _finish(profile, _game_in('quiet'))

    assert evaluate_for_touched_games(profile, [], notify=False)['awarded'] == []
    assert not UserGroupBadge.objects.filter(profile=profile).exists(), (
        'an empty touch list fell through to evaluating the whole catalogue'
    )


def test_the_sync_entry_point_is_what_sync_complete_calls():
    """`_job_sync_complete` actually CALLS `evaluate_for_sync`. Deleting the wiring used to pass the entire
    suite, which is the regression this whole module exists to prevent and was the one thing untested.

    Asserted by parsing the AST for a real Call node, not by importing `token_keeper` and not by scanning
    its text. Importing it registers an atexit handler that logs after pytest has closed stdout, so every
    suite run picked up teardown noise; a substring scan would match the call in a comment or a docstring.
    The AST matches only a call that would execute.
    """
    import ast
    import pathlib

    source = pathlib.Path(__file__).resolve().parents[2] / 'trophies' / 'token_keeper.py'
    tree = ast.parse(source.read_text(encoding='utf-8'))

    target = next(
        (n for n in ast.walk(tree)
         if isinstance(n, ast.FunctionDef) and n.name == '_job_sync_complete'),
        None,
    )
    assert target is not None, '_job_sync_complete is gone -- this test needs repointing'

    called = {
        n.func.id for n in ast.walk(target)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
    }
    assert 'evaluate_for_sync' in called, (
        'sync_complete no longer calls evaluate_for_sync -- the rebuilt badge subsystem is back to being '
        'written only by a management command that is on no schedule'
    )


def test_the_sync_entry_point_never_raises():
    """It owns its error handling so `token_keeper` does not have to. A badge failure must not fail a sync,
    and the swallow must be here rather than around the import -- an ImportError caught at the call site
    degraded to a log line on every sync forever, with the boards silently frozen."""
    from trophies.services.badge_apply import evaluate_for_sync

    profile = ProfileFactory(is_linked=True)
    with patch('trophies.services.badge_apply.evaluate_for_touched_games',
               side_effect=RuntimeError('engine exploded')):
        result = evaluate_for_sync(profile, [1, 2, 3])

    assert result == {'awarded': [], 'revoked': [], 'updated': []}


@patch('trophies.discord_utils.discord_notifications.queue_webhook_send')
def test_the_sync_entry_point_announces_an_earn(mock_send):
    """Inverted at cutover 5b. This was silent for as long as the legacy engine ran alongside it and sent
    its own embed to the same webhook -- announcing then would have pinged a hunter twice for one act,
    once tier-shaped and once edition-shaped. The legacy engine is gone, so this is the only voice left,
    and a hunter earning a badge mid-sync must hear about it."""
    from trophies.services.badge_apply import evaluate_for_sync

    _series_with_both_editions('loud', [ULTRA])
    profile = ProfileFactory(is_linked=True, is_discord_verified=True, discord_id='123')
    pg = _finish(profile, _game_in('loud'))

    result = evaluate_for_sync(profile, [pg.id])
    assert result['awarded'], 'fixture is wrong -- expected an award'
    assert mock_send.call_count == 1, 'the sync path earned a badge and said nothing'


@patch('trophies.discord_utils.discord_notifications.queue_webhook_send')
def test_a_re_earned_badge_is_not_announced_twice(mock_send):
    """`UserGroupBadge` is binary: a revoke DELETES the row, so a later re-earn is indistinguishable from a
    first earn and would announce again. PSN flux, a DLC drop or a curator editing a stage could therefore
    re-ping a hunter about a badge they have held for a year -- the legacy engine's `maintenance` state
    made that structurally impossible.

    `GroupBadgeAnnouncement` restores the property. A cooldown was the alternative and is NOT sufficient:
    any TTL short enough to be a cooldown has expired by the time the year-later flux happens, which is
    the whole case being defended against. So the marker is durable and this test revokes properly (the
    hold is deleted, exactly as the engine does it) rather than simulating a short gap."""
    from trophies.models import UserGroupBadge
    from trophies.services.badge_apply import evaluate_for_sync

    _series_with_both_editions('flap', [ULTRA])
    profile = ProfileFactory(is_linked=True, is_discord_verified=True, discord_id='123')
    game = _game_in('flap')
    pg = _finish(profile, game)

    evaluate_for_sync(profile, [pg.id])
    assert mock_send.call_count == 1

    # Full revoke: the hold row is gone, which is precisely what makes the re-earn look brand new.
    UserGroupBadge.objects.filter(profile=profile).delete()
    evaluate_for_sync(profile, [pg.id])

    assert UserGroupBadge.objects.filter(profile=profile).exists(), 'fixture is wrong -- expected a re-earn'
    assert mock_send.call_count == 1, 'a re-earned badge announced a second time'


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

    # Twelve more live series, none of them touched -- and the hunter QUALIFIES for every one. An unscoped
    # run would therefore do real award work, not merely read more: without that the only thing separating
    # the two measurements was an incidental `zeroed` DELETE, so a scope-by-badge bug was invisible here.
    for i in range(12):
        _series_with_both_editions(f'other-{i}', [ULTRA])
        _finish(profile, _game_in(f'other-{i}'))

    with CaptureQueriesContext(connection) as large:
        evaluate_for_touched_games(profile, [pg.id], notify=False)

    assert len(large.captured_queries) == len(small.captured_queries), (
        f'{len(small.captured_queries)} queries with 1 series in the catalogue but '
        f'{len(large.captured_queries)} with 13 -- the sync seam is not scoped'
    )
    assert UserGroupBadge.objects.filter(profile=profile).count() == 1, (
        'the untouched series were evaluated and awarded -- the scope is not holding'
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
def test_a_mass_award_still_fits_inside_a_discord_embed(mock_send):
    """Discord rejects a description over 4096 chars with a 400, and `webhook_sender_worker` treats any
    non-429 as terminal -- so an over-long embed is dropped whole, with a log line that does not name the
    profile. The optimistic "Queued notification" log has already fired by then.

    This is the cutover scenario, not a hypothetical: the deploy checklist says a hunter's first sync after
    `is_live` is flipped awards every badge the engine agrees with at once, and a badge is per
    (series x edition) so one series can contribute two.
    """
    from trophies.discord_utils.discord_notifications import send_group_badges_earned_notification

    ultra, _ = _editions()
    badges = [
        GroupBadgeFactory(
            series=BadgeSeriesFactory(series_slug=f'mass-{i}',
                                      name=f'A Rather Long Series Name For Testing {i}'),
            platform_group=ultra, is_live=True)
        for i in range(60)
    ]
    profile = ProfileFactory(is_linked=True, is_discord_verified=True, discord_id='123456789012345678')

    send_group_badges_earned_notification(profile, badges)

    payload = mock_send.call_args.args[0]
    description = payload['embeds'][0]['description']
    assert len(description) <= 4096, f'description is {len(description)} chars -- Discord will 400 this'
    assert '…and 45 more' in description, 'the truncated remainder is not counted for the reader'
    assert payload['embeds'][0]['title'].startswith('🎖️') and '60' in payload['embeds'][0]['title'], (
        'the TITLE should still report the real total, even though the list is trimmed'
    )


@patch('trophies.discord_utils.discord_notifications.queue_webhook_send')
def test_a_series_name_cannot_inject_a_mention_or_break_the_formatting(mock_send):
    """Series names are admin-authored free text landing in a message that pings a channel. The embed
    carries a real `<@id>` mention, so mentions demonstrably resolve in this payload."""
    from trophies.discord_utils.discord_notifications import send_group_badges_earned_notification

    ultra, _ = _editions()
    badge = GroupBadgeFactory(
        series=BadgeSeriesFactory(series_slug='sneaky', name='Sonic @everyone **bold** [x](http://e.vil)'),
        platform_group=ultra, is_live=True)
    profile = ProfileFactory(is_linked=True, is_discord_verified=True, discord_id='123')

    send_group_badges_earned_notification(profile, [badge])
    payload = mock_send.call_args.args[0]

    assert payload['allowed_mentions'] == {'users': ['123']}, (
        'without allowed_mentions an @everyone in a series name would resolve'
    )
    description = payload['embeds'][0]['description']
    assert '\\*\\*bold\\*\\*' in description, 'markdown in a series name was not escaped'
    assert '\\[x\\]\\(http://e.vil\\)' in description, 'a link in a series name was not escaped'


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

    assert mock_send.called, (
        'the announcement was never attempted -- this test cannot distinguish a swallowed exception from '
        'a call that never happened, which is what it exists to check'
    )
    assert result['awarded'], 'the award was rolled back by a failed announcement'
    assert UserGroupBadge.objects.filter(profile=profile).exists()


def test_the_announcement_backfill_covers_every_existing_hold():
    """Migration 0305's data step, exercised directly.

    The table starts empty and `evaluate_for_sync` now announces, so without this backfill the first sync
    after deploy re-pings every hunter for every badge they already hold. That failure is loud, public and
    unrecoverable -- you cannot unsend a webhook -- which is why the backfill lives in the migration rather
    than on a deploy checklist, and why it is called here rather than trusted.

    Imported by `importlib` because the module name starts with a digit.
    """
    import importlib

    from django.apps import apps as global_apps
    from trophies.models import GroupBadgeAnnouncement, UserGroupBadge
    from trophies.services.badge_apply import evaluate_and_apply

    migration = importlib.import_module('trophies.migrations.0305_group_badge_announcement')

    _series_with_both_editions('backfill', [ULTRA])
    profile = ProfileFactory(is_linked=True)
    _finish(profile, _game_in('backfill'))

    # Holds WITHOUT markers: what a pre-migration database looks like.
    evaluate_and_apply(profile, notify=False)
    GroupBadgeAnnouncement.objects.all().delete()
    held = set(UserGroupBadge.objects.values_list('profile_id', 'group_badge_id'))
    assert held, 'fixture is wrong -- expected at least one hold'

    migration.seed_from_existing_holds(global_apps, None)
    assert set(GroupBadgeAnnouncement.objects.values_list('profile_id', 'group_badge_id')) == held

    # Idempotent: re-running must not raise on the unique constraint.
    migration.seed_from_existing_holds(global_apps, None)
    assert GroupBadgeAnnouncement.objects.count() == len(held)


def test_a_backfilled_hold_is_never_announced_after_flux():
    """The backfill's actual purpose: a hunter whose badge predates the marker table must stay silent when
    PSN flux revokes and re-earns it.

    The first version of this test held the badge and asserted silence, which proved nothing -- `diff()`
    only emits an `award` when the hunter does NOT already hold the badge, so a held badge never announces
    with or without the guard. The revoke is what makes the re-earn look brand new, so the revoke has to be
    in the test. Mutation-checked: neutering the migration's backfill makes this fail.
    """
    import importlib

    from django.apps import apps as global_apps
    from trophies.models import GroupBadgeAnnouncement, UserGroupBadge
    from trophies.services.badge_apply import evaluate_and_apply, evaluate_for_sync

    migration = importlib.import_module('trophies.migrations.0305_group_badge_announcement')

    _series_with_both_editions('flux-after-backfill', [ULTRA])
    profile = ProfileFactory(is_linked=True, is_discord_verified=True, discord_id='123')
    game = _game_in('flux-after-backfill')
    pg = _finish(profile, game)

    # A pre-migration database: holds exist, markers do not.
    evaluate_and_apply(profile, notify=False)
    GroupBadgeAnnouncement.objects.all().delete()
    migration.seed_from_existing_holds(global_apps, None)

    # Flux: the hold is deleted, so the next evaluation is a genuine fresh award.
    UserGroupBadge.objects.filter(profile=profile).delete()

    with patch('trophies.discord_utils.discord_notifications.queue_webhook_send') as mock_send:
        result = evaluate_for_sync(profile, [pg.id])

    assert result['awarded'], 'fixture is wrong -- the revoke should have made this a fresh award'
    assert mock_send.call_count == 0, 'a backfilled hold was re-announced after flux'
