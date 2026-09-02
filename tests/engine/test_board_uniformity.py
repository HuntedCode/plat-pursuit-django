"""Every board is the SAME board.

Three surfaces render a leaderboard -- Global Boards (`/leaderboards/`), badge detail's Ranks tab and job
detail's Ranks tab -- and the whole point of the rebuild was that they stop being three implementations
that merely resemble each other. They now share one shell partial, one row partial, one jump bar, one
window parser and one JS engine.

Uniformity is exactly the kind of property that decays silently: nothing BREAKS when one surface grows its
own row markup or forgets a data attribute, it just quietly drifts, and the drift is only visible to
somebody who opens two boards side by side. So it is asserted here, per surface, against the contract the
client actually depends on rather than against a screenshot.

FOUR surfaces now, not three. Game detail's board was the holdout: it ran the same engine but its own
row, its own chrome and its own controls, so it was the most featured board on the site and the one that
looked least like the others. It was reduced onto the shared row rather than the shared row being grown
to fit it -- the per-tier trophy dots, the completion bar and the speed board's second date are gone,
which is a real trade recorded in `test_game_leaderboard_view` rather than hidden here.

What it still has that the others do not: three board kinds, trophy-group scoping, a minibar, and a
`?at=` rank PREVIEW on its search field. Those are FEATURES, not drift.

The hunter SEARCH did spread -- that is what the jump bar's `extra_partial` slot was built for, and all
four boards now run `PlatPursuit.wireBoardSearch` against their own `?suggest=`.
"""

import datetime as dt
import pathlib

import pytest
from django.urls import reverse

from tests.engine.test_leaderboards_landing import _ranked
from tests.factories import (
    BadgeSeriesFactory, GameFactory, GroupBadgeFactory, PlatformGroupFactory, ProfileFactory,
    ProfileGameFactory,
)
from trophies.models import Game, Job, ProfileJobXP, SeriesBadgeStanding

pytestmark = pytest.mark.django_db


@pytest.fixture
def client(client):
    """Model traffic that arrived through Cloudflare.

    Game detail's board lives at `/games/<x>/<y>/`, the shape `CloudflareOriginGuardMiddleware` bounces
    when a request carries no CF-Ray header -- it protects the profile-scoped detail pages from scrapers
    that cached the origin IP. A real browser fetch for that panel comes from a page already served
    through the proxy, so it always has the header. Setting it here keeps the guard live for every other
    path rather than switching it off for the suite, and mirrors what `test_game_leaderboard_view` does
    for the same reason.
    """
    client.defaults['HTTP_CF_RAY'] = 'test-ray'
    return client


# --- fixtures ----------------------------------------------------------------------------------------

def _profile(name):
    return ProfileFactory(display_psn_username=name, is_linked=True)


def _series_board(slug='uniform', n=60):
    # Built from the factories, not from whatever `PlatformGroup` the migrations happened to seed. A
    # fixture that reaches for `.first()` is non-hermetic, which is a poor foundation for a file whose
    # entire premise is pinning a contract.
    series = BadgeSeriesFactory(series_slug=slug, name='Uniform')
    GroupBadgeFactory(series=series, platform_group=PlatformGroupFactory(key='uni'), is_live=True)
    for i in range(n):
        # `xp` is what the board ranks by -- `progress_bp` is the furthest-along EDITION's fraction and
        # stopped being the ordering key when the board moved to badge points. A fixture that sets only
        # the latter makes every row tie on the former, so the ordering falls through to the tiebreak and
        # any assertion about position is measuring the wrong thing.
        SeriesBadgeStanding.objects.create(
            profile=_profile(f'Hunter{i:02d}'), series_slug=slug, xp=1000 - i,
            progress_bp=9900 - i, advanced_at=dt.date(2025, 1, 1), is_linked=True)
    return series


def _job_board(slug='archivist', n=60):
    job = Job.objects.create(slug=slug, name='Archivist')
    for i in range(n):
        ProfileJobXP.objects.create(
            profile=_profile(f'Worker{i:02d}'), job=job, total_xp=1000 - i, level=2, is_linked=True)
    return job


