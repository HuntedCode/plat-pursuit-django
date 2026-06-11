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
    BadgeFactory,
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


def test_open_world_partitions_on_combat_genre():
    # Open-world + a combat genre -> Outlaw; open-world without -> Wayfarer.
    assert assign_jobs({'Shooter'}, {'Open world'}) == {'Gunslinger', 'Outlaw'}
    assert assign_jobs({'Role-playing (RPG)'}, {'Open world'}) == {'Roleplayer', 'Wayfarer'}


def test_comedy_partitions_on_platform():
    assert assign_jobs({'Platform'}, {'Comedy'}) == {'Acrobat', 'Mascot'}      # mascot platformer
    assert assign_jobs({'Puzzle'}, {'Comedy'}) == {'Puzzler', 'Jester'}        # other comedy


def test_freelancer_fallback_for_no_specialization():
    assert assign_jobs({'Adventure'}, {'Action'}) == {'Freelancer'}
    # ...but any real specialization suppresses the fallback.
    assert 'Freelancer' not in assign_jobs({'Shooter'}, set())


@pytest.mark.django_db
def test_command_counts_only_series_developer_badge_stages():
    rpg = Genre.objects.create(igdb_id=1, name='Role-playing (RPG)', slug='rpg')
    fantasy = Theme.objects.create(igdb_id=1, name='Fantasy', slug='fantasy')
    shooter = Genre.objects.create(igdb_id=2, name='Shooter', slug='shooter')

    def _concept(title, genre):
        c = ConceptFactory(unified_title=title, anchor_migration_completed_at=timezone.now())
        GameFactory(concept=c, shovelware_status='clean')
        ConceptCompanyFactory(concept=c)
        ConceptGenre.objects.create(concept=c, genre=genre)
        return c

    # A Fantasy RPG on a SERIES badge stage -> counts (maps to Mage).
    mage = _concept('Fantasy RPG', rpg)
    ConceptTheme.objects.create(concept=mage, theme=fantasy)
    BadgeFactory(series_slug='series-rpg')
    StageFactory(series_slug='series-rpg', stage_number=1).concepts.add(mage)

    # A Shooter on a GENRE badge stage -> excluded (genre badges grant no XP).
    gun = _concept('Genre Shooter', shooter)
    BadgeFactory(series_slug='genre-shooter', badge_type='genre')
    StageFactory(series_slug='genre-shooter', stage_number=1).concepts.add(gun)

    out = io.StringIO()
    call_command('report_job_assignment', stdout=out)
    report = out.getvalue()

    assert 'SERIES + DEVELOPER badges' in report
    assert '100.0%  Mage' in report          # Mage on the ONLY counted stage (the series badge)
    assert '0.0%  Gunslinger' in report      # the genre-badge shooter is excluded (count 0)
