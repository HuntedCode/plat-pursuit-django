"""Materialize each Genre/Theme's browse read-models: `representative_game` (tile cover) + `related_tags`.

Both are slow-changing derived data that would otherwise cost a per-request query, so we compute them off the
request path here and the pages read them O(1).

representative_game (the tile cover), per tag:
  1. A CONTRACT game (curated Job-Board entry) with real cover art -- the most-recent `POOL_CAP`, then a
     STABLE per-tag variety shuffle (a hash of tag+game, so adjacent tiles differ but a tile never reshuffles
     between page loads). Bounded to POOL_CAP so the shuffle never scans a whole large genre.
  2. Fallback: the most-recent member game with real art.
  3. Last resort: any most-recent member game (so a games-having tag never renders the empty placeholder).

related_tags (the detail-page rail): the top-`RELATED_N` OTHER same-type tags ranked by game co-occurrence --
the genres/themes whose games overlap this one's the most -- stored as an ordered slug list. The co-occurrence
GROUP BY is the reason this is materialized: it scans a tag's concepts x their memberships, fine nightly but
heavy per page load for a big genre.

Idempotent -- recomputes from scratch. Run on a daily cadence (after `recalc_earn_rates`) + once at cutover.
"""

import hashlib

from django.core.management.base import BaseCommand
from django.db.models import Count, Exists, OuterRef, Q

from trophies.models import Genre, Theme, Game, Contract, ConceptGenre, ConceptTheme
from trophies.services.game_grouping_service import _MOST_RECENT_RELEASE_ORDER


# A game "has real art" when it carries a genuine cover source (not merely the generic PS title icon).
_HAS_ART = (
    Q(concept__igdb_match__igdb_cover_image_id__gt='')
    | Q(concept__concept_icon_url__gt='')
    | Q(title_image__gt='')
)


class Command(BaseCommand):
    help = (
        "Materialize Genre/Theme.representative_game (the tile cover: a contract game with a stable per-tag "
        "variety shuffle) + .related_tags (top co-occurring same-type tags for the detail rail). O(1) at render."
    )

    POOL_CAP = 50   # bound the per-tag contract pool so the offline shuffle never scans a whole big genre
    RELATED_N = 6   # how many related tags to store per tag

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
        for Model, Through, tag_field, path in (
            (Genre, ConceptGenre, 'genre', 'concept__concept_genres__genre'),
            (Theme, ConceptTheme, 'theme', 'concept__concept_themes__theme'),
        ):
            changed = self._recompute(Model, Through, tag_field, path, contract_exists, dry)
            total += changed
            verb = 'would change' if dry else 'updated'
            self.stdout.write(f"{Model.__name__}: {changed} tag(s) {verb}.")
        self.stdout.write(self.style.SUCCESS(
            f"Done. {total} tag(s) {'to change' if dry else 'updated'}."
        ))

    def _recompute(self, Model, Through, tag_field, path, contract_exists, dry):
        updates = []
        for tag in Model.objects.all().only('id', 'representative_game', 'related_tags'):
            gid = self._pick(tag.id, path, contract_exists)
            related = self._related(Through, tag_field, path, tag.id)
            changed = False
            if tag.representative_game_id != gid:
                tag.representative_game_id = gid
                changed = True
            if tag.related_tags != related:
                tag.related_tags = related
                changed = True
            if changed:
                updates.append(tag)
        if updates and not dry:
            Model.objects.bulk_update(updates, ['representative_game', 'related_tags'], batch_size=200)
        return len(updates)

    def _pick(self, tag_id, path, contract_exists):
        base = Game.objects.filter(**{path: tag_id})
        # 1. Contract games with real art: the most-recent POOL_CAP, then a stable per-tag variety pick.
        pool = list(
            base.filter(_HAS_ART).filter(contract_exists)
            .order_by(*_MOST_RECENT_RELEASE_ORDER)
            .values_list('id', flat=True)[:self.POOL_CAP]
        )
        if pool:
            return min(pool, key=lambda gid: hashlib.md5(f"{tag_id}:{gid}".encode()).hexdigest())
        # 2. Most-recent member game with real art.
        gid = (
            base.filter(_HAS_ART).order_by(*_MOST_RECENT_RELEASE_ORDER)
            .values_list('id', flat=True).first()
        )
        if gid:
            return gid
        # 3. Any most-recent member game (so a games-having tag never falls to the empty placeholder).
        return base.order_by(*_MOST_RECENT_RELEASE_ORDER).values_list('id', flat=True).first()

    def _related(self, Through, tag_field, path, tag_id):
        """Top-RELATED_N OTHER same-type tags by shared-concept co-occurrence (an ordered slug list).

        `path` (e.g. 'concept__concept_genres__genre') applied to the through model selects the through-rows
        whose concept also belongs to this tag; grouping the OTHER tags on those rows + counting distinct
        concepts gives the overlap. Raw count (biased toward big tags, but intuitive) -- ties broken by slug.
        """
        rows = (
            Through.objects.filter(**{path: tag_id})
            .exclude(**{f'{tag_field}_id': tag_id})
            .values(f'{tag_field}__slug')
            .annotate(c=Count('concept', distinct=True))
            .order_by('-c', f'{tag_field}__slug')[:self.RELATED_N]
        )
        return [r[f'{tag_field}__slug'] for r in rows]
