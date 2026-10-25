"""Celebration moments: recorded when they happen, played once (badge rewards phase 3, owner 2026-10-25).

WHY RECORDED AT THE EARN. The platinum confetti this replaces fired from an inbox poll on whatever page the
hunter opened next, so it went off nowhere near the thing it celebrated (removed in adbffdd3). A `Moment` row
is written where the earn happens instead, and plays only where the hunter chose to look:

  welcome    the ONE summary a hunter's first finished sync gets, in place of the flood it would otherwise
             record (every badge and platinum they arrived with). Written by `start_moments`, which also
             stamps `Profile.moments_since`; nothing is recorded for a hunter before that stamp.
  badge      `badge_apply.apply_changes`, on every award (sync, nightly, rechecks). A re-earn records a new
             one (owner: the ceremony plays every time it is earned), but a badge never waits as two.
  platinum   `psn_api_service`, when sync sees a platinum newly earned within `PLATINUM_RECENCY`, so an old
             platinum synced for the first time is not news. Shovelware included: it is the hunter's own.
  milestone  a platinum-count milestone tier crossed in a sync (`record_milestones`): the reel's last beat.

PLAYING. Badge and welcome moments AUTO-PLAY on the hunter's next My Pursuit page, one at a time, unless they
turned celebrations off (`CustomUser.play_celebrations`). Platinums and milestones never ambush: they wait
behind a pill and play as a reel when tapped. A moment counts as played once it has been ON SCREEN
(`mark_played`), server-side, so it never replays on another device. Unplayed moments older than `MOMENT_TTL`
drop quietly; the badge itself is still on the wall.

COST. `for_request` is the only read on the page path: one seek on the partial index of unplayed rows, memoised
on the request so the strip's pill and the overlay share it, and nothing at all off My Pursuit pages.
"""
import logging
from datetime import timedelta

from django.db import DatabaseError, IntegrityError, transaction
from django.db.models import Exists, OuterRef, Q
from django.utils import timezone

from trophies.models import EarnedTrophy, Moment, Profile, UserGroupBadge

logger = logging.getLogger(__name__)

MOMENT_TTL = timedelta(days=14)
PLATINUM_RECENCY = timedelta(days=2)       # the old notification threshold, against first-sight-of-an-old-plat
QUEUE_CAP = 30                             # a page never builds more than this many at once
PLATINUM_METRIC = 'lifetime_platinums'
AUTO_KINDS = (Moment.KIND_WELCOME, Moment.KIND_BADGE)
REEL_KINDS = (Moment.KIND_PLATINUM, Moment.KIND_MILESTONE)


def _started(profile) -> bool:
    """Moments are recorded only for a hunter with a site account whose first sync has finished."""
    return bool(profile.user_id and profile.moments_since)


# --- recording ----------------------------------------------------------------------------------------------

def record_badge(profile, group_badge, occurred_at=None):
    """A badge was awarded. When one is already waiting for it (the badge flipped off and on) that one is
    REFRESHED rather than a second added: an unplayed moment that had expired would otherwise hold the
    one-pending slot forever and swallow every later earn of the badge.

    NEVER RAISES. This runs inside `apply_changes`' transaction, so an error escaping here would roll back
    every award in the run: a celebration is not worth a badge. Its own savepoint keeps a failure local."""
    if not _started(profile):
        return
    when = occurred_at or timezone.now()
    try:
        with transaction.atomic():
            refreshed = Moment.objects.filter(
                profile=profile, kind=Moment.KIND_BADGE, group_badge=group_badge, played_at__isnull=True,
            ).update(occurred_at=when, created_at=timezone.now())
            if not refreshed:
                Moment.objects.create(profile=profile, kind=Moment.KIND_BADGE, group_badge=group_badge,
                                      occurred_at=when)
    except IntegrityError:
        pass        # a concurrent award wrote it first: it is waiting either way
    except DatabaseError:
        logger.exception("moment: badge %s for profile %s not recorded", group_badge.id, profile.id)


