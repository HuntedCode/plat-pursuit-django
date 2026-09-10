"""Who is due the "new contracts" modal on Career, and what it should show them.

THE MARKER is the newest `went_live_at` this hunter has already been shown, stored as an ISO string
in `ui_flags['contracts_seen']`. Not a timestamp of "now": a contract published between the render
and the dismissal would be silently skipped by a now-stamp, because it went live before the click but
after the query. Storing what was actually SHOWN cannot skip anything -- the same reasoning as What's
New storing the newest entry id rather than a boolean.

Deliberately NOT the global 14-day `NEW_CONTRACT_WINDOW_DAYS` window that the board's Latest chip and
the card markers use. That window answers "is this contract new?"; this answers "is this new TO YOU?".
A hunter away for three weeks would be told nothing by the window and everything by the marker, and
they are the person the modal exists for.

ORDERED BY WHAT THEY CAN ACT ON. `annotated_contracts` already decorates every contract with this
viewer's status in SQL, so leading with the ones already claimable or in progress costs nothing and
turns a catalogue notice into "you have already done the work on two of these".
"""
import logging

from django.db.models import Prefetch
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from core.services.contract_announcer import cover_url_for
from trophies.models import Job

logger = logging.getLogger(__name__)

#: The ui_flags key. Its own key rather than a `ui_flag` boolean for the same reason
#: `whats_new_seen` is: this is a MOVING marker, and that branch is documented as sticky booleans.
FLAG = 'contracts_seen'

#: HEROES: the contracts that get cover art. Three fits the dialog at desktop width without shrinking
#: the art to a thumbnail; CSS drops it to one on a phone, where a big cover is the whole draw.
MAX_HEROES = 3

#: The scroll list's hard ceiling, and it is a real one rather than a tidy number. This renders on
#: EVERY Career load for every hunter, so a bulk publish dropping three hundred contracts must not
#: become three hundred rows plus their job icons in a modal nobody asked for. Past this the board
#: is the right surface, and the footer link says so.
MAX_LIST = 60

#: Kept for the callers/tests that still speak in "how many does it name".
MAX_SHOWN = MAX_LIST



def seen_marker(user):
    """The stamp this hunter was last shown, or None. Junk parses to None -- which shows them the
    modal again rather than hiding it forever, the safer direction for a value we do not control."""
    raw = (getattr(user, 'ui_flags', None) or {}).get(FLAG)
    if not isinstance(raw, str):
        return None
    parsed = parse_datetime(raw)
    if parsed is None:
        logger.debug("Unparseable %s marker: %r", FLAG, raw)
        return None
    return parsed if timezone.is_aware(parsed) else timezone.make_aware(parsed)


def new_for(profile, user, limit=MAX_LIST):
    """Everything the modal needs, in a bounded number of queries.

    Returns a dict: `heroes`, `rows`, `jobs`, `total`, `extra`, `newest`. Empty `rows` means nothing
    is new and the modal should not render at all.

    `newest` is what to store on dismissal -- taken from the wave itself, never from the clock, so a
    contract published between this query and the click is not silently marked seen.
    """
    from trophies.services.contracts_service import annotated_contracts

    empty = {'heroes': [], 'rows': [], 'jobs': [], 'total': 0, 'extra': 0, 'newest': None}
    if profile is None or user is None or not getattr(user, 'is_authenticated', False):
        return empty

    qs = annotated_contracts(profile, with_ranking=False).filter(went_live_at__isnull=False)
    marker = seen_marker(user)
    if marker is not None:
        qs = qs.filter(went_live_at__gt=marker)

    total = qs.count()
    if not total:
        return empty

    # `status_order` is the board's own SQL ranking -- claimable 0, in progress 1, everything else 2,
    # annotated by `annotated_contracts` for the board anyway. Ordering by it is exact and free.
    # Sorting the slice in Python instead was an approximation: a claimable contract outside the
    # first page could never be promoted into it, which is the one the reader most wants to see.
    #
    # ONE query for the rows plus ONE prefetch for their jobs. The jobs are needed twice -- for each
    # row's icons and for the filter chips -- and computing the chips from the prefetched rows rather
    # than from a second aggregate keeps the two consistent by construction: a chip can never count a
    # contract the list does not show.
    rows = list(
        qs.order_by('status_order', '-went_live_at', 'name')
          .prefetch_related(Prefetch('jobs', queryset=Job.objects.order_by('name')))[:limit]
    )
    newest = qs.order_by('-went_live_at').values_list('went_live_at', flat=True).first()

    heroes = rows[:MAX_HEROES]
    for hero in heroes:
        # Reuses the announcer's definition of "this contract's cover" rather than a second one --
        # it already walks the member-game gate and `display_image_url`'s fallback chain, and two
        # answers to "which picture represents this contract" is exactly one too many.
        hero.cover_url = cover_url_for(hero)

    return {
        'heroes': heroes,
        'rows': rows,
        'jobs': _job_facets(rows),
        'total': total,
        'extra': max(total - len(rows), 0),
        'newest': newest,
    }


def _job_facets(rows):
    """[{slug, name, icon, discipline, count}] for the filter chips, biggest first then alphabetical.

    Built from the rows the modal actually shows, not from a separate aggregate: a chip that counts
    contracts the list cannot display is a filter that leads to an empty list.
    """
    seen = {}
    for contract in rows:
        for job in contract.jobs.all():
            entry = seen.setdefault(job.slug, {
                'slug': job.slug, 'name': job.name, 'icon': job.icon,
                'discipline': job.discipline, 'count': 0,
            })
            entry['count'] += 1
    return sorted(seen.values(), key=lambda j: (-j['count'], j['name']))


# NO `is_due` helper here, deliberately. The view needs the contracts, the total AND the stamp, so a
# separate "is anything due" call would run the same query twice on every Career render for every
# hunter. One `new_for` call answers all three: `total > 0` IS the gate.
