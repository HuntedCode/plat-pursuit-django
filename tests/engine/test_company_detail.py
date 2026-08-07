"""Tests for the rebuilt Company detail page (CompanyDetailView, /companies/<slug>/).

Covers the from-scratch shell rebuild: the accented header (logo thumb + catalogue totals), the role
switcher (.pp-switch, one chip per populated role) vs the single-role fallback, the shared .fgroup group
list, the authed your-progress block, the community-stats strip, the merger links, and the HTMX role/sort
swap partial guard.
"""

import itertools

import pytest
from django.urls import reverse

from tests.factories import (
    ConceptFactory, GameFactory, IGDBMatchFactory, ProfileFactory, ProfileGameFactory,
    UserConceptRatingFactory,
)

pytestmark = pytest.mark.django_db

FULL_PAGE = 'trophies/company_detail.html'
GROUP_PARTIAL = 'trophies/partials/franchise_detail/game_groups_list.html'

_co_seq = itertools.count(65001)
_ig_seq = itertools.count(775001)

_ROLE_FLAG = {
    'developer': 'is_developer', 'publisher': 'is_publisher',
    'porting': 'is_porting', 'supporting': 'is_supporting',
}
_TROPHIES = {'bronze': 10, 'silver': 5, 'gold': 3, 'platinum': 1}


def _company(name, slug, country=None, logo=None, parent=None):
    from trophies.models import Company
    return Company.objects.create(
        igdb_id=next(_co_seq), name=name, slug=slug, country=country, logo_image_id=logo or '', parent=parent,
    )


def _link(company, title, role='developer'):
    from trophies.models import ConceptCompany
    concept = ConceptFactory(unified_title=title)
    IGDBMatchFactory(concept=concept, igdb_id=next(_ig_seq))
    game = GameFactory(concept=concept, title_name=title, defined_trophies=_TROPHIES)
    ConceptCompany.objects.create(concept=concept, company=company, **{_ROLE_FLAG[role]: True})
    return concept, game


# ── Rendering + identity ──────────────────────────────────────────────────────────────────────────────────

def test_company_detail_renders(client):
    co = _company('Naughty Dog', 'nd-detail')
    _link(co, 'The Last of Us')

    resp = client.get(reverse('company_detail', kwargs={'slug': co.slug}))
    content = resp.content.decode()

    assert resp.status_code == 200
    assert 'Naughty Dog' in content
    assert 'fgroup' in content                 # shared grouped-game card
    assert 'The Last of Us' in content
    assert '>Company<' in content              # identity chip
    assert 'scard' in content
    assert '{#' not in content and '{%' not in content


def test_logo_thumb_renders(client):
    co = _company('Insomniac', 'insomniac-detail', logo='logo987')
    _link(co, 'Spider-Man')

    content = client.get(reverse('company_detail', kwargs={'slug': co.slug})).content.decode()

    assert 'co-logo__plate' in content
    assert 'logo987' in content


# ── Role switcher ─────────────────────────────────────────────────────────────────────────────────────────

def test_role_switcher_when_multiple_roles(client):
    co = _company('Multi Role', 'multi-role')
    _link(co, 'Dev Game', role='developer')
    _link(co, 'Pub Game', role='publisher')

    content = client.get(reverse('company_detail', kwargs={'slug': co.slug})).content.decode()

    assert 'pp-switch' in content
    assert 'Developed' in content and 'Published' in content
    # Default tab is the first populated role (developed): its game shows, the other role's doesn't.
    assert 'Dev Game' in content
    assert 'Pub Game' not in content

    published = client.get(reverse('company_detail', kwargs={'slug': co.slug}), {'tab': 'published'}).content.decode()
    assert 'Pub Game' in published
    assert 'Dev Game' not in published


def test_single_role_has_no_switcher(client):
    co = _company('Solo Role', 'solo-role')
    _link(co, 'Only Game', role='developer')

    content = client.get(reverse('company_detail', kwargs={'slug': co.slug})).content.decode()

    assert 'pp-switch' not in content
    assert 'name="tab"' in content             # single role carried as a hidden input
    assert 'Only Game' in content


# ── Authed progress + community + merger ──────────────────────────────────────────────────────────────────

def test_authed_your_progress_block(client):
    co = _company('Sucker Punch', 'sucker-punch')
    _, game = _link(co, 'Ghost of Tsushima')
    profile = ProfileFactory()
    ProfileGameFactory(profile=profile, game=game, progress=100, has_plat=True)

    client.force_login(profile.user)
    content = client.get(reverse('company_detail', kwargs={'slug': co.slug})).content.decode()

    assert 'Your progress' in content
    assert 'fr-prog' in content
    assert 'pp-horizon' in content
    assert 'fgroup__ring' in content


def test_community_stats_strip(client):
    co = _company('Rated Studio', 'rated-studio')
    concept, _ = _link(co, 'Rated Game')
    UserConceptRatingFactory(concept=concept, concept_trophy_group=None, overall_rating=4.0)

    content = client.get(reverse('company_detail', kwargs={'slug': co.slug})).content.decode()

    assert 'co-comm' in content
    assert 'Based on' in content


def test_merger_parent_link(client):
    parent = _company('Parent Corp', 'parent-corp')
    _link(parent, 'Parent Game')
    sub = _company('Sub Studio', 'sub-studio', parent=parent)
    _link(sub, 'Sub Game')

    content = client.get(reverse('company_detail', kwargs={'slug': sub.slug})).content.decode()

    assert 'Subsidiary of' in content
    assert 'Parent Corp' in content


# ── HTMX role/sort swap ───────────────────────────────────────────────────────────────────────────────────

def test_role_swap_returns_group_partial(client):
    co = _company('Swap Studio', 'swap-studio')
    _link(co, 'Swap Game')

    resp = client.get(
        reverse('company_detail', kwargs={'slug': co.slug}), {'sort': 'alpha'},
        HTTP_HX_REQUEST='true',
    )
    templates = {t.name for t in resp.templates if t.name}

    assert resp.status_code == 200
    assert GROUP_PARTIAL in templates
    assert FULL_PAGE not in templates
    assert 'pp-reveal' in resp.content.decode()


def test_detail_query_count_bounded(client, django_assert_max_num_queries):
    co = _company('Big Catalogue', 'big-catalogue')
    for i in range(10):
        _link(co, f'Game {i}')

    with django_assert_max_num_queries(18):
        resp = client.get(reverse('company_detail', kwargs={'slug': co.slug}))
    assert resp.status_code == 200