def record_platinum(profile, earned_trophy, earned_at):
    """Sync saw this platinum newly earned. Only recent ones are news; each is celebrated once, ever. Never
    raises, for the same reason as `record_badge`: it runs in the middle of a sync's trophy batch."""
    if not _started(profile) or earned_at is None or earned_at < timezone.now() - PLATINUM_RECENCY:
        return
    try:
        with transaction.atomic():
            # Which platinum this is ("#251"), read NOW from the live denorm the EarnedTrophy signal has just
            # bumped. Counting a hunter's platinums by date at play time would scan a whale's whole history.
            number = Profile.objects.filter(id=profile.id).values_list('total_plats', flat=True).first()
            Moment.objects.create(profile=profile, kind=Moment.KIND_PLATINUM, earned_trophy=earned_trophy,
                                  occurred_at=earned_at, data={'number': number})
    except IntegrityError:
        pass        # `moment_once_per_platinum`
    except DatabaseError:
        logger.exception("moment: platinum %s for profile %s not recorded", earned_trophy.id, profile.id)


def record_milestones(profile, tiers):
    """Milestone tiers newly crossed in a sync; only the platinum-count ones get a beat (the reel's last)."""
    if not _started(profile):
        return
    now = timezone.now()
    Moment.objects.bulk_create([
        Moment(profile=profile, kind=Moment.KIND_MILESTONE, occurred_at=now,
               data={'name': t.name or t.milestone.name, 'threshold': t.threshold})
        for t in tiers if t.milestone.metric == PLATINUM_METRIC
    ])


def start_moments(profile):
    """End of a sync: a hunter's FIRST finished sync records one welcome summary and starts their moments.
    Every later sync is a no-op here. Returns True when it started them."""
    if not profile.user_id or profile.moments_since:
        return False
    now = timezone.now()
    badges = UserGroupBadge.objects.filter(profile=profile).count()
    Moment.objects.create(profile=profile, kind=Moment.KIND_WELCOME, occurred_at=now,
                          data={'badges': badges, 'platinums': profile.total_plats})
    Profile.objects.filter(id=profile.id).update(moments_since=now)
    profile.moments_since = now
    return True


# --- reading ------------------------------------------------------------------------------------------------

def pending(profile):
    """The hunter's playable moments, oldest first: unplayed, not expired, and (for a badge) still held, since
    a badge revoked before its moment played has nothing left to celebrate."""
    held = UserGroupBadge.objects.filter(profile_id=OuterRef('profile_id'), group_badge_id=OuterRef('group_badge_id'))
    return (
        Moment.objects.filter(profile=profile, played_at__isnull=True,
                              created_at__gte=timezone.now() - MOMENT_TTL)
        .filter(~Q(kind=Moment.KIND_BADGE) | Q(Exists(held)))
        .order_by('occurred_at', 'id')[:QUEUE_CAP]
    )


def for_request(request):
    """The waiting moments for THIS request's hunter, read once per request (the strip's pill and the overlay
    both ask). Empty for anyone without a started profile."""
    cached = getattr(request, '_pp_moments', None)
    if cached is not None:
        return cached
    moments = []
    user = getattr(request, 'user', None)
    if user is not None and user.is_authenticated:
        profile = getattr(user, 'profile', None) if hasattr(user, 'profile') else None
        if profile is not None and profile.moments_since:
            # Every relation the badge medallion's art and caption read (GroupBadge.art_layers, badge_frame),
            # so a page with several waiting badges does not pay a query per relation per badge.
            moments = list(pending(profile).select_related(
                'group_badge__platform_group', 'group_badge__series__title', 'group_badge__series__artwork_source',
                'group_badge__series__submitted_by', 'group_badge__series__franchise',
                'group_badge__series__collection', 'group_badge__series__developer',
            ))
    request._pp_moments = moments
    return moments


def mark_played(profile, ids) -> int:
    """These moments have been on screen. Only the hunter's own, only once."""
    return Moment.objects.filter(profile=profile, id__in=ids, played_at__isnull=True).update(played_at=timezone.now())


# --- what each moment shows ---------------------------------------------------------------------------------

