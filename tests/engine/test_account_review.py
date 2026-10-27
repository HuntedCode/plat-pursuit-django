"""The Account review's fixes (docs/design/reviews/account.md, AC-01..AC-12; SW-06)."""
import re
from datetime import timedelta
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

import pytest
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.urls import reverse
from django.utils import timezone
from PIL import Image

from fundraiser.models import Donation, DonationBadgeClaim, Fundraiser
from trophies.models import BadgeSeries, GroupBadge
from trophies.services import badge_thumbs
from tests.factories import (
    BadgeSeriesFactory, GroupBadgeFactory, PlatformGroupFactory, ProfileFactory, UserFactory,
)

pytestmark = pytest.mark.django_db

ROOT = Path(__file__).resolve().parents[2]


def _read(rel):
    return (ROOT / rel).read_text(encoding='utf-8')


def _png(colour=(200, 40, 40, 255), size=1000, name='art.png'):
    buf = BytesIO()
    Image.new('RGBA', (size, size), colour).save(buf, format='PNG')
    return SimpleUploadedFile(name, buf.getvalue(), content_type='image/png')


@pytest.fixture
def media_root(settings, tmp_path):
    settings.MEDIA_ROOT = str(tmp_path)
    return tmp_path


def _edition(series):
    return GroupBadgeFactory(series=series, platform_group=PlatformGroupFactory(key=f'{series.series_slug}-g'),
                             is_live=True)


def _thumb_pixel(fieldfile):
    with fieldfile.open('rb') as f:
        img = Image.open(f)
        img.load()
    return img.size, img.format, img.convert('RGBA').getpixel((10, 10))


# ── AC-01 / SW-06: badge art has a display-size copy ─────────────────────────────────────────

def test_an_uploaded_series_image_gets_a_512px_webp_copy(media_root):
    series = BadgeSeriesFactory(series_slug='souls', badge_image=_png())
    series.refresh_from_db()

    assert series.badge_thumb.name == badge_thumbs.thumb_name(series.badge_image.name)
    size, fmt, _ = _thumb_pixel(series.badge_thumb)
    assert size == (512, 512) and fmt == 'WEBP'


def test_medallions_draw_the_copy_and_link_previews_keep_the_upload(media_root):
    series = BadgeSeriesFactory(series_slug='souls', badge_image=_png())
    art = _edition(series).art_layers()

    assert art['main'].endswith('.webp') and 'badges/thumbs/' in art['main']
    assert art['main_full'] == series.badge_image.url


def test_a_source_changed_without_a_save_falls_back_to_the_upload(media_root):
    """The staleness check is the thumbnail's NAME: a source swapped by a queryset update must never be
    drawn from the old picture's copy."""
    series = BadgeSeriesFactory(series_slug='souls', badge_image=_png())
    BadgeSeries.objects.filter(pk=series.pk).update(badge_image='badges/series/other.png')
    gb = _edition(BadgeSeries.objects.get(pk=series.pk))

    art = gb.art_layers()
    assert art['main'] == art['main_full'] and art['main'].endswith('other.png')


def test_a_reupload_under_the_same_name_remakes_the_copy(media_root):
    """S3 overwrites a same-named upload, so the name alone cannot tell; the fresh-upload flag does."""
    series = BadgeSeriesFactory(series_slug='souls', badge_image=_png((200, 40, 40, 255)))
    series.refresh_from_db()
    with patch.object(series.badge_image.storage, 'get_available_name', side_effect=lambda name, **kw: name):
        series.badge_image.storage.delete(series.badge_image.name)
        series.badge_image = _png((20, 200, 40, 255), name=Path(series.badge_image.name).name)
        series.save()
    series.refresh_from_db()

    assert _thumb_pixel(series.badge_thumb)[2][1] > 150, 'the copy still shows the old picture'


def test_clearing_the_image_clears_the_copy(media_root):
    series = BadgeSeriesFactory(series_slug='souls', badge_image=_png())
    series.badge_image = None
    series.save()
    series.refresh_from_db()

    assert not series.badge_thumb


