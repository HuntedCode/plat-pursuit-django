"""Profile Activity: sessions, and the cost of building them.

The tab this replaces paginated with `Paginator` over every earned trophy, which runs `COUNT(*)` across
the whole set on every page load. Profiles here reach 250,000+ trophies, so the cost properties below are
correctness, not tuning -- and none of them are visible in a page that merely renders.
"""
from datetime import timedelta

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from trophies.models import EarnedTrophy, Trophy
from trophies.services.activity_service import build_activity_page, day_sessions
from tests.factories import GameFactory, ProfileFactory

pytestmark = pytest.mark.django_db


def _earn(profile, game, when, tier='bronze', earn_rate=50.0, name=None):
    trophy = Trophy.objects.create(
        game=game,
        trophy_id=Trophy.objects.filter(game=game).count(),
        trophy_name=name or f'{tier} {timezone.now().timestamp()}',
        trophy_type=tier,
        trophy_earn_rate=earn_rate,
        trophy_group_id='default',
    )
    return EarnedTrophy.objects.create(
        profile=profile, trophy=trophy, earned=True, earned_date_time=when,
    )


def _sessions(profile, days_ago=1):
    """The sessions of one day. They are TIER 2 now: the page builds day tiles only, and a day's sessions
    are fetched when that day is opened."""
    return day_sessions(profile, _at(days_ago).date())


def _at(days_ago, hour=12):
    return timezone.now().replace(hour=hour, minute=0, second=0, microsecond=0) - timedelta(days=days_ago)


def test_one_session_per_game_per_day():
    """The unit. Twelve trophies from one afternoon on one game are ONE thing that happened."""
    profile = ProfileFactory(is_linked=True)
    game = GameFactory()
    for i in range(5):
        _earn(profile, game, _at(1, hour=10 + i))

    sessions = _sessions(profile)

    assert len(sessions) == 1
    assert sessions[0]['trophies'] == 5


def test_two_games_on_one_day_are_two_sessions():
    profile = ProfileFactory(is_linked=True)
    a, b = GameFactory(), GameFactory()
    _earn(profile, a, _at(1, hour=10))
    _earn(profile, b, _at(1, hour=20))

    sessions = _sessions(profile)

    assert len(sessions) == 2
    assert {s['game'].id for s in sessions} == {a.id, b.id}


def test_the_session_carries_its_tier_breakdown_and_platinum():
    """The card's headline facts. A platinum is the reason a session is worth looking at."""
    profile = ProfileFactory(is_linked=True)
    game = GameFactory()
    _earn(profile, game, _at(2, hour=9), tier='bronze')
    _earn(profile, game, _at(2, hour=10), tier='gold')
    _earn(profile, game, _at(2, hour=11), tier='platinum', earn_rate=1.2)

    session = _sessions(profile, 2)[0]

    assert session['trophies'] == 3
    assert session['has_platinum'] is True
    assert dict((t['tier'], t['count']) for t in session['tiers']) == {'bronze': 1, 'gold': 1, 'platinum': 1}
    assert session['rarest'] == pytest.approx(1.2)   # Min: a LOWER earn rate is rarer
    # Empty tiers are omitted rather than rendered as zeroes -- a card should not list what did not happen.
    assert all(t['count'] for t in session['tiers'])


def test_undated_trophies_are_excluded_not_guessed_at():
    """A session IS a day, so a trophy with no date has no session to belong to. It stays visible in the
    Log view; dropping it from both would lose it entirely."""
    profile = ProfileFactory(is_linked=True)
    game = GameFactory()
    _earn(profile, game, _at(1))
    undated = _earn(profile, game, _at(1))
    EarnedTrophy.objects.filter(pk=undated.pk).update(earned_date_time=None)

    assert _sessions(profile)[0]['trophies'] == 1, 'an undated trophy was counted into a day it has no claim to'


def test_unearned_trophies_never_appear():
    profile = ProfileFactory(is_linked=True)
    game = GameFactory()
    unearned = _earn(profile, game, _at(1))
    EarnedTrophy.objects.filter(pk=unearned.pk).update(earned=False)

    assert _sessions(profile) == []


def test_sessions_come_back_newest_first():
    profile = ProfileFactory(is_linked=True)
    game = GameFactory()
    _earn(profile, game, _at(9))
    _earn(profile, game, _at(1))
    _earn(profile, game, _at(5))

    days = [d['day'] for d in build_activity_page(profile)['activity_days']]

    assert days == sorted(days, reverse=True)


def test_pages_of_days_do_not_repeat_or_skip():
    """Offset over DISTINCT DAYS. That is safe where offsetting trophies is not: days are bounded by how
    long someone has been playing (thousands at most), while a whale holds 250,000 trophies. It also needs
    no COUNT -- the scroller stops when a page comes back short."""
    profile = ProfileFactory(is_linked=True)
    game = GameFactory()
    for d in range(1, 8):
        _earn(profile, game, _at(d))

    first = build_activity_page(profile, page=1, per_page=3)
    second = build_activity_page(profile, page=2, per_page=3)

    assert len(first['activity_days']) == 3
    assert len(second['activity_days']) == 3
    assert max(d['day'] for d in second['activity_days']) < min(d['day'] for d in first['activity_days'])


def test_a_day_is_never_split_across_two_pages():
    """Why the window is days rather than sessions: a day's sessions are indivisible. Cutting mid-day
    would need every already-shown (day, game) pair carried in the cursor to avoid repeats."""
    profile = ProfileFactory(is_linked=True)
    games = [GameFactory() for _ in range(4)]
    for i, g in enumerate(games):
        _earn(profile, g, _at(1, hour=9 + i))       # four sessions, all on ONE day
    _earn(profile, games[0], _at(2))

    first = build_activity_page(profile, per_page=1)

    assert len(first['activity_days']) == 1, 'the page window is not in whole days'
    assert first['activity_days'][0]['games'] == 4, 'a day lost games to the page boundary'


def test_the_last_page_ends_cleanly():
    profile = ProfileFactory(is_linked=True)
    _earn(profile, GameFactory(), _at(1))

    beyond = build_activity_page(profile, page=9, per_page=5)

    assert beyond['activity_days'] == []


def test_an_empty_profile_does_not_query_itself_into_a_hole():
    profile = ProfileFactory(is_linked=True)

    page = build_activity_page(profile)

    assert page == {'activity_days': []}


def test_the_query_count_does_not_grow_with_the_trophy_count():
    """THE whale property. The grouping happens in Postgres, so the work is a fixed handful of queries
    returning summary rows -- not one query per session, and not 250,000 ORM objects built in Python."""
    small = ProfileFactory(is_linked=True)
    game = GameFactory()
    _earn(small, game, _at(1))
    with CaptureQueriesContext(connection) as few:
        build_activity_page(small)

    big = ProfileFactory(is_linked=True)
    games = [GameFactory() for _ in range(6)]
    for d in range(1, 6):
        for g in games:
            for h in range(4):
                _earn(big, g, _at(d, hour=8 + h))
    with CaptureQueriesContext(connection) as many:
        build_activity_page(big)

    assert len(many) == len(few), (
        f'{len(few)} queries for 1 trophy, {len(many)} for 120 -- the page scales with the data\n'
        + '\n'.join(q['sql'][:120] for q in many.captured_queries)
    )


def test_the_cover_chain_is_joined_and_the_blob_deferred():
    """Session cards show cover art, and `display_image_url` resolves a trusted IGDB cover FIRST -- so
    without the join every card walks Game -> Concept -> IGDBMatch, and without the defer each row hauls
    the ~30 KB IGDB blob no cover template reads. The pair caused a web-server OOM once already."""
    profile = ProfileFactory(is_linked=True)
    for g in (GameFactory(), GameFactory(), GameFactory()):
        _earn(profile, g, _at(1))

    with CaptureQueriesContext(connection) as ctx:
        page = build_activity_page(profile)
        for d in page['activity_days']:
            for game in d['covers']:
                _ = game.display_image_url            # would N+1 without the join

    assert len(ctx) <= 3, f'{len(ctx)} queries -- the cover chain is resolving per card'
    assert not any('raw_response' in q['sql'] for q in ctx.captured_queries), (
        'the 30 KB IGDB blob is being hauled for cards that never read it'
    )


# --- the tab itself ------------------------------------------------------------------------------

CF = {'HTTP_CF_RAY': '8f0000000000abcd-LHR'}


def test_a_malformed_page_is_discarded_not_trusted(client):
    """`?page=` is client-supplied on a public, crawled URL. Anything unparseable falls back to page 1."""
    profile = ProfileFactory(is_linked=True)
    _earn(profile, GameFactory(), _at(1))

    response = client.get(
        f'/hunters/{profile.psn_username}/?tab=trophies&page=nonsense', **CF
    )

    assert response.status_code == 200
    assert len(response.context['activity_days']) == 1


