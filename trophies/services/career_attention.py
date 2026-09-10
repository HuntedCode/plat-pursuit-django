"""The two attention markers on the My Pursuit nav item: a claim count, and a new-contracts dot.

WHY A SEPARATE MODULE. Both answers already exist elsewhere -- `contracts_service.claimable_summary`
and `new_contracts_modal.new_for` -- and neither can be used here. They are page-cost functions,
built for one render of `/career/`, and this runs on EVERY page of the site for every signed-in
hunter, including the Django admin. So these are the same two questions asked the cheap way, and the
comments below are mostly about what makes them cheap.

THE BAR IS `whats_new_unread` in plat_pursuit/context_processors.py: zero queries, off values the
request already has. Neither of these quite reaches that, but they get close, and the shape is the
same -- an attention marker must never be the reason a page got slower.

WHAT EACH MARKER MEANS, because they are deliberately different kinds of thing:

  CLAIM COUNT   work waiting: rewards this hunter has earned and not taken. A NUMBER, because "how
                many" is answerable without a click and is the whole reason to go.
  NEW PILL      news: contracts announced since they last looked. A WORD, not a light -- the
                correction the avatar's own New marker already made, and the same reason a count of
                things that are merely new would compete with the count of things that are theirs.
"""
import logging

from django.core.cache import cache
from django.db.models import Exists, OuterRef, Q
from django.utils import timezone

logger = logging.getLogger(__name__)

#: Per-hunter claim count. Both writers invalidate explicitly -- `contract_service.claim` spends a
#: reward, `mark_contract_reached` creates one -- so this TTL only bounds a MISSED invalidation.
#:
#: The second of those was missing at first and the comment here claimed it anyway. That is the worse
#: half to lose: the hunter is on the site WHILE the sync runs, so every page render re-arms this key
#: with a fresh 300s at a zero count, moments before the reward lands. The badge would then be absent
#: for close to the full TTL at exactly the moment it had something to say, while /career/'s own rail
#: -- uncached -- already showed the reward. Two surfaces on one page disagreeing.
CLAIMABLE_TTL = 300

#: The newest announcement site-wide. One value for everybody, so a long TTL costs one query per
#: interval for the entire site; `announce_contracts` clears it the moment it posts.
LATEST_TTL = 900

_CLAIMABLE_KEY = 'career:claimable:%s'
_LATEST_KEY = 'contracts:latest_announced'


def claimable_count(profile):
    """How many contracts this hunter can claim right now, cached.

    ANSWERED FROM `EarnedContract`, NOT THE CATALOGUE. `contracts_service` derives the same status by
    annotating every live contract with four correlated subqueries against this hunter's rows and
    filtering the result -- correct, and the right shape for a board that also needs progress,
    ranking and XP. Here the question is only "how many", and all four stamps that decide it live on
    the hunter's own `EarnedContract` rows, of which there are a handful. One indexed query over
    their rows instead of a scan of the catalogue.

    The definition is copied deliberately rather than shared: `is_live`, `has_jobs` and the
    reached-but-not-accepted pair are the board's rule (`annotated_contracts`), and the badge must
    show the same number the board does or it is lying. A test pins the two together.
    """
    if profile is None:
        return 0
    key = _CLAIMABLE_KEY % profile.pk
    hit = cache.get(key)
    if hit is not None:
        return hit

    from trophies.models import Contract, EarnedContract

    has_jobs = Exists(Contract.jobs.through.objects.filter(contract_id=OuterRef('contract_id')))
    count = (EarnedContract.objects
             .filter(profile=profile, contract__is_live=True)
             .filter(Q(platinum_reached_at__isnull=False, platinum_accepted_at__isnull=True)
                     | Q(full_reached_at__isnull=False, full_accepted_at__isnull=True))
             .annotate(has_jobs=has_jobs).filter(has_jobs=True)
             .count())
    cache.set(key, count, CLAIMABLE_TTL)
    return count


def forget_claimable(profile):
    """Drop the cached count. Called from the claim path, which is the only thing that can spend one
    -- a claim that left the badge standing for five minutes would look broken in the one moment the
    hunter is looking straight at it."""
    if profile is not None:
        cache.delete(_CLAIMABLE_KEY % profile.pk)


