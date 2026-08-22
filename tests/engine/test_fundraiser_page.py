"""The fundraiser PAGE -- the first tests that actually render it.

The gap these close: the BadgeSeries repoint left `DonationService` unimported in the view and
NOBODY noticed, because every existing fundraiser test exercised services or context processors.
The public page 500'd on every badge-artwork campaign render. The live-campaign render test
below IS that regression test.
"""
from datetime import timedelta
from itertools import count
from unittest.mock import patch

import pytest
from django.core.cache import cache
from django.urls import reverse
from django.utils import timezone

from fundraiser.models import Donation, DonationBadgeClaim, Fundraiser
from tests.factories import (
    BadgeSeriesFactory, GroupBadgeFactory, PlatformGroupFactory, ProfileFactory, UserFactory,
)

pytestmark = pytest.mark.django_db

_seq = count(1)


@pytest.fixture(autouse=True)
def _clear_fundraiser_caches():
    for k in ('fundraiser:active_banner', 'fundraiser:live'):
        cache.delete(k)
    yield
    for k in ('fundraiser:active_banner', 'fundraiser:live'):
        cache.delete(k)


def _campaign(**over):
    now = timezone.now()
    n = next(_seq)
    defaults = dict(
        name='Badge Artwork Drive', slug=f'art-drive-{n}', description='Fund the art.',
        campaign_type='badge_artwork', start_date=now - timedelta(days=1), end_date=None,
    )
    defaults.update(over)
    return Fundraiser.objects.create(**defaults)


def _series_with_edition(slug):
    series = BadgeSeriesFactory(series_slug=slug, name=slug.title())
    GroupBadgeFactory(series=series, platform_group=PlatformGroupFactory(key=f'{slug}-grp'),
                      is_live=True)
    return series


# ------------------------------------------------------------------- the page renders ----

def test_a_live_campaign_page_renders(client):
    """THE NameError regression: rendering a live badge-artwork campaign executes
    series_needing_artwork twice in the view; before the import fix this was a 500."""
    campaign = _campaign()
    response = client.get(reverse('fundraiser', kwargs={'slug': campaign.slug}))
    assert response.status_code == 200
    assert campaign.name in response.content.decode()


def test_an_ended_campaign_celebrates(client):
    campaign = _campaign(start_date=timezone.now() - timedelta(days=30),
                         end_date=timezone.now() - timedelta(days=1))
    response = client.get(reverse('fundraiser', kwargs={'slug': campaign.slug}))
    body = response.content.decode()
    assert response.status_code == 200
    assert 'Mission Accomplished' in body
    assert 'donation-form' not in body, 'an ended campaign must not take money'


def test_upcoming_is_staff_preview_only(client):
    campaign = _campaign(start_date=timezone.now() + timedelta(days=3))

    # Non-staff bounce home (the info message rides the redirect; whether home renders it is
    # home's concern, not this page's).
    response = client.get(reverse('fundraiser', kwargs={'slug': campaign.slug}))
    assert response.status_code == 302
    assert response['Location'] == '/'

    staff = UserFactory(is_staff=True)
    client.force_login(staff)
    response = client.get(reverse('fundraiser', kwargs={'slug': campaign.slug}))
    assert response.status_code == 200
    assert 'STAFF PREVIEW' in response.content.decode()


def test_a_completed_claim_renders_its_medallion(client):
    """The empty-gallery bug: the template read `claim.badge_layers` (never set) and included
    the legacy partial; the view builds `claim.medallion` for components/badge_medallion.html."""
    campaign = _campaign()
    series = _series_with_edition('painted')
    donation = Donation.objects.create(
        fundraiser=campaign, amount=10, provider='stripe',
        provider_transaction_id=f'tx-{next(_seq)}', status='completed',
        badge_picks_earned=1, badge_picks_used=1,
    )
    DonationBadgeClaim.objects.create(
        donation=donation, profile=ProfileFactory(), series=series,
        series_slug=series.series_slug, series_name=series.name, status='completed',
    )
    body = client.get(reverse('fundraiser', kwargs={'slug': campaign.slug})).content.decode()
    assert 'pp-med' in body, 'the completed claim gallery lost its medallions again'
    assert 'pp-med__l' in body, 'the medallion rendered but with no art layers'


# ------------------------------------------------------------------------ the landing ----

def test_the_landing_resolves_the_latest_campaign(client):
    live = _campaign()
    response = client.get(reverse('support_fundraiser'))
    assert response.status_code == 302
    assert response['Location'] == reverse('fundraiser', kwargs={'slug': live.slug})


def test_the_landing_resolves_an_ended_campaign_when_nothing_is_live(client):
    ended = _campaign(start_date=timezone.now() - timedelta(days=30),
                      end_date=timezone.now() - timedelta(days=2))
    response = client.get(reverse('support_fundraiser'))
    assert response.status_code == 302
    assert response['Location'] == reverse('fundraiser', kwargs={'slug': ended.slug})


