"""The media-density contract rule's read-only calibration report. The rule itself lives in
trophies/services/contract_candidates.py (shared with the nightly evaluate_contract_candidates
pipeline); this command dry-runs it and prints the calibration read-outs:

- per-tier counts + samples (highest played_count first -- the demand ranking the queues use)
- the precision ladder: no guard -> pyramid guard -> shovelware override
- the franchise rescue's haul (and its shovelware honesty check)
- recall vs existing contracts (run in the environment that HOLDS the contract catalog)

Calibrated on prod 2026-08-31: Tier A 0% flagged shovelware; recall vs 942 staff-curated
contracts = 100% surfaced (78% A / 22% B / 0% C). Tune --min-shots / --min-earnable here;
the pipeline shares the same defaults.
"""
from collections import Counter

from django.core.management.base import BaseCommand

from trophies.services.contract_candidates import (   # noqa: F401  (pyramid re-export for tests)
    DEFAULT_MIN_EARNABLE, DEFAULT_MIN_SHOTS, CatalogScanner, pyramid_is_degenerate,
)


class Command(BaseCommand):
    help = "Dry-run the media-density contract rule over the trusted catalogue (read-only calibration)."

    def add_arguments(self, parser):
        parser.add_argument('--min-shots', type=int, default=DEFAULT_MIN_SHOTS,
                            help='Screenshot threshold for Tier B without a video (default 4).')
        parser.add_argument('--min-earnable', type=int, default=DEFAULT_MIN_EARNABLE,
                            help='Pyramid guard: an earnable list smaller than this is degenerate (default 15).')
        parser.add_argument('--min-players', type=int, default=0,
                            help='Demand floor: skip games with fewer tracked players than this (default 0 = off).')
        parser.add_argument('--sample', type=int, default=8,
                            help='Sample titles printed per bucket, highest played first (default 8).')

    def handle(self, *args, **opts):
        min_shots = opts['min_shots']
        min_players = opts['min_players']
        sample_n = opts['sample']
        w = self.stdout.write
        head = self.style.MIGRATE_HEADING

        scanner = CatalogScanner(min_shots=min_shots, min_earnable=opts['min_earnable'])

        pop = Counter()
        pop_shovel = Counter()
        reasons = Counter()
        reason_shovel = Counter()
        raw_video = 0                # video games in population (no-guard baseline)
        raw_video_shovel = 0
        recall = Counter()
        below_floor = 0
        samples = {t: [] for t in 'ABC'}     # (played, name) per tier
        reason_samples = {'blocked': [], 'rescued': []}
        seen_igdb_pop = set()

        n_total = 0
        for row in scanner.iter_matches():
            n_total += 1
            if row['contracted']:
                recall[row['tier']] += 1
                continue
            if row['igdb_id'] in seen_igdb_pop:
                continue   # sibling concepts share the IGDB page: one vote per game
            seen_igdb_pop.add(row['igdb_id'])
            if row['players'] < min_players:
                below_floor += 1
                continue
            t = row['tier']
            pop[t] += 1
            if row['is_shovelware']:
                pop_shovel[t] += 1
            if row['has_video']:
                raw_video += 1
                if row['is_shovelware']:
                    raw_video_shovel += 1
            if row['reason']:
                reasons[row['reason']] += 1
                if row['is_shovelware']:
                    reason_shovel[row['reason']] += 1
                reason_samples[row['reason']].append((row['players'], row['name']))
            samples[t].append((row['players'], row['name']))

        if not n_total:
            self.stdout.write(self.style.WARNING('No trusted, anchored matches found.'))
            return

        def top(pairs):
            return sorted(pairs, key=lambda x: -x[0])[:sample_n]

        labels = {
            'A': 'Tier A  auto-contract (video + sane pyramid + not shovelware)',
            'B': f'Tier B  review (video+degenerate OR >= {min_shots} shots OR blocked OR rescued)',
            'C': f'Tier C  snooze (< {min_shots} shots, no video, no franchise)',
        }
        w(head(f'Media-density contract rule v2 -- calibration '
               f'(--min-shots {min_shots}, --min-earnable {opts["min_earnable"]}, '
               f'--min-players {min_players})'))
        floor_note = f', {below_floor} below the demand floor' if min_players else ''
        w(f'Population: {sum(pop.values())} uncontracted IGDB games '
          f'({n_total} trusted anchored matches scanned{floor_note})\n')
        for t in 'ABC':
            n = pop[t]
            sh = pop_shovel[t]
            pct = (100 * n / sum(pop.values())) if pop else 0
            shpct = (100 * sh / n) if n else 0
            w(head(labels[t]))
            w(f'  {n} games ({pct:.1f}%)  |  flagged shovelware inside: {sh} ({shpct:.1f}%)')
            for p, name in top(samples[t]):
                w(f'    - {name}  ({p} players)')
            w('')

        w(head('The precision ladder (video games in the population)'))
        w(f'  No guard:            video => contract would auto-admit {raw_video} games, '
          f'{raw_video_shovel} flagged shovelware')
        w(f'  + pyramid guard:     {pop["A"] + reasons["blocked"]} would remain, '
          f'{reason_shovel["blocked"]} flagged shovelware')
        w(f'  + shovelware block:  Tier A admits {pop["A"]} games, {pop_shovel["A"]} flagged '
          f'shovelware (the override sent {reasons["blocked"]} to review)')
        if reason_samples['blocked']:
            w('  Blocked from auto (top by players):')
            for p, name in top(reason_samples['blocked']):
                w(f'    - {name}  ({p} players)')
        w('')

        w(head('The franchise rescue (would-be snoozes with real IP)'))
        w(f'  Promoted to review: {reasons["rescued"]} games '
          f'({reason_shovel["rescued"]} of them flagged shovelware -- the honesty check)')
        for p, name in top(reason_samples['rescued']):
            w(f'    - {name}  ({p} players)')
        w('')

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
            w('  (no contracted games in the population -- run this in the environment that '
              'holds the contract catalog for the recall read-out)')
