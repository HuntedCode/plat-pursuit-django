"""The three fundraiser emails on base v2.

None of the three had a test that rendered them before this file. The receipt is the one a donor
keeps, so most of the pins here are about it saying enough to BE a receipt: the amount to two
decimals, the provider by its display name, the date, and the transaction id. The twins are
pinned on the thing that made them fail silently: a blank denormalized series name.
"""
import html as _html
import re
from decimal import Decimal
from pathlib import Path

import pytest
from django.conf import settings
from django.template.loader import render_to_string
from django.utils.html import strip_tags

SITE = 'https://platpursuit.com'
TXN = 'cs_test_a1b2c3d4e5f6g7h8i9j0k1l2m3n4o5p6q7r8s9t0'
BADGE_URL = SITE + '/badges/trophy-hunter/'
CLAIM_URL = SITE + '/fundraiser/badge-artwork-fund/'


def _plain(body):
    stripped = re.sub(r'(?is)<(style|script)[^>]*>.*?</\1>', ' ', body)
    return _html.unescape(strip_tags(stripped))


def _content(body):
    """The visible body, with the hidden preheader dropped: the preview line paraphrases the
    copy, so a pin run against the whole render can pass on the preheader alone.

    Everything before <body> goes too: the <title> repeats the headline, so a hero assertion
    could pass on the tab title alone."""
    visible = body.split('<body', 1)[-1]
    return re.sub(r'<div style="display: none;.*?</div>', '', visible, count=1, flags=re.S)


class _Donation:
    """Only what the template reads. A real Donation needs a fundraiser row and a unique
    transaction id, and none of these pins are about the database."""
    amount = Decimal('25.5')  # unpadded on purpose: Decimal('25.50') needs no filter
    provider_transaction_id = TXN
    completed_at = None
    created_at = None

    def get_provider_display(self):
        return 'PayPal'


class _Fundraiser:
    name = 'Badge Artwork Fund'
    slug = 'badge-artwork-fund'


def _receipt(**over):
    ctx = dict(site_url=SITE, username='TestHunter', donation=_Donation(),
               fundraiser=_Fundraiser(), badge_picks_earned=0, claim_url=CLAIM_URL)
    ctx.update(over)
    return render_to_string('emails/donation_receipt.html', ctx)


def _twin(name, **over):
    ctx = dict(site_url=SITE, username='TestHunter', series_name='Trophy Hunter',
               badge_url=BADGE_URL)
    ctx.update(over)
    return render_to_string('emails/' + name, ctx)


# --- the family contract ---

def test_every_fundraiser_email_rides_the_new_base():
    bodies = [_receipt(), _twin('badge_claim_confirmation.html'), _twin('artwork_complete.html')]

    for body in bodies:
        assert 'role="presentation"' in body
        assert '#667eea' not in body, 'the pre-rebuild purple came back'
        assert chr(8212) not in body
        assert 'Manage your account settings' in body
        # The headline must carry its colour inline or it renders near-black on the dark band.
        assert re.search(r'<h1[^>]*style="[^"]*color: #F0F6FD', body)


def test_every_fundraiser_cta_is_reachable_in_plaintext():
    """strip_tags keeps no hrefs, so a link that only exists as a button is invisible to a
    plaintext reader."""
    for body, url in ((_receipt(), CLAIM_URL),
                      (_twin('badge_claim_confirmation.html'), BADGE_URL),
                      (_twin('artwork_complete.html'), BADGE_URL)):
        assert url in _plain(body), 'the CTA vanishes for a plaintext reader'


# --- the receipt is a record, not just a thank-you ---

def test_the_receipt_states_the_amount_to_two_decimals():
    """`amount` is a Decimal: without floatformat, Decimal('25.5') renders as "25.5", which
    reads as a truncated number on the one email a donor keeps. Both places that print the
    amount are checked, because only one of them lost the filter the first time."""
    body = _content(_receipt())

    assert body.count('$25.50') == 2, 'the hero and the detail row must agree'
    assert '25.5<' not in body and '$25.5 ' not in body


def test_the_receipt_names_the_provider_the_way_the_model_does():
    """The old template ran `provider|title` over the raw column, which renders "Paypal"."""
    body = _content(_receipt())

    assert 'PayPal' in body
    assert 'Paypal' not in body


