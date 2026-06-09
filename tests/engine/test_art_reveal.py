"""Tests for the Badge Art Reveal engine: the badge-tied platinum counter and
the reveal reconciliation (count -> released set + art pushed onto the badge).

The counter is community-wide and must count each qualifying platinum exactly
once. Reconciliation must be idempotent and cap at the number of items.
"""

from datetime import timedelta
from unittest.mock import PropertyMock, patch

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone

from art_reveal.models import ArtRevealEvent, ArtRevealItem
from art_reveal.services import compute_badge_platinum_count, reconcile_event
from tests.factories import (
    BadgeFactory, ConceptFactory, EarnedTrophyFactory, GameFactory,
    ProfileFactory, StageFactory, TrophyFactory,
)

pytestmark = pytest.mark.django_db

PNG = b'\x89PNG\r\n\x1a\n\x00\x00'  # minimal bytes; ImageField isn't validated on save


@pytest.fixture
def media_root(settings, tmp_path):
    settings.MEDIA_ROOT = str(tmp_path)
    return tmp_path


def _badge_platinum_trophy(series='s', shovelware='clean'):
    """A platinum trophy on a game whose concept is covered by a badge (Badge and
    Stage are joined by series_slug)."""
    concept = ConceptFactory()
    BadgeFactory(series_slug=series, tier=1)
    StageFactory(series_slug=series).concepts.add(concept)
    game = GameFactory(concept=concept, shovelware_status=shovelware)
    return TrophyFactory(game=game, trophy_type='platinum')


# --- the counter -------------------------------------------------------------


def test_counts_only_badge_platinums_in_window():
    start = timezone.now() - timedelta(days=1)
    plat = _badge_platinum_trophy()

    # Three community members earn the badge platinum inside the window.
    for _ in range(3):
        EarnedTrophyFactory(trophy=plat)

    # None of the following may be counted:
    EarnedTrophyFactory(trophy=plat, earned_date_time=start - timedelta(days=3))  # before start
    EarnedTrophyFactory(trophy=plat, earned=False)  # not actually earned
    EarnedTrophyFactory(  # platinum, but concept has no badge
        trophy=TrophyFactory(game=GameFactory(), trophy_type='platinum'))
    EarnedTrophyFactory(  # badge game, but a bronze not a platinum
        trophy=TrophyFactory(game=plat.game, trophy_type='bronze'))
    flagged = _badge_platinum_trophy(series='s2', shovelware='auto_flagged')
    EarnedTrophyFactory(trophy=flagged)  # badge platinum, but shovelware-flagged game

    assert compute_badge_platinum_count(since=start) == 3


def test_concept_in_multiple_badges_counted_once():
    """A concept covered by two badge series must not double-count its platinum."""
    start = timezone.now() - timedelta(days=1)
    plat = _badge_platinum_trophy(series='s')
    BadgeFactory(series_slug='s2', tier=1)
    StageFactory(series_slug='s2').concepts.add(plat.game.concept)

    EarnedTrophyFactory(trophy=plat)

    assert compute_badge_platinum_count(since=start) == 1


def test_manually_cleared_games_still_count():
    start = timezone.now() - timedelta(days=1)
    plat = _badge_platinum_trophy(shovelware='manually_cleared')
    EarnedTrophyFactory(trophy=plat)
    assert compute_badge_platinum_count(since=start) == 1


# --- reconciliation ----------------------------------------------------------


def _event_with_items(n=4, per=5):
    event = ArtRevealEvent.objects.create(
        name='E', slug='e', is_active=True,
        started_at=timezone.now() - timedelta(days=1), platinums_per_reveal=per,
    )
    for i in range(1, n + 1):
        ArtRevealItem.objects.create(
            event=event, badge=BadgeFactory(series_slug=f'evt-{i}', tier=1), order=i,
            artwork=SimpleUploadedFile(f'{i}.png', PNG, content_type='image/png'),
        )
    return event


def test_reconcile_releases_floor_of_count_over_per_reveal(media_root, monkeypatch):
    event = _event_with_items(n=4, per=5)
    monkeypatch.setattr('art_reveal.services.compute_badge_platinum_count',
                        lambda *, since: 12)

    result = reconcile_event(event)

    assert result['released'] == [1, 2]  # 12 // 5 == 2
    event.refresh_from_db()
    assert event.last_platinum_count == 12
    assert event.released_count == 2


def test_reconcile_is_idempotent(media_root, monkeypatch):
    event = _event_with_items(n=4, per=5)
    monkeypatch.setattr('art_reveal.services.compute_badge_platinum_count',
                        lambda *, since: 12)

    reconcile_event(event)
    second = reconcile_event(event)  # same count, nothing new

    assert second['released'] == []
    assert event.items.filter(released=True).count() == 2


def test_reconcile_advances_and_caps_at_total(media_root, monkeypatch):
    event = _event_with_items(n=4, per=5)
    monkeypatch.setattr('art_reveal.services.compute_badge_platinum_count',
                        lambda *, since: 7)
    reconcile_event(event)  # 1 released

    monkeypatch.setattr('art_reveal.services.compute_badge_platinum_count',
                        lambda *, since: 1000)
    result = reconcile_event(event)  # would be 200, capped at 4

    assert result['released'] == [2, 3, 4]
    assert event.items.filter(released=True).count() == 4


