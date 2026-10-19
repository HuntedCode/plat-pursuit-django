"""Company slugs are slugified on write and unique.

IGDB company slugs used to be stored as sent. One with characters the `<slug:>` URL converter rejects (IGDB's
"ctg-(core-technology-group)" shape) raised NoReverseMatch wherever that company page was linked, and the
column was not unique, so a duplicate 500'd the company page, which looks a company up by slug.
`IGDBService._company_slug` cleans every write; migration 0343 cleaned existing rows and 0344 made the
column unique.
"""
import importlib

import pytest
from django.db import IntegrityError
from django.urls import reverse

from tests.factories import CompanyFactory
from trophies.models import Company
from trophies.services.igdb_service import IGDBService

pytestmark = pytest.mark.django_db

planned_slugs = importlib.import_module('trophies.migrations.0343_clean_company_slugs').planned_slugs


def _write(**data):
    return IGDBService._get_or_create_company(data)


# ── the writer ───────────────────────────────────────────────────────────────────────────────────────

def test_an_unclean_igdb_slug_is_slugified_and_links(client):
    company = _write(id=501, name='CTG (Core Technology Group)', slug='ctg-(core-technology-group)')

    assert company.slug == 'ctg-core-technology-group'
    assert reverse('company_detail', kwargs={'slug': company.slug}) == '/companies/ctg-core-technology-group/'


def test_a_missing_slug_falls_back_to_the_name_then_the_id():
    assert _write(id=502, name='Rockstar North').slug == 'rockstar-north'
    assert _write(id=503, name='!!!').slug == 'company-503'


def test_a_clash_with_another_company_gets_its_igdb_id_appended():
    CompanyFactory(igdb_id=600, slug='twin-studio')

    assert _write(id=601, name='Twin Studio', slug='twin-studio').slug == 'twin-studio-601'


def test_a_refresh_keeps_a_companys_own_slug():
    _write(id=602, name='Naughty Dog', slug='naughty-dog')

    assert _write(id=602, name='Naughty Dog', slug='naughty-dog').slug == 'naughty-dog'
    assert Company.objects.filter(slug__startswith='naughty-dog').count() == 1


def test_a_parent_created_from_a_child_payload_is_slugified_too():
    child = _write(id=603, name='Child', slug='child', parent={'id': 604, 'name': 'Parent (Holdings)',
                                                            'slug': 'parent-(holdings)'})

    assert child.parent.slug == 'parent-holdings'


def test_the_database_refuses_a_duplicate_slug():
    CompanyFactory(slug='only-once')

    with pytest.raises(IntegrityError):
        CompanyFactory(slug='only-once')


# ── migration 0343's plan (pure: the test database already has 0344's constraint) ────────────────────

def test_clean_unique_slugs_are_left_alone():
    assert planned_slugs([(1, 101, 'Naughty Dog', 'naughty-dog'), (2, 102, 'Insomniac', 'insomniac')]) == {}


def test_an_unclean_slug_is_cleaned_and_an_empty_one_takes_the_name_or_id():
    plan = planned_slugs([
        (1, 101, 'CTG', 'ctg-(core-technology-group)'),
        (2, 102, 'Rockstar North', ''),
        (3, 103, '!!!', ''),
    ])

    assert plan == {1: 'ctg-core-technology-group', 2: 'rockstar-north', 3: 'company-103'}


def test_among_duplicates_the_oldest_row_keeps_the_slug():
    plan = planned_slugs([(7, 207, 'Twin B', 'twin'), (3, 203, 'Twin A', 'twin')])

    assert plan == {7: 'twin-207'}


def test_a_cleaned_slug_never_takes_one_a_clean_row_already_holds():
    # The clean row keeps its working URL even though the unclean one has the lower id.
    plan = planned_slugs([(1, 101, 'Studio', 'studio-(old)'), (2, 102, 'Studio Old', 'studio-old')])

    assert plan == {1: 'studio-old-101'}


def test_an_overlong_slug_is_cut_to_fit_with_its_suffix():
    long_slug = 'a' * 300
    plan = planned_slugs([(1, 101, 'A', 'a' * 255), (2, 102, 'A again', long_slug)])

    assert plan == {2: 'a' * (255 - len('-102')) + '-102'}


# ── clashes, races and long slugs ────────────────────────────────────────────────────────────────────

def test_when_the_suffixed_slug_is_taken_too_the_next_candidate_wins():
    CompanyFactory(igdb_id=700, slug='twin')
    CompanyFactory(igdb_id=701, slug='twin-705')   # someone's IGDB slug is literally the suffixed form

    assert _write(id=705, name='Twin', slug='twin').slug == 'company-705'


