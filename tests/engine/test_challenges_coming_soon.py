"""Challenges is a REAL PAGE while it is rebuilt, not a redirect.

The rule was set when the system was demolished in 2026-08: somebody following a link into a parked
system gets told so, on a page, in the site's own voice. Bouncing them to the homepage reads as a
broken link and teaches people the link is dead.

It also stops the Community rail looking like the whole offering is two items at the Game Lists
launch. What these tests hold is that the placeholder behaves like a page rather than like a hole --
and, critically, that it does not become a permanent thin search result once the real browse
replaces it at the same URL.
"""
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

    # CLASS ATTRIBUTES ONLY. The first cut scanned the whole file and matched the COMMENT that
    # explains why this block does not exist -- a string assertion finding the wrong copy of itself,
    # which is the exact failure mode this project keeps re-learning.
    used = set()
    for attr in re.findall(r'class="([^"]+)"', markup):
        used.update(attr.split())

    invented = sorted(name for name in used if name.startswith('pp-soon'))
    assert not invented, f'a throwaway block came back: {invented}'
    # It reuses the shell every rebuilt page opens with, so it does not look half-finished beside
    # Game Lists in the same rail.
    assert 'pp-head-cascade' in markup
    assert 'scard' in markup


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
    after = css[start:]
    active = [line for line in after.splitlines()
              if 'is-active' in line and 'pp-subpill__tag' in line]
    assert not active or all('--pp-primary' not in line for line in active), (
        'the tag turns primary on the active pill, where the label is already primary')
