"""The last live readers of the legacy badge tables, repointed onto the grouping-badge subsystem
(cutover step 5b.3).

Four surfaces were still reading `Badge` / `UserBadge` / `UserBadgeProgress` after the engine was rebuilt.
None of them had a single test, which is why the drift was invisible: the bot's /recheck-badges could
report badges from an engine nothing writes to any more, and the weekly digest could tell a hunter about
a tier that no longer exists, and the suite would stay green.

Covered here:

  - `RecheckBadgesView` -- the bot contract. Its response keys are consumed by a PlatBot slash command, so
    the shape is pinned as tightly as the behaviour.
  - `WeeklyDigestService.get_badge_updates` -- the email. A badge's secondary label is now its EDITION,
    not a tier name.

`compute_community_stats` is covered next door in test_community_stats_badge_catalog.py.
"""
from unittest.mock import patch

import pytest
from django.conf import settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from core.services.weekly_digest_service import WeeklyDigestService
from trophies.models import (
    GroupBadge, ProfileGame, ProfileTrophyGroup, TrophyGroup, UserGroupBadge,
)
from tests.factories import (
    BadgeSeriesFactory, ConceptFactory, GameFactory, GroupBadgeFactory, PlatformGroupFactory,
    ProfileFactory, StageFactory, UserFactory,
)

pytestmark = pytest.mark.django_db


# --- helpers ------------------------------------------------------------------


def _earnable_series(slug, name, platforms=('PS4', 'PS5'), group_key='ultra-hd', group_name='Ultra HD'):
    """A one-stage series with one live edition and one qualifying game."""
    series = BadgeSeriesFactory(series_slug=slug, name=name)
    group = PlatformGroupFactory(key=group_key, name=group_name, platforms=list(platforms))
    badge = GroupBadgeFactory(series=series, platform_group=group, is_live=True)
    stage = StageFactory(series_slug=slug, stage_number=1)
    concept = ConceptFactory()
    stage.concepts.add(concept)
    game = GameFactory(concept=concept, title_platform=list(platforms))
    return series, badge, game


def _finish(profile, game):
    """Complete the game's base list -- the bar a gating stage measures."""
    ProfileGame.objects.update_or_create(
        profile=profile, game=game, defaults={'progress': 100, 'has_plat': True})
    tg, _ = TrophyGroup.objects.get_or_create(
        game=game, trophy_group_id='default', defaults={'trophy_group_name': 'Base'})
    ProfileTrophyGroup.objects.update_or_create(
        profile=profile, trophy_group=tg, defaults={'progress': 100, 'last_trophy_at': timezone.now()})


def _bot_client():
    """An APIClient authenticated as the Discord bot (IsDiscordBot matches on the token key)."""
    user = UserFactory()
    Token.objects.filter(user=user).delete()
    token = Token.objects.create(user=user, key=settings.BOT_API_KEY)
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f'Token {token.key}')
    return client


# --- the bot's /recheck-badges ------------------------------------------------


def test_recheck_awards_through_the_new_engine_and_reports_it():
    """The end-to-end contract: a hunter who qualifies but holds nothing gets the badge written to
    `UserGroupBadge` (the new table) and named in the response."""
    profile = ProfileFactory(discord_id='555', is_discord_verified=True)
    _, badge, game = _earnable_series('souls', 'Souls Series')
    _finish(profile, game)

    resp = _bot_client().post(reverse('api:recheck-badges'), {'discord_id': '555'}, format='json')

    assert resp.status_code == 200
    body = resp.json()
    assert body['success'] is True
    assert UserGroupBadge.objects.filter(profile=profile, group_badge=badge).exists()
    # Named by series + edition, which is how a group badge reads. Asserting the series name is in there
    # rather than the exact string keeps this from breaking on a __str__ tweak, but it does prove the
    # response is built from the NEW catalog and not an empty legacy one.
    assert len(body['awarded']) == 1
    assert 'Souls Series' in body['awarded'][0]
    assert body['revoked'] == []
    # A LITERAL, not the same queryset the view runs -- re-deriving it made this `1 == 1` and could not
    # tell whether the view scoped to live badges at all.
    assert body['badges_checked'] == 1


