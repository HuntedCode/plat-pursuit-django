"""The Odds and ends review's fixes (docs/design/reviews/odds-and-ends.md, OE-01..OE-11)."""
import re
from datetime import timedelta
from io import BytesIO
from pathlib import Path

import pytest
from django.core.exceptions import PermissionDenied, SuspiciousOperation
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.template.loader import render_to_string
from django.test import Client
from django.urls import path, reverse
from django.utils import timezone
from django_ratelimit.exceptions import Ratelimited
from PIL import Image

from art_reveal.models import ArtRevealEvent, ArtRevealItem
from core import whats_new
from trophies.services import badge_thumbs
from tests.factories import BadgeSeriesFactory, ProfileFactory, UserFactory

pytestmark = pytest.mark.django_db

ROOT = Path(__file__).resolve().parents[2]


def _read(rel):
    return (ROOT / rel).read_text(encoding='utf-8')


# ── OE-01: every error has a page ────────────────────────────────────────────────────────────
# A urlconf that is the real site plus doors that raise, so the handlers under test are the ones
# plat_pursuit/urls.py declares (imported from it, not restated here).

def _raise(exc):
    def view(request):
        raise exc
    return view


def _boom(request):
    raise RuntimeError('boom')


from plat_pursuit import urls as site_urls  # noqa: E402

handler403 = site_urls.handler403
handler404 = site_urls.handler404
urlpatterns = [
    path('oe-test/denied/', _raise(PermissionDenied())),
    path('oe-test/limited/', _raise(Ratelimited())),
    path('oe-test/bad/', _raise(SuspiciousOperation())),
    path('oe-test/boom/', _boom),
    *site_urls.urlpatterns,
]


@pytest.fixture
def site_with_doors(settings):
    settings.ROOT_URLCONF = __name__
    return settings


def test_the_handlers_are_the_house_views():
    assert site_urls.handler403 == 'core.error_views.permission_denied'
    assert site_urls.handler404 == 'core.error_views.not_found'
    assert site_urls.handler429 == 'core.error_views.throttled'


def test_a_refused_request_gets_the_locked_trophy_page(client, site_with_doors):
    resp = client.get('/oe-test/denied/')

    assert resp.status_code == 403
    body = resp.content.decode()
    assert '403: Off Limits' in body and 'class="err-card"' in body
    assert 'id="navbar"' in body or '<nav' in body, 'a site error keeps the site around it'


FETCH = {'HTTP_SEC_FETCH_MODE': 'cors', 'HTTP_SEC_FETCH_DEST': 'empty'}
PAGE_LOAD = {'HTTP_SEC_FETCH_MODE': 'navigate', 'HTTP_SEC_FETCH_DEST': 'document'}


def test_a_tripped_rate_limit_is_answered_as_a_429_with_the_cooldown_page(client, site_with_doors):
    resp = client.get('/oe-test/limited/', **PAGE_LOAD)

    assert resp.status_code == 429
    assert '429: Too Many Pursuits' in resp.content.decode()


def test_a_refusal_stays_cheap_no_site_chrome_on_the_cooldown_page(client, site_with_doors):
    """A limiter sheds load: the cooldown page must not run base.html's context processors."""
    body = client.get('/oe-test/limited/').content.decode()

    assert 'err--standalone' in body and '<nav' not in body


def test_a_fetch_that_trips_a_limit_gets_json_it_can_read(client, site_with_doors):
    """The site's fetch helpers send no Accept header; the browser's Sec-Fetch headers are what tell."""
    resp = client.get('/oe-test/limited/', **FETCH)

    assert resp.status_code == 429
    assert resp.json() == {'error': 'Too many requests. Wait a minute and try again.'}


def test_an_explicit_json_accept_also_gets_json(client, site_with_doors):
    assert client.get('/oe-test/limited/', HTTP_ACCEPT='application/json').json()['error']


def test_an_image_load_is_not_mistaken_for_a_fetch(client, site_with_doors):
    resp = client.get('/oe-test/limited/', HTTP_SEC_FETCH_MODE='no-cors', HTTP_SEC_FETCH_DEST='image')

    assert resp['Content-Type'].startswith('text/html')


def test_a_refused_fetch_gets_json(client, site_with_doors):
    resp = client.get('/oe-test/denied/', **FETCH)

    assert resp.status_code == 403 and 'error' in resp.json()


