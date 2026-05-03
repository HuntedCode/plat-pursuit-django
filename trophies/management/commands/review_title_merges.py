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

import re

from django.core.management.base import BaseCommand
from django.db.models import Prefetch
from django.utils import timezone

from trophies.models import Concept, IGDBMatch, Game
from trophies.services.igdb_service import (
    IGDB_PLATFORM_NAMES,
    IGDBService,
)


_WHITESPACE_RE = re.compile(r'\s+')
_AND_WORD_RE = re.compile(r'\band\b')

# Unicode punctuation variants that render identically to their ASCII
# counterparts but compare unequal. Normalized to ASCII before the
# separator-swap + whitespace passes so Unicode dashes get caught too.
_UNICODE_PUNCT_MAP = {
    '\u2018': "'",   # left single quotation mark   '
    '\u2019': "'",   # right single quotation mark  '
    '\u201C': '"',   # left double quotation mark   "
    '\u201D': '"',   # right double quotation mark  "
    '\u2013': '-',   # en dash                      –
    '\u2014': '-',   # em dash                      —
}

# Trademark noise characters stripped entirely — semantically empty.
_STRIP_CHARS = '\u2122\u00AE\u00A9\u2120'  # ™ ® © ℠


# Heavy IGDBMatch fields this command never reads. Defer to keep the
# in-memory footprint down — `raw_response` alone is ~10 KB per row, and
# at thousands of mismatches per pass that adds up to tens of MB of dead
# weight. Display + lock paths only need `igdb_name` and
# `igdb_first_release_date` from the match.
_IGDBMATCH_DEFERRED_FIELDS = (
    'raw_response',
    'igdb_summary',
    'igdb_storyline',
    'franchise_names',
    'similar_game_igdb_ids',
    'external_urls',
)

# Heavy Concept JSON fields. `media` and `descriptions` can each run
# several KB; with thousands of joined concepts that adds up. Concept.save()
# reads `unified_title` and `slug`, so neither is in this list.
_CONCEPT_DEFERRED_FIELDS = (
    'descriptions',
    'content_rating',
    'media',
    'genres',
    'subgenres',
    'igdb_genres',
    'igdb_themes',
    'title_ids',
)

# Heavy Game JSON / URL fields the command never reads. Game.save() only
# touches `title_name`, so deferring these is safe under `update_fields=`.
_GAME_DEFERRED_FIELDS = (
    'region',
    'title_ids',
    'title_image',
    'title_icon_url',
)


