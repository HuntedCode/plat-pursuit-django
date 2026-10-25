"""Build a badge series' stages from its franchise / collection / developer.

One stage per origin game (ports, remasters, remakes and editions share their original's stage), titled
with the origin game's name. Developer badges are ordered by studio (the developer first, then its
studios A-Z) and A-Z within each; every other badge runs oldest origin release first. Rules and edge cases
live in `trophies/services/badge_stage_service.py`.

A series that already has stages is refused unless --append, which adds only the games no stage covers
yet: a new version of a game that has a stage joins that stage, anything else becomes a new stage after the
last one. Existing stages only ever gain concepts. A game whose stage holds it inside a bundle is listed for
you to add by hand, never added automatically. Bundles are always manual.

    python manage.py generate_badge_stages <series_slug> --dry-run
    python manage.py generate_badge_stages <series_slug>
    python manage.py generate_badge_stages <series_slug> --append
"""

from django.core.management.base import BaseCommand, CommandError
from django.db import IntegrityError

from trophies.models import BadgeSeries
from trophies.services.badge_stage_service import StagePlanError, plan_stages, write_stages


def format_plan(plan):
    """One block per planned stage: its number, title and studio, then each game."""
    lines = []
    for planned in plan:
        studio = f"  [{planned.studio[1]}]" if planned.studio else ""
        if planned.bundled:
            head = (
                f"Stage {planned.existing.stage_number} (existing, uses a bundle or trophy group): ADD BY "
                f"HAND, not written. Put these in the bundle or the stage yourself"
            )
        elif planned.existing is not None:
            head = f"Stage {planned.existing.stage_number} (existing): add to \"{planned.existing.title}\""
        else:
            head = f"Stage {planned.stage_number}: {planned.title}"
        lines.append(f"{head}{studio}")
        for concept in planned.concepts:
            lines.append(f"    - {concept.unified_title or concept.pk}  (concept pk {concept.pk})")
    return "\n".join(lines)


class Command(BaseCommand):
    # build_badge runs this then convert_series_to_groups itself, so the "Next:" it would print is done already.
    stealth_options = ('skip_next_steps',)
    help = (
        "Build a badge series' stages from its franchise/collection/developer: one stage per origin game. "
        "Refuses a series that has stages unless --append."
    )

    def add_arguments(self, parser):
        parser.add_argument('series_slug', help="The BadgeSeries.series_slug to build stages for.")
        parser.add_argument('--append', action='store_true',
                            help="Series already has stages: add only the games none of them cover.")
        parser.add_argument('--dry-run', action='store_true', help="Print the plan; write nothing.")

    def handle(self, *args, **options):
        slug = options['series_slug']
        series = (
            BadgeSeries.objects.select_related('franchise', 'collection', 'developer')
            .filter(series_slug=slug).first()
        )
        if series is None:
            raise CommandError(f'No badge series with series_slug "{slug}".')

        try:
            plan = plan_stages(series, append=options['append'])
        except StagePlanError as exc:
            raise CommandError(str(exc)) from exc

        if not plan:
            self.stdout.write(self.style.SUCCESS(
                f'Nothing to add: every game of "{slug}" is already in a stage.'
            ))
            return

        self.stdout.write(format_plan(plan))
        new_count = sum(1 for p in plan if p.existing is None)
        extended = {p.existing.pk for p in plan if p.existing is not None and not p.bundled}
        manual = {p.existing.pk for p in plan if p.bundled}
        summary = f"{new_count} new stage(s)"
        if extended:
            summary += f", {len(extended)} existing stage(s) extended"
        if manual:
            summary += f" ({len(manual)} bundle stage(s) left for you to update by hand)"

        if not new_count and not extended:
            self.stdout.write(self.style.WARNING(
                f"Nothing written: every addition is for a bundle stage ({len(manual)}), listed above."
            ))
            return
        if options['dry_run']:
            self.stdout.write(self.style.WARNING(f"Dry run: would write {summary}. Nothing written."))
            return

        try:
            write_stages(series, plan)
        except IntegrityError as exc:
            raise CommandError(
                f'Stages for "{slug}" changed while this ran (a stage number was taken). Nothing was '
                f'written; run it again.'
            ) from exc
        self.stdout.write(self.style.SUCCESS(f'Wrote {summary} for "{slug}".'))
        if not options.get('skip_next_steps'):
            self._print_next_steps(series)

    def _print_next_steps(self, series):
        slug = series.series_slug
        steps = [
            f"python manage.py convert_series_to_groups {slug}   "
            f"(creates editions for any platform group the stages now reach; safe to re-run)",
        ]
        if series.group_badges.filter(is_live=True).exists():
            steps.append(f"python manage.py evaluate_badges --series {slug}   (awards the live editions)")
        else:
            steps.append(
                f"No edition is live yet: mark one live in the GroupBadge admin, then run "
                f"python manage.py evaluate_badges --series {slug}"
            )
        self.stdout.write("Next:\n" + "\n".join(f"  {step}" for step in steps))
