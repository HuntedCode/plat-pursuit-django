"""Celebration moments (badge rewards phase 3): recorded at the earn, played once (trophies/services/moment_service.py).

The owner's rules (2026-10-25):

  * a first sync records ONE welcome summary, never the flood of everything a hunter arrived with
  * waiting badges play one at a time; unplayed moments drop after 14 days
  * the off switch stops auto-play only: the strip's pill then opens every waiting moment
  * platinums never ambush: they wait behind a pill, as a reel
  * the confetti this replaces fired from an inbox poll on any page; moments read nothing off My Pursuit pages
"""
import inspect
from datetime import timedelta
from types import SimpleNamespace

import pytest
from django.db import connection
from django.template import Context, Template
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from milestones.models import Milestone, MilestoneTier
from tests.factories import (
    BadgeSeriesFactory, EarnedTrophyFactory, GameFactory, GroupBadgeFactory, PlatformGroupFactory, ProfileFactory,
    StageFactory, TrophyFactory, TrophyGroupFactory,
)
from trophies.models import Moment, ProfileGame, ProfileTrophyGroup, Trophy, UserGroupBadge
from trophies.services import moment_service
from trophies.services.badge_apply import evaluate_and_apply

pytestmark = pytest.mark.django_db

NOW = timezone.now


def _hunter(**kw):
    """A hunter whose moments have started (their first sync has finished)."""
    return ProfileFactory(moments_since=NOW() - timedelta(days=30), **kw)


def _badge(game):
    series = BadgeSeriesFactory()
    StageFactory(series_slug=series.series_slug, stage_number=1).concepts.add(game.concept)
    return GroupBadgeFactory(series=series, platform_group=PlatformGroupFactory(key='ultra-hd', platforms=['PS4', 'PS5']))


def _finished(profile, game):
    ProfileGame.objects.update_or_create(profile=profile, game=game, defaults={'progress': 100})
    ProfileTrophyGroup.objects.update_or_create(
        profile=profile, trophy_group=game.trophy_groups.get(trophy_group_id='default'),
        defaults={'progress': 100, 'last_trophy_at': NOW() - timedelta(hours=2)},
    )


def _game():
    game = GameFactory(title_platform=['PS5'])
    TrophyGroupFactory(game=game, trophy_group_id='default')
    return game


def _plat(profile, when=None, game=None):
    game = game or _game()
    trophy = TrophyFactory(game=game, trophy_type='platinum')
    return EarnedTrophyFactory(profile=profile, trophy=trophy, earned_date_time=when or NOW() - timedelta(hours=1))


# --- recording ----------------------------------------------------------------------------------------------

def test_earning_a_badge_records_its_moment():
    game = _game()
    gb = _badge(game)
    profile = _hunter()
    _finished(profile, game)

    evaluate_and_apply(profile)

    [m] = Moment.objects.filter(profile=profile)
    assert (m.kind, m.group_badge_id, m.played_at) == ('badge', gb.id, None)


def test_nothing_is_recorded_before_a_hunters_first_sync_has_finished():
    game = _game()
    _badge(game)
    profile = ProfileFactory()            # moments_since is null: their first sync is still running
    _finished(profile, game)

    evaluate_and_apply(profile)
    moment_service.record_platinum(profile, _plat(profile), NOW())

    assert not Moment.objects.exists()


def test_a_profile_without_a_site_account_records_nothing():
    profile = ProfileFactory(user=None, moments_since=NOW())

    moment_service.record_badge(profile, GroupBadgeFactory())
    assert moment_service.start_moments(profile) is False
    assert not Moment.objects.exists()


def test_a_badge_that_flips_off_and_on_waits_as_one_moment_and_a_played_one_can_come_again():
    """Owner: the ceremony plays every time it is earned. But never twice for one unseen earn."""
    gb = GroupBadgeFactory()
    profile = _hunter()

    moment_service.record_badge(profile, gb)
    moment_service.record_badge(profile, gb)
    assert Moment.objects.filter(profile=profile).count() == 1

    Moment.objects.update(played_at=NOW())
    moment_service.record_badge(profile, gb)
    assert Moment.objects.filter(profile=profile, played_at__isnull=True).count() == 1


