"""Tests for report_job_assignment: the v1 Job catalog detection rules.

Most pin the pure assign_jobs() logic (genre/theme detection + combo overrides);
one runs the command end-to-end against a stage.
"""
import io

import pytest
from django.core.management import call_command
from django.utils import timezone

from trophies.management.commands.report_job_assignment import assign_jobs
from trophies.models import ConceptGenre, ConceptTheme, Genre, Theme
from tests.factories import (
    ConceptCompanyFactory,
    ConceptFactory,
    GameFactory,
    StageFactory,
)


def test_combo_overrides_its_base_job():
    assert assign_jobs({'Role-playing (RPG)'}, {'Fantasy'}) == {'Mage'}            # not Roleplayer
    assert assign_jobs({'Shooter'}, {'Science fiction'}) == {'Starfarer'}          # not Gunslinger


def test_plain_genre_jobs():
    assert assign_jobs({'Role-playing (RPG)'}, set()) == {'Roleplayer'}
    assert assign_jobs({'Shooter'}, {'Fantasy'}) == {'Gunslinger'}                 # Fantasy != Sci-fi, no combo


def test_theme_and_multi_genre():
    assert assign_jobs({'Shooter', 'Platform'}, {'Stealth'}) == {'Gunslinger', 'Acrobat', 'Infiltrator'}


def test_merged_tactician():
    assert assign_jobs({'Turn-based strategy (TBS)'}, set()) == {'Tactician'}
    assert assign_jobs({'MOBA'}, set()) == {'Tactician'}


def test_pure_adventure_yields_no_job():
    assert assign_jobs({'Adventure'}, {'Action'}) == set()


@pytest.mark.django_db
def test_command_runs_against_a_stage():
    rpg = Genre.objects.create(igdb_id=1, name='Role-playing (RPG)', slug='rpg')
    fantasy = Theme.objects.create(igdb_id=1, name='Fantasy', slug='fantasy')
    c = ConceptFactory(unified_title='Fantasy RPG', anchor_migration_completed_at=timezone.now())
    GameFactory(concept=c, shovelware_status='clean')
    ConceptCompanyFactory(concept=c)
    ConceptGenre.objects.create(concept=c, genre=rpg)
    ConceptTheme.objects.create(concept=c, theme=fantasy)
    stage = StageFactory(series_slug='series-x', stage_number=1)
    stage.concepts.add(c)

    out = io.StringIO()
    call_command('report_job_assignment', stdout=out)
    report = out.getvalue()

    assert 'Job assignment simulation' in report
    assert 'Job feed' in report
    assert 'Mage' in report          # the Fantasy RPG maps to Mage (combo override)
    assert '1 job' in report         # exactly one job on the stage
