"""Audit Job Board coverage: badge games not yet on the Job Board.

A "badge game" is any Concept in a badge Stage (direct Stage.concepts, or via a
ConceptBundle on a stage). It is "covered" if it's an ANCHORED concept whose raw IGDB id
keys a Contract, OR it satisfies one via a ContractBundle. This lists the badge games
still MISSING a Contract, grouped by badge series, so staff know what to curate onto the
Job Board. Read-only; catalog-bounded (not per-user), so it loads id sets in memory.
"""
from collections import defaultdict

from django.core.management.base import BaseCommand

from trophies.models import Badge, Concept, Contract, Stage


class Command(BaseCommand):
    help = "List badge games not yet on the Job Board (missing a Contract), grouped by series."

    def add_arguments(self, parser):
        parser.add_argument('--live-only', action='store_true', dest='live_only',
                            help='Only consider live badges.')
        parser.add_argument('--summary', action='store_true',
                            help='Print counts only; skip the per-game lists.')

    def handle(self, *args, **options):
        live_only = options['live_only']
        summary = options['summary']

        # Badge series (tier-1 row carries the series identity) + live status.
        badges = Badge.objects.filter(tier=1)
        if live_only:
            badges = badges.filter(is_live=True)
        series_info = {
            b.series_slug: {'name': b.name, 'is_live': b.is_live}
            for b in badges if b.series_slug
        }
        slugs = set(series_info)
        if not slugs:
            self.stdout.write(self.style.WARNING('No badge series found.'))
            return

        # Badge games per series: direct stage members + bundle members.
        stage_concepts = defaultdict(set)
        for slug, cid in (Stage.objects.filter(series_slug__in=slugs)
                          .values_list('series_slug', 'concepts__id')):
            if cid:
                stage_concepts[slug].add(cid)
        for slug, cid in (Stage.objects.filter(series_slug__in=slugs)
                          .values_list('series_slug', 'concept_bundles__concepts__id')):
            if cid:
                stage_concepts[slug].add(cid)

        # Covered = an ANCHORED concept whose raw igdb_id keys a Contract, OR is in a bundle.
        contract_igdb_ids = set(Contract.objects.exclude(igdb_id=None).values_list('igdb_id', flat=True))
        covered = set(
            Concept.objects.filter(
                anchor_migration_completed_at__isnull=False,
                igdb_match__igdb_id__in=contract_igdb_ids,
            ).values_list('id', flat=True)
        ) if contract_igdb_ids else set()
        covered |= set(
            Concept.objects.filter(contract_bundles__isnull=False).values_list('id', flat=True)
        )

        per_series = {}            # slug -> set of uncovered concept ids
        all_uncovered = set()
        for slug, cids in stage_concepts.items():
            uncovered = cids - covered
            if uncovered:
                per_series[slug] = uncovered
                all_uncovered |= uncovered

        all_games = set().union(*stage_concepts.values()) if stage_concepts else set()

        w = self.stdout.write
        head = self.style.MIGRATE_HEADING
        scope = ' (LIVE badges only)' if live_only else ''
        w(head(f'Job Board coverage: badge games missing a Contract{scope}'))
        w(f'  Badge series:           {len(slugs):>6,}')
        w(f'  Badge games:            {len(all_games):>6,}')
        w(f'  Covered (on Job Board): {len(all_games) - len(all_uncovered):>6,}')
        w(f'  Uncovered:              {len(all_uncovered):>6,}')

        if summary or not per_series:
            return

        titles = dict(Concept.objects.filter(id__in=all_uncovered).values_list('id', 'unified_title'))
        cids = dict(Concept.objects.filter(id__in=all_uncovered).values_list('id', 'concept_id'))

        w('')
        w(head('Uncovered games by badge series'))
        for slug in sorted(per_series, key=lambda s: (series_info[s]['name'] or s).lower()):
            info = series_info[slug]
            live = ' [LIVE]' if info['is_live'] else ''
            ids = per_series[slug]
            w(f"  {info['name'] or slug}{live}  ({len(ids)})")
            for cid in sorted(ids, key=lambda i: (titles.get(i) or '').lower()):
                w(f"    - {titles.get(cid) or '(untitled)'}  ({cids.get(cid)})")
