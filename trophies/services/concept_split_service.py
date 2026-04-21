"""Bundle-concept split and reverse-merge logic.

A compilation concept is a PSN-side concept that bundles multiple distinct
trophy lists (e.g. Spyro Reignited Trilogy = 3 games = 3 trophy lists under
one concept). Splitting separates those trophy lists into their own concepts
so each game enriches against its own IGDB entry.

Algorithm is deliberately narrow:

  * Keep the parent concept intact (preserves ratings, reviews, comments).
  * Pick a "kept" Game by platform priority; parent is renamed to its title.
  * Each other Game is moved onto a new child concept.
  * Re-run IGDB enrichment on the parent and each child — no bespoke IGDB
    bundle-member lookup. Each child has exactly one Game with a clean title,
    which the Phase 2 matcher resolves against its own IGDB entry.

Reverse-merge undoes a split by moving child Games back onto the parent.
`game.add_concept()` automatically triggers `parent.absorb(child)` + child
deletion once a child becomes empty, so accumulated data on children
(ratings, reviews, etc. from after the split) migrates back to the parent.
"""

import logging

from django.db import transaction
from django.utils import timezone

from trophies.models import Concept, ConceptSplitEvent
from trophies.util_modules.constants import PLATFORM_PRIORITY_ORDER


logger = logging.getLogger('psn_api')


class ConceptSplitError(Exception):
    """Raised when a split or reverse-merge cannot proceed safely."""


def _platform_rank(game):
    """Lower rank = higher priority (newer platform)."""
    platforms = game.title_platform or []
    for idx, platform in enumerate(PLATFORM_PRIORITY_ORDER):
        if platform in platforms:
            return idx
    return len(PLATFORM_PRIORITY_ORDER)


def _pick_kept_game(games):
    """Pick the Game to retain on the parent: newest platform wins.

    Ties are broken by the Game's pk (stable within a sync session).
    """
    return min(games, key=lambda g: (_platform_rank(g), g.pk))


def preview_split(concept):
    """Read-only preview used by the admin confirmation page.

    Returns a dict describing the proposed parent rename and child-concept
    creations so the admin can sanity-check before executing. Does not
    raise — surfaces validation issues via the `'issues'` key so the
    template can render them.
    """
    games = list(concept.games.all().order_by('id'))
    issues = []
    if len(games) < 2:
        issues.append('Concept has fewer than 2 Games — nothing to split.')
    locked = [g for g in games if g.concept_lock]
    if locked:
        names = ', '.join(f'{g.title_name} ({g.np_communication_id})' for g in locked)
        issues.append(
            f'Cannot split: the following Games have concept_lock=True and '
            f'would refuse the reassignment: {names}'
        )

    if games:
        kept = _pick_kept_game(games)
    else:
        kept = None

    proposed_children = []
    if kept is not None:
        for game in games:
            if game.id == kept.id:
                continue
            proposed_children.append({
                'game_id': game.id,
                'game_title': game.title_name,
                'np_communication_id': game.np_communication_id,
                'platforms': list(game.title_platform or []),
                'new_concept_title': game.title_name,
            })

    return {
        'concept': concept,
        'games': games,
        'kept_game': kept,
        'proposed_parent_title': kept.title_name if kept else None,
        'proposed_children': proposed_children,
        'issues': issues,
    }


