import re

from django.core.management.base import BaseCommand

from trophies.models import Concept, IGDBMatch, Stage
from trophies.services.igdb_service import IGDBService, IGDB_PLATFORM_NAMES


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
            help='Re-process concepts previously recorded as no_match',
        )
        parser.add_argument(
            '--refresh', action='store_true',
            help='Re-fetch IGDB data for all accepted matches (no re-matching)',
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

    def handle(self, *args, **options):
        # Toggle verbose IGDB logging
        import logging
        igdb_logger = logging.getLogger('psn_api')
        if options.get('verbose'):
            igdb_logger.setLevel(logging.INFO)
        else:
            igdb_logger.setLevel(logging.WARNING)

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
        ).select_related('concept').prefetch_related('concept__games')

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

            # Display current match
            self.stdout.write(self.style.WARNING(
                f'--- [{idx + 1}/{total}] {concept.concept_id} "{concept.unified_title}" ---'
            ))
            concept_platforms = set()
            for game in concept.games.all():
                for p in (game.title_platform or []):
                    concept_platforms.add(p)
            self.stdout.write(f'  PSN platforms:  {", ".join(sorted(concept_platforms)) or "unknown"}')
            self.stdout.write(f'  PSN publisher:  {concept.publisher_name or "unknown"}')
            self.stdout.write(
                f'  Current match:  IGDB #{match.igdb_id} "{match.igdb_name}" '
                f'({match.match_method}, {match.match_confidence:.0%})'
            )

            # Show IGDB dev/publisher from raw response
            igdb_companies = match.raw_response.get('involved_companies', []) if match.raw_response else []
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
            self.stdout.write(f'  IGDB developer: {", ".join(igdb_devs) or "unknown"}')
            self.stdout.write(f'  IGDB publisher: {", ".join(igdb_pubs) or "unknown"}')

            # Show IGDB platforms for current match
            self.stdout.write(f'  IGDB entry:     {self._format_game_result(match.raw_response or {}, current_igdb_id=match.igdb_id).strip()}')
            self.stdout.write('')

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
                        break
                    else:
                        self.stdout.write('  Cancelled.')

                else:
                    self.stdout.write('  Commands: [a]pprove  [r]eject  [s]earch <query>  [sa] search all <query>  [m]anual <igdb_id>  [n]ext  [q]uit')

            idx += 1
            self.stdout.write('')

        self._print_review_summary(approved, rejected, reassigned, skipped)

    def _print_review_summary(self, approved, rejected, reassigned, skipped):
        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS('Review Complete'))
        self.stdout.write(f'  Approved:    {approved}')
        self.stdout.write(f'  Rejected:    {rejected}')
        self.stdout.write(f'  Reassigned:  {reassigned}')
        self.stdout.write(f'  Skipped:     {skipped}')

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
        ).prefetch_related('games')

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

            concept_platforms = set()
            for game in concept.games.all():
                for p in (game.title_platform or []):
                    concept_platforms.add(p)

            self.stdout.write(self.style.WARNING(
                f'--- [{idx + 1}/{total}] {concept.concept_id} "{concept.unified_title}" ---'
            ))
            self.stdout.write(f'  PSN platforms:  {", ".join(sorted(concept_platforms)) or "unknown"}')
            self.stdout.write(f'  PSN publisher:  {concept.publisher_name or "unknown"}')
            self.stdout.write('')

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

        matches = IGDBMatch.objects.filter(
            status__in=('auto_accepted', 'accepted'),
        ).select_related('concept')

        if options['concept_id']:
            matches = matches.filter(concept__concept_id=options['concept_id'])

        total = matches.count()
        if total == 0:
            self.stdout.write('No accepted matches to refresh.')
            return

        self.stdout.write(f'Refreshing {total} IGDB match(es)...')
        if dry_run:
            self.stdout.write(self.style.WARNING('[DRY RUN] No changes will be saved.'))

        refreshed = 0
        not_found = 0
        errors = 0

        for i, igdb_match in enumerate(matches.iterator()):
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
        cat = r.get('category')
        if cat is not None:
            cat_display = dict(IGDBMatch.GAME_CATEGORY_CHOICES).get(cat, f'Unknown ({cat})')
        elif self._DLC_NAME_PATTERNS.search(r.get('name', '')):
            cat_display = 'Likely DLC'
        else:
            cat_display = 'Main Game'

        platforms = r.get('platforms', [])
        plat_names = []
        for p in platforms:
            pid = p if isinstance(p, int) else p.get('id') if isinstance(p, dict) else None
            plat_names.append(IGDB_PLATFORM_NAMES.get(pid, str(pid)))

        marker = ' <-- current' if current_igdb_id and r.get('id') == current_igdb_id else ''
        return (
            f'    IGDB #{r["id"]:>8}  "{r.get("name")}"  '
            f'[{cat_display}]  {", ".join(plat_names)}{marker}'
        )

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
            concept_ids = IGDBMatch.objects.filter(
                status='no_match'
            ).values_list('concept_id', flat=True)
            concepts = Concept.objects.filter(id__in=concept_ids)
        elif options['all'] or force:
            concepts = Concept.objects.all()
        else:
            # Default: missing — concepts with no IGDBMatch row at all.
            # no_match rows count as "tried already" and are excluded; use
            # --retry-no-match to re-attempt them.
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

        for i, concept in enumerate(concepts.iterator()):
            try:
                result = IGDBService.match_concept(concept)

                if not result:
                    if not dry_run:
                        IGDBService.record_no_match(concept)
                    summary['no_match'] += 1
                    self._log_progress(i + 1, total, concept, 'no_match')
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
                )

            except Exception as e:
                summary['errors'] += 1
                self.stdout.write(self.style.ERROR(
                    f'  [{i + 1}/{total}] ERROR {concept.concept_id} '
                    f'"{concept.unified_title}": {e}'
                ))

        self._print_summary(summary, total, dry_run)

    def _log_progress(self, current, total, concept, status,
                      igdb_name='', confidence=0.0, method=''):
        style_map = {
            'auto_accepted': self.style.SUCCESS,
            'accepted': self.style.SUCCESS,
            'pending_review': self.style.WARNING,
            'no_match': lambda x: x,
            'rejected': self.style.ERROR,
        }
        style = style_map.get(status, lambda x: x)

        msg = f'  [{current}/{total}] {concept.concept_id} "{concept.unified_title}"'
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
