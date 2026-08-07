"""Tests for the rebuilt Franchise/Series detail page (FranchiseDetailView, /franchises/<slug>/).

Covers the from-scratch rebuild: the accented header (Franchise vs Series identity + totals), the shared
IGDB-grouped game card (.fgroup, also used by Company detail), the related-groupings rail (.pp-gtile), the
authed your-progress block, the HTMX sort-swap partial guard, and that the rebuilt shared card did not break
the Company detail page that includes it.
"""

import itertools

import pytest
from django.urls import reverse

from tests.factories import (
    ConceptFactory, GameFactory, IGDBMatchFactory, ProfileFactory, ProfileGameFactory,
)

pytestmark = pytest.mark.django_db

FULL_PAGE = 'trophies/franchise_detail.html'
GROUP_PARTIAL = 'trophies/partials/franchise_detail/game_groups_list.html'

_fr_seq = itertools.count(50001)
_ig_seq = itertools.count(990001)

_TROPHIES = {'bronze': 10, 'silver': 5, 'gold': 3, 'platinum': 1}


def _franchise(name, slug, source_type='franchise'):
    from trophies.models import Franchise
    return Franchise.objects.create(igdb_id=next(_fr_seq), name=name, slug=slug, source_type=source_type)


def _member(fr, title, igdb_id=None, platforms=None, spinoff=False, excluded=False, concept=None):
    """Attach a member game (its own concept + IGDB id, so it becomes its own group) to a franchise."""
    from trophies.models import ConceptFranchise
    concept = concept or ConceptFactory(unified_title=title)
    if not hasattr(concept, 'igdb_match'):
        IGDBMatchFactory(concept=concept, igdb_id=igdb_id or next(_ig_seq))
    game = GameFactory(
        concept=concept, title_name=title, title_platform=platforms or ['PS5'], defined_trophies=_TROPHIES,
    )
    ConceptFranchise.objects.create(concept=concept, franchise=fr, is_spinoff=spinoff, is_excluded=excluded)
    return concept, game


# ── Rendering + identity ──────────────────────────────────────────────────────────────────────────────────

def test_franchise_detail_renders(client):
    fr = _franchise('Resident Evil', 'resident-evil')
    _member(fr, 'Resident Evil 4')
    _member(fr, 'Resident Evil Village')

    resp = client.get(reverse('franchise_detail', kwargs={'slug': fr.slug}))
    content = resp.content.decode()

    assert resp.status_code == 200
    assert 'Resident Evil' in content
    assert 'fgroup' in content                    # the shared grouped-game card
    assert 'Resident Evil 4' in content
    assert 'Resident Evil Village' in content
    assert '>Franchise<' in content               # identity chip
    assert 'scard' in content                     # totals row
    assert '{#' not in content and '{%' not in content


def test_series_identity_chip(client):
    fr = _franchise('FF VII Remake', 'ff-vii-remake', source_type='collection')
    _member(fr, 'Final Fantasy VII Remake')

    content = client.get(reverse('franchise_detail', kwargs={'slug': fr.slug})).content.decode()

    assert '>Series<' in content
    assert '>Franchise<' not in content


# ── Related rail (opposite-type groupings sharing games) ──────────────────────────────────────────────────

def test_related_rail_surfaces_opposite_type(client):
    """A franchise page rails the series that share its games (and vice versa) via the shared .pp-gtile."""
    franchise = _franchise('Final Fantasy', 'final-fantasy', source_type='franchise')
    series = _franchise('FF VII Collection', 'ff-vii-collection', source_type='collection')
    # One shared concept lives in both the franchise and the series.
    shared = ConceptFactory(unified_title='Final Fantasy VII')
    IGDBMatchFactory(concept=shared, igdb_id=next(_ig_seq))
    _member(franchise, 'Final Fantasy VII', concept=shared)
    from trophies.models import ConceptFranchise
    GameFactory(concept=ConceptFactory(unified_title='FF VII spin'), title_platform=['PS5'])  # noise
    ConceptFranchise.objects.create(concept=shared, franchise=series)

    content = client.get(reverse('franchise_detail', kwargs={'slug': franchise.slug})).content.decode()

    assert 'pp-related' in content
    assert 'FF VII Collection' in content
    assert 'Related series' in content            # opposite-type label, lowercased in the heading
    assert 'pp-gtile' in content                  # rendered with the shared grouping tile


# ── Authed your-progress ──────────────────────────────────────────────────────────────────────────────────

def test_authed_your_progress_block(client):
    fr = _franchise('Uncharted', 'uncharted')
    _, game = _member(fr, 'Uncharted 4')
    profile = ProfileFactory()
    ProfileGameFactory(profile=profile, game=game, progress=100, has_plat=True)

    client.force_login(profile.user)
    content = client.get(reverse('franchise_detail', kwargs={'slug': fr.slug})).content.decode()

    assert 'Your progress' in content
    assert 'fr-prog' in content
    assert 'pp-horizon' in content
    assert 'fgroup__ring' in content              # per-version progress ring (authed toggle)