def _global_board(n=60):
    # Reuses the landing suite's own fixture rather than re-deriving which counters the Trophies board
    # reads -- they live on Profile, not on a standing row, and guessing that wrong is silent.
    for i in range(n):
        _ranked(f'Global{i:02d}', plats=100 - i, trophies=1000 - i)


def _game_board(n=60):
    """A game board of verified hunters. The gate is not optional: this board used to rank every scraped
    PSN profile and now applies the same `is_linked` rule as the other three, so an unlinked fixture
    builds a board with nobody on it."""
    game = GameFactory()
    for i in range(n):
        ProfileGameFactory(game=game, profile=_profile(f'Player{i:02d}'), progress=100 - i,
                           most_recent_trophy_date=dt.datetime(2025, 1, 1, tzinfo=dt.timezone.utc)
                           + dt.timedelta(minutes=i))
    return game


#: (label, how to render the board, how to render one window past the first)
SURFACES = ['global', 'badge', 'job', 'game']


def _render(surface, client, fresh=True):
    """The BOARD-BEARING response for one surface, plus a window fetch from the same endpoint.

    `fresh=False` skips building the board, for callers that already built one (and put themselves on
    it) and would otherwise get a second 60 hunters stacked on top of the first.
    """
    if surface == 'global':
        if fresh:
            _global_board()
        return (client.get(reverse('overall_badge_leaderboards')),
                client.get(reverse('leaderboard_rows'), {'tab': 'trophies', 'range': 51}))
    if surface == 'badge':
        if fresh:
            _series_board()
        url = reverse('badge_ranks_panel', args=['uniform'])
        return client.get(url), client.get(url, {'range': 51})
    if surface == 'job':
        if fresh:
            _job_board()
        url = reverse('job_ranks_panel', args=['archivist'])
        return client.get(url), client.get(url, {'range': 51})
    game = _game_board() if fresh else Game.objects.get()
    url = reverse('game_leaderboard', args=[game.np_communication_id])
    return client.get(url), client.get(url, {'range': 51})


def _suggest_for(surface, q):
    """Where each board's typeahead lives. Three share the rows/panel endpoint; game detail has its own."""
    if surface == 'global':
        return reverse('leaderboard_rows'), {'tab': 'trophies', 'suggest': q}
    if surface == 'badge':
        return reverse('badge_ranks_panel', args=['uniform']), {'suggest': q}
    if surface == 'job':
        return reverse('job_ranks_panel', args=['archivist']), {'suggest': q}
    return (reverse('game_leaderboard', args=[Game.objects.get().np_communication_id]),
            {'suggest': q})


def _rank_me_on(surface):
    """A signed-in hunter placed MID-BOARD on one surface, so `my_rank` is a real number rather than 1 --
    a viewer who happens to be first would pass a chip test that a viewer at #31 would not, because the
    engine's own seeding renders rank 1 either way."""
    if surface == 'global':
        _global_board()
        return _ranked('Zqviewer', plats=70, trophies=700)
    if surface == 'badge':
        _series_board()
        me = _profile('Zqviewer')
        SeriesBadgeStanding.objects.create(
            profile=me, series_slug='uniform', xp=970, progress_bp=9870,
            advanced_at=dt.date(2025, 1, 1), is_linked=True)
        return me
    if surface == 'job':
        job = _job_board()
        me = _profile('Zqviewer')
        ProfileJobXP.objects.create(profile=me, job=job, total_xp=970, level=2, is_linked=True)
        return me
    game = _game_board()
    me = _profile('Zqviewer')
    ProfileGameFactory(game=game, profile=me, progress=70,
                       most_recent_trophy_date=dt.datetime(2025, 6, 1, tzinfo=dt.timezone.utc))
    # An UNVERIFIED profile ranked ahead, so the `is_linked` gate has something to remove. Without one,
    # a gate applied to the rows and not the rank (or the reverse) leaves both numbers agreeing and the
    # rank-vs-row assertion cannot fail.
    ProfileGameFactory(game=game, profile=ProfileFactory(is_linked=False), progress=99,
                       most_recent_trophy_date=dt.datetime(2025, 1, 1, tzinfo=dt.timezone.utc))
    return me