def _normalize_for_merge(s):
    """Aggressive normalization for auto-merge comparison.

    Applied in this exact order (order matters — the separator swap and
    whitespace strip rely on earlier passes having already landed):

      1. Unicode punctuation -> ASCII. Smart quotes/apostrophes (' ') -> ',
         smart double quotes ("") -> ", en/em dashes (– —) -> -. Runs
         first so the separator swap in step 2 catches Unicode dashes too.
      2. " - " (space-ASCII-dash-space) -> ": ". PSN's typical
         title/subtitle separator convention; surrounding spaces
         distinguish it from a hyphen inside a word. "Spider-Man" stays
         intact; "Spider - Man" becomes "spider:man".
      3. Casefold — handles PSN ALL CAPS vs IGDB proper case.
      4. Strip trademark symbols: ™ ® © ℠. Semantically empty noise PSN
         sometimes includes where IGDB doesn't.
      5. Whole-word "and" -> "&". Word-boundary regex, so "Portland" and
         "Understanding" stay untouched. Normalizes "Ratchet & Clank"
         vs "Ratchet and Clank".
      6. Strip colons. PSN/IGDB diverge on title:subtitle colons
         ("Uncharted: Drake's Fortune" vs "Uncharted Drake's Fortune").
      7. Strip ALL whitespace. Catches double spaces AND compound-word
         vs two-word differences ("BioShock" vs "Bio Shock").
    """
    if not s:
        return ''
    for unicode_ch, ascii_ch in _UNICODE_PUNCT_MAP.items():
        s = s.replace(unicode_ch, ascii_ch)
    s = s.replace(' - ', ': ')
    s = s.casefold()
    for ch in _STRIP_CHARS:
        s = s.replace(ch, '')
    s = _AND_WORD_RE.sub('&', s)
    s = s.replace(':', '')
    s = _WHITESPACE_RE.sub('', s)
    return s


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

        # Upper-bound count for the [N/M] display only. The queue
        # itself is consumed lazily — never materialize it.
        upper_bound = self._upper_bound_count(options)

        if upper_bound == 0:
            self.stdout.write('No concepts need review. All mismatches resolved or auto-locked.')
            return

        scope = f'Up to {upper_bound} concept(s) to review (streaming; live mismatch count <= this)'
        if options.get('limit'):
            scope += f' (capped at {options["limit"]})'
        self.stdout.write(scope + '.')
        self.stdout.write(
            'Commands: [m]erge (with suffix)  [ms] merge without suffix  '
            '[me] merge with custom title  [l]eave  [s]kip  [q]uit\n'
        )

        stats = {'merged': 0, 'left': 0, 'skipped': 0, 'errors': 0}
        queue_iter = iter(self._build_queue(options))
        idx = 0
        row = None

        while True:
            total_acted = stats['merged'] + stats['left'] + stats['skipped']
            if options.get('limit') and total_acted >= options['limit']:
                break

            if row is None:
                try:
                    row = next(queue_iter)
                except StopIteration:
                    break

            self._display_row(idx, upper_bound, row)
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

            consumed = False
            if lower == 'l':
                consumed = self._perform_leave(row, stats)
            elif lower == 's':
                self.stdout.write('  Skipped.')
                stats['skipped'] += 1
                consumed = True
            elif lower == 'm':
                consumed = self._perform_merge(row, suffix=row['suggested_suffix'], custom_concept_title=None, stats=stats)
            elif lower == 'ms':
                consumed = self._perform_merge(row, suffix='', custom_concept_title=None, stats=stats)
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
                consumed = self._perform_merge(row, suffix=None, custom_concept_title=remainder, stats=stats)
            else:
                self.stdout.write(
                    '  Unknown action. [m]/[ms]/[me] <title>/[l]/[s]/[q]'
                )
                continue

            if not consumed:
                continue

            idx += 1
            row = None  # release the row's ORM refs; next iteration fetches fresh.
            self.stdout.write('')

        self._print_summary(stats)

    # -------------------------------------------------------------------
    # Auto-lock pre-pass
    # -------------------------------------------------------------------

    @staticmethod
    def _base_match_queryset():
        """Shared trimmed IGDBMatch queryset for auto-lock + queue passes.

        Defers heavy JSON on IGDBMatch and Concept and uses a focused
        Prefetch for games so each row in memory is a fraction of a
        full ORM instance. With chunk_size=200 in iterator() Django
        applies the prefetch per chunk and frees the prior chunk —
        peak resident memory stays bounded regardless of catalog size.
        """
        concept_deferred = tuple(f'concept__{f}' for f in _CONCEPT_DEFERRED_FIELDS)
        games_qs = Game.objects.defer(*_GAME_DEFERRED_FIELDS)
        return (
            IGDBMatch.objects
            .filter(status__in=('accepted', 'auto_accepted'))
            .exclude(igdb_name='')
            .select_related('concept')
            .defer(*_IGDBMATCH_DEFERRED_FIELDS, *concept_deferred)
            .prefetch_related(Prefetch('concept__games', queryset=games_qs))
        )

    def _auto_lock_matching(self, options):
        """Auto-resolve concepts whose titles already match IGDB.

        Two resolution flavours:

          * EXACT: concept title + every game's title_name are byte-equal
            to IGDB (or IGDB + legacy suffix for the concept). Lock + mark
            reviewed without rewriting anything.

          * NORMALIZED: concept + every game match IGDB after
            `_normalize_for_merge`, but byte representations differ. The
            normalizer folds Unicode punctuation (smart quotes, en/em
            dashes) to ASCII, swaps " - " -> ":", casefolds, strips
            trademark symbols, normalizes "and" -> "&", strips colons,
            and strips all whitespace. See that helper's docstring for
            the exact order. Covers the vast majority of cosmetic PSN/
            IGDB divergences (ALL CAPS, apostrophe variants, spacing,
            dash-vs-colon separators, "& vs and"). Rewrite concept to
            canonical form (IGDB name + legacy suffix if legacy), rewrite
            games to raw IGDB, then lock + mark reviewed. IGDB's form
            wins unconditionally.
        """
        qs = self._base_match_queryset().filter(
            concept__title_reviewed_at__isnull=True,
        )
        if options.get('concept_id'):
            qs = qs.filter(concept__concept_id=options['concept_id'])

        exact_locked = 0
        normalized_merged = 0
        game_lock_count = 0
        now = timezone.now()

        for match in qs.iterator(chunk_size=200):
            concept = match.concept
            games = list(concept.games.all())
            igdb_name = match.igdb_name

            suggested_suffix = ''
            if self._is_legacy(games):
                suggested_suffix = self._legacy_suffix(games)
            canonical_concept_title = igdb_name + suggested_suffix

            concept_title = concept.unified_title or ''

            # Exact match first — cheapest check.
            concept_exact = concept_title in (igdb_name, canonical_concept_title)
            games_exact = all(g.title_name == igdb_name for g in games)

            # Normalized fallback. Casefold + whitespace collapse + " - " -> ":"
            # swap. IGDB's form wins unconditionally — all-caps IGDB will
            # overwrite proper-case PSN and vice versa.
            concept_norm = False
            games_norm = False
            if not concept_exact:
                ct_norm = _normalize_for_merge(concept_title)
                concept_norm = ct_norm in (
                    _normalize_for_merge(igdb_name),
                    _normalize_for_merge(canonical_concept_title),
                )
            if not games_exact:
                igdb_norm = _normalize_for_merge(igdb_name)
                games_norm = all(
                    g.title_name == igdb_name
                    or _normalize_for_merge(g.title_name) == igdb_norm
                    for g in games
                )

            concept_ok = concept_exact or concept_norm
            games_ok = games_exact or games_norm
            if not concept_ok or not games_ok:
                continue

            needs_rewrite = concept_norm or not games_exact

            # Apply title rewrites for the case-merge branch.
            concept_fields = []
            if needs_rewrite and concept_title != canonical_concept_title:
                concept.unified_title = canonical_concept_title
                concept_fields.append('unified_title')
            if not concept.title_lock:
                concept.title_lock = True
                concept_fields.append('title_lock')
            concept.title_reviewed_at = now
            concept_fields.append('title_reviewed_at')
            concept.save(update_fields=concept_fields)

            for g in games:
                g_fields = []
                if needs_rewrite and g.title_name != igdb_name:
                    g.title_name = igdb_name
                    g_fields.append('title_name')
                if not g.lock_title:
                    g.lock_title = True
                    g_fields.append('lock_title')
                if g_fields:
                    g.save(update_fields=g_fields)
                    if 'lock_title' in g_fields:
                        game_lock_count += 1

            if needs_rewrite:
                normalized_merged += 1
            else:
                exact_locked += 1

        if exact_locked or normalized_merged:
            parts = []
            if exact_locked:
                parts.append(f'{exact_locked} exact-match lock(s)')
            if normalized_merged:
                parts.append(f'{normalized_merged} normalized merge(s)')
            parts.append(f'{game_lock_count} game lock(s) applied')
            self.stdout.write(self.style.SUCCESS(
                'Auto-resolved: ' + '; '.join(parts) + '.'
            ))
        else:
            self.stdout.write('Auto-lock pre-pass: nothing to do.')

    # -------------------------------------------------------------------
    # Queue building
    # -------------------------------------------------------------------

    def _build_queue(self, options):
        """Stream candidate rows one at a time. NEVER materialize this.

        The interactive loop never goes back, so streaming with
        iterator(chunk_size=200) keeps peak memory at one chunk + the
        current row regardless of catalog size. Calling list() on this
        generator defeats the entire purpose.
        """
        qs = self._base_match_queryset().order_by('concept__concept_id')

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
                'concept': concept,
                'igdb_name': igdb_name,
                'igdb_release_date': match.igdb_first_release_date,
                'igdb_ps_release_dates': match.igdb_ps_release_dates or [],
                'games': games,
                'is_legacy': is_legacy,
                'suggested_suffix': suggested_suffix,
                'concept_mismatch': concept_mismatch,
                'game_mismatches': game_mismatches,
            }

    def _upper_bound_count(self, options):
        """Cheap COUNT(*) over the pre-filter queue.

        Indexed on IGDBMatch.status; doesn't fetch any rows. Used only
        for the [N/M] display in the interactive prompt. Real
        reviewable count is <= this because mismatch filtering happens
        in Python after the SQL pass.
        """
        qs = (
            IGDBMatch.objects
            .filter(status__in=('accepted', 'auto_accepted'))
            .exclude(igdb_name='')
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
        return qs.count()

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
        ps_dates_full = row.get('igdb_ps_release_dates') or []
        ps_dates_display = IGDBService.collapse_ps_release_dates_for_display(ps_dates_full)
        date_marker = self._date_match_marker(
            concept.release_date, ps_dates_display, row['igdb_release_date']
        )
        self.stdout.write(f'  Released:      PSN {psn_date}  |  IGDB {igdb_date}{date_marker}')

        if ps_dates_display:
            parts = [
                f'{IGDB_PLATFORM_NAMES.get(e["platform"], str(e["platform"]))} {e["date"]}'
                for e in ps_dates_display
            ]
            self.stdout.write(f'  IGDB PS dates: {"  |  ".join(parts)}')

        mismatches = row['game_mismatches']
        self.stdout.write(f'  Games ({len(row["games"])}, {len(mismatches)} mismatch{"es" if len(mismatches) != 1 else ""}):')
        for game in row['games']:
            is_mismatch = game in mismatches
            marker = self.style.ERROR(' ≠ IGDB') if is_mismatch else self.style.SUCCESS(' = IGDB')
            lock = '  [lock_title]' if game.lock_title else ''
            shovelware = self._shovelware_tag(game)
            platforms = ', '.join(game.title_platform or []) or '?'
            comm_id = (game.np_communication_id or '')[:16].ljust(16)
            self.stdout.write(
                f'    · {platforms:<12} {comm_id} "{game.title_name}"{lock}{shovelware}{marker}'
            )

        if row['is_legacy']:
            self.stdout.write(
                f'  Legacy concept: yes  →  suggested suffix "{row["suggested_suffix"]}"'
            )
        else:
            self.stdout.write(f'  Legacy concept: no')

    def _date_match_marker(self, psn_dt, ps_dates_list, igdb_first_dt):
        """Annotation comparing PSN concept date to IGDB PS release dates.

        Compares PSN's date against ALL per-platform PS release dates in
        `ps_dates_list` (the denormalized column). Catches exact matches
        on later platform launches that an "earliest only" check would
        miss — e.g. PSN 2025-05-22 matching the PS4 entry exactly while
        IGDB's earliest PS date is the PS5 entry months earlier.

        Falls back to `igdb_first_dt` when ps_dates_list is empty (rows
        where IGDB has no per-platform release_dates entries).

          exact day on any platform -> ✓ exact match (PLAT)
          same year on any platform -> ✓ same year (PLAT)
          ±1 year on closest         -> ✓ within 1 year
          beyond                     -> ~ Ny apart (yellow caution)
        """
        if not psn_dt:
            return ''

        candidates = list(ps_dates_list or [])
        if not candidates and igdb_first_dt:
            candidates = [{
                'date': igdb_first_dt.strftime('%Y-%m-%d'),
                'platform': None,
            }]

        if not candidates:
            return ''

        psn_iso = psn_dt.strftime('%Y-%m-%d')
        psn_year = psn_dt.year

        def label_for(plat_id):
            if plat_id is None:
                return ''
            name = IGDB_PLATFORM_NAMES.get(plat_id, str(plat_id))
            return f' ({name})'

        for c in candidates:
            if c['date'] == psn_iso:
                return self.style.SUCCESS(f'  ✓ exact match{label_for(c.get("platform"))}')

        for c in candidates:
            if c['date'][:4] == str(psn_year):
                return self.style.SUCCESS(f'  ✓ same year{label_for(c.get("platform"))}')

        candidate_years = {int(c['date'][:4]) for c in candidates}
        closest_year = min(candidate_years, key=lambda y: abs(y - psn_year))
        diff = abs(closest_year - psn_year)
        if diff <= 1:
            return self.style.SUCCESS('  ✓ within 1 year')
        return self.style.WARNING(f'  ~ {diff}y apart')

    def _shovelware_tag(self, game):
        """Annotation showing shovelware status when flagged.

        auto_flagged shows as [shovelware: auto], manually_flagged as
        [shovelware: manual]. Other states render nothing — admins only
        care about positive flags here.
        """
        if game.shovelware_status == 'auto_flagged':
            return self.style.WARNING('  [shovelware: auto]')
        if game.shovelware_status == 'manually_flagged':
            return self.style.WARNING('  [shovelware: manual]')
        return ''

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
