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
    ProfileJobXP.objects.create(profile=p, job=job, total_xp=xp, level=level)
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

    ranks = client.get(reverse('job_detail', args=['archivist']), {'tab': 'ranks'}).content.decode()
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
    body = client.get(reverse('job_detail', args=['archivist']), {'tab': 'ranks'}).content.decode()
    assert body.index('Higher') < body.index('Lower')


def test_a_job_with_no_xp_says_so_rather_than_rendering_an_empty_wall(client):
    _job('quiet', 'Quiet Job')
    body = client.get(reverse('job_detail', args=['quiet']), {'tab': 'ranks'}).content.decode()
    assert 'Nobody has banked XP here yet' in body


def test_only_the_OPEN_tab_is_built(client):
    """These are `?tab=` links that reload the page, and the template renders exactly one panel -- so
    building both meant every visit paid for six queries to render three."""
    job = _job('archivist', 'Archivist')
    _xp(job, 'Someone', 500)
    for i in range(3):
        _contract(f'Con {i}', [job])

    with CaptureQueriesContext(connection) as ranks:
        client.get(reverse('job_detail', args=['archivist']), {'tab': 'ranks'})
    ranks_sql = ' '.join(q['sql'] for q in ranks.captured_queries)
    assert 'trophies_contract' not in ranks_sql, 'the Ranks tab queried the Contracts panel'

    with CaptureQueriesContext(connection) as contracts:
        client.get(reverse('job_detail', args=['archivist']), {'tab': 'contracts'})
    contracts_sql = ' '.join(q['sql'] for q in contracts.captured_queries)
    assert 'trophies_profilejobxp' in contracts_sql, (
        'the header tally is on both tabs, so ProfileJobXP is expected here'
    )
    # ...but only for the tally, not for the board rows the Contracts tab never renders.
    assert contracts_sql.count('trophies_profilejobxp') < ranks_sql.count('trophies_profilejobxp')


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
    ProfileJobXP.objects.create(profile=viewer, job=job, total_xp=900, level=2)
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

    first = client.get(reverse('job_detail', args=['archivist']), {'tab': 'ranks'})
    assert len(first.context['board']) == 25
    assert first.context['board_has_next'] is True
    assert first.context['board_has_prev'] is False
    assert first.context['board'][0]['rank'] == 1

    second = client.get(reverse('job_detail', args=['archivist']), {'tab': 'ranks', 'page': 2})
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
        resp = client.get(reverse('job_detail', args=['archivist']), {'tab': 'ranks', 'page': raw})
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
    ProfileJobXP.objects.create(profile=viewer, job=job, total_xp=900, level=2)
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

    first = client.get(reverse('job_detail', args=['archivist']), {'tab': 'ranks'})
    assert len(first.context['board']) == 25
    assert first.context['board_has_next'] is False, 'a full first page offered a next page that is empty'

    # ...and one more entrant flips it, so the look-ahead is actually being read.
    _xp(job, 'Hunter25', 500)
    again = client.get(reverse('job_detail', args=['archivist']), {'tab': 'ranks'})
    assert again.context['board_has_next'] is True
    assert len(again.context['board']) == 25, 'the look-ahead row leaked into the rendered page'
