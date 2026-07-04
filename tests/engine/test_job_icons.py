"""Every seeded job has a Lucide icon that resolves to a real glyph, and the tag renders SVG."""
import pytest

from trophies.models import Job
from trophies.services.element_render import DISCIPLINE_ICON
from trophies.templatetags.job_icons import _ICONS, job_icon

pytestmark = pytest.mark.django_db


def test_every_job_has_a_registered_icon():
    jobs = list(Job.objects.all())
    assert len(jobs) == 25                                  # the 24 specializations + Freelancer
    for job in jobs:
        assert job.icon, f"{job.slug} has no icon assigned"
        assert job.icon in _ICONS, f"{job.slug}'s icon '{job.icon}' is not in the registry"


def test_every_discipline_icon_is_registered():
    # The dossier / character-sheet section headers resolve through the same registry.
    for slug, icon in DISCIPLINE_ICON.items():
        assert icon in _ICONS, f"discipline {slug}'s icon '{icon}' is not in the registry"


def test_job_icon_tag_renders_and_degrades():
    svg = job_icon('swords', 'w-6 h-6')
    assert svg.startswith('<svg') and 'w-6 h-6' in svg and 'viewBox="0 0 24 24"' in svg
    assert job_icon('') == ''                               # blank -> nothing
    assert job_icon('nonexistent') == ''                   # unknown -> nothing
