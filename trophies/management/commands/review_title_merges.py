"""Interactive admin tool to merge concept/game titles to their IGDB canonical.

Workflow:

  1. Pre-pass: auto-lock every concept whose unified_title AND every game's
     title_name already match the IGDB canonical (including the legacy
     platform suffix form). These don't need human review — just curation
     protection going forward.
  2. Queue: concepts with trusted IGDBMatch where either unified_title or
     any game's title_name differs from the IGDB name AND the concept
     hasn't been reviewed yet (title_reviewed_at IS NULL).
  3. Interactive loop: for each mismatched concept, admin picks Merge,
     Leave, or Skip.

Merge actions set Concept.title_lock + every Game.lock_title to True.
Leave also sets them (the admin explicitly blessed the current state).
Both also stamp Concept.title_reviewed_at so the concept isn't re-queued.
Skip does nothing — comes back on the next run.

Invariants:
  * Games always receive the RAW IGDB name on merge. Suffix is a
    concept-level disambiguator only.
  * Legacy suffix form: " - (PS3)", " - (PS3/PSVITA)" (alphabetical),
    etc. Auto-applied when every game is on a pre-PS4 platform.
  * Only trusted matches (status='accepted'/'auto_accepted') drive the
    queue — pending / rejected / no_match don't have a vetted canonical.
"""

from django.core.management.base import BaseCommand
from django.utils import timezone

from trophies.models import Concept, IGDBMatch, Game


_LEGACY_PLATFORMS = frozenset({'PS3', 'PSP', 'PSVITA', 'PS2', 'PS1'})
_MODERN_PLATFORMS = frozenset({'PS4', 'PS5', 'PSVR', 'PSVR2'})


