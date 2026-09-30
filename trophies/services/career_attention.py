"""The three attention markers on the My Pursuit nav item: a claim count, an XP mark, a New pill.

WHY A SEPARATE MODULE. All three answers already exist elsewhere -- `contracts_service.claimable_summary`,
`challenges.services.rewards.redeemable_slots` / `pending_xp`, and `new_contracts_modal.new_for` -- and none
of them can be used here. They are page-cost functions, built for one render of `/career/` or of a challenge
run, and this runs on EVERY page of the site for every signed-in hunter, including the Django admin. So these
are the same three questions asked the cheap way, and the comments below are mostly about what makes them
cheap.

THE BAR IS `whats_new_unread` in plat_pursuit/context_processors.py: zero queries, off values the
request already has. None of the three quite reaches that -- two are a cached query each and the New pill is
a cached site-wide value -- but they get close, and the shape is the same: an attention marker must never be
the reason a page got slower.

WHAT EACH MARKER MEANS, because they are deliberately different kinds of thing:

  CLAIM COUNT   work waiting: rewards this hunter has earned and not taken. A NUMBER, because "how
                many" is answerable without a click and is the whole reason to go.
  XP PILL       Challenge job XP earned and not yet claimed. The same KIND of thing as the count --
                something of theirs, waiting -- but a WORD rather than a number, because a run with
                four paid-up squares is one press of Claim all and not four errands.
  NEW PILL      news: contracts announced since they last looked. A WORD, not a light -- the
                correction the avatar's own New marker already made, and the same reason a count of
                things that are merely new would compete with the count of things that are theirs.

PRECEDENCE when more than one applies (owner, 2026-09-29): COUNT, then the XP PILL, then NEW.
Two kinds of waiting reward, then news. Contracts come first because the count is a QUANTITY -- a
hunter works through them one at a time -- while Challenge XP is one press of Claim all however many
squares are owed, so it cannot be ranked by size against anything. News is last because it is the only
one that is not theirs yet.

All three fit only above 1280px; below that and on the mobile tab bar the first that applies wins,
which is the same exclusivity the pair already had and for the same reason -- there is no room.
`chrome.css` carries the arithmetic.
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

#: Same TTL as the claim count, and for the same reason: the TTL is not the mechanism, it only bounds a
#: MISSED invalidation. Both writers that can change this answer clear the key explicitly.
CHALLENGE_XP_TTL = 300
_CHALLENGE_XP_KEY = 'career:chalxp:%s'


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


def has_unclaimed_challenge_xp(profile):
    """Does this hunter have Job XP waiting to be claimed on a Challenge? Cached per hunter.

    THE THIRD MARKER. It reads "XP", and unlike the count beside it there is no number -- a hunter with four
    squares waiting does not act on them four times in any meaningful sense, they go to the run and press
    Claim all. So the pill says what is waiting, and the count keeps being the only quantity.

    IT ASKED A DIFFERENT QUESTION FOR ABOUT AN HOUR: "claimed and not yet CELEBRATED", back when the
    ceremony was going to be deferred to the next Career visit. The celebration fires on the run page now,
    in the same response as the payout, so nothing is ever uncelebrated -- and the marker points at the
    thing that IS still outstanding, which is the claim itself. That also makes it the same KIND of thing as
    the count: something of yours, waiting.

    ONE `EXISTS` BEHIND A CACHE. `chalslot_unclaimed_xp_idx` is a partial index on `challenge` carrying only
    the squares in flight, so the rows that can answer yes are a handful however long a hunter's claimed
    history is -- their paid slots are not in it at all. Whether the planner reaches for it depends on it
    reaching the hunter's runs first (the predicate here is `challenge__profile`, not a slot id), and that has
    NOT been confirmed with `EXPLAIN` against prod-shaped data. The claim is about what the index makes
    possible, not about a plan anybody has read.

    THE BAR FOR THIS MODULE, restated because it applies hardest to the newest marker: this runs on EVERY
    page for every signed-in hunter, including the Django admin. An attention marker must never be the
    reason a page got slower.

    THE CATALOGUE CAN STILL MAKE IT STALE, both ways, and it is worth naming because the shared predicate is
    otherwise described as making the pill unable to lie. Deleting a `Job` un-owes its squares; creating one
    back under an existing completed unpaid square re-owes them. Neither goes through either writer, so this
    key is not cleared and the pill is wrong for up to the TTL in whichever direction.

    NO CLAIM RESCUES THE FIRST CASE, which an earlier version of this paragraph said it did ("the redeem reply
    computes a fresh answer, so a hunter who claims sees the pill go"). If the deleted job's square was the
    only one owed, `summary` reports nothing claimable and the panel renders no Claim button at all -- there is
    no door to press, and the TTL is the only way out. Bounded at five minutes and staff-triggered, so it is
    left as it is rather than wired to a `Job` signal for something that happens approximately never.
    """
    if profile is None:
        return False
    key = _CHALLENGE_XP_KEY % profile.pk
    hit = cache.get(key)
    if hit is not None:
        # THE BOOL IS STORED AS ITSELF, and the guard is `is not None` rather than truthiness. This shipped
        # storing an int, justified by "`cache.get` cannot tell a stored `False` from a miss" -- which is
        # simply not true of this cache: `False` round-trips and `is not None` separates it from a miss
        # (checked against the configured backend). The int also would not have helped if it were true,
        # because `0` is falsy exactly like `False`. What the real hazard would be is a future reader
        # changing this line to `if cache.get(key):`, and no stored type survives that.
        #
        # The sibling `latest_announced_at` storing `''` for None is a GENUINELY different problem: there
        # `None` IS the value being cached, so it collides with the miss sentinel.
        return hit

    # ITS OWN GUARD, because this is the only marker that reaches into ANOTHER APP's models. Without it a
    # DatabaseError in `challenges` propagates to the context processor's blanket handler, which returns an
    # empty dict -- so a hunter would lose the claim COUNT and the New pill too, over a marker that is third
    # in precedence. Each marker failing alone is the shape this module's siblings already have.
    try:
        from challenges.services.rewards import has_unclaimed_xp

        pending = has_unclaimed_xp(profile)
    except Exception:
        logger.debug('Could not resolve the challenge-XP marker', exc_info=True)
        return False
    cache.set(key, pending, CHALLENGE_XP_TTL)
    return pending


def forget_challenge_xp(profile):
    """Drop the cached marker. Called by BOTH writers that can change the answer -- a square completing
    (which arms it) and a claim (which spends it).

    BOTH HALVES, because this module's own comment on the claim count records what happens with only one:
    it shipped invalidating the spending side alone, so every page render re-cached a zero with a fresh TTL
    moments before the reward landed, and the badge was dark for most of its TTL at exactly the moment it
    had something to say."""
    if profile is not None:
        cache.delete(_CHALLENGE_XP_KEY % profile.pk)


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
    """(count, dot, unclaimed_xp) forced on for a team preview, or None on an ordinary request.

    A THREE-TUPLE SINCE THE THIRD MARKER ARRIVED, and it joined this door rather than getting one of its own
    because the thing being looked at is the ROW: how three lozenges sit beside a label, which yields to which
    below 1280px. A door that lit two of three would leave the widest case -- the one the CSS is written for
    -- with no way to see it. (Two of three is a perfectly ordinary hunter state; an earlier version of this
    paragraph claimed otherwise.)

    THE MARKERS ONLY APPEAR WHEN THERE IS SOMETHING TO SAY, which makes them the hardest thing on the
    site to look at deliberately: you need an unclaimed reward and an unseen announcement at the same
    moment. This is the door for looking at them anyway.

        ?preview=career-markers          both markers, using the real claim count (3 if there is none)
        ?preview=career-markers&n=12     force the count, e.g. to see the 9+ cap
        ?preview=career-markers&n=0      the New pill and the XP mark, no count

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
        return None, True, True     # the caller substitutes the real count
    try:
        return max(0, min(int(raw), 999)), True, True
    except (TypeError, ValueError):
        return None, True, True


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
