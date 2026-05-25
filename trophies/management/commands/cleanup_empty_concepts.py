"""One-off janitor for orphan Concepts with zero Games.

Concepts are normally absorb-and-deleted by `Game.add_concept` when the last
Game leaves. Edge cases observed during the anchor migration produced orphan
empty Concepts — `232911` after a manual_anchor_selected run, the `PP_5232`
stub spotted during a batch — where the cascade didn't fire and a Concept
sits in the DB with no Games linked.

This command finds those, optionally previews them, and deletes them. Social
data on these empty Concepts is migrated to a target Concept if one exists
at the same canonical IGDB id, otherwise dropped (the empty Concept never
had Games whose progress could anchor user-facing social data, so the
risk surface is small).

Usage:
    python manage.py cleanup_empty_concepts --dry-run        # report only
    python manage.py cleanup_empty_concepts                  # actually delete

Filters:
    --concept-id <id>   only act on this specific concept_id
    --pp-only           only delete PP_* stubs (skips legacy PSN-style ids)
    --limit N           process at most N
"""
from django.core.management.base import BaseCommand
from django.db.models import Count
from django.db import transaction

from trophies.models import Concept
from trophies.services.igdb_service import IGDBService


class Command(BaseCommand):
    help = (
        'Delete Concepts with zero Games. Migrates social data to a '
        'canonical-IGDB-anchored target Concept when one exists, otherwise '
        'drops the empty row.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run', action='store_true',
            help='Report what would be deleted without writing.',
        )
        parser.add_argument(
            '--concept-id', type=str, default=None,
            help='Only act on this specific concept_id (e.g. "232911").',
        )
        parser.add_argument(
            '--pp-only', action='store_true',
            help='Only delete PP_* stubs; skip legacy PSN-style ids.',
        )
        parser.add_argument(
            '--limit', type=int, default=None,
            help='Process at most N empty Concepts.',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']

        qs = Concept.objects.annotate(
            game_count=Count('games')
        ).filter(game_count=0)

        if options['concept_id']:
            qs = qs.filter(concept_id=options['concept_id'])
        if options['pp_only']:
            qs = qs.filter(concept_id__startswith='PP_')
        if options['limit']:
            qs = qs[:options['limit']]

        empties = list(qs.select_related('igdb_match'))
        if not empties:
            self.stdout.write(self.style.SUCCESS('No empty Concepts found.'))
            return

        self.stdout.write(
            f'Found {len(empties)} empty Concept(s):'
        )

        deleted = 0
        absorbed_into = 0
        errors = 0
        for c in empties:
            match = getattr(c, 'igdb_match', None)
            target = None
            target_id_label = '(none)'
            if match and match.igdb_id:
                canonical = IGDBService._resolve_canonical_igdb_id(
                    match.raw_response or {}, match.igdb_id
                )
                target = Concept.objects.filter(
                    concept_id=str(canonical)
                ).first()
                if target and target.pk != c.pk:
                    target_id_label = repr(target.concept_id)
                else:
                    target = None  # don't absorb into self
            self.stdout.write(
                f'  {c.concept_id!r} (pk={c.pk}, title={c.unified_title!r}) '
                f'→ absorb into: {target_id_label}'
            )
            if dry_run:
                continue
            try:
                with transaction.atomic():
                    if target is not None:
                        target.absorb(c)
                        absorbed_into += 1
                    c.delete()
                    deleted += 1
            except Exception as exc:
                errors += 1
                self.stderr.write(self.style.ERROR(
                    f'    failed for {c.concept_id!r}: {exc}'
                ))

        self.stdout.write('')
        if dry_run:
            self.stdout.write(self.style.WARNING(
                f'[DRY RUN] Would delete {len(empties)} empty Concept(s). '
                f'Re-run without --dry-run to apply.'
            ))
        else:
            self.stdout.write(self.style.SUCCESS(
                f'Done. Deleted {deleted}, absorbed-into-target {absorbed_into}, '
                f'errors {errors}.'
            ))
