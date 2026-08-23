"""The Profile Card (2026-08): the share-card family's whole-career sibling.

One template serves the profile page's Card tab (inline preview) and the PNG endpoint (the
download), so what needs pinning is the family contract -- the shared identity literals, the
two embedded faces, no custom properties -- plus the ownership rules: the card is only ever
YOURS, the tab is only offered to the owner, and a visitor asking for the slug by hand gets
the default tab rather than someone else's card chrome.
"""
from pathlib import Path

import pytest
from django.template.loader import render_to_string

from tests.factories import EarnedTrophyFactory, ProfileFactory

pytestmark = pytest.mark.django_db

ROOT = Path(__file__).resolve().parents[2]

# Profile pages sit behind the Cloudflare origin guard.
CF = {'HTTP_CF_RAY': '8f0000000000abcd-LHR'}


def _card_html(profile):
    """The card rendered exactly as the tab renders it: real data, uncached image URLs."""
    from core.services import profile_card_service

    data = profile_card_service.get_card_data(profile)
    data['avatar_image'] = data['user_avatar_url']
    for m in data['badges']['medallions']:
        m['layers_cached'] = m['layers']
    for key in ('rarest_plat', 'latest_plat'):
        if data.get(key):
            data[key]['cover_cached'] = data[key]['cover_url']
    return render_to_string('shareables/profile_card.html', data)


def _linked_profile(**over):
    over.setdefault('is_linked', True)
    over.setdefault('sync_status', 'synced')
    return ProfileFactory(**over)


# --- The family contract ---

def test_the_card_shares_the_plat_cards_identity():
    """Same rule (and same literals) as the recap card's pin: the identity pieces are shared
    VERBATIM, compared against the plat card file itself so a drift in either card fails here."""
    plat = (ROOT / 'templates' / 'shareables' / 'plat_card.html').read_text(encoding='utf-8')
    card = _card_html(_linked_profile())

    shared = [
        # The ground.
        'radial-gradient(120% 90% at 12% 0%, #232a31 0%, #181d23 45%, #05080c 100%)',
        # Both scrim layers.
        'linear-gradient(100deg, rgba(5, 8, 12, 0.74) 0%, rgba(5, 8, 12, 0.52) 52%, rgba(5, 8, 12, 0.20) 100%)',
        'linear-gradient(to top, rgba(5, 8, 12, 0.66) 0%, rgba(5, 8, 12, 0) 44%)',
        # The Frame, and the hairline that separates every zone.
        'inset: 16px;',
        'rgba(64, 72, 83, 0.55)',
        # The page rhythm.
        'padding: 40px 46px;',
        # The brand block.
        'Platinum Pursuit',
        'platpursuit.com',
    ]
    for token in shared:
        assert token in plat, f'{token!r} is not in the plat card -- this test is comparing to nothing'
        assert token in card, f'the profile card no longer shares {token!r} with the plat card'


def test_the_card_uses_only_the_two_embedded_faces():
    """The renderer embeds Bricolage Grotesque and Inter from static/fonts/. A third family here
    silently renders as the fallback."""
    import re

    families = set()
    for decl in re.findall(r"font-family:\s*([^;\"]+)", _card_html(_linked_profile())):
        families.update(f.strip().strip("'\"") for f in decl.split(','))

    assert families <= {'Bricolage Grotesque', 'Inter', 'sans-serif'}, (
        f'unembeddable font families on the card: {families - {"Bricolage Grotesque", "Inter", "sans-serif"}}'
    )


def test_no_css_custom_properties_reach_the_card():
    assert 'var(--' not in _card_html(_linked_profile()), (
        'a custom property will not resolve in the renderer'
    )


def test_the_card_wears_the_mark():
    """The identity strip goes through the same mark system as every other surface: a supporter's
    name takes the mark colour and the star run."""
    profile = _linked_profile()
    profile.display_mark = 'backer'
    profile.save(update_fields=['display_mark'])

    html = _card_html(profile)

    # One star SVG per _mark_glyphs_inline star; the marked name is coloured, not #f0f6fd.
    assert 'M12 2.6l2.6 5.9' in html, 'the supporter star run is missing'
    # And the tier's full name in words, on the identity strip (his call: stars need their words).
    assert 'PlatPursuit Backer' in html, 'the mark label is missing from the identity strip'


