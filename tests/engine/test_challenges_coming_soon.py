"""Challenges is a REAL PAGE while it is rebuilt, not a redirect.

The rule was set when the system was demolished in 2026-08: somebody following a link into a parked
system gets told so, on a page, in the site's own voice. Bouncing them to the homepage reads as a
broken link and teaches people the link is dead.

It also stops the Community rail looking like the whole offering is two items at the Game Lists
launch. What these tests hold is that the placeholder behaves like a page rather than like a hole --
and, critically, that it does not become a permanent thin search result once the real browse
replaces it at the same URL.
"""
import re
from pathlib import Path

import pytest
from django.urls import resolve, reverse

pytestmark = pytest.mark.django_db

ROOT = Path(__file__).resolve().parents[2]


def test_it_is_a_page_and_not_a_redirect(client):
    resp = client.get(reverse('challenges'))

    assert resp.status_code == 200, 'Challenges redirects again instead of explaining itself'
    body = resp.content.decode()
    assert 'Challenges' in body
    assert 'rebuilt' in body


def test_it_is_public(client):
    """Anonymous, signed-out, no membership. Somebody deciding whether this site is worth joining is
    exactly who follows a link to a feature page."""
    assert client.get(reverse('challenges')).status_code == 200


def test_it_promises_the_one_thing_the_codebase_can_keep(client):
    """`ArchivedAZChallenge` is the ONLY surviving copy of that data and the revival plan re-imports
    it, so "your past A-Z runs are safe" is a promise with a table behind it.

    Nothing else about the rebuild is promised here, deliberately: the shape of the returning
    challenges is still moving, and a placeholder page is the worst place to commit to it.
    """
    from trophies.models import ArchivedAZChallenge      # the promise's evidence

    assert ArchivedAZChallenge is not None
    body = client.get(reverse('challenges')).content.decode()
    assert 'A&ndash;Z runs are safe' in body or 'A–Z runs are safe' in body


def test_it_is_not_indexed(client):
    """A page whose only content is "not yet" is a thin result that answers nobody -- and because
    the real browse takes this same URL, a page that ranked as a placeholder would go on ranking
    with the wrong snippet afterwards. `follow` so the rail still passes authority onward."""
    body = client.get(reverse('challenges')).content.decode()

    assert 'noindex' in body
    assert 'nofollow' not in body, 'the rail should still pass authority to the pages that exist'


def test_it_costs_no_queries(client):
    """It reads nothing. A placeholder that touched the database would be the one page on the site
    whose cost is entirely waste."""
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    client.get(reverse('challenges'))          # warm any session/chrome caching
    with CaptureQueriesContext(connection) as ctx:
        assert client.get(reverse('challenges')).status_code == 200

    # The chrome (heartbeat, nav) may query; the PAGE must not add a challenge-shaped read, and
    # nothing may touch the archive table it is making a promise about.
    offenders = [q['sql'] for q in ctx.captured_queries if 'challenge' in q['sql'].lower()]
    assert offenders == [], f'the placeholder queried the challenge tables: {offenders}'

    # AND A CEILING ON THE TOTAL, because the check above is a negative with a narrow matcher: a
    # view that grew an unrelated read -- a profile lookup, a count against a renamed table -- is
    # invisible to it while the test's name still claims the page "costs no queries". The bound is
    # deliberately loose (it is chrome, not this page) and only has to catch a page that started
    # reading. Raise it consciously if the chrome genuinely grows.
    assert len(ctx.captured_queries) <= 12, (
        f'the placeholder now runs {len(ctx.captured_queries)} queries; it used to read nothing'
    )


def test_it_is_in_the_community_rail(client):
    """The rail is how somebody learns what a hub contains. A hub of two while a third is weeks away
    reads as the whole offering."""
    from core.hub_subnav import COMMUNITY_HUB

    slugs = [item.slug for item in COMMUNITY_HUB.items]
    assert 'challenges' in slugs
    assert 'lists' in slugs, 'Game Lists left the rail'
    # LAST. The two things you can actually use should not sit behind the one you cannot.
    assert slugs[-1] == 'challenges', 'the unfinished item is not at the end of the rail'