def _badge_card(moment, holo, participants):
    from trophies.services.badge_xp import XP_BADGE_COMPLETION_BONUS
    from trophies.services.collection_service import badge_frame

    gb = moment.group_badge
    frame = badge_frame(gb, {gb.id: holo}, {}, participants)
    frame['dom_id'] = f'moment-{moment.id}'
    return {
        'frame': frame, 'series': gb.series, 'edition': gb.platform_group.name,
        'title': gb.series.title.name if gb.series.title_id else None,
        'bonus_xp': XP_BADGE_COMPLETION_BONUS,
    }


def _duration(delta) -> str:
    """Start-to-platinum time in the largest whole unit that fits: "3 hours", "14 days"."""
    hours = int(delta.total_seconds() // 3600)
    if hours < 24:
        n, unit = max(hours, 1), 'hour'
    else:
        n, unit = hours // 24, 'day'
    return f'{n:,} {unit}{"" if n == 1 else "s"}'


def _platinum_cards(profile, moments):
    """The reel's facts per platinum, in bounded reads over the handful waiting: the game, which platinum it
    is (#N, stored when it was recorded), PSN's rarity, start-to-platinum time, and the share card."""
    from core.services.completion_card_service import share_link
    from trophies.models import ProfileGame, ProfileTrophyGroup

    ets = {
        et.id: et for et in EarnedTrophy.objects.filter(id__in=[m.earned_trophy_id for m in moments])
        .select_related('trophy__game__concept__igdb_match').defer('trophy__game__concept__igdb_match__raw_response')
    }
    if not ets:
        return {}
    profile_id = profile.id
    game_ids = {et.trophy.game_id for et in ets.values()}
    progress = dict(ProfileGame.objects.filter(profile_id=profile_id, game_id__in=game_ids).values_list('game_id', 'progress'))
    started = dict(ProfileTrophyGroup.objects.filter(
        profile_id=profile_id, trophy_group__game_id__in=game_ids, trophy_group__trophy_group_id='default',
    ).values_list('trophy_group__game_id', 'first_trophy_at'))
    cards = {}
    for m in moments:
        et = ets.get(m.earned_trophy_id)
        if et is None:
            continue
        game = et.trophy.game
        first = started.get(game.id)
        cards[m.id] = {
            'game': game, 'number': m.data.get('number'), 'earn_rate': et.trophy.trophy_earn_rate,
            'took': _duration(et.earned_date_time - first) if (first and et.earned_date_time and first <= et.earned_date_time) else None,
            'share': share_link(profile, game, progress.get(game.id, 0)),
        }
    return cards


def build_view(profile, moments, autoplay=True):
    """The overlay's data: `auto` (welcome + badges, in order) and `reel` (platinums, then milestones), each
    item {'moment', 'kind', plus what it shows}. Only called when something is waiting."""
    from trophies.services.rarity import community_size

    participants = community_size()
    # The holds behind the waiting badges, in ONE read (each is held: `pending` filtered the rest out).
    holo = dict(UserGroupBadge.objects.filter(
        profile=profile, group_badge_id__in=[m.group_badge_id for m in moments if m.kind == Moment.KIND_BADGE],
    ).values_list('group_badge_id', 'is_holo'))
    auto, plats, milestones = [], [], []
    for m in moments:
        if m.kind == Moment.KIND_WELCOME:
            auto.append({'moment': m, 'kind': m.kind, **m.data})
        elif m.kind == Moment.KIND_BADGE:
            auto.append({'moment': m, 'kind': m.kind, **_badge_card(m, bool(holo.get(m.group_badge_id)), participants)})
        elif m.kind == Moment.KIND_PLATINUM:
            plats.append(m)
        elif m.kind == Moment.KIND_MILESTONE:
            milestones.append({'moment': m, 'kind': m.kind, **m.data})
    cards = _platinum_cards(profile, plats) if plats else {}
    reel = [{'moment': m, 'kind': m.kind, **cards[m.id]} for m in plats if m.id in cards]
    platinum_count = len(reel)
    reel += milestones          # a round number is the reel's last beat
    return {'auto': auto, 'reel': reel, 'autoplay': autoplay, 'platinum_count': platinum_count}
