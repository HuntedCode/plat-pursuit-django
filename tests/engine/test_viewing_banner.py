"""The ownership banner: "you are looking at someone else's progress".

Two bugs, one shared cause. Game detail and the badge header each carried their OWN copy of this banner,
so they drifted -- and when badge detail was rebuilt (bd2) the copy was left behind, leaving that page with
nothing but a small meta item. On game detail the surviving copy collided with the hero's pinned "X Players"
stat (gone since the trophy list pass). Both pages now render one shared chrome class.
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
    # Game detail may still POSITION the shared banner -- what it must not do is restyle it.
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


def test_the_banner_sits_above_the_hero_and_nothing_is_pinned_over_it(client):
    """The reported bug was a corner stat ("X Players") absolutely positioned against the card, landing on
    the banner. The trophy list pass (2026-10-22) replaced that corner with the Game page's platinum stat, a
    grid item in the hero's own grid: the banner sits above the grid in flow, so nothing can land on it as
    long as nothing in the hero is pinned to the card again."""
    from tests.factories import GameFactory, ProfileGameFactory, TrophyFactory

    game = GameFactory(defined_trophies={'bronze': 5, 'silver': 0, 'gold': 0, 'platinum': 1})
    TrophyFactory(game=game, trophy_type='platinum', trophy_earn_rate=4.2, trophy_rarity=1)
    other = ProfileFactory(is_linked=True, psn_username='Rival')
    ProfileGameFactory(profile=other, game=game, progress=40)
    client.force_login(ProfileFactory(is_linked=True).user)

    body = client.get(reverse('game_detail_with_profile', kwargs={
        'np_communication_id': game.np_communication_id, 'psn_username': other.psn_username}), **CF).content.decode()

    assert 'pp-viewing' in body and 'gp-hero__side' in body
    assert body.index('class="pp-viewing"') < body.index('gp-hero__grid'), 'the banner must sit above the hero grid'
    css = re.sub(r'/\*[\s\S]*?\*/', '', (ROOT / 'static' / 'css' / 'components' / 'game-page.css').read_text(encoding='utf-8'))
    pinned = re.findall(r'(\.gp-(?:hero__side|rarity)[^{]*)\{[^}]*position:\s*absolute', css)
    assert not pinned, f'a hero stat is pinned to the card again: {pinned}'
