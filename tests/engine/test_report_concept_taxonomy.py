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
from tests.factories import (
    ConceptCompanyFactory,
    ConceptFactory,
    GameFactory,
    StageFactory,
)

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


def test_badge_stages_flag_narrows_to_stage_concepts():
    shooter = Genre.objects.create(igdb_id=10, name='Shooter', slug='shooter')
    racing = Genre.objects.create(igdb_id=11, name='Racing', slug='racing')

    # Qualifies AND sits in a badge stage.
    in_stage = _anchored('InStage')
    ConceptGenre.objects.create(concept=in_stage, genre=shooter)
    ConceptCompanyFactory(concept=in_stage)
    stage = StageFactory(series_slug='series-x', stage_number=1)
    stage.concepts.add(in_stage)

    # Qualifies but is in NO stage -> excluded by --badge-stages.
    out_stage = _anchored('OutStage')
    ConceptGenre.objects.create(concept=out_stage, genre=racing)
    ConceptCompanyFactory(concept=out_stage)

    out = io.StringIO()
    call_command('report_concept_taxonomy', '--badge-stages', '--no-csv', stdout=out)
    report = out.getvalue()

    assert 'BADGE-STAGE GAMES ONLY' in report
    assert 'Badge series:' in report
    assert 'Shooter' in report        # the in-stage concept's genre
    assert 'Racing' not in report     # out-of-stage concept excluded


def test_by_stage_specialization_and_coverage():
    adv = Genre.objects.create(igdb_id=20, name='Adventure', slug='adventure')
    shooter = Genre.objects.create(igdb_id=21, name='Shooter', slug='shooter')
    rpg = Genre.objects.create(igdb_id=22, name='RPG', slug='rpg')

    # One stage, two concepts: Adventure+Shooter and Adventure+RPG.
    # Specialization genres (excluding the Adventure base) = {Shooter, RPG} = 2.
    c1 = _anchored('Stage1 GameA')
    ConceptCompanyFactory(concept=c1)
    ConceptGenre.objects.create(concept=c1, genre=adv)
    ConceptGenre.objects.create(concept=c1, genre=shooter)
    c2 = _anchored('Stage1 GameB')
    ConceptCompanyFactory(concept=c2)
    ConceptGenre.objects.create(concept=c2, genre=adv)
    ConceptGenre.objects.create(concept=c2, genre=rpg)
    stage = StageFactory(series_slug='series-x', stage_number=1)
    stage.concepts.add(c1, c2)

    out = io.StringIO()
    call_command('report_concept_taxonomy', '--by-stage', stdout=out)
    report = out.getvalue()

    assert 'Badge-stage JOB analysis' in report
    assert 'Specialization genres per stage' in report
    assert 'Stage genre coverage' in report
    assert '2 genres' in report                  # the stage spans 2 specializations
    assert 'Shooter' in report and 'RPG' in report


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
