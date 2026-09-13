"""The Community hub returned (2026-09) as a nav grouping.

It is the fifth hub, and the reason it exists is worth stating because the doc it lives in spent a
year saying "resist a 5th". The four others sort by the reader's INTENT -- find, rank, mine, support
-- and miss a second axis: who AUTHORED the thing. Everything in Browse and Leaderboards is
site-owned (PSN data, IGDB metadata, PlatPursuit's own badges and jobs). User-generated content is a
different class, which the codebase already says by carrying an `all_ugc` restriction scope over
comments, reviews, ratings and lists.

**It is not a page**, and that is the distinction these tests exist to keep. `/community/` 301s to
`/leaderboards/` and always will -- it has been live since 2026-08 and is therefore cached in
browsers indefinitely. `test_community_hub_retired.py` still pins that. A hub here is a grouping of
destinations, not an address.
"""
from pathlib import Path

import pytest

pytestmark = pytest.mark.django_db

ROOT = Path(__file__).resolve().parents[2]


def _req(path):
    from django.contrib.auth.models import AnonymousUser
    from django.test import RequestFactory

    req = RequestFactory().get(path)
    req.user = AnonymousUser()
    return req


def test_the_hub_exists_and_owns_its_prefixes():
    from core.hub_subnav import HUB_SUBNAV_CONFIG

    hub = next((h for h in HUB_SUBNAV_CONFIG if h.key == 'community'), None)
    assert hub is not None, 'the Community hub is gone'
    assert hub.label == 'Community'
    # `/profiles/` is here alongside `/hunters/` while the 301s between them stand: this is a PATH
    # PREFIX match, so a visitor arriving on an old profile URL would otherwise lose the chrome on
    # the way through.
    assert set(hub.prefixes) == {'/community/', '/hunters/', '/profiles/'}


def test_it_carries_no_rail_yet_and_that_is_deliberate():
    """A single pill naming the page you are already on is not navigation -- the same reasoning that
    emptied the Leaderboards rail. Hunters is the only member today; the rail turns on when Game
    Lists comes off `_DevelopmentGate` and there is somewhere else to go."""
    from core.hub_subnav import HUB_SUBNAV_CONFIG

    hub = next(h for h in HUB_SUBNAV_CONFIG if h.key == 'community')
    assert hub.items == ()


def test_hunters_is_chromed_as_community_everywhere_it_is_reachable():
    from plat_pursuit.context_processors import hub_subnav

    for path in ('/hunters/', '/hunters/somebody/', '/profiles/'):
        assert hub_subnav(_req(path))['hub_section'] == 'community', path


def test_browse_kept_everything_except_hunters():
    """The move must not have taken a neighbour with it, and Browse must not still claim the prefix --
    two hubs claiming `/hunters/` would let match ORDER decide the chrome."""
    from core.hub_subnav import BROWSE_HUB

    assert 'profiles' not in [i.slug for i in BROWSE_HUB.items]
    assert not [p for p in BROWSE_HUB.prefixes if p.startswith(('/hunters/', '/profiles/'))]
    assert [i.slug for i in BROWSE_HUB.items if i.group == 'Catalog'] == [
        'games', 'trophy-lists', 'badges', 'jobs', 'recently-added']


def test_the_hub_is_reachable_from_the_chrome(client):
    """A hub nobody can click is not navigation. Both the desktop row and the mobile bar carry it,
    and both point at Hunters -- `/community/` is a redirect, so the button targets a real page."""
    body = client.get('/games/').content.decode()

    # BOTH, separately. The first draft joined them with `or`, which made the desktop half
    # unfailable -- the mobile clause alone satisfied it. The two are different markup in different
    # templates and either can go missing on its own.
    assert '>Community</a>' in body, 'the desktop hub row has no Community button'
    assert 'aria-label="Community"' in body, 'the mobile tab bar has no Community tab'
    # ...and both target Hunters, because `/community/` is a permanent redirect.
    assert body.count('/hunters/') >= 2