def test_a_platinum_is_news_only_when_recent_and_is_celebrated_once():
    profile = _hunter()
    old = _plat(profile, when=NOW() - timedelta(days=3))
    new = _plat(profile)

    moment_service.record_platinum(profile, old, old.earned_date_time)
    moment_service.record_platinum(profile, new, new.earned_date_time)
    moment_service.record_platinum(profile, new, new.earned_date_time)

    assert list(Moment.objects.values_list('earned_trophy_id', flat=True)) == [new.id]


def test_sync_records_a_new_platinum_and_numbers_it_from_the_live_count():
    from trophies.services.psn_api_service import PsnApiService

    profile = _hunter()
    _plat(profile)                                   # their first platinum, already on file
    trophy = TrophyFactory(game=_game(), trophy_type='platinum')
    data = SimpleNamespace(earned=True, trophy_hidden=False, progress=None, progress_rate=None,
                           progressed_date_time=None, earned_date_time=NOW() - timedelta(minutes=5))

    PsnApiService.create_or_update_earned_trophy_from_trophy_data(profile, trophy, data)

    [m] = Moment.objects.filter(kind='platinum')
    assert m.earned_trophy.trophy_id == trophy.id
    assert m.data['number'] == 2, 'the second platinum, by the denorm the save just bumped'


def test_a_first_sync_records_one_welcome_summary_and_starts_moments():
    game = _game()
    gb = _badge(game)
    profile = ProfileFactory()
    _finished(profile, game)
    evaluate_and_apply(profile)                     # awards during the first sync: not recorded one by one

    assert moment_service.start_moments(profile) is True
    assert moment_service.start_moments(profile) is False, 'only the first sync'

    [m] = Moment.objects.filter(profile=profile)
    assert m.kind == 'welcome' and m.data['badges'] == 1
    profile.refresh_from_db()
    assert profile.moments_since is not None
    assert UserGroupBadge.objects.filter(profile=profile, group_badge=gb).exists()


def test_only_platinum_count_milestones_get_a_beat():
    plats = Milestone.objects.create(slug='plats', name='Platinum Hunter', metric='lifetime_platinums')
    other = Milestone.objects.create(slug='games', name='Explorer', metric='games_played')
    tiers = [MilestoneTier.objects.create(milestone=plats, index=1, threshold=250, name='Plat Lord'),
             MilestoneTier.objects.create(milestone=other, index=1, threshold=100)]
    profile = _hunter()

    moment_service.record_milestones(profile, tiers)

    [m] = Moment.objects.all()
    assert (m.kind, m.data) == ('milestone', {'name': 'Plat Lord', 'threshold': 250})


def test_sync_complete_records_milestones_then_starts_moments_before_it_reports_synced():
    """The last steps of `_job_sync_complete`, in order: a first sync's own awards are already written when
    `start_moments` runs, so they are summarised, not recorded one by one."""
    from trophies.token_keeper import TokenKeeper

    # Live statements only: each must START a source line, so a commented-out call cannot satisfy it.
    lines = [line.strip() for line in inspect.getsource(TokenKeeper._job_sync_complete).splitlines()]

    def at(statement, after=-1):
        return next(i for i, line in enumerate(lines) if i > after and line.startswith(statement))

    i_eval = at('evaluate_for_sync(profile, touched_profilegame_ids)')
    i_tiers = at('newly_earned_tiers = recompute_on_sync(profile)', i_eval)
    i_record = at('moment_service.record_milestones(profile, newly_earned_tiers)', i_tiers)
    i_start = at('moment_service.start_moments(profile)', i_record)
    assert at("profile.set_sync_status('synced')", i_start) > i_start


# --- reading ------------------------------------------------------------------------------------------------