def test_the_day_totals_what_its_sessions_add_up_to():
    """The day is a card, not a heading, and a card needs its own headline figures -- that is what makes
    the combined shape (one card per grouping, a row per member) worth more than a date over loose rows."""
    profile = ProfileFactory(is_linked=True)
    a, b = GameFactory(), GameFactory()
    _earn(profile, a, _at(1, hour=9))
    _earn(profile, a, _at(1, hour=10), tier='platinum')
    _earn(profile, b, _at(1, hour=20))

    day = build_activity_page(profile)['activity_days'][0]

    assert day['trophies'] == 3
    assert day['games'] == 2
    assert day['platinums'] == 1, 'the tile does not count the platinums under it'
    assert day['covers'], 'the tile has no covers for its mosaic'


def test_days_stay_newest_first():
    profile = ProfileFactory(is_linked=True)
    game = GameFactory()
    _earn(profile, game, _at(1))
    _earn(profile, game, _at(4))

    days = build_activity_page(profile)['activity_days']

    assert [d['day'] for d in days] == sorted((d['day'] for d in days), reverse=True)


def test_the_expanded_trophies_are_cards_not_a_list():
    """The rebuild stays off pure lists, and a trophy carries little enough -- icon, name, tier, rarity,
    the minute it popped -- that a card holds it without inventing filler to justify itself.

    `auto-fill` with a min track rather than fixed columns: these sit inside the day CARD, so the width
    they answer to is the container's, not the viewport's.
    """
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    tpl = (root / 'templates/trophies/partials/profile_detail/activity_trophies.html').read_text(encoding='utf-8')
    card = (root / 'templates/trophies/partials/profile_detail/_trophy_card.html').read_text(encoding='utf-8')
    css = (root / 'static' / 'css' / 'components' / 'profile-hero.css').read_text(encoding='utf-8')

    assert '<ul' not in tpl and '<li' not in tpl, 'the expansion is still a list'
    assert 'pp-actt__card' in card

    grid = re.search(r'(?m)^\.pp-actt\s*\{([^}]*)\}', css)
    assert grid and 'grid' in grid.group(1), 'the trophy cards do not lay out as a wall'
    assert 'auto-fill' in grid.group(1), 'fixed columns will break at the wrong width inside a day card'


def test_a_trophy_card_names_its_tier_rather_than_only_tinting_it():
    """The stripe and the icon ring carry the tier colour, and colour alone is not a label."""
    from pathlib import Path

    card = (Path(__file__).resolve().parents[2]
            / 'templates/trophies/partials/profile_detail/_trophy_card.html').read_text(encoding='utf-8')

    assert 'pp-actt__tier' in card and 'trophy_type|title' in card


# --- the three tiers -------------------------------------------------------------------------------

def test_the_page_carries_day_tiles_only_not_their_contents(client):
    """The point of the tiering, and the property worth guarding: the grid needs day totals and four
    covers, so that is ALL it builds. Sessions arrive when a day is opened, trophies when a session is.
    The flat version aggregated every session on the page whether or not anyone looked at one."""
    profile = ProfileFactory(is_linked=True)
    game = GameFactory()
    for h in range(6):
        _earn(profile, game, _at(1, hour=8 + h), name=f'trophy-{h}')

    body = client.get(f'/hunters/{profile.psn_username}/?tab=trophies', **CF).content.decode()

    assert 'pp-gtile' in body, 'days are not rendered as the shared grouping tile'
    assert 'trophy-0' not in body, 'the page shipped trophies it was built to summarise'
    # Matched as a class ATTRIBUTE: the page's own JS names this selector, so a bare substring check
    # would fail on the script rather than on any rendered session.
    assert 'class="pp-act__session' not in body, 'the page shipped the sessions a day modal is meant to fetch'


def test_a_day_is_a_real_url_not_only_a_modal(client):
    """The profile is public and crawled. Content reachable only by clicking is invisible to a crawler and
    impossible to link, so the tile is an <a> to a real page and the modal fetches that same URL."""
    profile = ProfileFactory(is_linked=True)
    _earn(profile, GameFactory(), _at(1), name='visible-trophy')
    day = _at(1).date().isoformat()

    grid = client.get(f'/hunters/{profile.psn_username}/?tab=trophies', **CF).content.decode()
    assert f'/day/{day}/' in grid, 'the day tile is not a link'

    page = client.get(f'/hunters/{profile.psn_username}/day/{day}/', **CF)
    assert page.status_code == 200
    # The standalone page must carry its trophies IN THE HTML -- a crawler will not make the fetch.
    assert 'visible-trophy' in page.content.decode()


def test_the_day_view_honours_the_privacy_flag(client):
    """It is reachable on its own URL, so it cannot lean on the tab's guard -- the same side door the HTMX
    tab bypass turned out to be."""
    profile = ProfileFactory(is_linked=True, psn_history_public=False)
    _earn(profile, GameFactory(), _at(1))

    assert client.get(f'/hunters/{profile.psn_username}/day/{_at(1).date().isoformat()}/',
                      **CF).status_code == 404


def test_an_empty_or_malformed_day_is_a_404(client):
    profile = ProfileFactory(is_linked=True)
    _earn(profile, GameFactory(), _at(1))

    assert client.get(f'/hunters/{profile.psn_username}/day/not-a-date/', **CF).status_code == 404
    assert client.get(f'/hunters/{profile.psn_username}/day/1999-01-01/', **CF).status_code == 404


def test_the_standalone_page_gets_its_trophies_in_one_query():
    """One query for the whole day grouped in Python, not one per session. A day has few games, but "few"
    is not a reason to write an N+1 into the page built for crawlers."""
    from trophies.services.activity_service import attach_day_trophies

    profile = ProfileFactory(is_linked=True)
    for _ in range(4):
        _earn(profile, GameFactory(), _at(1))
    day = _at(1).date()
    sessions = day_sessions(profile, day)

    with CaptureQueriesContext(connection) as ctx:
        attach_day_trophies(profile, day, sessions)

    assert len(ctx) == 1, f'{len(ctx)} queries for 4 sessions -- the day page N+1s'
    assert all(s['trophies_list'] for s in sessions)


def test_the_day_wall_scrolls_rather_than_asking_to_load_more():
    """Infinite scroll, as everywhere else on the site. The scroller is strictly `?page=N`, which is why
    days paginate by offset -- and why that is safe here: distinct days are bounded by how long someone has
    played, not by how many trophies they hold."""
    from pathlib import Path
    from trophies.services.activity_service import DAYS_PER_PAGE

    root = Path(__file__).resolve().parents[2]
    tpl = (root / 'templates/trophies/partials/profile_detail/activity_sessions.html').read_text(encoding='utf-8')

    # 30 fills whole rows at the grid's 2, 3 and 5 column breakpoints.
    assert DAYS_PER_PAGE == 30
    # The scroller finds its parts by id; without these it silently never initialises.
    for part in ('id="trophies-grid"', 'id="trophies-sentinel"', 'id="trophies-loading"'):
        assert part in tpl, f'{part} is missing, so infinite scroll cannot attach'
    assert 'Earlier activity' not in tpl, 'the load-more button survived alongside the scroller'


def test_the_scroller_is_served_tiles_not_a_whole_tab():
    """Appending a page must return TILES, or the scroller injects a grid inside the grid. The trophies tab
    has two views and only Activity appends tiles, so the mapping is keyed by view -- the Log still appends
    trophy rows."""
    from trophies.views.profile_views import ProfileDetailView

    keyed = ProfileDetailView._INFINITE_SCROLL_VIEWS
    assert keyed[('trophies', 'activity')].endswith('activity_tiles.html')
    assert keyed[('trophies', 'search')].endswith('trophy_list_items.html')


def test_appending_a_page_returns_bare_tiles(client):
    profile = ProfileFactory(is_linked=True)
    for d in range(1, 5):
        _earn(profile, GameFactory(), _at(d))

    body = client.get(
        f'/hunters/{profile.psn_username}/?tab=trophies&view=activity&page=2',
        HTTP_X_REQUESTED_WITH='XMLHttpRequest', **CF,
    ).content.decode()

    assert 'pp-gtile-grid' not in body, 'the scroller was handed a grid to nest inside its own'
    assert 'id="trophies-sentinel"' not in body


def test_every_game_in_a_day_arrives_open(client):
    """No collapsed poster, no per-session fetch: a day has few games, and showing them is cheaper than
    making someone ask twice."""
    profile = ProfileFactory(is_linked=True)
    _earn(profile, GameFactory(), _at(1), name='first-game-trophy')
    _earn(profile, GameFactory(), _at(1), name='second-game-trophy')

    body = client.get(f'/hunters/{profile.psn_username}/day/{_at(1).date().isoformat()}/',
                      HTTP_HX_REQUEST='true', **CF).content.decode()

    assert 'first-game-trophy' in body and 'second-game-trophy' in body
    # Collapsible, but OPEN -- no `hidden` on any panel, so every trophy is in the rendered page.
    assert body.count('data-act-collapse') == 2
    assert 'pp-act__trophies" id' in body and 'pp-act__trophies" hidden' not in body


