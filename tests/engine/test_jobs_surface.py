"""`/jobs/` (the public catalogue) + `/jobs/<slug>/` (what a job is, its contracts, its board).

Jobs get a PUBLIC catalogue in the Browse hub, not under Leaderboards: a catalogue of jobs is a browse
surface. Its relationship to Career's Dossier is the split this codebase already settled for Collection vs
Browse Badges -- "SCOPE, not pagination". Career shows YOUR standing across the 24 jobs; this shows what
the jobs are.

The load-bearing constraint is that it works SIGNED OUT. A job page an anonymous visitor cannot read
defeats the discovery it exists for, and the board being identical for everyone is what keeps it
cacheable.
"""
import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from trophies.models import Contract, EarnedContract, Job, ProfileJobXP
from tests.factories import ProfileFactory

pytestmark = pytest.mark.django_db


def _job(slug, name, discipline='combat', **kw):
    return Job.objects.create(slug=slug, name=name, discipline=discipline, **kw)


def _xp(job, name, xp, level=1):
    """Give a hunter XP in a job. Counts are read LIVE off `ProfileJobXP` -- the `Job.entrants` denorm
    went with the Job Boards directory, which was the only thing that needed a count it could gate and
    sort on before pagination."""
    p = ProfileFactory(display_psn_username=name)
    ProfileJobXP.objects.create(profile=p, job=job, total_xp=xp, level=level, is_linked=True)
    return p


_IGDB = [9000]


def _contract(name, jobs, *, live=True):
    _IGDB[0] += 1
    c = Contract.objects.create(
        name=name, slug=name.lower().replace(' ', '-'), igdb_id=_IGDB[0], is_live=live)
    c.jobs.set(jobs)
    return c


# ------------------------------------------------------------------ the catalogue ------------------------

def test_the_jobs_catalogue_is_public(client):
    """An anonymous visitor is exactly who this page is for -- it is the readable surface of a system they
    have not signed up for yet."""
    _job('archivist', 'Archivist')
    resp = client.get(reverse('jobs_browse'))
    assert resp.status_code == 200 and 'Archivist' in resp.content.decode()


def test_the_catalogue_counts_are_grouped_not_per_card(client):
    """Two figures per card (hunters, contracts) from grouped reads done once for the page. A count per
    card is the shape that looks fine at 24 jobs and is wrong on principle."""
    jobs = [_job(f'j{i}', f'Job {i}') for i in range(3)]
    for j in jobs:
        _xp(j, f'H{j.slug}', 100)
        _contract(f'C {j.slug}', [j])
    with CaptureQueriesContext(connection) as small:
        client.get(reverse('jobs_browse'))

    more = [_job(f'k{i}', f'Job K{i}') for i in range(12)]
    for j in more:
        _xp(j, f'H{j.slug}', 100)
        _contract(f'C {j.slug}', [j])
    with CaptureQueriesContext(connection) as large:
        client.get(reverse('jobs_browse'))

    assert len(large.captured_queries) == len(small.captured_queries), (
        f'{len(small.captured_queries)} queries for 3 jobs but {len(large.captured_queries)} for 15'
    )


# ------------------------------------------------------------------ job detail ---------------------------

def test_job_detail_is_public_and_both_tabs_render_signed_out(client):
    """The anon/authed split is PER TAB, not per page. Ranks is identical for everyone; Contracts shows
    the games and what they pay without a viewer's state."""
    job = _job('archivist', 'Archivist')
    _xp(job, 'TopHunter', 900, level=9)
    _contract('Some Contract', [job])

    ranks = client.get(reverse('job_ranks_panel', args=['archivist'])).content.decode()
    assert 'TopHunter' in ranks, 'the board is not visible signed out'

    contracts = client.get(reverse('job_detail', args=['archivist']), {'tab': 'contracts'}).content.decode()
    assert 'Some Contract' in contracts
    assert 'Ready to claim' not in contracts, 'viewer state leaked into the anonymous view'


def test_a_linked_viewer_sees_their_own_state_on_each_contract(client):
    job = _job('archivist', 'Archivist')
    contract = _contract('Mine', [job])
    profile = ProfileFactory(is_linked=True)
    EarnedContract.objects.create(profile=profile, contract=contract)
    client.force_login(profile.user)

    body = client.get(reverse('job_detail', args=['archivist']), {'tab': 'contracts'}).content.decode()
    assert 'In progress' in body or 'Ready to claim' in body or 'Banked' in body


