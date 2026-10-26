"""Mastery, Full Set and Series Master through the real evaluation (badge rewards phase 4, owner 2026-10-25).

The pure rules are pinned in test_badge_mastery.py. These hold down what the DB seam adds: holds that come and
go with the badge, "Mastered since" that never re-stamps while it holds, a celebration only for what was
finished recently (a veteran's old mastery seen for the first time is silent), and one Discord ping per run.

Mastery is offered only on an edition that asks more than holo, so the mastery tests give their series a stage 0
(a region-locked release) the hunter has also cleared; without one, every finished hold here stays holo.
"""
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.utils import timezone

from trophies.models import Moment, ProfileGame, ProfileTrophyGroup, RewardAnnouncement, SeriesAward, TrophyGroup, UserGroupBadge
from trophies.services import moment_service
from trophies.services.badge_apply import evaluate_and_apply, evaluate_and_apply_batch
from tests.factories import (
    BadgeSeriesFactory, ConceptFactory, GameFactory, GroupBadgeFactory, PlatformGroupFactory, ProfileFactory,
    StageFactory,
)

pytestmark = pytest.mark.django_db

ULTRA = ['PS4', 'PS5']
LEGACY = ['PS3', 'PSVITA']
CROSS = ['PS3', 'PS5']


def _hunter(**kw):
    """A hunter whose moments have started (their first sync has finished)."""
    return ProfileFactory(moments_since=timezone.now() - timedelta(days=30), is_linked=True, **kw)


def _series(*stages, legacy=True):
    """A series whose stages are {stage_number: [platform lists, one game each]}; a live badge per edition.
    Returns (series, {key: GroupBadge}, {stage_number: [games]})."""
    series = BadgeSeriesFactory()
    ultra = PlatformGroupFactory(key='ultra-hd', name='Ultra HD', platforms=ULTRA, sort_order=10)
    badges = {'ultra-hd': GroupBadgeFactory(series=series, platform_group=ultra, is_live=True)}
    if legacy:
        lg = PlatformGroupFactory(key='legacy-hd', name='Legacy HD', platforms=LEGACY, sort_order=20)
        badges['legacy-hd'] = GroupBadgeFactory(series=series, platform_group=lg, is_live=True)
    games = {}
    for number, platform_lists in stages:
        st = StageFactory(series_slug=series.series_slug, stage_number=number)
        for platforms in platform_lists:
            concept = ConceptFactory()
            st.concepts.add(concept)
            games.setdefault(number, []).append(GameFactory(concept=concept, title_platform=list(platforms)))
    return series, badges, games


def _finish(profile, game, full=True, ago=timedelta(hours=2)):
    """The base list at 100%, and the whole game too when `full`, dated `ago`."""
    when = timezone.now() - ago
    ProfileGame.objects.update_or_create(profile=profile, game=game, defaults={
        'progress': 100 if full else 80, 'has_plat': True, 'most_recent_trophy_date': when})
    tg, _ = TrophyGroup.objects.get_or_create(game=game, trophy_group_id='default',
                                              defaults={'trophy_group_name': 'Base'})
    ProfileTrophyGroup.objects.update_or_create(profile=profile, trophy_group=tg,
                                                defaults={'progress': 100, 'last_trophy_at': when})


def _hold(profile, badge):
    return UserGroupBadge.objects.filter(profile=profile, group_badge=badge).first()


# -- mastery -------------------------------------------------------------------------------------------------