def test_waiting_moments_drop_when_played_expired_or_the_badge_was_revoked():
    profile = _hunter()
    held, revoked = GroupBadgeFactory(), GroupBadgeFactory()
    UserGroupBadge.objects.create(profile=profile, group_badge=held, earned_at=NOW())
    keep = Moment.objects.create(profile=profile, kind='badge', group_badge=held, occurred_at=NOW())
    Moment.objects.create(profile=profile, kind='badge', group_badge=revoked, occurred_at=NOW())
    Moment.objects.create(profile=profile, kind='welcome', occurred_at=NOW(), played_at=NOW())
    stale = Moment.objects.create(profile=profile, kind='welcome', occurred_at=NOW())
    Moment.objects.filter(id=stale.id).update(created_at=NOW() - moment_service.MOMENT_TTL - timedelta(hours=1))

    assert list(moment_service.pending(profile)) == [keep]


def test_mark_played_touches_only_the_hunters_own_unplayed_moments():
    mine, theirs = _hunter(), _hunter()
    a = Moment.objects.create(profile=mine, kind='welcome', occurred_at=NOW())
    b = Moment.objects.create(profile=theirs, kind='welcome', occurred_at=NOW())

    assert moment_service.mark_played(mine, [a.id, b.id]) == 1
    assert moment_service.mark_played(mine, [a.id]) == 0
    assert Moment.objects.get(id=b.id).played_at is None


def test_the_request_read_is_one_query_shared_by_the_pill_and_the_overlay(rf):
    profile = _hunter()
    request = rf.get('/career/')
    request.user = profile.user

    with CaptureQueriesContext(connection) as ctx:
        moment_service.for_request(request)
        moment_service.for_request(request)

    assert len([q for q in ctx.captured_queries if 'trophies_moment' in q['sql']]) == 1


# --- what plays -------------------------------------------------------------------------------------------

def _render(tag, profile, rf):
    request = rf.get('/career/')
    request.user = profile.user
    # A plain Context carrying the request: the tags read only that, and the site's context processors would
    # add their own (cached, so order-dependent) queries to every count.
    return Template('{% load moments %}' + tag).render(Context({'request': request}))


def test_the_overlay_queues_badges_to_auto_play_and_platinums_for_the_reel(rf):
    profile = _hunter()
    gb = GroupBadgeFactory()
    UserGroupBadge.objects.create(profile=profile, group_badge=gb, earned_at=NOW())
    moment_service.record_badge(profile, gb)
    et = _plat(profile)
    moment_service.record_platinum(profile, et, et.earned_date_time)

    html = _render('{% moments_overlay %}', profile, rf)

    assert 'id="pp-moments" data-autoplay="1"' in html
    assert html.count('data-queue="auto"') == 1 and html.count('data-queue="reel"') == 1
    assert f'data-badge-id="{gb.id}"' in html and 'Badge earned' in html
    assert 'New platinum' in html and et.trophy.game.title_name in html


def test_nothing_waiting_renders_nothing(rf):
    assert _render('{% moments_overlay %}{% moments_pill %}', _hunter(), rf).strip() == ''


def test_the_pill_counts_platinums_and_never_the_badges_that_play_by_themselves(rf):
    profile = _hunter()
    gb = GroupBadgeFactory()
    UserGroupBadge.objects.create(profile=profile, group_badge=gb, earned_at=NOW())
    moment_service.record_badge(profile, gb)
    assert _render('{% moments_pill %}', profile, rf).strip() == '', 'a badge alone auto-plays'

    for _ in range(2):
        et = _plat(profile)
        moment_service.record_platinum(profile, et, et.earned_date_time)
    html = _render('{% moments_pill %}', profile, rf)
    assert 'data-moments-open="reel"' in html and '<span>2 new platinums</span>' in html


def test_with_celebrations_off_nothing_auto_plays_and_the_pill_opens_everything(rf):
    profile = _hunter()
    profile.user.play_celebrations = False
    profile.user.save()
    gb = GroupBadgeFactory()
    UserGroupBadge.objects.create(profile=profile, group_badge=gb, earned_at=NOW())
    moment_service.record_badge(profile, gb)

    assert 'data-autoplay=""' in _render('{% moments_overlay %}', profile, rf)
    pill = _render('{% moments_pill %}', profile, rf)
    assert 'data-moments-open="all"' in pill and '<span>1 new</span>' in pill


