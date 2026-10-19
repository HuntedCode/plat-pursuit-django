"""Re-home standalone expansions into their own GameFamily.

Until 2026-10 the family resolver followed a Standalone Expansion's `parent_game` (IGDB game_type 4), so
Dishonored: Death of the Outsider sat in Dishonored 2's family, Miles Morales in Marvel's Spider-Man's, and
`generate_badge_stages` (one stage per family) made each pair one stage. The resolver now stops at a
standalone expansion (`IGDBService._CANONICAL_PARENT_GAME_TYPES`); this one-shot pass re-links the families
enriched before that.

Scope: trusted matches that ARE a standalone expansion, plus trusted matches one step below one we hold (an
edition, remaster or port whose `version_parent` / `parent_game` is that expansion). Each goes through
`IGDBService._link_concept_to_family`, the linker enrichment runs, so a new family gets its name and origin
snapshot and a family left empty is deleted. A derivative of an expansion we do not hold, or two steps below
one, is out of scope: it re-links on its next enrichment refresh.

Also, for what the families leave behind:
  * Existing badge STAGES are never edited. A stage the move splits (its games were one family and are now
    two or more) is listed for a hand split. Only direct stage concepts are checked: bundles are hand-built.
  * OPEN (pending or deferred) join reviews store the canonical id their game would land in, resolved under
    the old rule, and approving one whose canonical changed fails with "no longer resolves". A review for a
    version in scope takes that version's new canonical; any other open review whose canonical differs from
    its raw id is walked again from IGDB (one fetch per hop), since it may be a version we do not hold. Each
    change is printed; a walk IGDB cannot finish is listed for a hand check, never written.

Cost: a derivative needs one IGDB fetch (the walk up to its expansion), an expansion none, plus the open
reviews above. A dry run fetches the same. An IGDB error on one game is listed and skipped, so the moves
already made still get their stage report; a re-run retries it.
"""
from django.core.management.base import BaseCommand
from django.db.models import F, Prefetch, Q

from trophies.models import Concept, ConceptJoinReview, IGDBMatch, Stage
from trophies.services.igdb_service import IGDBService

STANDALONE_EXPANSION = 4
OPEN_REVIEW_STATUSES = ('pending', 'deferred')


def _family_key(family_id, igdb_id):
    return ('igdb', igdb_id) if igdb_id else ('family', family_id)