def test_a_refresh_after_a_clash_keeps_the_suffixed_slug():
    CompanyFactory(igdb_id=710, slug='shared-name')
    first = _write(id=711, name='Shared Name', slug='shared-name').slug

    assert first == 'shared-name-711'
    assert _write(id=711, name='Shared Name', slug='shared-name').slug == first


def test_a_slug_clash_at_write_time_retries_and_leaves_the_transaction_usable(monkeypatch):
    # Two enrichments can both see a slug free and race to write it. The loser retries with a fresh pick;
    # get_or_create's own savepoint keeps the failed insert from breaking the surrounding transaction.
    CompanyFactory(igdb_id=720, slug='raced')
    real = IGDBService._company_slug.__func__
    picks = iter(['raced'])   # the first pick is stale: another company took it after the check

    def stale_then_real(cls, raw_slug, name, igdb_id):
        return next(picks, None) or real(cls, raw_slug, name, igdb_id)
    monkeypatch.setattr(IGDBService, '_company_slug', classmethod(stale_then_real))

    company = _write(id=721, name='Raced', slug='raced')

    assert company.slug == 'raced-721'
    assert Company.objects.filter(igdb_id=721).count() == 1   # the transaction is still usable


def test_a_cut_slug_never_ends_in_a_separator():
    raw = 'a' * 254 + '-tail'           # the cut at 255 characters lands on the dash

    assert _write(id=730, name='Long', slug=raw).slug == 'a' * 254


def test_the_migration_plan_uses_the_same_candidates_and_trims_the_cut():
    plan = planned_slugs([
        (1, 101, 'Twin', 'twin'), (2, 102, 'Twin 2', 'twin-103'),   # the suffixed form is held
        (3, 103, 'Twin 3', 'twin'),                                  # -> company-103
        (4, 104, 'Long', 'a' * 254 + '-tail'),                       # -> the cut drops the dash
    ])

    assert plan == {3: 'company-103', 4: 'a' * 254}


def _stale_first_pick(monkeypatch, stale):
    """The first slug pick returns `stale` (taken by the time it is written); later picks are real."""
    real = IGDBService._company_slug.__func__
    picks = iter([stale])

    def pick(cls, raw_slug, name, igdb_id):
        return next(picks, None) or real(cls, raw_slug, name, igdb_id)
    monkeypatch.setattr(IGDBService, '_company_slug', classmethod(pick))


def test_a_slug_clash_on_a_refresh_retries_too(monkeypatch):
    # The UPDATE branch: an existing company whose new slug was taken between the check and the write.
    _write(id=740, name='Renamed', slug='old-name')
    CompanyFactory(igdb_id=741, slug='new-name')
    _stale_first_pick(monkeypatch, 'new-name')

    assert _write(id=740, name='Renamed', slug='new-name').slug == 'new-name-740'


def test_a_new_parents_slug_clash_retries_too(monkeypatch):
    CompanyFactory(igdb_id=750, slug='holding')
    _stale_first_pick(monkeypatch, 'holding')

    child = _write(id=752, name='Child Co', slug='child-co',
                   parent={'id': 751, 'name': 'Holding', 'slug': 'holding'})

    assert child.parent.slug == 'holding-751'


def test_an_existing_parent_costs_no_slug_lookup(monkeypatch):
    _write(id=760, name='Parent', slug='parent')
    calls = []
    real = IGDBService._company_slug.__func__

    def counting(cls, raw_slug, name, igdb_id):
        calls.append(igdb_id)
        return real(cls, raw_slug, name, igdb_id)
    monkeypatch.setattr(IGDBService, '_company_slug', classmethod(counting))

    _write(id=761, name='Child', slug='child', parent={'id': 760, 'name': 'Parent', 'slug': 'parent'})

    assert calls == [761], 'only the child (an update_or_create) needs its slug picked'


def test_the_last_resort_slug_is_stable_across_refreshes():
    CompanyFactory(igdb_id=770, slug='crowded')
    CompanyFactory(igdb_id=771, slug='crowded-772')
    CompanyFactory(igdb_id=773, slug='company-772')

    first = _write(id=772, name='Crowded', slug='crowded').slug

    assert first == 'company-772-2'
    assert _write(id=772, name='Crowded', slug='crowded').slug == first


def test_a_migration_assigned_last_resort_slug_survives_a_refresh():
    # 0343's last resort is `company-<igdb id>-<pk>`; the service's own counter would pick `-2`, so without
    # keeping the existing one the first refresh would move this company's URL.
    CompanyFactory(igdb_id=780, slug='packed')
    CompanyFactory(igdb_id=781, slug='packed-782')
    CompanyFactory(igdb_id=783, slug='company-782')
    CompanyFactory(igdb_id=782, slug='company-782-99')

    assert _write(id=782, name='Packed', slug='packed').slug == 'company-782-99'
