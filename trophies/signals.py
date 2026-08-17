import logging
from django.db.models.signals import post_save, post_delete, m2m_changed, pre_save, pre_delete
from django.dispatch import receiver
from django.db.models import F
from trophies.models import (
    Stage, ConceptBundle, Profile, EarnedTrophy, ProfileGame,
    GroupBadge, UserGroupBadge,
)

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────
# Incremental counter maintenance for Trophy.earned_count and
# Game.played_count. The daily `recalc_earn_rates` cron is the source of
# truth and reconciles drift; these signals keep the counters live in
# steady state so users see accurate values between cron runs.
#
# Notes:
# - Trophy.earn_rate (= earned_count / played_count) is NOT updated here.
#   It's a derived value, refreshed by the daily cron. Up-to-24h-stale
#   percentages are acceptable; updating it incrementally would require
#   touching every trophy in a game when its played_count moves.
# - Sync paths stamp `_previous_earned` on the EarnedTrophy instance
#   before calling .save() so the post_save handler can detect transitions
#   without a SELECT (the existing pre_save handler that tracks this is
#   suppressed during sync for performance — see trophies/sync_utils.py).
# - bulk_create / bulk_update do not fire these signals (Django default).
#   The cron compensates for any rows touched that way.
# ──────────────────────────────────────────────────────────────────────


def _resolve_previous_earned(instance):
    """Read the previous earned state of an EarnedTrophy from the instance.

    Prefers `_sync_previous_earned` (stamped by sync code in
    psn_api_service.create_or_update_earned_trophy_from_trophy_data) over
    `_previous_earned` (set by the notifications app's pre_save handler).

    The two names are intentionally distinct: during sync, the pre_save
    handler overwrites `_previous_earned` to None so the post_save
    notification handler doesn't double-fire (sync queues notifications
    via DeferredNotificationService instead). That clobber breaks our
    counter-maintenance signals if they read `_previous_earned`, hence
    the sync-specific stamp.
    """
    if hasattr(instance, '_sync_previous_earned'):
        return instance._sync_previous_earned
    return getattr(instance, '_previous_earned', None)


@receiver(post_save, sender=EarnedTrophy, dispatch_uid="update_trophy_earned_count_on_save")
def update_trophy_earned_count_on_save(sender, instance, created, **kwargs):
    """Increment/decrement Trophy.earned_count when an EarnedTrophy flips."""
    from trophies.models import Trophy

    if created:
        if instance.earned:
            Trophy.objects.filter(pk=instance.trophy_id).update(
                earned_count=F('earned_count') + 1
            )
        return

    prev = _resolve_previous_earned(instance)
    # No previous-state hint means we cannot tell what changed. Daily cron
    # reconciliation will fix any drift; skipping here is safer than guessing.
    if prev is None:
        return

    if prev is False and instance.earned is True:
        Trophy.objects.filter(pk=instance.trophy_id).update(
            earned_count=F('earned_count') + 1
        )
    elif prev is True and instance.earned is False:
        Trophy.objects.filter(pk=instance.trophy_id, earned_count__gt=0).update(
            earned_count=F('earned_count') - 1
        )


@receiver(post_delete, sender=EarnedTrophy, dispatch_uid="update_trophy_earned_count_on_delete")
def update_trophy_earned_count_on_delete(sender, instance, **kwargs):
    """Decrement Trophy.earned_count when an earned row is removed."""
    if instance.earned:
        from trophies.models import Trophy
        Trophy.objects.filter(pk=instance.trophy_id, earned_count__gt=0).update(
            earned_count=F('earned_count') - 1
        )


@receiver(post_save, sender=ProfileGame, dispatch_uid="update_game_played_count_on_save")
def update_game_played_count_on_save(sender, instance, created, **kwargs):
    """Increment Game.played_count when a profile picks up a new game."""
    if created:
        from trophies.models import Game
        Game.objects.filter(pk=instance.game_id).update(
            played_count=F('played_count') + 1
        )


@receiver(post_delete, sender=ProfileGame, dispatch_uid="update_game_played_count_on_delete")
def update_game_played_count_on_delete(sender, instance, **kwargs):
    """Decrement Game.played_count when a profile-game link is removed."""
    from trophies.models import Game
    Game.objects.filter(pk=instance.game_id, played_count__gt=0).update(
        played_count=F('played_count') - 1
    )


