"""/support/roadmap/ -- the forward list in the platinum-roadmap frame -- and the storefront band.

The content rules are the tests that matter: the forward content carries no dates, months,
quarters, counts or percentages -- tier is the only promise, and the wishlist is dreams labelled
as dreams.
"""
import re

import pytest
from django.template.loader import render_to_string
from django.urls import reverse

from users.constants import ROADMAP_FEATURES, ROADMAP_TIERS

pytestmark = pytest.mark.django_db

TIERS = tuple(key for key, _name, _sub in ROADMAP_TIERS)


def _flat(client, url_name='support_roadmap'):
    body = client.get(reverse(url_name)).content.decode()
    return ' '.join(body.split())


# ------------------------------------------------------------------------- the skeletons ----

def test_the_feature_list_is_well_formed():
    """The page's tier sections walk this constant blind; every tier must have members."""
    tiers = [f['tier'] for f in ROADMAP_FEATURES]

    assert set(tiers) == set(TIERS)
    for feat in ROADMAP_FEATURES:
        assert feat['key'] and feat['name'] and feat['blurb']
    # Tier order in the constant mirrors display order: works, then next, then wishlist.
    assert tiers == sorted(tiers, key=TIERS.index)


def test_the_forward_content_promises_no_dates():
    """DELIBERATELY NO DATES anywhere forward-looking: the moment one slips, the roadmap becomes
    a promise ledger. Applies to the ahead stages AND every feature at every tier."""
    months = ('january february march april may june july august september '
              'october november december').split()
    forward = [f['name'] + ' ' + f['blurb'] for f in ROADMAP_FEATURES]

    for text in forward:
        low = text.lower()
        assert not re.search(r'\b(19|20)\d\d\b', low), f'a year slipped into: {text[:40]}'
        assert not re.search(r'\bq[1-4]\b', low), f'a quarter slipped into: {text[:40]}'
        assert not any(m in low for m in months), f'a month slipped into: {text[:40]}'


# ------------------------------------------------------------------------- the page --------

def test_the_roadmap_page_renders_for_everyone(client):
    response = client.get(reverse('support_roadmap'))
    assert response.status_code == 200
    body = ' '.join(response.content.decode().split())
    assert 'The platinum roadmap for PlatPursuit itself.' in body
    for _key, tier_name, _sub in ROADMAP_TIERS:
        assert tier_name in body
    assert 'just me!' in body


def test_every_feature_appears_in_its_own_tier(client):
    body = _flat(client)

    for tier in TIERS:
        section = body[body.index(f'is-{tier}'):]
        nxt = [body.index(f'is-{t2}') for t2 in TIERS if body.index(f'is-{t2}') > body.index(f'is-{tier}')]
        section = section[:min(nxt) - body.index(f'is-{tier}')] if nxt else section
        for feat in ROADMAP_FEATURES:
            if feat['tier'] == tier:
                assert feat['name'] in section, f"{feat['key']} missing from its {tier} section"


def test_the_wishlist_says_it_makes_no_promises(client):
    """The no-promises rule as UX: dreams get to be on the page BECAUSE they are marked as
    dreams. The label is load-bearing copy, not decoration."""
    body = _flat(client)
    assert 'Dreams, labelled as dreams. No promises here, just direction.' in body


def test_no_counts_anywhere_in_the_forward_body(client):
    """The old page put live tallies on its history cards; the pivot removed history, so now the
    rule is total: no tally and no percentage anywhere outside the header's banked line."""
    body = client.get(reverse('support_roadmap')).content.decode()

    assert 'pp-tally' not in body, 'a live count crept back onto a forward-only page'
    # Scripts out first: the rule is about CONTENT the reader sees, and the scroll observer's
    # rootMargin carries a percentage that is not copy.
    content = re.sub(r'<script.*?</script>', ' ', body, flags=re.S)
    text = ' '.join(re.sub(r'<[^>]+>', ' ', content).split())
    assert not re.search(r'\d+%', text), 'a percentage leaked into the roadmap'


def test_every_feature_key_has_its_own_icon():
    """A new feature added with a typo'd or missing icon key silently falls back to the compass;
    this makes that a loud failure instead of a quiet shrug."""
    fallback = render_to_string('support/_roadmap_icon.html', {'key': '__nope__'})

    for feat in ROADMAP_FEATURES:
        icon = render_to_string('support/_roadmap_icon.html', {'key': feat['key']})
        assert icon.strip() != fallback.strip(), f"{feat['key']} renders the fallback compass"


def test_the_ask_swaps_for_members(client):
    from unittest.mock import patch
    from tests.factories import UserFactory

    body = _flat(client)
    assert 'Come along for the ride' in body

    client.force_login(UserFactory())
    with patch('users.views.SubscriptionService.has_active_subscription',
               return_value=(True, 'stripe')):
        member_body = _flat(client)
    assert 'Come along for the ride' not in member_body
    assert 'You already are.' in member_body


