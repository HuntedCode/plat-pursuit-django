"""The three ways a concept belongs to a badge stage, written down once.

A stage reaches its games by three paths:

  Stage.concepts                      a whole list counts
  Stage.concept_bundles -> concepts   an episodic set that counts only together (ConceptBundle)
  Stage.trophy_groups -> concept      one trophy group inside a list counts on its own (a game shipped as DLC
                                      in a collection, such as the Kingdom Hearts PS5 sets)

Every question of the form "which series is this game in?" must follow all three. Matching fewer does not
miss the odd game: it misses EVERY member of the path it skips, and nothing errors. That was a real bug in both
badge engines for bundles, and it is why the paths live here instead of being retyped at each call site.

One deliberate exception, `lists_only=True`: a question about the LIST ITSELF (its platinum, its contract, its
genres) must not follow the trophy-group path. There the concept is a whole collection, a different game from
the one the stage asks for, so the collection's platinum is not badge work and its contract does not cover the
stage.
"""
from django.db.models import Q

#: From a Concept to the `series_slug` of a stage it belongs to, one lookup per path. The trophy-group path is
#: LAST, so `[:-1]` is the whole-list paths (see `lists_only`).
CONCEPT_SERIES_PATHS = (
    'stages__series_slug',
    'bundles__stage__series_slug',
    'concept_trophy_groups__stages__series_slug',
)

#: From a Stage to a member Concept, one lookup per path, trophy-group path last as above.
STAGE_CONCEPT_PATHS = ('concepts', 'concept_bundles__concepts', 'trophy_groups__concept')


def _paths(paths, lists_only):
    return paths[:-1] if lists_only else paths


def concept_series_paths(*, lists_only=False):
    return _paths(CONCEPT_SERIES_PATHS, lists_only)


def stage_concept_paths(*, lists_only=False):
    return _paths(STAGE_CONCEPT_PATHS, lists_only)


def in_series_q(slugs, prefix='', *, lists_only=False):
    """Q matching a concept that belongs to a stage of any series in `slugs`.

    `prefix` is the path from the queried model to the concept: '' on Concept, 'concept__' on Game. The joins
    can repeat a row, so callers that need one row per match add `.distinct()`. From a model that fans out
    before the concept (a Profile's played games), filter on `concept_id__in=` a Concept subquery built from
    this instead: OR'd joins multiply every played game by its concept's trophy groups."""
    q = Q()
    for path in concept_series_paths(lists_only=lists_only):
        q |= Q(**{f'{prefix}{path}__in': slugs})
    return q


def stage_has_concept_q(concept_ids, *, lists_only=False):
    """Q matching a Stage that holds any of `concept_ids` (ids or Concepts) by any path."""
    q = Q()
    for path in stage_concept_paths(lists_only=lists_only):
        q |= Q(**{f'{path}__in': concept_ids})
    return q
