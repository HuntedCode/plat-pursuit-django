"""The ownership banner: "you are looking at someone else's progress".

Two bugs, one shared cause. Game detail and the badge header each carried their OWN copy of this banner,
so they drifted -- and when badge detail was rebuilt (bd2) the copy was left behind, leaving that page with
nothing but a small meta item. On game detail the surviving copy collided with the hero's pinned "X Players"
stat. Both pages now render one shared chrome class.
"""
import re
from pathlib import Path

import pytest
from django.urls import reverse

from tests.engine.test_badge_detail import _group, _stage
from tests.factories import BadgeSeriesFactory, ProfileFactory

pytestmark = pytest.mark.django_db

ROOT = Path(__file__).resolve().parents[2]
CHROME = ROOT / 'static' / 'css' / 'components' / 'chrome.css'
GAME_CSS = ROOT / 'static' / 'css' / 'components' / 'game-detail.css'
BADGE_TPL = ROOT / 'templates' / 'trophies' / 'badge_detail.html'
# The Cloudflare origin guard blocks the profile-scoped badge path without a ray header.
CF = {'HTTP_CF_RAY': '8f0000000000abcd-LHR'}


def _viewing(client, other):
    """Sign a viewer in and open `other`'s badge page.

    The profile-scoped variant REQUIRES auth -- anon is redirected to the canonical page with a
    `from_profile` hint. Which is the right shape for this banner: 'View mine' only means something to
    someone who has a 'mine'. Without the login these tests assert against an empty 302 body.
    """
    client.force_login(ProfileFactory(is_linked=True, psn_username='Viewer').user)
    return client.get(
        reverse('badge_detail_with_profile', args=['gow', other.psn_username]), **CF
    ).content.decode()


def _series():
    series = BadgeSeriesFactory(series_slug='gow', name='God of War')
    _stage(series, 1, ['PS5'])
    _group(series, 'ultra-hd', 'Ultra HD', ['PS4', 'PS5'])
    return series


def test_badge_detail_shows_the_banner_for_someone_elses_progress(client):
    """The bug: the bd2 rebuild dropped it, so the page silently changed meaning with no banner saying so."""
    _series()
    other = ProfileFactory(is_linked=True, psn_username='Rival')

    body = _viewing(client, other)

    assert 'pp-viewing' in body, 'badge detail has no ownership banner when viewing another profile'
    assert (other.display_psn_username or other.psn_username) in body, 'the banner does not name whose page this is'


def test_badge_detail_has_no_banner_on_your_own_view(client):
    """The other half: no banner when there is nobody else to name.

    Signed in and opening your OWN profile-scoped page -- the `target_profile == viewer_profile` branch.
    Hitting the anonymous canonical URL instead would pass through a different branch (`target_profile` is
    None because nobody is logged in) and never exercise this one.
    """
    _series()
    owner = ProfileFactory(is_linked=True, psn_username='Owner')
    client.force_login(owner.user)

    response = client.get(
        reverse('badge_detail_with_profile', args=['gow', owner.psn_username]), **CF
    )

    assert response.status_code == 200
    assert 'pp-viewing' not in response.content.decode()


def test_the_banner_states_it_once_not_once_per_edition_tab(client):
    """It sits OUTSIDE the per-edition panels: whose progress this is does not change when you switch
    editions, so a per-panel banner would repeat itself once per tab and animate on every switch."""
    series = BadgeSeriesFactory(series_slug='gow', name='God of War')
    _stage(series, 1, ['PS5'])
    _group(series, 'legacy-hd', 'Legacy HD', ['PS3'])
    _group(series, 'ultra-hd', 'Ultra HD', ['PS4', 'PS5'])
    other = ProfileFactory(is_linked=True, psn_username='Rival')

    body = _viewing(client, other)

    assert body.count('data-bd2-view=') >= 2, 'the fixture built one panel, so the count below proves nothing'
    assert body.count('class="pp-viewing"') == 1, 'the banner repeats per edition panel'


def test_the_fact_is_not_also_stated_in_the_meta_row(client):
    """The meta row's "&middot; X's progress" item was the weaker signal the banner replaces. Leaving both
    states the same fact twice, a few centimetres apart."""
    _series()
    other = ProfileFactory(is_linked=True, psn_username='Rival')

    body = _viewing(client, other)

    name = other.display_psn_username or other.psn_username
    assert body.count(f"{name}</strong>'s progress") == 1
    assert f"&middot; {name}'s progress" not in body, 'the meta row still repeats what the banner says'


