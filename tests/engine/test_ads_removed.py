"""PlatPursuit is ad-free (2026-08).

Removed, not gated: there is no kill switch to flip back on, because a switch is exactly how this
creeps back. AdSense earned close to nothing while taxing the first impression of every new visitor,
which is the impression that produces members, so the whole layer went -- loader, CMP, rails, mobile
banner, in-content slots, context processor, settings, and the ``ad_free`` tier that existed only to
sell the removal of it.

What this pins is the ABSENCE OF THE MECHANISM rather than the absence of ad pixels on one page. A
single rendered page proves very little here: ads were already suppressed for premium users, on a
list of path prefixes, and whenever the env var was off, so a clean render was always reachable for
the wrong reason. The assertions below instead go after the things that would have to come back
first -- a slot in a template, an origin in the CSP, a tier in the constants.

Two of these also guard against OVER-reach, because the CSP cleanup had to keep entries that merely
sat next to AdSense ones (Google Fonts, IGDB covers). Deleting those would break the site quietly
in a way no ad test would notice.
"""
import ast
import re
from pathlib import Path

import pytest
from django.conf import settings
from django.urls import reverse

from users.constants import (
    PAYPAL_PLANS,
    PREMIUM_TIER_CHOICES,
    PREMIUM_TIER_DISPLAY,
    STRIPE_PRICES,
    STRIPE_PRODUCTS,
)

ROOT = Path(__file__).resolve().parents[2]

# Every marker that would signal the ad layer coming back. Kept as substrings rather than a regex
# per-marker so a failure names the exact token that reappeared.
AD_MARKERS = (
    'adsbygoogle',
    'googlesyndication',
    'fundingchoices',
    'adtrafficquality',
    'googletagservices',
    'doubleclick',
    'data-ad-client',
    'data-ad-slot',
    'ADSENSE',
    'has-mobile-ad',
)

# This file is allowed to name them -- it is the thing doing the checking.
SELF = Path(__file__).resolve()


def _templates():
    """Discovered, never enumerated.

    A hand-written list is the failure mode this suite has already hit once: the enumerated version
    of the reveal-bake test silently skipped a file that had been added after the list was written,
    and passed for years describing coverage it did not have. rglob cannot go stale.
    """
    return [p for p in (ROOT / 'templates').rglob('*.html')]


def test_templates_are_discovered_not_enumerated():
    """Guards the guard: if the glob ever resolves to nothing, every template assertion below turns
    into a vacuous pass over an empty list."""
    found = _templates()
    assert len(found) > 100, f'only {len(found)} templates found -- the glob is wrong, not the repo'


DJANGO_COMMENT = re.compile(
    r'\{#.*?#\}|\{%\s*comment\s*%\}.*?\{%\s*endcomment\s*%\}',
    re.DOTALL,
)


def _markup_only(text):
    """The template with its Django comments stripped out.

    Comments are removed as REGIONS, not line by line. The removal deliberately left prose behind
    explaining itself (base.html on the rails, subscribe.html on the retired benefit card), and both
    of those comments wrap onto continuation lines that do not themselves start with ``{#``. A
    per-line heuristic reads those continuations as live markup and fails on our own documentation.
    """
    return DJANGO_COMMENT.sub('', text)


@pytest.mark.parametrize('marker', AD_MARKERS)
def test_no_template_carries_an_ad_marker(marker):
    """Case-insensitive on purpose. The tokens that actually matter are lowercase in real markup
    (`adsbygoogle`, `data-ad-slot`), but a reintroduction could just as easily arrive as
    `{% if adsense_enabled %}`, and a case-sensitive scan for `ADSENSE` would sail straight past it."""
    needle = marker.lower()
    offenders = []
    for path in _templates():
        text = _markup_only(path.read_text(encoding='utf-8', errors='replace'))
        for lineno, line in enumerate(text.splitlines(), 1):
            if needle in line.lower():
                offenders.append(f'{path.relative_to(ROOT)}:{lineno}')
    assert not offenders, f'{marker!r} is back in: {", ".join(offenders)}'


def test_the_comment_stripper_only_eats_comments():
    """Guards the guard. If ``_markup_only`` over-matched it would blank whole templates and every
    marker assertion above would pass on empty strings."""
    sample = '<ins class="adsbygoogle"></ins>{# adsbygoogle in prose #}<div>keep</div>'
    stripped = _markup_only(sample)
    assert '<ins class="adsbygoogle">' in stripped, 'real markup was eaten'
    assert 'in prose' not in stripped, 'the comment survived'
    assert '<div>keep</div>' in stripped