class Command(BaseCommand):
    help = (
        "Interactive: walk concepts whose trusted IGDB match carries a title "
        "different from the concept or any of its games, and merge each to "
        "the IGDB canonical. Auto-locks perfectly-matching concepts up front."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--clear-locks', action='store_true',
            help='Before the review, bulk-reset Concept.title_lock, '
                 'Game.lock_title, and Concept.title_reviewed_at across the '
                 'catalog. Confirmation prompt.',
        )
        parser.add_argument(
            '--include-reviewed', action='store_true',
            help='Include concepts that were previously reviewed '
                 '(title_reviewed_at IS NOT NULL). Default excludes them.',
        )
        parser.add_argument(
            '--concept-id', type=str,
            help='Process a single concept by concept_id (for spot checks).',
        )
        parser.add_argument(
            '--limit', type=int, default=None,
            help='Stop after N actions (merge/leave/skip count).',
        )
        parser.add_argument(
            '--legacy-only', action='store_true',
            help='Only surface concepts whose games are all on legacy platforms.',
        )
        parser.add_argument(
            '--badge', action='store_true',
            help='Filter to concepts referenced by any badge stage.',
        )
        parser.add_argument(
            '--no-auto-lock', action='store_true',
            help='Skip the pre-pass that auto-locks already-matching concepts.',
        )

    def handle(self, *args, **options):
        if options['clear_locks']:
            self._clear_locks_interactive()

        if not options['no_auto_lock']:
            self._auto_lock_matching(options)

        candidates = list(self._build_queue(options))
        total = len(candidates)

        if total == 0:
            self.stdout.write('No concepts need review. All mismatches resolved or auto-locked.')
            return

        scope = f'{total} concept(s) with title mismatches to review'
        if options.get('limit'):
            scope += f' (capped at {options["limit"]})'
        self.stdout.write(scope + '.')
        self.stdout.write(
            'Commands: [m]erge (with suffix)  [ms] merge without suffix  '
            '[me] merge with custom title  [l]eave  [s]kip  [q]uit\n'
        )

        stats = {'merged': 0, 'left': 0, 'skipped': 0, 'errors': 0}
        idx = 0

        while idx < len(candidates):
            total_acted = stats['merged'] + stats['left'] + stats['skipped']
            if options.get('limit') and total_acted >= options['limit']:
                break

            row = candidates[idx]
            self._display_row(idx, total, row)
            try:
                action = self._prompt(row).strip()
            except (EOFError, KeyboardInterrupt):
                self.stdout.write('\n')
                self._print_summary(stats)
                return

            if action == 'q':
                self._print_summary(stats)
                return

            lower = action.lower()
            if not lower:
                continue

            if lower == 'l':
                if self._perform_leave(row, stats):
                    pass
                else:
                    continue
            elif lower == 's':
                self.stdout.write('  Skipped.')
                stats['skipped'] += 1
            elif lower == 'm':
                if not self._perform_merge(row, suffix=row['suggested_suffix'], custom_concept_title=None, stats=stats):
                    continue
            elif lower == 'ms':
                if not self._perform_merge(row, suffix='', custom_concept_title=None, stats=stats):
                    continue
            elif lower.startswith('me'):
                remainder = action[2:].strip()
                if not remainder:
                    try:
                        remainder = input('  Custom concept title: ').strip()
                    except (EOFError, KeyboardInterrupt):
                        self.stdout.write('\n')
                        self._print_summary(stats)
                        return
                if not remainder:
                    self.stdout.write('  Cancelled (empty title).')
                    continue
                if not self._perform_merge(row, suffix=None, custom_concept_title=remainder, stats=stats):
                    continue
            else:
                self.stdout.write(
                    '  Unknown action. [m]/[ms]/[me] <title>/[l]/[s]/[q]'
                )
                continue

            idx += 1
            self.stdout.write('')

        self._print_summary(stats)

    # -------------------------------------------------------------------
    # Auto-lock pre-pass
    # -------------------------------------------------------------------

    def _auto_lock_matching(self, options):
        """Lock and mark reviewed any concept whose titles already match IGDB.

        These are the "nothing to do" rows that don't need human review —
        we just want to curate them so PSN sync can't regress them and so
        they don't re-surface forever in the queue.
        """
        qs = (
            IGDBMatch.objects
            .filter(status__in=('accepted', 'auto_accepted'))
            .exclude(igdb_name='')
            .filter(concept__title_reviewed_at__isnull=True)
            .select_related('concept')
            .prefetch_related('concept__games')
        )
        if options.get('concept_id'):
            qs = qs.filter(concept__concept_id=options['concept_id'])

        locked_count = 0
        game_lock_count = 0
        now = timezone.now()

        for match in qs.iterator(chunk_size=200):
            concept = match.concept
            games = list(concept.games.all())
            igdb_name = match.igdb_name

            suggested_suffix = ''
            if self._is_legacy(games):
                suggested_suffix = self._legacy_suffix(games)

            # "Perfect match" means:
            #   * concept title is either exactly igdb_name, or
            #     igdb_name + legacy_suffix (the canonical form).
            #   * every game's title_name is exactly igdb_name.
            concept_ok = (
                concept.unified_title == igdb_name
                or (suggested_suffix and concept.unified_title == igdb_name + suggested_suffix)
            )
            games_ok = all(g.title_name == igdb_name for g in games)

            if not concept_ok or not games_ok:
                continue

            concept_fields = []
            if not concept.title_lock:
                concept.title_lock = True
                concept_fields.append('title_lock')
            concept.title_reviewed_at = now
            concept_fields.append('title_reviewed_at')
            concept.save(update_fields=concept_fields)
            locked_count += 1

            for g in games:
                if not g.lock_title:
                    g.lock_title = True
                    g.save(update_fields=['lock_title'])
                    game_lock_count += 1

        if locked_count:
            self.stdout.write(self.style.SUCCESS(
                f'Auto-locked {locked_count} concept(s) already matching IGDB '
                f'({game_lock_count} game lock(s) applied).'
            ))
        else:
            self.stdout.write('Auto-lock pre-pass: nothing to do.')

    # -------------------------------------------------------------------
    # Queue building
    # -------------------------------------------------------------------

    def _build_queue(self, options):
        qs = (
            IGDBMatch.objects
            .filter(status__in=('accepted', 'auto_accepted'))
            .exclude(igdb_name='')
            .select_related('concept')
            .prefetch_related('concept__games')
            .order_by('concept__concept_id')
        )

        if not options.get('include_reviewed'):
            qs = qs.filter(concept__title_reviewed_at__isnull=True)

        if options.get('concept_id'):
            qs = qs.filter(concept__concept_id=options['concept_id'])

        if options.get('badge'):
            from trophies.models import Stage
            badge_concept_ids = set(
                Stage.objects.values_list('concepts__id', flat=True)
                .exclude(concepts__id=None)
            )
            qs = qs.filter(concept_id__in=badge_concept_ids)

        legacy_only = options.get('legacy_only')

        for match in qs.iterator(chunk_size=200):
            concept = match.concept
            igdb_name = match.igdb_name
            games = list(concept.games.all())

            is_legacy = self._is_legacy(games)
            if legacy_only and not is_legacy:
                continue

            suggested_suffix = self._legacy_suffix(games) if is_legacy else ''

            concept_mismatch = concept.unified_title != igdb_name
            if concept_mismatch and suggested_suffix:
                # The suffix form is canonical curation, not drift.
                if concept.unified_title == igdb_name + suggested_suffix:
                    concept_mismatch = False

            game_mismatches = [g for g in games if g.title_name != igdb_name]

            if not concept_mismatch and not game_mismatches:
                continue

            yield {
                'match': match,
                'concept': concept,
                'igdb_name': igdb_name,
                'igdb_release_date': match.igdb_first_release_date,
                'games': games,
                'is_legacy': is_legacy,
                'suggested_suffix': suggested_suffix,
                'concept_mismatch': concept_mismatch,
                'game_mismatches': game_mismatches,
            }

    @staticmethod
    def _is_legacy(games):
        """A concept is legacy when no game sits on PS4/PS5/PSVR/PSVR2."""
        if not games:
            return False
        for g in games:
            for p in (g.title_platform or []):
                if p in _MODERN_PLATFORMS:
                    return False
        return True

    @staticmethod
    def _legacy_suffix(games):
        """Build a concept suffix from the platforms present.

        Single: " - (PS3)". Multi: " - (PS3/PSVITA)" (alphabetical).
        """
        platforms = set()
        for g in games:
            for p in (g.title_platform or []):
                if p in _LEGACY_PLATFORMS:
                    platforms.add(p)
        if not platforms:
            return ''
        return f" - ({'/'.join(sorted(platforms))})"

    # -------------------------------------------------------------------
    # Display
    # -------------------------------------------------------------------

    def _display_row(self, idx, total, row):
        concept = row['concept']
        self.stdout.write(self.style.WARNING(
            f'--- [{idx + 1}/{total}] {concept.concept_id} ---'
        ))

        concept_marker = self.style.ERROR(' (mismatch)') if row['concept_mismatch'] else self.style.SUCCESS(' (match)')
        lock_note = '  [title_lock]' if concept.title_lock else ''
        reviewed_note = '  [previously reviewed]' if concept.title_reviewed_at else ''
        self.stdout.write(
            f'  Concept title: "{concept.unified_title}"{lock_note}{reviewed_note}{concept_marker}'
        )
        self.stdout.write(f'  IGDB name:     "{row["igdb_name"]}"')

        psn_date = concept.release_date.strftime('%Y-%m-%d') if concept.release_date else 'unknown'
        igdb_date = row['igdb_release_date'].strftime('%Y-%m-%d') if row['igdb_release_date'] else 'unknown'
        self.stdout.write(f'  Released:      PSN {psn_date}  |  IGDB {igdb_date}')

        mismatches = row['game_mismatches']
        self.stdout.write(f'  Games ({len(row["games"])}, {len(mismatches)} mismatch{"es" if len(mismatches) != 1 else ""}):')
        for game in row['games']:
            is_mismatch = game in mismatches
            marker = self.style.ERROR(' ≠ IGDB') if is_mismatch else self.style.SUCCESS(' = IGDB')
            lock = '  [lock_title]' if game.lock_title else ''
            platforms = ', '.join(game.title_platform or []) or '?'
            comm_id = (game.np_communication_id or '')[:16].ljust(16)
            self.stdout.write(
                f'    · {platforms:<12} {comm_id} "{game.title_name}"{lock}{marker}'
            )

        if row['is_legacy']:
            self.stdout.write(
                f'  Legacy concept: yes  →  suggested suffix "{row["suggested_suffix"]}"'
            )
        else:
            self.stdout.write(f'  Legacy concept: no')

    def _prompt(self, row):
        self.stdout.write('')
        suggested_title = row['igdb_name'] + (row['suggested_suffix'] if row['is_legacy'] else '')
        self.stdout.write(
            f'  [m]erge → concept: "{suggested_title}" | games: "{row["igdb_name"]}"'
        )
        return input('  > ').strip()

    # -------------------------------------------------------------------
    # Actions
    # -------------------------------------------------------------------

    def _perform_merge(self, row, *, suffix, custom_concept_title, stats):
        """Merge concept + games to IGDB canonical and lock everything.

        suffix semantics:
          None - custom_concept_title is used verbatim.
          ''   - concept gets raw IGDB name (no suffix).
          str  - concept gets igdb_name + suffix.
        """
        concept = row['concept']
        igdb_name = row['igdb_name']
        games = row['games']

        if custom_concept_title is not None:
            new_concept_title = custom_concept_title
        elif suffix:
            new_concept_title = igdb_name + suffix
        else:
            new_concept_title = igdb_name

        try:
            self._apply_concept_changes(
                concept,
                new_title=new_concept_title,
                set_lock=True,
            )
            updated_games = self._lock_all_games(games, rename_to=igdb_name)
        except Exception as exc:
            self.stdout.write(self.style.ERROR(f'  ERROR applying merge: {exc}'))
            stats['errors'] += 1
            return False

        self.stdout.write(self.style.SUCCESS(
            f'  Merged: concept → "{new_concept_title}"  |  '
            f'{updated_games} game(s) updated  |  locks set.'
        ))
        stats['merged'] += 1
        return True

    def _perform_leave(self, row, stats):
        """Leave titles as-is but lock them so sync can't touch."""
        concept = row['concept']
        games = row['games']

        try:
            self._apply_concept_changes(concept, new_title=None, set_lock=True)
            locked_games = self._lock_all_games(games, rename_to=None)
        except Exception as exc:
            self.stdout.write(self.style.ERROR(f'  ERROR applying leave: {exc}'))
            stats['errors'] += 1
            return False

        self.stdout.write(self.style.SUCCESS(
            f'  Left as-is: {locked_games} game lock(s) applied, concept locked + reviewed.'
        ))
        stats['left'] += 1
        return True

    def _apply_concept_changes(self, concept, *, new_title, set_lock):
        """Apply concept-level mutations and stamp title_reviewed_at."""
        fields = []
        if new_title is not None and concept.unified_title != new_title:
            concept.unified_title = new_title
            fields.append('unified_title')
        if set_lock and not concept.title_lock:
            concept.title_lock = True
            fields.append('title_lock')
        concept.title_reviewed_at = timezone.now()
        fields.append('title_reviewed_at')
        concept.save(update_fields=fields)

    @staticmethod
    def _lock_all_games(games, *, rename_to):
        """Lock each game's title. Optionally rename first to rename_to."""
        updated = 0
        for game in games:
            changed = []
            if rename_to is not None and game.title_name != rename_to:
                game.title_name = rename_to
                changed.append('title_name')
            if not game.lock_title:
                game.lock_title = True
                changed.append('lock_title')
            if changed:
                game.save(update_fields=changed)
                updated += 1
        return updated

    # -------------------------------------------------------------------
    # Helpers
    # -------------------------------------------------------------------

    def _clear_locks_interactive(self):
        concept_count = Concept.objects.filter(title_lock=True).count()
        reviewed_count = Concept.objects.filter(title_reviewed_at__isnull=False).count()
        game_count = Game.objects.filter(lock_title=True).count()
        if not concept_count and not game_count and not reviewed_count:
            self.stdout.write('No locks or reviews to clear.')
            return

        self.stdout.write(self.style.WARNING(
            f'About to clear:\n'
            f'  * {concept_count} Concept.title_lock flag(s)\n'
            f'  * {reviewed_count} Concept.title_reviewed_at stamp(s)\n'
            f'  * {game_count} Game.lock_title flag(s)\n'
            f'across the catalog.'
        ))
        try:
            confirm = input('Proceed? Type "yes" to confirm: ').strip().lower()
        except (EOFError, KeyboardInterrupt):
            self.stdout.write('\nAborted.')
            raise SystemExit(1)
        if confirm != 'yes':
            self.stdout.write('Aborted.')
            raise SystemExit(1)

        Concept.objects.filter(title_lock=True).update(title_lock=False)
        Concept.objects.filter(title_reviewed_at__isnull=False).update(title_reviewed_at=None)
        Game.objects.filter(lock_title=True).update(lock_title=False)
        self.stdout.write(self.style.SUCCESS(
            f'Cleared {concept_count} title_lock + {reviewed_count} title_reviewed_at '
            f'+ {game_count} lock_title flag(s).'
        ))

    def _print_summary(self, stats):
        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS('Review Complete'))
        self.stdout.write(f'  Merged:   {stats["merged"]}')
        self.stdout.write(f'  Left:     {stats["left"]}')
        self.stdout.write(f'  Skipped:  {stats["skipped"]}')
        if stats['errors']:
            self.stdout.write(self.style.ERROR(f'  Errors:   {stats["errors"]}'))
