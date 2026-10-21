"""`contract_service.claim_projection`: what claiming every claimable contract would make a hunter.

The first arrival leads with it ("claim them to reach Level 103 · Hunter V"), so it is a promise: it has to
be what a claim actually pays. Pinned against a real Claim all, not against a re-derivation of the split.
"""
import itertools

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from tests.factories import ProfileFactory
from trophies.models import Contract, EarnedContract, Job, ProfileJobXP
from trophies.services import contract_service
from trophies.util_modules.leveling import level_for_xp

pytestmark = pytest.mark.django_db

_seq = itertools.count(94001)


def _contract(jobs, *, live=True):
    n = next(_seq)
    c = Contract.objects.create(name=f'proj-{n}', slug=f'proj-{n}', igdb_id=n, is_live=live)
    c.jobs.set(Job.objects.filter(slug__in=jobs))
    return c


def _claimable(profile, jobs, *, plat=False, has_platinum=False, live=True):
    now = timezone.now()
    return EarnedContract.objects.create(
        profile=profile, contract=_contract(jobs, live=live), has_platinum=has_platinum,
        platinum_reached_at=now if plat else None, full_reached_at=now)


def _levels(profile):
    return {slug: level for slug, level in ProfileJobXP.objects.filter(profile=profile).values_list('job_id', 'level')}


def test_the_projection_is_what_claiming_everything_actually_gives():
    """Differential against a real Claim all: one-, two- and three-job contracts, jobs shared between
    contracts, a platinum tier as well as a full one, and a job that already has XP of its own."""
    profile = ProfileFactory(is_linked=True)
    ProfileJobXP.objects.create(profile=profile, job=Job.objects.get(slug='mage'), total_xp=7500,
                                level=level_for_xp(7500))
    _claimable(profile, ['gunslinger'])
    _claimable(profile, ['gunslinger', 'pathfinder'], plat=True, has_platinum=True)
    _claimable(profile, ['pathfinder', 'mage', 'tycoon'])
    _claimable(profile, ['mage'], plat=True, has_platinum=True)
    for _ in range(4):
        _claimable(profile, ['pathfinder'])

    projection = contract_service.claim_projection(profile)
    contract_service.claim(profile, all_claimable=True)

    after = _levels(profile)
    assert {slug: j['to'] for slug, j in projection['jobs'].items()} == {
        slug: lvl for slug, lvl in after.items() if slug in projection['jobs']}
    assert projection['level'] == contract_service._pursuer_level(profile)
    assert projection['rank']['key'] == contract_service.pursuer_rank_for_level(projection['level'])['key']
    assert projection['jobs']['mage']['from'] == level_for_xp(7500), 'the starting level ignored held XP'


def test_nothing_claimable_projects_nothing():
    profile = ProfileFactory(is_linked=True)
    _claimable(profile, ['gunslinger'], live=False)      # not live: not claimable anywhere
    _claimable(profile, [])                              # no jobs: levels nothing
    assert contract_service.claim_projection(profile) is None


def test_top_lists_the_jobs_raised_furthest_and_only_raised_ones():
    profile = ProfileFactory(is_linked=True)
    for _ in range(3):
        _claimable(profile, ['pathfinder'])              # 3 x 6,000 = +6 levels
    _claimable(profile, ['gunslinger'])                  # +2
    _claimable(profile, ['tycoon', 'mage'])              # +1 each
    _claimable(profile, ['mastermind', 'mage', 'tycoon', 'outlaw', 'vanguard', 'slayer'])  # 1,000 each

    top = contract_service.claim_projection(profile)['top']

    assert [(t['slug'], t['from'], t['to']) for t in top] == [
        ('pathfinder', 1, 7), ('gunslinger', 1, 3), ('mage', 1, 2)]
    assert top[0]['name'] == 'Pathfinder'
    jobs = contract_service.claim_projection(profile)['jobs']
    assert jobs['slayer'] == {'from': 1, 'to': 1}, 'a job short of a level is in the projection, unraised'


def test_its_query_count_does_not_grow_with_the_claimables():
    """A whale's first link can carry thousands of claimables; the cost has to be flat in them."""
    def queries(profile):
        with CaptureQueriesContext(connection) as ctx:
            contract_service.claim_projection(profile)
        return len(ctx.captured_queries)

    small, large = ProfileFactory(is_linked=True), ProfileFactory(is_linked=True)
    _claimable(small, ['gunslinger'])
    for i in range(12):
        _claimable(large, ['gunslinger', 'pathfinder', 'mage'][: 1 + i % 3])

    assert queries(small) == queries(large) == 3