def test_the_landing_skips_a_drafted_upcoming_campaign(client):
    """Audit find: resolving the newest ROW bounced the public home with a toast whenever the
    next campaign was drafted. The landing resolves the latest STARTED campaign instead (the
    ended celebration), or the quiet card when none ever started."""
    ended = _campaign(start_date=timezone.now() - timedelta(days=30),
                      end_date=timezone.now() - timedelta(days=2))
    _campaign(start_date=timezone.now() + timedelta(days=5))
    response = client.get(reverse('support_fundraiser'))
    assert response.status_code == 302
    assert response['Location'] == reverse('fundraiser', kwargs={'slug': ended.slug})

    Fundraiser.objects.filter(slug=ended.slug).delete()
    response = client.get(reverse('support_fundraiser'))
    assert response.status_code == 200, 'only a draft exists: the public gets the quiet card'


def test_the_landing_without_any_campaign_is_a_quiet_card(client):
    response = client.get(reverse('support_fundraiser'))
    assert response.status_code == 200
    assert 'Nothing running right now' in response.content.decode()


# ----------------------------------------------------------------- the frozen contracts ----

def test_the_payment_urls_never_move():
    """These exact paths are baked into every processor session's success/cancel URLs and every
    sent email. Failing this test means an in-flight checkout will land on a 404."""
    assert reverse('fundraiser', kwargs={'slug': 's'}) == '/fundraiser/s/'
    assert reverse('fundraiser_success', kwargs={'slug': 's'}) == '/fundraiser/s/success/'


def test_the_checkout_builds_the_frozen_paths(client):
    campaign = _campaign(minimum_donation=5)
    user = UserFactory()
    ProfileFactory(user=user)
    client.force_login(user)

    with patch('fundraiser.services.donation_service.DonationService.create_stripe_checkout',
               return_value='https://checkout.stripe.com/x') as checkout:
        response = client.post(
            f'/api/v1/fundraiser/{campaign.slug}/donate/',
            {'amount': '10', 'provider': 'stripe'},
            content_type='application/json',
        )
    assert response.status_code == 200, response.content
    kwargs = checkout.call_args.kwargs
    assert kwargs['success_url'].endswith(f'/fundraiser/{campaign.slug}/success/')
    assert kwargs['cancel_url'].endswith(f'/fundraiser/{campaign.slug}/')


def test_email_links_still_carry_the_slug_path():
    """After reverse()-ification the outbound copy is byte-identical -- the win is a loud break
    if the route ever changes."""
    from django.urls import reverse as _r
    assert _r('fundraiser', kwargs={'slug': 'drive'}) == '/fundraiser/drive/'
    import inspect
    from fundraiser.services import donation_service
    source = inspect.getsource(donation_service)
    assert '/fundraiser/{donation' not in source, 'a literal path survived the reverse()-ification'


# ------------------------------------------------------------------- the re-clothe (Phase 2) ----

def test_the_success_redirect_message_finally_renders(client):
    """The page never carried the breadcrumb partial (the site's only messages renderer), so the
    post-payment "Thank you" was silently swallowed. The .sup-msgs block fixes it."""
    campaign = _campaign()
    user = UserFactory()
    ProfileFactory(user=user)
    client.force_login(user)

    fake_session = type('S', (), {'payment_status': 'paid',
                                  'metadata': {'donation_id': ''}})()
    with patch('fundraiser.views.stripe.checkout.Session.retrieve', return_value=fake_session):
        response = client.get(
            reverse('fundraiser_success', kwargs={'slug': campaign.slug}) + '?session_id=cs_x',
            follow=True,
        )
    assert b'Thank you for your donation' in response.content


def test_no_scale_hovers_in_the_fundraiser_surface():
    """Glow, not scale (career-reference-standard 5). The legacy partials/badge.html is out of
    scope; this pins the fundraiser's own JS and templates."""
    import pathlib as _pathlib
    root = _pathlib.Path(__file__).resolve().parents[2]
    files = [root / 'static' / 'js' / 'fundraiser.js']
    files += list((root / 'templates' / 'fundraiser').rglob('*.html'))
    offenders = [str(f.name) for f in files
                 if 'scale-105' in f.read_text(encoding='utf-8')
                 or 'hover:scale' in f.read_text(encoding='utf-8')]
    assert not offenders, f'scale hovers in: {offenders}'


def test_the_double_render_is_fixed(client):
    """The page used to render every available tile TWICE (grid + modal, ~400 tiles). The
    on-page grid is now an 18-tile preview and the modal alone carries the full list."""
    campaign = _campaign()
    for i in range(22):
        _series_with_edition(f'series-{i:02d}')
    user = UserFactory()
    profile = ProfileFactory(user=user)
    Donation.objects.create(
        fundraiser=campaign, amount=10, provider='stripe',
        provider_transaction_id=f'tx-{next(_seq)}', status='completed',
        badge_picks_earned=1, badge_picks_used=0, user=user, profile=profile,
        completed_at=timezone.now(),
    )
    client.force_login(user)
    body = client.get(reverse('fundraiser', kwargs={'slug': campaign.slug})).content.decode()
    on_page = body.split('id="badge-picker-grid"')[0]
    modal = body.split('id="badge-picker-grid"')[1]
    assert on_page.count('badge-pick-option') == 18, 'the on-page grid must be the 18-tile preview'
    assert modal.count('badge-pick-option') == 22, 'the modal must carry the full list'
    assert 'Browse all 22' in body