@patch('trophies.discord_utils.discord_notifications.queue_webhook_send')
def test_recheck_ignores_dormant_editions_and_stays_silent(mock_send):
    """Two decisions in one run, both previously unasserted.

    Dormant: the view scopes to `is_live=True`, so a curator's unreleased edition must not be evaluated or
    awarded. Without this, /recheck-badges was the one surface that could hand a hunter an invisible badge.

    Silent: `notify=False` is deliberate -- the bot reports the deltas in its own reply, so announcing here
    would double-message the recheck.
    """
    profile = ProfileFactory(discord_id='559', is_discord_verified=True)
    _, live_badge, game = _earnable_series('souls', 'Souls Series')
    _finish(profile, game)

    dormant_series = BadgeSeriesFactory(series_slug='unreleased', name='Unreleased')
    dormant = GroupBadgeFactory(
        series=dormant_series,
        platform_group=PlatformGroupFactory(key='legacy-hd', name='Legacy HD', platforms=['PS4', 'PS5']),
        is_live=False,
    )
    StageFactory(series_slug='unreleased', stage_number=1).concepts.add(game.concept)

    body = _bot_client().post(reverse('api:recheck-badges'), {'discord_id': '559'}, format='json').json()

    assert body['badges_checked'] == 1, 'the dormant edition was counted'
    assert UserGroupBadge.objects.filter(profile=profile, group_badge=live_badge).exists()
    assert not UserGroupBadge.objects.filter(profile=profile, group_badge=dormant).exists(),         'a dormant edition was awarded'
    assert mock_send.call_count == 0, 'the recheck announced as well as replying'


def test_recheck_reports_nothing_when_the_hunter_does_not_qualify():
    """The discriminating half: an unqualified hunter must come back empty, not just non-erroring."""
    profile = ProfileFactory(discord_id='556', is_discord_verified=True)
    _earnable_series('souls', 'Souls Series')   # game exists, but the profile never finished it

    body = _bot_client().post(reverse('api:recheck-badges'), {'discord_id': '556'}, format='json').json()

    assert body['awarded'] == []
    assert not UserGroupBadge.objects.filter(profile=profile).exists()


def test_recheck_revokes_a_badge_the_hunter_no_longer_qualifies_for():
    """Revocation flows through the same result dict the awards do. This is the branch that would have
    silently died on the repoint: the old view diffed UserBadge snapshots, the new one trusts the engine."""
    profile = ProfileFactory(discord_id='557', is_discord_verified=True)
    _, badge, _game = _earnable_series('souls', 'Souls Series')
    # Held, but nothing backs it up -- the profile has no completed game.
    UserGroupBadge.objects.create(profile=profile, group_badge=badge)

    body = _bot_client().post(reverse('api:recheck-badges'), {'discord_id': '557'}, format='json').json()

    assert len(body['revoked']) == 1
    assert 'Souls Series' in body['revoked'][0]
    assert not UserGroupBadge.objects.filter(profile=profile, group_badge=badge).exists()


def test_recheck_rejects_an_unverified_profile():
    profile = ProfileFactory(discord_id='558', is_discord_verified=False)
    resp = _bot_client().post(reverse('api:recheck-badges'), {'discord_id': '558'}, format='json')
    assert resp.status_code == 400
    assert not UserGroupBadge.objects.filter(profile=profile).exists()


# --- the weekly digest --------------------------------------------------------


def test_digest_reports_badges_earned_this_week_with_their_edition():
    """`tier_name` is gone; the secondary label is the edition the badge was earned in."""
    profile = ProfileFactory()
    _, badge, _game = _earnable_series('souls', 'Souls Series', group_name='Ultra HD')
    week_start = timezone.now() - timezone.timedelta(days=3)
    week_end = timezone.now() + timezone.timedelta(days=1)
    UserGroupBadge.objects.create(profile=profile, group_badge=badge)

    updates = WeeklyDigestService.get_badge_updates(profile, week_start, week_end)

    assert updates['badges_earned'] == [{'name': 'Souls Series', 'edition': 'Ultra HD'}]


def test_digest_excludes_badges_awarded_outside_the_window():
    """The window is the whole point of the block; without this the test above passes on any badge held."""
    profile = ProfileFactory()
    _, badge, _game = _earnable_series('souls', 'Souls Series')
    ugb = UserGroupBadge.objects.create(profile=profile, group_badge=badge)
    UserGroupBadge.objects.filter(pk=ugb.pk).update(
        created_at=timezone.now() - timezone.timedelta(days=30))

    updates = WeeklyDigestService.get_badge_updates(
        profile,
        timezone.now() - timezone.timedelta(days=7),
        timezone.now() + timezone.timedelta(days=1),
    )

    assert updates['badges_earned'] == []


def test_digest_windows_on_award_time_not_completion_date():
    """The distinction that broke this block once. `earned_at` is the hunter's COMPLETION date and the
    engine rewrites it; `created_at` is when we awarded the row.

    A series shipped today and awarded to someone who platted its games in 2019 IS news to them. Windowing
    on `earned_at` reported nothing -- the launch of a whole series was invisible to every digest.
    """
    profile = ProfileFactory()
    _, badge, _game = _earnable_series('souls', 'Souls Series')
    ugb = UserGroupBadge.objects.create(profile=profile, group_badge=badge)
    # Awarded just now; completed years ago.
    UserGroupBadge.objects.filter(pk=ugb.pk).update(
        earned_at=timezone.now() - timezone.timedelta(days=2000))

    updates = WeeklyDigestService.get_badge_updates(
        profile,
        timezone.now() - timezone.timedelta(days=7),
        timezone.now() + timezone.timedelta(days=1),
    )

    assert len(updates['badges_earned']) == 1, 'a retroactively awarded badge was hidden from the digest'


