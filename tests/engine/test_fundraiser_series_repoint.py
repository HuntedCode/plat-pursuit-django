"""The fundraiser's badge-artwork flow, repointed onto `BadgeSeries` (2026-08).

The bug this closes: `complete_badge_claim` credited the donor via `Badge.funded_by`, but the medallion
renders `GroupBadge.effective_funded_by` (`funded_by_override or series.funded_by`). Somebody paid for
artwork on a live payment page and was credited on a row nothing displays. It broke silently when the new
badge display shipped, not when the fundraiser was written, which is why no test caught it: every
assertion in the suite checked the WRITE.

So the tests here assert the read-through wherever they can, and the claimable predicate -- which used to
exist as three hand-copied filter stacks (picker, tracker, claim validation) and is now one helper.
"""
import itertools

import pytest
from django.utils import timezone

from fundraiser.models import Donation, DonationBadgeClaim, Fundraiser
from fundraiser.services.donation_service import DonationService
from trophies.models import BadgeSeries
from tests.factories import (
    BadgeSeriesFactory, GroupBadgeFactory, PlatformGroupFactory, ProfileFactory,
)

pytestmark = pytest.mark.django_db


def _claimable_series(slug='souls', name='Souls Series', live=True, **kwargs):
    """A series in the state a donor can claim: live edition, no art yet, not user-submitted."""
    series = BadgeSeriesFactory(series_slug=slug, name=name, **kwargs)
    GroupBadgeFactory(
        series=series, platform_group=PlatformGroupFactory(key=f'{slug}-grp'), is_live=live,
    )
    return series


_donation_seq = itertools.count()


def _donation(picks=1):
    """A completed donation with picks to spend. `Fundraiser.slug` is unique, so each call gets its own
    campaign -- two donors claiming in one test is the normal case, not the exception."""
    n = next(_donation_seq)
    fr = Fundraiser.objects.create(
        name='Artwork', slug=f'artwork-{n}', description='', start_date=timezone.now(),
        campaign_type='badge_artwork',
    )
    return Donation.objects.create(
        fundraiser=fr, amount=10 * picks, provider='stripe',
        provider_transaction_id=f'tx-{n}', status='completed',
        badge_picks_earned=picks, badge_picks_used=0,
    )


# --- the claimable predicate --------------------------------------------------


def test_a_live_unclaimed_series_without_art_is_claimable():
    series = _claimable_series()
    assert list(DonationService.series_needing_artwork()) == [series]


def test_a_series_with_no_live_edition_is_not_claimable():
    """There is no `is_live` on BadgeSeries -- liveness is per-edition. A dormant series is invisible to
    hunters, so offering its artwork for sale would be selling something nobody can see."""
    _claimable_series(live=False)
    assert list(DonationService.series_needing_artwork()) == []


def test_a_series_that_already_has_art_is_not_claimable():
    series = _claimable_series()
    series.badge_image = 'badges/series/already-there.png'
    series.save(update_fields=['badge_image'])
    assert list(DonationService.series_needing_artwork()) == []


def test_a_user_submitted_series_is_not_claimable():
    """User badges draw their art from the submitter's avatar, so there is nothing to commission."""
    _claimable_series(badge_type='user')
    assert list(DonationService.series_needing_artwork()) == []


def test_an_already_claimed_series_is_not_claimable():
    series = _claimable_series()
    DonationBadgeClaim.objects.create(
        donation=_donation(), profile=ProfileFactory(), series=series,
        series_slug=series.series_slug, series_name=series.name,
    )
    assert list(DonationService.series_needing_artwork()) == []


def test_a_two_edition_series_is_offered_once():
    """`group_badges__is_live=True` is a join, so a series shipping in both editions would appear twice
    without the `.distinct()`. A duplicate in the picker is a donor claiming a thing already claimed."""
    series = _claimable_series()
    GroupBadgeFactory(
        series=series, platform_group=PlatformGroupFactory(key='second-ed'), is_live=True,
    )
    assert list(DonationService.series_needing_artwork()) == [series]