class Command(BaseCommand):
    help = 'Move standalone expansions (and their direct editions/ports) out of their parent game\'s family.'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true', help='Report the moves without writing.')

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        trusted = IGDBMatch.objects.filter(status__in=IGDBMatch.TRUSTED_STATUSES, igdb_id__isnull=False)
        expansion_ids = list(trusted.filter(game_category=STANDALONE_EXPANSION).values_list('igdb_id', flat=True))
        if not expansion_ids:
            self.stdout.write('No standalone expansions matched. Nothing to do.')
            return

        # IGDB embeds a parent as {id, name, slug}; older cached payloads may hold a bare id.
        below_an_expansion = (
            Q(raw_response__version_parent__id__in=expansion_ids)
            | Q(raw_response__parent_game__id__in=expansion_ids)
            | Q(raw_response__version_parent__in=expansion_ids)
            | Q(raw_response__parent_game__in=expansion_ids)
        )
        scope = (
            trusted.filter(Q(game_category=STANDALONE_EXPANSION) | below_an_expansion)
            .select_related('concept__family')
            .order_by('igdb_name', 'id')
        )

        canonical_of = {}       # raw igdb id -> canonical under the new rule
        # concept pk -> family key (None = no family), captured BEFORE any write: a move can delete a family.
        before, after = {}, {}
        moved = unchanged = 0
        failed = []
        for match in scope.iterator(chunk_size=200):
            concept = match.concept
            old = concept.family
            old_key = _family_key(old.pk, old.igdb_id) if old is not None else None
            old_name = old.canonical_name if old else 'no family'
            try:
                if dry_run:
                    target_id = IGDBService._resolve_canonical_igdb_id(match.raw_response or {}, match.igdb_id)
                else:
                    # Link, then read where it landed: resolving first too would fetch every derivative twice.
                    IGDBService._link_concept_to_family(match, match.raw_response or {})
                    concept.refresh_from_db(fields=['family'])
                    target_id = concept.family.igdb_id
            except Exception as exc:  # one IGDB error must not cost the moves already made their stage report
                failed.append(f'{match.igdb_name}: {exc}')
                continue
            before[concept.pk] = old_key
            canonical_of[match.igdb_id] = target_id
            after[concept.pk] = ('igdb', target_id)
            if old_key == after[concept.pk]:
                unchanged += 1
                continue
            moved += 1
            self.stdout.write(f'  {match.igdb_name}: {old_name} -> family {target_id}')

        verb = 'Would move' if dry_run else 'Moved'
        self.stdout.write(self.style.SUCCESS(f'{verb} {moved} concept(s); {unchanged} already in the right family.'))
        # Stages first: they are worked out from this run's moves, which a re-run cannot see again.
        self._report_split_stages(before, after)
        self._repoint_reviews(canonical_of, dry_run)
        if failed:
            self.stdout.write(self.style.WARNING(
                f'{len(failed)} game(s) failed and were left as they were; re-run to retry them:'
            ))
            for line in failed:
                self.stdout.write(f'  {line}')
        if dry_run and moved:
            self.stdout.write('Re-run without --dry-run to apply.')

    def _report_split_stages(self, before, after):
        """List stages whose direct concepts were N families before the move and more than N after it.

        Read from the keys captured before any write, so a dry run and a real run agree, and a family the
        linker deleted on the way does not matter. An expansion staged with its own remaster moves as a pair
        and is not listed; a stage that already spanned two families is listed only if the move adds one."""
        moved = [pk for pk in after if before[pk] != after[pk]]
        if not moved:
            return
        stages = (
            Stage.objects.filter(concepts__in=moved).distinct().order_by('series_slug', 'stage_number')
            .prefetch_related(Prefetch(
                'concepts', queryset=Concept.objects.only('id', 'unified_title', 'family_id', 'family__igdb_id')
                .select_related('family'),
            ))
        )
        lines = []
        for stage in stages:
            concepts = stage.concepts.all()
            was, now = set(), set()
            for c in concepts:
                current = _family_key(c.family_id, c.family.igdb_id) if c.family_id else None
                was.add(before[c.pk] if c.pk in before else current)
                now.add(after[c.pk] if c.pk in after else current)
            was.discard(None)
            now.discard(None)
            if len(now) > len(was):
                titles = ', '.join(sorted(c.unified_title for c in concepts if c.pk in moved))
                lines.append(f'{stage.series_slug} stage {stage.stage_number} ("{stage.title}"): {titles}')
        if lines:
            self.stdout.write(self.style.WARNING(
                f'{len(lines)} badge stage(s) now hold more than one game. They would generate as separate '
                f'stages; split these by hand:'
            ))
            for line in lines:
                self.stdout.write(f'  {line}')

    def _repoint_reviews(self, canonical_of, dry_run):
        stale, unresolved = [], []
        reviews = ConceptJoinReview.objects.filter(
            status__in=OPEN_REVIEW_STATUSES, proposed_raw_igdb_id__isnull=False,
        ).exclude(proposed_canonical_igdb_id=F('proposed_raw_igdb_id')).order_by('pk')
        for review in reviews:
            raw_id = review.proposed_raw_igdb_id
            if raw_id not in canonical_of:
                # Outside this run's scope (often a version we hold no match for): its stored canonical may
                # have run through an expansion, so walk it again. A walk that could not finish is not a
                # canonical, so it is listed rather than written.
                try:
                    data = IGDBService.fetch_full_game_data(raw_id)
                    canonical, terminal = (
                        IGDBService._resolve_canonical_igdb_data(data, raw_id) if data else (None, None)
                    )
                except Exception:
                    canonical, terminal = None, None
                if terminal is None:
                    unresolved.append(review)
                    continue
                canonical_of[raw_id] = canonical
            new = canonical_of[raw_id]
            if review.proposed_canonical_igdb_id != new:
                stale.append((review, review.proposed_canonical_igdb_id, new))
        if stale:
            verb = 'Would re-point' if dry_run else 'Re-pointed'
            self.stdout.write(f'{verb} {len(stale)} open join review(s) at the new canonical id:')
            for review, old, new in stale:
                self.stdout.write(f'  review {review.pk} (IGDB {review.proposed_raw_igdb_id}): {old} -> {new}')
                review.proposed_canonical_igdb_id = new
            if not dry_run:
                ConceptJoinReview.objects.bulk_update([r for r, _, _ in stale], ['proposed_canonical_igdb_id'])
        if unresolved:
            self.stdout.write(self.style.WARNING(
                f'{len(unresolved)} open join review(s) could not be re-resolved from IGDB; check them by '
                f'hand: ' + ', '.join(str(r.pk) for r in unresolved)
            ))
