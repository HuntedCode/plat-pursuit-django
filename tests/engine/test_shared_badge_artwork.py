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


# ── the regression guard: series that borrow NOTHING must resolve exactly as before ──────────────

@pytest.mark.parametrize('shape', ['nothing', 'series_art', 'holo_only', 'override_only',
                                   'override_plus_series', 'user_avatar'])
def test_a_non_borrowing_series_is_unaffected(shape):
    """EVERY row in production has artwork_source = None, so the refactor that routed three live
    properties through `_artwork_origin()` had to leave all of them byte-identical. This walks the
    six shapes a series can be in. It is the only test here that guards the EXISTING site rather
    than the new feature, and it is the one that would have caught a bad refactor."""
    funder = ProfileFactory(display_psn_username='Funder')
    series = _series('plain', 'Plain', badge_type='user' if shape == 'user_avatar' else 'series')
    if shape in ('series_art', 'override_plus_series'):
        series.badge_image = 'badges/series/main.png'
    if shape == 'holo_only':
        series.holo_badge_image = 'badges/series/holo.png'
    if shape == 'user_avatar':
        series.submitted_by = ProfileFactory(display_psn_username='Submitter',
                                             avatar_url='https://example.test/a.png')
    series.funded_by = funder
    series.save()

    edition = _live_edition(series)
    if shape in ('override_only', 'override_plus_series'):
        edition.badge_image_override = 'badges/group/override.png'
        edition.save(update_fields=['badge_image_override'])

    layers = edition.art_layers()

    assert series.artwork_source is None, 'this test is about the NON-borrowing path'
    assert edition.effective_funded_by == funder, 'credit must still come from the series itself'
    if shape in ('override_only', 'override_plus_series'):
        assert layers['main'] == edition.badge_image_override.url
    elif shape == 'series_art':
        assert layers['main'] == series.badge_image.url
    elif shape == 'user_avatar':
        assert layers['is_avatar'] is True
    else:
        assert layers['has_custom_image'] is False, f'{shape} should fall through to the default'


# ── the claim ENDPOINT, not just the picker ──────────────────────────────────────────────────────

def test_claiming_a_borrowing_series_is_refused_by_the_service():
    """The picker is a list; THIS is the thing that takes the money, and it reads `series_id`
    straight from the POST body without intersecting the two. Hiding a row from the picker does not
    stop a stale page, a cached id, or a guess from reaching here."""
    from fundraiser.models import Donation, Fundraiser
    from django.utils import timezone as tz

    lender = _series('gow-series', 'God of War')
    borrower = _series('gow-franchise', 'GoW', badge_type='franchise', artwork_source=lender)
    _live_edition(borrower)

    fr = Fundraiser.objects.create(name='F', slug='f', description='d', start_date=tz.now())
    profile = ProfileFactory()
    donation = Donation.objects.create(
        fundraiser=fr, profile=profile, user=profile.user, amount=25,
        provider='stripe', status='completed', badge_picks_earned=1, badge_picks_used=0,
    )

    with pytest.raises(ValueError, match='shares another series'):
        DonationService.claim_badge(donation, profile, borrower.id)

    assert not hasattr(borrower, 'artwork_claim') or borrower.artwork_claim is None
    donation.refresh_from_db()
    assert donation.badge_picks_used == 0, 'a refused claim must not consume the pick'


def test_linking_a_series_with_an_open_claim_is_refused():
    """The ordinary curator workflow is the failure: a donor claims the franchise badge, a curator
    later notices it duplicates the series badge. Linking it would leave the donor waiting forever
    for art nobody intends to draw, and walk the fundraiser tracker past 100%."""
    from fundraiser.models import Donation, DonationBadgeClaim, Fundraiser
    from django.utils import timezone as tz

    lender = _series('gow-series', 'God of War')
    claimed = _series('gow-franchise', 'GoW', badge_type='franchise')
    _live_edition(claimed)
    fr = Fundraiser.objects.create(name='F', slug='f2', description='d', start_date=tz.now())
    profile = ProfileFactory()
    donation = Donation.objects.create(
        fundraiser=fr, profile=profile, user=profile.user, amount=25,
        provider='stripe', status='completed', badge_picks_earned=1, badge_picks_used=1,
    )
    DonationBadgeClaim.objects.create(
        donation=donation, profile=profile, series=claimed,
        series_slug=claimed.series_slug, series_name=claimed.name, status='claimed')

    claimed.refresh_from_db()
    claimed.artwork_source = lender
    with pytest.raises(ValidationError, match='open artwork claim'):
        claimed.clean()