def test_a_month_header_opens_each_month():
    """The wall breaks itself up by month, so scrolling for a particular time is not a hunt through
    undifferentiated tiles."""
    profile = ProfileFactory(is_linked=True)
    _earn(profile, GameFactory(), _at(1))
    _earn(profile, GameFactory(), _at(75))      # comfortably a different month

    days = build_activity_page(profile)['activity_days']

    assert days[0]['month_start'] is True, 'the first day of the wall opens its month'
    assert days[-1]['month_start'] is True, 'a new month did not get its header'


def test_days_inside_one_month_do_not_each_get_a_header():
    profile = ProfileFactory(is_linked=True)
    for d in (1, 2, 3):
        _earn(profile, GameFactory(), _at(d))

    days = build_activity_page(profile)['activity_days']
    marked = [d for d in days if d['month_start']]

    # Three consecutive days can straddle at most one month boundary.
    assert len(marked) <= 2
    assert days[0]['month_start'] is True


def test_a_month_running_across_a_page_boundary_is_not_re_announced():
    """The reason this is decided in the service. An appended page cannot see what is already on screen, so
    marking the first day of every page would repeat the header whenever a break lands mid-month."""
    profile = ProfileFactory(is_linked=True)
    for d in range(1, 7):
        _earn(profile, GameFactory(), _at(d))   # six consecutive days, one month

    second = build_activity_page(profile, page=2, per_page=3)['activity_days']

    assert second, 'the fixture did not produce a second page'
    assert second[0]['month_start'] is False, 'the month was announced again on the next page'


def test_the_first_page_always_opens_with_its_month():
    """There is nothing above it to continue from, so page 1 always states where it starts."""
    profile = ProfileFactory(is_linked=True)
    for d in range(1, 5):
        _earn(profile, GameFactory(), _at(d))

    assert build_activity_page(profile, page=1, per_page=2)['activity_days'][0]['month_start'] is True


def test_a_month_header_travels_with_its_day_when_appended():
    """The bug this exists to prevent, and it shipped once: the scroller appends ONLY elements matching its
    `cardSelector` and discards everything else, so a bare month header was dropped from every appended
    page. Page 1 kept its months, being server-rendered, and nothing after it had any.

    A header and its tile are one `.pp-act__cell` -- what the scroller both counts and appends. `display:
    contents` keeps both children as direct grid items, so the wrapper costs nothing in layout.
    """
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    tpl = (root / 'templates/trophies/partials/profile_detail/activity_tiles.html').read_text(encoding='utf-8')
    css = (root / 'static' / 'css' / 'components' / 'profile-hero.css').read_text(encoding='utf-8')
    js = (root / 'templates' / 'trophies' / 'profile_detail.html').read_text(encoding='utf-8')

    cell = tpl[tpl.index('<div class="pp-act__cell">'):tpl.index('{% endfor %}')]
    assert 'pp-act__month' in cell, 'the header sits outside the unit the scroller appends'
    assert 'pp-gtile' in cell

    rule = re.search(r'(?m)^\.pp-act__cell\s*\{([^}]*)\}', css)
    assert rule and 'display: contents' in rule.group(1), (
        'the wrapper is a real box, so it becomes the grid item instead of the tile'
    )
    assert "isActivity ? '.pp-act__cell'" in js, (
        'the scroller selects the tile again, which discards the month headers beside it'
    )


def test_one_cell_per_day_so_the_scroller_resumes_correctly(client):
    """The count the scroller divides by its page size. One cell per day whether or not that day opens a
    month -- a header must not inflate it, or an appended page resumes from the wrong offset."""
    profile = ProfileFactory(is_linked=True)
    for d in (1, 2, 75):                      # 75 days back guarantees a second month
        _earn(profile, GameFactory(), _at(d))

    body = client.get(f'/hunters/{profile.psn_username}/?tab=trophies', **CF).content.decode()

    assert body.count('class="pp-act__cell"') == 3
    assert body.count('pp-act__month') >= 2, 'the fixture did not span two months'


def test_only_the_walls_first_month_header_sits_flush():
    """A `display: contents` trap that silently undid the spacing.

    Every header is the first child of its own `.pp-act__cell`, so `.pp-act__month:first-child` matched all
    of them and zeroed the top margin the divider depends on -- the wrapper changes layout, not what
    `:first-child` matches. The rule is scoped through the CELL instead, so only the wall's opening month
    sits flush.
    """
    import re
    from pathlib import Path

    css = (Path(__file__).resolve().parents[2]
           / 'static' / 'css' / 'components' / 'profile-hero.css').read_text(encoding='utf-8')
    css = re.sub(r'/\*[\s\S]*?\*/', '', css)      # the comment above the rule explains the trap

    assert '.pp-act__cell:first-child .pp-act__month' in css, (
        'the flush rule is not scoped through the cell'
    )
    assert not re.search(r'(?m)^\.pp-act__month:first-child', css), (
        'every header is its cell\'s first child, so this matches all of them and kills the spacing'
    )


def test_the_month_reads_louder_than_its_year():
    """You scan a wall for "August", not for "2026" -- and on a wall mostly within one year, the year at
    full strength on every header is the least useful thing on it."""
    from pathlib import Path

    tpl = (Path(__file__).resolve().parents[2]
           / 'templates/trophies/partials/profile_detail/activity_tiles.html').read_text(encoding='utf-8')

    assert 'pp-act__month-yr' in tpl, 'the year is not distinguished from the month'
    header = tpl[tpl.index('pp-act__month">'):tpl.index('</h3>')]
    assert header.index('date:"F"') < header.index('pp-act__month-yr'), 'the month should lead'


def test_every_grid_gets_the_selector_its_own_cards_use():
    """A wrong `cardSelector` does not degrade -- the scroller appends only what it matches, so nothing
    matching means `newCards.length === 0`, which it reads as "no more pages" and stops. It looks exactly
    like having reached the end of someone's history.

    Games had been in that state since its card was rebuilt onto `.pp-gcard`: the default `.card` matched
    nothing in the appended HTML, so the games grid silently stopped after page one.
    """
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    js = (root / 'templates' / 'trophies' / 'profile_detail.html').read_text(encoding='utf-8')

    assert "cardSel = revealSel = '.pp-gcard';" in js, 'the games grid is back on .card'
    assert "cardSel = isActivity ? '.pp-act__cell' : '.pp-actt__card'" in js

    # The APPEND unit and the REVEAL target are separate on the activity wall: `.pp-act__cell` is
    # `display: contents` (it carries a month header along with its tile as one appendable thing) and so
    # has no box to animate. See test_profile_detail_queries for the failure that distinction prevents.
    assert "revealSel = isActivity ? '.pp-gtile'" in js

    # And the selectors must match what those partials actually render. Matched as a class among others
    # rather than as the whole attribute: the library variant adds `pp-gcard--lib` (2026-08), and a
    # selector is satisfied by the class being PRESENT. Pinning the exact attribute string would fail on
    # a modifier that is deliberately additive -- i.e. it would report a break that is not one, while
    # still not catching the break that matters (the class disappearing entirely).
    games = (root / 'templates/trophies/partials/profile_detail/game_list_items.html').read_text(encoding='utf-8')
    log = (root / 'templates/trophies/partials/profile_detail/trophy_list_items.html').read_text(encoding='utf-8')
    card = (root / 'templates/trophies/partials/profile_detail/_trophy_card.html').read_text(encoding='utf-8')
    assert re.search(r'class="[^"]*\bpp-gcard\b[^"]*"', games), (
        'the games partial no longer renders .pp-gcard, so the scroller will silently stop after page one'
    )
    assert '_trophy_card.html' in log and 'pp-actt__card' in card


def test_the_results_partials_render_the_same_wall_as_the_tab():
    """A results partial answers the same target the tab first rendered, so a stale class there silently
    undoes the layout on the first filter. Both had kept a pre-rebuild flex column.

    Compares the wall's FULL class list against the tab's, not the presence of one base class. The weaker
    version passed for months while `games_results.html` was missing `pp-gbrowse__grid--lib`: the modifier
    is what sets the library's column ladder, so the wall rendered correctly on load and then reverted to
    the browse ladder's six columns the moment anyone filtered, sorted or searched. A partial that shares
    a target has to share its classes exactly -- "contains the base class" is not that.
    """
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parents[2] / 'templates/trophies/partials/profile_detail/tabs'

    # The structural guarantee: a tab INCLUDES its results partial rather than restating the wall, so the
    # two cannot disagree at all. Asserted instead of merely comparing classes, because a class comparison
    # only catches the drift that has already happened -- this catches the duplication that allows it.
    for tab_file, results_file in [
        ('games_tab.html', 'games_results.html'),
        ('trophies_tab.html', 'trophies_results.html'),
        ('ratings_tab.html', 'ratings_results.html'),
    ]:
        tab = (root / tab_file).read_text(encoding='utf-8')
        assert results_file in tab, (
            f'{tab_file} no longer includes {results_file}. If it renders the wall itself, the first '
            f'filter swaps in different markup -- which is exactly how the Games wall came to use the '
            f'library column ladder on load and the browse ladder immediately after.'
        )
        # And it must not ALSO carry its own copy of the wall.
        assert not re.search(r'id="[\w-]+-grid"', tab), (
            f'{tab_file} restates a wall it already includes, so the two copies can drift'
        )

    # MARKUP only. These files explain themselves in `{% comment %}` blocks that name the very classes
    # being asserted, so a plain substring check passes against the prose describing a class that is no
    # longer applied -- which is how a deliberately broken wall came back green while this test watched.
    def markup(name):
        return re.sub(r'\{%\s*comment\s*%\}.*?\{%\s*endcomment\s*%\}', '',
                      (root / name).read_text(encoding='utf-8'), flags=re.S)

    trophies, games = markup('trophies_results.html'), markup('games_results.html')
    assert 'pp-actt--log' in trophies, 'filtering the Log drops it back to a list'
    assert 'pp-gbrowse__grid--lib' in games, 'the Games wall lost the library column ladder'
    assert 'flex flex-col' not in trophies and 'flex flex-col' not in games