def test_the_mobile_tab_says_what_it_does(client):
    """THE BUG THIS EXISTS FOR: the swap changed the tab's href and its `aria-label` and left the
    VISIBLE span reading "Support" on a tab that navigated to Hunters.

    Every assertion in this file passed -- `aria-label="Community"` was present, `aria-label="Support"`
    was gone, and `>Community</a>` matched the DESKTOP button. Nothing looked at the one string a
    sighted person actually reads. Checking a label and its `aria-label` separately is the point:
    they are two different promises and either can be left behind.
    """
    body = client.get('/games/').content.decode()

    bar = body[body.index('mobile-tabbar-inner'):]
    bar = bar[:bar.index('</nav>')]

    assert '<span>Community</span>' in bar, 'the Community tab does not say Community'
    assert '<span>Support</span>' not in bar, 'a tab still carries the pre-swap label'
    # ...and it goes where it says. `/community/` is a permanent redirect, so the target is Hunters.
    community = bar[bar.index('aria-label="Community"') - 400:bar.index('aria-label="Community"')]
    assert '/hunters/' in community


def test_all_five_hubs_are_reachable_from_md_up(client):
    """The third part of the mobile trade, and the one that closes its only real cost: below `md` no
    tab highlights while you are in Support Us. From `md` there is room for five (~153px each at
    768px), so it comes back as a tab and the highlight with it."""
    body = client.get('/games/').content.decode()
    css = (ROOT / 'static/css/components/chrome.css').read_text(encoding='utf-8')

    bar = body[body.index('mobile-tabbar-inner'):]
    bar = bar[:bar.index('</nav>')]
    # On the ELEMENT's class attribute. A bare membership check on the rendered page was
    # satisfied by an HTML comment that named the class -- the comment shipped, so the string
    # was there with no fifth tab in sight.
    assert 'class="mobile-tabbar-item mobile-tabbar-item--wide' in bar, (
        'there is no fifth tab at any width')
    assert '<span>Support Us</span>' in bar

    # Mobile-first: absent by default, shown from `md`. The reverse (present, hidden below) would
    # put a fifth item in the 375px bar for anyone whose CSS had not loaded.
    rule = css[css.index('.mobile-tabbar-item--wide { display: none; }'):]
    assert '@media (min-width: 768px)' in rule[:200]
    assert 'display: flex' in rule[:300]


def test_support_us_left_the_mobile_bar_for_the_dropdown(client):
    """Five tabs do not fit: they are `flex: 1` at 0.62rem, so a fifth takes each from ~93px to ~75px
    at 375px while "Leaderboards" alone runs ~68-72px of text. Support Us is the right one to demote
    by frequency -- and it has to land somewhere, or the hub becomes unreachable below `lg`."""
    from tests.factories import ProfileFactory, UserFactory

    user = UserFactory()
    ProfileFactory(user=user, is_linked=True, psn_username='hunter')
    client.force_login(user)

    body = client.get('/games/').content.decode()

    # `aria-label="Support"` exactly -- the fifth tab is labelled "Support Us", so this still catches
    # the pre-swap tab coming back without flagging the deliberate md+ one.
    assert 'aria-label="Support"' not in body, 'the pre-swap Support tab is back'

    # INSIDE THE AVATAR PANEL, not merely somewhere on the page. The desktop hub button now reads
    # "Support Us" too, so a bare membership check matches that instead -- delete the dropdown entry
    # and the hub becomes unreachable below `lg` with the test still green. Found by a mutation that
    # was aimed at something else.
    panel = body[body.index('pp-avmenu'):]
    panel = panel[:panel.index('</div>')]
    assert '>Support Us</a>' in panel, 'Support Us is not in the avatar dropdown'


def test_the_hub_is_relabelled_support_us():
    """"Support" alone reads as a help desk on most of the web, and this is the one place a confused
    reader would look for one."""
    from core.hub_subnav import HUB_SUBNAV_CONFIG

    hub = next(h for h in HUB_SUBNAV_CONFIG if h.key == 'support')
    assert hub.label == 'Support Us'


def test_the_landing_page_did_not_come_back(client):
    """The hub returned; the PAGE did not. This is the distinction the whole design rests on, and it
    is cheap to lose by accident -- somebody adding a `/community/` route would break every inbound
    link that has been 301ing for a year."""
    resp = client.get('/community/')

    assert resp.status_code == 301
    assert resp['Location'] == '/leaderboards/'