# --- the contract ------------------------------------------------------------------------------------

@pytest.mark.parametrize('surface', SURFACES)
def test_every_board_ships_the_shared_shell(surface, client):
    """The data attributes ARE the contract -- `wireBoard` reads every one of them and carries no
    constants of its own, precisely so a page size cannot disagree with the server that pages by it.

    A missing attribute does not raise. `data-lb-page-size` absent means the client falls back to 50 and
    fetches in a granularity the server does not serve, which shows up as GAPS in the rows a reader
    scrolls past -- so it has to be asserted rather than noticed.
    """
    panel, _ = _render(surface, client)
    body = panel.content.decode()

    # The RENDERED TAG, not the bare attribute name. `[data-lb-board]` also appears in the Global Boards
    # page's own script (it is the selector `mount()` queries), so a containment check on the attribute
    # alone passed with the entire board shell deleted. Same trap this suite already documents twice.
    assert '<div class="lb-board" data-lb-board' in body, f'{surface}: no board root'
    # NON-EMPTY VALUES, not just the attribute names. An attribute that renders as `data-lb-page-size=""`
    # satisfies a containment check and then makes `wireBoard` DECLINE TO MOUNT -- and an unmounted board
    # never sizes its spacer, so the rows sit absolutely-positioned in a zero-height list and the rest of
    # the page draws straight through them. Present-but-empty is the failure this shape has.
    import re as _re
    for attr in ('data-lb-total', 'data-lb-page-size', 'data-lb-rows-url'):
        m = _re.search(rf'{attr}="([^"]*)"', body)
        assert m, f'{surface}: the shell is missing {attr}'
        assert m.group(1).strip(), f'{surface}: {attr} rendered empty, so the board will not mount'
    assert 'data-lb-wall' in body, f'{surface}: the engine has nothing to mount on'
    # The wall ships as FLOW. `--virtual` absolutely positions every row and is only survivable once the
    # engine is reserving their space, so the engine adds it when it mounts -- shipping them together
    # meant any board that failed to mount rendered a zero-height pile with the page drawn through it.
    assert 'lb-wall--virtual' not in body, (
        f'{surface}: the wall ships pre-virtualized, so a board that does not mount collapses'
    )
    assert 'data-lb-total="60"' in body, f'{surface}: the spacer is sized to the window, not the board'


@pytest.mark.parametrize('surface', SURFACES)
def test_every_board_uses_the_shared_row(surface, client):
    """One row component, or the boards drift in the way a reader actually notices: different figure
    labels, a flag on one and not another, a supporter mark that only some boards show."""
    panel, _ = _render(surface, client)
    body = panel.content.decode()

    assert body.count('<li class="lb-row') > 0, f'{surface}: no shared rows'
    assert 'lb-row__rank' in body and 'lb-row__figs' in body, f'{surface}: not the shared row markup'
    # `data-lb-rank` is what the engine places rows BY -- a row without it is spliced in and positioned
    # nowhere, so it is invisible on a virtualized wall.
    assert 'data-lb-rank="1"' in body, f'{surface}: rows carry no rank for the virtualizer'


@pytest.mark.parametrize('surface', SURFACES)
def test_every_board_offers_the_rank_box(surface, client):
    """Half of the two ways in, and the half an anonymous visitor gets. The other half needs a viewer who
    is actually ON the board -- see the test below, which is separate precisely because this one used to
    claim to cover both and could not: every fixture here is signed out, so `{% if my_rank %}` never
    rendered and deleting the whole jump-chip block kept it green."""
    panel, _ = _render(surface, client)
    body = panel.content.decode()

    assert 'lb-jumpbar' in body, f'{surface}: no jump bar'
    assert 'data-lb-gotoform' in body, f'{surface}: no rank box'
    assert '-goto-input' in body, f'{surface}: the rank box has no labelled input'
    # The label IS the accessible name (no `aria-label`, deliberately -- it would win the name
    # computation and leave a voice-control user saying words that match nothing).
    assert 'Go to rank' in body, f'{surface}: the rank box has no visible label'