def test_release_pushes_art_onto_badge(media_root, monkeypatch):
    event = _event_with_items(n=1, per=5)
    monkeypatch.setattr('art_reveal.services.compute_badge_platinum_count',
                        lambda *, since: 5)
    item = event.items.first()
    assert not item.badge.badge_image

    reconcile_event(event)

    item.refresh_from_db()
    assert item.released is True
    assert item.released_at is not None
    item.badge.refresh_from_db()
    assert bool(item.badge.badge_image) is True  # art is now live on the badge


# --- progress math -----------------------------------------------------------


def test_progress_math():
    event = ArtRevealEvent(platinums_per_reveal=5, last_platinum_count=12)
    with patch.object(ArtRevealEvent, 'total_items', new_callable=PropertyMock, return_value=4):
        p = event.progress()
    assert p['revealed'] == 2
    assert p['into_current'] == 2
    assert p['remaining_to_next'] == 3
    assert p['next_threshold'] == 15
    assert p['pct_next'] == 40
    assert p['pct_overall'] == 50
    assert p['complete'] is False


def test_banner_payload_is_cached(media_root, monkeypatch, django_assert_num_queries):
    from django.core.cache import cache
    from art_reveal.services import get_active_banner

    cache.clear()
    event = _event_with_items(n=2, per=5)
    ArtRevealEvent.objects.filter(pk=event.pk).update(banner_active=True)
    monkeypatch.setattr('art_reveal.services.compute_badge_platinum_count',
                        lambda *, since: 5)
    reconcile_event(event)  # releases item 1, clears the banner cache

    first = get_active_banner()  # warms the cache
    assert first['name'] == event.name
    assert first['progress']['revealed'] == 1
    assert first['latest']['series_slug'] == event.items.get(order=1).badge.series_slug

    with django_assert_num_queries(0):  # warm cache => no per-request DB work
        cached = get_active_banner()
    assert cached == first


def test_progress_complete_state():
    event = ArtRevealEvent(platinums_per_reveal=5, last_platinum_count=999)
    with patch.object(ArtRevealEvent, 'total_items', new_callable=PropertyMock, return_value=4):
        p = event.progress()
    assert p['revealed'] == 4
    assert p['complete'] is True
    assert p['next_threshold'] is None
    assert p['pct_overall'] == 100


# --- funder claim completion on reveal ---------------------------------------


def _make_claim(badge, profile, status='in_progress'):
    from fundraiser.models import Fundraiser, Donation, DonationBadgeClaim
    fr = Fundraiser.objects.create(
        name='F', slug='fr-' + badge.series_slug, description='',
        start_date=timezone.now(),
    )
    don = Donation.objects.create(
        fundraiser=fr, amount=10, provider='stripe',
        provider_transaction_id='tx-' + badge.series_slug, status='completed',
    )
    return DonationBadgeClaim.objects.create(
        donation=don, profile=profile, badge=badge,
        series_slug=badge.series_slug, series_name=badge.name, status=status,
    )


def test_complete_badge_claim_credits_all_tiers_and_notifies(monkeypatch):
    from fundraiser.services.donation_service import DonationService

    # Verify delegation to the senders without depending on email/template infra.
    calls = []
    monkeypatch.setattr(DonationService, 'send_artwork_complete_email',
                        staticmethod(lambda c: calls.append('email')))
    monkeypatch.setattr(DonationService, 'send_artwork_complete_notification',
                        staticmethod(lambda c: calls.append('notif')))

    profile = ProfileFactory()
    t1 = BadgeFactory(series_slug='claim-s', tier=1)
    t2 = BadgeFactory(series_slug='claim-s', tier=2)
    claim = _make_claim(t1, profile)

    assert DonationService.complete_badge_claim(claim) is True

    claim.refresh_from_db()
    assert claim.status == 'completed'
    assert claim.completed_at is not None
    t1.refresh_from_db()
    t2.refresh_from_db()
    assert t1.funded_by_id == profile.id
    assert t2.funded_by_id == profile.id  # every tier credited
    assert calls == ['email', 'notif']    # email + notification fired

    assert DonationService.complete_badge_claim(claim) is False  # idempotent


def test_reveal_completes_funder_claim(media_root, monkeypatch):
    event = _event_with_items(n=1, per=5)
    item = event.items.first()
    profile = ProfileFactory()
    claim = _make_claim(item.badge, profile)
    monkeypatch.setattr('art_reveal.services.compute_badge_platinum_count',
                        lambda *, since: 5)

    reconcile_event(event)

    claim.refresh_from_db()
    assert claim.status == 'completed'
    item.badge.refresh_from_db()
    assert item.badge.funded_by_id == profile.id


def test_reveal_without_a_claim_is_a_noop(media_root, monkeypatch):
    event = _event_with_items(n=1, per=5)  # badge has no fundraiser claim
    monkeypatch.setattr('art_reveal.services.compute_badge_platinum_count',
                        lambda *, since: 5)

    result = reconcile_event(event)  # must not raise

    assert result['released'] == [1]
    assert event.items.first().released is True


def test_reveal_does_not_recomplete_an_already_completed_claim(media_root, monkeypatch):
    from fundraiser.services.donation_service import DonationService

    event = _event_with_items(n=1, per=5)
    item = event.items.first()
    _make_claim(item.badge, ProfileFactory(), status='completed')

    calls = []
    monkeypatch.setattr(DonationService, 'send_artwork_complete_email',
                        staticmethod(lambda c: calls.append('email')))
    monkeypatch.setattr(DonationService, 'send_artwork_complete_notification',
                        staticmethod(lambda c: calls.append('notif')))
    monkeypatch.setattr('art_reveal.services.compute_badge_platinum_count',
                        lambda *, since: 5)

    reconcile_event(event)

    assert calls == []  # already-completed claim is not re-completed / re-notified