def test_the_motion_is_reduced_motion_safe():
    """Both animations declared at the source under no-preference -- the pattern that cannot
    lose a cascade fight."""
    import pathlib
    root = pathlib.Path(__file__).resolve().parents[2]
    css = (root / 'static' / 'css' / 'components' / 'support-roadmap.css').read_text(encoding='utf-8')
    css = re.sub(r'/\*.*?\*/', '', css, flags=re.S)

    # Stronger than spot checks: EVERY animation declaration in this file must sit inside a
    # no-preference block. The lookback spans one gated block (nothing in this file puts an
    # animation more than ~700 chars from its gate; an oversized window can false-pass).
    for m in re.finditer(r'animation(-name)?:', css):
        window = css[max(0, m.start() - 700):m.start()]
        assert 'prefers-reduced-motion: no-preference' in window,             f'{m.group(0)} is declared outside a no-preference gate'


def test_the_cta_previews_the_storefronts_stars(client):
    """The button that leads to the storefront carries the storefront's own star emitter -- the
    destination's motion, previewed. Same markup, accent fallback colour, aria-hidden."""
    body = _flat(client)
    cta = body[body.index('rm-ask__cta'):]

    assert 'class="sup-go__rise" aria-hidden="true"' in cta
    assert cta.count('sup-go__risestar') == 5


def test_nothing_is_hidden_without_javascript(client):
    """The scroll choreography's contract: hiding gates on html.rm-arm, which only the head
    script sets (before paint, per motion-patterns). Server markup must never carry it, and the
    end-of-body script must reveal everything when IntersectionObserver is missing."""
    body = client.get(reverse('support_roadmap')).content.decode()

    # Scripts stripped first: the class only matters as MARKUP (a class attribute the server
    # rendered); the scripts may name it in code and comments freely.
    markup = re.sub(r'<script.*?</script>', ' ', body, flags=re.S)
    assert 'rm-arm' not in markup, 'the arming class is server-rendered; cards would hide without JS'
    # The no-IO fallback: armed by the head script but no observer means reveal all, not return.
    assert "classList.add('is-in')" in body


def test_the_breathing_chip_survives_the_css_build():
    """THE lightningcss trap, hit here despite being the most-documented gotcha in the codebase:
    color-mix inside @keyframes compiles to EMPTY frames, and the source-reading motion test
    passed while the built chip never breathed. This one reads the BUILD."""
    import pathlib as pl
    root = pl.Path(__file__).resolve().parents[2]
    out = (root / 'staticfiles' / 'css' / 'output.css').read_text(encoding='utf-8')

    i = out.index('@keyframes rmHere{')
    block = out[i:i + 300]
    assert 'box-shadow' in block, 'rmHere compiled to empty frames (color-mix in keyframes?)'


# ------------------------------------------------------------------------- the band --------

def test_the_storefront_carries_the_roadmap_band(client):
    body = _flat(client, 'support_hub')

    assert 'data-sup-road' in body
    assert 'Where this is going' in body
    band = body[body.index('data-sup-road'):body.index('data-sup-paid')]
    assert reverse('support_roadmap') in band, 'the band does not link to the full roadmap'


def test_the_band_speaks_the_pages_tier_vocabulary(client):
    """One story across both surfaces: the band compresses the SAME tiers and feature names the
    page walks in full -- no separate skeleton to drift."""
    body = _flat(client, 'support_hub')
    band = body[body.index('data-sup-road'):body.index('data-sup-paid')]

    for _key, tier_name, _sub in ROADMAP_TIERS:
        assert tier_name in band, f'{tier_name} missing from the band'
    first_works = next(f for f in ROADMAP_FEATURES if f['tier'] == 'works')
    assert first_works['name'] in band
    # The miniature: each cell wears the page's chip primitive and its tier class, so the band
    # inherits the temperature semantics instead of restyling them.
    for key in ('works', 'next', 'wishlist'):
        assert f'sup-road__cell is-{key}' in band, f'the {key} cell lost its tier class'
    assert band.count('rm-tier__chip') == 3
    # ...and every teased feature is its own tiny icon card (three per tier, capped).
    expected = sum(min(3, sum(1 for f in ROADMAP_FEATURES if f['tier'] == key))
                   for key in TIERS)
    assert band.count('sup-road__feat') == expected, 'a tier lost its icon-card tease'
    feat_block = band[band.index('sup-road__feat'):]
    assert 'rm-feat__icon' in feat_block and '<svg' in feat_block


def test_the_band_sits_inside_the_pitch(client):
    body = _flat(client, 'support_hub')

    assert body.index('data-sup-road') < body.index('data-sup-paid')