def test_the_log_cover_keeps_its_aspect_ratio():
    """A stretched flex item takes its cross size from the STRETCH, not from `aspect-ratio` -- so the cover
    grew to the body's height, by a different amount per card, and `object-position: top` cropped the bottom
    off by that varying amount. Two covers side by side were visibly different heights."""
    import re
    from pathlib import Path

    css = (Path(__file__).resolve().parents[2]
           / 'static' / 'css' / 'components' / 'profile-hero.css').read_text(encoding='utf-8')

    art = re.search(r'(?m)^\.pp-actt__art\s*\{([^}]*)\}', css)
    # Any explicit align-self will do; what must not happen is the default STRETCH.
    assert art and 'align-self:' in art.group(1), 'the cover will stretch and lose its 3/4 ratio'
    assert 'stretch' not in art.group(1)
    assert 'aspect-ratio: 3 / 4' in art.group(1)


def test_the_log_wall_keeps_climbing_like_its_siblings():
    """Pinned to two columns forever, the Log was a 2-across wall inside a tab whose other view runs to
    five -- flipping between them read as a different page. A track FLOOR gives 2 at tablet and more as
    there is room, without packing in columns of truncated text."""
    import re
    from pathlib import Path

    css = (Path(__file__).resolve().parents[2]
           / 'static' / 'css' / 'components' / 'profile-hero.css').read_text(encoding='utf-8')

    # Checked against the wall's rules OUTSIDE the mobile block. Below 768px the cards are list rows and
    # one column is the point -- and `auto-fill minmax(182px, 1fr)` still makes THREE columns at 640px, so
    # without that override the rows would be squeezed into 202px each. The original intent (climb with the
    # room you have, never pin a count) is unchanged everywhere it applies.
    mobile = css[css.index('SEARCH RESULTS AS A LIST'):]
    mobile = mobile[:mobile.index('\n}\n', mobile.index('@media (max-width: 767px)')) + 3]
    wide = css.replace(mobile, '')

    # A track FLOOR at every width, not a fixed count -- the cards are portrait, so the floor is narrower
    # than the old horizontal card's and the wall still climbs with the room it has.
    rules = re.findall(r'\.pp-actt--log\s*\{([^}]*)\}', wide)
    assert rules, 'the search wall has no grid rule'
    assert all('auto-fill' in r for r in rules if 'grid-template-columns' in r), (
        'the search wall is pinned to a fixed column count again'
    )

    # And the mobile exception is deliberate, not a leak: assert it exists rather than merely tolerating it.
    mobile_rules = re.findall(r'\.pp-actt--log\s*\{([^}]*)\}', mobile)
    assert any('minmax(0, 1fr)' in r for r in mobile_rules), (
        'the log wall is no longer single-column on mobile, so the list rows share a row'
    )


def test_the_rarity_number_is_marked_as_PSNs(client):
    """The site publishes BOTH a PlatPursuit rate and a PSN one, and a bare percentage on a trophy card
    could as easily read as completion. It carries PSN's mark, as game detail's trophy list does.

    NOT graded with the `--rar-*` scale: those grades are against the whole PlatPursuit community while a
    PSN earn rate is against that game's owners, so grading one on the other's thresholds would read
    authoritative and be wrong.
    """
    from pathlib import Path

    card = (Path(__file__).resolve().parents[2]
            / 'templates/trophies/partials/profile_detail/_trophy_card.html').read_text(encoding='utf-8')

    assert 'ps_logo.html' in card, 'the earn rate is unattributed'
    assert 'rar-c' not in card and 'pp-rarity' not in card, (
        "a PSN earn rate is being graded on PlatPursuit's community scale"
    )


def test_the_whole_log_card_is_the_tap_target():
    """It has exactly one destination and it is unambiguous. A 48px cover and a game name that both mean
    "this game", where only the 11px text is clickable, is a missed affordance and a poor tap target."""
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    card = (root / 'templates/trophies/partials/profile_detail/_trophy_card.html').read_text(encoding='utf-8')
    css = (root / 'static' / 'css' / 'components' / 'profile-hero.css').read_text(encoding='utf-8')

    assert 'pp-actt__link' in card
    assert '.pp-actt__link::after' in css, 'the link is not stretched over the card'
    assert ':focus-within' in css, 'the focus ring is on the text rather than the card'
    # The session variant must stay inert: its host row is already the click target.
    sess = card[card.index('{% else %}'):card.index('{% endif %}', card.index('{% else %}'))]
    assert 'pp-actt__link' not in sess


# --- one surface, two shapes -------------------------------------------------------------------------

def test_the_tab_browses_by_default_and_searches_when_asked(client):
    """No switcher: the tab's shape follows intent. Empty search is the day wall; a query is the answer."""
    profile = ProfileFactory(is_linked=True)
    _earn(profile, GameFactory(), _at(1), name='Hoarder')

    browsing = client.get(f'/hunters/{profile.psn_username}/?tab=trophies', **CF)
    assert browsing.context['is_searching'] is False
    assert 'activity_days' in browsing.context
    assert 'pp-gtile' in browsing.content.decode()

    searching = client.get(f'/hunters/{profile.psn_username}/?tab=trophies&q=Hoarder', **CF)
    assert searching.context['is_searching'] is True
    body = searching.content.decode()
    assert 'Hoarder' in body and 'pp-actt__card' in body
    assert 'pp-gtile__mosaic' not in body, 'the day wall is still rendered underneath the results'


def test_search_matches_the_game_name_as_well_as_the_trophy(client):
    """"Did they ever play X" and "did they ever get Y" are the same question asked two ways."""
    profile = ProfileFactory(is_linked=True)
    game = GameFactory(title_name='Bloodborne')
    _earn(profile, game, _at(1), name='Unseen Blood')

    body = client.get(f'/hunters/{profile.psn_username}/?tab=trophies&q=Bloodborne', **CF).content.decode()

    assert 'Unseen Blood' in body


def test_a_tier_chip_searches_on_its_own(client):
    """The chips are a search in their own right, not a filter on top of one -- "show me their platinums"
    should not need a query first."""
    profile = ProfileFactory(is_linked=True)
    game = GameFactory()
    _earn(profile, game, _at(1), tier='platinum', name='The Platinum')
    _earn(profile, game, _at(1), tier='bronze', name='A Bronze')

    response = client.get(f'/hunters/{profile.psn_username}/?tab=trophies&tier=platinum', **CF)
    body = response.content.decode()

    assert response.context['is_searching'] is True
    assert 'The Platinum' in body and 'A Bronze' not in body


def test_an_unknown_tier_is_discarded_not_trusted(client):
    """Public, crawled URL: a hand-edited `?tier=` must not 500 or filter on nonsense."""
    profile = ProfileFactory(is_linked=True)
    _earn(profile, GameFactory(), _at(1))

    response = client.get(f'/hunters/{profile.psn_username}/?tab=trophies&tier=nonsense', **CF)

    assert response.status_code == 200
    assert response.context['trophy_selected'] == []
    assert response.context['is_searching'] is False, 'a bogus tier put the tab into search mode'


def test_search_does_not_count_the_whole_history():
    """`Paginator` runs COUNT(*) over the match set on every page, and a whale holds 250,000 trophies. The
    scroller stops when a page comes back short, so the count buys nothing -- the results are a slice."""
    import re
    from pathlib import Path

    src = (Path(__file__).resolve().parents[2] / 'trophies' / 'views' / 'profile_views.py').read_text(encoding='utf-8')
    # Bounded to this method: the class continues into builders that legitimately paginate, and a loose
    # slice picked those up. The docstring goes too -- it NAMES the Paginator it rejects.
    builder = src[src.index('def _build_trophy_search_context'):]
    builder = builder[:builder.index("return {'trophy_log'") + 200]
    builder = re.sub(r'"""[\s\S]*?"""', '', builder)
    builder = re.sub(r'#.*', '', builder)

    assert 'Paginator' not in builder, 'the search counts the whole match set to render one page'
    assert 'offset:offset + per_page' in builder


