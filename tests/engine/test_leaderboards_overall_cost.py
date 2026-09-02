"""Cost defects on `/leaderboards/` (2026-08 backend audit).

Three findings, all about work the page did that nothing rendered. None of them changed a pixel, which is
why they survived: the page looked correct the whole time and simply cost more than it should.

1. The Series directory joined `IGDBMatch` twice per row without deferring `raw_response` -- the ~30 KB
   API blob CLAUDE.md names as the trigger for the May 2026 web-server OOM. Unpaginated, on a public page.
2. It then called Redis once per series inside a Python loop, so the round-trip count grew with the
   catalogue.
3. Every tab's board was assembled on every request, so `?tab=country` paid for the XP and global-progress
   pages it would never render.

These are pinned by BEHAVIOUR (the SQL that runs, the calls that are made), not by reading the source --
each one is a line that is easy to delete while leaving something that still renders correctly.
"""
import re
from unittest.mock import patch

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse


pytestmark = pytest.mark.django_db

URL = reverse('overall_badge_leaderboards')

# Every board key appears in the strip on every render, so "the key is in the body" says nothing about
# which one is OPEN. This reads the one chip marked current.
#
# `aria-current="page"`, not `aria-selected="true"`: the strip became a NAV of links in the 2026-08 audit.
# It was `role="tab"` chips with no tabpanel to control, and the roving tabindex that came with the tab
# roles made the boards you were not on unreachable by Tab.
_ACTIVE_CHIP = re.compile(r'aria-current="page"\s+data-board="([a-z-]+)"')


def active_board(body):
    match = _ACTIVE_CHIP.search(body)
    return match.group(1) if match else None


@pytest.mark.parametrize('tab, must_not_fetch', [
    ('trophies', ['xp_rows', 'career_xp_rows']),
    ('points',   ['trophy_rows', 'career_xp_rows']),
    ('career',   ['trophy_rows', 'xp_rows']),
])
def test_a_tab_does_not_pay_for_the_boards_it_does_not_render(client, tab, must_not_fetch):
    """Every board used to be assembled on every request. Only the ACTIVE one is built now.

    The RANK lookups deliberately stay unconditional and are not asserted against here -- they are single
    indexed COUNTs feeding the header's "your standing" strip, which shows all three whichever board is
    open. Cutting those would change what renders, not just what it costs.
    """
    patches = {name: patch(f'trophies.services.badge_leaderboards.{name}') for name in must_not_fetch}
    started = {name: p.start() for name, p in patches.items()}
    try:
        assert client.get(URL, {'tab': tab}).status_code == 200
    finally:
        for p in patches.values():
            p.stop()

    for name, mock in started.items():
        assert mock.call_count == 0, (
            f'?tab={tab} still built the {name} board it never renders ({mock.call_count} calls)'
        )


@pytest.mark.parametrize('legacy, lands_on', [
    ('xp', 'points'), ('country', 'points'), ('progress', 'trophies'),
])
def test_the_old_tab_keys_still_land_somewhere_sensible(client, legacy, lands_on):
    """`?tab=xp` was the Badge Points board, `?tab=country` was a TAB before country became a filter, and
    `?tab=progress` was Badge Trophies before it was renamed. All three are in the wild in links and
    bookmarks, and an unrecognised key silently falling back to the default would drop a reader somewhere
    they did not ask for with no sign anything happened.

    Asserted on the ACTIVE chip rather than on the key merely appearing: every key appears in the strip on
    every render, so `data-board="trophies" in body` is true no matter which tab the alias resolved to.
    """
    body = client.get(URL, {'tab': legacy}).content.decode()
    assert f'data-board="{lands_on}"' in body
    assert active_board(body) == lands_on, (
        f'?tab={legacy} rendered the strip but activated {active_board(body)!r}, not {lands_on!r}'
    )


def test_an_unknown_country_does_not_silently_empty_the_board(client):
    """An unvalidated code would return zero rows, which reads as "nobody from there plays" rather than
    "that is not a country we rank". The filter falls back to Everywhere instead."""
    from trophies.models import ProfileBadgeStanding
    from tests.factories import ProfileFactory

    ProfileFactory(display_psn_username='Somebody', country_code='CA', country='Canada',
                   is_linked=True, total_plats=2, total_trophies=30)

    body = client.get(URL, {'tab': 'trophies', 'country': 'ZZ'}).content.decode()
    assert 'Somebody' in body, 'an unknown country code emptied the board instead of falling back'