def test_a_milestone_alone_still_gets_its_beat(rf):
    profile = _hunter()
    Moment.objects.create(profile=profile, kind='milestone', occurred_at=NOW(), data={'name': 'Plat Lord', 'threshold': 250})

    assert '<span>New milestone</span>' in _render('{% moments_pill %}', profile, rf)
    assert 'Milestone' in _render('{% moments_overlay %}', profile, rf)


def test_the_welcome_says_what_they_arrived_with(rf):
    profile = _hunter()
    Moment.objects.create(profile=profile, kind='welcome', occurred_at=NOW(), data={'badges': 0, 'platinums': 340})

    html = _render('{% moments_overlay %}', profile, rf)

    assert 'Your Pursuit begins' in html and 'data-countup="340">340</b> platinums' in html
    assert ' badge</span>' not in html and ' badges</span>' not in html, 'no "0 badges"'


def test_a_platinum_card_says_how_long_it_took(rf):
    profile = _hunter()
    game = _game()
    ProfileTrophyGroup.objects.create(profile=profile, trophy_group=game.trophy_groups.get(trophy_group_id='default'),
                                      first_trophy_at=NOW() - timedelta(days=14, hours=3))
    et = _plat(profile, game=game)
    moment_service.record_platinum(profile, et, et.earned_date_time)

    assert '<b>14 days</b>' in _render('{% moments_overlay %}', profile, rf)


# --- the pages ------------------------------------------------------------------------------------------------

def test_a_my_pursuit_page_plays_moments_and_other_pages_read_nothing_for_them(client):
    profile = _hunter()
    Moment.objects.create(profile=profile, kind='welcome', occurred_at=NOW(), data={'badges': 1, 'platinums': 2})
    client.force_login(profile.user)

    assert 'id="pp-moments"' in client.get(reverse('my_titles')).content.decode()
    with CaptureQueriesContext(connection) as ctx:
        html = client.get(reverse('settings')).content.decode()
    assert 'id="pp-moments"' not in html
    assert not [q for q in ctx.captured_queries if 'trophies_moment' in q['sql']]


def test_the_played_endpoint_marks_only_the_hunters_own(client):
    profile = _hunter()
    mine = Moment.objects.create(profile=profile, kind='welcome', occurred_at=NOW())
    theirs = Moment.objects.create(profile=_hunter(), kind='welcome', occurred_at=NOW())
    url = reverse('api:moments-played')

    assert client.post(url, {'ids': [mine.id]}, content_type='application/json').status_code in (401, 403)
    client.force_login(profile.user)
    r = client.post(url, {'ids': [mine.id, theirs.id]}, content_type='application/json')
    assert (r.status_code, r.json()) == (200, {'played': 1})
    assert client.post(url, {'ids': 'nope'}, content_type='application/json').status_code == 400
    assert Moment.objects.get(id=theirs.id).played_at is None


def test_the_settings_toggle_turns_auto_play_off(client):
    profile = _hunter()
    client.force_login(profile.user)

    client.post(reverse('settings'), {'action': 'library'})

    profile.user.refresh_from_db()
    assert profile.user.play_celebrations is False


# --- the audit's failure modes --------------------------------------------------------------------------------

def test_an_expired_waiting_badge_does_not_swallow_its_next_earn():
    gb = GroupBadgeFactory()
    profile = _hunter()
    UserGroupBadge.objects.create(profile=profile, group_badge=gb, earned_at=NOW())
    moment_service.record_badge(profile, gb)
    Moment.objects.update(created_at=NOW() - moment_service.MOMENT_TTL - timedelta(days=1))
    assert list(moment_service.pending(profile)) == []

    moment_service.record_badge(profile, gb)

    assert len(moment_service.pending(profile)) == 1
    assert Moment.objects.count() == 1, 'refreshed, not stacked'


def test_a_moment_that_cannot_be_written_never_costs_the_badge():
    from unittest import mock
    from django.db import OperationalError

    game = _game()
    gb = _badge(game)
    profile = _hunter()
    _finished(profile, game)

    with mock.patch.object(Moment.objects, 'create', side_effect=OperationalError('boom')):
        evaluate_and_apply(profile)

    assert UserGroupBadge.objects.filter(profile=profile, group_badge=gb).exists()
    assert not Moment.objects.exists()