# ──────────────────────────────────────────────────────────────────────
# Incremental Profile.total_<type> counter maintenance.
#
# Replaces the EarnedTrophy aggregate query that used to live in
# profile_stats_service.update_profile_trophy_counts (lines 77-82). That
# query was the same fan-out shape as the trophy.earned_count recompute
# we extracted in step 1: per-profile-scoped, but tens of thousands of
# rows for active hunters, multiplied by N concurrent sync_completes.
#
# Profile.total_trophies, total_unearned, and avg_progress are still
# recomputed by update_profile_trophy_counts() because they depend on
# the user's hide_hiddens / hide_zeros settings (filter changes can't
# be caught by EarnedTrophy signals). Those queries are cheap — they
# sum the already-denormalized ProfileGame.earned_trophies_count fields.
#
# The daily recalc_profile_counters cron reconciles all four type counters
# from scratch as a drift-correction safety net.
# ──────────────────────────────────────────────────────────────────────

_TROPHY_TYPE_TO_PROFILE_FIELD = {
    'bronze': 'total_bronzes',
    'silver': 'total_silvers',
    'gold': 'total_golds',
    'platinum': 'total_plats',
}


def _resolve_trophy_type(instance):
    """Read trophy type from the instance, preferring a sync-stamped attribute
    over the FK lookup. Sync paths set `_trophy_type` on the EarnedTrophy
    before calling save() so this signal doesn't re-issue a SELECT for the
    Trophy row. Non-sync paths (admin actions, cascade deletes) fall back
    to the lazy FK access."""
    cached = getattr(instance, '_trophy_type', None)
    if cached:
        return cached
    try:
        return instance.trophy.trophy_type
    except Exception:
        return None


@receiver(post_save, sender=EarnedTrophy, dispatch_uid="update_profile_type_counts_on_save")
def update_profile_type_counts_on_save(sender, instance, created, **kwargs):
    """Maintain Profile.total_bronzes/silvers/golds/plats on EarnedTrophy save."""
    if created:
        if not instance.earned:
            return
        type_field = _TROPHY_TYPE_TO_PROFILE_FIELD.get(_resolve_trophy_type(instance))
        if type_field:
            Profile.objects.filter(pk=instance.profile_id).update(
                **{type_field: F(type_field) + 1}
            )
        return

    prev = _resolve_previous_earned(instance)
    if prev is None:
        return  # daily reconcile fixes drift

    type_field = _TROPHY_TYPE_TO_PROFILE_FIELD.get(_resolve_trophy_type(instance))
    if not type_field:
        return

    if prev is False and instance.earned is True:
        Profile.objects.filter(pk=instance.profile_id).update(
            **{type_field: F(type_field) + 1}
        )
    elif prev is True and instance.earned is False:
        Profile.objects.filter(pk=instance.profile_id, **{f'{type_field}__gt': 0}).update(
            **{type_field: F(type_field) - 1}
        )


@receiver(post_delete, sender=EarnedTrophy, dispatch_uid="update_profile_type_counts_on_delete")
def update_profile_type_counts_on_delete(sender, instance, **kwargs):
    """Decrement Profile.total_<type> when an earned row is removed."""
    if not instance.earned:
        return
    type_field = _TROPHY_TYPE_TO_PROFILE_FIELD.get(_resolve_trophy_type(instance))
    if not type_field:
        return
    Profile.objects.filter(pk=instance.profile_id, **{f'{type_field}__gt': 0}).update(
        **{type_field: F(type_field) - 1}
    )


# ──────────────────────────────────────────────────────────────────────
# Profile premium transitions: keep profile showcases in sync with premium
# tier. Runs for every path that changes user_is_premium (subscription
# webhooks, admin toggles, management commands, shell edits).
# ──────────────────────────────────────────────────────────────────────
@receiver(pre_save, sender=Profile, dispatch_uid="track_profile_premium_transition")
def _track_profile_premium_transition(sender, instance, **kwargs):
    """Snapshot the old premium value so the post_save handler can detect the edge."""
    if not instance.pk:
        instance._old_user_is_premium = None
        instance._old_country_code = None
        return
    try:
        old = Profile.objects.only('user_is_premium', 'country_code').get(pk=instance.pk)
        instance._old_user_is_premium = old.user_is_premium
        instance._old_country_code = old.country_code
    except Profile.DoesNotExist:
        instance._old_user_is_premium = None
        instance._old_country_code = None


@receiver(post_save, sender=Profile, dispatch_uid="propagate_country_to_standings")
def _propagate_country_to_standings(sender, instance, created, **kwargs):
    """Keep the denormalized `country_code` on the standing stores in step with the profile.

    The recompute seams (badge_xp.recompute_standing, contract_service.recompute_career_standing) already
    stamp it on every row they write, which covers the normal case: a profile syncs, its standings are
    rebuilt, the country travels with them. This handler covers the one path that bypasses them -- the
    country CHANGING with no recompute behind it, which would otherwise leave a hunter ranked in the
    country they left until their next badge evaluation.

    Gated on the edge, not fired on every profile save: country changes are rare (it comes from PSN), and
    a blind UPDATE per store on every Profile.save() would be a real cost for a value that almost never
    moves.

    EVERY store carrying a denormalized `country_code` has to be in this list. One left out does not error
    -- it just keeps ranking that hunter in the country they left, on one board out of several, which is
    the kind of thing only a reader who moved would ever notice.
    """
    if created:
        return
    old = getattr(instance, '_old_country_code', None)
    new = instance.country_code or ''
    if old is None or (old or '') == new:
        return

    for model in country_mirrored_standings():
        model.objects.filter(profile_id=instance.pk).update(country_code=new)


