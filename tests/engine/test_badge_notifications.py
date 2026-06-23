"""Tests for the badge notification rework.

Badges no longer grant Discord roles. Earned-badge notifications are sent as ONE
consolidated Discord batch per run (gated on the profile being Discord-linked), and the
refresh command can silence every channel (Discord + on-site + email).
"""
import pytest

from notifications.services.deferred_notification_service import DeferredNotificationService
from trophies.discord_utils import discord_notifications
from trophies.discord_utils.discord_notifications import send_badge_earned_notification
from trophies.models import UserBadge
from trophies.services import badge_refresh_service
from trophies.services.badge_refresh_service import refresh_badge_series_awards
from tests.factories import (
    BadgeFactory, ConceptFactory, GameFactory, ProfileFactory, ProfileGameFactory, StageFactory,
)

pytestmark = pytest.mark.django_db


def _linked(**kw):
    """A Discord-linked profile (verified + discord_id)."""
    return ProfileFactory(is_discord_verified=True, discord_id='123456789', **kw)


def _earnable_series(slug):
    """A tier-1 series badge whose single stage holds one concept+game."""
    concept = ConceptFactory()
    game = GameFactory(concept=concept)
    badge = BadgeFactory(series_slug=slug, tier=1, display_series='Test Series')
    StageFactory(series_slug=slug, stage_number=1).concepts.add(concept)
    return badge, concept, game


# --- send_badge_earned_notification (the single Discord path) ------------------


def test_notification_noop_without_badges(monkeypatch):
    calls = []
    monkeypatch.setattr(discord_notifications, 'queue_webhook_send', lambda p: calls.append(p))

    send_badge_earned_notification(_linked(), [])

    assert calls == []


def test_notification_noop_when_not_discord_linked(monkeypatch):
    calls = []
    monkeypatch.setattr(discord_notifications, 'queue_webhook_send', lambda p: calls.append(p))

    send_badge_earned_notification(ProfileFactory(), [BadgeFactory()])  # not linked

    assert calls == []


def test_notification_sends_one_batch_listing_every_badge(monkeypatch):
    calls = []
    monkeypatch.setattr(discord_notifications, 'queue_webhook_send', lambda p: calls.append(p))
    b1 = BadgeFactory(tier=1, display_series='Resident Evil')
    b2 = BadgeFactory(tier=4, display_series='Devil May Cry')

    send_badge_earned_notification(_linked(), [b1, b2])

    assert len(calls) == 1  # ONE batch embed, not per-badge
    desc = calls[0]['embeds'][0]['description']
    assert 'Resident Evil' in desc and 'Devil May Cry' in desc
    assert 'role' not in desc.lower()  # roles are retired


# --- refresh_badge_series_awards: notifications + the silence flag -------------


def test_refresh_notifies_discord_and_web_when_earned(monkeypatch):
    discord_calls, web_calls = [], []
    monkeypatch.setattr(
        badge_refresh_service, 'send_badge_earned_notification',
        lambda profile, badges: discord_calls.append((profile.id, list(badges))),
    )
    monkeypatch.setattr(
        DeferredNotificationService, 'create_badge_notifications',
        lambda profile_id, **kw: web_calls.append(profile_id),
    )
    badge, _concept, game = _earnable_series('rf-notify')
    profile = _linked()
    ProfileGameFactory(profile=profile, game=game, progress=100, has_plat=True)

    refresh_badge_series_awards('rf-notify')

    assert discord_calls == [(profile.id, [badge])]  # one batch, the newly-earned badge
    assert web_calls == [profile.id]


def test_refresh_skip_notifications_silences_every_channel(monkeypatch):
    discord_calls, web_calls = [], []
    monkeypatch.setattr(
        badge_refresh_service, 'send_badge_earned_notification',
        lambda profile, badges: discord_calls.append(profile.id),
    )
    monkeypatch.setattr(
        DeferredNotificationService, 'create_badge_notifications',
        lambda profile_id, **kw: web_calls.append(profile_id),
    )
    discarded = []
    monkeypatch.setattr(
        DeferredNotificationService, 'discard_badge_notifications',
        lambda profile_id: discarded.append(profile_id),
    )
    badge, _concept, game = _earnable_series('rf-silent')
    profile = _linked()
    ProfileGameFactory(profile=profile, game=game, progress=100, has_plat=True)

    refresh_badge_series_awards('rf-silent', skip_notifications=True)

    assert discord_calls == [] and web_calls == []     # no channel emitted
    assert discarded == [profile.id]                   # queued web/email DRAINED, not deferred
    assert UserBadge.objects.filter(profile=profile, badge=badge).exists()  # but still awarded
