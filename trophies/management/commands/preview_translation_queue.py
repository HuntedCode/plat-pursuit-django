import re

from django.core.management.base import BaseCommand

from trophies.models import Concept


# CJK-family character ranges. Any character in these blocks flags a title as
# "likely needs translation". Covers Japanese (Hiragana, Katakana, Kanji),
# Korean (Hangul), Simplified/Traditional Chinese, plus common extensions and
# fullwidth ASCII forms that appear in mixed titles like "NEW GAME！".
_CJK_PATTERN = re.compile(
    '['
    '\u3040-\u309F'   # Hiragana
    '\u30A0-\u30FF'   # Katakana
    '\u3400-\u4DBF'   # CJK Unified Ideographs Extension A
    '\u4E00-\u9FFF'   # CJK Unified Ideographs
    '\uAC00-\uD7AF'   # Hangul Syllables
    '\u1100-\u11FF'   # Hangul Jamo
    '\u3130-\u318F'   # Hangul Compatibility Jamo
    '\uFF00-\uFFEF'   # Halfwidth and Fullwidth Forms
    ']'
)


class Command(BaseCommand):
    help = (
        "Preview the next batch of no_match concepts in the same order "
        "'enrich_from_igdb --unmatched' will surface them. Output is formatted "
        "for pasting into a translation pass: native title + context (platforms, "
        "publisher, year, trophy count) for disambiguation."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--count', type=int, default=50,
            help='Number of concepts to include in the preview (default: 50).',
        )
        parser.add_argument(
            '--skip', type=int, default=0,
            help='Skip the first N entries of the queue (for resuming mid-session).',
        )
        parser.add_argument(
            '--include-english', action='store_true',
            help='Include concepts whose title has no CJK characters (default: exclude).',
        )

    def handle(self, *args, **options):
        count = options['count']
        skip = options['skip']
        include_english = options['include_english']

        # Mirror enrich_from_igdb._handle_unmatched so preview order == review order.
        qs = Concept.objects.filter(
            igdb_match__status='no_match'
        ).prefetch_related('games')

        concepts = list(qs)
        pool_total = len(concepts)

        if not include_english:
            concepts = [c for c in concepts if self._has_cjk(c.unified_title)]

        filtered_total = len(concepts)
        window = concepts[skip:skip + count]

        if not window:
            self.stdout.write(self.style.SUCCESS(
                f'No concepts in window (pool={pool_total}, after CJK filter={filtered_total}, skip={skip}).'
            ))
            return

        header_suffix = '' if include_english else ', CJK-filtered'
        self.stdout.write(
            f'=== Queue preview (positions {skip + 1}-{skip + len(window)} of {filtered_total}'
            f'{header_suffix}) ==='
        )
        self.stdout.write(
            f'Pool: {pool_total} no_match concepts total. '
            f'{filtered_total} after filter. Showing {len(window)}.'
        )
        self.stdout.write('')

        for offset, concept in enumerate(window):
            idx = skip + offset + 1
            self.stdout.write(self._format_row(idx, concept))

    @staticmethod
    def _has_cjk(text):
        if not text:
            return False
        return bool(_CJK_PATTERN.search(text))

    @staticmethod
    def _format_row(idx, concept):
        platforms = set()
        trophy_total = 0
        for game in concept.games.all():
            for p in (game.title_platform or []):
                platforms.add(p)
            if game.defined_trophies:
                trophy_total = max(trophy_total, game.get_total_defined_trophies())

        platforms_str = '/'.join(sorted(platforms)) if platforms else '?'
        publisher = concept.publisher_name or '?'
        year = concept.release_date.year if concept.release_date else '?'
        trophies = f'{trophy_total}T' if trophy_total else '?T'
        title = concept.unified_title or '(no title)'

        return f'[{idx}] {concept.concept_id} | {platforms_str} | {publisher} | {year} | {trophies} | {title}'
