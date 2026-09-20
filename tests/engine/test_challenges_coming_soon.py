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