def test_the_403_offers_a_visitor_sign_in_back_to_the_same_page(client, site_with_doors):
    body = client.get('/oe-test/denied/', **PAGE_LOAD).content.decode()

    assert f"{reverse('account_login')}?next=/oe-test/denied/" in body


def test_allauths_own_limits_reach_the_cooldown_page(rf):
    from allauth.core.ratelimit import respond_429
    request = rf.get('/accounts/login/')
    request.user = UserFactory()
    from django.contrib.sessions.backends.cache import SessionStore
    request.session = SessionStore()

    resp = respond_429(request)

    assert resp.status_code == 429
    assert '429: Too Many Pursuits' in resp.content.decode()


def test_an_expired_form_says_so_instead_of_djangos_csrf_page():
    resp = Client(enforce_csrf_checks=True).post(reverse('account_login'), {'login': 'x', 'password': 'y'})

    assert resp.status_code == 403
    body = resp.content.decode()
    assert 'This form expired' in body and 'CSRF verification failed' not in body


def test_an_expired_fetch_gets_json():
    resp = Client(enforce_csrf_checks=True).post(reverse('account_login'), {'login': 'x'}, **FETCH)

    assert resp.status_code == 403
    assert resp.json() == {'error': 'This form expired. Refresh the page and try again.'}


def test_a_server_error_gets_the_standalone_page(site_with_doors):
    resp = Client(raise_request_exception=False).get('/oe-test/boom/')

    assert resp.status_code == 500
    body = resp.content.decode()
    assert '500: Our Fault, Not Yours' in body
    assert 'err--standalone' in body and '<nav' not in body, 'a 500 must not lean on the site chrome'


def test_the_500_page_renders_with_no_context_at_all():
    """Django renders 500.html with an empty context and no request: it must stand alone."""
    html = render_to_string('500.html')

    assert 'Trophy Hunter' in html and 'err--standalone' in html


def test_a_bad_request_gets_the_standalone_page(client, site_with_doors):
    resp = client.get('/oe-test/bad/')

    assert resp.status_code == 400
    body = resp.content.decode()
    assert '400: Scrambled Request' in body and 'err--standalone' in body


def test_the_404_names_the_hunter_who_went_looking(client):
    profile = ProfileFactory(psn_username='losthunter', display_psn_username='LostHunter', user=UserFactory())
    client.force_login(profile.user)

    body = client.get('/no-such-page-oe/').content.decode()

    assert 'Sought by <b>LostHunter</b>' in body


def test_the_404_stays_a_404_for_visitors(client):
    resp = client.get('/no-such-page-oe/')

    assert resp.status_code == 404
    body = resp.content.decode()
    assert 'Sought by <b>Trophy Hunter</b>' in body
    assert '<nav' in body and 'err--standalone' not in body, 'a lost page keeps the site around it'


@pytest.mark.parametrize('rel', ['templates/404.html', 'templates/429.html', 'templates/403.html',
                                 'templates/403_csrf.html', 'templates/400.html', 'templates/500.html'])
def test_the_error_cards_use_the_component_not_inline_styles(rel):
    """OE-05: the 404 card was inline styles with fixed 140px columns that broke at 375."""
    src = _read(rel)
    assert "extends 'errors/base_error.html'" in src
    assert 'style="width' not in src and 'Earn Rate' not in src


# ── OE-02 / OE-03 / OE-07: About, Contact and the legal pages ───────────────────────────────

def _between(body, start, end):
    """The page between two markers: the footer and navbar link most of the same places."""
    return body[body.index(start):body.index(end)]


def test_about_names_only_live_features_and_links_each_one(client):
    body = client.get(reverse('about')).content.decode()
    features = _between(body, 'id="pg-what-h"', 'id="pg-who-h"')

    assert 'Roadmaps' not in body and 'Trophy Case' not in body
    for name in ('games_list', 'badges_list', 'jobs_browse', 'challenges', 'lists_browse',
                 'overall_badge_leaderboards', 'recap_index'):
        assert f'href="{reverse(name)}"' in features, name
    assert f'href="{reverse("support_hub")}"' in _between(body, 'id="pg-who-h"', 'Get in touch')


def test_about_offers_sign_up_to_a_visitor_only(client):
    head = _between(client.get(reverse('about')).content.decode(), 'class="pg__eyebrow"', 'id="pg-what-h"')
    assert f'href="{reverse("account_signup")}"' in head

    client.force_login(UserFactory())
    head = _between(client.get(reverse('about')).content.decode(), 'class="pg__eyebrow"', 'id="pg-what-h"')
    assert 'pg__actions' not in head and reverse('account_signup') not in head