def test_digest_excludes_dormant_editions():
    """A curator smoke-testing an unreleased edition against a real profile must not mail that hunter
    about a badge nobody can see. Same is_live discipline `badges_held_counts` enforces."""
    profile = ProfileFactory()
    series = BadgeSeriesFactory(series_slug='secret', name='Unreleased')
    group = PlatformGroupFactory(key='ultra-hd', name='Ultra HD')
    dormant = GroupBadgeFactory(series=series, platform_group=group, is_live=False)
    UserGroupBadge.objects.create(profile=profile, group_badge=dormant)

    updates = WeeklyDigestService.get_badge_updates(
        profile,
        timezone.now() - timezone.timedelta(days=7),
        timezone.now() + timezone.timedelta(days=1),
    )

    assert updates['badges_earned'] == []


def test_digest_closest_badge_reads_the_materialized_standing():
    """The digest and the site's Collection CTA must name the same series, so the digest reads
    `collection_service.closest_badge` rather than running its own nearest-badge heuristic."""
    from trophies.models import SeriesBadgeStanding

    profile = ProfileFactory()
    # A LIVE edition is required: `closest_badge` gates on liveness so a dormant series' leftover standing
    # cannot surface in the email (see test_digest_closest_badge_ignores_dormant_series below).
    GroupBadgeFactory(
        series=BadgeSeriesFactory(series_slug='ff', name='Final Fantasy'),
        platform_group=PlatformGroupFactory(key='ff-ultra'),
        is_live=True,
    )
    SeriesBadgeStanding.objects.create(
        profile=profile, series_slug='ff', xp=500,
        progress_bp=6667, stages_cleared=2, stages_total=3,
    )

    updates = WeeklyDigestService.get_badge_updates(
        profile,
        timezone.now() - timezone.timedelta(days=7),
        timezone.now() + timezone.timedelta(days=1),
    )

    assert updates['closest_badge'] == {
        'name': 'Final Fantasy',
        'progress_pct': 67,
        'completed': 2,
        'required': 3,
    }


def test_digest_closest_badge_ignores_dormant_series():
    """`recompute_standing` only deletes standings for the series it was handed, and it is only ever handed
    LIVE ones -- so a standing written while a series was live (or by `evaluate_badges --series`, which
    deliberately includes dormant badges for testing) outlives the series going dormant, forever.

    Without a liveness gate, a curator smoke-testing an unreleased series against real profiles put that
    series into those hunters' weekly email and Home CTA.
    """
    from trophies.models import SeriesBadgeStanding

    profile = ProfileFactory()
    GroupBadgeFactory(
        series=BadgeSeriesFactory(series_slug='secret', name='Unreleased'),
        platform_group=PlatformGroupFactory(key='secret-grp'),
        is_live=False,
    )
    SeriesBadgeStanding.objects.create(
        profile=profile, series_slug='secret', xp=500,
        progress_bp=6667, stages_cleared=2, stages_total=3,
    )

    updates = WeeklyDigestService.get_badge_updates(
        profile,
        timezone.now() - timezone.timedelta(days=7),
        timezone.now() + timezone.timedelta(days=1),
    )
    assert updates['closest_badge'] is None


def test_digest_closest_badge_ignores_a_series_already_held():
    """Under `completion_policy='min_count'` (megamix) a badge is EARNED at `min_required` gating stages
    while `progress_bp` measures cleared/gating -- so a hunter can hold the badge at 3750 bp. Filtering on
    progress alone offered them a medallion already on their wall. Under policy 'all' the two coincide,
    which is why this only ever showed up on megamix series."""
    from trophies.models import SeriesBadgeStanding

    profile = ProfileFactory()
    series = BadgeSeriesFactory(series_slug='mega', name='Megamix', completion_policy='min_count',
                                min_required=3)
    badge = GroupBadgeFactory(series=series, platform_group=PlatformGroupFactory(key='mega-grp'),
                              is_live=True)
    UserGroupBadge.objects.create(profile=profile, group_badge=badge)      # HELD at partial progress
    SeriesBadgeStanding.objects.create(
        profile=profile, series_slug='mega', xp=500,
        progress_bp=3750, stages_cleared=3, stages_total=8,
    )

    updates = WeeklyDigestService.get_badge_updates(
        profile,
        timezone.now() - timezone.timedelta(days=7),
        timezone.now() + timezone.timedelta(days=1),
    )
    assert updates['closest_badge'] is None, 'the digest offered a badge the hunter already holds'


def test_digest_closest_badge_is_none_with_no_standing():
    profile = ProfileFactory()
    updates = WeeklyDigestService.get_badge_updates(
        profile,
        timezone.now() - timezone.timedelta(days=7),
        timezone.now() + timezone.timedelta(days=1),
    )
    assert updates['closest_badge'] is None
