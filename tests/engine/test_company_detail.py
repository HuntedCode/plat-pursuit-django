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


def _company(name, slug, country=None, logo=None, parent=None, changed_company=None):
    from trophies.models import Company
    return Company.objects.create(
        igdb_id=next(_co_seq), name=name, slug=slug, country=country, logo_image_id=logo or '',
        parent=parent, changed_company=changed_company,
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
    UserConceptRatingFactory(
        concept=concept, concept_trophy_group=None,
        overall_rating=4.0, difficulty=7, grindiness=3, fun_ranking=8, hours_to_platinum=25,
    )

    content = client.get(reverse('company_detail', kwargs={'slug': co.slug})).content.decode()

    assert 'co-comm' in content
    assert 'Based on' in content
    # The aggregated metrics actually render (labels + a distinctive value).
    for label in ('Rating', 'Difficulty', 'Fun', 'Grind'):
        assert label in content
    assert '25h' in content            # avg hours-to-plat, distinctive


def test_merger_parent_link(client):
    parent = _company('Parent Corp', 'parent-corp')
    _link(parent, 'Parent Game')
    sub = _company('Sub Studio', 'sub-studio', parent=parent)
    _link(sub, 'Sub Game')

    content = client.get(reverse('company_detail', kwargs={'slug': sub.slug})).content.decode()

    assert 'Subsidiary of' in content
    assert 'Parent Corp' in content


def test_description_renders_readmore_scaffold(client):
    """When the company has a description, the expandable About scaffold + (initially hidden) toggle render.
    The overflow gating that un-hides the toggle is JS/browser-side; here we assert the markup is present."""
    co = _company('Described Co', 'described-co')
    co.description = 'A long studio history. ' * 25
    co.save(update_fields=['description'])
    _link(co, 'Some Game')

    content = client.get(reverse('company_detail', kwargs={'slug': co.slug})).content.decode()

    assert 'data-co-about' in content
    assert 'data-co-about-text' in content          # the element the toggle JS measures/queries
    assert 'data-co-about-toggle' in content
    assert 'Read more' in content
    assert 'aria-expanded="false"' in content       # toggle starts collapsed
    assert 'aria-controls="co-about-text"' in content


def test_no_description_no_about_block(client):
    co = _company('Terse Co', 'terse-co')   # no description
    _link(co, 'Some Game')

    content = client.get(reverse('company_detail', kwargs={'slug': co.slug})).content.decode()

    assert 'data-co-about' not in content


def test_merger_now_operating_as_link(client):
    successor = _company('New Corp', 'new-corp')
    _link(successor, 'New Game')
    old = _company('Old Corp', 'old-corp', changed_company=successor)
    _link(old, 'Old Game')

    content = client.get(reverse('company_detail', kwargs={'slug': old.slug})).content.decode()

    assert 'Now operating as' in content
    assert 'New Corp' in content


# ── Fallbacks / edge cases ────────────────────────────────────────────────────────────────────────────────

def test_stale_tab_falls_back_to_first_section(client):
    co = _company('Fallback Studio', 'fallback-studio')
    _link(co, 'Dev Game', role='developer')

    content = client.get(
        reverse('company_detail', kwargs={'slug': co.slug}), {'tab': 'ported'},   # role with no games
    ).content.decode()

    assert 'Dev Game' in content       # fell back to the developed section rather than a blank tab


def test_company_with_no_games_shows_empty_message(client):
    from trophies.models import ConceptCompany
    co = _company('Ghost Studio', 'ghost-studio')
    concept = ConceptFactory(unified_title='Concept Without Games')   # no Game attached
    ConceptCompany.objects.create(concept=concept, company=co, is_developer=True)

    content = client.get(reverse('company_detail', kwargs={'slug': co.slug})).content.decode()

    assert 'No games found for this role.' in content
    assert 'pp-switch' not in content


def test_sort_reorders_group_list(client):
    co = _company('Sortable Studio', 'sortable-studio')
    _link(co, 'Alpha Game')
    _link(co, 'Zeta Game')

    az = client.get(reverse('company_detail', kwargs={'slug': co.slug}), {'sort': 'alpha'}).content.decode()
    za = client.get(reverse('company_detail', kwargs={'slug': co.slug}), {'sort': 'alpha_desc'}).content.decode()

    assert az.index('Alpha Game') < az.index('Zeta Game')
    assert za.index('Zeta Game') < za.index('Alpha Game')


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


def test_detail_no_per_game_n_plus_1(client):
    """Query count must be CONSTANT regardless of catalogue size -- a fixed ceiling would hide a per-game N+1.
    Exercises the authed path (progress rings) + a multi-version group, the N+1-prone renders."""
    from trophies.models import ConceptCompany
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    profile = ProfileFactory()
    client.force_login(profile.user)

    def _seed(prefix, n):
        co = _company(f'{prefix} Studio', f'{prefix.lower()}-studio')
        for i in range(n):
            concept, game = _link(co, f'{prefix} Game {i}')
            GameFactory(concept=concept, title_name=f'{prefix} Game {i}', title_platform=['PS4'],
                        defined_trophies=_TROPHIES)   # second edition -> a multi-version group
            ProfileGameFactory(profile=profile, game=game, progress=50)
        return co

    small = _seed('Small', 3)
    big = _seed('Big', 12)

    def _q(slug):
        with CaptureQueriesContext(connection) as ctx:
            assert client.get(reverse('company_detail', kwargs={'slug': slug})).status_code == 200
        return len(ctx)

    assert _q(small.slug) == _q(big.slug)   # 4x the games, same query count -> no per-game/version N+1