def test_every_stage_at_100_with_stage_zero_masters_the_badge_and_celebrates_it():
    _, badges, games = _series((0, [ULTRA]), (1, [ULTRA]), legacy=False)
    hunter = _hunter()
    _finish(hunter, games[1][0])
    _finish(hunter, games[0][0], full=False)

    result = evaluate_and_apply(hunter)
    hold = _hold(hunter, badges['ultra-hd'])
    assert hold.is_holo and hold.mastered_at is None, 'stage 0 short of 100% leaves it holo, not mastered'
    assert result['mastered'] == []

    _finish(hunter, games[0][0])
    result = evaluate_and_apply(hunter)
    assert result['mastered'] == [badges['ultra-hd'].id]
    assert _hold(hunter, badges['ultra-hd']).mastered_at is not None
    assert Moment.objects.filter(profile=hunter, kind=Moment.KIND_MASTERED, group_badge=badges['ultra-hd']).exists()


def test_a_new_stage_takes_mastery_away_and_the_re_clear_brings_it_back_with_a_new_date():
    series, badges, games = _series((0, [ULTRA]), (1, [ULTRA]), legacy=False)
    hunter = _hunter()
    _finish(hunter, games[0][0], ago=timedelta(days=3))
    _finish(hunter, games[1][0], ago=timedelta(days=3))
    evaluate_and_apply(hunter)
    first = _hold(hunter, badges['ultra-hd']).mastered_at
    assert first is not None

    st = StageFactory(series_slug=series.series_slug, stage_number=2)
    concept = ConceptFactory()
    st.concepts.add(concept)
    new_game = GameFactory(concept=concept, title_platform=ULTRA)
    evaluate_and_apply(hunter)
    assert _hold(hunter, badges['ultra-hd']) is None, 'the new stage gates the badge itself'

    _finish(hunter, new_game)
    evaluate_and_apply(hunter)
    again = _hold(hunter, badges['ultra-hd']).mastered_at
    assert again is not None and again > first, 'binary: the current mastery is dated by its own re-clear'


def test_mastered_since_does_not_move_while_it_holds():
    _, badges, games = _series((0, [ULTRA]), (1, [ULTRA, ULTRA]), legacy=False)
    hunter = _hunter()
    _finish(hunter, games[0][0], ago=timedelta(days=5))
    _finish(hunter, games[1][0], ago=timedelta(days=5))
    evaluate_and_apply(hunter)
    since = _hold(hunter, badges['ultra-hd']).mastered_at
    assert since is not None

    _finish(hunter, games[1][1], ago=timedelta(days=9))     # an EARLIER clear of the same stage turns up
    evaluate_and_apply(hunter)
    assert _hold(hunter, badges['ultra-hd']).mastered_at == since


def test_a_dead_stage_counts_toward_mastery_but_never_toward_the_badge():
    _, badges, games = _series((1, [ULTRA]), (2, [ULTRA]), legacy=False)
    games[2][0].is_obtainable = False
    games[2][0].save()
    hunter = _hunter()
    _finish(hunter, games[1][0])

    evaluate_and_apply(hunter)
    hold = _hold(hunter, badges['ultra-hd'])
    assert hold is not None and hold.is_holo, 'the dead stage does not gate the badge or its holo'
    assert hold.mastered_at is None, 'but Mastery is every stage the edition covers'


def test_an_old_mastery_seen_for_the_first_time_is_marked_but_not_celebrated():
    """The deploy's veteran case (owner: silent backfill): the hold is marked, dated by the real finish, and
    no ceremony plays for something done long ago."""
    _, badges, games = _series((0, [ULTRA]), (1, [ULTRA]), legacy=False)
    hunter = _hunter()
    _finish(hunter, games[0][0], ago=timedelta(days=200))
    _finish(hunter, games[1][0], ago=timedelta(days=200))

    evaluate_and_apply(hunter)
    hold = _hold(hunter, badges['ultra-hd'])
    assert hold.mastered_at is not None and hold.mastered_at < timezone.now() - timedelta(days=150)
    assert not Moment.objects.filter(profile=hunter, kind=Moment.KIND_MASTERED).exists()


