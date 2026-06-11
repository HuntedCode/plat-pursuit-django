"""Tests for the report_concept_taxonomy command.

Pins the scope (anchored AND non-shovelware) and the genre/theme distribution
that the gamification jobs/XP planning will rely on.
"""
import csv
import io

import pytest
from django.core.management import call_command
from django.utils import timezone

from trophies.models import ConceptGenre, ConceptTheme, Genre, Theme
from tests.factories import ConceptCompanyFactory, ConceptFactory, GameFactory

pytestmark = pytest.mark.django_db


def _anchored(title, clean=True):
    c = ConceptFactory(unified_title=title, anchor_migration_completed_at=timezone.now())
    GameFactory(concept=c, shovelware_status='clean' if clean else 'auto_flagged')
    return c


def _setup():
    rpg = Genre.objects.create(igdb_id=1, name='RPG', slug='rpg')
    puzzle = Genre.objects.create(igdb_id=2, name='Puzzle', slug='puzzle')
    fantasy = Theme.objects.create(igdb_id=1, name='Fantasy', slug='fantasy')

    a = _anchored('Game A')
    b = _anchored('Game B')
    ConceptGenre.objects.create(concept=a, genre=rpg)
    ConceptGenre.objects.create(concept=b, genre=rpg)
    ConceptTheme.objects.create(concept=a, theme=fantasy)
    ConceptCompanyFactory(concept=a)  # main developer (is_developer default True)
    ConceptCompanyFactory(concept=b, is_developer=False, is_porting=True)  # porting dev (tests the OR)

    # Excluded: anchored + clean but every game is shovelware-flagged.
    shovel = _anchored('Shovel', clean=False)
    ConceptGenre.objects.create(concept=shovel, genre=puzzle)
    ConceptCompanyFactory(concept=shovel)

    # Excluded: clean game but NOT anchored.
    unanchored = ConceptFactory(unified_title='Unanchored')
    GameFactory(concept=unanchored, shovelware_status='clean')
    ConceptGenre.objects.create(concept=unanchored, genre=puzzle)
    ConceptCompanyFactory(concept=unanchored)

    # Excluded: anchored + clean but only a PUBLISHER (no developer/porting).
    pub = _anchored('PubOnly')
    ConceptGenre.objects.create(concept=pub, genre=puzzle)
    ConceptCompanyFactory(concept=pub, is_developer=False, is_publisher=True)
    return a, b


def test_summary_scope_and_distribution():
    _setup()
    out = io.StringIO()
    call_command('report_concept_taxonomy', '--no-csv', stdout=out)
    report = out.getvalue()

    assert 'Total concepts:' in report
    # RPG covers both qualifying concepts; only the excluded ones carry Puzzle.
    assert 'RPG' in report
    assert 'Fantasy' in report
    assert 'Puzzle' not in report   # excluded concepts must not leak into the report

    # Combination analysis is present and reflects the set.
    assert 'Genre COMBINATIONS' in report
    assert 'Genre x Theme co-occurrence' in report
    assert 'RPG  x  Fantasy' in report   # concept A pairs RPG genre with Fantasy theme


def test_csv_lists_only_qualifying_concepts(tmp_path):
    a, b = _setup()
    csv_path = tmp_path / 'taxonomy.csv'
    call_command('report_concept_taxonomy', '--output', str(csv_path), stdout=io.StringIO())

    rows = list(csv.DictReader(csv_path.open(encoding='utf-8')))
    titles = {r['title'] for r in rows}
    assert titles == {'Game A', 'Game B'}        # not Shovel, not Unanchored
    row_a = next(r for r in rows if r['title'] == 'Game A')
    assert row_a['genres'] == 'RPG'
    assert row_a['themes'] == 'Fantasy'