def test_the_search_joins_the_cover_chain_and_defers_the_blob():
    """Its cards show cover art, so the chain is joined and the ~30 KB blob deferred -- always together."""
    import re
    from pathlib import Path

    src = (Path(__file__).resolve().parents[2] / 'trophies' / 'views' / 'profile_views.py').read_text(encoding='utf-8')
    builder = src[src.index('def _build_trophy_search_context'):]
    builder = re.sub(r'#.*', '', builder[:builder.index('return {')])

    assert "'trophy__game__concept__igdb_match'" in builder
    assert "defer('trophy__game__concept__igdb_match__raw_response')" in builder


def test_the_log_view_and_its_form_are_gone():
    """The Log was an index dressed as a wall. Its sorts went to Activity, its platform and rarity-range
    filters went entirely, and what remained is the search box."""
    from pathlib import Path
    import trophies.forms as forms_module
    from trophies.views.profile_views import ProfileDetailView

    root = Path(__file__).resolve().parents[2]

    assert not hasattr(forms_module, 'ProfileTrophiesForm')
    assert not (root / 'templates/trophies/partials/profile_detail/trophy_log_filters.html').exists()
    assert not hasattr(ProfileDetailView, '_TROPHY_VIEWS'), 'the Activity|Log switcher survived'

    tab = (root / 'templates/trophies/partials/profile_detail/tabs/trophies_tab.html').read_text(encoding='utf-8')
    assert 'pp-switch' not in tab, 'the tab still renders a view switcher'
    assert 'pp-tsearch' in tab


def test_a_platinum_day_is_obvious_on_the_tile(client):
    """A platinum is the loudest thing that can happen on a day, and it used to be a 9px grey word in the
    count line -- not a signal on a wall of thirty tiles. It takes the franchise cards' corner-star seal
    plus a platinum edge, so the day reads as special before you get to any text on it."""
    profile = ProfileFactory(is_linked=True)
    game = GameFactory()
    _earn(profile, game, _at(1), tier='platinum')
    _earn(profile, GameFactory(), _at(4))          # an ordinary day, for contrast

    body = client.get(f'/hunters/{profile.psn_username}/?tab=trophies', **CF).content.decode()

    assert body.count('pp-act__seal') == 1, 'the platinum seal is missing, or on every day'
    assert body.count('pp-act__tile--plat') == 1, 'the platinum edge is missing, or on every tile'


def test_the_seal_counts_only_when_there_is_more_than_one(client):
    """A bare star already says "a platinum happened here"; the number is only worth printing when it is
    not one."""
    profile = ProfileFactory(is_linked=True)
    _earn(profile, GameFactory(), _at(1), tier='platinum')
    _earn(profile, GameFactory(), _at(1), tier='platinum')
    _earn(profile, GameFactory(), _at(6), tier='platinum')

    body = client.get(f'/hunters/{profile.psn_username}/?tab=trophies', **CF).content.decode()
    seals = body.split('pp-act__seal')

    assert '<b>2</b>' in seals[1], 'a two-platinum day does not show its count'
    assert '<b>1</b>' not in body, 'a single platinum is labelled with a redundant 1'


def test_search_cards_are_portrait_with_reserved_text_rows():
    """Art on top, facts beneath -- the same shape the badge gallery card uses, so a wall of these reads
    like the site's other walls rather than like a table that lost its columns.

    Both text rows RESERVE their two lines rather than merely capping them. A one-line trophy name beside
    a two-line one, or a trophy with no description at all, otherwise made every row of the wall a
    different height.
    """
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    css = (root / 'static' / 'css' / 'components' / 'profile-hero.css').read_text(encoding='utf-8')
    card = (root / 'templates/trophies/partials/profile_detail/_trophy_card.html').read_text(encoding='utf-8')

    variant = re.search(r'\.pp-actt__card--game\s*\{([^}]*)\}', css)
    assert variant and 'flex-direction: column' in variant.group(1), 'the search card is still horizontal'
    assert 'align-items: center' in variant.group(1)

    for sel in ('.pp-actt__card--game .pp-actt__name', '.pp-actt__card--game .pp-actt__desc'):
        rule = re.search(re.escape(sel) + r'\s*\{([^}]*)\}', css)
        assert rule and 'min-height' in rule.group(1), f'{sel} caps its lines but does not reserve them'

    # And the description must be emitted even when the trophy has none, or the reserve never applies.
    game_branch = card[card.index('{% if show_game %}', card.index('pp-actt__body')):card.index('{% elif t.trophy_detail %}')]
    assert 'pp-actt__desc' in game_branch and '{% if t.trophy_detail %}<span class="pp-actt__desc"' not in game_branch


def test_the_session_card_stays_horizontal():
    """Only the search variant went portrait. Inside a session the cards sit in a narrow well under a row
    that already names the game, where a column of posters would be a second wall inside a card."""
    import re
    from pathlib import Path

    css = (Path(__file__).resolve().parents[2]
           / 'static' / 'css' / 'components' / 'profile-hero.css').read_text(encoding='utf-8')

    base = re.search(r'(?m)^\.pp-actt__card\s*\{([^}]*)\}', css)
    assert base and 'flex-direction' not in base.group(1), 'the base card picked up the portrait layout'


def test_the_rarity_sits_on_the_same_baseline_as_the_text_beside_it():
    """It rendered a few pixels high. The meta row aligns on BASELINES, and an inline-flex box takes its
    baseline from its first flex item -- here the PS logo, whose baseline is its bottom EDGE rather than a
    text baseline, which lifted the whole rate.

    Opting the logo out of baseline alignment leaves the NUMBER as what the row aligns on. Also pins the
    element: the logo include emits an `<svg>`, not an `<img>`, so a rule written for `img` alone silently
    never applied.
    """
    import re
    from pathlib import Path

    css = (Path(__file__).resolve().parents[2]
           / 'static' / 'css' / 'components' / 'profile-hero.css').read_text(encoding='utf-8')

    rate = re.search(r'(?m)^\.pp-actt__rate\s*\{([^}]*)\}', css)
    assert rate and 'align-items: baseline' in rate.group(1), (
        'the logo supplies the box baseline again, lifting the rate off the row'
    )
    mark = re.search(r'\.pp-actt__rate svg[^{]*\{([^}]*)\}', css)
    assert mark, 'the logo is styled as an img only, which the include does not render'
    assert 'align-self: center' in mark.group(1), 'the logo is back in the baseline group'


def test_the_search_card_has_room_around_its_text():
    """Centred two-line text fills a poster card's whole width, so its padding has to clear its widest
    line rather than its tallest -- at the horizontal card's 9px it read as touching the edges."""
    import re
    from pathlib import Path

    css = (Path(__file__).resolve().parents[2]
           / 'static' / 'css' / 'components' / 'profile-hero.css').read_text(encoding='utf-8')

    rule = re.search(r'\.pp-actt__card--game\s*\{([^}]*)\}', css)
    pad = re.search(r'padding:\s*(\d+)px\s+(\d+)px', rule.group(1))
    assert pad and int(pad.group(2)) >= 13, 'the search card is cramped against its own text again'


def test_the_search_toolbar_is_the_sites_and_not_a_lookalike(client):
    """A hand-rolled field LOOKED like the site's and behaved like none of it.

    `browse-filters.js` gives any `[data-browse-form]` Enter-to-submit, debounced live search,
    Escape-to-clear, a clear button and an in-flight spinner -- but pages opt into those with MARKUP
    (`[data-search-wrap]`, `[data-search-clear]`, `.pp-search-spin`) and the shared `[data-page-search]`
    hook is what binds `/` to focus. Without them the behaviour is simply absent, silently.
    """
    from pathlib import Path

    tab = (Path(__file__).resolve().parents[2]
           / 'templates/trophies/partials/profile_detail/tabs/trophies_tab.html').read_text(encoding='utf-8')

    assert 'pp-toolbar-card' in tab, 'the toolbar has no shared surface'
    assert 'pp-bgal__search' in tab, 'the search field is bespoke again'
    for hook in ('data-browse-form', 'data-live-search', 'data-search-wrap',
                 'data-search-clear', 'data-page-search', 'pp-search-spin', 'pp-search-kbd'):
        assert hook in tab, f'{hook} missing -- that affordance is inert'

    assert 'pp-bgal__chip' in tab, 'the tier chips are bespoke'


def test_the_toolbar_supplies_its_own_box_metrics():
    """`.pp-toolbar-card` is SURFACE ONLY -- no padding, no margin. A page that omits them leaves its
    controls flush against the toolbar's own border, which is a documented trap."""
    import re
    from pathlib import Path

    css = (Path(__file__).resolve().parents[2]
           / 'static' / 'css' / 'components' / 'profile-hero.css').read_text(encoding='utf-8')

    rule = re.search(r'(?m)^\.pp-tsearch\s*\{([^}]*)\}', css)
    assert rule and 'padding' in rule.group(1) and 'margin-bottom' in rule.group(1)