def test_about_rows_stack_on_a_phone():
    """OE-02: the old grid stayed two across at 375 (491px page). One per row below md."""
    css = _read('static/css/components/pages.css')
    base_rule = re.search(r'^\.pg-rows \{([^}]*)\}', css, re.M).group(1)
    assert re.search(r'grid-template-columns:\s*minmax\(0, 1fr\);', base_rule)
    assert 'grid-cols-2' not in _read('templates/pages/about.html')


@pytest.mark.parametrize('name', ['privacy', 'terms'])
def test_the_legal_pages_read_in_the_house_long_form_style(client, name):
    assert 'pp-doc' in client.get(reverse(name)).content.decode()
    # The template, not the page: the footer links the Support roadmap, a different thing.
    src = _read(f'templates/pages/{name}.html')
    assert not re.search(r'class="[^"]*\bprose\b', src), 'prose does nothing here: no typography plugin'
    assert 'roadmap' not in src.lower(), 'roadmaps are not live (owner, 2026-10-27)'


def test_the_long_form_style_gives_headings_a_size():
    css = _read('static/css/components/pages.css')
    assert '.pp-doc h2 {' in css and '.pp-doc h3 {' in css


def test_contact_shows_the_address_as_wrapping_text_not_a_button(client):
    body = client.get(reverse('contact')).content.decode()

    assert '<span class="pg-row__value block">support@platpursuit.com</span>' in body
    assert 'overflow-wrap: anywhere' in _read('static/css/components/pages.css')


def test_the_discord_logo_has_one_source():
    path_start = 'M13.545 2.907'
    for rel in ('templates/partials/footer.html', 'templates/pages/contact.html',
                'templates/partials/_social_rows.html', 'templates/pages/about.html'):
        assert path_start not in _read(rel), rel
    assert path_start in _read('templates/partials/icons/discord.html')


# ── OE-04: the reveal page ───────────────────────────────────────────────────────────────────

@pytest.fixture
def media_root(settings, tmp_path):
    settings.MEDIA_ROOT = str(tmp_path)
    return tmp_path


def _png(colour=(200, 40, 40, 255), size=600, name='art.png'):
    buf = BytesIO()
    Image.new('RGBA', (size, size), colour).save(buf, format='PNG')
    return SimpleUploadedFile(name, buf.getvalue(), content_type='image/png')


def _event(n=3, per=5, count=0):
    event = ArtRevealEvent.objects.create(
        name='Summer Reveal', slug='summer', is_active=False, platinums_per_reveal=per,
        started_at=timezone.now() - timedelta(days=30), last_platinum_count=count,
    )
    for i in range(1, n + 1):
        ArtRevealItem.objects.create(event=event, series=BadgeSeriesFactory(series_slug=f'oe-{i}'), order=i,
                                     artwork=_png(name=f'{i}.png'), released=i * per <= count)
    return event


def test_with_no_event_there_is_no_page(client):
    assert client.get(reverse('art_reveal_event')).status_code == 404


def test_a_finished_event_is_a_gallery_not_a_meter(client, media_root):
    _event(n=3, per=5, count=15)

    body = client.get(reverse('art_reveal_event')).content.decode()

    assert 'Complete' in body and 'The collection' in body
    assert 'Unlock the next piece' not in body and 'reveal-carousel' not in body


def test_a_running_event_keeps_its_rules_and_meter(client, media_root):
    _event(n=3, per=5, count=7)

    body = client.get(reverse('art_reveal_event')).content.decode()

    assert 'Unlock the next piece' in body and 'reveal-carousel' in body and 'Next reveal' in body


def test_the_reveal_draws_each_items_own_small_copy_lazily(client, media_root):
    event = _event(n=2, per=5, count=10)
    item = event.items.get(order=1)

    assert item.artwork_thumb.name.startswith(badge_thumbs.thumb_prefix(item.artwork.name))
    body = client.get(reverse('art_reveal_event')).content.decode()
    assert f'src="{item.artwork_thumb.url}" alt="{item.series.name} artwork" loading="lazy"' in body
    assert item.artwork.url not in body


def test_an_item_whose_copy_is_stale_draws_its_upload(media_root):
    item = _event(n=1, per=5, count=5).items.get()
    ArtRevealItem.objects.filter(pk=item.pk).update(artwork='art_reveal/other.png')

    item = ArtRevealItem.objects.get(pk=item.pk)
    assert item.art_url == item.artwork.url


