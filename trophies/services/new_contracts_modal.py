"""Who is due the "new contracts" modal on Career, and what it should show them.

ANNOUNCED, NOT MERELY PUBLISHED. A contract reaches this modal only once `announce_contracts` has
POSTED it -- `announced_at` plus `announcement_posted`, not `went_live_at`. Both halves are needed:
`--baseline` stamps `announced_at` too, because it also settles the row, but it settles it by
deciding not to post. The launch catalogue is settled that way, and a reader who was never told
about a thousand contracts must not be handed a modal listing them. Publishing is a staff action that
happens whenever staff happen to do it: a one-off fix, a single game re-added, a correction. Gating
on it meant any of those popped a modal at every hunter announcing one game, which is not an
announcement, it is a notification about housekeeping.

The announcer already batches: it runs daily, is silent when nothing is new, and refuses a wave
bigger than MAX_WAVE. Deferring to it gives the modal the same rhythm for free -- a game fixed on
Tuesday travels with Wednesday's wave -- and makes the two halves of an announcement say the SAME
thing, which is what an announcement is. It also means a wave that failed to post to Discord shows
nobody a modal claiming it was announced.

THE MARKER is the newest `announced_at` this hunter has already been shown, stored as an ISO string
in `ui_flags['contracts_seen']`. Not a timestamp of "now": a wave announced between the render and
the dismissal would be silently skipped by a now-stamp, because it was announced before the click but
after the query. Storing what was actually SHOWN cannot skip anything -- the same reasoning as What's
New storing the newest entry id rather than a boolean.

It has to be the SAME column the filter uses. A marker holding a `went_live_at` against a filter on
`announced_at` would skip every contract published before the marker and announced after it -- which
is precisely the batched one-off this gate exists to carry.

Deliberately NOT the global 14-day `NEW_CONTRACT_WINDOW_DAYS` window that the board's Latest chip and
the card markers use. That window answers "is this contract new?"; this answers "is this new TO YOU?".
A hunter away for three weeks would be told nothing by the window and everything by the marker, and
they are the person the modal exists for.

WITH ONE EXCEPTION: a hunter who has NO marker yet. "New to you" is then everything ever posted,
which is true and useless -- so a first visit falls back to that same 14-day window. It is the only
case where the two questions have to agree, because it is the only case where the personal one has
no answer of its own.

ORDERED BY WHAT THEY CAN ACT ON. `annotated_contracts` already decorates every contract with this
viewer's status in SQL, so leading with the ones already claimable or in progress costs nothing and
turns a catalogue notice into "you have already done the work on two of these".
"""
import logging

from django.db.models import Prefetch
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from core.services.contract_announcer import DISCIPLINE_ORDER
from trophies.models import Job

logger = logging.getLogger(__name__)

#: The ui_flags key. Its own key rather than a `ui_flag` boolean for the same reason
#: `whats_new_seen` is: this is a MOVING marker, and that branch is documented as sticky booleans.
FLAG = 'contracts_seen'

#: HEROES: the contracts that get cover art, and the TOP of the same progress order the list uses --
#: so the covers are the ones this hunter is furthest along on, not an arbitrary six.
#:
#: Six rather than three because three covers at dialog width were tall enough to push the list below
#: the fold. The dialog renders all six and CSS shows 2 / 4 / 6 by breakpoint: a phone gets two big
#: covers, a desktop gets six smaller ones, and the modal fits on screen at every size.
MAX_HEROES = 6

#: The scroll list's ceiling. Raised from 60 after the owner asked what the meaningful difference
#: was -- and the honest answer was that 60 defended against a cost I had created: the rows were
#: rendering icons with `job_icon` (a full inline SVG, 674 bytes) instead of `job_icon_use` (a sprite
#: reference, 186). At 3.6x smaller, 200 rows of icons weigh less than 60 did.
#:
#: A ceiling still exists because this renders on EVERY Career load, and DOM nodes are the cost that
#: does not shrink -- but 200 clears every realistic wave. The announcer's own MAX_WAVE guard trips
#: at 40, and the only way past 200 is a hunter who has been away for months, who is exactly the
#: person the board link below the list is for.
MAX_LIST = 200


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


#: ORDERED BY WHAT THEY CAN ACT ON, THEN BY HOW FAR ALONG THEY ARE. `annotated_contracts` decorates
#: every contract with this viewer's `status_order` (claimable 0, pursuing 1, available 2) and
#: `sort_progress` (percent, but only while pursuing) in SQL for the board anyway, so this is the
#: board's own default ordering rather than a second definition of "most relevant to you".
#:
#: Sorting the SLICE in Python instead was an approximation with a real failure: a claimable contract
#: outside the first page could never be promoted into it, and that is the row the reader most wants.
_ORDER = ('status_order', '-sort_progress', '-announced_at', '-went_live_at', 'name')


