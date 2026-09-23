"""The profile tab strip keeps the promise its ARIA roles make.

The chips already carried `role="tab"` and `aria-controls="tab-content"`. That is a PROMISE: arrows
move between tabs, the strip is one tab stop, and the thing named by `aria-controls` is a panel. None
of it was true. `wireTablist` was never called, so Arrow/Home/End did nothing and all N chips sat in
the tab order -- the opposite of the pattern the role advertises -- and `#tab-content` never said it
was a `tabpanel`, so the tabs pointed at an element making no such claim.

A half-implemented role is worse than no role. A reader who does not trust it navigates by heading
and gets there; a reader who does trust it presses Right and nothing happens.

Asserted against rendered HTML and template source, because the JS half has no runner here.
"""
import re
from pathlib import Path

import pytest

from tests.factories import ProfileFactory

pytestmark = pytest.mark.django_db

ROOT = Path(__file__).resolve().parents[2]
PAGE = ROOT / 'templates' / 'trophies' / 'profile_detail.html'

#: Django block comments. A template's prose routinely NAMES the thing it deliberately does not do,
#: so an un-stripped source check answers about the documentation rather than the markup.
_COMMENT = re.compile(r'\{%\s*comment\s*%\}.*?\{%\s*endcomment\s*%\}', re.S)

#: The switcher markup only, so a chip assertion cannot be satisfied by matching text elsewhere.
_TAB_BAR = re.compile(r'<div class="pp-switch".*?</div>\s*</div>', re.S)

#: The panel's opening tag, which is where every attribute under test lives.
_PANEL = re.compile(r'<div id="tab-content"[^>]*>')


#: The same header every other profile test passes. Without it `SECURE_SSL_REDIRECT` answers 302 and
#: every assertion below reads an empty body.
CF = {'HTTP_CF_RAY': '8f0000000000abcd-LHR'}


def _hunter(psn='hunter'):
    return ProfileFactory(is_linked=True, psn_username=psn)


def _page(client, profile, tab=None):
    url = f'/hunters/{profile.psn_username}/'
    if tab:
        url = f'{url}?tab={tab}'
    response = client.get(url, **CF)
    assert response.status_code == 200, f'the profile answered {response.status_code}'
    return response.content.decode()


def _panel_tag(html):
    found = _PANEL.search(html)
    assert found, 'the page rendered no #tab-content panel at all'
    return found.group(0)


def _script():
    """The page's own JS, with Django comments stripped.

    The helper names appear in this file's prose as well as its code, and a bare substring check
    over the raw template stays green when the call is ripped out and replaced by a hand-rolled
    `chips[0].tabIndex = 0` -- the exact mutation that caught the sibling assertion in
    `test_gamelists_my_lists.py`.
    """
    source = _COMMENT.sub(' ', PAGE.read_text(encoding='utf-8'))
    source = re.sub(r'/\*.*?\*/', '', source, flags=re.S)
    return re.sub(r'^\s*//.*$', '', source, flags=re.M)


# ── the markup half ──────────────────────────────────────────────────────────────────────────────

def test_the_panel_says_it_is_a_panel(client):
    """`aria-controls` on the chips pointed here from the start. Until this, the thing it pointed at
    claimed nothing -- so the relationship was declared in one direction only."""
    html = _page(client, _hunter())

    assert 'role="tabpanel"' in _panel_tag(html), \
        'the tabs name a panel that does not say it is one'


def test_the_panel_is_named_by_the_tab_that_opened_it(client):
    """The other direction of the same relationship, and the one that gives the panel a NAME. Without
    an `id` on the chip it could not exist: `aria-labelledby` needs something to point at."""
    profile = _hunter()

    for tab in ('games', 'trophies'):
        html = _page(client, profile, tab)
        bar = _TAB_BAR.search(html)
        assert bar and f'id="profile-tab-{tab}"' in bar.group(0), \
            f'the {tab} chip has no id for the panel to name'
        assert f'aria-labelledby="profile-tab-{tab}"' in _panel_tag(html), \
            f'the panel is not named by the {tab} tab that opened it'


