"""Calibrate the media-density contract rule (2026-08-31, Jeffrey's design) BEFORE wiring it in.

The rule under test (v2, after the first prod calibration run), over anchored + trusted-matched
concepts whose IGDB id holds no contract:
  Tier A (auto-contract): trailer/video AND a sane trophy pyramid AND not flagged shovelware.
          The pyramid guard keeps the trailer-making easy-plat publishers (the
          eastasiasoft/Ratalaika class) out; the SHOVELWARE OVERRIDE (v2, Jeffrey's rule) means
          a flagged game can NEVER auto-accept -- a would-be Tier A shovelware game is demoted
          to review and marked as blocked.
  Tier B (review): video but a degenerate pyramid, no video but >= --min-shots screenshots,
          a shovelware-blocked would-be A, or a FRANCHISE RESCUE (v2): a would-be Tier C game
          whose concept belongs to an IGDB franchise/collection -- the fix for the first run's
          finding that AAA back-catalog titles (old, static IGDB pages with thin media) landed
          in snooze beside the junk.
  Tier C (snooze): under --min-shots screenshots, no video, no franchise membership.

Queues are ranked by OUR OWN demand signal: samples print highest played_count first
(contracts exist to give players XP -- work top-down by impact), and --min-players applies an
optional demand floor to the whole population.

Calibration read-outs: per-tier counts/samples, the shovelware-admitted precision ladder
(no guard -> pyramid guard -> shovelware override), the franchise rescue's haul (and how much
flagged shovelware it pulls up -- the honesty check), and recall vs existing contracts
(staff-curated ground truth; run in the environment that HOLDS the contract catalog).

Read-only. Tune the knobs here; Phase 2 (the ContractCandidate pipeline) hardcodes the winners.
"""
from collections import Counter

from django.core.management.base import BaseCommand

from trophies.models import ConceptFranchise, Contract, Game, IGDBMatch


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
        parser.add_argument('--min-players', type=int, default=0,
                            help='Demand floor: skip games with fewer tracked players than this (default 0 = off).')
        parser.add_argument('--sample', type=int, default=8,
                            help='Sample titles printed per bucket, highest played first (default 8).')

    def handle(self, *args, **opts):
        min_shots = opts['min_shots']
        min_earnable = opts['min_earnable']
        min_players = opts['min_players']
        sample_n = opts['sample']
        w = self.stdout.write
        head = self.style.MIGRATE_HEADING

        contracted_igdb = set(Contract.objects.filter(
            igdb_id__isnull=False).values_list('igdb_id', flat=True))

        # Franchise rescue set: concepts with any non-excluded franchise/collection link.
        # Spin-off links still count -- membership proves real IP context either way.
        franchise_concepts = set(ConceptFranchise.objects.filter(
            is_excluded=False).values_list('concept_id', flat=True))

        # One pass over trusted, anchored matches: media counts off the match, the pyramid off
        # the concept's biggest trophy list, shovelware off any flagged game row, demand off
        # the concept's most-played list.
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
        ).values('concept_id', 'defined_trophies', 'shovelware_status', 'played_count')
        biggest = {}        # concept_id -> defined_trophies of the largest list
        shovelware = set()  # concept_ids with any flagged game
        played = {}         # concept_id -> max played_count across its lists
        for g in games:
            defined = g['defined_trophies'] or {}
            size = sum(v or 0 for v in defined.values())
            if size >= sum(v or 0 for v in (biggest.get(g['concept_id']) or {}).values()):
                biggest[g['concept_id']] = defined
            if g['shovelware_status'] in ('auto_flagged', 'manually_flagged'):
                shovelware.add(g['concept_id'])
            played[g['concept_id']] = max(played.get(g['concept_id'], 0), g['played_count'] or 0)

        def bucket(m):
            """Returns (tier, reason): reason is 'blocked' (shovelware override out of A),
            'rescued' (franchise promotion out of C), or None."""
            cid = m['concept_id']
            has_video = bool(m['igdb_video_youtube_ids'])
            shots = len(m['igdb_screenshot_image_ids'] or [])
            degenerate = pyramid_is_degenerate(biggest.get(cid) or {}, min_earnable)
            if has_video and not degenerate:
                if cid in shovelware:
                    return 'B', 'blocked'    # flagged shovelware NEVER auto-accepts
                return 'A', None
            if has_video or shots >= min_shots:
                return 'B', None
            if cid in franchise_concepts:
                return 'B', 'rescued'        # real IP with a thin old IGDB page
            return 'C', None

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
        for m in matches.iterator(chunk_size=2000):
            n_total += 1
            t, reason = bucket(m)
            if m['igdb_id'] in contracted_igdb:
                recall[t] += 1
                continue
            if m['igdb_id'] in seen_igdb_pop:
                continue   # sibling concepts share the IGDB page: one vote per game
            seen_igdb_pop.add(m['igdb_id'])
            p = played.get(m['concept_id'], 0)
            if p < min_players:
                below_floor += 1
                continue
            name = m['igdb_name'] or f"igdb:{m['igdb_id']}"
            pop[t] += 1
            is_shovel = m['concept_id'] in shovelware
            if is_shovel:
                pop_shovel[t] += 1
            if m['igdb_video_youtube_ids']:
                raw_video += 1
                if is_shovel:
                    raw_video_shovel += 1
            if reason:
                reasons[reason] += 1
                if is_shovel:
                    reason_shovel[reason] += 1
                reason_samples[reason].append((p, name))
            samples[t].append((p, name))

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
               f'(--min-shots {min_shots}, --min-earnable {min_earnable}, --min-players {min_players})'))
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