def latest_announced_at():
    """The newest `announced_at` on the board, cached SITE-WIDE.

    This is the trick that makes the dot free. "Is anything new to this hunter" is a comparison
    between a per-user marker and a global maximum -- and the marker already rides `request.user`,
    loaded by authentication. So the only fetch is one value shared by every visitor, and the
    per-user half costs nothing at all.

    `None` is cached as a sentinel string, because `cache.get` cannot tell a stored None from a miss
    and a board with nothing announced would otherwise re-query on every render of every page.
    """
    hit = cache.get(_LATEST_KEY)
    if hit is not None:
        return None if hit == '' else _parse(hit)

    from trophies.models import Contract

    # `has_jobs` IS PART OF THE GATE, and leaving it out was unbounded rather than merely stale.
    # The modal filters `is_live` AND `has_jobs` (`annotated_contracts`); this filtered only the
    # first. So a newest announced contract with no jobs -- a curator stripping the M2M, or a jobless
    # row reaching `mark_announced` -- lit the pill for everyone whose marker was older, rendered no
    # modal, and therefore never advanced anyone's marker. Permanent, for every hunter, with nothing
    # able to clear it. `is_live` alone is TTL-bounded because both queries filter it; this was not.
    has_jobs = Exists(Contract.jobs.through.objects.filter(contract_id=OuterRef('pk')))
    newest = (Contract.objects
              .filter(is_live=True, announcement_posted=True, announced_at__isnull=False)
              .annotate(has_jobs=has_jobs).filter(has_jobs=True)
              .order_by('-announced_at')
              .values_list('announced_at', flat=True)
              .first())
    cache.set(_LATEST_KEY, newest.isoformat() if newest else '', LATEST_TTL)
    return newest


def forget_latest_announced():
    """Called by `announce_contracts` after a post. Without it the dot would take up to LATEST_TTL to
    appear, which is a strange way to treat the one event the whole feature is built around."""
    cache.delete(_LATEST_KEY)


def has_new_contracts(user):
    """Whether this hunter has an unseen announcement. ZERO QUERIES beyond the shared value above.

    The comparison is the modal's, exactly: newer than their marker, or -- when they have no marker
    -- inside the same `NEW_CONTRACT_WINDOW_DAYS` floor a first visit uses. The dot and the modal
    must agree, or a hunter clicks a dot and meets nothing.
    """
    from trophies.services import new_contracts_modal
    from trophies.util_modules.constants import NEW_CONTRACT_WINDOW_DAYS

    newest = latest_announced_at()
    if newest is None:
        return False
    marker = new_contracts_modal.seen_marker(user)
    if marker is None:
        return newest >= timezone.now() - timezone.timedelta(days=NEW_CONTRACT_WINDOW_DAYS)
    return newest > marker


#: `?preview=career-markers` -- see `preview_counts`.
PREVIEW = 'career-markers'


def preview_counts(request):
    """(count, dot) forced on for a team preview, or None when this is an ordinary request.

    THE MARKERS ONLY APPEAR WHEN THERE IS SOMETHING TO SAY, which makes them the hardest thing on the
    site to look at deliberately: you need an unclaimed reward and an unseen announcement at the same
    moment. This is the door for looking at them anyway.

        ?preview=career-markers          both markers, using the real claim count (3 if there is none)
        ?preview=career-markers&n=12     force the count, e.g. to see the 9+ cap
        ?preview=career-markers&n=0      the New pill alone

    An `n` that is not a number is ignored rather than refused, and you get the real count -- which
    can read as a forced one. Deliberate: this is a viewing tool on a live page, and a 400 from a
    context processor would take the whole page down over a typo in a querystring.

    Staff-gated and writes nothing, like every other preview door -- see `core.previews`.
    """
    from core.previews import previewing

    if not previewing(request, PREVIEW):
        return None
    raw = request.GET.get('n')
    if raw is None:
        return None, True     # the caller substitutes the real count
    try:
        return max(0, min(int(raw), 999)), True
    except (TypeError, ValueError):
        return None, True


def _parse(raw):
    """An ISO string from the cache, or None.

    Guards the TYPE as well as the value. `parse_datetime` raises ValueError on a well-formed
    impossible date and TypeError on a non-string, and this caught only the first -- so anything that
    decoded to a non-string non-empty (a hand-SET key, a value from an older code version) raised
    straight through the service. `new_contracts_modal.seen_marker` already guards both; two
    functions written for the same hazard should not handle it two different ways.
    """
    from django.utils.dateparse import parse_datetime

    if not isinstance(raw, str):
        return None
    try:
        return parse_datetime(raw)
    except ValueError:
        return None
