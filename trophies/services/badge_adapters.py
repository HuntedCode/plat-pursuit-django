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


def grant_series_title(profile_id, series):
    """Grant the series' title (one per series, on ANY group-badge earn in it). Idempotent. Takes profile_id so
    the batch path never has to hold Profile objects.

    INVARIANT: a BadgeSeries.title must be a Title row NOT shared with a legacy Badge. UserTitle is unique on
    (profile, title) without source_type, so if a series reused a legacy 'badge' Title, get_or_create would
    return that legacy row and this system could neither record nor later revoke its own grant. New series must
    use their own Title rows (enforced by authoring / the cutover backfill, not a DB constraint)."""
    if not series.title_id:
        return
    UserTitle.objects.get_or_create(
        profile_id=profile_id, title_id=series.title_id,
        defaults={'source_type': TITLE_SOURCE, 'source_id': series.id},
    )


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
    """The 'badge earned' event seam. Notifications are out of scope for the rebuild; this is the crude
    emit-point a future notification adapter will consume. For now it just logs."""
    logger.info("badge_earned: profile=%s group_badge=%s", profile_id, group_badge_id)
