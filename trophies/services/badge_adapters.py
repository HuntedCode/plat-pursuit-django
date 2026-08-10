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

    UserTitle is unique on (profile, title) WITHOUT source_type, so when a series reuses a legacy Badge's Title
    the row may already exist under another source. get_or_create alone would return it untouched and this
    system would have recorded nothing -- the hunter holds the title, but every count filtered to TITLE_SOURCE
    is blind to them, and `revoke_series_title_if_orphaned` can never clean it up. That shipped: titles read as
    "Be the first" while the viewer was wearing them, and rarity graded off a fraction of the real holders.

    So an existing row is ADOPTED. They hold a badge in this series, which is the whole bar for the title;
    which system first wrote the row is bookkeeping. `earned_at` is left alone -- it is a real date, and this
    is a correction rather than a re-grant.

    Series should still be authored with their OWN Title rows (this is a repair, not a licence to share one)."""
    if not series.title_id:
        return
    _, created = UserTitle.objects.get_or_create(
        profile_id=profile_id, title_id=series.title_id,
        defaults={'source_type': TITLE_SOURCE, 'source_id': series.id},
    )
    if not created:
        UserTitle.objects.filter(
            profile_id=profile_id, title_id=series.title_id,
        ).exclude(source_type=TITLE_SOURCE).update(source_type=TITLE_SOURCE, source_id=series.id)


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
