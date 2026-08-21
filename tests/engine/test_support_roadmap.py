"""/support/roadmap/ -- the forward list in the platinum-roadmap frame -- and the storefront band.

The content rules are the tests that matter: the forward content carries no dates, months,
quarters, counts or percentages (tier is the only promise; the wishlist is dreams labelled as
dreams), and the Horizon strip always pairs with real stage progress, never decoration.
"""
import re

import pytest
from django.template.loader import render_to_string
from django.urls import reverse

from users.constants import ROADMAP_FEATURES, ROADMAP_STAGES

pytestmark = pytest.mark.django_db

TIERS = ('works', 'next', 'wishlist')


def _flat(client, url_name='support_roadmap'):
    body = client.get(reverse(url_name)).content.decode()
    return ' '.join(body.split())


# ------------------------------------------------------------------------- the skeletons ----

def test_the_stage_skeleton_is_well_formed():
    """The pips and the storefront band walk this constant blind."""
    statuses = [st['status'] for st in ROADMAP_STAGES]

    assert statuses.count('now') == 1, 'exactly one stage is where we are'
    assert all(s in ('shipped', 'now', 'next', 'later') for s in statuses)
    assert statuses == sorted(statuses, key=('shipped', 'now', 'next', 'later').index)
    for stage in ROADMAP_STAGES:
        assert stage['key'] and stage['when'] and stage['title'] and stage['blurb']


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
    forward = [st['when'] + ' ' + st['blurb'] for st in ROADMAP_STAGES
               if st['status'] in ('next', 'later')]
    forward += [f['name'] + ' ' + f['blurb'] for f in ROADMAP_FEATURES]

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
    assert 'In the works' in body and 'Up next' in body and 'The wishlist' in body
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


def test_the_horizon_strip_carries_real_stage_progress(client):
    """The primitive's own anti-pattern rule: never decorative, always a real fraction."""
    body = _flat(client)
    shipped = sum(1 for st in ROADMAP_STAGES if st['status'] == 'shipped')

    strip = body[body.index('rm__pips'):body.index('rm__progress-k')]
    assert strip.count('pp-horizon__seg') == len(ROADMAP_STAGES)
    assert strip.count('data-state="done"') == shipped
    assert strip.count('data-state="active"') == 1
    assert f'aria-valuenow="{shipped}"' in strip
    assert f'{shipped} of {len(ROADMAP_STAGES)} stages banked' in body


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
    css = pathlib.Path('static/css/components/support-roadmap.css').read_text(encoding='utf-8')
    css = re.sub(r'/\*.*?\*/', '', css, flags=re.S)

    # Stronger than spot checks: EVERY animation declaration in this file must sit inside a
    # no-preference block. Lookback is generous because the pip-delay ladder is long.
    for m in re.finditer(r'animation: \w+', css):
        window = css[max(0, m.start() - 1400):m.start()]
        assert 'prefers-reduced-motion: no-preference' in window,             f'{m.group(0)} is declared outside a no-preference gate'


def test_the_cta_previews_the_storefronts_stars(client):
    """The button that leads to the storefront carries the storefront's own star emitter -- the
    destination's motion, previewed. Same markup, accent fallback colour, aria-hidden."""
    body = _flat(client)
    cta = body[body.index('rm-step__cta'):]

    assert 'class="sup-go__rise" aria-hidden="true"' in cta
    assert cta.count('sup-go__risestar') == 5


def test_the_pip_ignition_is_reduced_motion_safe_and_compositor_friendly():
    """The load-time journey replay: declared under no-preference at the source, and its
    keyframes touch opacity/transform only (box-shadow would also be doc-legal; these do not
    even need it)."""
    import pathlib
    css = pathlib.Path('static/css/components/support-roadmap.css').read_text(encoding='utf-8')
    css = re.sub(r'/\*.*?\*/', '', css, flags=re.S)

    pip_at = css.index('animation: rmPipIn')
    assert 'prefers-reduced-motion: no-preference' in css[max(0, pip_at - 500):pip_at]
    block = css[css.index('@keyframes rmPipIn'):]
    block = block[:block.index('\n}') + 2]
    for line in block.splitlines():
        line = line.strip()
        if ':' in line and not line.startswith(('@', '0%', '60%', '100%')):
            prop = line.split(':')[0].strip()
            assert prop in ('opacity', 'transform'), f'{prop} animates off-compositor'


def test_nothing_is_hidden_without_javascript(client):
    """The scroll choreography's contract: hiding happens only via the JS-added .rm-armed class
    (inside the no-preference gate). Server markup must never ship a hidden card."""
    body = client.get(reverse('support_roadmap')).content.decode()

    assert 'rm-armed' not in body.replace("classList.add('rm-armed')", ''),         'the armed class is server-rendered; cards would hide without JS'


# ------------------------------------------------------------------------- the band --------

def test_the_storefront_carries_the_roadmap_band(client):
    body = _flat(client, 'support_hub')

    assert 'data-sup-road' in body
    assert 'Where this is going' in body
    band = body[body.index('data-sup-road'):body.index('data-sup-paid')]
    assert reverse('support_roadmap') in band, 'the band does not link to the full roadmap'
    assert band.count('pp-horizon__seg') == len(ROADMAP_STAGES)


def test_the_band_names_only_the_stages_ahead(client):
    """The band sells what support BUILDS next; the serve band already brags for the past."""
    body = _flat(client, 'support_hub')
    band = body[body.index('data-sup-road'):body.index('data-sup-paid')]

    for stage in ROADMAP_STAGES:
        if stage['status'] in ('next', 'later'):
            assert stage['blurb'] in band, f"{stage['key']} missing from the band"
        else:
            assert stage['blurb'] not in band, f"{stage['key']} (not ahead) leaked into the band"


def test_the_band_sits_inside_the_pitch(client):
    body = _flat(client, 'support_hub')

    assert body.index('data-sup-road') < body.index('data-sup-paid')