@pytest.mark.parametrize('requested', ['card', 'lists', 'not-a-tab-at-all'])
def test_every_resolved_tab_has_a_chip_to_name(client, requested):
    """THE INVARIANT THE UNCONDITIONAL `aria-labelledby` RESTS ON, pinned where it is made.

    The chips are conditional -- `lists` needs public lists, `card` needs it to be your own profile
    -- so a tab could in principle be resolved with no chip on the page, and the panel would then
    name an element that does not exist. A dangling reference is worse than none: the panel ends up
    unnamed either way, but a dangling one also asserts that somebody checked.

    It cannot happen, because the view normalizes each conditional tab back to `games` before
    rendering. That normalization exists for its own reasons (a switcher with nothing selected reads
    as broken), and the accessible name now depends on it too -- so it is asserted here rather than
    re-derived in the template, where it would be a second source of truth that could disagree.

    Reachable by a typed URL and by a chip that went stale between render and click.
    """
    html = _page(client, _hunter('someone'), requested)

    named = re.search(r'aria-labelledby="([^"]+)"', _panel_tag(html))
    assert named, 'the panel has no accessible name at all'

    bar = _TAB_BAR.search(html)
    assert bar and f'id="{named.group(1)}"' in bar.group(0),         f'the panel names {named.group(1)}, which is not a chip on this page'


def test_the_panel_takes_no_tab_stop_of_its_own(client):
    """The APG asks for `tabindex="0"` on a panel only when it holds NOTHING focusable. These panels
    are grids of links and buttons, so adding it would insert a tab stop in front of every one of
    them and buy nothing. `career.html`'s panels omit it for the same reason; this records that the
    omission is a decision."""
    html = _page(client, _hunter())

    assert 'tabindex' not in _panel_tag(html), \
        'the panel added a tab stop in front of content that is already reachable'


# ── the behaviour half ───────────────────────────────────────────────────────────────────────────

def test_the_strip_uses_the_shared_tablist_helper():
    """Arrow/Home/End and the roving tabindex, from the one implementation rather than a fifth copy.

    `manual: true` is not merely the cautious choice. The chips are `hx-get` anchors, so htmx already
    owns activation and a helper that also bound click would switch the panel twice -- the reason
    `gamelists.js` passes it too. It also makes arrows MOVE focus without activating, which is what
    the APG asks for when activating a tab costs a network fetch.
    """
    js = _script()

    assert 'PlatPursuit.wireTablist(' in js, \
        'the strip re-rolls the roving tabindex instead of calling the shared helper'
    assert 'manual: true' in js, \
        'wireTablist must not also bind click on hx-get chips, or the panel switches twice'


def test_the_roving_tabindex_is_resynced_after_a_swap():
    """`syncTabindex` reads `.is-active`, and htmx moves that class on every tab change. Wiring the
    helper once at load and never re-syncing leaves the tab stop on whichever chip was active when
    the page loaded -- so after one swap, Tab lands on a chip that is no longer selected."""
    js = _script()

    active = js.index('function updateActiveTab(')
    body = js[active:js.index('\n        }', active)]
    assert 'syncTabindex()' in body, \
        'the tab stop is left on the chip that was active when the page loaded'


def test_the_panel_name_follows_the_tab_across_a_swap():
    """There is ONE panel and htmx swaps its contents, so a server-rendered `aria-labelledby` would
    go on naming the tab the page LOADED with -- wrong from the first swap onwards, silently, and
    only to a screen reader.

    And it is REMOVED rather than left stale when nothing matches, which is the same rule the server
    applies on first render."""
    js = _script()

    active = js.index('function updateActiveTab(')
    body = js[active:js.index('\n        }', active)]
    assert "setAttribute('aria-labelledby'" in body, \
        'the panel goes on naming the tab the page loaded with'
    assert "removeAttribute('aria-labelledby')" in body, \
        'an unchipped tab leaves the previous tab\'s name on the panel'
