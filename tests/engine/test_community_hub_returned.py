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


def test_the_rail_turned_on_when_there_was_somewhere_else_to_go():
    """It ran `items=()` from 2026-09-13 to the un-hide, on the reasoning that emptied the
    Leaderboards rail: a single pill naming the page you are already on is not navigation. Game Lists
    gave the hub a second destination and that reasoning expired.

    My Lists is deliberately NOT here -- it is personal and login-gated, so it sits in My Pursuit ->
    Tools. The public browse is what belongs to the community.
    """
    from core.hub_subnav import HUB_SUBNAV_CONFIG

    hub = next(h for h in HUB_SUBNAV_CONFIG if h.key == 'community')
    # Exact equality, which already excludes `my_lists` -- a second assertion saying so was
    # decoration wearing a guard's message, and no edit could make one pass while the other failed.
    # `test_lists_live.py` carries the membership check that CAN fail independently.
    # `challenges` joined 2026-09-19: a coming-soon PAGE, not a redirect, because a hub of two
    # while a third is weeks away reads as the whole offering. It sits LAST and wears a
    # `Soon` tag -- the two things you can use should not sit behind the one you cannot. See
    # `test_challenges_coming_soon.py`.
    assert [i.slug for i in hub.items] == ['lists', 'profiles', 'challenges']


def test_hunters_is_chromed_as_community_everywhere_it_is_reachable():
    from plat_pursuit.context_processors import hub_subnav

    # `/profiles/` is INSURANCE, not a live render path: every route under it is a RedirectView,
    # and a 301 has no body, so no `/profiles/` request ever paints chrome. It is claimed so that
    # a future non-redirect route there cannot land in the wrong hub by default.
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
    # ...and each targets Hunters, checked on its OWN element. A bare `count('/hunters/') >= 2`
    # was satisfied by any two of the three links on the page (nav, tab bar, footer), so
    # re-pointing either nav control at the `/community/` redirect would have passed.
    desktop = body[body.index('>Community</a>') - 600:body.index('>Community</a>')]
    assert '/hunters/' in desktop, 'the desktop hub button does not point at Hunters'


def test_the_footer_files_hunters_under_community(client):
    """THE THIRD CHROME SURFACE, and the move missed it: for one commit the footer listed Hunters
    under a heading that said Browse, while the page it led to highlighted Community.

    The footer is where a nav change goes stale quietly, because nothing about editing the navbar
    exercises it.
    """
    body = client.get('/games/').content.decode()

    assert 'aria-label="Community pages"' in body, 'the footer has no Community column'

    community = body[body.index('aria-label="Community pages"'):]
    community = community[:community.index('</nav>')]
    # THE VISIBLE HEADING TOO, not just the landmark. Asserting only `aria-label="Community pages"`
    # let the <h2> a sighted reader actually reads say anything at all -- the same aria-versus-label
    # split that shipped a tab saying "Support" that navigated to Hunters. Third time in this lane.
    assert '>Community</h2>' in community, 'the column heading does not say Community'
    assert 'Hunters' in community, 'Hunters is not filed under Community'

    browse = body[body.index('aria-label="Browse pages"'):]
    browse = browse[:browse.index('</nav>')]
    assert 'Hunters' not in browse, 'Hunters is still filed under Browse'


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
    # Sliced FORWARD from the tab's own opening tag rather than backward by a guessed 400 chars:
    # a negative start index silently slices from the END of the string, which would have made
    # this pass or fail for reasons unrelated to the href.
    at = bar.index('aria-label="Community"')
    tab = bar[bar.rindex('<a ', 0, at):at]
    assert '/hunters/' in tab, 'the Community tab does not point at Hunters'


def test_every_hub_has_a_tab_and_the_fifth_returns_from_md(client):
    """The third part of the mobile trade, and the one that closes its only real cost: below `md` no
    tab highlights while you are in Support Us. From `md` there is room for five (~153px each at
    768px), so it comes back as a tab and the highlight with it."""
    body = client.get('/games/').content.decode()
    # THE SERVED BUNDLE, not the source. `base.html` links only `output.css`, so reading
    # `chrome.css` meant forgetting to rebuild left the fifth tab absent at every width with this
    # test green. Both are checked: the source for the rule's shape, the bundle for delivery.
    css = (ROOT / 'static/css/components/chrome.css').read_text(encoding='utf-8')
    served = (ROOT / 'staticfiles/css/output.css').read_text(encoding='utf-8')
    assert 'mobile-tabbar-item--wide' in served, 'the rule never reached the served bundle'

    bar = body[body.index('mobile-tabbar-inner'):]
    bar = bar[:bar.index('</nav>')]
    # On the ELEMENT's class attribute. A bare membership check on the rendered page was
    # satisfied by an HTML comment that named the class -- the comment shipped, so the string
    # was there with no fifth tab in sight.
    assert 'class="mobile-tabbar-item mobile-tabbar-item--wide' in bar, (
        'there is no fifth tab at any width')
    assert '<span>Support Us</span>' in bar
    # ...and the four unconditional ones are all still there. The name used to promise five hubs
    # while checking only Support Us.
    for label in ('Browse', 'Leaderboards', 'Community'):
        assert f'aria-label="{label}"' in bar, label
    # My Pursuit is auth-gated, so an anonymous client sees three plus the md+ fifth.
    assert 'aria-label="My Pursuit' not in bar, 'the personal hub is not anon-visible'

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


