"""Celebration moments: recorded when they happen, played once (badge rewards phase 3, owner 2026-10-25).

WHY RECORDED AT THE EARN. The platinum confetti this replaces fired from an inbox poll on whatever page the
hunter opened next, so it went off nowhere near the thing it celebrated (removed in adbffdd3). A `Moment` row
is written where the earn happens instead, and plays only where the hunter chose to look:

  welcome    the ONE summary a hunter's first finished sync gets, in place of the flood it would otherwise
             record (every badge and platinum they arrived with). Written by `start_moments`, which also
             stamps `Profile.moments_since`; nothing is recorded for a hunter before that stamp.
  badge      `badge_apply.apply_changes`, on every award (sync, nightly, rechecks). A re-earn records a new
             one (owner: the ceremony plays every time it is earned), but a badge never waits as two.
  mastered   `badge_apply`, when a held badge becomes MASTERED, and `full_set` / `series_master` when a
             series reward is gained (phase 4). Like a badge, every time it is earned, but only when the
             thing was finished within `REWARD_RECENCY`: a veteran whose old mastery is noticed for the first
             time (the deploy, a data fix) gets the medallion, not a ceremony for something done years ago.
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
from datetime import datetime, time, timedelta

from django.db import DatabaseError, IntegrityError, transaction
from django.db.models import Count, Exists, OuterRef, Q
from django.utils import timezone

from trophies.models import EarnedTrophy, Moment, Profile, SeriesAward, UserGroupBadge

logger = logging.getLogger(__name__)

MOMENT_TTL = timedelta(days=14)
PLATINUM_RECENCY = timedelta(days=2)       # the old notification threshold, against first-sight-of-an-old-plat
REWARD_RECENCY = MOMENT_TTL                # a reward finished longer ago than a moment lives is not news
QUEUE_CAP = 30                             # a page never builds more than this many at once
PREVIEW = 'moments'                        # `?preview=moments` (core.previews): the team's look at the player
PLATINUM_METRIC = 'lifetime_platinums'
AUTO_KINDS = (Moment.KIND_WELCOME, Moment.KIND_BADGE, Moment.KIND_MASTERED, Moment.KIND_FULL_SET,
              Moment.KIND_SERIES_MASTER)
SERIES_KINDS = (Moment.KIND_FULL_SET, Moment.KIND_SERIES_MASTER)
PICKUP_KINDS = (Moment.KIND_BADGE, Moment.KIND_MASTERED) + SERIES_KINDS   # a medallion you can pick up
REEL_KINDS = (Moment.KIND_PLATINUM, Moment.KIND_MILESTONE)


def _started(profile) -> bool:
    """Moments are recorded only for a hunter with a site account whose first sync has finished."""
    return bool(profile.user_id and profile.moments_since)


# --- recording ----------------------------------------------------------------------------------------------

def _record_waiting(profile, kind, occurred_at, **subject):
    """Record a moment that waits as ONE per subject (a badge, a mastery, a series reward). When one is already
    waiting (the thing flipped off and on) that one is REFRESHED rather than a second added: an unplayed moment
    that had expired would otherwise hold the one-pending slot forever and swallow every later earn.

    NEVER RAISES. This runs inside `apply_changes`' transaction, so an error escaping here would roll back
    every award in the run: a celebration is not worth a badge. Its own savepoint keeps a failure local."""
    if not _started(profile):
        return
    when = occurred_at or timezone.now()
    try:
        with transaction.atomic():
            refreshed = Moment.objects.filter(
                profile=profile, kind=kind, played_at__isnull=True, **subject,
            ).update(occurred_at=when, created_at=timezone.now())
            if not refreshed:
                Moment.objects.create(profile=profile, kind=kind, occurred_at=when, **subject)
    except IntegrityError:
        pass        # a concurrent award wrote it first: it is waiting either way
    except DatabaseError:
        logger.exception("moment: %s %s for profile %s not recorded", kind, subject, profile.id)


def record_badge(profile, group_badge, occurred_at=None):
    """A badge was awarded: celebrated every time, however it was noticed."""
    _record_waiting(profile, Moment.KIND_BADGE, occurred_at, group_badge=group_badge)


def is_recent_reward(finished_at) -> bool:
    """Whether a reward finished at `finished_at` is news. Undated is not: it is the deploy's veteran case
    (an old completion PSN never dated), and a ceremony for it would be a guess."""
    if finished_at is None:
        return False
    if not isinstance(finished_at, datetime):     # a bare date: its midnight, so the comparison cannot raise
        finished_at = timezone.make_aware(datetime.combine(finished_at, time.min))
    return finished_at >= timezone.now() - REWARD_RECENCY


def record_mastered(profile, group_badge, finished_at):
    """A held badge became mastered. Celebrated only when it was finished recently (`is_recent_reward`)."""
    if is_recent_reward(finished_at):
        _record_waiting(profile, Moment.KIND_MASTERED, finished_at, group_badge=group_badge)


def record_series_reward(profile, series, kind, finished_at):
    """A series' Full Set or Series Master was gained (`kind` is the Moment kind). Recent only, as mastery."""
    if is_recent_reward(finished_at):
        _record_waiting(profile, kind, finished_at, series=series)


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
    # The same for a reward: a mastery or a Full Set lost before it played has nothing left to celebrate.
    awarded = SeriesAward.objects.filter(profile_id=OuterRef('profile_id'), series_id=OuterRef('series_id'),
                                         kind=OuterRef('kind'))
    return (
        Moment.objects.filter(profile=profile, played_at__isnull=True,
                              created_at__gte=timezone.now() - MOMENT_TTL)
        .filter(~Q(kind=Moment.KIND_BADGE) | Q(Exists(held)))
        .filter(~Q(kind=Moment.KIND_MASTERED) | Q(Exists(held.filter(mastered_at__isnull=False))))
        .filter(~Q(kind__in=SERIES_KINDS) | Q(Exists(awarded)))
        .order_by('occurred_at', 'id')[:QUEUE_CAP]
    )


