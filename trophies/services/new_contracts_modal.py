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

from django.utils import timezone
from django.utils.dateparse import parse_datetime

logger = logging.getLogger(__name__)

#: The ui_flags key. Its own key rather than a `ui_flag` boolean for the same reason
#: `whats_new_seen` is: this is a MOVING marker, and that branch is documented as sticky booleans.
FLAG = 'contracts_seen'

#: How many contracts the modal names before it counts the rest. The modal is a notice, not the board.
MAX_SHOWN = 6



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


def new_for(profile, user, limit=MAX_SHOWN):
    """(contracts, total_new, newest_stamp) for this hunter, or ([], 0, None) when nothing is new.

    `total_new` counts everything published since their marker; `contracts` is the slice the modal
    names. `newest_stamp` is what to store on dismissal -- taken from the wave itself, never from the
    clock, so nothing published mid-visit is skipped.
    """
    from trophies.services.contracts_service import annotated_contracts

    if profile is None or user is None or not getattr(user, 'is_authenticated', False):
        return [], 0, None

    qs = annotated_contracts(profile, with_ranking=False).filter(went_live_at__isnull=False)
    marker = seen_marker(user)
    if marker is not None:
        qs = qs.filter(went_live_at__gt=marker)

    total = qs.count()
    if not total:
        return [], 0, None

    # `status_order` is the board's own SQL ranking -- claimable 0, in progress 1, everything else 2 --
    # already annotated by `annotated_contracts`. Ordering by it is exact and free. Sorting the slice
    # in Python instead was an approximation: a claimable contract outside the first page would never
    # have been promoted into it, which is precisely the one the reader most wants to see.
    ordered = qs.order_by('status_order', '-went_live_at', 'name')
    newest = qs.order_by('-went_live_at').values_list('went_live_at', flat=True).first()
    return list(ordered[:limit]), total, newest


# NO `is_due` helper here, deliberately. The view needs the contracts, the total AND the stamp, so a
# separate "is anything due" call would run the same query twice on every Career render for every
# hunter. One `new_for` call answers all three: `total > 0` IS the gate.
