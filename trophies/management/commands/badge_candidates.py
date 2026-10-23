"""List the franchises and series (IGDB collections) that would make a good badge. Read-only.

A subject is listed when one platform edition (Legacy HD, Ultra HD) would gate two or more games with a live
contract (a remaster or port folds into its original, as it would in a stage), ranked by the average unique
players of its contracted games. Subjects that already have a badge are hidden unless --include-existing.
Rules live in `trophies/services/badge_candidate_service.py`.

    python manage.py badge_candidates
    python manage.py badge_candidates --limit 100 --include-existing

Build the one you pick with `build_badge --franchise <slug>` or `build_badge --series <slug>` (add --franchise to
a series badge to cover the franchise's games too).
"""

from django.core.management.base import BaseCommand

from trophies.services.badge_candidate_service import MIN_EDITION_GAMES, active_editions, badge_candidates

DEFAULT_LIMIT = 50
TYPE_LABELS = {'franchise': 'Franchise', 'collection': 'Series'}


class Command(BaseCommand):
    help = (
        f"List franchises and series with {MIN_EDITION_GAMES}+ live-contracted games gating on one edition, "
        f"ranked by average unique players. Read-only."
    )

    def add_arguments(self, parser):
        parser.add_argument('--limit', type=int, default=DEFAULT_LIMIT,
                            help=f"How many to list (default {DEFAULT_LIMIT}; 0 lists them all).")
        parser.add_argument('--include-existing', action='store_true',
                            help="Also list subjects a badge already points at, marked with *.")

    def handle(self, *args, **options):
        candidates = badge_candidates(include_existing=options['include_existing'])
        if not candidates:
            self.stdout.write(
                f"No franchise or series has {MIN_EDITION_GAMES}+ live-contracted games gating on one edition."
            )
            return
        total = len(candidates)
        if options['limit'] > 0:
            candidates = candidates[:options['limit']]

        editions = active_editions()
        header = ["#", "Type", "Avg players", *(e.name for e in editions), "Games", "Name", "Slug"]
        rows = [
            [
                str(rank),
                TYPE_LABELS.get(c.subject.source_type, c.subject.source_type),
                f"{c.avg_players:,.0f}",
                *(str(c.edition_counts.get(e.key, 0)) for e in editions),
                str(c.game_count),
                f"{c.subject.name}{' *' if c.has_badge else ''}",
                c.subject.slug,
            ]
            for rank, c in enumerate(candidates, start=1)
        ]
        widths = [max(len(row[i]) for row in [header, *rows]) for i in range(len(header))]
        for row in [header, *rows]:
            self.stdout.write("  ".join(cell.ljust(width) for cell, width in zip(row, widths)).rstrip())

        shown = f"Showing {len(rows)} of {total}." if len(rows) < total else f"{total} candidate(s)."
        self.stdout.write(
            f"\n{shown} Edition columns count the live-contracted games that would gate there (remasters and "
            f"ports fold into their original)."
        )
        if options['include_existing']:
            self.stdout.write("* already has a badge.")
        self.stdout.write(
            "Build one: python manage.py build_badge --franchise <slug> | --series <slug> [--franchise <slug>] "
            "| --developer <company slug>"
        )
