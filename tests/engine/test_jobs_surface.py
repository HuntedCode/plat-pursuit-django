"""`/jobs/` + `/jobs/<slug>/` + the Job Boards directory (leaderboards rebuild, steps 7-8).

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
    """Give a hunter XP in a job, and refresh the denormalized entrant count.

    Job Boards gates and sorts on `Job.entrants`, which is recomputed nightly rather than maintained on
    write -- so a fixture that creates XP rows and stops leaves the board correctly invisible. Calling
    the real recompute (rather than setting the column by hand) means these tests would notice if it
    broke.
    """
    from trophies.management.commands.recalc_board_entrants import recalc_job_entrants

    p = ProfileFactory(display_psn_username=name)
    ProfileJobXP.objects.create(profile=p, job=job, total_xp=xp, level=level)
    recalc_job_entrants()
    job.refresh_from_db()
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


# ------------------------------------------------------------------ the Job Boards directory -------------

def test_job_boards_follows_the_same_thin_rule(client):
    """24 entities is exactly where a filter panel looks harmless and a third sort looks free. Same base,
    same rules -- that is the point of it being a shared base rather than a third bespoke page."""
    job = _job('archivist', 'Archivist')
    _xp(job, 'Someone', 500)

    body = client.get(reverse('job_boards')).content.decode()
    assert 'Archivist' in body
    assert body.count('<option value="') == 2, 'Job Boards should offer exactly two sorts'
    for drawer in ('data-browse-form', 'filterPanel', 'pp-bgal__advanced'):
        assert drawer not in body, f'{drawer} -- Job Boards grew a filter panel'


def test_a_job_nobody_is_levelling_is_not_listed(client):
    _job('empty', 'Empty Job')
    body = client.get(reverse('job_boards')).content.decode()
    assert 'Empty Job' not in body, 'a board with no entrants was listed'


def test_the_hub_subnav_carries_all_four_boards():
    """The hub carried NO items until this rebuild, on the argument that a rail would be a single pill
    naming the page you were already on. It now has four."""
    from core.hub_subnav import LEADERBOARDS_HUB
    keys = {i.slug for i in LEADERBOARDS_HUB.items}
    assert keys == {'global', 'games', 'badges', 'jobs'}, keys
