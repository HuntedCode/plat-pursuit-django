import re

from django.core.management.base import BaseCommand

from trophies.models import Game, GameFamily, Concept, Trophy, clean_title_field


# Game-specific patterns (some PSN titles include these suffixes)
_GAME_SUFFIX_PATTERNS = [
    re.compile(r'- trophy set', re.IGNORECASE),
    re.compile(r'trophy set', re.IGNORECASE),
    re.compile(r'- trophies', re.IGNORECASE),
    re.compile(r'trophies', re.IGNORECASE),
]


def _clean_game_title(value: str) -> str:
    """Apply shared cleanup plus Game-specific suffix stripping."""
    cleaned = clean_title_field(value)
    for pattern in _GAME_SUFFIX_PATTERNS:
        cleaned = pattern.sub('', cleaned).strip()
    return cleaned


class Command(BaseCommand):
    help = 'Clean titles: strip TM/® symbols, normalize Unicode Roman numerals, remove trophy set suffixes'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Preview changes without writing to the database',
        )

    def _bulk_clean(self, queryset, field_name, clean_fn, extra_setters=None, dry_run=False):
        """Clean a field across all rows in a queryset.

        Args:
            extra_setters: Optional dict of {field_name: value} to set on changed objects.

        Returns the number of updated records.
        """
        update_fields = [field_name] + list((extra_setters or {}).keys())
        batch = []
        count = 0

        for obj in queryset.iterator(chunk_size=1000):
            original = getattr(obj, field_name)
            cleaned = clean_fn(original)
            if cleaned != original:
                if dry_run:
                    self.stdout.write(f"  {original!r}  ->  {cleaned!r}")
                else:
                    setattr(obj, field_name, cleaned)
                    for attr, val in (extra_setters or {}).items():
                        setattr(obj, attr, val)
                    batch.append(obj)
                count += 1

                if not dry_run and len(batch) >= 1000:
                    queryset.model.objects.bulk_update(batch, update_fields)
                    batch = []

        if batch:
            queryset.model.objects.bulk_update(batch, update_fields)

        return count

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        if dry_run:
            self.stdout.write(self.style.WARNING('DRY RUN: no changes will be written\n'))

        # Games (extra suffix stripping + lock_title flag)
        game_count = self._bulk_clean(
            Game.objects.all(), 'title_name', _clean_game_title,
            extra_setters={'lock_title': True}, dry_run=dry_run,
        )
        self.stdout.write(f"{'Would clean' if dry_run else 'Cleaned'} {game_count} games.")

        # Concepts
        concept_count = self._bulk_clean(
            Concept.objects.all(), 'unified_title', clean_title_field, dry_run=dry_run,
        )
        self.stdout.write(f"{'Would clean' if dry_run else 'Cleaned'} {concept_count} concepts.")

        # Trophies
        trophy_count = self._bulk_clean(
            Trophy.objects.all(), 'trophy_name', clean_title_field, dry_run=dry_run,
        )
        self.stdout.write(f"{'Would clean' if dry_run else 'Cleaned'} {trophy_count} trophies.")

        # Game Families
        family_count = self._bulk_clean(
            GameFamily.objects.all(), 'canonical_name', clean_title_field, dry_run=dry_run,
        )
        self.stdout.write(f"{'Would clean' if dry_run else 'Cleaned'} {family_count} game families.")

        total = game_count + concept_count + trophy_count + family_count
        if total == 0:
            self.stdout.write(self.style.SUCCESS('All titles are already clean!'))
        elif dry_run:
            self.stdout.write(self.style.WARNING(f'\n{total} records would be updated. Run without --dry-run to apply.'))
        else:
            self.stdout.write(self.style.SUCCESS(f'\nAll titles cleaned successfully! ({total} records updated)'))
