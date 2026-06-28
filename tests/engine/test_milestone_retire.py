"""Retiring milestone criteria-types.

Retiring hides a milestone (is_active=False, excluded from the page + no longer awarded) and
removes the titles it granted (auto-unequipping), while PRESERVING earned UserMilestone
records. Covers the service helper, the active() manager filter, and the dry-run/apply command.
"""
import pytest
from django.core.management import call_command

from trophies.models import Milestone, UserMilestone, UserTitle, Title
from trophies.services.milestone_service import retire_milestones
from tests.factories import ProfileFactory

pytestmark = pytest.mark.django_db


def _milestone(name, ctype, target=10, title=None):
    return Milestone.objects.create(
        name=name, criteria_type=ctype, criteria_details={'target': target}, title=title,
    )


def test_retire_hides_milestone_removes_titles_preserves_earned():
    profile = ProfileFactory()
    title = Title.objects.create(name='Reviewer')
    keep = _milestone('Rate 10 games', 'rating_count')
    retire = _milestone('Write 10 reviews', 'review_count', title=title)

    UserMilestone.objects.create(profile=profile, milestone=retire)          # earned record
    UserTitle.objects.create(profile=profile, title=title, source_type='milestone',
                             source_id=retire.id, is_displayed=True)          # granted + equipped

    retired, removed = retire_milestones(Milestone.objects.filter(criteria_type='review_count'))

    assert (retired, removed) == (1, 1)
    retire.refresh_from_db(); keep.refresh_from_db()
    assert retire.is_active is False
    assert keep.is_active is True                                             # untouched
    assert not UserTitle.objects.filter(source_type='milestone', source_id=retire.id).exists()  # removed (unequipped)
    assert UserMilestone.objects.filter(profile=profile, milestone=retire).exists()              # earned record kept


def test_active_manager_excludes_retired():
    _milestone('Active', 'plat_count')
    retired = _milestone('Retired', 'review_count')
    retire_milestones(Milestone.objects.filter(id=retired.id))

    active_ids = set(Milestone.objects.active().values_list('id', flat=True))
    assert retired.id not in active_ids
    assert Milestone.objects.active().count() == 1


def test_command_dry_run_then_apply():
    profile = ProfileFactory()
    title = Title.objects.create(name='Checklist Star')
    m = _milestone('Checklist 10', 'checklist_upvotes', title=title)
    UserTitle.objects.create(profile=profile, title=title, source_type='milestone', source_id=m.id)

    call_command('retire_milestones', 'checklist_upvotes')          # dry run: no changes
    m.refresh_from_db()
    assert m.is_active is True
    assert UserTitle.objects.filter(source_id=m.id).exists()

    call_command('retire_milestones', 'checklist_upvotes', '--apply')
    m.refresh_from_db()
    assert m.is_active is False
    assert not UserTitle.objects.filter(source_type='milestone', source_id=m.id).exists()
