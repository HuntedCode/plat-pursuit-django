"""Tests for report_job_assignment: scopes to SERIES + DEVELOPER badge stages and
reports the job feed (by slug). The detection logic itself lives in
trophies/services/job_detection.py and is tested in test_job_detection.py."""
import io

import pytest
from django.core.management import call_command
from django.utils import timezone

from trophies.models import ConceptGenre, ConceptTheme, Genre, Theme
from tests.factories import (
    BadgeFactory, ConceptCompanyFactory, ConceptFactory, GameFactory, StageFactory,
)

pytestmark = pytest.mark.django_db


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

    # A Fantasy RPG on a SERIES badge stage -> counts (maps to mage).
    mage = _concept('Fantasy RPG', rpg)
    ConceptTheme.objects.create(concept=mage, theme=fantasy)
    BadgeFactory(series_slug='series-rpg')
    StageFactory(series_slug='series-rpg', stage_number=1).concepts.add(mage)

    # A Shooter on a COLLECTION badge stage -> excluded (only series + developer count).
    gun = _concept('Collection Shooter', shooter)
    BadgeFactory(series_slug='collection-shooter', badge_type='collection')
    StageFactory(series_slug='collection-shooter', stage_number=1).concepts.add(gun)

    out = io.StringIO()
    call_command('report_job_assignment', stdout=out)
    report = out.getvalue()

    assert 'SERIES + DEVELOPER badges' in report
    assert '100.0%  mage' in report          # mage on the ONLY counted stage (the series badge)
    assert '0.0%  gunslinger' in report      # the genre-badge shooter is excluded (count 0)
