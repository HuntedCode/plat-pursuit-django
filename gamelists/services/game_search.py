"""The catalogue half of an adder's typeahead, shared by every surface that adds games to something.

EXTRACTED RATHER THAN COPIED when Tiers/Grids/Polls needed the same adder. The split was already
here in spirit: `ListGameSearchView` cached its results on the NORMALIZED QUERY ALONE and applied the
per-list `already_added` flag after the cache, precisely because the catalogue answer is the same for
everybody and is the expensive half. This module is that half, given a name.

The alternative was a second copy in `prompts`. What made that the wrong call is not line count -- it
is that the comments below record two real incidents (a sequential scan misdescribed as an index
seek, and a per-keystroke read of an entire list into a Python set), and a copy is a place where the
next fix does not land.

WHAT STAYS WITH THE CALLER is the membership flag: which of these results is already in THIS list,
THIS prompt, whatever container is asking. That question needs the caller's table and its own index,
and it is deliberately not cached -- see `ListGameSearchView` for the bounded shape it must take.

THE INDEX CLAIM THAT USED TO BE HERE WAS FALSE, and is preserved rather than quietly dropped: this
does NOT ride the pg_trgm GIN index on `Concept.unified_title`. Django compiles `__icontains` on
Postgres to `UPPER(col::text) LIKE UPPER(%q%)` (verified, not assumed), and a `gin_trgm_ops` index on
the raw column cannot serve a LIKE against a function of that column. There is no expression index on
`UPPER(unified_title::text)` anywhere in the tree, so this is a sequential scan of the catalogue.
`SiteSuggestView` has the same shape. Fixing that -- and deciding whether `trophies_concept` gains an
expression index -- is a change to shared catalogue infrastructure and belongs in its own lane with
EXPLAIN output from production.

What this module CAN do, and does, is bound the damage: a rate limit at each view, a short cache keyed
on the normalized query, and an upper bound on the term.
"""
from django.core.cache import cache
from django.db.models import Case, CharField, F, IntegerField, Value, When, Window
from django.db.models.functions import Cast, Concat, RowNumber

from gamelists.services.covers import cover_games_for
from trophies.models import Concept, IGDBMatch

#: How many rows a typeahead answers with. Also the bound every caller's membership check must respect
#: -- the answer only ever needs to be known for the rows being rendered.
#:
#: 20, up from 12 (2026-09). The panel scrolls, so the cost of more rows is bytes rather than layout,
#: and 12 was demonstrably too few the moment a series shared a substring with the thing being looked
#: for: searching "myst" returned twelve titles beginning with it and never reached
#: "Riven: The Sequel to Myst". The ranking below is the real fix; this widens the window it ranks
#: into.
LIMIT = 20

#: THREE, not two. pg_trgm extracts no trigrams from a two-character pattern, so a 2-char query is a
#: guaranteed full pass even once the index question above is settled.
MIN_QUERY = 3

#: An unbounded `q` is an unbounded LIKE pattern. Matches the browse filters' own bound in both apps,
#: and lives here now so a third caller cannot pick a different one.
MAX_QUERY = 64

CACHE_TTL = 60

#: Keyed on the query and NOTHING ELSE -- not the caller, not the container, not the viewer. That is
#: what makes the expensive half shareable across every adder on the site, and it is why the
#: membership flag must be applied afterwards. `adder:` rather than the old `gamelists:search:` prefix
#: because the cached value is a catalogue answer that was never about lists; the rename costs one
#: minute of cold cache.
_CACHE_PREFIX = 'adder:search:'


class SearchRefused(Exception):
    """The term itself is refused. Carries the words the caller should answer with."""