def test_a_completed_claim_does_not_block_linking():
    """Only an OPEN claim strands someone. Art already delivered is a closed story."""
    from fundraiser.models import Donation, DonationBadgeClaim, Fundraiser
    from django.utils import timezone as tz

    lender = _with_art(_series('gow-series', 'God of War'))
    done = _series('gow-franchise', 'GoW', badge_type='franchise')
    fr = Fundraiser.objects.create(name='F', slug='f3', description='d', start_date=tz.now())
    profile = ProfileFactory()
    donation = Donation.objects.create(
        fundraiser=fr, profile=profile, user=profile.user, amount=25,
        provider='stripe', status='completed', badge_picks_earned=1, badge_picks_used=1,
    )
    DonationBadgeClaim.objects.create(
        donation=donation, profile=profile, series=done,
        series_slug=done.series_slug, series_name=done.name, status='completed')

    done.refresh_from_db()
    done.artwork_source = lender
    done.clean()   # must not raise


# ── the other write path ─────────────────────────────────────────────────────────────────────────

def test_an_art_reveal_does_not_overwrite_a_borrowing_series():
    """A reveal writing here would silently END the borrow -- the badge would stop matching its
    sister, which is the divergence the borrow exists to prevent, and with funded_by still empty it
    would credit the reveal art to nobody."""
    from art_reveal.models import ArtRevealEvent, ArtRevealItem
    from django.utils import timezone as tz

    lender = _with_art(_series('gow-series', 'God of War'))
    borrower = _series('gow-franchise', 'GoW', badge_type='franchise', artwork_source=lender)

    event = ArtRevealEvent.objects.create(name='Reveal', slug='reveal', started_at=tz.now())
    item = ArtRevealItem.objects.create(event=event, series=borrower, order=1,
                                        artwork='art_reveal/x.png')

    assert item.release() is True, 'the item still releases; only the write is skipped'

    borrower.refresh_from_db()
    assert not borrower.badge_image, 'the reveal ended the borrow'
    assert _live_edition(borrower).art_layers()['main'] == lender.badge_image.url


# ── partial art: the OR bug that resolution used to have ─────────────────────────────────────────
# Partial art is the NORM, not an edge: ArtRevealItem.release() writes badge_image alone, and the
# fundraiser keys claimability on badge_image alone, so main-without-holo is everywhere and the
# model has no opinion that the two travel together. Resolution keyed on `main OR holo` at both
# levels, which reads naturally and breaks in two places.

def test_a_holo_only_edition_override_does_not_lose_the_borrowed_main_image():
    """THE regression. A curator adds a holo to one edition of a borrowing badge -- an action whose
    entire intent is "add art". Under `override_main OR override_holo`, that flipped the origin to
    the borrower, whose main image is empty, so the badge that wore real art yesterday fell back to
    the grey placeholder today and lost its credit line with it. No error, no log."""
    donor = ProfileFactory(display_psn_username='LenderDonor')
    lender = _with_art(_series('gow-series', 'God of War'), funder=donor)
    borrower = _series('gow-franchise', 'GoW', badge_type='franchise', artwork_source=lender)

    edition = _live_edition(borrower)
    edition.holo_badge_image_override = 'badges/group/holo_only.png'
    edition.save(update_fields=['holo_badge_image_override'])

    layers = edition.art_layers()

    assert layers['main'] == lender.badge_image.url, 'a holo upload wiped the borrowed main image'
    assert layers['has_custom_image'] is True
    assert edition.effective_funded_by == donor, 'and it took the credit line with it'
    assert edition.effective_holo_image == edition.holo_badge_image_override


def test_a_series_holding_only_a_holo_still_borrows_the_main_image():
    """The mirror, one level up. A series with a holo but no main plainly NEEDS a main image, and
    borrowing exists to fill exactly that empty slot -- but `badge_image OR holo_badge_image` read
    the holo as "has its own art" and refused."""
    lender = _with_art(_series('gow-series', 'God of War'))
    borrower = _series('gow-franchise', 'GoW', badge_type='franchise', artwork_source=lender)
    borrower.holo_badge_image = 'badges/series/own_holo.png'
    borrower.save(update_fields=['holo_badge_image'])

    layers = _live_edition(borrower).art_layers()

    assert layers['main'] == lender.badge_image.url, 'a holo-only series refused to borrow a main'
    assert layers['has_custom_image'] is True


def test_the_holo_follows_whichever_series_supplied_the_main():
    """The two halves of one drawing stay together. A badge showing its OWN main takes its own holo
    or none -- never the lender's, which would make the medallion change subject the moment a hunter
    masters it."""
    lender = _with_art(_series('gow-series', 'God of War'))       # has main AND holo
    borrower = _series('gow-franchise', 'GoW', badge_type='franchise', artwork_source=lender)

    # Fully borrowing: the lender's holo comes with the lender's main.
    assert _live_edition(borrower).effective_holo_image == lender.holo_badge_image

    # Now it has its OWN main and no holo -- it must NOT wear the lender's holo over its own art.
    borrower.badge_image = 'badges/series/own_main.png'
    borrower.save(update_fields=['badge_image'])
    borrower.refresh_from_db()
    edition = _live_edition(borrower, key='legacy-hd')

    assert edition.art_layers()['main'] == borrower.badge_image.url
    assert not edition.effective_holo_image, 'own main must not be paired with a borrowed holo'
