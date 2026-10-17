"""A fair, first-come line for the Challenge Optimizer's whole-catalogue badge builds (owner, 2026-10-16).

WHY A LINE: at most `SLOTS` whole-catalogue builds ("All badges", a whole type) run at once across the site,
because that is the one build whose memory grows with the catalogue. A hunter who arrives while every slot is
taken takes a TICKET and waits their turn; the page re-asks every few seconds saying where they stand ("You're
3rd in line, about 8 seconds"). A measured whale build is about 1.6s of data work (prod, 2026-10-16), so a wait is
seconds, which is why this is an in-page line rather than a background job.

HOW IT WORKS, all in raw Redis (the Django cache API has no sorted sets). Four sorted sets and a hash:

- `planner:catalogue-holders`: the builds running now, one LEASE each, scored by start time. A lease older than
  `LEASE_SECONDS` is cleared on the next look whatever else is happening, so a build killed mid-run (OOM killer,
  gunicorn timeout: exactly the memory-heavy case) can hold its slot at most that long. (The first version kept a
  counter whose TTL every take attempt renewed, so under load a crashed build's slot was never given back.)
- `planner:catalogue-line`: the waiting tickets, scored by ARRIVAL, so rank is place in line.
- `planner:catalogue-seen`: the same tickets scored by LAST RE-ASK. A ticket not seen for `HEARTBEAT` seconds is
  dropped (the hunter left); one `ZRANGEBYSCORE` finds them all, not a round trip per ticket.
- `planner:catalogue-owners`: ticket -> user id, so a ticket only ever speaks for the hunter it was issued to.
- `planner:catalogue-build-seconds` (a string): a moving average of recent successful build times, for the estimate.

ADMISSION: a ticket may build when fewer people are ahead of it than there are free slots, so when two slots free
at once the first two in line both go. A newcomer joins at the back even if a slot is free while others wait.
A ticket is CLAIMED by removing it from the line: only the request whose removal succeeds builds, so parallel
re-asks with one ticket get one build between them, and the rest raise `TicketLost` (the view then charges them
as the newcomers they are).

BEST-EFFORT, NOT TRANSACTIONAL: two workers can race the last free slot; the loser sees its lease rank past
`SLOTS` and backs out, and a rare simultaneous read can let `SLOTS + 1` run briefly. That is the right trade for
a wait measured in seconds. IF REDIS FAILS, the build goes ahead without the line (logged): the line protects
memory under load, and a broken line must never break the planner.
"""
import logging
import time
import uuid
from contextlib import contextmanager

from trophies.util_modules.cache import redis_client

logger = logging.getLogger(__name__)

#: Whole-catalogue builds allowed at once across the site.
SLOTS = 2
HOLDERS_KEY = 'planner:catalogue-holders'
LINE_KEY = 'planner:catalogue-line'
SEEN_KEY = 'planner:catalogue-seen'
OWNERS_KEY = 'planner:catalogue-owners'
AVG_KEY = 'planner:catalogue-build-seconds'

#: A build's lease lapses after this long, freeing the slot of a build that died without giving it back.
LEASE_SECONDS = 120
#: A waiting ticket is dropped after this long without a re-ask. The page re-asks every 2s; this is generous enough
#: for a briefly throttled background tab and short enough that a hunter who left does not block the line long.
HEARTBEAT = 20
#: The estimate before any build has been timed, and how fast the average follows new builds.
DEFAULT_BUILD_SECONDS = 3.0
AVERAGE_WEIGHT = 0.2


class LineWait(Exception):
    """Every slot is taken (or others are ahead): wait in line. Carries what the page shows and re-sends."""

    def __init__(self, ticket, position, eta_seconds):
        super().__init__(f'in line at {position}')
        self.ticket = ticket
        self.position = position
        self.eta_seconds = eta_seconds


class LineBusy(Exception):
    """Every slot is taken and the caller asked not to join the line (a one-off build, not a plan request)."""


class TicketLost(Exception):
    """The ticket the caller vouched for is no longer in line: claimed by a parallel request, or dropped for a
    lapsed heartbeat between the caller's check and this one. Its holder is a newcomer again."""


def _text(value):
    return value.decode() if isinstance(value, bytes) else value


def valid_ticket(ticket, user_id):
    """Is this a ticket still waiting in line, issued to this user? A crafted, stale or borrowed one is not."""
    if not isinstance(ticket, str) or not ticket or len(ticket) > 64 or user_id is None:
        return False
    try:
        owner = redis_client.hget(OWNERS_KEY, ticket)
        return owner is not None and _text(owner) == str(user_id) and redis_client.zscore(LINE_KEY, ticket) is not None
    except Exception:
        logger.warning('build line unavailable; treating ticket as invalid', exc_info=True)
        return False


def leave(ticket, user_id):
    """Take this user's ticket out of line (they got an answer some other way, e.g. a cached plan, or were refused
    before building). Someone else's ticket is left alone."""
    if not valid_ticket(ticket, user_id):
        return
    try:
        _drop(redis_client, [ticket])
    except Exception:
        logger.warning('build line unavailable; could not drop a ticket', exc_info=True)