def test_the_contract_state_lookup_is_batched_for_the_page(client):
    """The trap this page's own comment names: a lookup per row looks fine at 24 contracts and is not at
    200, and paging bounds the rows rendered but never the queries per row."""
    job = _job('archivist', 'Archivist')
    profile = ProfileFactory(is_linked=True)
    client.force_login(profile.user)

    for i in range(2):
        EarnedContract.objects.create(profile=profile, contract=_contract(f'Few {i}', [job]))
    with CaptureQueriesContext(connection) as small:
        client.get(reverse('job_detail', args=['archivist']), {'tab': 'contracts'})

    for i in range(12):
        EarnedContract.objects.create(profile=profile, contract=_contract(f'Many {i}', [job]))
    with CaptureQueriesContext(connection) as large:
        client.get(reverse('job_detail', args=['archivist']), {'tab': 'contracts'})

    assert len(large.captured_queries) == len(small.captured_queries), (
        f'{len(small.captured_queries)} queries for 2 contracts but {len(large.captured_queries)} for 14'
    )


def test_the_job_board_ranks_by_xp(client):
    job = _job('archivist', 'Archivist')
    _xp(job, 'Lower', 100)
    _xp(job, 'Higher', 900)
    body = client.get(reverse('job_ranks_panel', args=['archivist'])).content.decode()
    assert body.index('Higher') < body.index('Lower')


def test_a_job_with_no_xp_says_so_rather_than_rendering_an_empty_wall(client):
    _job('quiet', 'Quiet Job')
    body = client.get(reverse('job_ranks_panel', args=['quiet'])).content.decode()
    assert 'Nobody has banked XP here yet' in body


def test_the_page_never_builds_the_board(client):
    """The tabs switch IN PLACE now, so both panels are on the page -- which would normally mean paying
    for both. It does not, because only the CHEAP one is server-rendered: the board is fetched from
    `job_ranks_panel` when its tab is opened.

    That preserves a saving the `?tab=` version got for free from a full page reload, and it is the same
    trade game detail makes with its leaderboard panel.
    """
    job = _job('archivist', 'Archivist')
    _xp(job, 'Someone', 500)
    for i in range(3):
        _contract(f'Con {i}', [job])

    with CaptureQueriesContext(connection) as page:
        client.get(reverse('job_detail', args=['archivist']))
    sql = ' '.join(q['sql'] for q in page.captured_queries)

    # The header tally and the viewer's rank ARE on the page -- they sit above the switcher and show on
    # both tabs -- so ProfileJobXP is expected. What must NOT be there is the board's own row read.
    from trophies.views.career_views import JobDetailView
    # Derived from BOARD_SIZE rather than hardcoded: at 25 the look-ahead makes the board read `LIMIT 26`,
    # and a literal would go vacuously true (never matching, always passing) the day BOARD_SIZE changed.
    board_read = f'LIMIT {JobDetailView.BOARD_SIZE + 1}'

    assert 'trophies_profilejobxp' in sql, 'the header hunter tally is missing'
    assert board_read not in sql, 'the page built the board rows despite shipping an empty Ranks panel'

    # And the panel, asked for directly, does build them.
    with CaptureQueriesContext(connection) as panel:
        client.get(reverse('job_ranks_panel', args=['archivist']))
    assert board_read in ' '.join(q['sql'] for q in panel.captured_queries)

def test_a_signed_in_hunter_with_no_XP_is_TOLD_they_are_not_ranked(client):
    """`{% if my_rank %}` alone made the standing block simply absent for an unranked hunter, which reads
    as a missing feature rather than as the answer to the question they opened the tab with."""
    job = _job('archivist', 'Archivist')
    _xp(job, 'Someone', 500)
    viewer = ProfileFactory(display_psn_username='Newcomer')
    client.force_login(viewer.user)

    body = client.get(reverse('job_detail', args=['archivist']), {'tab': 'ranks'}).content.decode()
    assert 'Your standing' in body and 'Not ranked yet' in body

    # ...and a hunter who IS ranked gets the number, not the placeholder.
    ProfileJobXP.objects.create(profile=viewer, job=job, total_xp=900, level=2, is_linked=True)
    body = client.get(reverse('job_detail', args=['archivist']), {'tab': 'ranks'}).content.decode()
    assert '#1' in body and 'Not ranked yet' not in body


def test_a_signed_OUT_visitor_gets_no_standing_block_at_all(client):
    """The distinction the flag exists for: nothing to say, rather than "not ranked"."""
    job = _job('archivist', 'Archivist')
    _xp(job, 'Someone', 500)
    body = client.get(reverse('job_detail', args=['archivist']), {'tab': 'ranks'}).content.decode()
    assert 'Your standing' not in body and 'Not ranked yet' not in body


