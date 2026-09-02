"""SEO Lane 3 (2026-08-23): performance foundation.

Strategy: docs/design/seo-strategy.md. Fonts self-hosted (cache partitioning ended the CDN's
cross-site benefit; two third-party origins left the critical path), CLS-risk images reserve
their aspect ratio, and the two LCP candidates carry fetchpriority.
"""
from pathlib import Path

import pytest
from django.conf import settings

from tests.factories import (
    CompanyFactory, ConceptCompanyFactory, ConceptFactory, GameFactory, ProfileFactory,
)

pytestmark = pytest.mark.django_db

CF = {'HTTP_CF_RAY': '8f0000000000abcd-LHR'}

FONTS_DIR = Path(settings.BASE_DIR) / 'static' / 'fonts'
OUTPUT_CSS = Path(settings.BASE_DIR) / 'static' / 'css' / 'output.css'


# --- self-hosted fonts ---

def test_no_page_reaches_for_a_font_cdn(client):
    """base.html served Google Fonts to every page; the fonts are self-hosted now and the
    preconnects went with the links."""
    body = client.get('/', **CF).content.decode()

    assert 'fonts.googleapis.com' not in body
    assert 'fonts.gstatic.com' not in body


def test_the_two_critical_fonts_are_preloaded(client):
    body = client.get('/', **CF).content.decode()

    for name in ('bricolage-grotesque-var-latin.woff2', 'inter-var-latin.woff2'):
        assert f'rel="preload" href="/static/fonts/{name}"' in body, f'{name} lost its preload'
    # Font preloads without crossorigin double-fetch; assert it survived.
    assert body.count('as="font" type="font/woff2" crossorigin') >= 2


def test_every_declared_font_file_exists():
    """The @font-face block in input.css names 10 woff2 files; a rename that misses the CSS
    would 404 silently (font-display: swap hides it behind the fallback stack)."""
    expected = {
        'bricolage-grotesque-var-latin.woff2', 'bricolage-grotesque-var-latin-ext.woff2',
        'inter-var-latin.woff2', 'inter-var-latin-ext.woff2',
        'poppins-400-latin.woff2', 'poppins-400-latin-ext.woff2',
        'poppins-600-latin.woff2', 'poppins-600-latin-ext.woff2',
        'poppins-700-latin.woff2', 'poppins-700-latin-ext.woff2',
    }
    on_disk = {p.name for p in FONTS_DIR.glob('*.woff2')}

    missing = expected - on_disk
    assert not missing, f'declared fonts missing from static/fonts/: {missing}'


def test_the_bundle_carries_the_faces():
    """Bundle guard (the collectstatic-invisibility class of bug): the faces must survive the
    Tailwind build into output.css, with urls relative to the css dir."""
    css = OUTPUT_CSS.read_text(encoding='utf-8')

    assert css.count('@font-face') == 10, 'the @font-face block did not survive the build'
    assert '../fonts/bricolage-grotesque-var-latin.woff2' in css
    assert '../fonts/inter-var-latin.woff2' in css
    assert '../fonts/poppins-400-latin.woff2' in css


def test_the_csp_no_longer_trusts_the_font_cdn():
    directives = settings.CONTENT_SECURITY_POLICY['DIRECTIVES']

    assert directives['font-src'] == ["'self'"]
    assert all('googleapis' not in src for src in directives['style-src'])


def test_no_template_still_links_the_font_cdn():
    """The two staff design labs had standalone heads with their own CDN links; the CSP would
    now block them. Zero templates may reference the Google Fonts origins."""
    offenders = []
    for path in (Path(settings.BASE_DIR) / 'templates').rglob('*.html'):
        text = path.read_text(encoding='utf-8', errors='ignore')
        if 'fonts.googleapis.com' in text or 'fonts.gstatic.com' in text:
            offenders.append(str(path))

    assert not offenders, f'templates still reaching for the font CDN: {offenders}'


# --- image CLS + LCP ---

def test_landing_showcase_cards_reserve_their_ratio(client):
    """The two 1200x630 share-card PNGs render width:100%/height:auto; without width/height
    attributes the section collapses then jumps when they arrive (real CLS on the one page
    whose first impression matters most)."""
    body = client.get('/', **CF).content.decode()

    assert body.count('width="1200" height="630"') >= 2, 'the showcase cards lost their reserved ratio'


def test_the_game_detail_hero_wears_fetchpriority(client):
    """The cover art is the LCP candidate on every game detail page."""
    game = GameFactory(defined_trophies={'bronze': 5})

    body = client.get(f'/games/{game.np_communication_id}/', **CF).content.decode()

    assert 'fetchpriority="high"' in body