def _hero_covers(heroes):
    """{igdb_id: cover url} for the hero contracts, in ONE query.

    The announcer's `cover_url_for` answers this per contract, which is right for a post naming three
    games and wrong here: six heroes on every Career load would be six round trips. Same gate
    (`_member_gate`), same `display_image_url` fallback chain, same most-played tie-break -- DISTINCT
    ON just resolves them together.

    `.defer(...raw_response)` is not decoration: that column is the ~30 KB IGDB blob behind the May
    2026 web-server OOM, and nothing in a cover URL reads it.
    """
    from trophies.models import Game
    from trophies.services.contracts_service import _member_gate

    ids = [c.igdb_id for c in heroes if c.igdb_id]
    if not ids:
        return {}

    games = (Game.objects
             .filter(**_member_gate('concept__'), concept__igdb_match__igdb_id__in=ids)
             .select_related('concept', 'concept__igdb_match')
             .defer('concept__igdb_match__raw_response')
             .order_by('concept__igdb_match__igdb_id', '-played_count')
             .distinct('concept__igdb_match__igdb_id'))

    covers = {}
    for game in games:
        url = game.display_image_url or ''
        # Absolute or nothing. Every source in the chain is a URLField holding a PSN or IGDB CDN
        # address, so a relative value cannot arise -- and if one ever did, no art beats broken art.
        covers[game.concept.igdb_match.igdb_id] = url if url.startswith('http') else ''
    return covers


def new_for(profile, user, limit=MAX_LIST):
    """Everything the modal needs, in a bounded number of queries.

    Returns a dict: `heroes`, `rows`, `disciplines`, `total`, `extra`, `newest`. Empty `rows` means
    is new and the modal should not render at all.

    `newest` is what to store on dismissal -- taken from the wave itself, never from the clock, so a
    wave announced between this query and the click is not silently marked seen.
    """
    from trophies.services.contracts_service import annotated_contracts, new_contract_cutoff

    empty = {'heroes': [], 'rows': [], 'disciplines': [], 'total': 0, 'extra': 0, 'newest': None}
    if profile is None or user is None or not getattr(user, 'is_authenticated', False):
        return empty

    # POSTED, not merely settled. `announced_at` is the clock, but on its own it also covers the
    # rows `--baseline` recorded as known WITHOUT posting -- the ~1,000 launch contracts among them,
    # which do carry `went_live_at`. Announcing to a reader something nobody was ever told is the
    # one outcome this whole gate exists to prevent, so the flag is part of the filter.
    #
    # `went_live_at` needs no filter of its own: the announcer only ever sees contracts that have
    # one, so a stamp here implies it.
    qs = (annotated_contracts(profile, with_ranking=False)
          .filter(announced_at__isnull=False, announcement_posted=True))
    marker = seen_marker(user)
    if marker is not None:
        qs = qs.filter(announced_at__gt=marker)
    else:
        # NO MARKER MEANS NO FLOOR, and without one that reads as "everything ever posted is new to
        # you" -- literally true and useless: a hunter who signs up in a year would be met with every
        # wave since launch on their first Career visit.
        #
        # Only two people have no marker: a brand-new account, and everyone at rollout. Both want the
        # same thing, which is the recent past rather than the archive. `new_contract_cutoff` is the
        # site's existing 14-day answer to "is this contract new?" -- the board's Latest chip and the
        # card markers read it too -- so a first visit sees exactly what the board is calling new.
        #
        # This does NOT touch the hunter away for three weeks. They have a marker, so the floor never
        # applies to them and they are still told everything they missed, which is the whole reason
        # the marker exists.
        qs = qs.filter(announced_at__gte=new_contract_cutoff())

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
        qs.order_by(*_ORDER)
          .prefetch_related(Prefetch('jobs', queryset=Job.objects.order_by('name')))[:limit]
    )
    newest = qs.order_by('-announced_at').values_list('announced_at', flat=True).first()

    heroes = rows[:MAX_HEROES]
    covers = _hero_covers(heroes)
    for hero in heroes:
        hero.cover_url = covers.get(hero.igdb_id, '')

    return {
        'heroes': heroes,
        'rows': rows,
        'disciplines': _job_facets(rows),
        'total': total,
        'extra': max(total - len(rows), 0),
        'newest': newest,
    }


def _job_facets(rows):
    """The filter, GROUPED BY DISCIPLINE: [{slug, label, count, jobs: [{slug, name, icon, count}]}].

    Shaped for the `.rp-discs` dropdown the Career board and Browse Games already use -- a discipline
    trigger in its own colour opening a popover of its jobs -- rather than a flat row of chips. With
    25 jobs a flat row is a horizontal scroll nobody reads; grouped, it is five triggers.

    Built from the rows the modal actually shows, never a separate aggregate: a facet that counts
    contracts the list cannot display is a filter that leads to an emptier list than it advertised.

    A discipline's count is DISTINCT CONTRACTS, not the sum of its jobs' counts -- one contract
    feeding two jobs in the same discipline is one contract to that discipline, and adding the job
    counts would say two.
    """
    from trophies.models import Job

    labels = dict(Job.DISCIPLINES)
    discs = {}
    for contract in rows:
        for job in contract.jobs.all():
            disc = discs.setdefault(job.discipline, {
                'slug': job.discipline,
                'label': labels.get(job.discipline, job.discipline.title()),
                'jobs': {},
                'contracts': set(),
            })
            disc['contracts'].add(contract.pk)
            entry = disc['jobs'].setdefault(job.slug, {
                'slug': job.slug, 'name': job.name, 'icon': job.icon, 'count': 0,
            })
            entry['count'] += 1

    out = []
    for slug in DISCIPLINE_ORDER:
        if slug not in discs:
            continue
        d = discs[slug]
        out.append({
            'slug': slug,
            'label': d['label'],
            'count': len(d['contracts']),
            'jobs': sorted(d['jobs'].values(), key=lambda j: (-j['count'], j['name'])),
        })
    return out


# NO `is_due` helper here, deliberately. The view needs the contracts, the total AND the stamp, so a
# separate "is anything due" call would run the same query twice on every Career render for every
# hunter. One `new_for` call answers all three: `total > 0` IS the gate.