def test_the_board_pages_past_the_first_slice(client):
    """A job board is the only per-entity board with no fuller surface to hand off to, so capping at 25
    left a hunter ranked #26 told their rank in the header and permanently unable to reach the row."""
    job = _job('archivist', 'Archivist')
    for i in range(27):
        _xp(job, f'Hunter{i:02d}', 1000 - i)      # descending, so rank order == creation order

    first = client.get(reverse('job_ranks_panel', args=['archivist']))
    assert len(first.context['board']) == 25
    assert first.context['board_has_next'] is True
    assert first.context['board_has_prev'] is False
    assert first.context['board'][0]['rank'] == 1

    second = client.get(reverse('job_ranks_panel', args=['archivist']), {'page': 2})
    assert len(second.context['board']) == 2
    assert second.context['board_has_next'] is False
    assert second.context['board_has_prev'] is True
    # `page()` numbers by SLOT, so page 2 must continue the count rather than restart it.
    assert second.context['board'][0]['rank'] == 26, 'page 2 restarted the ranks at #1'
    body = second.content.decode()
    assert 'Hunter25' in body and 'Hunter00' not in body


def test_a_junk_page_number_falls_back_to_the_first_page(client):
    job = _job('archivist', 'Archivist')
    _xp(job, 'Someone', 500)
    for raw in ('0', '-3', 'abc', ''):
        resp = client.get(reverse('job_ranks_panel', args=['archivist']), {'page': raw})
        assert resp.status_code == 200
        assert resp.context['board_page'] == 1, f'page={raw!r} did not fall back'


def test_the_standing_chip_is_on_BOTH_tabs(client):
    """It lives in the page header, ABOVE the tab switcher, so it is chrome rather than tab content.

    Computing it inside the Ranks branch made it vanish from `?tab=contracts` -- which is the default, and
    therefore where a signed-in hunter lands. The chip only appeared once you clicked through to Ranks,
    the one tab that already shows you the board.
    """
    job = _job('archivist', 'Archivist')
    viewer = ProfileFactory(display_psn_username='Ranked')
    ProfileJobXP.objects.create(profile=viewer, job=job, total_xp=900, level=2, is_linked=True)
    client.force_login(viewer.user)

    for tab in ('contracts', 'ranks'):
        body = client.get(reverse('job_detail', args=['archivist']), {'tab': tab}).content.decode()
        assert 'Your standing' in body, f'the standing chip is missing on ?tab={tab}'
        assert '#1' in body, f'the rank is missing on ?tab={tab}'


def test_an_unverified_account_is_not_promised_a_board_it_cannot_enter(client):
    """Every board population is gated on `is_linked` (`badge_leaderboards._linked`), so telling an
    unlinked account "Not ranked yet" offers a board that will never have them on it. Game detail already
    resolved its viewer this way; the other two panels said "signed in" and meant it."""
    _job('archivist', 'Archivist')
    unlinked = ProfileFactory(is_linked=False, display_psn_username='Unverified')
    client.force_login(unlinked.user)

    body = client.get(reverse('job_detail', args=['archivist']), {'tab': 'ranks'}).content.decode()
    assert 'Your standing' not in body and 'Not ranked yet' not in body


def test_a_board_of_exactly_one_page_does_not_offer_a_next(client):
    """`len(rows) == BOARD_SIZE` is wrong at exact multiples of the page size: a 25-entrant board offered
    "Next", and page 2 had no rows, so it rendered "Nobody has banked XP here yet" -- with no pager on it,
    because the pager lives inside `{% if board %}`. Browser Back was the only way out of a board that was
    not empty at all. A look-ahead row answers it without a COUNT.
    """
    job = _job('archivist', 'Archivist')
    for i in range(25):                                  # EXACTLY one page
        _xp(job, f'Hunter{i:02d}', 1000 - i)

    first = client.get(reverse('job_ranks_panel', args=['archivist']))
    assert len(first.context['board']) == 25
    assert first.context['board_has_next'] is False, 'a full first page offered a next page that is empty'

    # ...and one more entrant flips it, so the look-ahead is actually being read.
    _xp(job, 'Hunter25', 500)
    again = client.get(reverse('job_ranks_panel', args=['archivist']))
    assert again.context['board_has_next'] is True
    assert len(again.context['board']) == 25, 'the look-ahead row leaked into the rendered page'


def test_a_huge_page_number_is_clamped_rather_than_scanned(client):
    """`?page=99999999` is a public URL that becomes a nine-figure OFFSET, and Postgres walks every
    skipped row to honour it. The page renders empty either way, so the cap costs a reader nothing."""
    from trophies.views.career_views import JobDetailView

    job = _job('archivist', 'Archivist')
    _xp(job, 'Someone', 500)

    resp = client.get(reverse('job_ranks_panel', args=['archivist']), {'page': 99999999})

    assert resp.status_code == 200
    assert resp.context['board_page'] == JobDetailView.MAX_PAGE