def test_art_released_under_an_old_name_is_redrawn(media_root):
    """The audit's case: art cleared, then a reveal writes new art under the SAME name through `FieldFile.save`
    (committed before the model's save runs, so not a "fresh upload"). On an overwriting storage the old copy's
    file is still there under the name the new art maps to; it must be re-rendered, not reused."""
    series = BadgeSeriesFactory(series_slug='souls', badge_image=_png((200, 40, 40, 255), name='reveal.png'))
    series.refresh_from_db()
    name = Path(series.badge_image.name).name
    series.badge_image.storage.delete(series.badge_image.name)
    series.badge_image = None
    series.save()
    series = BadgeSeries.objects.get(pk=series.pk)
    with patch.object(series._meta.get_field('badge_image').storage, 'get_available_name',
                      side_effect=lambda n, **kw: n):
        series.badge_image.save(name, _png((20, 200, 40, 255), name=name), save=True)
    series.refresh_from_db()

    assert badge_thumbs.thumb_is_current(series.badge_image, series.badge_thumb)
    assert _thumb_pixel(series.badge_thumb)[2][1] > 150, 'the copy still shows the old picture'


def test_a_failed_render_is_not_retried_on_every_save(media_root):
    series = BadgeSeriesFactory(series_slug='souls',
                                badge_image=SimpleUploadedFile('bad.png', b'not a png', content_type='image/png'))
    series = BadgeSeries.objects.get(pk=series.pk)
    with patch('trophies.services.badge_thumbs.render_thumb', side_effect=AssertionError('retried')):
        series.name = 'Renamed'
        series.save()


def test_clearing_an_override_clears_its_copy(media_root):
    gb = _edition(BadgeSeriesFactory(series_slug='souls'))
    gb.badge_image_override = _png(name='override.png')
    gb.save()
    gb = GroupBadge.objects.get(pk=gb.pk)
    gb.badge_image_override = None
    gb.save()
    gb.refresh_from_db()

    assert not gb.badge_override_thumb


def test_an_unreadable_image_saves_and_serves_the_upload(media_root):
    series = BadgeSeriesFactory(series_slug='souls',
                                badge_image=SimpleUploadedFile('bad.png', b'not a png', content_type='image/png'))
    series.refresh_from_db()

    assert not series.badge_thumb
    art = _edition(series).art_layers()
    assert art['main'] == art['main_full'] == series.badge_image.url


def test_a_steady_state_save_touches_no_storage(media_root):
    """A save that changes no art (an admin edit of the name, a data fix) must cost no storage I/O."""
    series = BadgeSeriesFactory(series_slug='souls', badge_image=_png())
    series.refresh_from_db()
    storage = series.badge_image.storage
    with patch.object(storage, 'exists', side_effect=AssertionError('storage hit')), \
         patch.object(storage, 'save', side_effect=AssertionError('storage hit')):
        series.name = 'Renamed'
        series.save()


def test_an_edition_override_gets_its_own_copy(media_root):
    series = BadgeSeriesFactory(series_slug='souls')
    gb = _edition(series)
    gb.badge_image_override = _png((10, 10, 220, 255), name='override.png')
    gb.save()
    gb = GroupBadge.objects.get(pk=gb.pk)

    assert gb.badge_override_thumb.name == badge_thumbs.thumb_name(gb.badge_image_override.name)
    assert gb.art_layers()['main'] == gb.badge_override_thumb.url


def test_the_backfill_makes_missing_copies_and_reruns_as_a_no_op(media_root, capsys):
    series = BadgeSeriesFactory(series_slug='souls', badge_image=_png())
    BadgeSeries.objects.filter(pk=series.pk).update(badge_thumb='')

    call_command('make_badge_thumbs')
    series.refresh_from_db()
    assert badge_thumbs.thumb_is_current(series.badge_image, series.badge_thumb)

    with patch('trophies.services.badge_thumbs.render_thumb', side_effect=AssertionError('re-rendered')):
        call_command('make_badge_thumbs')
    assert 'BadgeSeries: 0 made' in capsys.readouterr().out


def test_the_badge_page_previews_with_the_full_upload(media_root, client):
    series = BadgeSeriesFactory(series_slug='souls', badge_image=_png())
    _edition(series)
    body = client.get(reverse('badge_detail', args=[series.series_slug])).content.decode()

    og = re.search(r'<meta property="og:image" content="([^"]+)"', body)
    assert og and og.group(1).endswith(series.badge_image.url), 'the link preview took the 512px copy'


def test_the_discord_embed_uses_the_full_upload(media_root, settings):
    from trophies.discord_utils import discord_notifications as dn
    settings.DEBUG = False
    series = BadgeSeriesFactory(series_slug='souls', badge_image=_png())
    gb = _edition(series)
    with patch.object(GroupBadge, 'art_layers', return_value={
            'main': 'https://cdn.test/thumb.webp', 'main_full': 'https://cdn.test/full.png',
            'has_custom_image': True}):
        assert dn._badge_thumbnail(gb) == 'https://cdn.test/full.png'


# ── AC-02 / AC-03: the fundraiser ────────────────────────────────────────────────────────────