def split_compilation(*, concept, user=None):
    """Split a multi-game compilation concept into parent + N-1 children.

    Raises `ConceptSplitError` on validation failure; writes a
    `ConceptSplitEvent` audit row on success.

    Returns:
        ConceptSplitEvent
    """
    games = list(concept.games.all().order_by('id'))
    if len(games) < 2:
        raise ConceptSplitError(
            f'Concept {concept.concept_id} has {len(games)} Game(s) — need 2+ to split.'
        )
    locked = [g for g in games if g.concept_lock]
    if locked:
        names = ', '.join(f'{g.title_name} ({g.np_communication_id})' for g in locked)
        raise ConceptSplitError(
            f'Cannot split {concept.concept_id}: the following Games are locked '
            f'(concept_lock=True) and would refuse reassignment: {names}'
        )

    match = getattr(concept, 'igdb_match', None)
    original_title = concept.unified_title or ''
    original_igdb_id = match.igdb_id if match else None
    original_igdb_name = match.igdb_name if match else ''

    kept_game = _pick_kept_game(games)
    other_games = [g for g in games if g.id != kept_game.id]

    logger.info(
        f'Splitting compilation {concept.concept_id} "{original_title}": '
        f'keeping game {kept_game.id} "{kept_game.title_name}", '
        f'spinning off {len(other_games)} child concept(s)'
    )

    children = []
    with transaction.atomic():
        # Delete the compilation-era match so the parent can re-match against
        # the kept Game's IGDB entry cleanly.
        if match:
            match.delete()

        concept.unified_title = kept_game.title_name or original_title
        concept.save(update_fields=['unified_title'])

        for game in other_games:
            # create_default_concept() allocates a unique PP_* id via Redis
            # counter. Then override its "Title (Platforms)" default with the
            # raw game title — the new child is a real concept, not a stub.
            child = Concept.create_default_concept(game)
            child.unified_title = game.title_name or child.unified_title
            child.save(update_fields=['unified_title'])
            game.add_concept(child)  # moves game FK off parent onto child
            children.append(child)

        event = ConceptSplitEvent.objects.create(
            parent_concept=concept,
            parent_original_title=original_title,
            parent_original_igdb_id=original_igdb_id,
            parent_original_igdb_name=original_igdb_name,
            kept_game_id=kept_game.id,
            created_by=user,
        )
        event.child_concepts.set(children)

    # Enrichment runs outside the atomic block — it hits IGDB and shouldn't
    # hold an open DB transaction through HTTP latency.
    from trophies.services.igdb_service import IGDBService
    try:
        IGDBService.enrich_concept(concept)
    except Exception:
        logger.exception(f'Enrichment failed for parent concept {concept.concept_id} after split')
    for child in children:
        try:
            IGDBService.enrich_concept(child)
        except Exception:
            logger.exception(f'Enrichment failed for child concept {child.concept_id} after split')

    return event


def reverse_split(*, event, user=None):
    """Undo a previous split: move child Games back to the parent and absorb children.

    Parent's pre-split title and IGDB match are restored from the snapshot.
    Any social data accumulated on child concepts after the split migrates to
    the parent through `Concept.absorb()` when each child becomes empty.

    Raises `ConceptSplitError` on validation failure.
    """
    if event.is_reversed:
        raise ConceptSplitError('This split has already been reversed.')
    parent = event.parent_concept
    if parent is None:
        raise ConceptSplitError(
            'Parent concept is gone (likely deleted). Cannot reverse this split.'
        )

    children = list(event.child_concepts.all())
    if not children:
        raise ConceptSplitError(
            'No child concepts remain to merge back. '
            'Marking reversed without any Game moves.'
        )

    # Locked games anywhere in the children block a reverse-merge for the same
    # reason they block a forward split: add_concept silently bails on locked
    # games, which would leave a partial undo.
    locked = [
        g for child in children
        for g in child.games.all()
        if g.concept_lock
    ]
    if locked:
        names = ', '.join(f'{g.title_name} ({g.np_communication_id})' for g in locked)
        raise ConceptSplitError(
            f'Cannot reverse: the following child Games are locked '
            f'(concept_lock=True): {names}'
        )

    logger.info(
        f'Reversing split {event.pk}: merging {len(children)} child concept(s) '
        f'back into parent {parent.concept_id}'
    )

    with transaction.atomic():
        # Restore title BEFORE the merge so parent's subsequent enrichment
        # search hits the original bundle name, not the kept-Game name that
        # the split left behind.
        parent.unified_title = event.parent_original_title or parent.unified_title
        parent.save(update_fields=['unified_title'])

        for child in children:
            for game in list(child.games.all()):
                game.add_concept(parent)
            # add_concept auto-triggers parent.absorb(child) + child.delete()
            # once the child becomes empty, so no explicit cleanup here.

        # After all absorbs, parent may have inherited a child's IGDBMatch
        # (absorb copies the match over when the target lacks one). That match
        # points at a single game, not the bundle — delete it and re-enrich
        # against the restored original title.
        match = getattr(parent, 'igdb_match', None)
        if match:
            match.delete()

        event.is_reversed = True
        event.reversed_at = timezone.now()
        event.reversed_by = user
        event.save(update_fields=['is_reversed', 'reversed_at', 'reversed_by'])

    from trophies.services.igdb_service import IGDBService
    try:
        IGDBService.enrich_concept(parent)
    except Exception:
        logger.exception(f'Enrichment failed for parent concept {parent.concept_id} after reverse')

    return event