def test_the_tabs_are_a_real_tablist_over_real_panels(client):
    """Converted from `?tab=` links to in-place switching, so all three detail pages (game, badge, job)
    use one mechanism. The ARIA follows the behaviour rather than the other way round: it was a `nav` of
    links precisely BECAUSE nothing switched in place, and now something does."""
    _job('archivist', 'Archivist')

    body = client.get(reverse('job_detail', args=['archivist'])).content.decode()

    assert 'id="jobd-switch"' in body and 'role="tablist"' in body
    assert 'id="jobd-view-contracts"' in body and 'id="jobd-view-ranks"' in body
    assert 'data-view="ranks"' in body and 'data-ranks-src' in body
    # Contracts open, Ranks empty-and-hidden until asked for.
    assert '<li class="lb-row' not in body, 'the board was server-rendered into the page'
    # Scoped to the switcher: `aria-current="page"` is legitimately on the breadcrumb and the hub rail.
    strip = body[body.index('id="jobd-switch"'):body.index('</div>', body.index('id="jobd-switch"'))]
    assert 'aria-current' not in strip, 'the old link semantics survived the conversion'
    # They ARE still links -- deliberately, as the no-JS fallback (see the test below). What changed is
    # that they carry tab semantics and are intercepted, rather than being a nav of plain page loads.
    assert 'role="tab"' in strip and 'aria-selected' in strip


def test_an_old_tab_ranks_bookmark_still_opens_the_board(client):
    """`?tab=` was the switching mechanism and is now only an entry point. It has to keep working: it is
    what old bookmarks carry and what the board's own pager links use."""
    job = _job('archivist', 'Archivist')
    _xp(job, 'Someone', 500)

    body = client.get(reverse('job_detail', args=['archivist']), {'tab': 'ranks'}).content.decode()

    # Asserted on the TAB, not by scanning the panel for `hidden` -- the panel's own spinner carries
    # `aria-hidden` and a substring check would match it.
    tab = body[body.index('id="jobd-tab-ranks"'):]
    tab = tab[:tab.index('>')]
    assert 'aria-selected="true"' in tab, 'arriving on ?tab=ranks did not select the Ranks tab'

    # ...and the Contracts panel is the one closed instead.
    panel = body[body.index('id="jobd-view-contracts"'):]
    panel = panel[:panel.index('>')]
    assert 'hidden' in panel, 'both panels are open on ?tab=ranks'


def test_the_tab_script_actually_REACHES_the_browser(client):
    """The regression this file could not see.

    The switcher was written into `{% block extra_js %}`. `base.html` declares no such block, and Django
    DISCARDS a child block the parent never declared -- silently, with no error and no warning. So the
    entire script was dropped from the response: the chips had no handler, `?tab=ranks` showed a spinner
    that never resolved, and the job board was unreachable. Every test in this file stayed green, because
    every one of them asserted markup or hit the panel endpoint directly.

    Asserting a distinctive symbol from inside the block, so the block NAME being wrong fails here.
    """
    _job('archivist', 'Archivist')
    body = client.get(reverse('job_detail', args=['archivist'])).content.decode()

    assert 'jobd-switch' in body, 'the switcher markup is missing'
    assert "getElementById('jobd-switch')" in body, (
        'the tab script did not render -- check the block name against the ones base.html declares'
    )
    assert 'wireTablist' in body, 'the switcher is not wired to the shared tablist helper'


def test_the_tabs_still_work_without_javascript(client):
    """They were converted to `<button>`, which left a no-JS reader unable to reach EITHER tab -- the
    `?tab=` version had a server-side fallback for free. They are links again, intercepted by JS."""
    job = _job('archivist', 'Archivist')
    _xp(job, 'Someone', 500)

    body = client.get(reverse('job_detail', args=['archivist'])).content.decode()
    strip = body[body.index('id="jobd-switch"'):body.index('</div>', body.index('id="jobd-switch"'))]

    assert 'href="?tab=ranks"' in strip and 'href="?tab=contracts"' in strip, (
        'the tabs have no href -- without JS neither tab is reachable'
    )
    # ...and the server still honours it, which is what makes the href a real fallback.
    resp = client.get(reverse('job_detail', args=['archivist']), {'tab': 'ranks'})
    assert resp.context['active_tab'] == 'ranks'


def test_the_url_written_by_the_switcher_is_the_one_the_server_reads(client):
    """It wrote `?view=` (syncViewParam's default) while the view read `?tab=`. So clicking Ranks produced
    a URL that reloaded on Contracts, and arriving on `?tab=ranks` then clicking Contracts left `tab=ranks`
    in the URL while Contracts was on screen. One param, read and written."""
    _job('archivist', 'Archivist')
    body = client.get(reverse('job_detail', args=['archivist'])).content.decode()

    assert "param: 'tab'" in body, 'the switcher writes a param the server does not read'
    assert "default: 'contracts'" in body