_seq = iter(range(10_000))


def _campaign():
    return Fundraiser.objects.create(
        name='Badge Artwork Fund', slug=f'fund-{next(_seq)}', description='',
        campaign_type='badge_artwork', start_date=timezone.now() - timedelta(days=1),
    )


def _claim(campaign, profile, status='claimed', slug=None):
    slug = slug or f's-{next(_seq)}'
    series = BadgeSeriesFactory(series_slug=slug, name=slug)
    _edition(series)
    donation = Donation.objects.create(
        fundraiser=campaign, user=profile.user, profile=profile, amount=10, provider='stripe',
        provider_transaction_id=f'tx-{next(_seq)}', status='completed', badge_picks_earned=1,
        badge_picks_used=1, completed_at=timezone.now(),
    )
    return DonationBadgeClaim.objects.create(donation=donation, profile=profile, series=series,
                                             series_slug=slug, series_name=slug, status=status)


@pytest.fixture(autouse=True)
def _clear_caches():
    cache.clear()
    yield
    cache.clear()


def _fundraiser_page(client, campaign):
    return client.get(reverse('fundraiser', kwargs={'slug': campaign.slug}))


def test_the_tracker_counts_claimed_of_claimable(client):
    """AC-02: "N of M claimed" is this campaign's claims plus what can still be claimed. A series lacking
    art that the campaign cannot offer (claimed under another campaign) used to sit in the total: "136 of
    137 claimed" with nothing left to claim."""
    campaign, other = _campaign(), _campaign()
    donor = ProfileFactory()
    _claim(campaign, donor)
    _claim(other, donor)                          # lacks art, claimed elsewhere: not this campaign's
    _edition(BadgeSeriesFactory(series_slug='free-one'))   # claimable

    tracker = _fundraiser_page(client, campaign).context['badge_tracker']
    assert (tracker['claimed'], tracker['total']) == (1, 2)


def test_every_series_patroned_leads_with_the_news_and_drops_the_empty_box(client):
    campaign = _campaign()
    _claim(campaign, ProfileFactory())
    body = _fundraiser_page(client, campaign).content.decode()

    assert 'Every badge series has a patron. Donations now fund the next wave.' in body
    assert 'Artworks awaiting a patron' not in body, 'rule 3: an empty section'
    assert '>Available<' not in ' '.join(body.split()).replace('> ', '>').replace(' <', '<')


def test_a_donor_with_picks_is_not_sent_to_an_empty_picker(client):
    campaign = _campaign()
    donor = ProfileFactory()
    _claim(campaign, donor)
    Donation.objects.filter(profile=donor).update(badge_picks_earned=3, badge_picks_used=1)
    client.force_login(donor.user)
    body = ' '.join(_fundraiser_page(client, campaign).content.decode().split())

    assert 'open-badge-picker-btn' not in body
    assert 'saved for the next wave' in body


def test_an_ended_campaign_freezes_its_tracker_and_drops_the_news(client):
    campaign = _campaign()
    _claim(campaign, ProfileFactory(), status='completed')
    _edition(BadgeSeriesFactory(series_slug='later-one'))     # appeared after the campaign: not its total
    Fundraiser.objects.filter(pk=campaign.pk).update(end_date=timezone.now() - timedelta(hours=1))
    resp = _fundraiser_page(client, campaign)
    body = resp.content.decode()

    assert (resp.context['badge_tracker']['claimed'], resp.context['badge_tracker']['total']) == (1, 1)
    assert not resp.context['all_patroned']
    assert 'pp-factline fnd__facts' not in body, 'the totals repeat the results card'


def test_a_claimable_series_keeps_the_awaiting_grid_and_no_news(client):
    campaign = _campaign()
    _claim(campaign, ProfileFactory())
    _edition(BadgeSeriesFactory(series_slug='free-one'))
    body = _fundraiser_page(client, campaign).content.decode()

    assert 'Artworks awaiting a patron' in body
    assert 'Every badge series has a patron' not in body


def test_a_signed_in_donor_reads_that_a_pick_waits_for_the_next_wave(client):
    campaign = _campaign()
    _claim(campaign, ProfileFactory())
    viewer = ProfileFactory()
    client.force_login(viewer.user)
    body = ' '.join(_fundraiser_page(client, campaign).content.decode().split())

    assert 'you earn a badge pick, saved for the next wave' in body
    assert 'to claim a badge series for custom artwork' not in body