def test_a_badge_earned_already_mastered_plays_one_moment_not_two():
    """Audit: the badge moment and a mastery moment of the same medallion back to back said one thing twice.
    The badge moment wears the crown instead."""
    _, badges, games = _series((0, [ULTRA]), (1, [ULTRA]), legacy=False)
    hunter = _hunter()
    _finish(hunter, games[0][0])
    _finish(hunter, games[1][0])
    result = evaluate_and_apply(hunter)
    assert result['mastered'] == [badges['ultra-hd'].id], 'still reported, for the announcement'
    assert [m.kind for m in Moment.objects.filter(profile=hunter)] == [Moment.KIND_BADGE]


def test_a_badge_with_nothing_beyond_holo_tops_out_at_holo():
    """Owner, 2026-10-25: Mastery is above and beyond. On a badge that asks nothing past holo it would be holo
    under another name, so a hunter who finished everything holds a holo badge, with no crown and no second
    moment."""
    _, badges, games = _series((1, [ULTRA]), (2, [ULTRA]), legacy=False)
    hunter = _hunter()
    for number in (1, 2):
        _finish(hunter, games[number][0])
    result = evaluate_and_apply(hunter)
    hold = _hold(hunter, badges['ultra-hd'])
    assert hold.is_holo and hold.mastered_at is None
    assert result['mastered'] == []
    assert [m.kind for m in Moment.objects.filter(profile=hunter)] == [Moment.KIND_BADGE]


def test_a_stage_that_stops_gating_offers_mastery_to_whoever_cleared_it():
    """A game marked unobtainable later turns its stage into an extra: the hunter who cleared it while it could
    be bought is mastered, quietly when the clear is old. Marking it obtainable again takes the crown back."""
    _, badges, games = _series((1, [ULTRA]), (2, [ULTRA]), legacy=False)
    hunter = _hunter()
    for number in (1, 2):
        _finish(hunter, games[number][0], ago=timedelta(days=60))
    evaluate_and_apply(hunter)
    assert _hold(hunter, badges['ultra-hd']).mastered_at is None

    games[2][0].is_obtainable = False
    games[2][0].save()
    evaluate_and_apply(hunter)
    assert _hold(hunter, badges['ultra-hd']).mastered_at is not None
    assert not Moment.objects.filter(profile=hunter, kind=Moment.KIND_MASTERED).exists(), 'an old clear is silent'

    games[2][0].is_obtainable = True
    games[2][0].save()
    evaluate_and_apply(hunter)
    hold = _hold(hunter, badges['ultra-hd'])
    assert hold.is_holo and hold.mastered_at is None


def test_a_mastery_lost_before_it_played_has_nothing_left_to_celebrate():
    series, badges, games = _series((0, [ULTRA]), (1, [ULTRA]), legacy=False)
    hunter = _hunter()
    _finish(hunter, games[1][0])
    _finish(hunter, games[0][0], full=False)
    evaluate_and_apply(hunter)                       # the badge, short of mastery
    _finish(hunter, games[0][0])
    evaluate_and_apply(hunter)                       # mastered: its own moment
    assert any(m.kind == Moment.KIND_MASTERED for m in moment_service.pending(hunter))

    ProfileGame.objects.filter(profile=hunter, game=games[1][0]).update(progress=90)   # a DLC drop
    evaluate_and_apply(hunter)
    assert _hold(hunter, badges['ultra-hd']).mastered_at is None
    assert not any(m.kind == Moment.KIND_MASTERED for m in moment_service.pending(hunter))


# -- full set and series master --------------------------------------------------------------------------------

def test_holding_every_edition_is_a_full_set_and_losing_one_loses_it():
    series, badges, games = _series((1, [CROSS]))
    hunter = _hunter()
    _finish(hunter, games[1][0])

    evaluate_and_apply(hunter)
    award = SeriesAward.objects.get(profile=hunter, series=series, kind=SeriesAward.KIND_FULL_SET)
    assert award.since is not None
    assert Moment.objects.filter(profile=hunter, kind=Moment.KIND_FULL_SET, series=series).exists()

    st = StageFactory(series_slug=series.series_slug, stage_number=2)
    concept = ConceptFactory()
    st.concepts.add(concept)
    GameFactory(concept=concept, title_platform=LEGACY)       # Legacy HD gains a stage the hunter has not cleared
    evaluate_and_apply(hunter)
    assert not SeriesAward.objects.filter(profile=hunter, series=series).exists()


