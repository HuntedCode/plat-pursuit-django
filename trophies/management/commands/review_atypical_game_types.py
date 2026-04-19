from django.core.management.base import BaseCommand

from trophies.models import IGDBMatch


# IGDB game_type IDs that represent a "standard" single-game entry. Anything
# outside this set (Bundle, DLC, Expansion, Pack, Episode, Season, Mod, etc.)
# surfaces for staff review — those matches may have been auto-accepted via
# title similarity but the concept structure often warrants a split or a
# different IGDB assignment.
NORMAL_GAME_TYPE_IDS = {
    0,   # Main Game
    8,   # Remake
    9,   # Remaster
    11,  # Port
}


class Command(BaseCommand):
    help = (
        "List accepted IGDBMatch rows whose IGDB game_type is not a standard "
        "single-game entry (i.e. not Main Game / Remake / Remaster / Port). "
        "Useful for triaging Bundles, DLC, Expansions, Packs, and similar "
        "entries after the v4 field migration refresh has run."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--types', nargs='+', type=int, default=None,
            help='Restrict to specific game_type IDs (space-separated). '
                 'Default: everything except 0 (Main Game), 8 (Remake), '
                 '9 (Remaster), 11 (Port).',
        )
        parser.add_argument(
            '--status', nargs='+',
            default=['accepted', 'auto_accepted'],
            help='Restrict to IGDBMatch status values. Default: accepted + auto_accepted.',
        )
        parser.add_argument(
            '--count', type=int, default=None,
            help='Cap total rows printed.',
        )

    def handle(self, *args, **options):
        types_filter = options['types']
        status_filter = options['status']
        count_cap = options['count']

        qs = IGDBMatch.objects.filter(
            status__in=status_filter,
        ).select_related('concept').prefetch_related('concept__games')

        total = qs.count()

        atypical_by_type = {}
        refreshed = 0
        skipped_pre_refresh = 0

        for match in qs.iterator():
            game_type = (match.raw_response or {}).get('game_type')
            if not isinstance(game_type, dict):
                skipped_pre_refresh += 1
                continue
            refreshed += 1
            gt_id = game_type.get('id')
            gt_name = game_type.get('type', 'Unknown')

            if types_filter is not None:
                if gt_id not in types_filter:
                    continue
            elif gt_id in NORMAL_GAME_TYPE_IDS:
                continue

            atypical_by_type.setdefault(gt_name, []).append((match, gt_id))

        total_atypical = sum(len(v) for v in atypical_by_type.values())

        self.stdout.write(f'=== Atypical game_type review ({total_atypical} matches) ===')
        self.stdout.write(
            f'Scope: {total} match(es) with status in {status_filter}. '
            f'Refreshed (have game_type): {refreshed}. '
            f'Pre-refresh (skipped): {skipped_pre_refresh}.'
        )
        if skipped_pre_refresh and not types_filter:
            self.stdout.write(self.style.WARNING(
                f'Note: {skipped_pre_refresh} match(es) have not yet been refreshed '
                f'against the new IGDB fields and cannot be classified. Run '
                f'`enrich_from_igdb --refresh` to populate game_type on those rows.'
            ))
        self.stdout.write('')

        if not atypical_by_type:
            self.stdout.write(self.style.SUCCESS('No atypical matches in scope.'))
            return

        printed = 0
        # Largest buckets first, then alphabetical by type name for stability.
        for gt_name in sorted(atypical_by_type, key=lambda n: (-len(atypical_by_type[n]), n)):
            rows = atypical_by_type[gt_name]
            # Alphabetical by concept title within each bucket.
            rows.sort(key=lambda mt: (mt[0].concept.unified_title or '').lower())
            self.stdout.write(f'--- {gt_name} ({len(rows)}) ---')
            for match, gt_id in rows:
                if count_cap is not None and printed >= count_cap:
                    self.stdout.write(f'... truncated at --count={count_cap} ...')
                    return
                self.stdout.write(self._format_row(printed + 1, match, gt_id))
                printed += 1
            self.stdout.write('')

    @staticmethod
    def _format_row(idx, match, gt_id):
        concept = match.concept
        platforms = set()
        for game in concept.games.all():
            for p in (game.title_platform or []):
                platforms.add(p)
        platforms_str = '/'.join(sorted(platforms)) if platforms else '?'
        publisher = concept.publisher_name or '?'
        year = concept.release_date.year if concept.release_date else '?'
        title = concept.unified_title or '(no title)'
        return (
            f'[{idx}] {concept.concept_id} | {platforms_str} | {publisher} | '
            f'{year} | {title} -> {match.igdb_name} (type_id={gt_id})'
        )
