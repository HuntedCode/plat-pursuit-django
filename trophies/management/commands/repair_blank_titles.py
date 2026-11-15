"""Repair Games whose title_name is blank, and the PP_ stubs that a blank title produced.

PSN intermittently sends a blank trophyTitleName for a title it names correctly on other syncs.
Until the sync guard in `PsnApiService.create_or_update_game`, a blank overwrote the good title,
and any stub minted afterwards was named just "(PS4)", with no title for IGDB to match on.

Three passes, each safe to re-run:
  1. Fill blank titles from the PSN names already observed for that Game (PSNTitleObservation),
     through `Game.display_list_names`, the same pick and cleaning the site renders with.
     No PSN calls.
  2. Rename PP_ stubs whose whole name is "(<platforms>)" to `<title> (<platforms>)`, and
     regenerate their slug (it was derived from "(PS4)").
  3. Re-run `anchor_concepts --concept` on each renamed stub, so a Game that only missed its
     IGDB match for want of a title gets anchored. Skip with --no-anchor.
"""
import logging

from django.core.management import call_command
from django.core.management.base import BaseCommand

from trophies.models import Concept, Game

logger = logging.getLogger(__name__)

BLANK_TITLE = r'^\s*$'
#: A stub named ONLY its platform parenthetical, which is what default_stub_title makes of a
#: blank title. A real title that merely starts with "(" still has text after the ")".
JUNK_STUB_TITLE = r'^\s*\([^()]*\)\s*$'
BATCH_SIZE = 500


class Command(BaseCommand):
    help = (
        "Fill blank Game titles from observed PSN names, rename the '(PS4)' stubs a blank title "
        "produced, and re-anchor those stubs against IGDB."
    )

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true', help='Report what would change; write nothing.')
        parser.add_argument('--no-anchor', action='store_true', help='Skip the anchor_concepts pass.')

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        planned = self._fill_titles(dry_run)
        renamed = self._rename_junk_stubs(planned, dry_run)
        if dry_run:
            self.stdout.write(self.style.WARNING('\n[DRY RUN] Nothing written.'))
            return
        if options['no_anchor']:
            return
        self._anchor(renamed)

    def _fill_titles(self, dry_run):
        """Pass 1. Returns {game_pk: new title} for every Game it filled (or would fill)."""
        blank = Game.objects.filter(title_name__regex=BLANK_TITLE)
        locked = blank.filter(lock_title=True).count()
        games = list(blank.filter(lock_title=False).only('id', 'np_communication_id', 'title_name'))
        self.stdout.write(f'Blank-title games: {len(games)} (+{locked} locked, skipped)')

        planned, unfillable = {}, []
        for start in range(0, len(games), BATCH_SIZE):
            batch = games[start:start + BATCH_SIZE]
            names = Game.display_list_names(batch)
            for game in batch:
                name = (names.get(game.np_communication_id) or '').strip()
                if not name:
                    unfillable.append(game)
                    continue
                planned[game.pk] = name
                self.stdout.write(f'  {game.np_communication_id} -> {name!r}')
                if not dry_run:
                    game.title_name = name
                    game.save(update_fields=['title_name'])

        self.stdout.write(f'Filled: {len(planned)}. No observed name: {len(unfillable)}.')
        for game in unfillable:
            self.stdout.write(f'  [needs a hand fix] {game.np_communication_id}')
        return planned

    def _rename_junk_stubs(self, planned, dry_run):
        """Pass 2. Returns the concept_ids renamed (or that would be)."""
        stubs = Concept.objects.filter(
            concept_id__startswith='PP_', unified_title__regex=JUNK_STUB_TITLE, title_lock=False,
        ).prefetch_related('games')
        renamed = []
        for stub in stubs:
            # In a dry run pass 1 wrote nothing, so read its plan for the titles it would fill.
            named = [g for g in sorted(stub.games.all(), key=lambda g: g.pk)
                     if (planned.get(g.pk) or g.title_name).strip()]
            if not named:
                self.stdout.write(f'  [still untitled] {stub.concept_id} {stub.unified_title!r}')
                continue
            source = named[0]
            source.title_name = planned.get(source.pk) or source.title_name
            new_title = Concept.default_stub_title(source)
            self.stdout.write(f'  {stub.concept_id}: {stub.unified_title!r} -> {new_title!r}')
            renamed.append(stub.concept_id)
            if not dry_run:
                stub.unified_title = new_title
                stub.slug = ''  # derived from "(PS4)"; Concept.save regenerates a blank slug
                stub.save(update_fields=['unified_title', 'slug'])
        self.stdout.write(f'Junk stubs renamed: {len(renamed)}.')
        return renamed

    def _anchor(self, concept_ids):
        """Pass 3. One anchor_concepts run per stub; a failure costs only that stub."""
        failed = 0
        for concept_id in concept_ids:
            try:
                call_command('anchor_concepts', concept=concept_id, stdout=self.stdout)
            except Exception:
                failed += 1
                logger.exception(f'repair_blank_titles: anchor_concepts failed for {concept_id}')
                self.stderr.write(f'  [anchor FAILED] {concept_id}')
        self.stdout.write(self.style.SUCCESS(
            f'Re-anchored {len(concept_ids) - failed} stub(s); {failed} failed.'
        ))