def test_an_edition_that_cannot_be_earned_does_not_block_the_set():
    series, badges, games = _series((1, [CROSS]))
    third = PlatformGroupFactory(key='ps6', name='PS6', platforms=['PS6'], sort_order=30)
    GroupBadgeFactory(series=series, platform_group=third, is_live=True)   # nothing on PS6: never earnable
    hunter = _hunter()
    _finish(hunter, games[1][0])

    evaluate_and_apply(hunter)
    assert SeriesAward.objects.filter(profile=hunter, series=series, kind=SeriesAward.KIND_FULL_SET).exists()


def test_series_master_is_every_edition_finished_when_no_one_edition_covers_every_stage():
    """Neither edition here asks more than holo, so each one's top finish is holo, and Series Master reads that."""
    series, badges, games = _series((1, [CROSS]), (2, [ULTRA]), (3, [LEGACY]))
    hunter = _hunter()
    for number in (1, 2):
        _finish(hunter, games[number][0])
    _finish(hunter, games[3][0], full=False)

    evaluate_and_apply(hunter)
    kinds = set(SeriesAward.objects.filter(profile=hunter, series=series).values_list('kind', flat=True))
    assert kinds == {SeriesAward.KIND_FULL_SET}, 'Legacy HD is held but not mastered'

    _finish(hunter, games[3][0])
    evaluate_and_apply(hunter)
    kinds = set(SeriesAward.objects.filter(profile=hunter, series=series).values_list('kind', flat=True))
    assert kinds == {SeriesAward.KIND_FULL_SET, SeriesAward.KIND_SERIES_MASTER}
    assert not UserGroupBadge.objects.filter(profile=hunter, mastered_at__isnull=False).exists()


def test_a_series_master_moment_draws_each_edition_as_it_is_held(rf):
    """Audit: the moment drew only MASTERED editions (and every live one, crowned, when none was), so a Series
    Master finished at holo showed a dead edition crowned under "Every edition mastered"."""
    from django.template import Context, Template

    series, badges, games = _series((1, [CROSS]), (2, [ULTRA]), (3, [LEGACY]))
    dead = GroupBadgeFactory(series=series, is_live=True,
                             platform_group=PlatformGroupFactory(key='ps6', name='PS6', platforms=['PS6'], sort_order=30))
    hunter = _hunter()
    for number in (1, 2, 3):
        _finish(hunter, games[number][0])
    evaluate_and_apply(hunter)
    request = rf.get('/career/')
    request.user = hunter.user
    html = Template('{% load moments %}{% moments_overlay %}').render(Context({'request': request}))
    assert 'Every edition at 100%: Ultra HD and Legacy HD' in html
    assert f'data-badge-id="{dead.id}"' not in html
    assert 'pp-med--mastered' not in html, 'neither edition offers Mastery: their finish is holo, not a crown'


def test_series_master_waits_on_the_mastery_an_edition_offers():
    """Ultra HD has a stage 0 (region-locked): its top finish is Mastery, so its holo alone is not enough."""
    series, badges, games = _series((0, [ULTRA]), (1, [CROSS]), (2, [ULTRA]), (3, [LEGACY]))
    hunter = _hunter()
    for number in (1, 2, 3):
        _finish(hunter, games[number][0])
    _finish(hunter, games[0][0], full=False)
    evaluate_and_apply(hunter)
    assert _hold(hunter, badges['ultra-hd']).is_holo
    assert not SeriesAward.objects.filter(profile=hunter, series=series, kind=SeriesAward.KIND_SERIES_MASTER).exists()

    _finish(hunter, games[0][0])
    evaluate_and_apply(hunter)
    assert SeriesAward.objects.filter(profile=hunter, series=series, kind=SeriesAward.KIND_SERIES_MASTER).exists()


