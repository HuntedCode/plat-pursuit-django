"""Calibrate the media-density contract rule (2026-08-31, Jeffrey's design) BEFORE wiring it in.

The rule under test, over anchored + trusted-matched concepts whose IGDB id holds no contract:
  Tier A (auto-contract): has a trailer/video AND a sane trophy pyramid -- the guard that keeps
          the trailer-making easy-plat publishers (the eastasiasoft/Ratalaika class) out.
  Tier B (review): video but a DEGENERATE pyramid, or no video but >= --min-shots screenshots.
  Tier C (snooze): under --min-shots screenshots and no video.

Two calibration read-outs ride along:
  - PRECISION side: how much flagged shovelware each tier admits, with and without the guard.
  - RECALL side: existing contracts are staff-curated ground truth -- what share of them the
    rule would have surfaced (reported per tier over the CONTRACTED population).

Read-only; run against beta/prod data. Tune --min-shots and the pyramid knobs, then Phase 2
(the ContractCandidate pipeline) hardcodes the winners.
"""
from collections import Counter

from django.core.management.base import BaseCommand

from trophies.models import Contract, Game, IGDBMatch


# Degenerate-pyramid signature v1 (calibrate here, then promote): the classic easy-plat stack
# is gold-heavy and tiny (1 plat + ~11 gold + little else). A designed game is bronze-heavy
# with a real earnable count.
def pyramid_is_degenerate(defined, min_earnable=15):
    """`defined` is a Game.defined_trophies dict. True when the list reads easy-plat product:
    gold outnumbers bronze, or the whole earnable list (excl. platinum) is tiny."""
    bronze = defined.get('bronze') or 0
    silver = defined.get('silver') or 0
    gold = defined.get('gold') or 0
    earnable = bronze + silver + gold
    if earnable < min_earnable:
        return True
    return gold > bronze


