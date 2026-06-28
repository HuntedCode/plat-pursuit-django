"""Retire milestone criteria-type(s): hide them + remove the titles they granted.

Retiring sets Milestone.is_active=False for the given criteria-type(s) -- removing them from
the milestones page and stopping new awards -- and DELETES the UserTitle grants those
milestones produced (auto-unequipping anyone displaying one). Earned UserMilestone records
are PRESERVED.

Destructive on UserTitle data, so it is a DRY RUN by default; pass --apply to commit.

    python manage.py retire_milestones checklist_upvotes review_count review_helpful_count
    python manage.py retire_milestones checklist_upvotes review_count review_helpful_count --apply
"""
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from trophies.models import Milestone, UserMilestone, UserTitle
from trophies.services.milestone_service import retire_milestones


class Command(BaseCommand):
    help = "Retire milestone criteria-type(s): hide them + remove granted titles. Dry-run unless --apply."

    def add_arguments(self, parser):
        parser.add_argument(
            'criteria_types', nargs='+',
            help="criteria_type value(s) to retire, e.g. checklist_upvotes review_count review_helpful_count",
        )
        parser.add_argument(
            '--apply', action='store_true',
            help="Commit the changes. Without it, the command only reports what it would do.",
        )

    def handle(self, *args, **options):
        criteria_types = options['criteria_types']
        apply = options['apply']

        milestones = Milestone.objects.filter(criteria_type__in=criteria_types)
        matched_types = set(milestones.values_list('criteria_type', flat=True))
        unknown = [ct for ct in criteria_types if ct not in matched_types]
        if unknown:
            self.stdout.write(self.style.WARNING(
                f"No milestones found for criteria_type(s): {', '.join(unknown)}"))

        if not milestones.exists():
            raise CommandError("No milestones matched the given criteria_type(s); nothing to do.")

        milestone_ids = list(milestones.values_list('id', flat=True))
        active_to_retire = milestones.filter(is_active=True).count()
        already_retired = len(milestone_ids) - active_to_retire

        titles_qs = UserTitle.objects.filter(source_type='milestone', source_id__in=milestone_ids)
        titles_to_remove = titles_qs.count()
        users_unequipped = titles_qs.filter(is_displayed=True).count()
        earned_preserved = UserMilestone.objects.filter(milestone_id__in=milestone_ids).count()

        self.stdout.write("")
        self.stdout.write(f"Criteria-types:        {', '.join(sorted(matched_types))}")
        self.stdout.write(f"Milestones matched:    {len(milestone_ids)} "
                          f"({active_to_retire} to retire, {already_retired} already retired)")
        self.stdout.write(f"User titles to remove: {titles_to_remove} "
                          f"(unequipping {users_unequipped} user(s) currently displaying one)")
        self.stdout.write(f"Earned records kept:   {earned_preserved} UserMilestone row(s) preserved")
        self.stdout.write("")

        if not apply:
            self.stdout.write(self.style.WARNING(
                "DRY RUN -- no changes made. Re-run with --apply to commit."))
            return

        with transaction.atomic():
            retired, removed = retire_milestones(milestones)
        self.stdout.write(self.style.SUCCESS(
            f"Done. Retired {retired} milestone(s); removed {removed} granted title(s)."))