def test_no_series_master_when_one_edition_already_covers_every_stage():
    series, _, games = _series((1, [CROSS]), (2, [CROSS]))
    hunter = _hunter()
    for number in (1, 2):
        _finish(hunter, games[number][0])
    evaluate_and_apply(hunter)
    assert not SeriesAward.objects.filter(profile=hunter, series=series, kind=SeriesAward.KIND_SERIES_MASTER).exists()


def test_the_nightly_batch_applies_series_rewards_too():
    series, _, games = _series((1, [CROSS]))
    hunter = _hunter()
    _finish(hunter, games[1][0])
    totals = evaluate_and_apply_batch([hunter])
    assert totals['series_rewards'] == 1
    assert SeriesAward.objects.filter(profile=hunter, series=series).exists()


# -- discord ---------------------------------------------------------------------------------------------------

@patch('trophies.discord_utils.discord_notifications.queue_webhook_send')
def test_one_ping_carries_the_badges_and_the_rewards_and_a_reward_is_announced_once_ever(mock_send):
    series, badges, games = _series((0, [CROSS]), (1, [CROSS]))
    hunter = _hunter(is_discord_verified=True, discord_id='123')
    _finish(hunter, games[0][0])
    _finish(hunter, games[1][0])

    evaluate_and_apply(hunter, notify=True)
    assert mock_send.call_count == 1, 'finishing a series is one ping'
    body = str(mock_send.call_args)
    assert 'Full Set' in body and 'Mastered' in body

    # Lose it all (a DLC drop on the one game), then earn it back: nothing is news to Discord twice.
    ProfileTrophyGroup.objects.filter(profile=hunter).update(progress=50)
    ProfileGame.objects.filter(profile=hunter).update(progress=50)
    evaluate_and_apply(hunter, notify=True)
    _finish(hunter, games[0][0])
    _finish(hunter, games[1][0])
    evaluate_and_apply(hunter, notify=True)
    assert mock_send.call_count == 1
    assert RewardAnnouncement.objects.filter(profile=hunter).count() == 3   # two masteries + the Full Set


@patch('trophies.discord_utils.discord_notifications.queue_webhook_send')
def test_an_old_reward_is_not_announced(mock_send):
    _, badges, games = _series((0, [ULTRA]), (1, [ULTRA]), legacy=False)
    hunter = _hunter(is_discord_verified=True, discord_id='123')
    _finish(hunter, games[0][0], ago=timedelta(days=200))
    _finish(hunter, games[1][0], ago=timedelta(days=200))
    evaluate_and_apply(hunter, notify=True)
    assert _hold(hunter, badges['ultra-hd']).mastered_at is not None, 'mastered, so the silence means something'
    body = str(mock_send.call_args)
    assert 'Mastered' not in body, 'a veteran\'s old mastery is not news'


# -- the seam's guarantees (audit, 2026-10-25) -------------------------------------------------------------------

def test_a_full_set_both_runs_create_is_found_not_raised():
    """A sync and the nightly racing the same hunter both read "not held". The loser finds the winner's row,
    stays silent, and raises nothing (a raise here would cost the badges' announcement and the XP recompute)."""
    from trophies.services.badge_apply import apply_series_rewards
    from trophies.services.badge_orchestrator import evaluate_profile

    series, badges, games = _series((1, [CROSS]))
    hunter = _hunter()
    _finish(hunter, games[1][0])
    desired = evaluate_profile(hunter, list(badges.values()))
    SeriesAward.objects.create(profile=hunter, series=series, kind=SeriesAward.KIND_FULL_SET, since=timezone.now())

    gained = apply_series_rewards(hunter, desired, list(badges.values()), held=set())   # the stale read
    assert gained == [] or all(kind != SeriesAward.KIND_FULL_SET for kind, _, _ in gained)
    assert SeriesAward.objects.filter(profile=hunter, series=series, kind=SeriesAward.KIND_FULL_SET).count() == 1
    assert not Moment.objects.filter(profile=hunter, kind=Moment.KIND_FULL_SET).exists()


