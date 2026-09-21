"""A list's author wears their mark, on every surface the author appears.

Game Lists was the one place on the site that rendered an author as bare text, so a staff-written
list and a stranger's looked identical on the browse grid. `components/name_mark.html` has been the
one renderer for a marked name for a while; these tests pin that Lists now uses it too, and pin the
two things that are easy to break while doing so.

THE 99% CASE IS THE FRAGILE ONE. Almost nobody carries a mark, and `name_mark` renders an unmarked
name as a BARE text node with no wrapper, on purpose -- `.gl-card__author` truncates with
`text-overflow: ellipsis`, which works on a text node and does not work on a flex item. A "tidy-up"
that always wraps the name, or that turns the byline into a flex container, breaks truncation for
nearly every list on the page while looking fine on the handful that have a mark. That is why
`test_an_unmarked_author_renders_bare` exists and why it is worth more than the marked ones.

The marked case has its own trap, guarded by the CSS test at the bottom: the glyph sits AFTER the
name, so an unconstrained mark inside a `nowrap` byline gets sheared off by the container's
`overflow: hidden` -- losing the wrench on exactly the long names that made the row truncate.
"""
import re
from pathlib import Path

import pytest
from django.urls import reverse

from gamelists.services import game_list_service as svc
from tests.factories import ConceptFactory, GameFactory, ProfileFactory, UserFactory

pytestmark = pytest.mark.django_db

#: Reversed, not hand-written. The first draft of this file hardcoded `/community/lists/mine/` and
#: `/profile/<name>/`; both 404'd, and one of them failed as a *passing-looking* empty match rather
#: than as a clear 404, which is the failure mode that makes a hardcoded path in a test worse than
#: useless. `reverse` makes a moved route a loud error here instead of a quiet false green.
BROWSE = reverse('lists_browse')
MINE = reverse('my_lists')

#: Resolved from this file rather than the working directory; see the CSS tests at the bottom.
ROOT = Path(__file__).resolve().parents[2]

#: The byline as the tile renders it. Scoped to the `<p>` on purpose: the tile also states the
#: author inside its `aria-label`, so a bare `'curator' in html` passes whether or not the mark
#: rendered at all, and would have passed before this feature existed.
_AUTHOR_P = re.compile(r'<p class="gl-card__author">(.*?)</p>', re.S)


@pytest.fixture
def staff_client(client):
    """A logged-in viewer. The marks under test belong to the list OWNER, never to the viewer."""
    staff = UserFactory()
    staff.role = 'admin'
    staff.save()
    client.force_login(staff)
    return client


def _hunter(psn='curator', *, role=None, tier=None):
    """A profile whose mark is whatever the caller asks for.

    The mark is a DENORM (`Profile.display_mark`) written by `refresh_display_mark`, which runs off
    `CustomUser.save` for roles and off the premium reconcile for tiers. Both are poked through
    their real writers here rather than by setting `display_mark` directly, so these fixtures break
    if the precedence rules in `users/services/marks.py` ever move.
    """
    profile = ProfileFactory(is_linked=True, psn_username=psn)
    profile.user_is_premium = True
    profile.save(update_fields=['user_is_premium'])

    if role:
        profile.user.role = role
        profile.user.save()
    if tier:
        profile.user.premium_tier = tier
        profile.user.save()

    profile.refresh_from_db()
    return profile


def _list(owner, *, name='A list', public=True, games=1):
    game_list = svc.create_list(owner, name=name, is_public=public)
    for _ in range(games):
        concept = ConceptFactory()
        GameFactory(concept=concept, title_platform=['PS5'])
        svc.add_concept(game_list, owner, concept)
    return game_list


def _byline(html):
    """The contents of the tile's author `<p>`, or None when the tile did not render."""
    found = _AUTHOR_P.search(html)
    return found.group(1) if found else None


# ── the grid ─────────────────────────────────────────────────────────────────────────────────────

def test_a_staff_owned_list_wears_the_wrench_on_the_browse_grid(staff_client):
    """The whole point of the feature: an official list is distinguishable from a hunter's."""
    owner = _hunter('ppstaff', role='admin')
    _list(owner, name='Weekend Platinums')

    byline = _byline(staff_client.get(BROWSE).content.decode())

    assert byline is not None, 'the tile did not render; the rest of this file proves nothing'
    assert 'pp-markname' in byline, 'a staff author rendered without the mark wrapper'
    assert 'pp-markname__svc' in byline, 'the staff wrench glyph is missing from the byline'
    assert 'ppstaff' in byline


def test_a_supporter_author_wears_their_stars(staff_client):
    """The second register. `display_mark` carries the ladder slug, and the same one renderer turns
    it into stars -- so this passing is what makes "member, staff, etc" true rather than just
    "staff"."""
    owner = _hunter('backerpal', tier='backer')
    _list(owner, name='Cosy platinums')

    byline = _byline(staff_client.get(BROWSE).content.decode())

    assert 'pp-markname' in byline
    assert 'pp-supstar' in byline, 'a supporter author rendered without stars'


def test_an_unmarked_author_renders_bare(staff_client):
    """THE LOAD-BEARING ONE.

    `.gl-card__author` is `white-space: nowrap; overflow: hidden; text-overflow: ellipsis` with a
    bare text node inside, and that is the only arrangement in which the ellipsis actually appears.
    Wrapping every name -- or making the byline a flex container so the marked case looks tidier --
    turns the name into a flex item at min-content width, which hard-clips with no ellipsis for the
    ~99% of lists whose owner carries no mark.

    So: no wrapper, no glyph, just the name.
    """
    owner = _hunter('plainjane')
    _list(owner, name='Just a list')

    byline = _byline(staff_client.get(BROWSE).content.decode())

    assert byline.strip() == 'plainjane', (
        f'an unmarked author must render as a bare text node, got: {byline!r}')