def test_the_tier_chips_work_without_javascript(client):
    """Radios inside the shared chip, so `:has(input:checked)` drives the active state and the control is a
    real form field rather than a button that needs a handler."""
    from pathlib import Path

    tab = (Path(__file__).resolve().parents[2]
           / 'templates/trophies/partials/profile_detail/tabs/trophies_tab.html').read_text(encoding='utf-8')

    assert 'type="checkbox" name="tier"' in tab, 'the chips are not real form fields'

    profile = ProfileFactory(is_linked=True)
    _earn(profile, GameFactory(), _at(1), tier='platinum', name='NoJS Plat')

    body = client.get(f'/hunters/{profile.psn_username}/?tab=trophies&tier=platinum', **CF).content.decode()
    assert 'NoJS Plat' in body


# --- audit follow-ups: behaviour, not source text ---------------------------------------------------

def test_search_results_actually_append_a_second_page(client):
    """The scroller config had been derived from `?view=` -- the removed switcher's param, which nothing
    sets -- so the search shape was handed the day wall's page size AND its card selector. The scroller
    appends only what its selector matches, so search stopped dead after one page.

    Asserted by fetching page 2 the way the scroller does, rather than by grepping the JS for a string:
    three tests did exactly that and all passed while this was broken.
    """
    profile = ProfileFactory(is_linked=True)
    game = GameFactory(title_name='Repeated Game')
    _earn(profile, game, _at(1), name='a-match')

    body = client.get(
        f'/hunters/{profile.psn_username}/?tab=trophies&q=Repeated',
        HTTP_X_REQUESTED_WITH='XMLHttpRequest', **CF,
    ).content.decode()

    # What the scroller fetches: bare cards, no grid to nest inside its own, no day tiles.
    assert 'pp-actt__card' in body, 'the append path returns no trophy cards for the scroller to add'
    assert 'pp-gtile' not in body, 'the search was handed day tiles'
    assert 'pp-actt--log' not in body, 'the scroller was handed a grid to nest inside its own'

    # And the client must ASK for that shape -- read off what the SERVER RENDERED, not re-derived from the
    # URL. Deriving it here has been wrong twice: first against `?view=`, the removed switcher's param that
    # nothing sets (so it always believed it was browsing), then against `?q`/`?tier` raw, which is a second
    # definition of "is this a search" and disagrees with the view's. The view DROPS unknown tier values, so
    # `?tier=diamond` renders the day wall while a raw truthiness check calls it a search -- handing a wall
    # of `.pp-gtile`s the search card's selector, which skips the reveal and kills the scroll after page one.
    page = client.get(f'/hunters/{profile.psn_username}/?tab=trophies&q=Repeated', **CF).content.decode()
    assert "get('view')" not in page, 'the scroller still keys off a param nothing sets'
    assert "params.get('q')" not in page, 'the shape is re-derived from the URL again'
    # And read off the CHILDREN, not the grid's own class. Both shapes use `id="trophies-grid"`, and htmx
    # settle re-applies the OUTGOING node's attributes to the incoming one for the settle window -- which
    # is still open when afterSwap fires. Reading the class there reports the shape you just LEFT, which
    # broke the day wall on the way back from a filter: `.pp-actt__card` matched nothing, staggerReveal
    # bailed, and `.pp-reveal .pp-gtile` hid every tile with nothing able to un-hide it.
    assert "classList.contains('pp-actt')" not in page, (
        'the shape is read off an attribute htmx settle rewrites mid-swap'
    )
    # Scoped to the GRID's direct children. The day modal renders a `.pp-actt__card` per trophy and mounts
    # inside `#tab-content`, and closing it only sets `hidden` -- so an unscoped query would read "search"
    # while looking at the day wall, once any swap island changed.
    assert "querySelector('#trophies-grid > .pp-actt__card')" in page, (
        "the shape is no longer read off the grid's own cards"
    )

    # The concrete case the DOM check fixes: a tier the view does not recognise.
    stale = client.get(
        f'/hunters/{profile.psn_username}/?tab=trophies&tier=diamond', **CF
    ).content.decode()
    assert 'pp-gtile-grid' in stale, 'fixture drift: an unknown tier should still render the day wall'
    assert 'pp-actt--log' not in stale


def test_both_shapes_tell_the_scroller_their_own_page_size(client):
    """It gates its first fetch on the grid being a FULL page, so a size set on only one branch leaves the
    other silently unable to scroll at all."""
    from trophies.services.activity_service import DAYS_PER_PAGE

    profile = ProfileFactory(is_linked=True)
    _earn(profile, GameFactory(title_name='Findable'), _at(1))

    browsing = client.get(f'/hunters/{profile.psn_username}/?tab=trophies', **CF)
    searching = client.get(f'/hunters/{profile.psn_username}/?tab=trophies&q=Findable', **CF)

    assert browsing.context['scroll_per_page'] == DAYS_PER_PAGE
    assert searching.context['scroll_per_page'] > 0


def test_a_bogus_tier_cannot_split_the_context_from_the_template(client):
    """`get_template_names` read `?tier=` raw while the builder validated it, so a bogus tier built the day
    wall and rendered it with the search template -- which reads a `trophy_log` that does not exist."""
    profile = ProfileFactory(is_linked=True)
    _earn(profile, GameFactory(), _at(1))

    body = client.get(
        f'/hunters/{profile.psn_username}/?tab=trophies&tier=nonsense&page=1',
        HTTP_X_REQUESTED_WITH='XMLHttpRequest', **CF,
    ).content.decode()

    assert 'pp-gtile' in body, 'a bogus tier rendered the search template over day-wall context'


def test_a_day_means_the_same_thing_to_every_viewer():
    """`TimezoneMiddleware` activates each signed-in user's own zone and `TruncDate` follows the ACTIVE
    one, so day boundaries -- and therefore `profile_day` URLs, which are shared and crawlable -- would
    differ per viewer. A trophy at 02:00 UTC must not move to the previous day for a reader on UTC-8.
    """
    from django.utils import timezone as djtz

    profile = ProfileFactory(is_linked=True)
    _earn(profile, GameFactory(), _at(1).replace(hour=2))

    utc_days = [d['day'] for d in build_activity_page(profile)['activity_days']]
    djtz.activate('America/Los_Angeles')
    try:
        shifted = [d['day'] for d in build_activity_page(profile)['activity_days']]
    finally:
        djtz.deactivate()

    assert utc_days == shifted, 'the day a trophy belongs to moves with the viewer'


def test_a_day_is_filtered_as_a_timestamp_range_not_a_truncated_column():
    """The cost model of the whole tab. A predicate on `TruncDate(col)` cannot be an index range, so it
    degrades `earnedtrophy_timeline_idx` to its `(profile, earned)` prefix and reads every entry the
    profile owns, joined to Trophy. The trunc belongs in `values()`, which names the group, and nowhere
    near a WHERE.

    Asserted on the SQL Django actually emits: the failure is invisible in the source and in the results.
    """
    profile = ProfileFactory(is_linked=True)
    _earn(profile, GameFactory(), _at(1))

    with CaptureQueriesContext(connection) as ctx:
        day_sessions(profile, _at(1).date())

    checked = 0
    for q in ctx.captured_queries:
        if 'WHERE' not in q['sql']:
            continue
        predicate = q['sql'][q['sql'].index('WHERE'):]
        checked += 1
        assert '::date' not in predicate, (
            'a truncated column is being filtered on, which cannot use the index:\n' + predicate[:300]
        )
    assert checked, 'no filtered query was captured'


def test_every_day_on_a_page_gets_covers_for_its_mosaic():
    """The cover budget was a flat prefix of the page's rows. That only matches "the first few of each day"
    if every earlier day has at most four games -- a hunter playing nine a day exhausted it within the
    first third of the page, and every day after rendered an empty mosaic."""
    profile = ProfileFactory(is_linked=True)
    for d in range(1, 6):
        for _ in range(9):
            _earn(profile, GameFactory(), _at(d))

    days = build_activity_page(profile)['activity_days']

    assert len(days) == 5
    assert all(d['covers'] for d in days), 'later days on the page have no cover art at all'


def test_the_day_counts_platinums_not_games_that_have_one():
    """The key promises platinums, and the tile's seal prints it as a count."""
    profile = ProfileFactory(is_linked=True)
    game = GameFactory()
    _earn(profile, game, _at(1), tier='platinum')
    _earn(profile, game, _at(1), tier='platinum')

    assert build_activity_page(profile)['activity_days'][0]['platinums'] == 2


def test_the_session_span_is_worded_at_every_scale():
    """Untested until the audit said so: the five-minute floor, the one-decimal band and the whole-hours
    band above it, plus the ".0h" strip that stops a clean two-hour run reading "2.0h"."""
    from trophies.services.activity_service import _span_label

    assert _span_label(0) == '' and _span_label(4) == ''      # a moment is not a duration
    assert _span_label(45) == '45m'
    assert _span_label(120) == '2h', 'a clean two hours should not read 2.0h'
    assert _span_label(150) == '2.5h'
    assert _span_label(800) == '13h', 'past ten hours the decimal is noise'


