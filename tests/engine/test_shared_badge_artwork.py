"""Sister badges share one piece of artwork.

A franchise badge and a series badge can be the same subject wearing two labels -- "God of War" the
franchise and "God of War" the series. Commissioning art twice for one subject wastes a donor's
money and puts two different images on what a reader sees as one thing.

`BadgeSeries.artwork_source` says "display that series' art", and the funder credit travels with the
image so the person who paid is credited on both badges.

NOT derived from the shared `franchise` FK, which is the obvious free answer. Two reasons, either
sufficient: a franchise has SEVERAL sister series (God of War 2018 and Ragnarok are separate series
badges), so derivation has no deterministic answer for whose art wins; and `BadgeSeries.franchise`
already means something else load-bearing -- `audit_badge_coverage` reads it as "this series is
expected to cover every game in that franchise", so populating it on a series badge to express
sisterhood would flag every other franchise game as a coverage gap, by email, daily.
"""
import pytest
from django.core.exceptions import ValidationError

from fundraiser.services.donation_service import DonationService
from trophies.models import BadgeSeries
from tests.factories import BadgeSeriesFactory, GroupBadgeFactory, PlatformGroupFactory, ProfileFactory

pytestmark = pytest.mark.django_db


def _series(slug, name, badge_type='series', **kw):
    return BadgeSeriesFactory(series_slug=slug, name=name, badge_type=badge_type, **kw)


def _live_edition(series, key='ultra-hd'):
    return GroupBadgeFactory(
        series=series,
        platform_group=PlatformGroupFactory(key=key, name=key.title(), platforms=['PS4', 'PS5']),
        is_live=True,
    )


def _with_art(series, funder=None):
    """Give a series real artwork and a credited funder."""
    series.badge_image = 'badges/series/gow.png'
    series.holo_badge_image = 'badges/series/gow_holo.png'
    series.funded_by = funder or ProfileFactory(display_psn_username='TheDonor')
    series.save(update_fields=['badge_image', 'holo_badge_image', 'funded_by'])
    return series


# ── the borrowed art itself ──────────────────────────────────────────────────────────────────────

def test_a_borrowing_series_displays_the_lenders_art():
    lender = _with_art(_series('gow-series', 'God of War'))
    borrower = _series('gow-franchise', 'God of War', badge_type='franchise',
                       artwork_source=lender)
    edition = _live_edition(borrower)

    layers = edition.art_layers()

    assert layers['main'] == lender.badge_image.url
    assert layers['has_custom_image'] is True, 'a borrowed image is still custom art, not the default'


def test_the_holo_art_is_borrowed_from_the_same_place():
    """Resolving the main image and the holo separately is how a badge ends up wearing one series'
    art and another's holo."""
    lender = _with_art(_series('gow-series', 'God of War'))
    borrower = _series('gow-franchise', 'GoW', badge_type='franchise', artwork_source=lender)

    assert _live_edition(borrower).effective_holo_image == lender.holo_badge_image


def test_the_funder_credit_travels_with_the_image():
    """THE point of resolving art and credit together. The borrower has no funder of its own, so
    reading its own field would display someone's paid-for artwork crediting nobody."""
    donor = ProfileFactory(display_psn_username='PaidForIt')
    lender = _with_art(_series('gow-series', 'God of War'), funder=donor)
    borrower = _series('gow-franchise', 'GoW', badge_type='franchise', artwork_source=lender)

    assert _live_edition(borrower).effective_funded_by == donor


def test_a_series_with_its_own_art_ignores_the_source():
    """Borrowing is a fallback, not an override -- own art always wins, and keeps its own credit."""
    lender_donor = ProfileFactory(display_psn_username='Lender')
    own_donor = ProfileFactory(display_psn_username='Owner')
    lender = _with_art(_series('gow-series', 'God of War'), funder=lender_donor)
    borrower = _with_art(_series('gow-franchise', 'GoW', badge_type='franchise',
                                artwork_source=lender), funder=own_donor)
    borrower.badge_image = 'badges/series/own.png'
    borrower.save(update_fields=['badge_image'])

    edition = _live_edition(borrower)

    assert edition.art_layers()['main'] == borrower.badge_image.url
    assert edition.effective_funded_by == own_donor, 'own art must keep its own credit'