@pytest.mark.parametrize('surface', SURFACES)
def test_every_board_offers_jump_to_me_to_a_ranked_viewer(surface, client):
    """The other way in. On a board you cannot page through, this is how a hunter ranked #3,000 reaches
    their own row -- so a board that ships the rank box and not this one is not the same board with a
    smaller feature set, it is a board with a dead end for the person most likely to want it.

    Needs a SIGNED-IN viewer who is on the board, which is why it does not ride along with the test
    above: `my_rank` is None for everybody else and the chip does not render at all.
    """
    me = _rank_me_on(surface)
    client.force_login(me.user)
    panel, _ = _render(surface, client, fresh=False)
    body = panel.content.decode()

    assert 'data-lb-jump' in body, f'{surface}: a ranked viewer is offered no jump-to-me'
    assert 'You&#x27;re' in body or 'You&rsquo;re' in body, f'{surface}: the chip has no rank on it'
    # ...and the engine is told which row to light, or the chip scrolls to a row that looks like any
    # other. `data-lb-viewer-rank` is what carries it.
    assert 'data-lb-viewer-rank=""' not in body, f'{surface}: the viewer rank never reached the board'


@pytest.mark.parametrize('surface', SURFACES)
def test_every_window_is_bare_rows(surface, client):
    """`virtualBoard` splices what comes back straight into its spacer, so anything wrapping the rows is
    parsed and discarded -- and any CHROME in there would be spliced into the middle of the wall."""
    _, window = _render(surface, client)
    body = window.content.decode()

    assert body.count('<li class="lb-row') == 10, f'{surface}: the window is not the rows asked for'
    assert '<ol' not in body, f'{surface}: the window carried a list wrapper'
    assert 'lb-jumpbar' not in body and 'lb-boardcard' not in body, f'{surface}: the window carried chrome'
    assert 'data-lb-rank="51"' in body, f'{surface}: the window restarted its numbering'


@pytest.mark.parametrize('surface', SURFACES)
def test_the_engine_is_what_virtualizes_the_wall(surface, client):
    """The promotion has to come from the engine, or it is a promise the markup cannot keep.

    `lb-wall--virtual` makes every row `position: absolute`; the height that reserves their space is set
    by `virtualBoard` at mount. Rendered together, a board that never mounts -- JS off, a failed panel
    fetch, a missing `data-lb-page-size`, a cached older utils.js -- is a zero-height list of stacked
    rows with the footer drawn straight through it. Rendered apart, all of those degrade to a plain list
    of the first window, which is what the partial always claimed a no-JS read would get.
    """
    panel, _ = _render(surface, client)
    assert 'lb-wall--virtual' not in panel.content.decode()
    # ...and the engine is the one that adds it.
    js = pathlib.Path('static/js/utils.js').read_text(encoding='utf-8')
    assert "list.classList.add('lb-wall--virtual')" in js


@pytest.mark.parametrize('surface', SURFACES)
def test_no_board_hides_its_rows_behind_a_reveal(surface, client):
    """`staggerReveal` adds `.pp-reveal` to a container permanently and `.pp-reveal .lb-row` is
    `opacity: 0` until an IntersectionObserver grants `.is-revealed`. A virtualized wall mounts and
    evicts rows by scroll position, so they never reach that observer and arrive INVISIBLE.

    This collision shipped twice -- badge detail's "show more" and then the Global Boards wall -- which
    is why it is pinned on every surface rather than on the one that broke last.
    """
    panel, window = _render(surface, client)
    for label, resp in (('panel', panel), ('window', window)):
        assert 'pp-reveal' not in resp.content.decode(), f'{surface} {label}: a reveal is back on the wall'