# --- claiming -----------------------------------------------------------------


def test_claiming_records_the_series_and_spends_a_pick():
    series = _claimable_series()
    profile = ProfileFactory()
    donation = _donation()

    claim = DonationService.claim_badge(donation, profile, series.id)

    assert claim.series_id == series.id
    assert claim.series_slug == series.series_slug
    donation.refresh_from_db()
    assert donation.badge_picks_used == 1


def test_claiming_a_dormant_series_is_rejected():
    """The picker already excludes it; this is the server-side half. The two used to be separate filter
    stacks that could disagree, which is exactly how a donor spends a pick on something invalid."""
    series = _claimable_series(live=False)
    with pytest.raises(ValueError, match='not live'):
        DonationService.claim_badge(_donation(), ProfileFactory(), series.id)


def test_claiming_the_same_series_twice_is_rejected():
    series = _claimable_series()
    DonationService.claim_badge(_donation(), ProfileFactory(), series.id)
    with pytest.raises(ValueError, match='already been claimed'):
        DonationService.claim_badge(_donation(), ProfileFactory(), series.id)


# --- completion: the actual bug ----------------------------------------------


def test_completing_a_claim_credits_the_donor_where_the_medallion_reads_it(monkeypatch):
    """The regression, stated as the read rather than the write.

    `series.funded_by` is only correct because `GroupBadge.effective_funded_by` resolves it. Asserting
    the column alone would have passed against the old code too, if the old code had written this column
    -- so the assertion that matters is the one on the property the template calls.
    """
    monkeypatch.setattr(DonationService, 'send_artwork_complete_email', staticmethod(lambda c: None))
    monkeypatch.setattr(DonationService, 'send_artwork_complete_notification', staticmethod(lambda c: None))

    series = _claimable_series()
    edition = series.group_badges.first()
    profile = ProfileFactory()
    claim = DonationService.claim_badge(_donation(), profile, series.id)

    assert DonationService.complete_badge_claim(claim) is True

    edition.refresh_from_db()
    assert edition.effective_funded_by == profile


def test_a_per_edition_override_still_wins_over_the_series_credit(monkeypatch):
    """`funded_by_override` exists so one edition's art can have a different funder. The series-level
    credit must not silently overwrite that -- it is a different person's money."""
    monkeypatch.setattr(DonationService, 'send_artwork_complete_email', staticmethod(lambda c: None))
    monkeypatch.setattr(DonationService, 'send_artwork_complete_notification', staticmethod(lambda c: None))

    series = _claimable_series()
    edition = series.group_badges.first()
    other_donor = ProfileFactory()
    edition.funded_by_override = other_donor
    edition.save(update_fields=['funded_by_override'])

    claim = DonationService.claim_badge(_donation(), ProfileFactory(), series.id)
    DonationService.complete_badge_claim(claim)

    edition.refresh_from_db()
    assert edition.effective_funded_by == other_donor


def test_the_representative_edition_prefers_a_live_one():
    """`representative_group_badge` is what the fundraiser's claim tiles draw with. A dormant edition
    would render a medallion for something hunters cannot see."""
    series = BadgeSeriesFactory(series_slug='rep')
    dormant = GroupBadgeFactory(
        series=series, platform_group=PlatformGroupFactory(key='rep-dormant', sort_order=1), is_live=False,
    )
    live = GroupBadgeFactory(
        series=series, platform_group=PlatformGroupFactory(key='rep-live', sort_order=2), is_live=True,
    )

    assert series.representative_group_badge == live
    assert series.representative_group_badge != dormant


def test_the_representative_edition_is_none_before_editions_exist():
    """A series authored but not yet given editions must not explode a template."""
    assert BadgeSeriesFactory(series_slug='bare').representative_group_badge is None