def test_a_reupload_remakes_the_items_copy(media_root):
    item = ArtRevealItem.objects.get(pk=_event(n=1).items.get().pk)
    before = item.artwork_thumb.name
    item.artwork = _png(colour=(10, 200, 90, 255), name='new.png')
    item.save()

    item.refresh_from_db()
    assert item.artwork_thumb.name != before
    assert badge_thumbs.thumb_is_current(item.artwork, item.artwork_thumb)


def test_a_save_that_leaves_the_artwork_out_never_copies_unsaved_art(media_root):
    """`update_fields` without `artwork` writes no artwork, so it must write no copy of one either: a copy of
    the in-memory art would sit beside the old art in the row."""
    item = ArtRevealItem.objects.get(pk=_event(n=1).items.get().pk)
    before = item.artwork_thumb.name
    item.artwork = _png(colour=(10, 200, 90, 255), name='unsaved.png')
    item.released = True
    item.save(update_fields=['released'])

    assert ArtRevealItem.objects.get(pk=item.pk).artwork_thumb.name == before


def test_saving_an_item_without_touching_its_art_writes_no_copy(media_root):
    item = _event(n=1).items.get()
    before = item.artwork_thumb.name
    item.released = True
    item.save(update_fields=['released'])

    assert ArtRevealItem.objects.get(pk=item.pk).artwork_thumb.name == before


def test_the_backfill_covers_reveal_items(media_root):
    item = _event(n=1).items.get()
    ArtRevealItem.objects.filter(pk=item.pk).update(artwork_thumb='')

    call_command('make_badge_thumbs')

    item.refresh_from_db()
    assert badge_thumbs.thumb_is_current(item.artwork, item.artwork_thumb)


# ── OE-06 / OE-08: what was removed or redirected ───────────────────────────────────────────

def test_the_arcade_page_is_gone(client):
    assert client.get('/arcade/stellar-circuit/').status_code == 404


@pytest.mark.parametrize('url', ['/community/reviews/', '/community/reviews/some-game/', '/reviews/',
                                 '/reviews/some-game/'])
def test_every_old_review_url_lands_on_rate_my_games(client, url):
    resp = client.get(url)

    assert resp.status_code == 301 and resp['Location'] == reverse('rate_my_games')


def test_old_review_links_keep_their_query_string(client):
    assert client.get('/community/reviews/?utm_source=x')['Location'] == reverse('rate_my_games') + '?utm_source=x'


def test_old_review_links_built_by_name_still_resolve():
    """review_service builds notification links with reverse('review_hub', slug)."""
    assert reverse('review_hub', kwargs={'slug': 'x'}) == '/community/reviews/x/'
    assert reverse('reviews_landing') == '/community/reviews/'


@pytest.mark.parametrize('name', ['call-of-duty', 'founders', 'goat-simulator', 'kodaka-uchicoshi-creator',
                                  'little-nightmares', 'saints-row', 'spongebob', 'spyro', 'star-wars'])
def test_the_unused_badge_pngs_are_gone(name):
    assert not (ROOT / 'static/images/badges' / f'{name}.png').exists()


# ── OE-09 / OE-10 / OE-11: copy ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize('rel', ['templates/users/goodbye.html', 'templates/pages/about.html',
                                 'templates/pages/contact.html', 'templates/pages/privacy.html',
                                 'templates/art_reveal/event.html', 'templates/errors/base_error.html'])
def test_the_brand_is_spelled_out(rel):
    assert 'PlatPursuit' not in _read(rel)


def test_the_goodbye_title_names_the_site_once(client):
    body = client.get(reverse('account_deleted')).content.decode()

    assert '<title>Goodbye - Platinum Pursuit</title>' in body


def test_the_newest_announcement_covers_the_badge_rewards():
    top = whats_new.ENTRIES[0]

    assert top.id == '2026-10-badge-rewards'
    assert top.safe_link_url == reverse('badge_how_it_works')


def test_a_card_download_that_trips_a_limit_says_wait_not_retry():
    """The share-card PNG doors are plain views, so a tripped limit is a 429 now (the DRF ones stay 403).
    CardDownload must read both as "too many", or it tells the hunter to retry the thing that caused it."""
    js = _read('static/js/utils.js')
    assert "err && (err.message === '403' || err.message === '429')" in js