def test_a_long_search_query_is_bounded(client):
    """`?q=` is public and live-searched on a debounce, so an unbounded value is a cheap way to hand the
    database an enormous double ILIKE on every keystroke."""
    profile = ProfileFactory(is_linked=True)
    _earn(profile, GameFactory(), _at(1))

    response = client.get(f'/hunters/{profile.psn_username}/?tab=trophies&q=' + 'x' * 4000, **CF)

    assert response.status_code == 200
    assert len(response.context['trophy_query']) <= 80


def test_the_day_modal_stacks_its_games():
    """A regression worth a guard, because it was caused by a CLEANUP.

    The modal's games were an `auto-fill` grid left over from when a session could be a collapsed poster,
    with every session spanning `1 / -1` -- a grid that could never produce more than one column. Deleting
    the span rule as dead code dropped the sessions into narrow columns where their trophy cards clipped.

    A stack says what the grid always meant, and cannot be broken by removing a rule somewhere else.
    """
    import re
    from pathlib import Path

    css = (Path(__file__).resolve().parents[2]
           / 'static' / 'css' / 'components' / 'profile-hero.css').read_text(encoding='utf-8')

    rule = re.search(r'(?m)^\.pp-actday__games\s*\{([^}]*)\}', css)
    assert rule, 'the modal has no layout rule for its games'
    body = rule.group(1)
    assert 'flex' in body and 'column' in body, 'the modal games are a grid again'
    assert 'grid-template-columns' not in body

    # And the rule that follows must still be its own -- the regex that removed the span left a dangling
    # selector which swallowed the next rule whole.
    assert re.search(r'(?m)^\.pp-actday__link\s*\{', css), 'the day link rule lost its selector'


def _session_markup():
    """The session partial with its comments stripped. Its prose NAMES the `<button>` and the `<a>` it
    explains, so any slice taken on the raw text matches the explanation rather than the markup."""
    import re
    from pathlib import Path

    tpl = (Path(__file__).resolve().parents[2]
           / 'templates/trophies/partials/profile_detail/_activity_session.html').read_text(encoding='utf-8')
    return re.sub(r'{%\s*comment\s*%}[\s\S]*?{%\s*endcomment\s*%}', '', tpl)


def test_a_day_still_links_to_its_games(client):
    """The regression the audit caught, and the worst kind: a whole route disappearing as a side effect.

    Making the session header one big <button> forced the title's link out -- an `<a>` inside a `<button>`
    is invalid -- so a day had no route to any game's detail page, on the surface that exists to be
    crawled. The title is the LINK and the chevron is the toggle: two controls, each named for its own job.
    """
    partial = _session_markup()

    assert 'game_detail_with_profile' in partial, 'a day no longer links to its games'
    # The link must not sit inside the toggle, which is the arrangement that removed it.
    toggle = partial[partial.index('<button'):partial.index('</button>')]
    assert '<a ' not in toggle and 'pp-act__title' not in toggle

    profile = ProfileFactory(is_linked=True)
    game = GameFactory()
    _earn(profile, game, _at(1))
    body = client.get(f'/hunters/{profile.psn_username}/day/{_at(1).date().isoformat()}/', **CF).content.decode()
    assert f'/games/{game.np_communication_id}/' in body


def test_the_toggle_is_named_for_the_game_not_the_whole_card():
    """A button wrapping the row took its accessible name from everything inside it -- "Elden Ring 12
    trophies over 2.5h 1 3 8 Hide the trophies". The toggle is its own control now, named for the game,
    with `aria-expanded` carrying the state so the NAME never has to describe it."""
    partial = _session_markup()
    toggle = partial[partial.index('<button'):partial.index('</button>')]

    assert 'aria-label="Trophies from' in toggle
    assert 'aria-expanded' in toggle and 'aria-controls' in toggle
    for leaked in ('pp-act__stats', 'pp-act__tiers', 'pp-act__cover'):
        assert leaked not in toggle, f'{leaked} is inside the button and joins its accessible name'


def test_the_collapse_uses_the_shared_panel_helper():
    """A hand-rolled tween got two things wrong that `PlatPursuit.animatePanel` gets right: `transitionend`
    BUBBLES, so it needs an `ev.target !== panel` guard rather than only a property check, and a second
    click mid-tween must clear the previous listener instead of stacking another.

    It also toggles the panel's `hidden` attribute -- which is what actually removes a collapsed panel from
    the accessibility tree. `height: 0; overflow: hidden` hides it from the eye only, so a screen reader
    would read every trophy while the control claimed `aria-expanded="false"`.
    """
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    js = (root / 'static' / 'js' / 'activity-collapse.js').read_text(encoding='utf-8')

    # Comments stripped: the prose NAMES the pitfalls the helper handles, so a raw check matches the
    # explanation rather than an actual re-implementation.
    import re
    code = re.sub(r'/\*[\s\S]*?\*/', '', js)

    assert 'PlatPursuit.animatePanel' in code, 'the tween is hand-rolled again'
    assert 'scrollHeight' not in code and 'transitionend' not in code, 'it is re-implementing the helper'
    # Both hosts must load it, or the affordance is missing on one of them.
    for tpl in ('profile_detail.html', 'activity_day.html'):
        assert 'activity-collapse.js' in (root / 'templates' / 'trophies' / tpl).read_text(encoding='utf-8')


def test_the_panel_can_collapse_to_a_true_zero():
    """`animatePanel`'s contract, and the reason it is documented: under border-box, a border or padding on
    the panel clamps its collapsed height and snaps away the instant `hidden` lands. The divider therefore
    lives on the inner wall, not on the panel."""
    import re
    from pathlib import Path

    css = (Path(__file__).resolve().parents[2]
           / 'static' / 'css' / 'components' / 'profile-hero.css').read_text(encoding='utf-8')

    panel = re.search(r'(?m)^\.pp-act__trophies\s*\{([^}]*)\}', css)
    assert panel, 'the panel lost its rule'
    body = panel.group(1)
    assert 'overflow: hidden' in body and 'transition' in body
    assert 'border' not in body and 'padding' not in body, (
        'a border or padding on the panel clamps its collapsed height'
    )
    assert '.pp-act__trophies[hidden]' in css, 'a collapsed panel is not removed from the a11y tree'


def test_no_session_rule_depends_on_is_open():
    """`.is-open` used to mean "row rather than poster" and carried the row's whole layout; when it came to
    mean "trophies showing", collapsing dropped that layout and the cover reverted to a full-width poster.
    The state lives on the panel's `hidden` attribute now, so the class should not survive at all."""
    import re
    from pathlib import Path

    css = (Path(__file__).resolve().parents[2]
           / 'static' / 'css' / 'components' / 'profile-hero.css').read_text(encoding='utf-8')
    css = re.sub(r'/\*[\s\S]*?\*/', '', css)      # the comments record why it went

    assert '.is-open' not in css, 'a session rule still keys off .is-open'


def test_every_search_card_cover_is_the_same_size():
    """Cards came out at slightly different heights depending on the GAME.

    The art box takes its height from `width` + `aspect-ratio`, and a percentage-sized child resolving
    against a derived height is indeterminate -- so the image fell back to its intrinsic size, which
    differs between an IGDB cover and a PSN fallback. Only the cards whose art happened to be a different
    shape came out wrong, which is what made it look random.

    Pinning the image to the box is the same fix the medallion cover needed, for the same reason. If it
    ever goes back to percentage sizing, uneven cards come back with it.
    """
    import re
    from pathlib import Path

    css = (Path(__file__).resolve().parents[2]
           / 'static' / 'css' / 'components' / 'profile-hero.css').read_text(encoding='utf-8')

    cover = re.search(r'(?m)^\.pp-actt__cover\s*\{([^}]*)\}', css)
    assert cover, 'the search card cover lost its rule'
    assert 'position: absolute' in cover.group(1) and 'inset: 0' in cover.group(1), (
        'the cover is percentage-sized again, so its size follows the source art rather than the box'
    )

    box = re.search(r'(?m)^\.pp-actt__art\s*\{([^}]*)\}', css)
    assert box and 'aspect-ratio: 3 / 4' in box.group(1), 'the box has no ratio to derive its height from'
    assert 'position: relative' in box.group(1), 'the pinned cover has nothing to pin to'


def test_several_trophy_types_can_be_filtered_at_once(client):
    """"Their golds and platinums" is one question. Asking it as two searches is what a filter exists to
    avoid, and radios made that impossible."""
    profile = ProfileFactory(is_linked=True)
    game = GameFactory()
    _earn(profile, game, _at(1), tier='platinum', name='The Plat')
    _earn(profile, game, _at(1), tier='gold', name='A Gold')
    _earn(profile, game, _at(1), tier='bronze', name='A Bronze')

    response = client.get(
        f'/hunters/{profile.psn_username}/?tab=trophies&tier=platinum&tier=gold', **CF
    )
    body = response.content.decode()

    assert response.context['trophy_selected'] == ['platinum', 'gold']
    assert 'The Plat' in body and 'A Gold' in body
    assert 'A Bronze' not in body