@pytest.mark.parametrize('surface', SURFACES)
def test_every_board_puts_its_chrome_on_a_surface(surface, client):
    """STACKED CHROME CARDS + FREE CONTENT, the site-wide rule: chrome is carded, and the content -- a
    grid or a list -- flows free below it, never inside an outer card.

    This is the half the first propagation missed. Badge and job detail got the virtualized wall, the
    shared row and the jump bar, and then put all of it bare on the page background while the landing sat
    on a card -- so the boards behaved identically and did not look like the same product, which is the
    whole reason the work was done.
    """
    panel, _ = _render(surface, client)
    body = panel.content.decode()

    assert 'lb-controls' in body, f'{surface}: the board chrome is not on a surface'
    chrome = body[body.index('lb-controls'):body.index('<div class="lb-board"')]
    assert 'lb-boardcard' in chrome, f'{surface}: the board identity is outside the card'
    assert 'lb-jumpbar' in chrome, f'{surface}: the jump bar is outside the card'

    # ...and the WALL is not inside it. A card around an infinite list is a border that grows forever.
    assert body.index('<ol class="lb-wall') > body.index('</section>', body.index('lb-controls')), (
        f'{surface}: the wall is inside the chrome card'
    )


@pytest.mark.parametrize('surface', SURFACES)
def test_every_board_says_what_it_is_and_how_big_it_is(surface, client):
    """The board card, in full: a NAME, a one-line meaning, and a counting tally. Job detail had none of
    this; badge detail had a bare count line and its description one band up, above a panel that had not
    loaded yet."""
    panel, _ = _render(surface, client)
    body = panel.content.decode()

    assert 'lb-boardcard__name' in body, f'{surface}: the board does not say which board it is'
    assert 'lb-boardcard__what' in body, f'{surface}: the board does not say what it ranks'
    # The Tally, with the countUp hook `boardEntrance` reads -- the figure ticks on every board now, not
    # just the one whose page happened to wire it.
    assert 'lb-boardcard__tally' in body, f'{surface}: the board does not say how big it is'
    assert 'data-countup="60"' in body, f'{surface}: the tally does not count the board'


@pytest.mark.parametrize('surface', SURFACES)
def test_the_viewer_rank_points_at_the_viewers_own_row(surface, client):
    """The "this one is you" highlight is applied in the BROWSER: the engine reads `data-lb-viewer-rank`
    off the board root and tags the row whose `data-lb-rank` matches.

    So the two numbers must come from one ordering -- and they are produced by DIFFERENT code. The rows
    are numbered by SLOT (`page()` counts `offset + i + 1`); the viewer's rank is computed by COUNTING
    everyone ahead of them. They agree only while the ordering is total AND both reads share one
    population, so any filter applied to one and not the other puts the highlight on a stranger's row --
    silently, because both numbers still look entirely reasonable on their own.

    Asserted per surface because each board computes its rank with its own function, and they have
    diverged before: the badge board's ordering moved from `progress_bp` to `xp`, and the game board
    gained an `is_linked` gate and a country slice, each of which is a chance for the row numbering and
    the rank read to stop describing the same list.
    """
    import re

    me = _rank_me_on(surface)
    client.force_login(me.user)
    panel, _ = _render(surface, client, fresh=False)
    body = panel.content.decode()

    viewer = re.search(r'data-lb-viewer-rank="(\d+)"', body)
    assert viewer, f'{surface}: the engine is never told which row is the viewer'

    # Scoped to the WALL. A full page carries nav, headings and empty-state copy, so a bare substring
    # search for a username can match chrome long before it reaches a row -- and the fixture name is
    # deliberately unlike any word the site uses for the same reason.
    wall = body[body.index('data-lb-wall'):]
    name = me.display_psn_username or me.psn_username
    assert name in wall, f'{surface}: the viewer is not in the first window, so nothing can be checked'
    li = wall.rindex('<li class="lb-row', 0, wall.index(name))
    body = wall
    row = re.search(r'data-lb-rank="(\d+)"', body[li:li + 500])
    assert row, f'{surface}: the viewer row carries no rank for the engine to match'
    assert row.group(1) == viewer.group(1), (
        f'{surface}: the highlight would land on rank {viewer.group(1)} but the viewer is row '
        f'{row.group(1)} -- the numbering and the rank read different populations'
    )