def test_each_tile_gets_its_own_shimmer_index(staff_client):
    """`index` offsets the supporter shimmer per row. Without it a gridful of marked names pulses in
    lockstep, which reads as a page-wide effect rather than a per-person one.

    Asserted as DISTINCT delays rather than as specific values: the offset formula belongs to the
    tag, and pinning its arithmetic here would just copy it into a second place.
    """
    for n in range(3):
        _list(_hunter(f'starred{n}', tier='backer'), name=f'List {n}')

    html = staff_client.get(BROWSE).content.decode()
    delays = re.findall(r'--sup-delay:\s*(-?\d+ms)', html)

    assert len(delays) >= 3, f'expected a delay per marked tile, found {delays}'
    assert len(set(delays)) > 1, f'every tile got the same shimmer offset: {delays}'


def test_my_lists_passes_the_index_too(staff_client):
    """The tile is included from two grids and reads `mark_index` from whichever included it. My
    Lists was the one that could silently not pass it -- the tile still renders, just in lockstep,
    which no visual check catches."""
    owner = _hunter('selfcurator', tier='backer')
    staff_client.force_login(owner.user)
    for n in range(2):
        _list(owner, name=f'Mine {n}')

    html = staff_client.get(MINE).content.decode()
    delays = re.findall(r'--sup-delay:\s*(-?\d+ms)', html)

    assert len(set(delays)) > 1, f'My Lists rendered every tile with one offset: {delays}'


# ── the detail page ──────────────────────────────────────────────────────────────────────────────

def test_the_detail_page_byline_wears_the_mark(staff_client):
    owner = _hunter('ppstaff', role='admin')
    game_list = _list(owner, name='Weekend Platinums')

    html = staff_client.get(reverse('list_detail', args=[game_list.id])).content.decode()

    assert 'pp-markname__svc' in html, 'the list detail byline lost the staff wrench'


def test_the_detail_link_yields_its_colour_to_the_mark(staff_client):
    """A marked name is painted by its register (`--sup-t`). DaisyUI's `link` sets a colour on the
    anchor, which would override exactly the thing the mark exists to say -- so `link` is dropped
    when there is a mark and kept when there is not. Both halves are asserted, because a rule that
    only ever fires one way is a rule that was never tested."""
    marked = _list(_hunter('ppstaff', role='admin'), name='Official')
    plain = _list(_hunter('plainjane'), name='Ordinary')

    def anchor(game_list, psn):
        html = staff_client.get(
            reverse('list_detail', args=[game_list.id])).content.decode()
        # Anchored on THIS owner's profile URL. A looser pattern would match the first profile link
        # on the page, and the byline is not the only one.
        found = re.search(
            re.escape(reverse('profile_detail', args=[psn])) + r'"\s+class="([^"]*)"', html)
        assert found, f'no byline anchor for {psn}; the detail page did not render one'
        return found.group(1).split()

    assert 'link' not in anchor(marked, 'ppstaff'), 'a marked byline kept the link colour'
    assert 'link' in anchor(plain, 'plainjane'), 'an unmarked byline lost its link colour'


# ── the CSS contract ─────────────────────────────────────────────────────────────────────────────

def test_the_mark_is_capped_so_the_glyph_survives_truncation():
    """A source-CSS guard, because the failure it prevents is invisible in a render test: the mark
    renders perfectly and is then sheared off by the CONTAINER at a width no test client has.

    `.pp-markname` is an `inline-flex`. On a `nowrap` line nothing constrains it, so it lays out at
    full natural width and `.gl-card__author`'s `overflow: hidden` cuts off the overhang -- and the
    glyph sits after the name, so the overhang IS the mark. `max-width: 100%` gives the flex box a
    definite bound so the inner name absorbs the shrink instead.

    Pinned by RULE rather than by value: the cap is the mechanism, the specific percentage is not.
    """
    css = (ROOT / 'static' / 'css' / 'components' / 'gamelists.css').read_text(encoding='utf-8')

    assert '.gl-card__author .pp-markname' in css, (
        'the byline no longer caps the mark; a long marked name will lose its glyph')
    assert re.search(r'\.gl-card__author \.pp-markname\s*\{[^}]*max-width', css), (
        'the cap on .gl-card__author .pp-markname is no longer a max-width')


def test_the_byline_itself_is_not_a_flex_container():
    """The tempting "fix" that breaks the 99%. If someone turns `.gl-card__author` into a flex
    container, the marked case looks right and every unmarked name silently loses its ellipsis.
    `test_an_unmarked_author_renders_bare` guards the template half; this guards the CSS half."""
    css = (ROOT / 'static' / 'css' / 'components' / 'gamelists.css').read_text(encoding='utf-8')
    # EVERY block for this selector, not just the first. `re.search` stops at match one, so a
    # `display: flex` added in a later media-query override would have sailed straight through the
    # guard while breaking exactly what the guard exists to protect.
    blocks = re.findall(r'\.gl-card__author\s*\{([^}]*)\}', css)

    assert blocks, '.gl-card__author is gone; the byline truncation rules moved somewhere untested'
    for block in blocks:
        assert 'display: flex' not in block, (
            '.gl-card__author became a flex container -- unmarked names can no longer ellipsize')
