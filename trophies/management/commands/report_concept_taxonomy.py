"""Report anchored, non-shovelware, developer-attributed concepts and the genre /
theme structure of that set - including COMBINATIONS and co-occurrence, since
auto-detecting 20+ gamification jobs keys off genre/theme *sets*, not just single
marginals.

Scope of the concept set (all three must hold):
  - Anchored:       Concept.anchor_migration_completed_at is set (the project's
                    source-of-truth "do not reassign" flag).
  - Non-shovelware: the Concept has >= 1 Game whose shovelware_status is 'clean'
                    or 'manually_cleared' (not EVERY game is shovelware-flagged).
                    Mirrors `anchor_concepts --non-shovelware`.
  - Has a dev:      the Concept carries >= 1 company in a developer role - a main
                    developer (is_developer) and/or a porting developer
                    (is_porting). Publisher/supporting-only concepts are excluded.

Optional --badge-stages narrows to concepts that sit in a Badge stage (the curated
games behind the registered badges) - the real near-term jobs sample, vs the full
library. Same other criteria, so it's an apples-to-apples narrowing.

Output (stdout):
  - Totals + taxonomy gaps (concepts with no genre / no theme).
  - Genre marginals and Theme marginals (single value -> concept count).
  - Genre COMBINATIONS and Theme COMBINATIONS (exact set -> count, top N).
  - Genre x Theme co-occurrence (top (genre, theme) pairs).
Plus a per-concept CSV (--output, default anchored_concept_taxonomy.csv):
  concept_id, title, slug, genres (|-joined), themes (|-joined).

This is an offline analysis command over the curated library (bounded by catalog
size, not by any user's data), so it loads the concept->genre/theme maps into
memory once and does the combination math in Python. Run-once; not a request path.
"""
import csv
from collections import Counter, defaultdict

from django.core.management.base import BaseCommand
from django.db.models import Q

from trophies.models import Concept, ConceptGenre, ConceptTheme, Stage

NON_SHOVELWARE_STATUSES = ('clean', 'manually_cleared')


