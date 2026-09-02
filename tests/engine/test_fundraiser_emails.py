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


def _preheader(body):
    """The hidden inbox preview line, pulled out of the base's mso-hide div."""
    match = re.search(r'mso-hide: all[^>]*>(.*?)&zwnj;', body, re.S)
    return _html.unescape(match.group(1)).strip() if match else ''


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
    if name == 'badge_claim_confirmation.html':
        ctx['claim_url'] = CLAIM_URL
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
        # Only v2 emits this. Everything above is satisfied by the child's OWN markup, so without
        # it the whole test stays green after a revert to the legacy base.
        assert 'mso-hide: all' in body, 'no preheader block: this is not riding v2'


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

    # The preheader names the campaign too, so this half has to read the visible body.
    assert 'Badge Artwork Fund' in _plain(_content(_receipt(donation=donation)))
    assert 'August 24, 2026' in body
    assert 'UTC' in body, 'an unlabelled date is an off-by-one for every donor west of UTC'
    assert TXN in body, 'the transaction id is the only handle a support request has'


def test_the_receipt_dates_from_the_creation_stamp_when_it_never_completed():
    """`completed_at` is nullable on the model, so the row is guarded even though the only sender
    (complete_donation) stamps it before sending. An unguarded row would print "None" as the date."""
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
    assert 'TestHunter' in _content(_receipt())

    # Reverting to `user.first_name|default:user.email` raises rather than failing an assertion
    # (filter arguments must resolve), so the regression is pinned at the source instead.
    src = (Path(settings.BASE_DIR) / 'templates' / 'emails'
           / 'donation_receipt.html').read_text(encoding='utf-8')
    assert 'user.email' not in src and 'user.first_name' not in src


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
def test_the_twins_name_the_series_in_the_preheader(template):
    """The preview line is what a donor reads BEFORE opening, and it is the first thing in the
    plaintext part. "The badge series you commissioned is live" makes them open the mail to find
    out which one."""
    assert 'Trophy Hunter' in _preheader(_twin(template))


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


def test_the_claim_email_points_at_the_only_page_that_shows_a_status():
    """`/badges/<slug>/` renders the badge and knows nothing about artwork claims; the fundraiser
    page renders "Your claimed badges" with a state chip per claim. A rewrite of this email once
    told donors to watch the badge page, which is a dead end and the only status instruction the
    whole arc gives them."""
    body = _twin('badge_claim_confirmation.html')

    assert 'fundraiser page' in _content(body)
    assert 'badge page shows the status' not in _content(body)
    assert CLAIM_URL in _plain(body), 'the status pointer must survive the plaintext strip'


def test_the_claim_email_drops_the_status_pointer_rather_than_printing_a_bare_label():
    """`claim_url` is guarded: an older send path or a resend without it must not ship
    "Track the queue on the fundraiser page:" followed by nothing."""
    body = _twin('badge_claim_confirmation.html', claim_url='')

    assert 'Track the queue' not in body
    assert 'View the badge series' in body, 'the primary CTA is unconditional'


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

    class _Series:
        name = 'Trophy Hunter'

    class _Claim:
        series_name = ''
        series_id = 7          # NOT nullable on the model: a persisted claim always has one
        series = _Series()

    claim = _Claim()
    # The branch a real blank denorm takes. Asserting only the final fallback passes even with
    # this lookup deleted, and BadgeSeries.name is required, so the fallback is unreachable in
    # production -- a pin on it alone pins dead code.
    assert DonationService._claim_series_name(claim) == 'Trophy Hunter'

    claim.series_name = 'Claimed As This'
    assert DonationService._claim_series_name(claim) == 'Claimed As This', 'the denorm wins'

    claim.series_name = ''
    claim.series_id = None
    claim.series = None
    assert DonationService._claim_series_name(claim) == 'your badge series'


def test_the_fundraiser_senders_dropped_the_dead_preference_token():
    """It was minted (signed, per send) for all three by one shared helper, for a parked page
    and templates that never read it."""
    src = (Path(settings.BASE_DIR) / 'fundraiser' / 'services'
           / 'donation_service.py').read_text(encoding='utf-8')

    assert 'preference_url' not in src
    assert 'EmailPreferenceService' not in src, 'the import outlived its only caller'
    assert 'SITE_URL}/badges/' not in src, 'literal URL should be reverse()'