def test_the_unfinished_item_says_so_on_the_pill(client):
    """A pill that looks like its neighbours promises a destination like its neighbours. The tag is
    what tells somebody before they click, and dropping it is what marks the feature as shipped."""
    from core.hub_subnav import COMMUNITY_HUB

    challenges = next(item for item in COMMUNITY_HUB.items if item.slug == 'challenges')
    assert challenges.tag == 'Soon'
    # ...and nothing else wears one, so the tag stays meaningful.
    assert [i.slug for i in COMMUNITY_HUB.items if i.tag] == ['challenges']

    body = client.get(reverse('challenges')).content.decode()

    # BOTH RENDER SITES. The pill is drawn twice -- once in the rail, once in the overflow sheet the
    # narrow layout opens -- so `in body` proves only that ONE of them has it. Mutation testing
    # caught exactly that: stripping the `aria-label` from the rail left this green because the
    # sheet still carried it.
    assert body.count('pp-subpill__tag') == 2, 'the tag is missing from one of the two pills'
    # The `aria-label` REPLACES the contents-derived name, so it has to carry both parts or the tag
    # is invisible to a screen reader -- the trap the list tile's own comment records.
    assert body.count('aria-label="Challenges, coming soon"') == 2, (
        'a screen reader loses the tag on one of the two pills')


def test_the_real_browse_can_take_this_url_without_breaking_links():
    """The placeholder holds the URL and the url_name the real page will want, so nothing that links
    here has to be updated when it lands -- which is the whole reason not to call it
    `challenges_coming_soon` in the URL conf."""
    assert reverse('challenges') == '/community/challenges/'
    assert resolve('/community/challenges/').url_name == 'challenges'


def test_it_invents_no_css_that_will_outlive_it():
    """This page is deleted the day the real browse lands. A block invented for it would outlive it
    as orphan CSS, which is the class of bug the lists app keeps a guard against."""
    import re

    markup = (ROOT / 'templates' / 'pages' / 'challenges_coming_soon.html').read_text(encoding='utf-8')

    # CLASS ATTRIBUTES, OUT OF COMMENT BLOCKS FIRST. Two rounds of the same mistake got us here:
    # the original scanned the whole file and matched prose; scoping to `class="..."` was not
    # enough either, because the template's comments QUOTE the markup they replaced
    # (`class="scard p-4 md:p-6"`), and a regex over raw text cannot tell a quoted attribute from a
    # live one. Stripping `{% comment %}` and `{# #}` first is what actually makes this read the
    # page rather than the commentary about the page.
    live = re.sub(r'\{%\s*comment\s*%\}.*?\{%\s*endcomment\s*%\}', ' ', markup, flags=re.S)
    live = re.sub(r'\{#.*?#\}', ' ', live, flags=re.S)

    used = set()
    for attr in re.findall(r'class="([^"]+)"', live):
        used.update(attr.split())

    assert used, 'no class attributes found at all -- the scan is broken, not the page'

    invented = sorted(name for name in used if name.startswith('pp-soon'))
    assert not invented, f'a throwaway block came back: {invented}'
    # It reuses the shell every rebuilt page opens with, so it does not look half-finished beside
    # Game Lists in the same rail.
    #
    # ASSERTED AS CLASS TOKENS, from the `used` set built above. These read `in markup` -- the raw
    # file including comments -- and the template carries a long comment explaining why it is NOT
    # on `.scard` any more. So `assert 'scard' in markup` was satisfied purely by the prose about
    # removing it, and would have passed with the whole card deleted. Its sibling three tests down
    # asserts the opposite (`'scard' not in classes`); only the comment reconciled them.
    assert 'card' in used and 'card-body' in used, 'the page no longer uses the house card shell'
    assert 'pp-head-cascade' in used, 'the page lost the opening beat every rebuilt page has'
    assert 'scard' not in used, 'the prose block is back on the stat-cell primitive'