def test_the_pulse_is_gone_and_the_tracker_rides_the_horizon(client):
    import pathlib as _pathlib
    root = _pathlib.Path(__file__).resolve().parents[2]
    assert 'progress-pulse' not in (root / 'static' / 'css' / 'input.css').read_text(encoding='utf-8')

    campaign = _campaign()
    _series_with_edition('tracked')
    body = client.get(reverse('fundraiser', kwargs={'slug': campaign.slug})).content.decode()
    assert 'pp-horizon' in body
    assert 'progress-pulse' not in body


def test_the_js_hooks_survive_the_reclothe(client):
    """Every id fundraiser.js reaches for must exist on a live authed page with picks."""
    campaign = _campaign()
    _series_with_edition('hooked')
    user = UserFactory()
    profile = ProfileFactory(user=user)
    Donation.objects.create(
        fundraiser=campaign, amount=10, provider='stripe',
        provider_transaction_id=f'tx-{next(_seq)}', status='completed',
        badge_picks_earned=1, badge_picks_used=0, user=user, profile=profile,
        completed_at=timezone.now(),
    )
    client.force_login(user)
    body = client.get(reverse('fundraiser', kwargs={'slug': campaign.slug})).content.decode()
    for hook in ('id="donation-form"', 'id="donation-amount"', 'id="min-amount-warning"',
                 'id="donate-btn"', 'id="donate-btn-text"', 'id="donation-anonymous"',
                 'id="donation-message"', 'id="hero-donate-cta"', 'id="open-badge-picker-btn"',
                 'id="badge-picker-modal"', 'id="badge-picker-grid"', 'id="badge-picker-search"',
                 'id="badge-claim-confirm"', 'id="claim-badge-name"', 'id="confirm-claim-btn"',
                 'id="cancel-claim-btn"', 'data-donation-ids='):
        assert hook in body, f'JS hook missing after the re-clothe: {hook}'


def test_the_donor_wall_wears_the_credits_vocabulary(client):
    campaign = _campaign()
    profile = ProfileFactory()
    Donation.objects.create(
        fundraiser=campaign, amount=25, provider='stripe',
        provider_transaction_id=f'tx-{next(_seq)}', status='completed',
        user=profile.user, profile=profile, completed_at=timezone.now(),
    )
    body = client.get(reverse('fundraiser', kwargs={'slug': campaign.slug})).content.decode()
    assert 'sup-prev--credit' in body and 'fnd-donor' in body
    assert 'border-4 border-warning' not in body, 'the heavy top-donor border should be a quiet accent now'


def test_the_admin_tabs_are_the_segmented_switcher(client):
    campaign = _campaign()
    staff = UserFactory(is_staff=True)
    client.force_login(staff)
    body = client.get(reverse('fundraiser_admin')).content.decode()
    assert 'pp-switch' in body
    assert 'tabs-boxed' not in body


def test_the_reclothe_actually_reaches_the_browser():
    """The audit's critical find: fundraiser.css was written to a high standard and never
    imported -- every source-reading check stayed green while the page shipped naked. This one
    reads the BUILT bundle."""
    import pathlib as _pathlib
    root = _pathlib.Path(__file__).resolve().parents[2]
    assert 'fundraiser.css' in (root / 'static' / 'css' / 'input.css').read_text(encoding='utf-8')
    built = (root / 'static' / 'css' / 'output.css').read_text(encoding='utf-8')
    for cls in ('.fnd-donors', '.fnd-cta', '.fnd-tile', '.fnd-horizon__fill--claimed'):
        assert cls in built, f'{cls} missing from the built bundle'


def test_the_modal_gate_matches_the_browse_all_doorway(client):
    """An authed visitor with no picks gets the 18-tile page and NO modal (nothing on the page
    can open it); the full list belongs to pick-holders only."""
    campaign = _campaign()
    for i in range(20):
        _series_with_edition(f'gated-{i:02d}')
    user = UserFactory()
    ProfileFactory(user=user)
    client.force_login(user)
    body = client.get(reverse('fundraiser', kwargs={'slug': campaign.slug})).content.decode()
    assert 'id="badge-picker-modal"' not in body
    assert body.count('badge-pick-option') == 18


def test_the_donor_wall_cards_actually_have_edges(client):
    """The credit base reads --sup-t with no fallback (IACVT: an unset var kills the whole
    border/background declaration). is-legacy is the documented neutral treatment."""
    campaign = _campaign()
    profile = ProfileFactory()
    Donation.objects.create(
        fundraiser=campaign, amount=25, provider='stripe',
        provider_transaction_id=f'tx-{next(_seq)}', status='completed',
        user=profile.user, profile=profile, completed_at=timezone.now(),
    )
    body = client.get(reverse('fundraiser', kwargs={'slug': campaign.slug})).content.decode()
    assert 'sup-prev--credit is-legacy fnd-donor' in body

