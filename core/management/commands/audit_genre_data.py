"""
Management command to audit genre and subgenre data across Concepts.

By default, filters to challenge-eligible concepts only:
  - Excludes PP_ stub concepts (no genre data)
  - PS4/PS5 games only (modern consoles)
  - Excludes shovelware-flagged games

Reports coverage stats, unique genres/subgenres with counts, and
genre-to-subgenre relationships.

Usage:
    python manage.py audit_genre_data
    python manage.py audit_genre_data --all
"""
from collections import Counter, defaultdict

from django.core.management.base import BaseCommand
from django.db.models import Q

from trophies.models import Concept


class Command(BaseCommand):
    help = 'Audit genre and subgenre data across Concepts'

    def add_arguments(self, parser):
        parser.add_argument(
            '--all',
            action='store_true',
            help='Include all concepts (skip stub/platform filters)',
        )

    def handle(self, *args, **options):
        include_all = options['all']

        if include_all:
            concepts = Concept.objects.only('genres', 'subgenres')
            filter_label = 'ALL concepts (unfiltered)'
        else:
            concepts = (
                Concept.objects
                .exclude(concept_id__startswith='PP_')
                .exclude(
                    games__shovelware_status__in=[
                        'auto_flagged', 'manually_flagged',
                    ]
                )
                .filter(
                    Q(games__title_platform__contains='PS4')
                    | Q(games__title_platform__contains='PS5')
                )
                .distinct()
                .only('genres', 'subgenres')
            )
            filter_label = 'Eligible concepts (non-stub, PS4/PS5, non-shovelware)'

        total = concepts.count()

        genre_counter = Counter()
        subgenre_counter = Counter()
        genre_to_subgenres = defaultdict(Counter)

        has_genres = 0
        has_subgenres = 0
        empty_genres = 0
        empty_subgenres = 0

        for concept in concepts.iterator(chunk_size=500):
            genres = [g for g in (concept.genres or []) if g]
            subgenres = [sg for sg in (concept.subgenres or []) if sg]

            if genres:
                has_genres += 1
                for g in genres:
                    genre_counter[g] += 1
                    for sg in subgenres:
                        genre_to_subgenres[g][sg] += 1
            else:
                empty_genres += 1

            if subgenres:
                has_subgenres += 1
                for sg in subgenres:
                    subgenre_counter[sg] += 1
            else:
                empty_subgenres += 1

        # --- Coverage Stats ---
        self.stdout.write(
            self.style.MIGRATE_HEADING('\nGenre/Subgenre Data Audit\n')
        )
        self.stdout.write(f'  Filter: {filter_label}')
        self.stdout.write('=' * 60)
        self.stdout.write(
            self.style.MIGRATE_HEADING('\nCoverage Stats\n')
        )
        self.stdout.write(f'  Total concepts:           {total:,}')
        self.stdout.write(f'  With genres:              {has_genres:,} ({self._pct(has_genres, total)})')
        self.stdout.write(f'  Without genres:           {empty_genres:,} ({self._pct(empty_genres, total)})')
        self.stdout.write(f'  With subgenres:           {has_subgenres:,} ({self._pct(has_subgenres, total)})')
        self.stdout.write(f'  Without subgenres:        {empty_subgenres:,} ({self._pct(empty_subgenres, total)})')

        # --- Unique Genres ---
        self.stdout.write('\n' + '=' * 60)
        self.stdout.write(
            self.style.MIGRATE_HEADING(
                f'\nUnique Genres ({len(genre_counter)})\n'
            )
        )
        if genre_counter:
            name_width = max(len(g) for g in genre_counter) + 2
            for genre, count in sorted(genre_counter.items()):
                self.stdout.write(f'  {genre:<{name_width}} {count:>6,} concepts')
        else:
            self.stdout.write(self.style.WARNING('  No genre data found.'))

        # --- Unique Subgenres ---
        self.stdout.write('\n' + '=' * 60)
        self.stdout.write(
            self.style.MIGRATE_HEADING(
                f'\nUnique Subgenres ({len(subgenre_counter)})\n'
            )
        )
        if subgenre_counter:
            name_width = max(len(sg) for sg in subgenre_counter) + 2
            for subgenre, count in sorted(subgenre_counter.items()):
                self.stdout.write(f'  {subgenre:<{name_width}} {count:>6,} concepts')
        else:
            self.stdout.write(self.style.WARNING('  No subgenre data found.'))

        # --- Genre-to-Subgenre Mapping ---
        self.stdout.write('\n' + '=' * 60)
        self.stdout.write(
            self.style.MIGRATE_HEADING('\nGenre-to-Subgenre Mapping\n')
        )
        if genre_to_subgenres:
            for genre in sorted(genre_to_subgenres.keys()):
                subs = genre_to_subgenres[genre]
                self.stdout.write(
                    self.style.SUCCESS(f'  {genre} ({len(subs)} subgenres)')
                )
                for sg, count in subs.most_common():
                    self.stdout.write(f'    {sg}: {count:,}')
                self.stdout.write('')
        else:
            self.stdout.write(
                self.style.WARNING(
                    '  No genre+subgenre overlap data found.'
                )
            )

        self.stdout.write('=' * 60)
        self.stdout.write(self.style.SUCCESS('\nAudit complete.\n'))

    def _pct(self, part, total):
        if total == 0:
            return '0.0%'
        return f'{(part / total) * 100:.1f}%'