def test_a_fresh_profile_still_composes_a_complete_card():
    """Zeros everywhere must render a card, not a crash or a gap-toothed layout: every zone keeps
    its anchor figure (0 platinums, 0 of N badges) rather than vanishing."""
    html = _card_html(_linked_profile())

    assert 'share-image-content' in html
    assert 'Platinum' in html          # the big-statement label survives a zero
    assert 'of' in html and 'Badges' in html
    # The career row is a FIXED shape -- zeros render, cells never vanish (the density call:
    # a fresh Pursuer's card shows the shape of what's ahead, not a gap-toothed row).
    for label in ('Jobs played', 'Tiers earned', 'Career XP', 'Collection'):
        assert label in html, f'the Career/Collection sections lost their {label} line'
    # The three section eyebrows, in the site's own nouns -- the sectioning IS the design
    # decision (his note: the first cut mixed the systems' stats illegibly). Matched as the
    # eyebrow element's own text (">Career<"), because bare "Career" is trivially satisfied by
    # the ledger's "Career XP" line and would pin nothing.
    for eyebrow in ('Trophy Record', 'Career', 'Collection'):
        assert f'>{eyebrow}</span>' in html, f'the {eyebrow} section lost its eyebrow'


def test_the_platinum_minis_wear_their_art():
    """The rarest platinum renders as a mini card: eyebrow, name, rate, and the game's REAL cover
    through the cover_cached hop -- the branch every earlier test missed by rendering only the
    no-platinums placeholder path. Concept deliberately absent so the fallback chain lands on
    title_icon_url, the URL asserted below."""
    profile = _linked_profile()
    et = EarnedTrophyFactory(
        profile=profile,
        trophy__trophy_type='platinum',
        trophy__trophy_earn_rate=1.4,
        trophy__game__concept=None,
        trophy__game__title_icon_url='https://cdn.example/rarest-cover.png',
    )
    profile.rarest_plat = et
    profile.save(update_fields=['rarest_plat'])

    html = _card_html(profile)

    assert '>Rarest Platinum</div>' in html, 'the rarest mini card is missing its eyebrow'
    assert 'https://cdn.example/rarest-cover.png' in html, 'the cover never reached the card'
    assert '1.4% of players' in html, 'the earn rate left the mini card'


# --- The PNG endpoint ---

def test_the_download_requires_a_session(client):
    assert client.get('/api/v1/shareables/profile/png/').status_code in (401, 403)


def test_the_download_requires_a_linked_profile(client, django_user_model):
    user = django_user_model.objects.create_user(username='cardless', email='cardless@example.com', password='x')
    client.force_login(user)

    resp = client.get('/api/v1/shareables/profile/png/')

    assert resp.status_code == 400


def test_the_download_is_always_your_own_card(client, monkeypatch):
    """No key in the URL: the card is built from request.user.profile, so the endpoint cannot be
    asked for anyone else's. The filename carries the OWNER's name -- pinned with a second profile
    in the database to prove the render did not reach for someone else."""
    _linked_profile(psn_username='SomeoneElse')
    profile = _linked_profile(psn_username='CardOwner')
    client.force_login(profile.user)

    monkeypatch.setattr('core.services.playwright_renderer.render_png',
                        lambda *a, **k: b'\x89PNG-fake')

    resp = client.get('/api/v1/shareables/profile/png/')

    assert resp.status_code == 200
    assert resp['Content-Type'] == 'image/png'
    # The name comes off display_psn_username, which the factory keeps lowercase.
    assert 'cardowner-profile-card.png' in resp['Content-Disposition'].lower()


def test_the_download_refuses_a_theme_it_cannot_render(client):
    """Recap's rule: an unknown theme, or one expecting a game image this card never supplies,
    is a 400 -- never a silent swap to a different ground than the preview showed."""
    profile = _linked_profile()
    client.force_login(profile.user)

    assert client.get('/api/v1/shareables/profile/png/?theme=nope').status_code == 400
    # The half the filter exists for: ppArt is real and curated, but it expects a game image.
    assert client.get('/api/v1/shareables/profile/png/?theme=ppArt').status_code == 400


