"""The media-density contract rule (Jeffrey's design, calibrated on prod 2026-08-31): the ONE
scanner both consumers share -- `report_contract_candidates` (the read-only calibration report)
and `evaluate_contract_candidates` (the nightly pipeline that stages/queues candidates).

The rule, over anchored + trusted-matched concepts:
  Tier A (auto-contract): trailer/video AND a sane trophy pyramid AND not flagged shovelware.
  Tier B (review): video + degenerate pyramid; no video but >= min_shots screenshots; a
          shovelware-BLOCKED would-be A (flagged games never auto-accept); or a franchise
          RESCUE (a would-be C whose concept holds a non-excluded franchise/collection link --
          the AAA-back-catalog fix: old, static IGDB pages never gain media).
  Tier C (snooze): under min_shots screenshots, no video, no franchise membership.

Calibration (17.9k matches, prod): Tier A 0% flagged shovelware with the override; recall vs
942 staff-curated contracts = 100% surfaced (78% A / 22% B / 0% C).
"""
from trophies.models import ConceptFranchise, Contract, Game, IGDBMatch

DEFAULT_MIN_SHOTS = 4
DEFAULT_MIN_EARNABLE = 15

TIER_AUTO = 'A'
TIER_REVIEW = 'B'
TIER_SNOOZE = 'C'
REASON_BLOCKED = 'blocked'   # would-be A, shovelware-flagged: never auto-accepts
REASON_RESCUED = 'rescued'   # would-be C, franchise member: real IP with a thin page


def pyramid_is_degenerate(defined, min_earnable=DEFAULT_MIN_EARNABLE):
    """`defined` is a Game.defined_trophies dict. True when the list reads easy-plat product:
    gold outnumbers bronze, or the whole earnable list (excl. platinum) is tiny."""
    bronze = defined.get('bronze') or 0
    silver = defined.get('silver') or 0
    gold = defined.get('gold') or 0
    earnable = bronze + silver + gold
    if earnable < min_earnable:
        return True
    return gold > bronze


class CatalogScanner:
    """One pass of shared lookups over the anchored catalogue, then per-match bucketing.

    Builds: the concept's biggest trophy list (the pyramid), the shovelware set, the max
    played_count per concept (the demand signal), the franchise-membership set, and the
    contracted IGDB ids. `iter_matches()` yields one dict per trusted anchored match --
    consumers dedup siblings (one vote per IGDB game) themselves.
    """

    def __init__(self, min_shots=DEFAULT_MIN_SHOTS, min_earnable=DEFAULT_MIN_EARNABLE):
        self.min_shots = min_shots
        self.min_earnable = min_earnable

        self.contracted_igdb = set(Contract.objects.filter(
            igdb_id__isnull=False).values_list('igdb_id', flat=True))
        # Spin-off links still count -- membership proves real IP context either way.
        self.franchise_concepts = set(ConceptFranchise.objects.filter(
            is_excluded=False).values_list('concept_id', flat=True))

        self.biggest = {}        # concept_id -> defined_trophies of the largest list
        self.shovelware = set()  # concept_ids with any flagged game
        self.played = {}         # concept_id -> max played_count across its lists
        games = Game.objects.filter(
            concept__anchor_migration_completed_at__isnull=False,
        ).values('concept_id', 'defined_trophies', 'shovelware_status', 'played_count')
        for g in games:
            defined = g['defined_trophies'] or {}
            size = sum(v or 0 for v in defined.values())
            if size >= sum(v or 0 for v in (self.biggest.get(g['concept_id']) or {}).values()):
                self.biggest[g['concept_id']] = defined
            if g['shovelware_status'] in ('auto_flagged', 'manually_flagged'):
                self.shovelware.add(g['concept_id'])
            self.played[g['concept_id']] = max(
                self.played.get(g['concept_id'], 0), g['played_count'] or 0)

    def bucket(self, m):
        """(tier, reason) for one match row. reason: REASON_BLOCKED, REASON_RESCUED, or ''."""
        cid = m['concept_id']
        has_video = bool(m['igdb_video_youtube_ids'])
        shots = len(m['igdb_screenshot_image_ids'] or [])
        degenerate = pyramid_is_degenerate(self.biggest.get(cid) or {}, self.min_earnable)
        if has_video and not degenerate:
            if cid in self.shovelware:
                return TIER_REVIEW, REASON_BLOCKED
            return TIER_AUTO, ''
        if has_video or shots >= self.min_shots:
            return TIER_REVIEW, ''
        if cid in self.franchise_concepts:
            return TIER_REVIEW, REASON_RESCUED
        return TIER_SNOOZE, ''

    def iter_matches(self):
        """Yield one dict per trusted anchored match: the bucket plus everything both consumers
        read (name, demand, shovelware, contracted). Siblings are NOT deduped here."""
        matches = (
            IGDBMatch.objects.filter(
                status__in=IGDBMatch.TRUSTED_STATUSES,
                igdb_id__isnull=False,
                concept__anchor_migration_completed_at__isnull=False,
            )
            .values('concept_id', 'igdb_id', 'igdb_name',
                    'igdb_video_youtube_ids', 'igdb_screenshot_image_ids')
        )
        for m in matches.iterator(chunk_size=2000):
            tier, reason = self.bucket(m)
            yield {
                'igdb_id': m['igdb_id'],
                'concept_id': m['concept_id'],
                'name': m['igdb_name'] or f"igdb:{m['igdb_id']}",
                'tier': tier,
                'reason': reason,
                'players': self.played.get(m['concept_id'], 0),
                'is_shovelware': m['concept_id'] in self.shovelware,
                'has_video': bool(m['igdb_video_youtube_ids']),
                'contracted': m['igdb_id'] in self.contracted_igdb,
            }
