"""Migrate Concepts to IGDB-anchored identity.

Each batch processes one GameFamily (its Concepts and their Games) inside a
single transaction. Per Game we run `IGDBService.match_game` to get its
canonical IGDB id, group by that id, and then for each group either:

  - auto-move the Games to a Concept whose `concept_id = str(canonical_id)`
    (creating the target if needed, refreshing its IGDBMatch against the
    canonical so media is captured), or
  - flag the Game with a `ConceptJoinReview` entry when the trophy-metric
    fingerprint diverges from the target's existing Games, the identity
    cross-check trips a soft signal, or a concept_id collision is detected
    (an existing Concept owns `str(canonical_id)` but isn't IGDB-resolved
    to the same canonical).

Source Concepts emptied by all-Games-moved-out get auto-deleted via the
existing `Game.add_concept` -> `Concept.absorb` cascade. Source Concepts
left with NO_MATCH-only Games stay in place (no timestamp) and get
re-evaluated on a future run when IGDB enrichment may have improved.

If a source Concept's Games split across multiple canonical IGDB ids, the
whole Concept is escalated to the review queue (one ConceptJoinReview per
Game) and not auto-split, because the social-data routing decision is
unsafe to automate.

Resumable: `Concept.anchor_migration_completed_at` is the per-Concept skip
flag. A Family is "done" when none of its Concepts are null on that field.

Usage:
    python manage.py anchor_concepts [--family N] [--orphans] [--limit N]
                                     [--dry-run] [--api-delay 0.5]

Note: there's no concept_id collision pre-flight pass. PSN concept_ids that
happen to be bare integers exist throughout the DB and are typically far
larger than any IGDB game id, so a "look for bare-integer concept_ids" scan
would refuse to start on legitimate state. The mid-batch collision check
inside `_get_or_create_target_concept` is the actual safety net: when the
migration tries to create a target Concept at `str(canonical_id)` and finds
an existing Concept already owning that PK, it verifies the existing one's
IGDBMatch canonical-resolves to the same id. If not, the Game is flagged
with `concept_id_collision` and the batch moves on.
"""
import time
from collections import defaultdict

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from trophies.models import (
    Concept, ConceptJoinReview, GameFamily,
)
from trophies.services.concept_anchor_service import (
    compare_trophy_metrics, identity_cross_check, trophy_fingerprint,
)
from trophies.services.igdb_service import IGDBService