def test_the_tag_is_distinguishable_from_the_label_beside_it():
    """QUIET IS NOT INVISIBLE, and the first cut was invisible.

    The chip was `--pp-text-mute` (oklch 0.66 0.02 256) sitting next to a label in `--pp-text-dim`
    (oklch 0.72 0.02 256): same hue, same near-zero chroma, six hundredths of lightness apart, at
    under 0.8em. It read as more grey text. The active rule then turned it `--pp-primary` exactly
    when the label also turns primary, so it blended in both states.

    What this pins is the RULE rather than the shade: the chip must not be coloured with either of
    the greys the pill's own text uses, and must not take the colour the active label takes.
    """
    from pathlib import Path

    css = (Path(__file__).resolve().parents[2]
           / 'static' / 'css' / 'components' / 'chrome.css').read_text(encoding='utf-8')
    start = css.index('.pp-subpill__tag {')
    rule = css[start:css.index('}', start)]

    colour = [line for line in rule.splitlines() if line.strip().startswith('color:')]
    assert colour, 'the tag sets no colour of its own'
    assert '--pp-text-mute' not in colour[0] and '--pp-text-dim' not in colour[0], (
        'the tag is coloured with the same grey as the label beside it')

    # And nothing re-colours it to match the active label further down.
    #
    # THE RULE BODY, NOT THE SELECTOR LINE. This filtered lines containing both `is-active` and
    # `pp-subpill__tag` and then checked those lines for `--pp-primary` -- but a CSS rule in this
    # project's house style puts the selector on one line and each declaration on its own, so the
    # only line that ever matched was the SELECTOR, which will essentially never contain a colour.
    # The declaration was in a different list element and was never looked at. Adding
    # `.pp-subpill.is-active .pp-subpill__tag { color: var(--pp-primary) }` in the normal form --
    # the precise regression named above -- sailed straight through.
    for block in re.finditer(r'([^{}]*is-active[^{}]*pp-subpill__tag[^{}]*)\{([^}]*)\}', css):
        assert '--pp-primary' not in block.group(2), (
            'the tag turns primary on the active pill, where the label is already primary')


# ── polish-pass guards ───────────────────────────────────────────────────────────────────────────

def test_the_page_offers_somewhere_to_go(client):
    """A coming-soon page with no onward path is a bounce.

    It offered less than the browse grid's own empty state does, and that is a throwaway state
    rather than a whole page. The roadmap is the honest destination -- it already carries Challenges
    in its `in the works` tier and its content rules forbid dates, so it answers "when?" without
    this page promising anything.
    """
    body = client.get(reverse('challenges')).content.decode()

    # SCOPED TO THE PAGE'S OWN NAV. Against the whole document this passed with the links deleted,
    # because the site FOOTER also links the roadmap -- the assertion was reading the chrome.
    nav = body[body.index('aria-label="While you wait"'):]
    nav = nav[:nav.index('</nav>')]

    assert reverse('support_roadmap') in nav, 'no way to follow the rebuild'
    assert reverse('lists_browse') in nav, 'no route to the thing in this hub that exists'


def test_the_prose_block_is_not_a_stat_cell(client):
    """`.scard` sets `padding: 12px 8px` from an unlayered component file, and an unlayered
    declaration beats one inside `@layer utilities` whatever its specificity -- so the `p-4 md:p-6`
    written beside it never applied and the prose rendered at 8px of side padding on a phone,
    under a header card with 16px. `.scard` is the site's STAT CELL everywhere else; this was the
    only place it wrapped prose."""
    body = client.get(reverse('challenges')).content.decode()

    # MATCHED AS A CLASS TOKEN, not as a substring. `'scard' not in body` failed on the word
    # "Discard" in the unsaved-changes modal the chrome includes -- di-SCARD. A substring test
    # against a whole rendered document is almost always matching something you did not mean.
    classes = set()
    for attr in re.findall(r'class="([^"]+)"', body):
        classes.update(attr.split())

    assert 'scard' not in classes, (
        'the prose block is back on .scard, whose padding silently overrides the utilities beside it')
    assert 'card-body p-3 md:p-5 lg:p-7' in body, (
        'the prose block is not on the documented content-module progression')


def test_the_body_arrives_on_the_same_beat_as_the_header(client):
    """The header opened with the house cascade and the block the reader came for hard-cut in
    beneath it. The site animates its EMPTY STATES in; its coming-soon page should not just
    appear."""
    body = client.get(reverse('challenges')).content.decode()

    assert body.count('pp-head-cascade') == 2, (
        'the body block does not join the page entrance')


