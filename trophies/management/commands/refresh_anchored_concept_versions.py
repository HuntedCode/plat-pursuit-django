"""One-shot janitor to backfill per-version IGDB metadata on anchored Concepts.

Background
----------
The original anchor migration always wrote `IGDBMatch.igdb_id = canonical_id`
on every anchored Concept (both primary and siblings). Each Concept ended up
with the canonical version's IGDB metadata regardless of which specific
version it actually represented (e.g., The Last of Us Remastered Concept got
PS3 original's metadata).

The new model is: each Concept represents one specific IGDB version, with
THAT version's metadata. Family link still goes through canonical resolution.
This command walks every anchored Concept, determines its intended raw IGDB
id from its Games' match results, refreshes the IGDBMatch with that raw
version's data, and surfaces mixed-version Concepts for split.

What it does per Concept
------------------------
1. For each Game in the Concept, re-run `IGDBService.match_game` to determine
   the Game's `raw_igdb_id` (the specific IGDB version it represents).
2. Group Games by raw_igdb_id.
3. Single-raw Concept (the common case):
   - Refresh IGDBMatch with raw IGDB data so igdb_id, cover, summary,
     companies, etc. reflect the specific version.
4. Multi-raw Concept (split needed):
   - Move each minority raw group to its own Concept (existing sibling if
     one matches that raw, or a new sibling allocated for that raw).
   - Refresh each touched Concept's IGDBMatch.
5. No-match Concept (all Games NO_MATCH from match_game):
   - Skip; nothing to refresh.

Idempotent. Re-running on already-correct Concepts is a no-op (still
re-fetches IGDB data — pay the API cost only when needed via --skip-correct).

Usage
-----
    python manage.py refresh_anchored_concept_versions --dry-run
    python manage.py refresh_anchored_concept_versions --concept-id 1009
    python manage.py refresh_anchored_concept_versions --limit 5
    python manage.py refresh_anchored_concept_versions --api-delay 0.5
"""
import time
from collections import defaultdict

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from trophies.models import Concept
from trophies.services.concept_anchor_service import (
    allocate_sibling_concept_id,
    build_family_raw_igdb_map,
)
from trophies.services.igdb_service import IGDBService


