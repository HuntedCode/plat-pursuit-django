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
  NEW DOT       news: contracts announced since they last looked. A DOT, because a count of things
                that are merely new would compete with the count of things that are theirs.
"""
import logging

from django.core.cache import cache
from django.db.models import Exists, OuterRef, Q
from django.utils import timezone

logger = logging.getLogger(__name__)

#: Per-hunter claim count. Short, because the source of truth changes on a sync or a claim and both
#: of those invalidate explicitly -- this TTL only bounds how long a MISSED invalidation can lie.
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

    newest = (Contract.objects
              .filter(is_live=True, announcement_posted=True, announced_at__isnull=False)
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


def _parse(raw):
    from django.utils.dateparse import parse_datetime

    try:
        return parse_datetime(raw)
    except ValueError:
        return None