def test_the_share_card_says_what_the_page_says(client):
    """`noindex` means being passed around by hand is the only way this page travels, so the
    preview is the whole of its first impression. It fell back to the site name and the generic
    site blurb -- telling a reader nothing about why the link was sent to them."""
    body = client.get(reverse('challenges')).content.decode()

    # THE OG TAGS SPECIFICALLY. This first asserted the strings appeared anywhere in the body and
    # was vacuous on arrival: the page already carried a `meta_description` block containing both,
    # so it passed with no `seo_title`/`seo_description` set at all -- which is precisely the gap
    # it was written to close, since `og:*` reads those two and not `meta_description`.
    og_title = re.search(r'<meta[^>]+property="og:title"[^>]+content="([^"]*)"', body)
    og_desc = re.search(r'<meta[^>]+property="og:description"[^>]+content="([^"]*)"', body)

    assert og_title and 'coming back' in og_title.group(1), (
        f'og:title falls back to the site default: {og_title and og_title.group(1)!r}')
    assert og_desc and 'runs are safe' in og_desc.group(1), (
        f'og:description does not answer the real worry: {og_desc and og_desc.group(1)!r}')


def test_the_overflow_menu_keeps_the_tag_as_markup():
    """`subnav.js` rebuilt folded pills with `textContent`, which flattens
    `Challenges<span …>Soon</span>` into the literal unstyled string `ChallengesSoon` -- and dropped
    the `aria-label`, so it was announced that way too. Latent while Community is three pills, live
    the moment a tagged item lands on a rail that folds."""
    js = (Path(__file__).resolve().parents[2]
          / 'static' / 'js' / 'subnav.js').read_text(encoding='utf-8')
    # COMMENTS STRIPPED FIRST. The fix's own comment quotes the broken line it replaced, so scanning
    # the raw source found the string it was asserting the absence of -- a test failing on the
    # documentation of the bug it was written to prevent.
    code = re.sub(r'//[^\n]*', '', re.sub(r'/\*.*?\*/', '', js, flags=re.S))

    assert 'a.textContent = p.textContent' not in code, 'the overflow menu flattens pill markup again'
    assert 'cloneNode' in js, 'the overflow menu no longer copies the pill children'
    assert "getAttribute('aria-label')" in js, 'the overflow menu drops the pill accessible name'


def test_the_tag_is_announced_by_its_own_value():
    """The spoken phrase fired on "a tag exists" rather than on the tag's VALUE, hardcoded to
    "coming soon". The field documents 'New' as the other value, and a `tag='New'` pill would have
    announced "coming soon" while sighted readers saw NEW -- backwards, not merely wrong."""
    from core.hub_subnav import COMMUNITY_HUB

    template = (Path(__file__).resolve().parents[2]
                / 'templates' / 'partials' / 'hub_subnav.html').read_text(encoding='utf-8')

    assert 'coming soon"' not in template, 'the spoken tag phrase is hardcoded again'
    assert 'item.tag_aria' in template, 'the pill no longer speaks the tag its config declares'

    # THE FALLBACK, RENDERED. This asserted `HubSubnavItem(..., tag='New').tag_aria == ''` -- the
    # dataclass default equals the dataclass default, which is true by declaration and tests
    # nothing. The behaviour it claimed to pin lives in the template's `{% firstof %}`, which it
    # never touched: swapping that for a bare `{{ item.tag_aria }}` renders `aria-label="X, "` for
    # any tag without an explicit phrase, and the old assertion stayed green.
    # BOTH RENDER SITES, like the `aria-label` count two tests up. The pill is drawn twice -- once
    # in the rail, once in the overflow sheet -- so `in template` was satisfied by either one, and
    # a mutation that dropped the fallback from the rail alone left this green.
    assert template.count('{% firstof item.tag_aria item.tag %}') == 2, (
        'a tag with no spoken phrase announces nothing on one of the two pills')
    # And the config still carries an explicit phrase where the tag word alone would read oddly.
    challenges = next(i for i in COMMUNITY_HUB.items if i.slug == 'challenges')
    assert challenges.tag_aria == 'coming soon'