def test_the_profile_header_avatar_wears_fetchpriority(client):
    profile = ProfileFactory(is_linked=True, psn_history_public=True, total_trophies=10)

    body = client.get(f'/hunters/{profile.psn_username}/', **CF).content.decode()

    assert 'fetchpriority="high"' in body


def test_landing_serves_the_showcase_cards_as_webp(client):
    """The two card PNGs stay for og:image scrapers; the on-page <img> gets a webp source
    (~90% lighter). Both variants must exist or the <picture> silently falls back."""
    body = client.get('/', **CF).content.decode()

    assert body.count('type="image/webp"') >= 2
    for name in ('plat_card_example', 'recap_card_example'):
        assert (Path(settings.BASE_DIR) / 'static' / 'images' / 'showcase' / f'{name}.webp').exists()
        assert f'{name}.png' in body, 'the png fallback (and og target) left the page'


# --- the pre-paint drawer collapse (the 0.465 CLS on /games/) ---

SNIPPET_MARK = "setAttribute('hidden', '')"


def test_the_games_hub_collapses_its_drawer_before_paint(client):
    """The filter drawer renders open for no-JS, and filterPanel used to collapse it AFTER
    parse: the whole grid painted low and jumped up (CLS 0.465 in the Lighthouse baseline).
    The inline snippet inside the panel hides it during parse instead."""
    body = client.get('/games/', **CF).content.decode()

    panel_pos = body.index('id="gbrowse-advanced"')
    snippet_pos = body.index(SNIPPET_MARK)
    results_pos = body.index('id="browse-results"')

    assert panel_pos < snippet_pos < results_pos, (
        'the pre-collapse snippet must sit between the drawer and the grid to run before the grid paints'
    )


def test_parked_range_params_still_collapse_the_drawer(client):
    """The audit's HIGH: filter URLs carry range sliders parked at their own bounds
    (rating_min=0 is a non-empty value but not a filter -- filterPanel excludes it, and the
    first snippet cut did not, so every touched-a-filter URL kept the after-paint jump). The
    snippet reads SERVER truth (filter_chips) now, which parks the same ranges the JS does."""
    body = client.get('/games/?rating_min=0&rating_max=5', **CF).content.decode()

    assert SNIPPET_MARK in body, 'a parked range read as an active filter again'


def test_a_real_filter_leaves_the_drawer_open(client):
    """With a genuine content filter applied the drawer opens on load; pre-hiding it would
    flash it shut then open."""
    body = client.get('/games/?letter=A', **CF).content.decode()

    assert SNIPPET_MARK not in body


def test_the_company_hub_collapses_its_drawer_too(client):
    body = client.get('/companies/', **CF).content.decode()

    assert SNIPPET_MARK in body


# --- thin-page rules beyond the Lane 0 floors ---

def test_day_pages_are_addressable_but_not_indexable(client):
    """Profiles x dates is an unbounded space of thin slices; the profile is the search
    surface. follow keeps the day's links crawlable-through."""
    from datetime import datetime, timezone as tz

    from tests.factories import EarnedTrophyFactory

    profile = ProfileFactory(is_linked=True, psn_history_public=True, total_trophies=10)
    EarnedTrophyFactory(profile=profile,
                        earned_date_time=datetime(2026, 8, 1, 15, 0, tzinfo=tz.utc))

    body = client.get(f'/hunters/{profile.psn_username}/day/2026-08-01/', **CF).content.decode()

    assert 'content="noindex, follow"' in body


def test_a_one_game_company_page_is_noindexed(client):
    """A company page with a single game answers nothing its game page doesn't; thousands of
    one-game shovelware publishers would dilute the crawl."""
    company = CompanyFactory()
    concept = ConceptFactory()
    GameFactory(concept=concept, defined_trophies={'bronze': 5})
    ConceptCompanyFactory(company=company, concept=concept)

    body = client.get(f'/companies/{company.slug}/', **CF).content.decode()

    assert 'content="noindex, follow"' in body


def test_a_multi_game_company_page_stays_indexed(client):
    company = CompanyFactory()
    for _ in range(2):
        concept = ConceptFactory()
        GameFactory(concept=concept, defined_trophies={'bronze': 5})
        ConceptCompanyFactory(company=company, concept=concept)

    body = client.get(f'/companies/{company.slug}/', **CF).content.decode()

    assert 'content="index, follow"' in body