def test_the_ad_unit_partial_is_gone():
    assert not (ROOT / 'templates' / 'partials' / 'ad_unit.html').exists()


def test_no_python_module_reads_an_adsense_setting():
    """`ADSENSE_ENABLED` and friends no longer exist on settings, so a surviving reader would be an
    AttributeError at request time rather than a quietly disabled ad."""
    for name in ('ADSENSE_ENABLED', 'ADSENSE_PUB_ID', 'ADSENSE_TEST_MODE'):
        assert not hasattr(settings, name), f'settings.{name} still exists'


def test_the_ads_context_processor_is_unregistered_and_gone():
    processors = settings.TEMPLATES[0]['OPTIONS']['context_processors']
    assert not any(p.endswith('context_processors.ads') for p in processors)

    import plat_pursuit.context_processors as cp
    assert not hasattr(cp, 'ads')


def test_context_processors_has_no_orphan_imports():
    """Removing `ads()` orphaned `from django.conf import settings`. An unused import is cheap to
    leave behind and the module is on every single request's path."""
    source = (ROOT / 'plat_pursuit' / 'context_processors.py').read_text(encoding='utf-8')
    tree = ast.parse(source)

    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update((a.asname or a.name).split('.')[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.update(a.asname or a.name for a in node.names)

    # Names only. Attribute access like `settings.FOO` already surfaces `settings` as a Name, so
    # unioning in attribute names would only add a way for an unrelated `x.settings` elsewhere in
    # the module to mask a genuinely orphaned import.
    used = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    orphans = {name for name in imported if name not in used}
    assert not orphans, f'unused imports in context_processors.py: {sorted(orphans)}'


# ---------------------------------------------------------------------------
# CSP: the ad origins are gone, and the neighbours they sat beside are NOT.
# ---------------------------------------------------------------------------

def _directive(name):
    return settings.CONTENT_SECURITY_POLICY['DIRECTIVES'][name]


def _all_csp_values():
    out = []
    for values in settings.CONTENT_SECURITY_POLICY['DIRECTIVES'].values():
        out.extend(values)
    return out


@pytest.mark.parametrize('origin_fragment', [
    'googlesyndication',
    'doubleclick',
    'adtrafficquality',
    'fundingchoices',
    'googletagservices',
    'adservice.google',
    'csi.gstatic',
    'www.gstatic',
])
def test_csp_has_no_ad_origin(origin_fragment):
    offenders = [v for v in _all_csp_values() if origin_fragment in v]
    assert not offenders, f'ad origin back in the CSP: {offenders}'


def test_csp_dropped_wasm_unsafe_eval():
    """AdSense creatives were the only WebAssembly consumer in the app, so this relaxation went with
    them. It is the one genuine security tightening in the ad removal, and the easiest to hand back
    by accident when some future library asks for it."""
    assert "'wasm-unsafe-eval'" not in _directive('script-src')


def test_csp_kept_google_fonts_over_https():
    """Over-reach guard. `http://fonts.gstatic.com` was AdSense (its creatives loaded Google Sans over
    http); the https origin is our own webfont stack, preconnected in base.html. Deleting both would
    break every font on the site with only a console warning to show for it."""
    assert 'https://fonts.gstatic.com' in _directive('font-src')
    assert 'http://fonts.gstatic.com' not in _directive('font-src')


def test_csp_kept_igdb_covers_on_img_src():
    """Over-reach guard. `images.igdb.com` appeared in BOTH img-src (our cover art, load-bearing) and
    connect-src (AdSense's content-categorization scanner, not ours). Only the connect-src copy went."""
    assert 'https://images.igdb.com' in _directive('img-src')
    assert 'https://images.igdb.com' not in _directive('connect-src')


def test_csp_kept_the_cloudflare_analytics_beacon():
    """Over-reach guard. The beacon sits in the same directives the ad origins did, and it has been
    silently CSP-blocked once before -- which made the Cloudflare dashboard read zero traffic while
    the site was being hammered."""
    assert 'https://static.cloudflareinsights.com' in _directive('script-src')
    assert 'https://cloudflareinsights.com' in _directive('connect-src')


# ---------------------------------------------------------------------------
# The ad_free tier
# ---------------------------------------------------------------------------

def test_ad_free_is_not_a_purchasable_tier():
    """It sold the removal of ads, so it had nothing left to sell. It was already withdrawn from the
    storefront and never had a subscriber, which is why no member migration was needed."""
    assert 'ad_free' not in dict(PREMIUM_TIER_CHOICES)
    assert 'ad_free' not in PREMIUM_TIER_DISPLAY


@pytest.mark.parametrize('mapping,label', [
    (STRIPE_PRODUCTS, 'STRIPE_PRODUCTS'),
    (STRIPE_PRICES, 'STRIPE_PRICES'),
    (PAYPAL_PLANS, 'PAYPAL_PLANS'),
])
def test_no_payment_mapping_can_resolve_to_ad_free(mapping, label):
    """A leftover product/price/plan id is how a webhook would resurrect the tier: the reverse lookup
    would happily map an incoming id back onto `ad_free` and write it to `premium_tier`."""
    for mode, tiers in mapping.items():
        assert 'ad_free' not in tiers, f'{label}[{mode!r}] still maps ad_free'


def test_paypal_reverse_lookup_cannot_yield_ad_free():
    from users.constants import PAYPAL_PLAN_TO_TIER
    assert 'ad_free' not in set(PAYPAL_PLAN_TO_TIER.values())


def test_every_purchasable_tier_currently_grants_premium():
    """Equality, not subset -- and the strictness is the point.

    Three templates read `user.premium_tier` for TRUTHINESS and treat it as "is premium":
    `base.html` and `navbar.html` gate the upsell banner on it, and `navbar.html` shows the "My
    Premium" link on it. Python never does this; it goes through `profile.user_is_premium` /
    `is_tier_premium()`, which consult ACTIVE_PREMIUM_TIERS.

    That divergence was REAL until 2026-08: `ad_free` was a purchasable tier that granted nothing, so
    those three templates hid the upsell from, and offered a premium link to, people who were not
    premium. Removing the tier closed the gap by accident rather than by design.

    So the lists now coincide, and the templates are correct *because of that coincidence*. Adding
    another non-feature tier would silently break all three the same way. This asserts the coincidence
    holds; if it ever must not, the fix is to give those templates a real is-premium flag FIRST.
    """
    from users.constants import ACTIVE_PREMIUM_TIERS
    purchasable = set(dict(PREMIUM_TIER_CHOICES))
    assert set(ACTIVE_PREMIUM_TIERS) == purchasable, (
        'A purchasable tier no longer grants premium. Before changing this assertion, fix the three '
        'templates that read premium_tier truthiness as "is premium" (base.html, navbar.html x2).'
    )


def test_the_two_tier_constant_modules_agree():
    """`trophies/constants.py` duplicates the tier identifiers for its own app. Two copies is how one
    of them ends up a tier behind."""
    from trophies.constants import ACTIVE_PREMIUM_TIERS as TROPHIES_TIERS
    from users.constants import ACTIVE_PREMIUM_TIERS as USERS_TIERS
    assert set(TROPHIES_TIERS) == set(USERS_TIERS)


def test_trophies_constants_dropped_its_ad_free_alias():
    import trophies.constants as tc
    assert not hasattr(tc, 'PREMIUM_TIER_AD_FREE')


# ---------------------------------------------------------------------------
# Rendered output
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_the_privacy_policy_states_the_site_serves_no_ads(client):
    """The legal correction and the promise are the same edit: the AdSense disclosure and the
    advertising-cookie bullet described a thing that no longer happens."""
    body = client.get(reverse('privacy')).content.decode()

    assert 'AdSense' not in body
    assert 'Advertising Cookies' not in body
    assert re.search(r'does not serve advertising', body), 'the no-ads statement is missing'
    # The cookie section still has to describe what we DO set.
    assert 'Essential Cookies' in body
    assert 'Analytics Cookies' in body


@pytest.mark.django_db
def test_a_rendered_page_carries_no_ad_markup(client):
    """Weakest assertion in the file and deliberately last: with the layer gone this can only pass.
    It earns its place by covering the rendered composition (base.html + a page template + context
    processors) rather than any single source file."""
    body = client.get(reverse('privacy')).content.decode()
    for marker in ('adsbygoogle', 'googlesyndication', 'data-ad-slot', 'mobile-ad-banner'):
        assert marker not in body, f'{marker!r} rendered into the page'