def search_concepts(query):
    """`[{concept_id, title, cover}]` for a typeahead term, cached across every caller.

    Returns an EMPTY LIST for a term that is merely too short, and raises `SearchRefused` for one that
    is too long. The asymmetry is deliberate and is the existing behaviour: a short term is somebody
    still typing, which is not an error and must not paint one; a 400-character term is not a search.
    """
    query = (query or '').strip()
    if len(query) > MAX_QUERY:
        raise SearchRefused('That search is too long.')
    if len(query) < MIN_QUERY:
        return []

    cache_key = f'{_CACHE_PREFIX}{query.lower()}'
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    # `select_related('igdb_match')` paired with `.defer('igdb_match__raw_response')`, because
    # `display_image_url` reads the IGDB cover FIRST on every render -- without the select_related
    # that is a query per row, and without the defer each one drags the ~30 KB API blob that caused
    # the May 2026 web-server OOM. CLAUDE.md requires the pairing; neither half is optional.
    # RANKED BY HOW WELL THE TITLE MATCHES, then alphabetically inside each tier.
    #
    # This was `order_by('unified_title')[:LIMIT]` -- the alphabet, truncated. Which means a title
    # that CONTAINS the term but does not START with it competes on spelling against every title
    # that does, and loses. The reported case: "myst" never reached "Riven: The Sequel to Myst",
    # because a dozen titles beginning with those four letters sort ahead of anything starting with
    # R. The result was a search that looked broken on exactly the query a series had flooded.
    #
    # Four tiers, cheapest first to read:
    #   0  the whole title IS the term          -- "Myst"
    #   1  the title begins with it             -- "Myst III", "Mystery Case Files"
    #   2  it begins a WORD inside the title    -- "Riven: The Sequel to Myst"
    #   3  it appears mid-word                  -- "Pathologic" for "logi"
    #
    # Tier 2 is the one that earns this. A term at a word boundary is almost always the game
    # somebody means, and it was previously indistinguishable from a mid-word coincidence. It is
    # approximated with a leading space rather than a regex: `~*` would cost a per-row regex match
    # on a scan that is already the expensive part, and the space catches every real title because
    # the alternative -- a word starting mid-token -- is what tier 3 is for.
    #
    # No index is lost by this. The `icontains` cannot use one either way (see the module docstring),
    # so the sort was always over the matched set.
    # ONE ROW PER GAME PAGE, not per Concept.
    #
    # `Concept.game_page_url` states the identity rule: "deliberately-split concepts sharing an
    # igdb_id share one page". So two concepts can be the SAME GAME -- Browse Games collapses them
    # with `game_page_canonicals`, and this search did not, which meant the adder offered two rows
    # for one game while the catalogue showed one card. The same partition key, computed on Concept
    # rather than on Game.
    #
    # WHICH ONE REPRESENTS THE PAGE barely matters for navigation -- both route to the same place --
    # so the tiebreak is chosen for the READER: the concept whose title matches the query best, then
    # the lowest id so the answer is stable across requests and therefore cacheable.
    page_key = Case(
        When(igdb_match__status__in=IGDBMatch.TRUSTED_STATUSES,
             igdb_match__igdb_id__isnull=False,
             then=Concat(Value('igdb:'), Cast('igdb_match__igdb_id', CharField()))),
        default=Concat(Value('concept:'), F('concept_id')),
        output_field=CharField(),
    )

    concepts = list(
        Concept.objects.filter(unified_title__icontains=query)
        .exclude(unified_title='')
        .annotate(_match_rank=Case(
            When(unified_title__iexact=query, then=Value(0)),
            When(unified_title__istartswith=query, then=Value(1)),
            When(unified_title__icontains=' ' + query, then=Value(2)),
            default=Value(3),
            output_field=IntegerField(),
        ))
        .annotate(_page_key=page_key)
        .annotate(_page_rank=Window(
            expression=RowNumber(),
            partition_by=[F('_page_key')],
            order_by=[F('_match_rank').asc(), F('concept_id').asc()],
        ))
        # NOTHING NARROWING MAY BE CHAINED AFTER THIS. Django wraps a window filter in a subquery,
        # so a `.filter()` here lands INSIDE it and silently narrows the election POPULATION rather
        # than the elected rows -- the trap `GamesListView` documents at length. Only ordering,
        # `select_related`, `defer` and the slice follow, which are the verified-safe shapes.
        .filter(_page_rank=1)
        .select_related('igdb_match')
        .defer('igdb_match__raw_response')
        .order_by('_match_rank', 'unified_title')[:LIMIT]
    )
    covers = cover_games_for([concept.pk for concept in concepts])
    results = [
        {
            'concept_id': concept.pk,
            'title': concept.unified_title,
            # A concept with no trophy list is simply absent from the mapping -- that is
            # `cover_games_for`'s stated contract, so this is a miss rather than a None to guard.
            'cover': covers[concept.pk].display_image_url if concept.pk in covers else '',
        }
        for concept in concepts
    ]
    cache.set(cache_key, results, CACHE_TTL)
    return results
