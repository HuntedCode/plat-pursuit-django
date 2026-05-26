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

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from trophies.models import (
    Concept, ConceptJoinReview, GameFamily,
)
from trophies.services.concept_anchor_service import (
    allocate_sibling_concept_id, build_family_raw_igdb_map,
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
            '--concept', type=str, default=None,
            help=(
                'Process just this single Concept by concept_id (e.g. '
                '"215266" or "152231-1"). Runs the per-game matcher on every '
                'Game in the Concept and routes them to their IGDB-anchored '
                'destination(s). Use for targeted re-runs after admin '
                'cleanup or to retry a single stuck Concept without '
                'reprocessing its entire family.'
            ),
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
        # Django's --verbosity (0..3). 1 is the default, 2 unlocks per-game
        # match tracing via self._vlog, 3 also flips IGDBService._debug_scoring
        # which dumps full per-candidate scoring breakdowns (platform skips,
        # title ratios, modifiers) directly to stdout from inside the matcher.
        self.verbosity = int(options.get('verbosity', 1))
        if self.verbosity >= 3:
            IGDBService._debug_scoring = True

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

        if options['concept'] is not None:
            self._process_single_concept(options['concept'])
        elif options['family'] is not None:
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

    def _process_single_concept(self, concept_id):
        """Run the per-game matcher on every Game in one Concept.

        Mirrors `_process_family_batch`'s transaction wrapping so the
        Concept's games either all migrate or none do, but skips the
        family-level batching machinery (we already know which Concept
        we're processing).

        Resolves the input by `concept_id` (string), not by pk — that's
        what staff see in admin output and verbose traces.
        """
        try:
            concept = Concept.objects.get(concept_id=concept_id)
        except Concept.DoesNotExist:
            raise CommandError(
                f'No Concept exists with concept_id={concept_id!r}'
            )

        family_label = (
            f'Family #{concept.family_id} "{concept.family.canonical_name}"'
            if concept.family_id else 'no family (orphan)'
        )
        self.stdout.write(self.style.HTTP_INFO(
            f'\n=== Concept {concept.concept_id!r} '
            f'"{concept.unified_title}" ({family_label}) ==='
        ))
        if self.dry_run:
            self._process_concept(concept)
        else:
            with transaction.atomic():
                self._process_concept(concept)
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

    def _vlog(self, message, level=2):
        """Write a verbose-only line, gated on --verbosity.

        level=2 is the standard "diagnostic" tier. level=3 reserved for
        chatty per-row data we don't want on by default at -v 2.
        """
        if self.verbosity >= level:
            self.stdout.write(message)

    def _process_concept(self, source_concept):
        self.concepts_processed += 1

        # Mark the source Concept as touched by the migration, regardless of
        # outcome. Used by the admin's "Attempted but not anchored" filter to
        # distinguish concepts the migration actually tried (and deferred for
        # NO_MATCH / SPLIT / COLLISION / fingerprint-mismatch reasons) from
        # ones it hasn't reached yet. `Concept.objects.filter().update()` is
        # safe even if the source gets absorbed/deleted later in this call.
        if not self.dry_run:
            Concept.objects.filter(pk=source_concept.pk).update(
                anchor_migration_last_attempt_at=timezone.now()
            )

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

        ctx = f'{source_concept.concept_id!r} ({n_games} game(s))'

        # Match every Game in the source concept. Decisions are strictly
        # per-game — the source concept's existing IGDBMatch is NOT used as
        # an input; it was the old PSN-concept-as-a-unit model. Each game
        # determines its own destination concept based solely on its own
        # IGDB match.
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
                igdb_name = match_result.get('igdb_data', {}).get('name', '')
                trophy_group_title = match_result.get('trophy_group_title') or ''
                self._vlog(
                    f'    pk={game.pk} "{game.title_name}" '
                    f'(trophy_group="{trophy_group_title}") -> '
                    f'IGDB {match_result["raw_igdb_id"]} "{igdb_name}" '
                    f'(canonical={match_result["canonical_igdb_id"]}, '
                    f'conf={match_result["confidence"]:.2f})'
                )
                if cross and cross.get('flag_reasons'):
                    self._vlog(
                        f'      cross-check flags: {", ".join(cross["flag_reasons"])}'
                    )
            else:
                cross = None
                # For NO_MATCH, surface the trophy-group title that match_game
                # would have used so we can see whether the search input is
                # the real-game name (matcher problem) or some stylized
                # PSN-only title that doesn't exist on IGDB (data problem).
                trophy_group_title = IGDBService._extract_trophy_group_title(game)
                self._vlog(
                    f'    pk={game.pk} "{game.title_name}" '
                    f'(trophy_group="{trophy_group_title}") -> NO_MATCH'
                )
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

        # Case 3: single canonical family. Subdivide by raw_igdb_id (specific
        # IGDB version) so each version gets its own Concept with version-
        # specific metadata (cover, summary, companies, screenshots, etc.).
        canonical_id, group = next(iter(matched_groups.items()))

        # Subdivide by raw_igdb_id within the canonical family. A single
        # source Concept can rarely contain Games from multiple versions
        # (e.g., PSN merged PS3 original + Remastered into one concept_id) —
        # each raw subgroup gets its own destination Concept.
        by_raw = defaultdict(list)
        for p in group:
            raw_id = p['match']['raw_igdb_id']
            by_raw[raw_id].append(p)

        raw_map = build_family_raw_igdb_map(
            canonical_id, exclude_concept_pk=source_concept.pk,
        )

        anchored_a_game = False
        moved_count = 0
        moves_by_destination = defaultdict(int)
        new_review_count = 0
        preserved_review_count = 0
        per_game_lines = []
        any_collision = False
        target_concept_id = None  # for summary line
        target_name = ''  # for summary line (IGDB name of the destination)

        for raw_igdb_id, subgroup in by_raw.items():
            raw_data = subgroup[0]['match']['igdb_data']
            target, collision = self._get_or_create_target_for_raw(
                raw_igdb_id, canonical_id, raw_data, raw_map,
            )

            if collision:
                any_collision = True
                self.stdout.write(self.style.WARNING(
                    f'  {ctx}: COLLISION on concept_id slot for raw IGDB '
                    f'{raw_igdb_id} — escalating to review'
                ))
                for p in subgroup:
                    is_new = self._create_review_entry(
                        p, target=target,
                        extra_flags=['concept_id_collision'],
                    )
                    flags = list(p['cross_check']['flag_reasons']) if p['cross_check'] else []
                    flags.append('concept_id_collision')
                    self._log_per_game_review(p, is_new, flags)
                continue

            # Refresh target's IGDBMatch with the RAW data so this Concept
            # represents the SPECIFIC IGDB version it anchors (not canonical).
            if target is not None:
                self._refresh_target_match(target, raw_data)
                if raw_igdb_id not in raw_map:
                    raw_map[raw_igdb_id] = target  # update for downstream lookups

            # Reference Game = an existing Game in target that isn't part of
            # this source's subgroup. Used for trophy-fingerprint vetting.
            existing_target_games = (
                list(target.games.exclude(pk__in=[p['game'].pk for p in subgroup]))
                if target and target.pk
                else []
            )
            reference_game = existing_target_games[0] if existing_target_games else None
            subgroup_anchored_a_game = False

            for p in subgroup:
                game = p['game']
                cross_flags = list(p['cross_check']['flag_reasons']) if p['cross_check'] else []
                game_fp = trophy_fingerprint(game)

                fingerprint_flags = []
                if reference_game:
                    metric = compare_trophy_metrics(game, reference_game)
                    fingerprint_flags = metric['flag_reasons']

                flag_reasons = cross_flags + [
                    fr for fr in fingerprint_flags if fr not in cross_flags
                ]

                if flag_reasons:
                    is_new = self._create_review_entry(p, target=target, extra_flags=flag_reasons)
                    if is_new:
                        new_review_count += 1
                        per_game_lines.append(
                            f'    pk={game.pk}: review created '
                            f'({", ".join(flag_reasons)})'
                        )
                    else:
                        preserved_review_count += 1
                        per_game_lines.append(
                            f'    pk={game.pk}: review preserved (already resolved)'
                        )
                else:
                    if not self.dry_run and target is not None:
                        # force=True bypasses concept_lock — migration is a
                        # deliberate staff-initiated move, not automated sync.
                        game.add_concept(target, force=True)
                    self.games_moved += 1
                    moved_count += 1
                    if target is not None:
                        moves_by_destination[target.concept_id] += 1
                    anchored_a_game = True
                    subgroup_anchored_a_game = True
                    if reference_game is None:
                        reference_game = game

            # Stamp this version's target if any clean Game landed in it.
            if subgroup_anchored_a_game and target is not None and not self.dry_run:
                target.anchor_migration_completed_at = timezone.now()
                target.save(update_fields=['anchor_migration_completed_at'])

            # Record a target_concept_id + name for the summary line. When
            # multiple versions are anchored from one source, the summary
            # shows the breakdown in moves_by_destination anyway. The name
            # falls back to the IGDB raw_data when the target Concept hasn't
            # been created yet (dry-run mode).
            if target is not None:
                target_concept_id = target.concept_id
                target_name = target.unified_title or (raw_data.get('name', '') if raw_data else '')
            elif target_concept_id is None:
                target_concept_id = str(canonical_id)
                target_name = raw_data.get('name', '') if raw_data else ''

        if any_collision:
            self.concepts_deferred_collision += 1
            return
        if anchored_a_game:
            self.concepts_anchored += 1

        # Per-Concept summary line, then per-Game detail lines indented under
        # it. The destination breakdown shows when fingerprint-aware sibling
        # routing landed Games at non-primary Concepts in the same family.
        summary_parts = []
        if moved_count:
            if len(moves_by_destination) > 1:
                breakdown = ', '.join(
                    f'{cid!r} ({n})'
                    for cid, n in sorted(moves_by_destination.items())
                )
                summary_parts.append(f'{moved_count} moved [{breakdown}]')
            else:
                summary_parts.append(f'{moved_count} moved')
        if new_review_count:
            summary_parts.append(f'{new_review_count} new review(s)')
        if preserved_review_count:
            summary_parts.append(f'{preserved_review_count} preserved review(s)')
        summary = ', '.join(summary_parts) or 'no changes'

        prefix = '[DRY] Would anchor' if self.dry_run else 'Anchored'
        name_suffix = f' "{target_name}"' if target_name else ''
        if anchored_a_game:
            self.stdout.write(
                f'  {prefix} {ctx} → {target_concept_id!r}{name_suffix}: {summary}'
            )
        elif new_review_count or preserved_review_count:
            self.stdout.write(
                f'  {ctx}: no clean anchor; {summary} '
                f'(target {target_concept_id!r}{name_suffix} not anchored)'
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

    def _get_or_create_target_for_raw(self, raw_igdb_id, canonical_id, raw_data, raw_map):
        """Find or create the per-version Concept for `raw_igdb_id` in the family.

        Returns (target_concept_or_None, collision_bool).

        Concept_id naming: `concept_id = str(raw_igdb_id)` for the natural
        slot (one Concept per IGDB version). Duplicates only when the same
        raw needs multiple Concepts (e.g., Our World Is Ended-style: same
        IGDB game, materially different trophy lists) — those go to
        `allocate_sibling_concept_id(raw_igdb_id)` returning
        `str(raw)-N` slots.

        Reuses existing Concept from `raw_map` when one anchors this raw.
        Detects collisions when the slot is owned by a Concept in a
        DIFFERENT canonical family — staff resolves manually.

        Returns target=None when dry-running a create.
        """
        # Existing per-version Concept anchors this raw_igdb_id?
        if raw_igdb_id in raw_map:
            self.targets_reused += 1
            return (raw_map[raw_igdb_id], False)

        # Natural slot for this version: concept_id directly = raw IGDB id.
        target_concept_id = str(raw_igdb_id)

        existing = Concept.objects.filter(concept_id=target_concept_id).first()
        if existing:
            existing_match = getattr(existing, 'igdb_match', None)
            if existing_match and existing_match.igdb_id:
                existing_canonical = IGDBService._resolve_canonical_igdb_id(
                    existing_match.raw_response or {}, existing_match.igdb_id
                )
                if existing_canonical != canonical_id:
                    # Different family owns this slot — true collision.
                    return (existing, True)
                # Same family, but existing's raw doesn't match — would have
                # been in raw_map otherwise. Possible legacy state. Allocate
                # a same-raw suffix slot so we don't trample.
                if existing_match.igdb_id != raw_igdb_id:
                    target_concept_id = allocate_sibling_concept_id(raw_igdb_id)
                else:
                    self.targets_reused += 1
                    return (existing, False)
            else:
                # No IGDBMatch on existing — safe to reuse the slot.
                self.targets_reused += 1
                return (existing, False)

        if self.dry_run:
            self.targets_created += 1
            return (None, False)

        target = Concept.objects.create(
            concept_id=target_concept_id,
            unified_title=raw_data.get('name', '') if raw_data else '',
        )
        self.targets_created += 1
        return (target, False)

    def _refresh_target_match(self, target, raw_data):
        """Refresh target's IGDBMatch against this Concept's RAW IGDB data.

        The Concept represents one specific IGDB version. process_match writes
        IGDBMatch.igdb_id = raw_data['id'] (this version), pulls in
        version-specific metadata (cover, summary, companies, etc.), and
        links Concept to its GameFamily via canonical resolution.
        """
        if self.dry_run:
            return None
        if not raw_data:
            self.stderr.write(self.style.WARNING(
                f'    No IGDB data; leaving target Concept '
                f'{target.concept_id!r} without a refresh'
            ))
            return None
        # confidence=1.0 forces auto_accepted status; method='manual' signals
        # this match was anchored by the migration.
        return IGDBService.process_match(target, raw_data, confidence=1.0, method='manual')

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
        raw_igdb_id = match['raw_igdb_id'] if match else None
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
                'proposed_raw_igdb_id': raw_igdb_id,
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
        if self.dry_run:
            self.stdout.write(self.style.WARNING(
                '\n[DRY RUN] No writes were made. Re-run without --dry-run to apply.'
            ))
        else:
            self.stdout.write(self.style.SUCCESS('\nDone.'))