def test_both_pages_use_the_same_banner_class():
    """The point of the fix. Two copies is how one page ended up with a banner the other had lost, and how
    the surviving pair drifted in radius, weight and tint."""
    hero = (ROOT / 'templates/trophies/partials/game_detail/hero.html').read_text(encoding='utf-8')
    badge = BADGE_TPL.read_text(encoding='utf-8')

    for tpl, name in ((hero, 'game detail'), (badge, 'badge detail')):
        assert 'pp-viewing' in tpl, f'{name} is not on the shared banner'
        assert 'gd-viewing' not in tpl, f'{name} still uses the old page-local class'


def test_the_banner_is_declared_once_in_shared_chrome():
    """Declared in chrome.css and nowhere else -- a page-local redefinition is how the drift started."""
    assert '.pp-viewing {' in CHROME.read_text(encoding='utf-8')

    game = GAME_CSS.read_text(encoding='utf-8')
    assert '.gd-viewing' not in game, 'the game-detail copy of the banner is still declared'
    # Game detail may still POSITION the shared banner (it pins a stat over that corner) -- what it must
    # not do is restyle it.
    assert not re.search(r'(?m)^\s*\.pp-viewing\s*[,{]', game), 'game-detail.css redefines the shared banner'

    # Swept across EVERY component file, not just the two pages in play. Checking only game-detail.css is
    # how `.bdh-viewing` -- the badge header's copy, and the other half of the drift -- survived the first
    # cut of this change.
    # `.pp-bdetail__viewing` (badge-inspect.css) is deliberately NOT a copy: it is a compact pill inside
    # the badge modals, with no "View mine" affordance and no page-level role. Different component, not
    # the same one twice -- so it is named as an exception rather than swept up by a loose match.
    for css in (ROOT / 'static' / 'css' / 'components').glob('*.css'):
        if css.name in ('chrome.css', 'badge-inspect.css'):
            continue
        assert not re.search(r'(?m)^\s*\.[\w-]+-viewing\s*[,{]', css.read_text(encoding='utf-8')), (
            f'{css.name} declares its own ownership banner -- that is the duplication being removed'
        )


def test_the_hero_corner_stat_does_not_sit_on_top_of_the_banner():
    """The reported bug. `.gd-hero__players` is absolute against the `.card` (DaisyUI makes it the
    positioned ancestor), so it measures from the CARD's top edge -- above the banner, landing inside its
    tinted box on top of "View mine".

    The corner is re-anchored to the content ROW when the banner is present, so the banner stays full-width
    and the stat sits below it. Two earlier shapes were wrong and must not come back: offsetting `top` by a
    hand-measured banner height (drifts the moment a long username wraps the banner to two lines), and
    reserving horizontal space so the stat sits BESIDE the banner, which reads as though the two belong
    together -- badge detail puts its banner above everything, and these pages should agree.

    Pinned in the source because this is a layout collision no render assertion can catch.
    """
    css = GAME_CSS.read_text(encoding='utf-8')
    css = re.sub(r'/\*[\s\S]*?\*/', '', css)     # the comment explains the fix and names the rejected shapes

    scoped = re.findall(r'\.gd-hero__body:has\(\.pp-viewing\)\s+([^{]+)\{([^}]*)\}', css)
    assert scoped, 'nothing moves the pinned corner, so the stat sits on top of the banner again'

    decls = {sel.strip(): body for sel, body in scoped}
    assert '.gd-hero__grid' in decls and 'position: relative' in decls['.gd-hero__grid'], (
        'the content row is not a positioning context, so the corner still measures from the card'
    )
    assert 'margin-right' not in ' '.join(decls.values()), (
        'the banner is narrowed to make room -- that puts the stat beside it, not below it'
    )
    # Re-anchoring the row is only half of it: without re-zeroing the offsets the stat keeps measuring
    # `top: 22px` from the row and sits low.
    assert '.gd-hero__players' in decls and 'top: 0' in decls['.gd-hero__players'], (
        'the corner keeps its card-relative offsets, so re-anchoring the row just moves the overlap'
    )
    # And it must stay behind the md gate: below 768px the stat is in FLOW, where absolute offsets would
    # tear it out of the layout that already avoids the banner.
    blocks = re.findall(r'@media \(min-width:\s*768px\)\s*\{((?:[^{}]|\{[^{}]*\})*)\}', css)
    assert any('.gd-hero__body:has(.pp-viewing)' in b for b in blocks), (
        'the re-anchor is not scoped to md+, so it applies on mobile where the stat is in flow'
    )