class Command(BaseCommand):
    help = (
        "Migrate Concepts to IGDB-anchored identity. Batched by GameFamily; "
        "resumable via Concept.anchor_migration_completed_at. Flags ambiguous "
        "placements to ConceptJoinReview for staff resolution."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--family', type=int, default=None,
            help='Process just this GameFamily id (skips orphan-Concept pass).',
        )
        parser.add_argument(
            '--orphans', action='store_true',
            help='Process only Family-less Concepts (one mini-batch per Concept).',
        )
        parser.add_argument(
            '--limit', type=int, default=None,
            help='Stop after N families processed (orphan pass is unbounded).',
        )
        parser.add_argument(
            '--dry-run', action='store_true',
            help='Run matching + decision logic but do not write to the database.',
        )
        parser.add_argument(
            '--api-delay', type=float, default=0.5,
            help='Seconds to sleep after each IGDB canonical-data fetch.',
        )

    def handle(self, *args, **options):
        self.dry_run = options['dry_run']
        self.api_delay = options['api_delay']
        # Per-run cache: canonical_igdb_id -> full IGDB payload. Reused across
        # Concepts in the same Family batch (and across batches in the same
        # run) so we only fetch each canonical entry once.
        self._canonical_data_cache = {}

        # Counters
        self.batches_processed = 0
        self.concepts_processed = 0
        self.concepts_anchored = 0
        self.concepts_deferred_no_match = 0
        self.concepts_deferred_split = 0
        self.concepts_deferred_collision = 0
        self.games_moved = 0
        self.games_flagged_for_review = 0
        self.targets_created = 0
        self.targets_reused = 0
        self.start_time = time.time()

        if options['family'] is not None:
            families = GameFamily.objects.filter(pk=options['family'])
            self._process_families(families, limit=None)
        elif options['orphans']:
            self._process_orphans(limit=options['limit'])
        else:
            families = GameFamily.objects.order_by('pk')
            self._process_families(families, limit=options['limit'])
            if options['limit'] is None:
                self._process_orphans(limit=None)

        self._print_summary()

    # ------------------------------------------------------------------
    # Top-level iteration
    # ------------------------------------------------------------------

    def _process_families(self, families_qs, limit):
        count = 0
        for family in families_qs.iterator(chunk_size=50):
            if limit is not None and count >= limit:
                break
            if self._family_is_done(family):
                continue
            self._process_family_batch(family)
            count += 1

    def _family_is_done(self, family):
        return not family.concepts.filter(
            anchor_migration_completed_at__isnull=True
        ).exists()

    def _process_family_batch(self, family):
        self.stdout.write(self.style.HTTP_INFO(
            f'\n=== Family #{family.pk} "{family.canonical_name}" '
            f'(igdb_id={family.igdb_id}) ==='
        ))
        if self.dry_run:
            self._run_batch_logic(family.concepts.filter(
                anchor_migration_completed_at__isnull=True
            ))
        else:
            with transaction.atomic():
                self._run_batch_logic(family.concepts.filter(
                    anchor_migration_completed_at__isnull=True
                ))
        self.batches_processed += 1

    def _process_orphans(self, limit):
        qs = Concept.objects.filter(
            family__isnull=True,
            anchor_migration_completed_at__isnull=True,
        ).order_by('pk')
        self.stdout.write(self.style.HTTP_INFO(
            f'\n=== Orphan Concepts (no Family) ==='
        ))
        count = 0
        for source_concept in qs.iterator(chunk_size=50):
            if limit is not None and count >= limit:
                break
            if self.dry_run:
                self._process_concept(source_concept)
            else:
                with transaction.atomic():
                    self._process_concept(source_concept)
            count += 1

    def _run_batch_logic(self, concepts_qs):
        # Order by game count descending so the busiest Concepts process first
        # (their movement cascades resolve more in fewer iterations).
        concepts = list(concepts_qs)
        concepts.sort(key=lambda c: -c.games.count())
        for source_concept in concepts:
            self._process_concept(source_concept)

    # ------------------------------------------------------------------
    # Per-Concept logic
    # ------------------------------------------------------------------

    def _process_concept(self, source_concept):
        self.concepts_processed += 1
        games = list(source_concept.games.all())
        n_games = len(games)
        if not games:
            self.stdout.write(
                f'  {source_concept.concept_id!r}: empty — marking done'
            )
            if not self.dry_run:
                source_concept.anchor_migration_completed_at = timezone.now()
                source_concept.save(update_fields=['anchor_migration_completed_at'])
            return

        # Match every Game in the source concept.
        proposals = []
        for game in games:
            match_result = self._safe_match_game(game)
            if match_result:
                cross = identity_cross_check(
                    game,
                    match_result['igdb_data'],
                    confidence=match_result['confidence'],
                    trophy_group_title=match_result['trophy_group_title'],
                )
            else:
                cross = None
            proposals.append({
                'game': game,
                'match': match_result,
                'cross_check': cross,
            })

        # Group proposals by canonical_igdb_id (None == NO_MATCH).
        by_canonical = defaultdict(list)
        for p in proposals:
            cid = p['match']['canonical_igdb_id'] if p['match'] else None
            by_canonical[cid].append(p)

        matched_groups = {k: v for k, v in by_canonical.items() if k is not None}

        ctx = f'{source_concept.concept_id!r} ({n_games} game(s))'

        # Case 1: source's Games split across multiple canonical ids → review.
        if len(matched_groups) > 1:
            self.stdout.write(
                f'  {ctx}: SPLIT across {len(matched_groups)} canonical ids '
                f'— escalating to review'
            )
            for p in proposals:
                if p['match']:
                    extra = (
                        ['region_split_suspected_japan']
                        if self._looks_like_japan_split(matched_groups) else []
                    )
                    is_new = self._create_review_entry(
                        p, target=None, extra_flags=extra,
                    )
                    flags = list(p['cross_check']['flag_reasons']) if p['cross_check'] else []
                    flags.extend(f for f in extra if f not in flags)
                    self._log_per_game_review(p, is_new, flags)
            self.concepts_deferred_split += 1
            return

        # Case 2: no Game matched IGDB at all → leave source as-is.
        if not matched_groups:
            self.stdout.write(f'  {ctx}: NO_MATCH for all games — deferred')
            self.concepts_deferred_no_match += 1
            return

        # Case 3: single canonical id (the common, clean case).
        canonical_id, group = next(iter(matched_groups.items()))
        hint_data = group[0]['match']['igdb_data']

        target, collision = self._get_or_create_target_concept(canonical_id, hint_data)

        if collision:
            self.stdout.write(self.style.WARNING(
                f'  {ctx}: COLLISION on concept_id={canonical_id!r} '
                f'(existing Concept resolves to different canonical) '
                f'— escalating to review'
            ))
            for p in group:
                is_new = self._create_review_entry(
                    p, target=target,
                    extra_flags=['concept_id_collision'],
                )
                flags = list(p['cross_check']['flag_reasons']) if p['cross_check'] else []
                flags.append('concept_id_collision')
                self._log_per_game_review(p, is_new, flags)
            self.concepts_deferred_collision += 1
            return

        # If target is brand new, refresh its IGDBMatch against the canonical
        # IGDB data (this also pulls in media). If reused, we still refresh —
        # clean slate per run.
        if target is not None:
            self._refresh_target_match(target, canonical_id, hint_data=hint_data)

        # Compare each candidate Game against the target's existing Games for
        # trophy-fingerprint homogeneity. If target was newly-created, no
        # reference Game exists yet — first candidate becomes the reference.
        existing_target_games = (
            list(target.games.exclude(pk__in=[p['game'].pk for p in group]))
            if target and target.pk
            else []
        )
        reference_game = existing_target_games[0] if existing_target_games else None

        anchored_a_game = False
        moved_count = 0
        new_review_count = 0       # freshly written ConceptJoinReview rows
        preserved_review_count = 0  # already-resolved reviews we honored
        per_game_lines = []
        for p in group:
            flag_reasons = list(p['cross_check']['flag_reasons']) if p['cross_check'] else []
            if reference_game:
                metric = compare_trophy_metrics(p['game'], reference_game)
                # Avoid duplicate flags (compare_trophy_metrics can return both
                # platinum_status_diverged AND trophy_count_mismatch for
                # related conditions; union once, no double-counting).
                for fr in metric['flag_reasons']:
                    if fr not in flag_reasons:
                        flag_reasons.append(fr)
            if flag_reasons:
                is_new = self._create_review_entry(p, target=target, extra_flags=flag_reasons)
                if is_new:
                    new_review_count += 1
                    per_game_lines.append(
                        f'    pk={p["game"].pk}: review created '
                        f'({", ".join(flag_reasons)})'
                    )
                else:
                    preserved_review_count += 1
                    per_game_lines.append(
                        f'    pk={p["game"].pk}: review preserved (already resolved)'
                    )
            else:
                if not self.dry_run:
                    # force=True bypasses concept_lock. The migration is a
                    # deliberate staff-initiated move; the lock was meant to
                    # block automated sync, not migration. Without this, locked
                    # Games silently stay in their source Concept and the
                    # source never empties out for absorb-and-delete.
                    p['game'].add_concept(target, force=True)
                self.games_moved += 1
                moved_count += 1
                anchored_a_game = True
                # First clean join becomes the reference for subsequent ones.
                if reference_game is None:
                    reference_game = p['game']

        # If at least one Game joined cleanly, stamp the target so it's not
        # re-processed in a future batch. Source Concepts that emptied out
        # auto-delete via Game.add_concept's absorb cascade.
        target_concept_id = (
            target.concept_id if target is not None else str(canonical_id)
        )
        if anchored_a_game and target is not None:
            if not self.dry_run:
                target.anchor_migration_completed_at = timezone.now()
                target.save(update_fields=['anchor_migration_completed_at'])
            self.concepts_anchored += 1

        # Per-Concept summary line, then per-Game detail lines indented under it.
        summary_parts = []
        if moved_count:
            summary_parts.append(f'{moved_count} moved')
        if new_review_count:
            summary_parts.append(f'{new_review_count} new review(s)')
        if preserved_review_count:
            summary_parts.append(f'{preserved_review_count} preserved review(s)')
        summary = ', '.join(summary_parts) or 'no changes'

        prefix = '[DRY] Would anchor' if self.dry_run else 'Anchored'
        if anchored_a_game:
            self.stdout.write(
                f'  {prefix} {ctx} → {target_concept_id!r}: {summary}'
            )
        elif new_review_count or preserved_review_count:
            self.stdout.write(
                f'  {ctx}: no clean anchor; {summary} '
                f'(target {target_concept_id!r} not anchored)'
            )
        for line in per_game_lines:
            self.stdout.write(line)

    def _log_per_game_review(self, proposal, is_new, flag_reasons):
        """Emit a one-line per-Game review attribution line.

        Used by the split / collision branches to indent the per-Game work
        under the Concept summary line. `is_new` is True when a fresh review
        was created (or would be in dry-run), False when an existing resolved
        review was preserved.
        """
        game = proposal['game']
        if is_new:
            flag_summary = ', '.join(flag_reasons) or 'no flags'
            self.stdout.write(
                f'    pk={game.pk}: review created ({flag_summary})'
            )
        else:
            self.stdout.write(
                f'    pk={game.pk}: review preserved (already resolved)'
            )

        # If NO_MATCH Games remain in source after the moves, source stays
        # un-timestamped — it'll be re-evaluated when those Games either get
        # matched or stop being NO_MATCH. If source has no Games left at all,
        # it was absorbed and deleted by add_concept's cascade.

    # ------------------------------------------------------------------
    # Target Concept lifecycle
    # ------------------------------------------------------------------

    def _get_or_create_target_concept(self, canonical_id, hint_data):
        """Return (target_concept_or_None, collision_bool).

        Returns target=None when dry-running a create (we never actually
        materialise the row). Returns collision=True when the bare-integer
        slot is already taken by an unrelated Concept.
        """
        concept_id = str(canonical_id)
        existing = Concept.objects.filter(concept_id=concept_id).first()
        if existing:
            # Verify the existing Concept legitimately anchors at the same id.
            match = getattr(existing, 'igdb_match', None)
            if match and match.igdb_id:
                existing_canonical = IGDBService._resolve_canonical_igdb_id(
                    match.raw_response or {}, match.igdb_id
                )
                if existing_canonical == canonical_id:
                    self.targets_reused += 1
                    return (existing, False)
            return (existing, True)

        if self.dry_run:
            self.targets_created += 1
            return (None, False)

        target = Concept.objects.create(
            concept_id=concept_id,
            unified_title=hint_data.get('name', '') if hint_data else '',
        )
        self.targets_created += 1
        return (target, False)

    def _refresh_target_match(self, target, canonical_id, hint_data=None):
        """Refresh target's IGDBMatch against the canonical IGDB id's data."""
        if self.dry_run:
            return None
        canonical_data = self._get_canonical_igdb_data(canonical_id, hint_data=hint_data)
        if not canonical_data:
            self.stderr.write(self.style.WARNING(
                f'    IGDB returned no data for canonical id {canonical_id}; '
                f'leaving target Concept {target.concept_id!r} without a match'
            ))
            return None
        # confidence=1.0 forces auto_accepted status; method='manual' signals
        # this match was anchored by the migration, not by fuzzy search.
        return IGDBService.process_match(target, canonical_data, confidence=1.0, method='manual')

    def _get_canonical_igdb_data(self, canonical_id, hint_data=None):
        """Fetch (or cached-return) the full IGDB payload for canonical_id."""
        if canonical_id in self._canonical_data_cache:
            return self._canonical_data_cache[canonical_id]
        # The hint IGDB data is the matched Game's IGDB entry — only usable as
        # canonical data when it IS the canonical entry (i.e., not a packaging
        # variant pointing at a parent_game).
        if hint_data and hint_data.get('id') == canonical_id:
            # process_match expects `_time_to_beat` to be present; the matcher
            # doesn't fetch it but fetch_full_game_data does. Be defensive.
            if '_time_to_beat' not in hint_data:
                hint_data['_time_to_beat'] = IGDBService._fetch_time_to_beat(canonical_id)
                if self.api_delay:
                    time.sleep(self.api_delay)
            self._canonical_data_cache[canonical_id] = hint_data
            return hint_data
        data = IGDBService.fetch_full_game_data(canonical_id)
        if self.api_delay:
            time.sleep(self.api_delay)
        self._canonical_data_cache[canonical_id] = data
        return data

    # ------------------------------------------------------------------
    # Review queue
    # ------------------------------------------------------------------

    def _create_review_entry(self, proposal, target, extra_flags):
        game = proposal['game']
        match = proposal['match']
        cross = proposal['cross_check']

        flag_reasons = []
        if cross:
            flag_reasons.extend(cross['flag_reasons'])
        for fr in extra_flags:
            if fr not in flag_reasons:
                flag_reasons.append(fr)
        # Validate against the model's enum so a typo here can't write garbage.
        flag_reasons = [
            fr for fr in flag_reasons
            if fr in ConceptJoinReview.FLAG_REASON_CHOICES
        ]

        canonical_id = match['canonical_igdb_id'] if match else None
        identity_data = cross or {}
        # Strip flag_reasons from identity_data (already promoted to its own column).
        identity_data_clean = {
            k: v for k, v in identity_data.items() if k != 'flag_reasons'
        }
        if match:
            identity_data_clean['match_confidence'] = match['confidence']
            identity_data_clean['match_method'] = match['match_method']
            identity_data_clean['raw_igdb_id'] = match['raw_igdb_id']
            identity_data_clean['trophy_group_title'] = match['trophy_group_title']

        fingerprint = trophy_fingerprint(game)

        # Idempotency: if staff has already resolved a prior review for this
        # game (status approved/rejected/deferred), preserve their decision —
        # never clobber a non-pending review back to pending on re-run.
        # Returns False to signal "preserved an existing resolution" so the
        # caller can count preserved-vs-new reviews separately. Returns True
        # when a new pending review was created (or would be in dry-run).
        existing = ConceptJoinReview.objects.filter(game=game).first()
        if existing and existing.status != 'pending':
            return False

        self.games_flagged_for_review += 1

        if self.dry_run:
            return True

        ConceptJoinReview.objects.update_or_create(
            game=game,
            defaults={
                'proposed_canonical_igdb_id': canonical_id or 0,
                'proposed_concept': target if target and target.pk else None,
                'flag_reasons': flag_reasons,
                'trophy_fingerprint': fingerprint,
                'identity_check_data': identity_data_clean,
                'status': 'pending',
            },
        )
        return True

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _safe_match_game(self, game):
        try:
            return IGDBService.match_game(game)
        except Exception:
            self.stderr.write(self.style.WARNING(
                f'  match_game failed for game pk={game.pk} '
                f'"{game.title_name}"; treating as NO_MATCH'
            ))
            return None

    def _looks_like_japan_split(self, matched_groups):
        """Heuristic: if any group is exclusively JP/AS-region games, hint at the
        Japan/Asia trophy-list quirk in the flag reasons."""
        for group in matched_groups.values():
            regions = set()
            for p in group:
                for r in (p['game'].region or []):
                    regions.add(r)
            if regions and regions.issubset({'JP', 'AS', 'KR', 'CN'}):
                return True
        return False

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------

    def _print_summary(self):
        elapsed = time.time() - self.start_time
        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS('=== Migration summary ==='))
        self.stdout.write(f'  Elapsed:                       {elapsed:.1f}s')
        self.stdout.write(f'  Batches processed:             {self.batches_processed}')
        self.stdout.write(f'  Concepts processed:            {self.concepts_processed}')
        self.stdout.write(f'  Concepts anchored (target):    {self.concepts_anchored}')
        self.stdout.write(f'  Concepts deferred (no match):  {self.concepts_deferred_no_match}')
        self.stdout.write(f'  Concepts deferred (split):     {self.concepts_deferred_split}')
        self.stdout.write(f'  Concepts deferred (collision): {self.concepts_deferred_collision}')
        self.stdout.write(f'  Targets created:               {self.targets_created}')
        self.stdout.write(f'  Targets reused:                {self.targets_reused}')
        self.stdout.write(f'  Games moved:                   {self.games_moved}')
        self.stdout.write(f'  Games flagged for review:      {self.games_flagged_for_review}')
        self.stdout.write(f'  Canonical IGDB payloads cached:{len(self._canonical_data_cache)}')
        if self.dry_run:
            self.stdout.write(self.style.WARNING(
                '\n[DRY RUN] No writes were made. Re-run without --dry-run to apply.'
            ))
        else:
            self.stdout.write(self.style.SUCCESS('\nDone.'))