def country_mirrored_standings():
    """The stores holding a denormalized copy of `Profile.country_code`.

    Named and returned rather than inlined so a test can check it against what the models actually declare.
    Adding a store and forgetting this list does not error -- it leaves that one board ranking a hunter in
    the country they left, which nobody would think to look for.
    """
    from trophies.models import (
        ProfileBadgeStanding, ProfileCareerStanding, ProfileEditionStanding, ProfileJobXP,
        SeriesBadgeStanding,
    )
    return (ProfileBadgeStanding, ProfileCareerStanding, ProfileEditionStanding,
            SeriesBadgeStanding, ProfileJobXP)


@receiver(pre_delete, sender=Profile, dispatch_uid="reconcile_group_badge_earned_counts_on_profile_delete")
def reconcile_group_badge_earned_counts_on_profile_delete(sender, instance, **kwargs):
    """GroupBadge.earned_count is a manual denorm owned by badge_apply's award/revoke path (no signals there).
    A Profile deletion cascade-drops the profile's UserGroupBadge holds WITHOUT going through apply, which would
    leave earned_count inflated. Reconcile here on pre_delete -- while the holds still exist -- decrementing each
    held group badge by one (guarded > 0 so a already-drifted count can't go negative). Fires only on profile
    deletion, so it never double-counts the normal revoke (which deletes the hold without deleting the profile)."""
    gb_ids = list(
        UserGroupBadge.objects.filter(profile=instance).values_list('group_badge_id', flat=True)
    )
    if gb_ids:
        GroupBadge.objects.filter(pk__in=gb_ids, earned_count__gt=0).update(earned_count=F('earned_count') - 1)



# --- Stage Icon Auto-Population ---

def _compute_stage_icon(stage):
    """Resolve a Stage's icon source by precedence.

    Standalone Stage.concepts win over ConceptBundle members so a stage with
    both qualifier types keeps its original icon. Bundles provide the icon for
    bundle-only stages (e.g. PS3-episodic-only games where no full-list concept
    is attached). Returns the icon URL string or None.

    Uses `Concept.cover_url` (the shared IGDB-first chain: trusted IGDB
    cover -> PSN MASTER, skipping `PP_*` stubs) rather than reading
    `concept_icon_url` directly. The new IGDB-anchored concepts often have
    no PSN MASTER URL because their identity comes from IGDB, not PSN; the
    direct field read returned empty for them and broke this auto-fill.
    """
    first_standalone = stage.concepts.first()
    if first_standalone:
        return first_standalone.cover_url or None
    for bundle in stage.concept_bundles.order_by('sort_order', 'id'):
        first_member = bundle.concepts.first()
        if first_member:
            return first_member.cover_url or None
    return None


def _refresh_stage_icon(stage):
    new_icon = _compute_stage_icon(stage)
    if stage.stage_icon != new_icon:
        stage.stage_icon = new_icon
        stage.save(update_fields=['stage_icon'])


@receiver(m2m_changed, sender=Stage.concepts.through, dispatch_uid="auto_populate_stage_icon")
def auto_populate_stage_icon(sender, instance, action, **kwargs):
    """Auto-populate Stage.stage_icon when Stage.concepts changes.

    Falls back to ConceptBundle members when no standalone Concept is attached.
    """
    if action not in ('post_add', 'post_clear'):
        return
    try:
        _refresh_stage_icon(instance)
    except Exception:
        logger.exception(f"Failed to refresh stage_icon for {instance}")


@receiver(m2m_changed, sender=ConceptBundle.concepts.through, dispatch_uid="auto_populate_stage_icon_bundle")
def auto_populate_stage_icon_from_bundle(sender, instance, action, **kwargs):
    """Auto-populate Stage.stage_icon when a bundle's members change.

    Bundle changes only matter for the icon when the Stage has no standalone
    Concept; _refresh_stage_icon enforces that precedence.
    """
    if action not in ('post_add', 'post_clear'):
        return
    try:
        _refresh_stage_icon(instance.stage)
    except Exception:
        logger.exception(f"Failed to refresh stage_icon from bundle {instance}")