def test_an_unknown_tier_is_dropped_without_losing_the_rest(client):
    """A stale or hand-edited link still answers with whatever of it made sense, rather than rejecting the
    whole request or filtering on nonsense."""
    profile = ProfileFactory(is_linked=True)
    _earn(profile, GameFactory(), _at(1), tier='gold', name='A Gold')

    response = client.get(
        f'/hunters/{profile.psn_username}/?tab=trophies&tier=gold&tier=nonsense', **CF
    )

    assert response.context['trophy_selected'] == ['gold']
    assert 'A Gold' in response.content.decode()


def test_a_selected_chip_shows_as_selected(client):
    """The chips are real checkboxes, so `:has(input:checked)` reads the active state straight off the DOM
    -- no class to keep in sync, and toggling one off needs no handler."""
    profile = ProfileFactory(is_linked=True)
    _earn(profile, GameFactory(), _at(1), tier='gold')

    body = client.get(f'/hunters/{profile.psn_username}/?tab=trophies&tier=gold', **CF).content.decode()

    assert 'value="gold" checked' in body.replace('" checked', '" checked')
    assert 'value="bronze" checked' not in body


def test_clear_re_renders_the_toolbar_not_just_the_results():
    """The chips stayed lit after clearing. The toolbar lives OUTSIDE `#tab-results`, so clearing through
    the usual swap target updated the results and left every control describing the old ones.

    Clear targets the whole tab, which re-renders the form with nothing checked.
    """
    import re
    from pathlib import Path

    tab = (Path(__file__).resolve().parents[2]
           / 'templates/trophies/partials/profile_detail/tabs/trophies_tab.html').read_text(encoding='utf-8')
    tab = re.sub(r'{%\s*comment\s*%}[\s\S]*?{%\s*endcomment\s*%}', '', tab)

    clear = tab[tab.index('pp-tsearch__clear'):]
    clear = clear[:clear.index('>') + 1]
    assert 'hx-target="#tab-content"' in clear, 'clearing leaves the chips showing filters that are gone'

    # And the toolbar really is outside the results target, which is why that matters.
    assert tab.index('pp-tsearch') < tab.index('id="tab-results"')


def test_the_day_modal_can_be_swiped_closed():
    """It was the only sheet on the site without the gesture -- the badge peeks, the contract modal and the
    plat-card share sheet all have it, so a day was closable only by the X or the scrim.

    Wired inside the afterSwap handler rather than once at init, because `#act-day-modal` is HTMX-swapped
    per day: the dialog is a fresh node every time, and a single wiring would bind one that is already
    gone. The helper adds `.pp-dismissable`, which is what surfaces the grabber pill -- so a sheet can
    never advertise a gesture it does not honour."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    page = (root / 'templates' / 'trophies' / 'profile_detail.html').read_text(encoding='utf-8')

    swap = page[page.index("if (e.target.id !== 'act-day-modal')"):]
    swap = swap[:swap.index('panel.addEventListener(\'click\'')]
    assert 'PlatPursuit.dismissableSheet(dialog' in swap, 'the day modal has no swipe-to-close'
    assert "scrim: modal.querySelector('.pp-detail-modal__scrim')" in swap, 'the scrim will not fade'
    assert 'onClose: closeDay' in swap, 'the swipe does not run the same close the X does'

    # And the grabber needs somewhere to sit: the pill floats at the dialog's top, over a header whose own
    # padding is 4px. Touch-only, and keyed on `.pp-dismissable` so it cannot pad a sheet with no gesture.
    # NO grabber-clearance rule, deliberately. The pill occupies 9-13px and this dialog carries 28px of
    # its own padding (34px below 640px), so the header already starts clear of it. The sheets that DO
    # pad -- quick-rate, plat-card -- set `padding: 0` on their dialog and butt the head against its top
    # edge. A rule was added here on a false premise and cost 12px of dead space above the date.
    css = (root / 'static' / 'css' / 'components' / 'profile-hero.css').read_text(encoding='utf-8')
    assert '.pp-dismissable .pp-actday__head { padding-top' not in css, (
        'dead grabber padding is back -- this dialog already clears the pill'
    )


def _mobile_list_rules():
    """Every CSS rule inside the search-results mobile block, as (selectors, body) pairs.

    Parsed properly rather than scanned line-by-line. The first version of these tests looked for lines
    ENDING in `{`, which silently skipped every single-line rule -- and most of this block is single-line,
    so both guards passed against a deliberately broken stylesheet.
    """
    import re
    from pathlib import Path

    css = (Path(__file__).resolve().parents[2] / 'static' / 'css' / 'components'
           / 'profile-hero.css').read_text(encoding='utf-8')

    block = css[css.index('SEARCH RESULTS AS A LIST'):]
    block = block[:block.index('\n}\n', block.index('@media (max-width: 767px)')) + 3]

    inner = block[block.index('{', block.index('@media')) + 1: block.rindex('}')]
    inner = re.sub(r'/\*.*?\*/', '', inner, flags=re.S)
    return block, [
        ([s.strip() for s in sel.split(',')], body)
        for sel, body in re.findall(r'([^{}]+)\{([^{}]*)\}', inner)
    ]


def test_the_mobile_rule_parser_sees_the_whole_block():
    """Guards the guards below: if the slice or the regex resolves to nothing, both of them pass over an
    empty list while reporting that the stylesheet is fine."""
    block, rules = _mobile_list_rules()
    assert '@media (max-width: 767px)' in block, 'the list treatment is not gated to mobile'
    assert len(rules) >= 10, f'only {len(rules)} rules parsed out of the mobile block'


def test_the_trophy_search_results_condense_to_a_list_on_mobile_only():
    """The search-results card is a POSTER on a wide wall and a LIST ROW on a phone, and the switch is the
    whole point: three of its fields (name, game, description) reserve two lines whether or not they need
    them, which is what keeps a multi-column wall's rows even -- and at one column there is no neighbour to
    line up with, so the reserve buys nothing and costs ~230px per result.
    """
    _block, rules = _mobile_list_rules()

    # Iterated as a LIST, never collapsed into a {selector: body} dict. A selector legitimately appears in
    # more than one rule here -- `.pp-actt__name` is placed by one and released by another -- and keying by
    # selector silently drops all but the last, which reported the grid placements as missing when they
    # were there.

    # The wall is `auto-fill minmax(182px, 1fr)`, which still makes THREE columns at 640px -- rows squeezed
    # into 202px read worse than the posters they replace, so the single column is load-bearing.
    assert any('minmax(0, 1fr)' in body
               for sels, body in rules if any('pp-actt--log' in s for s in sels)), (
        'the log wall is not single-column on mobile, so the list rows share a row'
    )

    # `display: contents` promotes the meta's children into the body grid, so the tier can sit on the
    # name's line and the rate/date on the game's -- two rows of chrome instead of a centred footer.
    assert any('display: contents' in body for _s, body in rules), 'the meta row is not unwrapped'

    placed = set()
    for _sels, body in rules:
        if 'grid-area:' in body:
            placed.add(body.split('grid-area:')[1].split(';')[0].strip())
    wanted = {'name', 'desc', 'game', 'tier', 'rate', 'time'}
    assert wanted <= placed, f'unplaced parts in the list grid: {sorted(wanted - placed)}'


def test_all_three_two_line_reserves_are_released_together():
    """The reserves have to come off as a SET. Leaving any one of the three holds the row open to two lines
    of that field alone, which is both taller than the design and inconsistent between rows -- a trophy with
    a one-line description would sit shorter than its neighbour.

    Asserted against the rule that actually does the releasing, not against the names appearing SOMEWHERE in
    the block: they each also appear in a `grid-area` line, so a substring check passes with the reserve
    fully intact. That is the exact form of the vacuous assertion this file has been bitten by before.
    """
    _, rules = _mobile_list_rules()

    releasing = [sels for sels, body in rules if 'min-height: 0' in body and 'line-clamp: 1' in body]
    assert releasing, 'nothing releases the two-line reserves at all'

    released = {s for sels in releasing for s in sels}
    for part in ('__name', '__game', '__desc'):
        assert any(part in s for s in released), (
            f'.pp-actt{part} keeps its two-line reserve, so the list row stays open for it'
        )


def test_the_session_trophy_card_is_untouched_by_the_list_treatment():
    """The day-modal session card shares `_trophy_card.html` with the search results. It renders WITHOUT
    `show_game`, so it has no cover art and no `--game` class -- and every mobile rule is scoped to that
    class so the two cannot drift into each other.

    Asserted on the SCOPING rather than on a render, because the leak would arrive the day someone passes
    `show_game` from the session context, which no render today would catch.
    """
    from pathlib import Path

    _, rules = _mobile_list_rules()

    for sels, _body in rules:
        for sel in sels:
            if sel.startswith('.pp-actt--log'):
                continue          # the wall itself, which the session card does not live in
            assert 'pp-actt__card--game' in sel, (
                f'{sel!r} is unscoped, so it also reshapes the day-modal session card'
            )

    session = (Path(__file__).resolve().parents[2]
               / 'templates/trophies/partials/profile_detail/activity_trophies.html').read_text(encoding='utf-8')
    assert 'show_game' not in session, 'the session card now passes show_game, so the scoping above matters'