class Command(BaseCommand):
    help = (
        "Report anchored, non-shovelware, developer-attributed concepts and the "
        "genre/theme combinations of that set (informs gamification jobs/XP)."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--output', default='anchored_concept_taxonomy.csv',
            help='Path for the per-concept CSV (default: anchored_concept_taxonomy.csv).',
        )
        parser.add_argument(
            '--no-csv', action='store_true',
            help='Skip the per-concept CSV; print the analysis only.',
        )
        parser.add_argument(
            '--badge-stages', action='store_true', dest='badge_stages',
            help=(
                'Restrict to concepts that appear in a Badge stage (Stage.concepts '
                'or a ConceptBundle on a stage) - the real near-term jobs sample: '
                'the curated games behind the registered badges. Same other criteria, '
                'so it is an apples-to-apples narrowing of the full run.'
            ),
        )
        parser.add_argument(
            '--by-stage', action='store_true', dest='by_stage',
            help=(
                'Analyze at the BADGE STAGE level (the gamification XP unit) instead of '
                'per-concept: how many specialization genres a stage spans (informs jobs-'
                'per-stage), and which genres/themes cover the most stages (informs the '
                'job catalog). Implies --badge-stages.'
            ),
        )
        parser.add_argument(
            '--base-genre', default='Adventure',
            help='Near-universal base genre excluded from per-stage "specialization" counts (default: Adventure).',
        )
        parser.add_argument(
            '--top', type=int, default=40,
            help='Rows to show per COMBINATION / co-occurrence table (0 = all). Marginals always show in full.',
        )

    def handle(self, *args, **options):
        top = options['top']

        by_stage = options['by_stage']
        badge_stages = options['badge_stages'] or by_stage  # --by-stage implies badge scope

        # Target concept set: anchored AND non-shovelware AND developer-attributed,
        # optionally narrowed to concepts that sit in a Badge stage.
        concepts = (
            Concept.objects
            .filter(anchor_migration_completed_at__isnull=False)
            .filter(games__shovelware_status__in=NON_SHOVELWARE_STATUSES)
            .filter(Q(concept_companies__is_developer=True) | Q(concept_companies__is_porting=True))
        )
        if badge_stages:
            concepts = concepts.filter(Q(stages__isnull=False) | Q(bundles__isnull=False))
        concepts = concepts.distinct()

        # Identity rows + the two taxonomy maps, loaded once.
        id_rows = list(concepts.values_list('id', 'concept_id', 'unified_title', 'slug'))
        concept_ids = [r[0] for r in id_rows]
        total = len(concept_ids)

        if not total:
            self.stdout.write(self.style.WARNING('No anchored, non-shovelware, developer-attributed concepts found.'))
            return

        id_set = set(concept_ids)
        genre_by_concept = defaultdict(set)
        for cid, gname in ConceptGenre.objects.filter(concept_id__in=id_set).values_list('concept_id', 'genre__name'):
            genre_by_concept[cid].add(gname)
        theme_by_concept = defaultdict(set)
        for cid, tname in ConceptTheme.objects.filter(concept_id__in=id_set).values_list('concept_id', 'theme__name'):
            theme_by_concept[cid].add(tname)

        if by_stage:
            self._stage_report(id_set, genre_by_concept, theme_by_concept, options['base_genre'])
            return

        # --- Aggregations (in-memory over the bounded concept set) ---
        genre_marginal = Counter()
        theme_marginal = Counter()
        genre_combo = Counter()
        theme_combo = Counter()
        pair = Counter()
        no_genre = no_theme = 0

        for cid in concept_ids:
            gs = genre_by_concept.get(cid, set())
            ts = theme_by_concept.get(cid, set())
            if not gs:
                no_genre += 1
            if not ts:
                no_theme += 1
            for g in gs:
                genre_marginal[g] += 1
            for t in ts:
                theme_marginal[t] += 1
            genre_combo[tuple(sorted(gs))] += 1
            theme_combo[tuple(sorted(ts))] += 1
            for g in gs:
                for t in ts:
                    pair[(g, t)] += 1

        # --- Output ---
        w = self.stdout.write
        head = self.style.MIGRATE_HEADING
        scope = ' (BADGE-STAGE GAMES ONLY)' if badge_stages else ''
        w(head(f'Anchored / non-shovelware / developer-attributed concept taxonomy{scope}'))
        w(f'  Total concepts:   {total:>7,}')
        if badge_stages:
            series_n = Stage.objects.filter(
                Q(concepts__id__in=id_set) | Q(concept_bundles__concepts__id__in=id_set)
            ).values('series_slug').distinct().count()
            w(f'  Badge series:     {series_n:>7,}')
        w(f'  With >=1 genre:   {total - no_genre:>7,}   (no genre: {no_genre:,})')
        w(f'  With >=1 theme:   {total - no_theme:>7,}   (no theme: {no_theme:,})')
        w(f'  Distinct genres:  {len(genre_marginal):>7,}')
        w(f'  Distinct themes:  {len(theme_marginal):>7,}')
        w(f'  Distinct genre-sets: {len(genre_combo):>4,}      Distinct theme-sets: {len(theme_combo):,}')

        self._marginal('Genre marginals (single genre -> concepts)', genre_marginal, total)
        self._marginal('Theme marginals (single theme -> concepts)', theme_marginal, total)
        self._combo('Genre COMBINATIONS (exact genre-set -> concepts)', genre_combo, total, top)
        self._combo('Theme COMBINATIONS (exact theme-set -> concepts)', theme_combo, total, top)
        self._pairs('Genre x Theme co-occurrence (top pairs)', pair, total, top)

        # --- Per-concept CSV (built from the in-memory maps) ---
        if options['no_csv']:
            return
        path = options['output']
        with open(path, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow(['concept_id', 'title', 'slug', 'genres', 'themes'])
            for _id, concept_id, title, slug in sorted(id_rows, key=lambda r: (r[2] or '').lower()):
                genres = '|'.join(sorted(genre_by_concept.get(_id, ())))
                themes = '|'.join(sorted(theme_by_concept.get(_id, ())))
                writer.writerow([concept_id, title, slug, genres, themes])
        w('')
        w(self.style.SUCCESS(f'Wrote per-concept CSV ({total:,} rows) -> {path}'))

    def _stage_report(self, id_set, genre_by_concept, theme_by_concept, base_genre):
        """Stage-level view: stages are the gamification XP unit, so aggregate each
        Badge stage's member concepts and ask how many *specialization* genres it
        spans (jobs-per-stage signal) and which genres/themes cover the most stages
        (job-catalog signal)."""
        # stage -> set of qualifying concept ids (direct + bundle members).
        stage_concepts = defaultdict(set)
        for sid, cid in Stage.objects.values_list('id', 'concepts__id'):
            if cid in id_set:
                stage_concepts[sid].add(cid)
        for sid, cid in Stage.objects.values_list('id', 'concept_bundles__concepts__id'):
            if cid in id_set:
                stage_concepts[sid].add(cid)
        stage_concepts = {s: cs for s, cs in stage_concepts.items() if cs}

        w = self.stdout.write
        head = self.style.MIGRATE_HEADING
        n_stages = len(stage_concepts)
        if not n_stages:
            w(self.style.WARNING('No badge stages with qualifying concepts.'))
            return

        spec_hist = Counter()        # # specialization genres (excl base) per stage
        dom_spec_hist = Counter()    # # dominant specialization genres per stage
        genre_cover = Counter()      # genre -> # stages featuring it (any member)
        genre_dom = Counter()        # genre -> # stages where it's in >= half the members
        theme_cover = Counter()
        total_concepts = 0

        for cs in stage_concepts.values():
            n = len(cs)
            total_concepts += n
            gfreq, tfreq = Counter(), Counter()
            for cid in cs:
                for g in genre_by_concept.get(cid, ()):
                    gfreq[g] += 1
                for t in theme_by_concept.get(cid, ()):
                    tfreq[t] += 1
            for g in gfreq:
                genre_cover[g] += 1
            for t in tfreq:
                theme_cover[t] += 1
            thresh = (n + 1) // 2  # present in at least half the stage's concepts
            dom = {g for g, c in gfreq.items() if c >= thresh}
            for g in dom:
                genre_dom[g] += 1
            spec_hist[len(set(gfreq) - {base_genre})] += 1
            dom_spec_hist[len(dom - {base_genre})] += 1

        w(head(f'Badge-stage JOB analysis  (stage = XP unit; base genre excluded: {base_genre})'))
        w(f'  Stages analyzed:       {n_stages:>6,}')
        w(f'  Avg concepts / stage:  {total_concepts / n_stages:>6.1f}')

        def _hist(title, hist):
            w('')
            w(head(title))
            for k in sorted(hist):
                c = hist[k]
                w(f'  {c:>5,}  {c / n_stages * 100:5.1f}%  {k} genre{"" if k == 1 else "s"}')

        _hist(f'Specialization genres per stage (distinct, excluding {base_genre})', spec_hist)
        _hist(f'DOMINANT specialization genres per stage (in >= half the concepts, excl. {base_genre})', dom_spec_hist)

        def _cover(title, counter):
            w('')
            w(head(title))
            for name, c in counter.most_common():
                w(f'  {c:>5,}  {c / n_stages * 100:5.1f}%  {name}')

        _cover('Stage genre coverage (# of stages featuring the genre)', genre_cover)
        _cover('Stage genre DOMINANCE (genre in >= half the stage concepts)', genre_dom)
        _cover('Stage theme coverage (# of stages featuring the theme)', theme_cover)

    # --- table helpers ---
    def _marginal(self, title, counter, total):
        w = self.stdout.write
        w('')
        w(self.style.MIGRATE_HEADING(title))
        if not counter:
            w('  (none)')
            return
        for name, c in counter.most_common():
            w(f'  {c:>6,}  {c / total * 100:5.1f}%  {name}')

    def _combo(self, title, counter, total, top):
        w = self.stdout.write
        w('')
        shown = counter.most_common(top) if top else counter.most_common()
        suffix = f' (top {top} of {len(counter):,})' if top and len(counter) > top else ''
        w(self.style.MIGRATE_HEADING(f'{title}{suffix}'))
        for combo, c in shown:
            label = ' + '.join(combo) if combo else '(none)'
            w(f'  {c:>6,}  {c / total * 100:5.1f}%  {label}')

    def _pairs(self, title, counter, total, top):
        w = self.stdout.write
        w('')
        shown = counter.most_common(top) if top else counter.most_common()
        suffix = f' (top {top} of {len(counter):,})' if top and len(counter) > top else ''
        w(self.style.MIGRATE_HEADING(f'{title}{suffix}'))
        if not shown:
            w('  (none)')
            return
        for (g, t), c in shown:
            w(f'  {c:>6,}  {c / total * 100:5.1f}%  {g}  x  {t}')