def test_a_signed_out_visitor_can_reach_support_us_from_the_chrome(client):
    """Below `md` the Support Us TAB is `display: none`, the Premium pill is auth-gated, and the anon
    avatar menu held only Log in and Register -- so a signed-out visitor on a phone had no route to
    the hub from the chrome at all. The only survivor was a footer link a full page away, labelled
    "Support Hub" rather than the name the chrome uses everywhere else.

    That is the audience a fundraiser page most needs to reach.
    """
    body = client.get('/games/').content.decode()

    panel = body[body.index('pp-avmenu'):]
    panel = panel[:panel.index('</div>')]
    assert 'Log in' in panel, 'this is not the anonymous menu'
    assert '>Support Us</a>' in panel, 'a signed-out phone visitor cannot reach the hub'


def test_support_us_is_marked_current_once_not_twice(client):
    """The dropdown entry carried `aria-current` on the reasoning that "no tab can show it below
    `lg`" -- which the next commit falsified by returning Support Us as a fifth tab from `md`. A
    screen reader then heard "current page" on two links to the same destination in two different
    landmarks."""
    from tests.factories import ProfileFactory, UserFactory

    user = UserFactory()
    ProfileFactory(user=user, is_linked=True, psn_username='hunter')
    client.force_login(user)

    body = client.get('/support/').content.decode()

    # The desktop hub button and the md+ tab each carry it; the dropdown entry must not.
    panel = body[body.index('pp-avmenu'):]
    panel = panel[:panel.index('</div>')]
    assert 'aria-current' not in panel, 'the dropdown duplicates the current-page marker'


def test_the_hub_is_relabelled_support_us():
    """"Support" alone reads as a help desk on most of the web, and this is the one place a confused
    reader would look for one."""
    from core.hub_subnav import HUB_SUBNAV_CONFIG

    hub = next(h for h in HUB_SUBNAV_CONFIG if h.key == 'support')
    assert hub.label == 'Support Us'


def test_the_five_hub_row_still_fits_between_1024_and_1279():
    """A fifth hub plus "Support" -> "Support Us" added roughly 136px to a row this stylesheet
    documents as having about 80px to spare at the `lg` container step.

    It did not degrade gracefully: `.pp-navhub` is `white-space: nowrap` so the items refuse to
    shrink, while `.pp-nav__hubs` carries `min-width: 0` so the CONTAINER may be squeezed below its
    contents -- the row overflowed and painted over the PSN search field. Dropping the icons in that
    band returns ~110px, the same one-thing-at-a-time trade the career pills already make there.
    """
    css = (ROOT / 'static/css/components/chrome.css').read_text(encoding='utf-8')

    band = css[css.index('@media (max-width: 1279px) {'):]
    band = band[:band.index(chr(10) + '}')]
    assert '.pp-nav__hubs .pp-navhub svg { display: none; }' in band, (
        'the five-hub row has nothing giving it room below 1280')
    # The 6px icon gap goes too, or the space is only half recovered.
    assert '.pp-nav__hubs .pp-navhub { gap: 0; }' in band

    # ...and the items still refuse to wrap, which is what makes the overflow ugly rather than tall.
    assert 'white-space: nowrap' in css[css.index('.pp-navhub {'):css.index('.pp-navhub svg')]


def test_the_avatar_menu_rows_meet_the_touch_floor():
    """They were ~36px. It matters more than it did: Support Us left the mobile tab bar for this
    menu, so below `md` these rows are the only route to a top-level hub, and the bar guarantees 56px
    for the other four."""
    css = (ROOT / 'static/css/components/chrome.css').read_text(encoding='utf-8')

    rule = css[css.index('.pp-avmenu a, .pp-avmenu button {'):]
    rule = rule[:rule.index('}')]
    assert 'min-height: 44px' in rule, 'dropdown rows are below the 44px floor'


def test_the_landing_page_did_not_come_back(client):
    """The hub returned; the PAGE did not. This is the distinction the whole design rests on, and it
    is cheap to lose by accident -- somebody adding a `/community/` route would break every inbound
    link that has been 301ing for a year."""
    resp = client.get('/community/')

    assert resp.status_code == 301
    assert resp['Location'] == '/leaderboards/'