def test_the_deploy_backfill_starts_everyone_already_here_but_a_first_sync_in_progress():
    import importlib
    from django.apps import apps

    backfill = importlib.import_module('trophies.migrations.0352_backfill_moments_since')
    synced, errored = ProfileFactory(sync_status='synced'), ProfileFactory(sync_status='error')
    newcomer = ProfileFactory(sync_status='syncing')
    veteran = ProfileFactory(sync_status='syncing')
    UserGroupBadge.objects.create(profile=veteran, group_badge=GroupBadgeFactory(), earned_at=NOW())
    scraped = ProfileFactory(user=None, sync_status='synced')

    backfill.start_moments_for_existing_hunters(apps, None)

    started = {p.id for p in (synced, errored, newcomer, veteran, scraped) if (p.refresh_from_db() or p.moments_since)}
    assert started == {synced.id, errored.id, veteran.id}


def test_waiting_badges_cost_the_same_reads_however_many_there_are(rf):
    def queries_for(n):
        profile = _hunter()
        for _ in range(n):
            gb = GroupBadgeFactory()
            UserGroupBadge.objects.create(profile=profile, group_badge=gb, earned_at=NOW())
            moment_service.record_badge(profile, gb)
        with CaptureQueriesContext(connection) as ctx:
            _render('{% moments_overlay %}', profile, rf)
        return len(ctx.captured_queries)

    from trophies.services.rarity import community_size
    community_size()                        # the rarity denominator is cached site-wide: warm it for both counts
    assert queries_for(3) == queries_for(1)


def test_a_revoke_then_re_award_before_it_plays_is_still_one_moment():
    game = _game()
    gb = _badge(game)
    profile = _hunter()
    _finished(profile, game)
    evaluate_and_apply(profile)
    ProfileGame.objects.filter(profile=profile).update(progress=10)
    ProfileTrophyGroup.objects.filter(profile=profile).update(progress=10)
    evaluate_and_apply(profile)
    assert not UserGroupBadge.objects.filter(profile=profile, group_badge=gb).exists()

    _finished(profile, game)
    evaluate_and_apply(profile)

    assert Moment.objects.filter(profile=profile, group_badge=gb).count() == 1
    assert len(moment_service.pending(profile)) == 1


@pytest.mark.parametrize('ids', [[True], [2 ** 63], [0], list(range(1, moment_service.QUEUE_CAP + 2)), []])
def test_the_played_endpoint_refuses_bad_ids(client, ids):
    profile = _hunter()
    client.force_login(profile.user)

    r = client.post(reverse('api:moments-played'), {'ids': ids}, content_type='application/json')

    assert r.status_code == 400


def test_the_played_endpoint_needs_the_csrf_token():
    from django.test import Client

    profile = _hunter()
    m = Moment.objects.create(profile=profile, kind='welcome', occurred_at=NOW())
    client = Client(enforce_csrf_checks=True)
    client.force_login(profile.user)

    r = client.post(reverse('api:moments-played'), {'ids': [m.id]}, content_type='application/json')

    assert r.status_code == 403
    assert Moment.objects.get(id=m.id).played_at is None


def test_a_platinum_with_no_psn_rate_yet_says_nothing_about_it(rf):
    profile = _hunter()
    et = _plat(profile)
    Trophy.objects.filter(id=et.trophy_id).update(trophy_earn_rate=0)
    moment_service.record_platinum(profile, et, et.earned_date_time)

    assert 'of PSN players have it' not in _render('{% moments_overlay %}', profile, rf)


def test_a_platinum_card_shows_which_platinum_it_is(rf):
    profile = _hunter()
    et = _plat(profile)
    moment_service.record_platinum(profile, et, et.earned_date_time)
    number = Moment.objects.get().data['number']

    html = _render('{% moments_overlay %}', profile, rf)

    assert number and f'#<span data-countup="{number}">{number}</span>' in html
