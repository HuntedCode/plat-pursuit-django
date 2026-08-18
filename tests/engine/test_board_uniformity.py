"""Every board is the SAME board.

Three surfaces render a leaderboard -- Global Boards (`/leaderboards/`), badge detail's Ranks tab and job
detail's Ranks tab -- and the whole point of the rebuild was that they stop being three implementations
that merely resemble each other. They now share one shell partial, one row partial, one jump bar, one
window parser and one JS engine.

Uniformity is exactly the kind of property that decays silently: nothing BREAKS when one surface grows its
own row markup or forgets a data attribute, it just quietly drifts, and the drift is only visible to
somebody who opens two boards side by side. So it is asserted here, per surface, against the contract the
client actually depends on rather than against a screenshot.

Deliberately NOT covered: game detail's board. It runs the same engine (`PlatPursuit.virtualBoard`) but
its own row component, because its rows carry per-tier trophy counts, a completion bar and three
board-kind variants (progress / speed / playtime) that the shared row has no slot for. Folding it in
would mean growing the shared row four ways to serve one caller, which is the drift this file guards
against wearing a different hat.
"""

import datetime as dt

import pytest
from django.urls import reverse

from tests.engine.test_leaderboards_landing import _ranked
from tests.factories import (
    BadgeSeriesFactory, GroupBadgeFactory, PlatformGroupFactory, ProfileFactory,
)
from trophies.models import Job, ProfileJobXP, SeriesBadgeStanding

pytestmark = pytest.mark.django_db


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
        SeriesBadgeStanding.objects.create(
            profile=_profile(f'Hunter{i:02d}'), series_slug=slug,
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


#: (label, how to render the board, how to render one window past the first)
SURFACES = ['global', 'badge', 'job']


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
    if fresh:
        _job_board()
    url = reverse('job_ranks_panel', args=['archivist'])
    return client.get(url), client.get(url, {'range': 51})


def _rank_me_on(surface):
    """A signed-in hunter placed MID-BOARD on one surface, so `my_rank` is a real number rather than 1 --
    a viewer who happens to be first would pass a chip test that a viewer at #31 would not, because the
    engine's own seeding renders rank 1 either way."""
    if surface == 'global':
        _global_board()
        return _ranked('Me', plats=70, trophies=700)
    if surface == 'badge':
        _series_board()
        me = _profile('Me')
        SeriesBadgeStanding.objects.create(
            profile=me, series_slug='uniform', progress_bp=9870,
            advanced_at=dt.date(2025, 1, 1), is_linked=True)
        return me
    job = _job_board()
    me = _profile('Me')
    ProfileJobXP.objects.create(profile=me, job=job, total_xp=970, level=2, is_linked=True)
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
    for attr in ('data-lb-total=', 'data-lb-page-size=', 'data-lb-rows-url='):
        assert attr in body, f'{surface}: the shell is missing {attr}'
    assert 'lb-wall lb-wall--virtual' in body, f'{surface}: the wall is not virtualized'
    assert 'data-lb-wall' in body, f'{surface}: the engine has nothing to mount on'
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
    assert 'lb-jumpbar' not in body and 'lb-colhead' not in body, f'{surface}: the window carried chrome'
    assert 'data-lb-rank="51"' in body, f'{surface}: the window restarted its numbering'


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


#: The PAGE each board is mounted from. The panels above are fragments; the script that mounts them lives
#: on the page that fetches them, and a fragment test cannot see it.
PAGES = ['global', 'badge', 'job']


def _page_url(surface):
    if surface == 'global':
        _global_board(n=3)
        return reverse('overall_badge_leaderboards')
    if surface == 'badge':
        _series_board(n=3)
        return reverse('badge_detail', args=['uniform'])
    _job_board(n=3)
    return reverse('job_detail', args=['archivist'])


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
    assert 'PlatPursuit.wireBoard(root,' in body, f'{surface}: the page never mounts its board'