class Command(BaseCommand):
    help = (
        'Refresh anchored Concepts so their IGDBMatch reflects the specific '
        'IGDB version each Concept represents (rather than canonical). '
        'Splits Concepts that contain Games of multiple versions. '
        'WARNING: cost scales as match_game per Game (each match_game can '
        'make multiple IGDB API calls). For a Concept with 5 Games at '
        '--api-delay 0.5, that\'s ~10s. Run --dry-run first to scope.'
    )

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true')
        parser.add_argument(
            '--concept-id', type=str, default=None,
            help='Only act on this specific concept_id.',
        )
        parser.add_argument(
            '--limit', type=int, default=None,
            help='Process at most N anchored Concepts.',
        )
        parser.add_argument(
            '--api-delay', type=float, default=0.5,
            help='Seconds to sleep after each IGDB call.',
        )

    def handle(self, *args, **options):
        self.dry_run = options['dry_run']
        self.api_delay = options['api_delay']

        # Concepts considered "anchored" = concept_id matches family pattern
        # (bare integer for primaries OR integer-dash-integer for siblings).
        qs = Concept.objects.filter(
            concept_id__regex=r'^\d+(-\d+)?$',
            anchor_migration_completed_at__isnull=False,
        )
        if options['concept_id']:
            qs = qs.filter(concept_id=options['concept_id'])
        qs = qs.order_by('pk')
        if options['limit']:
            qs = qs[:options['limit']]

        concepts = list(qs.select_related('igdb_match').prefetch_related('games'))
        if not concepts:
            self.stdout.write(self.style.SUCCESS('No anchored Concepts to process.'))
            return

        refreshed = 0
        split_count = 0
        skipped_no_match = 0
        errors = 0
        start = time.time()

        for concept in concepts:
            try:
                outcome = self._process_concept(concept)
                if outcome == 'refreshed':
                    refreshed += 1
                elif outcome == 'split':
                    split_count += 1
                elif outcome == 'no_match':
                    skipped_no_match += 1
            except Exception as exc:
                errors += 1
                self.stderr.write(self.style.ERROR(
                    f'  {concept.concept_id!r}: failed — {exc}'
                ))

        elapsed = time.time() - start
        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS('=== Summary ==='))
        self.stdout.write(f'  Elapsed:               {elapsed:.1f}s')
        self.stdout.write(f'  Concepts processed:    {len(concepts)}')
        self.stdout.write(f'  Refreshed:             {refreshed}')
        self.stdout.write(f'  Split (multi-version): {split_count}')
        self.stdout.write(f'  Skipped (no_match):    {skipped_no_match}')
        self.stdout.write(f'  Errors:                {errors}')
        if self.dry_run:
            self.stdout.write(self.style.WARNING(
                '\n[DRY RUN] No writes were made.'
            ))

    def _process_concept(self, concept):
        """Returns one of: 'refreshed', 'split', 'no_match', 'noop'."""
        # Explicit pk ordering so the multi-version split's keeper-raw
        # fallback (first raw encountered) is stable across re-runs.
        games = list(concept.games.order_by('pk'))
        if not games:
            self.stdout.write(
                f'  {concept.concept_id!r} (pk={concept.pk}): no games — noop'
            )
            return 'noop'

        # Determine each game's raw_igdb_id via match_game.
        by_raw = defaultdict(list)
        for game in games:
            try:
                result = IGDBService.match_game(game)
            except Exception:
                self.stderr.write(self.style.WARNING(
                    f'    match_game failed for game pk={game.pk}; skipping'
                ))
                continue
            if not result:
                continue
            by_raw[result['raw_igdb_id']].append((game, result))
            if self.api_delay:
                time.sleep(self.api_delay)

        if not by_raw:
            self.stdout.write(
                f'  {concept.concept_id!r} (pk={concept.pk}): all '
                f'{len(games)} game(s) NO_MATCH — skipping'
            )
            return 'no_match'

        if len(by_raw) == 1:
            return self._refresh_single_version(concept, by_raw)
        return self._split_multi_version(concept, by_raw)

    def _refresh_single_version(self, concept, by_raw):
        raw_id, group = next(iter(by_raw.items()))
        raw_data = group[0][1]['igdb_data']

        # Check if concept_id matches the intended raw_id. If not, rename
        # by creating a Concept at the correct slot and absorbing the old.
        # We can't simply update concept_id because it's part of the unique
        # constraint and is FK'd by 20+ models — let Concept.absorb handle
        # the data migration via the established pattern.
        expected_concept_id = str(raw_id)
        bare_concept_id = concept.concept_id
        rename_needed = bare_concept_id != expected_concept_id

        if rename_needed:
            self.stdout.write(
                f'  {bare_concept_id!r}: RENAME → {expected_concept_id!r} '
                f'(IGDBMatch.igdb_id should be {raw_id}, concept_id was using '
                f'canonical-based naming)'
            )
        else:
            current_match = getattr(concept, 'igdb_match', None)
            current_id = current_match.igdb_id if current_match else None
            if current_id == raw_id:
                self.stdout.write(
                    f'  {bare_concept_id!r}: already at raw IGDB {raw_id} '
                    f'— refreshing metadata anyway (clean slate)'
                )
            else:
                self.stdout.write(
                    f'  {bare_concept_id!r}: refreshing '
                    f'IGDBMatch.igdb_id {current_id} → {raw_id}'
                )

        if self.dry_run:
            return 'refreshed'

        with transaction.atomic():
            if rename_needed:
                # Allocate Concept at correct slot. If the slot is taken by
                # an unrelated Concept (collision), bail. If taken by a same-
                # family Concept, use same-raw-suffix to avoid trampling.
                target = self._rename_concept(
                    concept, expected_concept_id, raw_data,
                )
                if target is None:
                    self.stderr.write(self.style.ERROR(
                        f'    Failed to rename {bare_concept_id!r}; left as-is'
                    ))
                    return 'noop'
                # Refresh target with the raw data (process_match was called
                # inside _rename_concept; this is the post-absorb refresh).
                IGDBService.process_match(
                    target, raw_data, confidence=1.0, method='manual',
                )
            else:
                IGDBService.process_match(
                    concept, raw_data, confidence=1.0, method='manual',
                )
        return 'refreshed'

    def _rename_concept(self, old_concept, new_concept_id, raw_data):
        """Move all Games from old_concept to a Concept at new_concept_id.

        Creates target if needed (with raw_data's metadata), moves games,
        absorbs old_concept's social data via Game.add_concept's cascade,
        which auto-deletes old_concept when its last Game leaves.

        Returns the target Concept, or None on collision.
        """
        target = Concept.objects.filter(concept_id=new_concept_id).first()
        if target is None:
            target = Concept.objects.create(
                concept_id=new_concept_id,
                unified_title=raw_data.get('name', ''),
            )
            IGDBService.process_match(
                target, raw_data, confidence=1.0, method='manual',
            )
            target.anchor_migration_completed_at = timezone.now()
            target.save(update_fields=['anchor_migration_completed_at'])

        # Move every Game from old → target. The last move triggers
        # Concept.absorb + delete via add_concept's cascade.
        for game in list(old_concept.games.all()):
            game.add_concept(target, force=True)

        # Defensive: if cascade didn't fire (we've seen this before), do it
        # explicitly. Safe — refresh_from_db raises if old_concept is gone.
        try:
            stale = Concept.objects.get(pk=old_concept.pk)
        except Concept.DoesNotExist:
            stale = None
        if stale and stale.games.count() == 0:
            target.absorb(stale)
            stale.delete()

        return target

    def _split_multi_version(self, concept, by_raw):
        # Multiple raw IGDB ids in one Concept — needs split. Determine which
        # raw "owns" this Concept's slot: if concept_id is bare integer and
        # one of the raws matches it, that's the keeper. Otherwise pick the
        # first raw (deterministic since by_raw insertion order = iteration
        # order over games which we control).
        canonical_id = self._canonical_for_concept(concept, by_raw)
        if canonical_id is None:
            self.stdout.write(self.style.WARNING(
                f'  {concept.concept_id!r}: cannot determine canonical id — skipping'
            ))
            return 'noop'

        keeper_raw = None
        try:
            slot_id = int(concept.concept_id)
            if slot_id in by_raw:
                keeper_raw = slot_id
        except (ValueError, TypeError):
            pass
        if keeper_raw is None:
            keeper_raw = next(iter(by_raw.keys()))

        self.stdout.write(self.style.WARNING(
            f'  {concept.concept_id!r}: SPLIT — {len(by_raw)} versions '
            f'{sorted(by_raw.keys())}, keeping raw {keeper_raw}, moving '
            f'{sum(len(g) for r, g in by_raw.items() if r != keeper_raw)} '
            f'game(s) to siblings'
        ))

        if self.dry_run:
            return 'split'

        with transaction.atomic():
            # Refresh keeper's IGDBMatch to the keeper raw's data.
            keeper_data = by_raw[keeper_raw][0][1]['igdb_data']
            IGDBService.process_match(
                concept, keeper_data, confidence=1.0, method='manual',
            )

            raw_map = build_family_raw_igdb_map(
                canonical_id, exclude_concept_pk=concept.pk,
            )

            for raw_id, group in by_raw.items():
                if raw_id == keeper_raw:
                    continue
                target = raw_map.get(raw_id)
                if target is None:
                    # Natural slot for this version: str(raw_id). Fall back
                    # to same-raw-suffix if taken (rare).
                    natural = str(raw_id)
                    if Concept.objects.filter(concept_id=natural).exists():
                        target_concept_id = allocate_sibling_concept_id(raw_id)
                    else:
                        target_concept_id = natural
                    raw_data = group[0][1]['igdb_data']
                    target = Concept.objects.create(
                        concept_id=target_concept_id,
                        unified_title=raw_data.get('name', ''),
                    )
                    IGDBService.process_match(
                        target, raw_data, confidence=1.0, method='manual',
                    )
                    target.anchor_migration_completed_at = timezone.now()
                    target.save(update_fields=['anchor_migration_completed_at'])
                    raw_map[raw_id] = target
                for game, _ in group:
                    self.stdout.write(
                        f'    pk={game.pk} → {target.concept_id!r}'
                    )
                    game.add_concept(target, force=True)
        return 'split'

    def _canonical_for_concept(self, concept, by_raw):
        """Derive the canonical IGDB id for the Concept.

        Prefer the family link if the Concept already has one. Else derive
        from any game's match (canonical_id should agree across all games
        if they're in the same family — which is the case for an anchored
        Concept).
        """
        if concept.family_id and concept.family.igdb_id:
            return concept.family.igdb_id
        for group in by_raw.values():
            if group:
                return group[0][1]['canonical_igdb_id']
        return None