def test_a_per_edition_override_keeps_its_own_credit():
    """An edition carrying its own art is not borrowing, so the lender must not be credited for it."""
    lender_donor = ProfileFactory(display_psn_username='Lender')
    override_donor = ProfileFactory(display_psn_username='EditionFunder')
    lender = _with_art(_series('gow-series', 'God of War'), funder=lender_donor)
    borrower = _series('gow-franchise', 'GoW', badge_type='franchise', artwork_source=lender)

    edition = _live_edition(borrower)
    edition.badge_image_override = 'badges/group/special.png'
    edition.funded_by_override = override_donor
    edition.save(update_fields=['badge_image_override', 'funded_by_override'])

    assert edition.art_layers()['main'] == edition.badge_image_override.url
    assert edition.effective_funded_by == override_donor


def test_borrowing_from_a_series_with_no_art_falls_through_to_the_default():
    """A link to a series that has not been drawn yet must not break the medallion -- it renders the
    placeholder, exactly as an unlinked series with no art does."""
    lender = _series('gow-series', 'God of War')          # no art yet
    borrower = _series('gow-franchise', 'GoW', badge_type='franchise', artwork_source=lender)

    layers = _live_edition(borrower).art_layers()

    assert layers['has_custom_image'] is False
    assert layers['main'], 'the placeholder must still resolve to a real static URL'


# ── the claim guard ──────────────────────────────────────────────────────────────────────────────

def test_a_borrowing_series_cannot_be_claimed_for_artwork():
    """It already displays the lender's image, so funding it would buy a second piece of art for a
    subject that has one."""
    lender = _series('gow-series', 'God of War')
    _live_edition(lender)
    borrower = _series('gow-franchise', 'GoW', badge_type='franchise', artwork_source=lender)
    _live_edition(borrower, key='legacy-hd')

    claimable = set(DonationService.series_needing_artwork().values_list('series_slug', flat=True))

    assert 'gow-franchise' not in claimable, 'a borrowing series was offered for commissioning'
    assert 'gow-series' in claimable, 'the SOURCE is what a donor should claim'


def test_the_guard_holds_before_the_lender_has_any_art():
    """Keyed on the LINK, not on the lender having an image. Keying on "has art" would leave the
    borrower claimable in exactly the window a donor would claim it -- before the art lands."""
    lender = _series('gow-series', 'God of War')     # deliberately undrawn
    _live_edition(lender)
    borrower = _series('gow-franchise', 'GoW', badge_type='franchise', artwork_source=lender)
    _live_edition(borrower, key='legacy-hd')

    claimable = set(DonationService.series_needing_artwork().values_list('series_slug', flat=True))

    assert 'gow-franchise' not in claimable
    assert 'gow-series' in claimable


def test_an_unlinked_series_is_still_claimable():
    """The guard must not narrow the pool beyond its one case."""
    plain = _series('plain', 'Plain Series')
    _live_edition(plain)

    assert 'plain' in set(
        DonationService.series_needing_artwork().values_list('series_slug', flat=True))


# ── the one-hop rule ─────────────────────────────────────────────────────────────────────────────

def test_a_series_cannot_borrow_from_itself():
    s = _series('gow-series', 'God of War')
    s.artwork_source = s

    with pytest.raises(ValidationError):
        s.clean()


def test_a_chain_is_refused():
    """One hop keeps "where does this art come from" a lookup rather than a traversal, and spares
    every reader from agreeing on a depth cap and a cycle guard."""
    holder = _with_art(_series('a', 'A'))
    middle = _series('b', 'B', artwork_source=holder)
    tail = _series('c', 'C')
    tail.artwork_source = middle

    with pytest.raises(ValidationError):
        tail.clean()


def test_a_lender_cannot_start_borrowing():
    """The same rule from the other end: a series others depend on cannot become the middle of a
    chain by acquiring a source of its own."""
    holder = _with_art(_series('a', 'A'))
    lender = _series('b', 'B')
    _series('c', 'C', artwork_source=lender)      # c depends on b

    lender.artwork_source = holder
    with pytest.raises(ValidationError):
        lender.clean()


def test_a_single_hop_validates():
    holder = _with_art(_series('a', 'A'))
    borrower = _series('b', 'B', badge_type='franchise')
    borrower.artwork_source = holder

    borrower.clean()   # must not raise


def test_deleting_the_lender_leaves_the_borrower_intact():
    """SET_NULL, not CASCADE: losing the art must not delete a badge series people hold badges in."""
    lender = _with_art(_series('gow-series', 'God of War'))
    borrower = _series('gow-franchise', 'GoW', badge_type='franchise', artwork_source=lender)

    lender.delete()

    borrower.refresh_from_db()
    assert borrower.artwork_source is None
    assert BadgeSeries.objects.filter(pk=borrower.pk).exists()
