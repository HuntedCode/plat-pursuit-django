"""Thin, severable adapters for the badge subsystem's OUTBOUND couplings.

The sealed badge engine reaches other systems ONLY through these, so those systems (titles, notifications) can
be reworked or archived without opening the engine. Kept deliberately crude. See
docs/design/rebuild/badge-backend-rebuild.md §3.6.
"""
import logging

from trophies.models import UserTitle, UserGroupBadge

logger = logging.getLogger(__name__)

# Distinct from the legacy 'badge' source so the old and new title systems never collide during the interim.
TITLE_SOURCE = 'badge_series'

#: The retiring tier-badge system's source. The ONLY source this system will adopt a row from (see
#: grant_series_title): a 'milestone' row is a one-off award with its own meaning and label, and claiming
#: one would silently reclassify a hunter's award.
LEGACY_TITLE_SOURCE = 'badge'


def grant_series_title(profile_id, series):
    """Grant the series' title (one per series, on ANY group-badge earn in it). Idempotent. Takes profile_id so
    the batch path never has to hold Profile objects.

    UserTitle is unique on (profile, title) WITHOUT source_type, so when a series reuses a legacy Badge's Title
    the row may already exist under another source. get_or_create alone would return it untouched and this
    system would have recorded nothing -- the hunter holds the title, but every count filtered to TITLE_SOURCE
    is blind to them, and `revoke_series_title_if_orphaned` can never clean it up. That shipped: titles read as
    "Be the first" while the viewer was wearing them, and rarity graded off a fraction of the real holders.

    So a LEGACY row is ADOPTED. They hold a badge in this series, which is the whole bar for the title; which
    system first wrote the row is bookkeeping. `earned_at` is left alone -- it is a real date, and this is a
    correction rather than a re-grant.

    Only `LEGACY_TITLE_SOURCE`, never 'milestone': a one-off award carries its own label ("Special award") and
    its own dashboard bucket, so adopting one would reclassify a hunter's award and overwrite the source_id
    that points at what granted it.

    NOTE the consequence, which is deliberate: an adopted row becomes revocable by this system, so if the
    group badge later lapses `revoke_series_title_if_orphaned` deletes it even though the hunter's legacy
    UserBadge may still stand. That follows the cutover decision -- the new system is the authority on who
    holds a series title -- but it IS a behaviour change for legacy holders. See test_badge_apply.

    Series should still be authored with their OWN Title rows (this is a repair, not a licence to share one)."""
    if not series.title_id:
        return
    _, created = UserTitle.objects.get_or_create(
        profile_id=profile_id, title_id=series.title_id,
        defaults={'source_type': TITLE_SOURCE, 'source_id': series.id},
    )
    if not created:
        UserTitle.objects.filter(
            profile_id=profile_id, title_id=series.title_id, source_type=LEGACY_TITLE_SOURCE,
        ).update(source_type=TITLE_SOURCE, source_id=series.id)


def revoke_series_title_if_orphaned(profile_id, series):
    """Remove the series title only when the profile has NO 'earned' group badge left in the series (call AFTER
    the lapse is written, so the lapsed badge no longer counts)."""
    if not series.title_id:
        return
    still_earned = UserGroupBadge.objects.filter(
        profile_id=profile_id, group_badge__series=series,
    ).exists()   # any held row in the series (binary model: a row IS a current hold)
    if not still_earned:
        UserTitle.objects.filter(
            profile_id=profile_id, title_id=series.title_id, source_type=TITLE_SOURCE,
        ).delete()


def emit_badge_earned(profile_id, group_badge_id):
    """The per-badge 'earned' event seam.

    DELIBERATELY still just a log. The ON-SITE notification path is not being ported at cutover: the
    notification inbox is parked pending its own rebuild (`plat_pursuit/urls.py` -- every view in
    `notifications/views.py` is unrouted and `/notifications/` 302s home), so a ported adapter would write
    rows nobody can read. Worse, the legacy consolidation rule is "group by series, keep the highest TIER",
    and this system has no tiers -- it has editions -- so porting means designing a new rule for invisible
    output, against an inbox that is itself being redesigned.

    So this stays the seam. When the notifications rebuild lands it consumes THIS, and the legacy
    `notify_badge_awarded` post_save on UserBadge dies with the rest of the old engine.

    DISCORD is different and is NOT deferred -- it is live and user-visible today. It is announced once per
    run from `announce_badges_earned` below rather than per badge from here, because three editions of one
    series finishing together is one event to a hunter, not three.
    """
    logger.info("badge_earned: profile=%s group_badge=%s", profile_id, group_badge_id)


def announce_badges_earned(profile, group_badges):
    """Announce a run's newly earned group badges to Discord, as ONE consolidated embed.

    Replaces the legacy `send_badge_earned_notification`, which reads tier-shaped `Badge` rows. The shape of
    the message is deliberately the same -- hunters already recognise it -- but a badge is now identified by
    its series AND its edition ("Soulsborne -- Ultra HD"), because in this system those are different badges
    and saying only the series name would make two distinct earns look like a duplicate.

    Never raises: an announcement is the least important thing happening in a sync, and the badges are
    already written by the time this runs.
    """
    from trophies.discord_utils.discord_notifications import send_group_badges_earned_notification

    if not profile or not group_badges:
        return
    try:
        send_group_badges_earned_notification(profile, group_badges)
    except Exception:
        logger.exception("badge announce failed: profile=%s", getattr(profile, 'id', None))