@pytest.mark.parametrize('surface', SURFACES)
def test_every_board_offers_the_hunter_search(surface, client):
    """The third way in, and the last thing game detail had alone.

    It reaches the page through a template-path SLOT (`extra_partial`), which is a silent failure mode:
    drop the kwarg or typo the path and the field simply is not there, with no error and nothing raised.
    The `?suggest=` endpoints stay covered by their own tests, so without this the suite would be fully
    green with the only UI that calls them gone.
    """
    panel, _ = _render(surface, client)
    body = panel.content.decode()

    assert 'data-lb-findform' in body, f'{surface}: the hunter search did not reach the board'
    assert 'data-lb-find' in body and 'data-lb-suggest' in body
    # ...and it sits INSIDE the jump bar, between the two other ways in, rather than beside the cluster.
    bar = body[body.index('lb-jumpbar'):body.index('</div>', body.index('lb-goto'))]
    assert 'data-lb-find' in bar, f'{surface}: the search is not in the jump bar slot'


@pytest.mark.parametrize('surface', SURFACES)
def test_every_board_search_is_scoped_and_ranked(surface, client):
    """A suggestion carries the rank you would jump to ON THIS BOARD. That is the whole reason a board
    search exists next to a navbar that already finds any hunter anywhere -- and it means the search has
    to read the same population the rows do."""
    me = _rank_me_on(surface)
    name = me.display_psn_username or me.psn_username

    url, params = _suggest_for(surface, name[:4])
    data = client.get(url, params).json()

    # Compared on `display`, which is the VISIBLE name in both shapes. `username` is not uniform: game
    # detail returns the canonical `psn_username`, while the shared serializer returns what the shared ROW
    # renders (`display_psn_username or psn_username`) so the suggestion and the row agree with each other.
    hit = [p for p in data['players'] if p['display'] == name]
    assert hit, f'{surface}: the search did not find a hunter who is on the board'
    assert hit[0]['rank'] >= 1, f'{surface}: the suggestion carries no rank to jump to'


#: The PAGE each board is mounted from. The panels above are fragments; the script that mounts them lives
#: on the page that fetches them, and a fragment test cannot see it.
PAGES = ['global', 'badge', 'job', 'game']


def _page_url(surface):
    if surface == 'global':
        _global_board(n=3)
        return reverse('overall_badge_leaderboards')
    if surface == 'badge':
        _series_board(n=3)
        return reverse('badge_detail', args=['uniform'])
    if surface == 'job':
        _job_board(n=3)
        return reverse('job_detail', args=['archivist'])
    return reverse('game_detail', args=[_game_board(n=3).np_communication_id])


@pytest.mark.parametrize('surface', PAGES)
def test_every_board_page_actually_ships_its_mount(surface, client):
    """The board is inert markup until something calls `wireBoard` on it, and the failure mode is silent:
    the wall renders its first window, so the page LOOKS right, and scrolling simply never loads row 51.

    This is pinned because it has already happened in a worse form. Job detail's switcher was written
    into `{% block extra_js %}` -- a block `base.html` does not declare -- and Django discards an
    undeclared child block with no error and no warning, so the entire script never reached the browser
    and the board was unreachable. The suite stayed green because nothing asserted the script SHIPS.

    Asserted as the CALL (`wireBoard(root,`) rather than as the bare name. Every one of these pages also
    carries an `if (!PlatPursuit.wireBoard) return;` capability guard, so a test looking for the name
    alone passes on the guard and goes vacuously green the moment the call itself is deleted -- which is
    exactly what a mutation run showed it doing.
    """
    body = client.get(_page_url(surface)).content.decode()

    # Three pages inline their mount; game detail's lives in `game-detail.js`, so the guard follows it
    # there rather than being weakened to "some script is loaded". Both halves still have to hold: the
    # page must SHIP the file, and the file must contain the call.
    if surface == 'game':
        assert 'js/game-detail.js' in body, 'the page does not load the script that mounts its board'
        js = pathlib.Path('static/js/game-detail.js').read_text(encoding='utf-8')
        assert 'PlatPursuit.wireBoard(root,' in js, f'{surface}: the script never mounts the board'
    else:
        assert 'PlatPursuit.wireBoard(root,' in body, f'{surface}: the page never mounts its board'