def test_the_campaign_totals_are_one_line_with_no_zeros(client):
    empty = _campaign()
    assert 'pp-factline fnd__facts' not in _fundraiser_page(client, empty).content.decode(), 'an empty line rendered'

    campaign = _campaign()
    _claim(campaign, ProfileFactory())                     # $10 raised, one donor, no artwork live yet
    body = _fundraiser_page(client, campaign).content.decode()
    facts = body.split('pp-factline fnd__facts', 1)[1].split('</p>', 1)[0]

    assert 'raised' in facts and 'donor' in facts
    assert 'artwork' not in facts, 'a zero total rendered'
    assert 'class="scard' not in body.split('Badge artwork progress')[0], 'the stat tiles came back'


def test_the_finished_gallery_shows_twelve_then_the_rest_on_demand(client):
    campaign = _campaign()
    donor = ProfileFactory()
    for _ in range(15):
        _claim(campaign, donor, status='completed')
    body = _fundraiser_page(client, campaign).content.decode()

    first, more = body.split('<details class="fnd-more">', 1)
    assert first.split('<h3 class="fnd__h3 text-success">Artwork complete</h3>', 1)[1].count('fnd-claim--done') == 12
    assert 'Show all 15' in more
    assert more.split('</details>', 1)[0].count('fnd-claim--done') == 3


def test_the_queue_is_a_closed_list_of_names_not_placeholder_medallions(client):
    campaign = _campaign()
    donor = ProfileFactory()
    for _ in range(5):
        _claim(campaign, donor)
    body = _fundraiser_page(client, campaign).content.decode()
    queue = body.split('<details class="fnd-queue">', 1)[1].split('</details>', 1)[0]

    assert queue.count('fnd-queue__row') == 5
    assert 'images/badges/default.png' not in queue
    assert '5 claimed, art on the way' in queue


def test_a_donor_card_shows_six_claims_then_a_count(client):
    campaign = _campaign()
    donor = ProfileFactory(display_psn_username='BigDonor')
    for _ in range(9):
        _claim(campaign, donor)
    body = _fundraiser_page(client, campaign).content.decode()
    card = body.split('fnd-donors', 1)[1]

    assert card.count('fnd-donor__claim"') == 6
    assert '+3 more' in card


# ── AC-04 / AC-06: Support ───────────────────────────────────────────────────────────────────

def test_support_puts_the_ask_after_the_lede_on_one_column():
    """AC-04: on a phone the box follows the lede and the proof follows the box; a wide screen keeps the
    box beside both."""
    css = _read('static/css/components/support.css')
    base = re.search(r'\.sup-head \{([^}]*)\}', css).group(1)
    assert 'grid-template-areas: "say" "box" "proof"' in base
    wide = re.search(r'@media \(min-width: 900px\) \{\s*\.sup-head \{([^}]*)\}', css).group(1)
    assert 'grid-template-areas: "say box" "proof box"' in wide
    page = _read('templates/support/support_hub.html')
    assert page.index('class="sup-head__say') < page.index('class="sup-head__proof"')


# ── AC-05: Email addresses ───────────────────────────────────────────────────────────────────

def _email_page(client, user):
    client.force_login(user)
    return client.get(reverse('account_email')).content.decode()


def test_a_lone_verified_primary_address_offers_no_actions(client):
    from allauth.account.models import EmailAddress
    user = UserFactory()
    EmailAddress.objects.update_or_create(user=user, email=user.email, defaults={'verified': True, 'primary': True})
    body = _email_page(client, user)

    for action in ('action_primary', 'action_send', 'action_remove'):
        assert f'name="{action}"' not in body, f'{action} offered on an address it cannot apply to'
    assert 'auth-card' in body, 'not on the auth shell'


def test_a_secondary_address_offers_only_what_applies(client):
    from allauth.account.models import EmailAddress
    user = UserFactory()
    EmailAddress.objects.update_or_create(user=user, email=user.email, defaults={'verified': True, 'primary': True})
    EmailAddress.objects.create(user=user, email='second@example.test', verified=False, primary=False)
    EmailAddress.objects.create(user=user, email='third@example.test', verified=True, primary=False)
    body = _email_page(client, user)
    rows = {m.group(1): m.group(0) for m in re.finditer(
        r'<li class="auth-addr">.*?auth-addr__email">([^<]+)<.*?</li>', body, re.S)}

    assert 'action_send' in rows['second@example.test'] and 'action_primary' not in rows['second@example.test']
    assert 'action_primary' in rows['third@example.test'] and 'action_send' not in rows['third@example.test']
    assert 'action_remove' in rows['second@example.test'] and 'action_remove' in rows['third@example.test']
    assert 'name="action_' not in rows[user.email]


