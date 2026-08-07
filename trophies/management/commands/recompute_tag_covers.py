"""Materialize the grouping browse read-models: `representative_game` (tile cover) for Genres, Themes, and
Franchises, plus `related_tags` (detail-page rail) for Genres and Themes.

Both are slow-changing derived data that would otherwise cost a per-request query, so we compute them off the
request path here and the pages read them O(1).

representative_game (the tile cover), per grouping:
  1. A CONTRACT game (curated Job-Board entry) with real cover art -- the most-recent `POOL_CAP`, then a
     STABLE per-grouping variety shuffle (a hash of id+game, so adjacent tiles differ but a tile never
     reshuffles between page loads). Bounded to POOL_CAP so the shuffle never scans a whole large grouping.
  2. Fallback: the most-recent member game with real art.
  3. Last resort: any most-recent member game (so a games-having grouping never renders the empty placeholder).
  Franchises pass a cover filter so excluded / spin-off links don't provide the cover (matches the live pick).

related_tags (Genres/Themes only -- the detail-page rail): the top-`RELATED_N` OTHER same-type tags ranked by
game co-occurrence, stored as an ordered slug list. The co-occurrence GROUP BY is the reason this is
materialized: it scans a tag's concepts x their memberships, fine nightly but heavy per page load.

Idempotent -- recomputes from scratch. Run on a daily cadence (after `recalc_earn_rates`) + once at cutover.
(Command name kept for continuity though it now covers franchises too.)
"""

import hashlib

from django.core.management.base import BaseCommand
from django.db.models import Count, Exists, OuterRef, Q

from trophies.models import (
    Genre, Theme, Franchise, Game, Contract, ConceptGenre, ConceptTheme, ConceptFranchise,
)
from trophies.services.game_grouping_service import _MOST_RECENT_RELEASE_ORDER


# A game "has real art" when it carries a genuine cover source (not merely the generic PS title icon).
_HAS_ART = (
    Q(concept__igdb_match__igdb_cover_image_id__gt='')
    | Q(concept__concept_icon_url__gt='')
    | Q(title_image__gt='')
)
# Franchise covers ignore excluded / spin-off links (matches the live cover annotation's visible-link filter).
_FRANCHISE_COVER_FILTER = Q(
    concept__concept_franchises__is_excluded=False,
    concept__concept_franchises__is_spinoff=False,
)


class Command(BaseCommand):
    help = (
        "Materialize Genre/Theme/Franchise.representative_game (the tile cover) + Genre/Theme.related_tags "
        "(the detail rail's co-occurring tags). Read O(1) at render."
    )

    POOL_CAP = 50   # bound the per-grouping contract pool so the offline shuffle never scans a whole big one
    RELATED_N = 6   # how many related tags to store per genre/theme

    # Per-grouping config. `cover_filter` narrows the games eligible to provide the cover; `related` marks the
    # models that carry related_tags (genres/themes); `iterate` bounds which rows we bother recomputing.
    CONFIGS = [
        {'model': Genre, 'through': ConceptGenre, 'field': 'genre',
         'path': 'concept__concept_genres__genre', 'cover_filter': None, 'related': True, 'iterate': None},
        {'model': Theme, 'through': ConceptTheme, 'field': 'theme',
         'path': 'concept__concept_themes__theme', 'cover_filter': None, 'related': True, 'iterate': None},
        {'model': Franchise, 'through': ConceptFranchise, 'field': 'franchise',
         'path': 'concept__concept_franchises__franchise', 'cover_filter': _FRANCHISE_COVER_FILTER,
         'related': False, 'iterate': Q(franchise_concepts__is_excluded=False)},
    ]

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true', help='Report changes without writing.')

    def handle(self, *args, **opts):
        dry = opts['dry_run']
        # Correlated EXISTS (indexed on Contract.igdb_id) rather than an id IN-list, so this stays fast even
        # if the contract catalogue grows to tens of thousands.
        contract_exists = Exists(
            Contract.objects.filter(igdb_id=OuterRef('concept__igdb_match__igdb_id'), is_live=True)
        )
        total = 0
        for cfg in self.CONFIGS:
            changed = self._recompute(cfg, contract_exists, dry)
            total += changed
            verb = 'would change' if dry else 'updated'
            self.stdout.write(f"{cfg['model'].__name__}: {changed} row(s) {verb}.")
        self.stdout.write(self.style.SUCCESS(
            f"Done. {total} grouping row(s) {'to change' if dry else 'updated'}."
        ))

    def _recompute(self, cfg, contract_exists, dry):
        Model = cfg['model']
        qs = Model.objects.all()
        if cfg['iterate'] is not None:
            qs = qs.filter(cfg['iterate']).distinct()
        only_fields = ['id', 'representative_game'] + (['related_tags'] if cfg['related'] else [])

        updates = []
        changed_fields = set()
        for row in qs.only(*only_fields):
            gid = self._pick(row.id, cfg['path'], contract_exists, cfg['cover_filter'])
            changed = False
            if row.representative_game_id != gid:
                row.representative_game_id = gid
                changed_fields.add('representative_game')
                changed = True
            if cfg['related']:
                related = self._related(cfg['through'], cfg['field'], cfg['path'], row.id)
                if row.related_tags != related:
                    row.related_tags = related
                    changed_fields.add('related_tags')
                    changed = True
            if changed:
                updates.append(row)
        if updates and not dry:
            Model.objects.bulk_update(updates, list(changed_fields) or ['representative_game'], batch_size=200)
        return len(updates)

    def _pick(self, row_id, path, contract_exists, cover_filter=None):
        base = Game.objects.filter(**{path: row_id})
        if cover_filter is not None:
            base = base.filter(cover_filter)
        # 1. Contract games with real art: the most-recent POOL_CAP, then a stable per-grouping variety pick.
        pool = list(
            base.filter(_HAS_ART).filter(contract_exists)
            .order_by(*_MOST_RECENT_RELEASE_ORDER)
            .values_list('id', flat=True)[:self.POOL_CAP]
        )
        if pool:
            return min(pool, key=lambda gid: hashlib.md5(f"{row_id}:{gid}".encode()).hexdigest())
        # 2. Most-recent member game with real art.
        gid = (
            base.filter(_HAS_ART).order_by(*_MOST_RECENT_RELEASE_ORDER)
            .values_list('id', flat=True).first()
        )
        if gid:
            return gid
        # 3. Any most-recent member game (so a games-having grouping never falls to the empty placeholder).
        return base.order_by(*_MOST_RECENT_RELEASE_ORDER).values_list('id', flat=True).first()

    def _related(self, Through, tag_field, path, tag_id):
        """Top-RELATED_N OTHER same-type tags by shared-concept co-occurrence (an ordered slug list)."""
        rows = (
            Through.objects.filter(**{path: tag_id})
            .exclude(**{f'{tag_field}_id': tag_id})
            .values(f'{tag_field}__slug')
            .annotate(c=Count('concept', distinct=True))
            .order_by('-c', f'{tag_field}__slug')[:self.RELATED_N]
        )
        return [r[f'{tag_field}__slug'] for r in rows]