def test_the_receipt_carries_the_details_that_make_it_quotable():
    """A donor querying a charge months later needs the campaign, the date and the id."""
    import datetime
    donation = _Donation()
    donation.completed_at = datetime.datetime(2026, 8, 24, 12, 0)
    body = _plain(_receipt(donation=donation))

    assert 'Badge Artwork Fund' in body
    assert 'August 24, 2026' in body
    assert TXN in body, 'the transaction id is the only handle a support request has'


def test_the_receipt_dates_from_the_creation_stamp_when_it_never_completed():
    """`completed_at` is nullable. An unguarded date row would print "None" as the date on a
    receipt for money we took."""
    import datetime
    donation = _Donation()
    donation.completed_at = None
    donation.created_at = datetime.datetime(2026, 8, 20, 9, 30)
    body = _plain(_receipt(donation=donation))

    assert 'August 20, 2026' in body
    assert 'None' not in body


def test_the_receipt_greets_by_psn_name_not_by_email_address():
    """The old greeting was `user.first_name|default:user.email`, and signup collects no name,
    so nearly every donor was addressed by their raw email address."""
    body = _content(_receipt())

    assert 'TestHunter' in body
    assert '@' not in _plain(body).split('Donation received')[0], 'an email address led the greeting'


# --- the picks fork decides whether a donor learns they have something to spend ---

def test_the_receipt_tells_a_donor_about_their_picks():
    body = _content(_receipt(badge_picks_earned=2))

    assert '2 badge artwork picks' in body
    assert 'Claim your badge picks' in body


def test_the_receipt_pluralises_a_single_pick():
    body = _content(_receipt(badge_picks_earned=1))

    assert '1 badge artwork pick<' in body.replace('</strong>', '<')


def test_the_receipt_without_picks_says_nothing_about_claiming():
    """Telling a donor to go claim a pick they have not earned sends them to a dead end."""
    body = _content(_receipt(badge_picks_earned=0))

    assert 'Claim your badge picks' not in body
    assert 'View the fundraiser' in body
    assert 'picks earned' not in body.lower()


# --- the twins ---

@pytest.mark.parametrize('template', ['badge_claim_confirmation.html', 'artwork_complete.html'])
def test_the_twins_name_the_series_in_the_subject_and_the_hero(template):
    body = _twin(template)

    assert '<title>' in body and 'Trophy Hunter' in body.split('</title>')[0]
    assert 'Trophy Hunter' in _content(body), 'the hero never names the badge'


@pytest.mark.parametrize('template', ['badge_claim_confirmation.html', 'artwork_complete.html'])
def test_the_twins_share_one_hero_shape(template):
    """Two files, one idiom: an accent bar, an uppercase eyebrow and the series name as display
    type. They drifted into two different boxes once already."""
    body = _twin(template)

    assert 'border-radius: 8px 0 0 8px' in body, 'the accent bar went missing'
    # The colour is part of the needle: the base's own header tagline is uppercase too, and
    # a bare 'text-transform: uppercase' pin passes on the band alone.
    assert 'text-transform: uppercase; color: #4A5768' in body, 'the eyebrow went missing'
    assert re.search(r'font-size: 24px;[^"]*font-weight: 800', body), 'the series name lost its weight'


def test_the_twins_are_coloured_for_staked_versus_finished():
    """The claim is a thing staked (gold), the completion a thing finished (green). Same shape,
    and the colour is the only thing carrying the difference."""
    claim = _twin('badge_claim_confirmation.html')
    done = _twin('artwork_complete.html')

    assert '#E7C25C' in claim and '#2E9E6B' not in claim
    assert '#2E9E6B' in done and '#E7C25C' not in done


def test_the_sender_derives_a_series_name_when_the_denorm_is_blank():
    """`series_name` is blank=True. It fed the subject line directly, so a blank one shipped
    "Badge claimed: " over an email that never named the badge."""
    from fundraiser.services.donation_service import DonationService

    class _Claim:
        series_name = ''
        series_id = None
        series = None

    claim = _Claim()
    assert DonationService._claim_series_name(claim) == 'your badge series'

    claim.series_name = 'Trophy Hunter'
    assert DonationService._claim_series_name(claim) == 'Trophy Hunter'


def test_the_fundraiser_senders_dropped_the_dead_preference_token():
    """It was minted (signed, per send) for all three by one shared helper, for a parked page
    and templates that never read it."""
    src = (Path(settings.BASE_DIR) / 'fundraiser' / 'services'
           / 'donation_service.py').read_text(encoding='utf-8')

    assert 'preference_url' not in src
    assert 'EmailPreferenceService' not in src, 'the import outlived its only caller'
    assert 'SITE_URL}/badges/' not in src, 'literal URL should be reverse()'
