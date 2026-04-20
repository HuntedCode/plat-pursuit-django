"""Backfill GameFamily records from accepted IGDB matches.

Post Phase 2.6, GameFamily is keyed on IGDB id: one family per IGDB game,
holding all concepts (regional/platform variants, stubs, etc.) that matched
to that game. The live sync / enrichment path populates this automatically
going forward. This command does the one-shot historical pass against the
existing accepted matches in the DB.

Intended run order:
    1. Deploy Phase 2.6 (or later) and migrate.
    2. Run the Phase 3 rematch pass so every concept that CAN match IGDB DOES.
    3. Then run this command. That ordering maximizes family coverage because
       this command only groups concepts whose IGDBMatch is accepted or
       auto_accepted; concepts the rematch promotes from pending/no_match to
       accepted are included in that state.

--dry-run reports the projected state without writing.
"""
import time
from collections import defaultdict

from django.core.management.base import BaseCommand

from trophies.models import Concept, GameFamily, IGDBMatch


class Command(BaseCommand):
    help = (
        "Walk every accepted/auto_accepted IGDBMatch, group by igdb_id, and "
        "link each concept to its IGDB-id-keyed GameFamily (creating the "
        "family on first encounter of each igdb_id). Idempotent — safe to "
        "re-run."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run', action='store_true',
            help='Report projected families and link counts without writing.',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']

        qs = IGDBMatch.objects.filter(
            status__in=['accepted', 'auto_accepted'],
            igdb_id__isnull=False,
        ).select_related('concept', 'concept__family')

        total = qs.count()
        if total == 0:
            self.stdout.write(self.style.SUCCESS('No accepted IGDBMatch rows. Nothing to do.'))
            return

        self.stdout.write(f'Scanning {total} accepted/auto_accepted IGDBMatch row(s)...')

        # Group matches by igdb_id
        by_igdb_id = defaultdict(list)
        for match in qs.iterator(chunk_size=500):
            by_igdb_id[match.igdb_id].append(match)

        start = time.time()
        families_created = 0
        families_touched = 0
        concepts_linked = 0
        concepts_already_linked = 0
        concepts_migrated = 0  # moved from another family to the correct one
        orphans_deleted = 0

        for igdb_id, matches in by_igdb_id.items():
            # Canonical name comes from the most confident / richest payload
            canonical_name = next(
                (m.igdb_name for m in matches if m.igdb_name),
                f'IGDB #{igdb_id}',
            )

            if dry_run:
                existing = GameFamily.objects.filter(igdb_id=igdb_id).first()
                family = existing or GameFamily(canonical_name=canonical_name, igdb_id=igdb_id, is_verified=True)
                if not existing:
                    families_created += 1
            else:
                family, created = GameFamily.objects.get_or_create(
                    igdb_id=igdb_id,
                    defaults={'canonical_name': canonical_name, 'is_verified': True},
                )
                if created:
                    families_created += 1
                elif not family.admin_notes and family.canonical_name != canonical_name:
                    family.canonical_name = canonical_name
                    family.save(update_fields=['canonical_name'])

            touched_family = False
            for match in matches:
                concept = match.concept
                if concept.family_id == (family.pk if not dry_run else None) and not dry_run:
                    # Concept already correctly linked
                    if concept.family_id:
                        concepts_already_linked += 1
                    continue

                old_family = concept.family if concept.family_id else None

                if dry_run:
                    if concept.family_id:
                        if old_family and old_family.igdb_id == igdb_id:
                            concepts_already_linked += 1
                            continue
                        concepts_migrated += 1
                    else:
                        concepts_linked += 1
                    touched_family = True
                    continue

                concept.family = family
                concept.save(update_fields=['family'])
                touched_family = True
                if old_family is None:
                    concepts_linked += 1
                elif old_family.pk != family.pk:
                    concepts_migrated += 1
                    # Clean up old family if emptied
                    if old_family.concepts.count() == 0:
                        old_family.delete()
                        orphans_deleted += 1
                else:
                    concepts_already_linked += 1

            if touched_family:
                families_touched += 1

        elapsed = time.time() - start
        self.stdout.write('')
        self.stdout.write(f'Scan complete in {elapsed:.1f}s.')
        self.stdout.write(f'  Unique igdb_ids processed:    {len(by_igdb_id)}')
        self.stdout.write(f'  Families created:             {families_created}')
        self.stdout.write(f'  Families touched:             {families_touched}')
        self.stdout.write(f'  Concepts linked (new):        {concepts_linked}')
        self.stdout.write(f'  Concepts migrated (moved):    {concepts_migrated}')
        self.stdout.write(f'  Concepts already correct:     {concepts_already_linked}')
        self.stdout.write(f'  Orphan families deleted:      {orphans_deleted}')

        if dry_run:
            self.stdout.write(self.style.WARNING(
                '\n[DRY RUN] No writes were made. Re-run without --dry-run to apply.'
            ))
        else:
            self.stdout.write(self.style.SUCCESS('\nBackfill complete.'))