def test_anon_has_no_progress_block(client):
    fr = _franchise('God of War', 'god-of-war')
    _member(fr, 'God of War')

    content = client.get(reverse('franchise_detail', kwargs={'slug': fr.slug})).content.decode()

    assert 'Your progress' not in content
    assert 'fgroup__ring' not in content


# ── Spin-off suppression (rebuilt path still honors is_spinoff) ───────────────────────────────────────────

def test_spinoff_member_hidden(client):
    fr = _franchise('Saints Row', 'saints-row-detail', source_type='collection')
    _member(fr, 'Saints Row IV')
    _member(fr, 'Agents of Mayhem', spinoff=True)

    content = client.get(reverse('franchise_detail', kwargs={'slug': fr.slug})).content.decode()

    assert 'Saints Row IV' in content
    assert 'Agents of Mayhem' not in content


# ── HTMX sort swap ────────────────────────────────────────────────────────────────────────────────────────

def test_sort_change_returns_group_partial(client):
    fr = _franchise('Dark Souls', 'dark-souls')
    _member(fr, 'Dark Souls III')

    resp = client.get(
        reverse('franchise_detail', kwargs={'slug': fr.slug}), {'sort': 'alpha'},
        HTTP_HX_REQUEST='true',
    )
    templates = {t.name for t in resp.templates if t.name}

    assert resp.status_code == 200
    assert GROUP_PARTIAL in templates
    assert FULL_PAGE not in templates
    assert 'pp-reveal' in resp.content.decode()   # group_reveal baked on the franchise HTMX swap


def test_detail_no_per_group_n_plus_1(client):
    """Query count must be CONSTANT regardless of member count -- a fixed ceiling would hide a small N+1."""
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    small = _franchise('Small IP', 'small-ip')
    for i in range(3):
        _member(small, f'S{i}')
    big = _franchise('Big IP', 'big-ip')
    for i in range(12):
        _member(big, f'B{i}')

    def _q(slug):
        with CaptureQueriesContext(connection) as ctx:
            assert client.get(reverse('franchise_detail', kwargs={'slug': slug})).status_code == 200
        return len(ctx)

    assert _q('small-ip') == _q('big-ip')   # 4x the members, same query count -> no per-group/version N+1


def test_excluded_member_hidden_from_list(client):
    fr = _franchise('Gears', 'gears-detail')
    _member(fr, 'Gears 5')
    _member(fr, 'Gears Hidden', excluded=True)

    content = client.get(reverse('franchise_detail', kwargs={'slug': fr.slug})).content.decode()

    assert 'Gears 5' in content
    assert 'Gears Hidden' not in content


def test_rail_counts_exclude_hidden_links(client):
    """A rail tile's game/version counts must match the browse list -- over VISIBLE links only."""
    from trophies.models import ConceptFranchise
    franchise = _franchise('Halo IP', 'halo-ip', source_type='franchise')
    series = _franchise('Halo Trilogy', 'halo-trilogy', source_type='collection')
    shared = ConceptFactory(unified_title='Halo CE')
    IGDBMatchFactory(concept=shared, igdb_id=next(_ig_seq))
    GameFactory(concept=shared, title_name='Halo CE', defined_trophies=_TROPHIES)
    ConceptFranchise.objects.create(concept=shared, franchise=franchise)   # ties the two together
    ConceptFranchise.objects.create(concept=shared, franchise=series)      # visible member of the series
    # An EXCLUDED extra member of the series must NOT inflate its rail counts.
    excluded = ConceptFactory(unified_title='Halo Excluded')
    IGDBMatchFactory(concept=excluded, igdb_id=next(_ig_seq))
    GameFactory(concept=excluded, title_name='Halo Excluded')
    ConceptFranchise.objects.create(concept=excluded, franchise=series, is_excluded=True)

    resp = client.get(reverse('franchise_detail', kwargs={'slug': franchise.slug}))
    rail = {e.slug: e for e in resp.context['related_entries']}

    assert 'halo-trilogy' in rail
    assert rail['halo-trilogy'].game_count == 1      # only the visible shared member; excluded one filtered out
    assert rail['halo-trilogy'].version_count == 1


# ── Company detail still works with the rebuilt shared card ───────────────────────────────────────────────

def test_company_detail_uses_rebuilt_card(client):
    """Company detail includes the shared game_groups_list.html; the rebuild must not break it."""
    from trophies.models import Company, ConceptCompany
    company = Company.objects.create(igdb_id=next(_fr_seq), name='Insomniac', slug='insomniac')
    concept = ConceptFactory(unified_title='Marvels Spider-Man')
    IGDBMatchFactory(concept=concept, igdb_id=next(_ig_seq))
    GameFactory(concept=concept, title_name='Marvels Spider-Man', defined_trophies=_TROPHIES)
    ConceptCompany.objects.create(concept=concept, company=company, is_developer=True)

    resp = client.get(reverse('company_detail', kwargs={'slug': company.slug}))
    content = resp.content.decode()

    assert resp.status_code == 200
    assert 'fgroup' in content                    # inherits the rebuilt shared card
    assert 'Marvels Spider-Man' in content
    # Company has no franchise reveal JS, so the shared partial must NOT bake pp-reveal for it.
    assert 'pp-reveal' not in content