def test_a_curated_ground_renders(client, monkeypatch):
    profile = _linked_profile()
    client.force_login(profile.user)
    seen = {}

    def fake_render(html, **kwargs):
        seen.update(kwargs)
        return b'PNG-fake'
    monkeypatch.setattr('core.services.playwright_renderer.render_png', fake_render)

    resp = client.get('/api/v1/shareables/profile/png/?theme=ppEmber')

    assert resp.status_code == 200
    assert seen.get('theme_key') == 'ppEmber', 'the chosen ground never reached the renderer'


def test_a_render_failure_reports_instead_of_crashing(client, monkeypatch):
    profile = _linked_profile()
    client.force_login(profile.user)

    def boom(*a, **k):
        raise RuntimeError('no chromium here')
    monkeypatch.setattr('core.services.playwright_renderer.render_png', boom)

    resp = client.get('/api/v1/shareables/profile/png/')

    assert resp.status_code == 500


# --- The Card tab ---

def _detail_url(profile):
    return f'/hunters/{profile.psn_username}/'


def test_the_owner_gets_the_card_tab(client):
    profile = _linked_profile()
    client.force_login(profile.user)

    body = client.get(_detail_url(profile) + '?tab=card', **CF).content.decode()

    # The opening tag, unescaped: 'share-image-content' alone survives autoescaping, so a
    # card_html that lost its SafeString-ness would keep that weaker assertion green while
    # rendering visible escaped markup to the owner.
    assert '<div class="share-image-content"' in body, 'the inline preview is missing or escaped'
    assert 'data-pfc-download' in body, 'the download control is missing'
    assert 'data-tab="card"' in body, 'the Card chip is not offered to the owner'
    # The family's grounds, the same .pc-theme swatch component the recap and plat cards render,
    # with no game-art backing offered (a career has no game to back it with).
    assert 'data-pfc-theme' in body, 'the ground swatches are missing'
    assert body.count('pc-theme__swatch') >= 8, 'the curated grounds are not all offered'
    assert 'pc-theme--art' not in body, 'a game-art ground leaked into the profile card picker'


def test_a_visitor_is_not_offered_the_chip_and_cannot_reach_the_tab(client):
    """Both halves matter: the chip is absent from the bar, AND typing the slug by hand normalizes
    to the default tab -- on the full render and on the HTMX path, which answers with the tab
    template directly and would otherwise leak the card chrome."""
    owner = _linked_profile(psn_username='TabOwner')
    visitor = _linked_profile(psn_username='JustLooking')
    client.force_login(visitor.user)

    full = client.get(_detail_url(owner) + '?tab=card', **CF)
    assert 'share-image-content' not in full.content.decode()
    # The chip, not the URL string: other markup (og tags, hx-get) echoes the query.
    assert 'data-tab="card"' not in full.content.decode()

    htmx = client.get(_detail_url(owner) + '?tab=card', **CF,
                      HTTP_HX_REQUEST='true', HTTP_HX_TARGET='tab-content')
    body = htmx.content.decode()
    assert 'data-pfc' not in body, 'the HTMX path answered a visitor with the card template'


def test_the_tab_wiring_is_actually_loaded():
    """Two single lines the whole tab degrades without, silently: the stylesheet @import (a
    dropped line renders an unclipped 1200px card) and the script include (a dropped line leaves
    the preview permanently cropped and the download button dead)."""
    css = (ROOT / 'static' / 'css' / 'input.css').read_text(encoding='utf-8')
    page = (ROOT / 'templates' / 'trophies' / 'profile_detail.html').read_text(encoding='utf-8')

    assert '@import "./components/profile-card-tab.css";' in css
    assert 'js/profile-card-tab.js' in page


def test_the_tab_survives_a_failed_card_build(client, monkeypatch):
    """The builder is try/except-isolated (the collection service's rule): a data-layer failure
    degrades to the empty state, never a 500 on the whole profile page."""
    profile = _linked_profile()
    client.force_login(profile.user)

    def boom(_profile):
        raise RuntimeError('degraded')
    monkeypatch.setattr('core.services.profile_card_service.get_card_data', boom)

    resp = client.get(_detail_url(profile) + '?tab=card', **CF)

    assert resp.status_code == 200
    assert b'could not be built' in resp.content
