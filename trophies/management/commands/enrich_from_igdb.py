import re
import time
from datetime import datetime, timezone as dt_timezone

from django.core.management.base import BaseCommand, CommandError
from django.db.models import F, Q

from trophies.models import Concept, IGDBMatch, Stage
from trophies.services.igdb_service import (
    IGDBService,
    IGDB_PLATFORM_NAMES,
)


# Token filtering for related-title surfacing. Short tokens and common
# English stopwords produce too much noise as seeds (e.g. "the" matches
# half the catalog).
_RELATED_STOPWORDS = frozenset({
    'the', 'a', 'an', 'of', 'for', 'to', 'and', 'in', 'on', 'at',
    'with', 'from', 'vs', 'by',
})
_RELATED_MIN_TOKEN_LEN = 3
_RELATED_MAX_SEED_TOKENS = 2


class Command(BaseCommand):
    help = 'Enrich Concepts with IGDB data (developer, genre, theme, time-to-beat, etc.)'

    def add_arguments(self, parser):
        parser.add_argument(
            '--all', action='store_true',
            help='Process all concepts (including those already matched)',
        )
        parser.add_argument(
            '--pending', action='store_true',
            help='Re-process concepts with pending_review status',
        )
        parser.add_argument(
            '--retry-no-match', action='store_true',
            help='Re-process concepts previously recorded as no_match (oldest first)',
        )
        parser.add_argument(
            '--missing-or-no-match', action='store_true',
            help='Re-process concepts with no IGDBMatch row OR status=no_match, '
                 'oldest first by last_synced_at (NULLS FIRST). Used by the weekly retry cron.',
        )
        parser.add_argument(
            '--max-minutes', type=int, metavar='N', default=None,
            help='Hard runtime cap for the enrichment loop. Exits cleanly with a '
                 'partial summary once N minutes have elapsed. Intended for cron '
                 'use (--missing-or-no-match and --refresh both honor it) so '
                 'Render billing stays bounded.',
        )
        parser.add_argument(
            '--refresh', action='store_true',
            help='Re-fetch IGDB data for all accepted matches (no re-matching)',
        )
        parser.add_argument(
            '--stale-before', type=str, metavar='YYYY-MM-DD',
            help='With --refresh, only refresh matches last synced before this date (UTC midnight, exclusive)',
        )
        parser.add_argument(
            '--concept-id', type=str,
            help='Process a single concept by concept_id',
        )
        parser.add_argument(
            '--batch-size', type=int, default=50,
            help='Number of concepts per batch (default: 50)',
        )
        parser.add_argument(
            '--dry-run', action='store_true',
            help='Report matches without saving to the database',
        )
        parser.add_argument(
            '--force', action='store_true',
            help='Re-match even if concept already has an IGDB match',
        )
        parser.add_argument(
            '--min-confidence', type=float,
            help='Override minimum confidence threshold for storing matches',
        )
        parser.add_argument(
            '--verbose', action='store_true',
            help='Show detailed IGDB search results and scoring for each concept',
        )
        parser.add_argument(
            '--manual', type=int, metavar='IGDB_ID',
            help='Manually assign an IGDB game ID to a concept (use with --concept-id)',
        )
        parser.add_argument(
            '--search', type=str, metavar='QUERY',
            help='Search IGDB and display results (does not save anything)',
        )
        parser.add_argument(
            '--review', action='store_true',
            help='Interactive review queue for pending matches',
        )
        parser.add_argument(
            '--unmatched', action='store_true',
            help='Interactive queue for concepts with no IGDB match',
        )
        parser.add_argument(
            '--badge', action='store_true',
            help='Filter --review or --unmatched to only concepts used in badges',
        )
        parser.add_argument(
            '--debug-scoring', action='store_true',
            help='Print a step-by-step confidence breakdown for every IGDB candidate '
                 'scored across every strategy. Noisy but invaluable for troubleshooting '
                 'unexpected confidence values. Best combined with --concept-id <id> to '
                 'limit output to a single target concept.',
        )

    def handle(self, *args, **options):
        # Toggle verbose IGDB logging
        import logging
        igdb_logger = logging.getLogger('psn_api')
        if options.get('verbose'):
            igdb_logger.setLevel(logging.INFO)
        else:
            igdb_logger.setLevel(logging.WARNING)

        # Toggle the IGDBService scoring-breakdown flag. Printed output goes
        # straight to stdout so it's visible through `docker compose exec`.
        IGDBService._debug_scoring = bool(options.get('debug_scoring'))

        if options['search']:
            return self._handle_search(options)
        if options['review']:
            return self._handle_review(options)
        if options['unmatched']:
            return self._handle_unmatched(options)
        if options['manual']:
            return self._handle_manual(options)
        if options['refresh']:
            return self._handle_refresh(options)
        return self._handle_enrich(options)

    # -------------------------------------------------------------------
    # Search mode: search IGDB and display results
    # -------------------------------------------------------------------

    # -------------------------------------------------------------------
    # Review mode: interactive review queue
    # -------------------------------------------------------------------

    def _get_badge_concept_ids(self):
        """Get all concept IDs used in any badge stage."""
        return set(
            Stage.objects.values_list('concepts__id', flat=True)
            .exclude(concepts__id=None)
        )

    def _handle_review(self, options):
        qs = IGDBMatch.objects.filter(
            status='pending_review',
        ).select_related('concept', 'concept__family').prefetch_related('concept__games')

        if options.get('badge'):
            badge_concept_ids = self._get_badge_concept_ids()
            qs = qs.filter(concept_id__in=badge_concept_ids)

        matches = list(qs)

        if options.get('concept_id'):
            matches = [m for m in matches if m.concept.concept_id == options['concept_id']]

        total = len(matches)
        if total == 0:
            self.stdout.write('No pending matches to review.')
            return

        self.stdout.write(f'{total} pending match(es) to review.\n')
        self.stdout.write('Commands: [a]pprove  [r]eject  [s]earch <query>  [sa]earch all <query>  [m]anual <igdb_id>  [n]ext  [q]uit\n')

        approved = 0
        rejected = 0
        reassigned = 0
        skipped = 0
        idx = 0

        while idx < len(matches):
            match = matches[idx]
            concept = match.concept

            self._print_review_header(idx, total, concept, match)

            # Interactive loop for this match
            while True:
                try:
                    action = input('  > ').strip().lower()
                except (EOFError, KeyboardInterrupt):
                    self.stdout.write('\n')
                    self._print_review_summary(approved, rejected, reassigned, skipped)
                    return

                if not action:
                    continue

                if action == 'a':
                    IGDBService.approve_match(match)
                    self.stdout.write(self.style.SUCCESS('  Approved.'))
                    approved += 1
                    self._surface_related_after_action(
                        match.igdb_name or concept.unified_title,
                        matches, idx,
                        title_fn=lambda m: m.concept.unified_title,
                        other_status='no_match',
                    )
                    break

                elif action == 'r':
                    old_igdb_id = match.igdb_id
                    match.delete()
                    self.stdout.write('  Rejected. Re-matching...')
                    new_match = IGDBService.enrich_concept(concept)
                    if new_match and new_match.igdb_id != old_igdb_id:
                        self.stdout.write(
                            f'  New match: IGDB #{new_match.igdb_id} "{new_match.igdb_name}" '
                            f'({new_match.match_method}, {new_match.match_confidence:.0%}) [{new_match.status}]'
                        )
                        if new_match.status == 'pending_review':
                            self.stdout.write('  New match is also pending. Decide now:')
                            # Replace current match and let the loop continue
                            matches[idx] = new_match
                            continue
                        else:
                            self.stdout.write(self.style.SUCCESS('  Auto-accepted.'))
                            self._surface_related_after_action(
                                new_match.igdb_name or concept.unified_title,
                                matches, idx,
                                title_fn=lambda m: m.concept.unified_title,
                                other_status='no_match',
                            )
                    elif new_match and new_match.igdb_id == old_igdb_id:
                        self.stdout.write('  Re-match found the same game. Use [s]earch + [m]anual to assign a different one, or [n]ext to skip.')
                        matches[idx] = new_match
                        continue
                    else:
                        self.stdout.write('  No alternative match found.')
                    rejected += 1
                    break

                elif action == 'n':
                    self.stdout.write('  Skipped.')
                    skipped += 1
                    break

                elif action == 'q':
                    self._print_review_summary(approved, rejected, reassigned, skipped)
                    return

                elif action.startswith('sa ') or action.startswith('sa\t'):
                    # Search all platforms (no PlayStation filter)
                    query = action[3:].strip()
                    if not query:
                        self.stdout.write('  Usage: sa <search query>')
                        continue
                    self.stdout.write(f'  Searching IGDB (all platforms) for "{query}"...')
                    results = IGDBService.search_game(query, limit=10, platform_filter=False)
                    exact = IGDBService.search_by_exact_name(query, limit=10)
                    seen = {r['id'] for r in (results or [])}
                    extra = [r for r in (exact or []) if r['id'] not in seen]
                    all_results = (results or []) + extra
                    if all_results:
                        for r in all_results:
                            self.stdout.write(self._format_game_result(r, current_igdb_id=match.igdb_id if isinstance(match, IGDBMatch) else None))
                    else:
                        self.stdout.write('  No results found.')

                elif action.startswith('s ') or action.startswith('s\t'):
                    query = action[2:].strip()
                    if not query:
                        self.stdout.write('  Usage: s <search query>')
                        continue
                    self.stdout.write(f'  Searching IGDB (PlayStation) for "{query}"...')
                    results = IGDBService.search_game(query, limit=10, platform_filter=True)
                    exact = IGDBService.search_by_exact_name(query, limit=10)
                    seen = {r['id'] for r in (results or [])}
                    extra = [r for r in (exact or []) if r['id'] not in seen]
                    all_results = (results or []) + extra
                    if all_results:
                        for r in all_results:
                            self.stdout.write(self._format_game_result(r, current_igdb_id=match.igdb_id if isinstance(match, IGDBMatch) else None))
                    else:
                        self.stdout.write('  No results found.')

                elif action.startswith('m ') or action.startswith('m\t'):
                    try:
                        new_igdb_id = int(action[2:].strip())
                    except ValueError:
                        self.stdout.write('  Usage: m <igdb_id> (number)')
                        continue
                    self.stdout.write(f'  Fetching IGDB #{new_igdb_id}...')
                    igdb_data = IGDBService.get_game_details(new_igdb_id)
                    if not igdb_data:
                        self.stdout.write(self.style.ERROR(f'  IGDB #{new_igdb_id} not found.'))
                        continue

                    # Show details for confirmation
                    self.stdout.write(f'  Name:      "{igdb_data.get("name")}"')
                    self.stdout.write(f'  Entry:     {self._format_game_result(igdb_data).strip()}')
                    ic_devs = []
                    ic_pubs = []
                    for ic in igdb_data.get('involved_companies', []):
                        co = ic.get('company', {})
                        name = co.get('name', '') if isinstance(co, dict) else ''
                        if name and ic.get('developer'):
                            ic_devs.append(name)
                        if name and ic.get('publisher'):
                            ic_pubs.append(name)
                    self.stdout.write(f'  Developer: {", ".join(ic_devs) or "unknown"}')
                    self.stdout.write(f'  Publisher: {", ".join(ic_pubs) or "unknown"}')

                    try:
                        confirm = input('  Assign this match? [y/n] > ').strip().lower()
                    except (EOFError, KeyboardInterrupt):
                        self.stdout.write('\n')
                        self._print_review_summary(approved, rejected, reassigned, skipped)
                        return

                    if confirm == 'y':
                        if isinstance(match, IGDBMatch):
                            match.delete()
                        new_match = IGDBService.process_match(concept, igdb_data, 1.0, 'manual')
                        self.stdout.write(self.style.SUCCESS(
                            f'  Assigned IGDB #{new_igdb_id} "{igdb_data.get("name")}" [{new_match.status}]'
                        ))
                        reassigned += 1
                        self._surface_related_after_action(
                            igdb_data.get('name') or concept.unified_title,
                            matches, idx,
                            title_fn=lambda m: m.concept.unified_title,
                            other_status='no_match',
                        )
                        break
                    else:
                        self.stdout.write('  Cancelled.')

                else:
                    self.stdout.write('  Commands: [a]pprove  [r]eject  [s]earch <query>  [sa] search all <query>  [m]anual <igdb_id>  [n]ext  [q]uit')

            idx += 1
            self.stdout.write('')

        self._print_review_summary(approved, rejected, reassigned, skipped)

    # ------------------------------------------------------------------
    # Related-title surfacing (shared by --review and --unmatched)
    # ------------------------------------------------------------------

    def _extract_seed_tokens(self, title):
        """Pull up to 2 significant tokens from a cleaned title for fuzzy matching.

        "Spyro Reignited Trilogy" -> ["spyro", "reignited"]
        "Final Fantasy XIII" -> ["final", "fantasy"]
        "The Last of Us" -> ["last"]
        """
        if not title:
            return []
        cleaned = IGDBService._clean_title_for_search(title) or ''
        tokens = []
        seen = set()
        for tok in cleaned.split():
            if len(tok) < _RELATED_MIN_TOKEN_LEN or tok.lower() in _RELATED_STOPWORDS:
                continue
            key = tok.lower()
            if key in seen:
                continue
            seen.add(key)
            tokens.append(tok)
            if len(tokens) >= _RELATED_MAX_SEED_TOKENS:
                break
        return tokens

    def _title_matches_seed(self, title, seed_tokens):
        """True if the cleaned title contains any seed token as a whole word."""
        if not title or not seed_tokens:
            return False
        cleaned = IGDBService._clean_title_for_search(title).lower()
        if not cleaned:
            return False
        cleaned_tokens = set(cleaned.split())
        return any(tok.lower() in cleaned_tokens for tok in seed_tokens)

    def _promote_related_in_queue(self, queue, idx, seed_tokens, title_fn):
        """Reorder queue[idx+1:] so items whose title matches seed_tokens come first.

        Returns the count of items moved to the front. `title_fn(item)` should
        return the string title of a queue element (the match's concept title
        for --review; the concept's own title for --unmatched).
        """
        if idx + 1 >= len(queue) or not seed_tokens:
            return 0
        related, other = [], []
        for item in queue[idx + 1:]:
            if self._title_matches_seed(title_fn(item), seed_tokens):
                related.append(item)
            else:
                other.append(item)
        if not related:
            return 0
        queue[idx + 1:] = related + other
        return len(related)

    def _count_related_in_other_queue(self, seed_tokens, other_status):
        """Count concepts in the OTHER queue whose title matches seed_tokens.

        `other_status` is 'no_match' when called from --review, and
        'pending_review' when called from --unmatched. Returns 0 when
        seed is empty or no concepts match.
        """
        if not seed_tokens:
            return 0
        qs = (
            Concept.objects
            .filter(igdb_match__status=other_status)
            .only('id', 'unified_title')
        )
        return sum(
            1 for c in qs
            if self._title_matches_seed(c.unified_title, seed_tokens)
        )

    def _surface_related_after_action(self, seed_title, queue, idx, title_fn, other_status):
        """Unified post-accept hook: promote related in-queue, note cross-queue.

        Called after a successful approve/reassign/manual-assign. Silently
        no-ops when seed_title produces no tokens (e.g. generic titles
        that reduce to stopwords only).
        """
        seed = self._extract_seed_tokens(seed_title)
        if not seed:
            return
        promoted = self._promote_related_in_queue(queue, idx, seed, title_fn)
        if promoted:
            self.stdout.write(self.style.WARNING(
                f'  -> {promoted} related item(s) queued next based on "{" ".join(seed)}".'
            ))
        cross = self._count_related_in_other_queue(seed, other_status)
        if cross:
            other_queue_name = 'no_match' if other_status == 'no_match' else 'pending review'
            mode_flag = '--unmatched' if other_status == 'no_match' else '--review'
            self.stdout.write(
                f'  Also: {cross} related concept(s) in the {other_queue_name} queue (run {mode_flag}).'
            )

    def _print_review_summary(self, approved, rejected, reassigned, skipped):
        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS('Review Complete'))
        self.stdout.write(f'  Approved:    {approved}')
        self.stdout.write(f'  Rejected:    {rejected}')
        self.stdout.write(f'  Reassigned:  {reassigned}')
        self.stdout.write(f'  Skipped:     {skipped}')

    def _print_review_header(self, idx, total, concept, match):
        """Expanded per-concept context block for the interactive review queue.

        Shows enough signal to tell at a glance whether a pending match is
        correct, whether the concept is a splittable compilation candidate,
        whether users have already engaged with it (which makes reassignment
        riskier), and what the proposed IGDB entry looks like vs. the PSN-side
        ground truth.
        """
        self._print_concept_context(idx, total, concept, match=match)
        self._print_match_context(match)

    def _print_unmatched_header(self, idx, total, concept):
        """Same expanded context block, but without match-side sections (no match exists)."""
        self._print_concept_context(idx, total, concept, match=None)
        self.stdout.write('')

    def _print_concept_context(self, idx, total, concept, match):
        """Shared PSN-side context block used by both review and unmatched queues."""
        self.stdout.write(self.style.WARNING(
            f'--- [{idx + 1}/{total}] {concept.concept_id} "{concept.unified_title}" ---'
        ))

        games = list(concept.games.all())
        concept_platforms = set()
        for game in games:
            for p in (game.title_platform or []):
                concept_platforms.add(p)

        # --- PSN-side concept facts ---
        psn_release = concept.release_date.strftime('%Y-%m-%d') if concept.release_date else 'unknown'
        self.stdout.write(
            f'  [Concept]   PSN platforms: {", ".join(sorted(concept_platforms)) or "unknown"}  '
            f'|  Released: {psn_release}  '
            f'|  Publisher: {concept.publisher_name or "unknown"}'
        )

        # --- Per-Game breakdown ---
        locked_count = sum(1 for g in games if g.concept_lock)
        lock_note = f', {locked_count} locked' if locked_count else ''
        self.stdout.write(f'  [Games]     {len(games)} total{lock_note}:')
        for game in games:
            lock = '  [locked]' if game.concept_lock else ''
            platforms = ', '.join(game.title_platform or []) or '?'
            comm_id = (game.np_communication_id or '')[:16].ljust(16)
            self.stdout.write(
                f'              · {platforms:<12} {comm_id} "{game.title_name}"{lock}'
            )

        # --- Compilation signals ---
        raw = match.raw_response if match else None
        game_type_name = ''
        if raw:
            game_type_obj = raw.get('game_type')
            if isinstance(game_type_obj, dict):
                # IGDB's game_type expansion key is `type`, not `name`.
                # Fall back to the legacy `category` numeric on pre-v4
                # cached responses.
                game_type_name = game_type_obj.get('type') or ''
            if not game_type_name:
                cat = raw.get('category')
                if cat is not None:
                    game_type_name = dict(IGDBMatch.GAME_CATEGORY_CHOICES).get(cat, '')
        is_compilation = bool(match and match.is_likely_compilation)
        is_dismissed = bool(match and match.compilation_review_dismissed)
        splittable = is_compilation and len(games) >= 2 and not is_dismissed
        if is_compilation or len(games) >= 2:
            dismissed_note = ' (admin dismissed)' if is_dismissed else ''
            if splittable:
                verdict = 'SPLITTABLE (2+ Games, not dismissed)'
            elif is_compilation:
                verdict = 'flagged bundle, 1 Game (unified trophy list)'
            elif len(games) >= 2:
                verdict = f'{len(games)} Games but no IGDB bundle flag'
            else:
                verdict = ''
            game_type_line = f'IGDB game_type: {game_type_name or "?"}  |  ' if match else ''
            flag_line = (
                f'is_likely_compilation={is_compilation}{dismissed_note}  |  '
                if match else ''
            )
            self.stdout.write(f'  [Compile]   {game_type_line}{flag_line}{verdict}')

        # --- Family linkage ---
        if concept.family_id:
            family = concept.family
            sibling_count = family.concepts.exclude(pk=concept.pk).count()
            igdb_tag = f' (igdb #{family.igdb_id})' if family.igdb_id else ''
            sibling_note = f', {sibling_count} sibling(s)' if sibling_count else ', no siblings yet'
            self.stdout.write(
                f'  [Family]    "{family.canonical_name}"{igdb_tag}{sibling_note}'
            )

        # --- Engagement (flags that reassignment has user-visible consequences) ---
        ratings_count = concept.user_ratings.count()
        reviews_count = concept.reviews.count()
        roadmap_count = concept.roadmaps.count()
        if ratings_count or reviews_count or roadmap_count:
            roadmap_note = (
                f', {roadmap_count} roadmap section{"s" if roadmap_count != 1 else ""}'
                if roadmap_count else ''
            )
            self.stdout.write(
                f'  [Engaged]   {ratings_count} rating(s), {reviews_count} review(s){roadmap_note}'
            )

    def _print_match_context(self, match):
        """IGDB-side match context (current match, developer/publisher, row summary)."""
        raw = match.raw_response or {}
        igdb_released = (
            match.igdb_first_release_date.strftime('%Y-%m-%d')
            if match.igdb_first_release_date else 'unknown'
        )
        self.stdout.write(
            f'  [Match]     IGDB #{match.igdb_id} "{match.igdb_name}"  '
            f'({match.match_method}, {match.match_confidence:.0%})  '
            f'|  Released: {igdb_released}'
        )
        ps_dates_display = IGDBService.collapse_ps_release_dates_for_display(
            match.igdb_ps_release_dates or []
        )
        if ps_dates_display:
            parts = [
                f'{IGDB_PLATFORM_NAMES.get(e["platform"], str(e["platform"]))} {e["date"]}'
                for e in ps_dates_display
            ]
            self.stdout.write(f'              IGDB PS dates: {"  |  ".join(parts)}')

        igdb_companies = raw.get('involved_companies', []) if raw else []
        igdb_devs = []
        igdb_pubs = []
        for ic in igdb_companies:
            company = ic.get('company', {})
            name = company.get('name', '') if isinstance(company, dict) else ''
            if not name:
                continue
            if ic.get('developer'):
                igdb_devs.append(name)
            if ic.get('publisher'):
                igdb_pubs.append(name)
        self.stdout.write(
            f'  [IGDB co.]  dev: {", ".join(igdb_devs) or "unknown"}  '
            f'|  pub: {", ".join(igdb_pubs) or "unknown"}'
        )
        self.stdout.write(f'  [IGDB row]  {self._format_game_result(raw, current_igdb_id=match.igdb_id).strip()}')
        self.stdout.write('')

    # -------------------------------------------------------------------
    # Unmatched mode: interactive queue for concepts with no match
    # -------------------------------------------------------------------

    def _handle_unmatched(self, options):
        # Concepts whose last enrichment attempt produced no result.
        # These have an IGDBMatch row with status='no_match' as a marker.
        # Truly untried concepts (no row at all) are surfaced by running the
        # default enrich pass first; once tried, they land here automatically.
        qs = Concept.objects.filter(
            igdb_match__status='no_match'
        ).select_related('family').prefetch_related('games')

        if options.get('badge'):
            badge_concept_ids = self._get_badge_concept_ids()
            qs = qs.filter(id__in=badge_concept_ids)

        concepts = list(qs)

        if options.get('concept_id'):
            concepts = [c for c in concepts if c.concept_id == options['concept_id']]

        total = len(concepts)
        if total == 0:
            self.stdout.write('No unmatched concepts.')
            return

        self.stdout.write(f'{total} unmatched concept(s).')
        self.stdout.write('Commands: [s]earch <query>  [sa]earch all <query>  [m]anual <igdb_id>  [n]ext  [q]uit\n')

        assigned = 0
        skipped = 0
        idx = 0

        while idx < len(concepts):
            concept = concepts[idx]
            self._print_unmatched_header(idx, total, concept)

            while True:
                try:
                    action = input('  > ').strip().lower()
                except (EOFError, KeyboardInterrupt):
                    self.stdout.write('\n')
                    self._print_unmatched_summary(assigned, skipped)
                    return

                if not action:
                    continue

                if action == 'n':
                    self.stdout.write('  Skipped.')
                    skipped += 1
                    break

                elif action == 'q':
                    self._print_unmatched_summary(assigned, skipped)
                    return

                elif action.startswith('sa ') or action.startswith('sa\t'):
                    query = action[3:].strip()
                    if not query:
                        self.stdout.write('  Usage: sa <search query>')
                        continue
                    self.stdout.write(f'  Searching IGDB (all platforms) for "{query}"...')
                    results = IGDBService.search_game(query, limit=10, platform_filter=False)
                    exact = IGDBService.search_by_exact_name(query, limit=10)
                    seen = {r['id'] for r in (results or [])}
                    extra = [r for r in (exact or []) if r['id'] not in seen]
                    all_results = (results or []) + extra
                    if all_results:
                        for r in all_results:
                            self.stdout.write(self._format_game_result(r))
                    else:
                        self.stdout.write('  No results found.')

                elif action.startswith('s ') or action.startswith('s\t'):
                    query = action[2:].strip()
                    if not query:
                        self.stdout.write('  Usage: s <search query>')
                        continue
                    self.stdout.write(f'  Searching IGDB (PlayStation) for "{query}"...')
                    results = IGDBService.search_game(query, limit=10, platform_filter=True)
                    exact = IGDBService.search_by_exact_name(query, limit=10)
                    seen = {r['id'] for r in (results or [])}
                    extra = [r for r in (exact or []) if r['id'] not in seen]
                    all_results = (results or []) + extra
                    if all_results:
                        for r in all_results:
                            self.stdout.write(self._format_game_result(r))
                    else:
                        self.stdout.write('  No results found.')

                elif action.startswith('m ') or action.startswith('m\t'):
                    try:
                        new_igdb_id = int(action[2:].strip())
                    except ValueError:
                        self.stdout.write('  Usage: m <igdb_id> (number)')
                        continue
                    self.stdout.write(f'  Fetching IGDB #{new_igdb_id}...')
                    igdb_data = IGDBService.get_game_details(new_igdb_id)
                    if not igdb_data:
                        self.stdout.write(self.style.ERROR(f'  IGDB #{new_igdb_id} not found.'))
                        continue

                    self.stdout.write(f'  Name:      "{igdb_data.get("name")}"')
                    self.stdout.write(f'  Entry:     {self._format_game_result(igdb_data).strip()}')
                    ic_devs = []
                    ic_pubs = []
                    for ic in igdb_data.get('involved_companies', []):
                        co = ic.get('company', {})
                        name = co.get('name', '') if isinstance(co, dict) else ''
                        if name and ic.get('developer'):
                            ic_devs.append(name)
                        if name and ic.get('publisher'):
                            ic_pubs.append(name)
                    self.stdout.write(f'  Developer: {", ".join(ic_devs) or "unknown"}')
                    self.stdout.write(f'  Publisher: {", ".join(ic_pubs) or "unknown"}')

                    try:
                        confirm = input('  Assign this match? [y/n] > ').strip().lower()
                    except (EOFError, KeyboardInterrupt):
                        self.stdout.write('\n')
                        self._print_unmatched_summary(assigned, skipped)
                        return

                    if confirm == 'y':
                        new_match = IGDBService.process_match(concept, igdb_data, 1.0, 'manual')
                        self.stdout.write(self.style.SUCCESS(
                            f'  Assigned IGDB #{new_igdb_id} "{igdb_data.get("name")}" [{new_match.status}]'
                        ))
                        assigned += 1
                        self._surface_related_after_action(
                            igdb_data.get('name') or concept.unified_title,
                            concepts, idx,
                            title_fn=lambda c: c.unified_title,
                            other_status='pending_review',
                        )
                        break
                    else:
                        self.stdout.write('  Cancelled.')

                else:
                    self.stdout.write('  Commands: [s]earch <query>  [sa] search all <query>  [m]anual <igdb_id>  [n]ext  [q]uit')

            idx += 1
            self.stdout.write('')

        self._print_unmatched_summary(assigned, skipped)

    def _print_unmatched_summary(self, assigned, skipped):
        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS('Unmatched Review Complete'))
        self.stdout.write(f'  Assigned:  {assigned}')
        self.stdout.write(f'  Skipped:   {skipped}')

    # -------------------------------------------------------------------
    # Search mode: search IGDB and display results
    # -------------------------------------------------------------------

    def _handle_search(self, options):
        query = options['search']
        self.stdout.write(f'Searching IGDB for "{query}"...\n')

        # Try fuzzy search first
        results = IGDBService.search_game(query, limit=10, platform_filter=True)
        if results:
            self.stdout.write(f'  Search results ({len(results)}):\n')
            self._print_search_results(results)

        # Also try exact name query (catches base games buried under DLC)
        exact_results = IGDBService.search_by_exact_name(query, limit=10)
        # Deduplicate against search results
        seen_ids = {r['id'] for r in (results or [])}
        new_exact = [r for r in exact_results if r['id'] not in seen_ids]
        if new_exact:
            self.stdout.write(f'\n  Exact name matches ({len(new_exact)} additional):\n')
            self._print_search_results(new_exact)

        if not results and not new_exact:
            self.stdout.write('  No results found on IGDB.')
            return

        self.stdout.write(f'\nUse --manual <IGDB_ID> --concept-id <CONCEPT_ID> to assign.')

    def _print_search_results(self, results):
        for r in results:
            self.stdout.write(self._format_game_result(r))
            alt_names = r.get('alternative_names', [])
            if alt_names:
                alts = ', '.join(a.get('name', '') for a in alt_names[:5])
                self.stdout.write(f'              alt names: {alts}')

    # -------------------------------------------------------------------
    # Manual mode: assign a specific IGDB game to a concept
    # -------------------------------------------------------------------

    def _handle_manual(self, options):
        igdb_id = options['manual']
        concept_id = options.get('concept_id')

        if not concept_id:
            self.stdout.write(self.style.ERROR(
                '--manual requires --concept-id. Example:\n'
                '  python manage.py enrich_from_igdb --concept-id 10010692 --manual 6065'
            ))
            return

        try:
            concept = Concept.objects.get(concept_id=concept_id)
        except Concept.DoesNotExist:
            self.stdout.write(self.style.ERROR(f'No concept found with concept_id="{concept_id}"'))
            return

        self.stdout.write(f'Fetching IGDB game #{igdb_id}...')
        igdb_data = IGDBService.get_game_details(igdb_id)
        if not igdb_data:
            self.stdout.write(self.style.ERROR(f'IGDB game #{igdb_id} not found.'))
            return

        self.stdout.write(f'  IGDB: "{igdb_data.get("name")}"')
        self.stdout.write(f'  PSN:  "{concept.unified_title}"')

        if options['dry_run']:
            self.stdout.write(self.style.WARNING('[DRY RUN] Would assign this match.'))
            return

        igdb_match = IGDBService.process_match(concept, igdb_data, 1.0, 'manual')
        self.stdout.write(self.style.SUCCESS(
            f'Manually matched "{concept.unified_title}" -> "{igdb_data.get("name")}" '
            f'(IGDB #{igdb_id}) [{igdb_match.status}]'
        ))

    # -------------------------------------------------------------------
    # Refresh mode: re-fetch data for existing matches
    # -------------------------------------------------------------------

    def _handle_refresh(self, options):
        dry_run = options['dry_run']

        # Oldest-first ordering so consecutive runtime-capped runs roll
        # naturally through the catalog. NULLS FIRST is defensive —
        # last_synced_at is auto_now_add so should never be null on
        # saved rows, but the explicit ordering avoids surprises.
        matches = (
            IGDBMatch.objects
            .filter(status__in=('auto_accepted', 'accepted'))
            .select_related('concept')
            .order_by(F('last_synced_at').asc(nulls_first=True))
        )

        if options['concept_id']:
            matches = matches.filter(concept__concept_id=options['concept_id'])

        stale_before = options.get('stale_before')
        if stale_before:
            try:
                cutoff_date = datetime.strptime(stale_before, '%Y-%m-%d').date()
            except ValueError:
                raise CommandError(
                    f'--stale-before must be in YYYY-MM-DD format (got "{stale_before}")'
                )
            cutoff = datetime.combine(
                cutoff_date, datetime.min.time(), tzinfo=dt_timezone.utc
            )
            matches = matches.filter(last_synced_at__lt=cutoff)
            self.stdout.write(
                f'Filtering to matches last synced before {cutoff.isoformat()}.'
            )

        total = matches.count()
        if total == 0:
            self.stdout.write('No accepted matches to refresh.')
            return

        self.stdout.write(f'Refreshing {total} IGDB match(es)...')
        if dry_run:
            self.stdout.write(self.style.WARNING('[DRY RUN] No changes will be saved.'))

        max_minutes = options.get('max_minutes')
        max_seconds = max_minutes * 60 if max_minutes else None
        loop_start = time.monotonic()
        capped_at = None

        refreshed = 0
        not_found = 0
        errors = 0

        for i, igdb_match in enumerate(matches.iterator()):
            if max_seconds is not None and (time.monotonic() - loop_start) >= max_seconds:
                capped_at = i
                break
            try:
                if dry_run:
                    self.stdout.write(
                        f'  [{i + 1}/{total}] {igdb_match.concept.concept_id} '
                        f'"{igdb_match.concept.unified_title}" (IGDB #{igdb_match.igdb_id})'
                    )
                    refreshed += 1
                    continue

                result = IGDBService.refresh_match(igdb_match)
                if result:
                    refreshed += 1
                    self.stdout.write(self.style.SUCCESS(
                        f'  [{i + 1}/{total}] {igdb_match.concept.concept_id} '
                        f'"{igdb_match.concept.unified_title}" [refreshed]'
                    ))
                else:
                    not_found += 1
                    self.stdout.write(self.style.WARNING(
                        f'  [{i + 1}/{total}] {igdb_match.concept.concept_id} '
                        f'"{igdb_match.concept.unified_title}" [not found on IGDB]'
                    ))

            except Exception as e:
                errors += 1
                self.stdout.write(self.style.ERROR(
                    f'  [{i + 1}/{total}] ERROR {igdb_match.concept.concept_id} '
                    f'"{igdb_match.concept.unified_title}": {e}'
                ))

        if capped_at is not None:
            elapsed = time.monotonic() - loop_start
            remaining = max(total - capped_at, 0)
            self.stdout.write(self.style.WARNING(
                f'\nReached --max-minutes cap of {max_minutes} min '
                f'({elapsed/60:.1f} min elapsed) after {capped_at} match(es); '
                f'{remaining} remaining will be picked up on the next run '
                f'(oldest-first ordering naturally rolls through the catalog).'
            ))

        prefix = '[DRY RUN] ' if dry_run else ''
        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS(f'{prefix}IGDB Refresh Complete'))
        self.stdout.write(f'  Refreshed:  {refreshed}')
        if not_found:
            self.stdout.write(self.style.WARNING(f'  Not found:  {not_found}'))
        if errors:
            self.stdout.write(self.style.ERROR(f'  Errors:     {errors}'))

    # -------------------------------------------------------------------
    # Enrich mode: match and enrich concepts
    # -------------------------------------------------------------------

    # -------------------------------------------------------------------
    # Display helpers
    # -------------------------------------------------------------------

    _DLC_NAME_PATTERNS = re.compile(
        r' - .+(?:Skin|Pack|Bundle|DLC|Season\s+\d|Edition|Steelbook|Set\b)',
        re.IGNORECASE,
    )

    def _format_game_result(self, r, current_igdb_id=None):
        """Format a single IGDB game result for display."""
        # Prefer IGDB v4 `game_type` (replaces deprecated `category`). IGDB
        # expands game_type as {id, type, checksum, ...} with `.*` — `type`
        # is the human-readable label ("Main Game", "Bundle", "Port", ...).
        # Fall back to legacy `category` for older cached responses that
        # predate the v4 migration, then name-pattern heuristic, then default.
        cat_display = None
        game_type = r.get('game_type')
        if isinstance(game_type, dict):
            cat_display = game_type.get('type')
            if not cat_display and game_type.get('id') is not None:
                cat_display = dict(IGDBMatch.GAME_CATEGORY_CHOICES).get(
                    game_type['id'], f'Unknown ({game_type["id"]})'
                )

        if not cat_display:
            cat = r.get('category')
            if cat is not None:
                cat_display = dict(IGDBMatch.GAME_CATEGORY_CHOICES).get(cat, f'Unknown ({cat})')
            elif self._DLC_NAME_PATTERNS.search(r.get('name', '')):
                cat_display = 'Likely DLC'
            else:
                cat_display = 'Main Game?'  # trailing ? flags the unconfirmed fallback

        platforms = r.get('platforms', [])
        plat_names = []
        for p in platforms:
            pid = p if isinstance(p, int) else p.get('id') if isinstance(p, dict) else None
            plat_names.append(IGDB_PLATFORM_NAMES.get(pid, str(pid)))

        release_year = self._format_igdb_release_year(r.get('first_release_date'))
        year_tag = f'  ({release_year})' if release_year else ''

        marker = ' <-- current' if current_igdb_id and r.get('id') == current_igdb_id else ''
        return (
            f'    IGDB #{r["id"]:>8}  "{r.get("name")}"{year_tag}  '
            f'[{cat_display}]  {", ".join(plat_names)}{marker}'
        )

    @staticmethod
    def _format_igdb_release_year(timestamp):
        """Format an IGDB first_release_date unix timestamp as a 4-digit year.

        Returns '' on missing/invalid timestamps — IGDB release dates are
        community-sourced and frequently absent on obscure or pre-release
        entries.
        """
        if not timestamp:
            return ''
        try:
            return datetime.fromtimestamp(int(timestamp), tz=dt_timezone.utc).strftime('%Y')
        except (ValueError, OSError, TypeError):
            return ''

    def _handle_enrich(self, options):
        dry_run = options['dry_run']
        force = options['force']

        # Build queryset
        if options['concept_id']:
            concepts = Concept.objects.filter(concept_id=options['concept_id'])
            if not concepts.exists():
                self.stdout.write(self.style.ERROR(
                    f"No concept found with concept_id='{options['concept_id']}'"
                ))
                return
        elif options['pending']:
            concept_ids = IGDBMatch.objects.filter(
                status='pending_review'
            ).values_list('concept_id', flat=True)
            concepts = Concept.objects.filter(id__in=concept_ids)
        elif options['retry_no_match']:
            concepts = (
                Concept.objects
                .filter(igdb_match__status='no_match')
                .order_by('igdb_match__last_synced_at')
            )
        elif options['missing_or_no_match']:
            # Union: concepts with no IGDBMatch row + concepts marked no_match.
            # Oldest-first by last_synced_at so missing rows (NULL) process
            # first and stale no_match rows rotate through across weekly runs.
            concepts = (
                Concept.objects
                .filter(Q(igdb_match__isnull=True) | Q(igdb_match__status='no_match'))
                .order_by(F('igdb_match__last_synced_at').asc(nulls_first=True))
            )
        elif options['all'] or force:
            concepts = Concept.objects.all()
        else:
            # Default: missing — concepts with no IGDBMatch row at all.
            # no_match rows count as "tried already" and are excluded; use
            # --retry-no-match or --missing-or-no-match to re-attempt them.
            matched_ids = IGDBMatch.objects.values_list('concept_id', flat=True)
            concepts = Concept.objects.exclude(id__in=matched_ids)


        total = concepts.count()
        if total == 0:
            self.stdout.write('No concepts to process.')
            return

        self.stdout.write(f'Processing {total} concept(s)...')
        if dry_run:
            self.stdout.write(self.style.WARNING('[DRY RUN] No changes will be saved.'))

        summary = {
            'auto_accepted': 0,
            'pending_review': 0,
            'no_match': 0,
            'errors': 0,
        }

        max_minutes = options.get('max_minutes')
        max_seconds = max_minutes * 60 if max_minutes else None
        loop_start = time.monotonic()
        capped_at = None

        for i, concept in enumerate(concepts.iterator()):
            if max_seconds is not None and (time.monotonic() - loop_start) >= max_seconds:
                capped_at = i
                break
            try:
                # Capture the search input the pipeline will use so we can
                # surface it in the per-concept output line. _pick_search_title
                # is pure and cheap; the subsequent match_concept call will
                # compute the same string internally.
                search_title = IGDBService._pick_search_title(concept)
                result = IGDBService.match_concept(concept)

                if not result:
                    if not dry_run:
                        IGDBService.record_no_match(concept)
                    summary['no_match'] += 1
                    self._log_progress(i + 1, total, concept, 'no_match', search_title=search_title)
                    continue

                igdb_data, confidence, method = result

                if dry_run:
                    status = 'auto_accepted' if confidence >= 0.85 else 'pending_review'
                    summary[status] += 1
                    self._log_progress(
                        i + 1, total, concept, status,
                        igdb_name=igdb_data.get('name', ''),
                        confidence=confidence,
                        method=method,
                        search_title=search_title,
                    )
                    continue

                if force:
                    # Clear existing match before re-processing
                    IGDBMatch.objects.filter(concept=concept).delete()

                igdb_match = IGDBService.process_match(concept, igdb_data, confidence, method)
                summary[igdb_match.status] = summary.get(igdb_match.status, 0) + 1
                self._log_progress(
                    i + 1, total, concept, igdb_match.status,
                    igdb_name=igdb_data.get('name', ''),
                    confidence=confidence,
                    method=method,
                    search_title=search_title,
                )

            except Exception as e:
                summary['errors'] += 1
                self.stdout.write(self.style.ERROR(
                    f'  [{i + 1}/{total}] ERROR {concept.concept_id} '
                    f'"{concept.unified_title}": {e}'
                ))

        if capped_at is not None:
            elapsed = time.monotonic() - loop_start
            remaining = max(total - capped_at, 0)
            self.stdout.write(self.style.WARNING(
                f'\nReached --max-minutes cap of {max_minutes} min '
                f'({elapsed/60:.1f} min elapsed) after {capped_at} concept(s); '
                f'{remaining} remaining will be picked up on the next run.'
            ))

        self._print_summary(summary, total, dry_run)

    def _log_progress(self, current, total, concept, status,
                      igdb_name='', confidence=0.0, method='', search_title=''):
        style_map = {
            'auto_accepted': self.style.SUCCESS,
            'accepted': self.style.SUCCESS,
            'pending_review': self.style.WARNING,
            'no_match': lambda x: x,
            'rejected': self.style.ERROR,
        }
        style = style_map.get(status, lambda x: x)

        msg = f'  [{current}/{total}] {concept.concept_id} "{concept.unified_title}"'
        # Only surface the search title when it differs from the concept's
        # unified_title — i.e. when _pick_search_title chose the game title
        # over the concept title. For single-game concepts where both are
        # identical this adds noise; for compilation-candidate concepts or
        # Asian-region concepts where the game title is richer, it's the
        # important context.
        if search_title and search_title != concept.unified_title:
            msg += f' (searched as "{search_title}")'
        if igdb_name:
            msg += f' -> "{igdb_name}" ({method}, {confidence:.0%})'
        msg += f' [{status}]'
        self.stdout.write(style(msg))

    def _print_summary(self, summary, total, dry_run):
        prefix = '[DRY RUN] ' if dry_run else ''
        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS(f'{prefix}IGDB Enrichment Complete'))
        self.stdout.write(f'  Total processed: {total}')
        self.stdout.write(self.style.SUCCESS(
            f'  Auto-accepted:   {summary["auto_accepted"]}'
        ))
        self.stdout.write(self.style.WARNING(
            f'  Pending review:  {summary["pending_review"]}'
        ))
        self.stdout.write(
            f'  No match:        {summary["no_match"]}'
        )
        if summary['errors']:
            self.stdout.write(self.style.ERROR(
                f'  Errors:          {summary["errors"]}'
            ))