class Command(BaseCommand):
    help = "Dry-run the media-density contract rule over the trusted catalogue (read-only calibration)."

    def add_arguments(self, parser):
        parser.add_argument('--min-shots', type=int, default=4,
                            help='Screenshot threshold for Tier B without a video (default 4).')
        parser.add_argument('--min-earnable', type=int, default=15,
                            help='Pyramid guard: an earnable list smaller than this is degenerate (default 15).')
        parser.add_argument('--sample', type=int, default=8,
                            help='Sample titles printed per bucket (default 8).')

    def handle(self, *args, **opts):
        min_shots = opts['min_shots']
        min_earnable = opts['min_earnable']
        sample_n = opts['sample']
        w = self.stdout.write
        head = self.style.MIGRATE_HEADING

        contracted_igdb = set(Contract.objects.filter(
            igdb_id__isnull=False).values_list('igdb_id', flat=True))

        # One pass over trusted, anchored matches: media counts off the match, the pyramid off
        # the concept's biggest trophy list, shovelware off any flagged game row.
        matches = (
            IGDBMatch.objects.filter(
                status__in=IGDBMatch.TRUSTED_STATUSES,
                igdb_id__isnull=False,
                concept__anchor_migration_completed_at__isnull=False,
            )
            .values('concept_id', 'igdb_id', 'igdb_name',
                    'igdb_video_youtube_ids', 'igdb_screenshot_image_ids')
        )

        games = Game.objects.filter(
            concept__anchor_migration_completed_at__isnull=False,
        ).values('concept_id', 'defined_trophies', 'shovelware_status')
        biggest = {}       # concept_id -> defined_trophies of the largest list
        shovelware = set()  # concept_ids with any flagged game
        for g in games:
            defined = g['defined_trophies'] or {}
            size = sum(v or 0 for v in defined.values())
            if size >= sum(v or 0 for v in (biggest.get(g['concept_id']) or {}).values()):
                biggest[g['concept_id']] = defined
            if g['shovelware_status'] in ('auto_flagged', 'manually_flagged'):
                shovelware.add(g['concept_id'])

        def bucket(m):
            has_video = bool(m['igdb_video_youtube_ids'])
            shots = len(m['igdb_screenshot_image_ids'] or [])
            degenerate = pyramid_is_degenerate(biggest.get(m['concept_id']) or {}, min_earnable)
            if has_video and not degenerate:
                return 'A'
            if has_video or shots >= min_shots:
                return 'B'
            return 'C'

        # Per-tier tallies, split into the scoring population (no contract yet) and the
        # ground-truth population (already contracted -- the recall read-out).
        pop = Counter()
        pop_shovel = Counter()
        pop_video_no_guard = Counter()   # where the rule WITHOUT the pyramid guard would land video games
        recall = Counter()
        samples = {t: [] for t in 'ABC'}
        seen_igdb_pop = set()

        n_total = 0
        for m in matches.iterator(chunk_size=2000):
            n_total += 1
            t = bucket(m)
            if m['igdb_id'] in contracted_igdb:
                recall[t] += 1
                continue
            # One vote per IGDB id in the scoring population (sibling concepts share the page).
            if m['igdb_id'] in seen_igdb_pop:
                continue
            seen_igdb_pop.add(m['igdb_id'])
            pop[t] += 1
            if m['concept_id'] in shovelware:
                pop_shovel[t] += 1
            if m['igdb_video_youtube_ids']:
                pop_video_no_guard['A'] += 1
                if m['concept_id'] in shovelware:
                    pop_video_no_guard['A_shovel'] += 1
            if len(samples[t]) < sample_n:
                samples[t].append(m['igdb_name'] or f"igdb:{m['igdb_id']}")

        if not n_total:
            self.stdout.write(self.style.WARNING('No trusted, anchored matches found.'))
            return

        labels = {
            'A': f'Tier A  auto-contract (video + sane pyramid)',
            'B': f'Tier B  review (video+degenerate OR >= {min_shots} shots)',
            'C': f'Tier C  snooze (< {min_shots} shots, no video)',
        }
        w(head(f'Media-density contract rule -- calibration '
               f'(--min-shots {min_shots}, --min-earnable {min_earnable})'))
        w(f'Population: {sum(pop.values())} uncontracted IGDB games '
          f'({n_total} trusted anchored matches scanned)\n')
        for t in 'ABC':
            n = pop[t]
            sh = pop_shovel[t]
            pct = (100 * n / sum(pop.values())) if pop else 0
            shpct = (100 * sh / n) if n else 0
            w(head(labels[t]))
            w(f'  {n} games ({pct:.1f}%)  |  flagged shovelware inside: {sh} ({shpct:.1f}%)')
            for name in samples[t]:
                w(f'    - {name}')
            w('')

        guarded_a_shovel = pop_shovel['A']
        raw_a = pop_video_no_guard['A']
        raw_a_shovel = pop_video_no_guard['A_shovel']
        w(head('The pyramid guard (video games only)'))
        w(f'  Without guard: video => contract would auto-admit {raw_a} games, '
          f'{raw_a_shovel} flagged shovelware')
        w(f'  With guard:    Tier A admits {pop["A"]} games, {guarded_a_shovel} flagged shovelware '
          f'(the rest fell to review)\n')

        n_contracted = sum(recall.values())
        w(head('Recall vs existing contracts (staff-curated ground truth)'))
        if n_contracted:
            for t in 'ABC':
                w(f'  {labels[t][:6]}: {recall[t]} ({100 * recall[t] / n_contracted:.1f}%)')
            caught = recall['A'] + recall['B']
            w(f'  Rule would have surfaced {caught}/{n_contracted} '
              f'({100 * caught / n_contracted:.1f}%) of contracted games (A auto, B via review); '
              f"the Tier-C remainder is the rule's blind spot -- eyeball those.")
        else:
            w('  (no contracted games in the population)')