def test_removing_a_secondary_address_still_works(client):
    from allauth.account.models import EmailAddress
    user = UserFactory()
    EmailAddress.objects.update_or_create(user=user, email=user.email, defaults={'verified': True, 'primary': True})
    EmailAddress.objects.create(user=user, email='second@example.test', verified=False, primary=False)
    client.force_login(user)
    client.post(reverse('account_email'), {'email': 'second@example.test', 'action_remove': ''})

    assert not EmailAddress.objects.filter(email='second@example.test').exists()


# ── AC-07 and the brand: tier titles and page titles ─────────────────────────────────────────

def test_a_tier_title_is_the_level_name_alone():
    from users.services.marks import mark_style
    assert mark_style('patron')['label'] == 'Patron'


@pytest.mark.parametrize('rel', [
    'templates/users/settings.html', 'templates/support/membership.html', 'templates/support/welcome.html',
    'templates/account/email.html',
])
def test_no_account_page_doubles_the_site_name_in_its_title(rel):
    """base.html appends " - Platinum Pursuit"; these pages also appended "- PlatPursuit"."""
    title = re.search(r'{% block title %}(.*?){% endblock %}', _read(rel)).group(1)
    assert 'PlatPursuit' not in title and 'Platinum Pursuit' not in title


def test_the_level_lines_wrap_rather_than_truncate():
    for rel, sel in (('static/css/components/support-welcome.css', '.supw-id__sub {'),
                     ('static/css/components/support-manage.css', '.supm-who__sub {')):
        css = _read(rel)
        rule = css[css.index(sel):css.index('}', css.index(sel))]
        assert 'ellipsis' not in rule and 'nowrap' not in rule, f'{sel} truncates again'


# ── AC-08: the roadmap ───────────────────────────────────────────────────────────────────────

def test_roadmap_tiers_fill_their_rows():
    css = _read('static/css/components/support-roadmap.css')
    rule = css[css.index('.rm-feats {'):css.index('}', css.index('.rm-feats {'))]
    assert 'auto-fit' in rule
    assert 'repeat(4, 1fr)' not in css


def test_roadmap_copy_has_no_double_hyphen():
    from users.constants import ROADMAP_FEATURES
    for feat in ROADMAP_FEATURES:
        assert ' -- ' not in feat['name'] + feat['blurb'], feat['key']


# ── AC-10: My Membership for somebody who never joined ───────────────────────────────────────

def test_the_wall_switch_is_not_offered_to_somebody_who_never_joined(client):
    profile = ProfileFactory()
    client.force_login(profile.user)
    body = client.get(reverse('subscription_management')).content.decode()

    assert 'List me on the supporter wall' not in body
    assert 'the rebuild moving' not in body


def test_a_comped_level_keeps_the_wall_switch(client):
    """A level with no processor subscription is still listed on the wall (it lists by `premium_tier`)."""
    profile = ProfileFactory(user__premium_tier='patron')
    client.force_login(profile.user)
    body = client.get(reverse('subscription_management')).content.decode()

    assert 'List me on the supporter wall' in body


def test_a_lapsed_member_keeps_the_wall_switch(client):
    from users.models import SubscriptionPeriod
    profile = ProfileFactory()
    SubscriptionPeriod.objects.create(user=profile.user, started_at=timezone.now() - timedelta(days=60),
                                      ended_at=timezone.now() - timedelta(days=30), provider='stripe')
    client.force_login(profile.user)
    body = client.get(reverse('subscription_management')).content.decode()

    assert 'List me on the supporter wall' in body


# ── AC-11 / AC-12 ────────────────────────────────────────────────────────────────────────────

def test_the_beta_gate_wears_the_account_card_and_keeps_the_chrome(client):
    body = client.get(reverse('beta_access_required')).content.decode()
    assert 'auth-card' in body and 'card-body' not in body
    assert 'mobile-tabbar' in body, 'a visitor bounced here lost the phone tab bar'


def test_the_email_page_keeps_the_chrome_and_sign_in_stays_focused(client):
    user = UserFactory()
    assert 'mobile-tabbar' in _email_page(client, user)
    client.logout()
    assert 'mobile-tabbar' not in client.get(reverse('account_reset_password')).content.decode()


def test_email_row_actions_are_a_tappable_size():
    page = _read('templates/account/email.html')
    assert 'pp-cta--xs' not in page, 'row actions at 22px, under the 24px minimum'


def test_the_celebrations_hint_says_what_the_switch_does():
    page = _read('templates/users/settings.html')
    assert 'its celebration plays by itself the next time you open My Pursuit' in page
    assert 'waits behind a button with your new platinums' not in page