def is_preview(request) -> bool:
    """`?preview=moments` on a My Pursuit page, staff or moderator only: a sample queue from the viewer's own
    data, written nowhere and never marked played, so it replays on every reload (core/previews.py)."""
    from core.previews import previewing

    return previewing(request, PREVIEW)


def preview_moments(profile):
    """UNSAVED moments for the preview: the welcome, the viewer's latest badge (or any live one), their two
    latest platinums and a platinum milestone. Negative ids, so nothing could ever mark a real row played."""
    from trophies.models import GroupBadge

    now = timezone.now()
    moments = [Moment(id=-1, profile=profile, kind=Moment.KIND_WELCOME, occurred_at=now, data={
        'badges': UserGroupBadge.objects.filter(profile=profile).count(), 'platinums': profile.total_plats,
    })]
    hold = UserGroupBadge.objects.filter(profile=profile).select_related('group_badge').order_by('-earned_at').first()
    badge = hold.group_badge if hold else GroupBadge.objects.filter(is_live=True).order_by('id').first()
    if badge:
        moments.append(Moment(id=-2, profile=profile, kind=Moment.KIND_BADGE, group_badge=badge, occurred_at=now))
        moments.append(Moment(id=-6, profile=profile, kind=Moment.KIND_MASTERED, group_badge=badge, occurred_at=now))
    # A series with 2+ live editions for the set beats: the viewer's latest badge's, else any, else (a catalogue
    # with no such series, like a dev database) the latest badge's own series, so the beats can still be seen.
    pair = (GroupBadge.objects.filter(is_live=True).values('series_id').annotate(n=Count('id')).filter(n__gte=2))
    series_id = badge.series_id if badge and pair.filter(series_id=badge.series_id).exists() else \
        pair.order_by('series_id').values_list('series_id', flat=True).first() or (badge.series_id if badge else None)
    if series_id:
        moments.append(Moment(id=-7, profile=profile, kind=Moment.KIND_FULL_SET, series_id=series_id, occurred_at=now))
        moments.append(Moment(id=-8, profile=profile, kind=Moment.KIND_SERIES_MASTER, series_id=series_id,
                              occurred_at=now))
    plats = EarnedTrophy.objects.filter(profile=profile, earned=True, trophy__trophy_type='platinum') \
        .order_by('-earned_date_time')[:2]
    for i, et in enumerate(plats):
        moments.append(Moment(id=-3 - i, profile=profile, kind=Moment.KIND_PLATINUM, earned_trophy=et,
                              occurred_at=et.earned_date_time or now, data={'number': profile.total_plats - i}))
    threshold = max(10, (profile.total_plats // 50) * 50)
    moments.append(Moment(id=-9, profile=profile, kind=Moment.KIND_MILESTONE, occurred_at=now,
                          data={'name': 'Preview', 'threshold': threshold}))
    return moments


def for_request(request):
    """The waiting moments for THIS request's hunter, read once per request (the strip's pill and the overlay
    both ask). Empty for anyone without a started profile. The team's `?preview=moments` gets a sample."""
    cached = getattr(request, '_pp_moments', None)
    if cached is not None:
        return cached
    moments = []
    user = getattr(request, 'user', None)
    if user is not None and user.is_authenticated:
        profile = getattr(user, 'profile', None) if hasattr(user, 'profile') else None
        if profile is not None and is_preview(request):
            moments = preview_moments(profile)
        elif profile is not None and profile.moments_since:
            # Every relation the badge medallion's art and caption read (GroupBadge.art_layers, badge_frame),
            # so a page with several waiting badges does not pay a query per relation per badge.
            moments = list(pending(profile).select_related(
                'group_badge__platform_group', 'group_badge__series__title', 'group_badge__series__artwork_source',
                'group_badge__series__submitted_by', 'group_badge__series__franchise',
                'group_badge__series__collection', 'group_badge__series__developer', 'series',
            ))
    request._pp_moments = moments
    return moments


def mark_played(profile, ids) -> int:
    """These moments have been on screen. Only the hunter's own, only once."""
    return Moment.objects.filter(profile=profile, id__in=ids, played_at__isnull=True).update(played_at=timezone.now())


# --- what each moment shows ---------------------------------------------------------------------------------

def _badge_card(moment, holo, mastered, participants):
    from trophies.services.badge_xp import XP_BADGE_COMPLETION_BONUS
    from trophies.services.collection_service import badge_frame

    gb = moment.group_badge
    frame = badge_frame(gb, {gb.id: holo}, {}, participants, {gb.id} if mastered else frozenset())
    frame['dom_id'] = f'moment-{moment.id}'
    return {
        'frame': frame, 'series': gb.series, 'edition': gb.platform_group.name,
        'title': gb.series.title.name if gb.series.title_id else None,
        'bonus_xp': XP_BADGE_COMPLETION_BONUS,
    }


def _set_card(moment, editions, holds, participants):
    """A Full Set / Series Master moment: every edition's medallion side by side, each wearing what the hunter
    holds. A Series Master's are all at their top finish, which is a crown only where the edition offers
    Mastery (badge_engine): elsewhere it is holo."""
    from trophies.services.collection_service import badge_frame

    master = moment.kind == Moment.KIND_SERIES_MASTER
    # The editions the reward is MADE of: the held ones. An edition that cannot be earned is not part of it and
    # is not drawn as if it were. None held is the team's preview, drawn at the reward's top finish.
    shown = [gb for gb in editions if gb.id in holds] or editions
    frames = []
    for gb in shown:
        holo, mastered = holds.get(gb.id, (master, master))
        frame = badge_frame(gb, {gb.id: holo}, {}, participants, {gb.id} if mastered else frozenset())
        frame['dom_id'] = f'moment-{moment.id}-{gb.id}'
        frames.append(frame)
    names = [gb.platform_group.name for gb in shown]
    label = names[0] if len(names) == 1 else ', '.join(names[:-1]) + ' and ' + names[-1]
    return {'series': moment.series, 'frames': frames, 'editions_label': label}


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

    from trophies.models import GroupBadge
    from trophies.services.badge_leaderboards import masters_counts   # (local: badge_leaderboards imports models)

    participants = community_size()
    # The series' live editions for the set beats, then the holds behind EVERY medallion on screen in ONE read
    # (each badge is held: `pending` filtered the rest out).
    series_ids = {m.series_id for m in moments if m.kind in SERIES_KINDS}
    editions = {}
    if series_ids:
        for gb in (GroupBadge.objects.filter(series_id__in=series_ids, is_live=True)
                   .select_related('platform_group', 'series', 'series__artwork_source', 'series__submitted_by',
                                   'series__franchise', 'series__collection', 'series__developer')
                   .order_by('platform_group__sort_order', 'id')):
            editions.setdefault(gb.series_id, []).append(gb)
    badge_ids = {m.group_badge_id for m in moments if m.group_badge_id} | {
        gb.id for gbs in editions.values() for gb in gbs}
    holds = {gid: (holo, at is not None) for gid, holo, at in UserGroupBadge.objects.filter(
        profile=profile, group_badge_id__in=badge_ids,
    ).values_list('group_badge_id', 'is_holo', 'mastered_at')}
    mastered_ids = [m.group_badge_id for m in moments if m.kind == Moment.KIND_MASTERED]
    # The count is of VERIFIED masters (the boards' population), so "One of N" is said only to one of them.
    masters = masters_counts(mastered_ids) if mastered_ids and profile.is_linked else {}
    auto, plats, milestones = [], [], []
    for m in moments:
        if m.kind == Moment.KIND_WELCOME:
            auto.append({'moment': m, 'kind': m.kind, **m.data})
        elif m.kind == Moment.KIND_BADGE:
            holo, mastered = holds.get(m.group_badge_id, (False, False))
            auto.append({'moment': m, 'kind': m.kind, **_badge_card(m, holo, mastered, participants)})
        elif m.kind == Moment.KIND_MASTERED:
            # Mastered by definition (`pending` dropped any lost since), holo with it.
            auto.append({'moment': m, 'kind': m.kind, 'masters': masters.get(m.group_badge_id, 0),
                         **_badge_card(m, True, True, participants)})
        elif m.kind in SERIES_KINDS:
            if editions.get(m.series_id):
                auto.append({'moment': m, 'kind': m.kind,
                             **_set_card(m, editions[m.series_id], holds, participants)})
        elif m.kind == Moment.KIND_PLATINUM:
            plats.append(m)
        elif m.kind == Moment.KIND_MILESTONE:
            milestones.append({'moment': m, 'kind': m.kind, **m.data})
    cards = _platinum_cards(profile, plats) if plats else {}
    reel = [{'moment': m, 'kind': m.kind, **cards[m.id]} for m in plats if m.id in cards]
    platinum_count = len(reel)
    reel += milestones          # a round number is the reel's last beat
    # The inspector a badge's medallion is picked up into: the hunter's own state of the badge (0 = the id slot).
    from django.urls import reverse
    peek_url = reverse('group_badge_progress_peek', args=[profile.psn_username, 0]) if any(
        a['kind'] in PICKUP_KINDS for a in auto) else None
    return {'auto': auto, 'reel': reel, 'autoplay': autoplay, 'platinum_count': platinum_count, 'peek_url': peek_url}