def has_room():
    """Could a newcomer build right now? For a caller that will not join the line and must not be charged for a
    build that would only be refused. A guess (the line can move before the build asks); fails open."""
    try:
        r = redis_client
        _prune(r, time.time())
        return r.zcard(LINE_KEY) < SLOTS - r.zcard(HOLDERS_KEY)
    except Exception:
        logger.warning('build line unavailable; assuming room', exc_info=True)
        return True


@contextmanager
def slot(ticket=None, user_id=None, join=True):
    """Run a whole-catalogue build in a slot, or raise `LineWait` with the hunter's place in line (`LineBusy` when
    `join` is off). A `ticket` is the CALLER VOUCHING that it is this user's ticket in line: it keeps its place, and
    raises `TicketLost` if it has gone. Without one the caller joins at the back."""
    r = redis_client
    try:
        lease = _admit(r, ticket, user_id, join)
    except (LineWait, LineBusy, TicketLost):
        raise
    except Exception:
        logger.warning('build line unavailable; building without it', exc_info=True)
        yield
        return
    started = time.monotonic()
    try:
        yield
    except BaseException:
        _release(r, lease)
        raise
    _release(r, lease)
    try:
        _record(r, time.monotonic() - started)
    except Exception:
        logger.warning('build line could not record a build time', exc_info=True)


def _admit(r, ticket, user_id, join):
    """A lease id when this caller may build now; otherwise raise. All the line's bookkeeping lives here."""
    now = time.time()
    _prune(r, now)
    mine = ticket or None
    if mine is not None:
        rank = r.zrank(LINE_KEY, mine) if valid_ticket(mine, user_id) else None
        if rank is None:
            raise TicketLost()
        ahead = rank
    else:
        ahead = r.zcard(LINE_KEY)
    free = SLOTS - r.zcard(HOLDERS_KEY)
    if ahead < free:
        lease = _take(r, now)
        if lease is not None:
            if mine is not None:
                _claim(r, lease, mine)
            return lease
    if not join:
        raise LineBusy()
    if mine is None:
        mine = uuid.uuid4().hex
        pipe = r.pipeline()
        pipe.hset(OWNERS_KEY, mine, str(user_id))
        pipe.zadd(SEEN_KEY, {mine: now})
        pipe.zadd(LINE_KEY, {mine: now})
        pipe.execute()
        ahead = r.zrank(LINE_KEY, mine) or 0
    else:
        r.zadd(SEEN_KEY, {mine: now})
    raise LineWait(mine, ahead + 1, _eta(r, ahead))


def _take(r, now):
    """Add a lease and keep it only if it ranks inside `SLOTS` (by start time); else back out. None when full."""
    lease = uuid.uuid4().hex
    r.zadd(HOLDERS_KEY, {lease: now})
    try:
        rank = r.zrank(HOLDERS_KEY, lease)
    except Exception:
        _release(r, lease)   # a lease nobody will build under must not hold a slot until it lapses
        raise
    if rank is None or rank >= SLOTS:
        r.zrem(HOLDERS_KEY, lease)
        return None
    return lease


def _claim(r, lease, ticket):
    """Take the ticket out of line as this request's own, or give the lease back. Removal is the atomic step: of
    several requests holding one ticket, only the one whose ZREM removed it builds."""
    try:
        claimed = r.zrem(LINE_KEY, ticket)
    except Exception:
        _release(r, lease)
        raise
    if not claimed:
        _release(r, lease)
        raise TicketLost()
    try:
        pipe = r.pipeline()
        pipe.zrem(SEEN_KEY, ticket)
        pipe.hdel(OWNERS_KEY, ticket)
        pipe.execute()
    except Exception:   # already out of line, which is what matters; the rest is swept by `_prune`
        logger.warning('build line could not tidy a claimed ticket', exc_info=True)


def _release(r, lease):
    try:
        r.zrem(HOLDERS_KEY, lease)
    except Exception:
        logger.warning('build line could not release a lease (it lapses on its own)', exc_info=True)


def _prune(r, now):
    """Clear lapsed leases and tickets whose hunter stopped re-asking. Range queries, not a call per member."""
    r.zremrangebyscore(HOLDERS_KEY, '-inf', now - LEASE_SECONDS)
    stale = [_text(m) for m in r.zrangebyscore(SEEN_KEY, '-inf', now - HEARTBEAT)]
    if stale:
        _drop(r, stale)


def _drop(r, tickets):
    pipe = r.pipeline()
    pipe.zrem(LINE_KEY, *tickets)
    pipe.zrem(SEEN_KEY, *tickets)
    pipe.hdel(OWNERS_KEY, *tickets)
    pipe.execute()


def _average(r):
    raw = r.get(AVG_KEY)
    try:
        return float(raw) if raw is not None else DEFAULT_BUILD_SECONDS
    except (TypeError, ValueError):
        return DEFAULT_BUILD_SECONDS


def _record(r, seconds):
    """Only SUCCESSFUL builds feed the average: a quick failure would make the next estimate look rosier."""
    r.set(AVG_KEY, '%.3f' % (_average(r) * (1 - AVERAGE_WEIGHT) + seconds * AVERAGE_WEIGHT), ex=86400)


def _eta(r, ahead):
    """Seconds until this hunter's build is done: the builds ahead of them run SLOTS at a time, then theirs."""
    rounds = ahead // SLOTS + 1
    return max(1, round(_average(r) * (rounds + 1)))