@patch('trophies.discord_utils.discord_notifications.queue_webhook_send')
def test_a_failing_series_reward_never_costs_the_badges_their_announcement(mock_send):
    _, badges, games = _series((1, [CROSS]))
    hunter = _hunter(is_discord_verified=True, discord_id='123')
    _finish(hunter, games[1][0])
    with patch('trophies.services.badge_apply.evaluate_series_rewards', side_effect=RuntimeError('boom')):
        result = evaluate_and_apply(hunter, notify=True)
    assert len(result['awarded']) == 2
    assert mock_send.call_count == 1 and 'Ultra HD' in str(mock_send.call_args)


@patch('trophies.discord_utils.discord_notifications.queue_webhook_send')
def test_a_run_with_only_a_new_reward_sends_the_rewards_embed(mock_send):
    _, badges, games = _series((1, [ULTRA]), (0, [ULTRA]), legacy=False)
    hunter = _hunter(is_discord_verified=True, discord_id='123')
    _finish(hunter, games[1][0])
    _finish(hunter, games[0][0], full=False)
    evaluate_and_apply(hunter, notify=True)          # the badge: one embed
    _finish(hunter, games[0][0])
    evaluate_and_apply(hunter, notify=True)          # then the mastery alone
    assert mock_send.call_count == 2
    assert 'Mastered' in str(mock_send.call_args)


@patch('trophies.discord_utils.discord_notifications.queue_webhook_send')
def test_a_hunter_without_discord_is_not_sent_anything(mock_send):
    _, _, games = _series((1, [ULTRA]), legacy=False)
    hunter = _hunter(is_discord_verified=False)
    _finish(hunter, games[1][0])
    evaluate_and_apply(hunter, notify=True)
    assert mock_send.call_count == 0


def test_the_nightly_batch_reads_series_awards_once_not_per_profile():
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    _, _, games = _series((1, [CROSS]))
    hunters = [_hunter() for _ in range(3)]
    for h in hunters:
        _finish(h, games[1][0])
    evaluate_and_apply_batch(hunters)                # everyone gains it: writes
    with CaptureQueriesContext(connection) as ctx:
        evaluate_and_apply_batch(hunters)            # nothing changes: reads only
    reads = [q['sql'] for q in ctx.captured_queries if 'trophies_seriesaward' in q['sql']]
    assert len(reads) == 1, f'{len(reads)} series-award reads for 3 hunters'


def test_a_bundle_is_mastered_by_its_last_member_to_reach_100():
    from trophies.models import ConceptBundle

    series, badges, games = _series((0, [ULTRA]), (1, []), legacy=False)
    from trophies.models import Stage
    stage = Stage.objects.get(series_slug=series.series_slug, stage_number=1)
    bundle = ConceptBundle.objects.create(stage=stage, label='Episodes')
    members = [ConceptFactory(), ConceptFactory()]
    bundle.concepts.set(members)
    first, last = (GameFactory(concept=c, title_platform=ULTRA) for c in members)
    hunter = _hunter()
    _finish(hunter, games[0][0], ago=timedelta(days=6))
    _finish(hunter, first, ago=timedelta(days=4))
    _finish(hunter, last, ago=timedelta(days=1))

    evaluate_and_apply(hunter)
    mastered_at = _hold(hunter, badges['ultra-hd']).mastered_at
    assert mastered_at is not None
    assert abs((timezone.now() - timedelta(days=1)) - mastered_at) < timedelta(minutes=5), 'dated by the LAST member'
