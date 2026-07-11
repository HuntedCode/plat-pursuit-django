"""Frame presentation service.

Builds the data contract consumed by templates/components/frame.html (the Frame
primitive) from a real Badge + an optional viewer Profile. This is the Frame's
first production mounting; the builder is reusable across every surface that
shows a badge frame (badge-detail hero, gallery, Pursuer Card, home).

FUTURE (NOT this update): frames become user-customizable per badge/frame. That
customization layers onto the returned dict via a SEPARATE step (e.g. a
UserFrameCustomization model + apply_frame_customization(frame, profile, badge)),
populating the component's customization hooks (flair_slug, extra_class, and the
background/overlay/particle slots). Keep this builder a pure DATA-derived base:
do NOT bake user preferences here. The dict is the single extension point.
"""
from django.template.defaultfilters import date as date_filter
from django.templatetags.static import static

# Badge.tier is an int (1-4); the Frame component keys its CSS on tier names.
_TIER_NAME = {1: "bronze", 2: "silver", 3: "gold", 4: "platinum"}
_NEXT_TIER_LABEL = {1: "Silver", 2: "Gold", 3: "Platinum", 4: "Maxed"}

# Segmented progress meter: one cell per platinum/100% toward the badge, up to this many. Above the
# cap the medallion renders a single smooth bar instead (individual cells stop being countable).
SEGMENT_CAP = 12


# Sentinel: distinguishes "caller did not pre-fetch" (query it) from "pre-fetched, and it
# is None" (the viewer has no UserBadge/UserBadgeProgress for this badge -- do NOT query).
_UNSET = object()


def _resolve_layer_url(path):
    """get_badge_layers() returns either an already-usable URL (badge_image.url,
    an avatar URL) or a relative static path. Static-resolve the latter; pass
    through anything that's already a URL/absolute path."""
    if not path:
        return None
    if path.startswith(("http://", "https://", "/")):
        return path
    return static(path)


def build_badge_frame(badge, profile=None, *, size="default", allow_flip=True,
                      earned=_UNSET, progress=_UNSET, include_live_stats=True,
                      current_rank=_UNSET, series_xp=_UNSET, showcase=False):
    """Build the Frame data dict for a single-tier Badge.

    state is derived from the viewer's progress:
      - earned      : viewer holds the UserBadge (also the anonymous showcase look)
      - in_progress : some stage progress, not yet earned
      - unearned    : no progress
    Anonymous viewers (profile=None) get the showcase 'earned' look so public
    badge pages present the artwork in full rather than locked.

    SHOWCASE MODE (`showcase=True`, the Browse catalog Gallery): present the badge in its full
    "as-designed" (earned) look for EVERY viewer -- the emitted `state` is forced to 'earned' (no
    tarnish) -- while the viewer's real state is stashed on the frame as `owned_state`
    (earned/maintenance/in_progress/unearned) + `owned_progress_pct`, so the CARD can show a small
    ownership marker WITHOUT changing the medallion. Anonymous viewers get no owned_state (pure catalog).

    PERFORMANCE: with a profile this issues, per call: the UserBadge query, an
    earners-leaderboard Redis lookup + a series-XP DB query (both for earned
    viewers), a UserBadgeProgress query (in-progress/unearned/maintenance), the FK
    reads in get_badge_layers(), and effective_franchise/effective_developer/
    effective_funded_by (FK reads on badge + base_badge — select_related them in
    the caller: franchise, developer, funded_by, and their base_badge__ twins).
    Fine for a single hero.

    BATCH USE (galleries / the collection album, MANY frames for one profile): do NOT
    let this query per badge. Bulk-fetch the viewer's UserBadge + UserBadgeProgress once
    in the caller and pass them via `earned=` (the UserBadge or None) and `progress=`
    (the UserBadgeProgress or None) to skip the per-badge queries, and pass
    `include_live_stats=False` to skip the per-earned-badge earners-rank (Redis) +
    series-XP (DB) lookups (those are back-of-card detail, not needed for a grid). With
    both, and the badge FKs select_related in the caller, this issues ZERO queries/Redis
    per call — safe in a loop over hundreds of badges. Defaults preserve the single-hero
    behavior (query everything, include live stats).

    To show the back-of-card live stats in a batch surface WITHOUT the per-badge Redis/DB
    fan-out, pass them pre-fetched via `current_rank=` (from the batched
    redis_leaderboard.get_earners_ranks) and `series_xp=` (from the denormalized
    ProfileGamification.series_badge_xp); these win over include_live_stats and are applied
    only to earned badges.
    """
    from trophies.models import UserBadge, UserBadgeProgress

    layers = badge.get_badge_layers()
    art_layers = [
        _resolve_layer_url(layers.get("backdrop")),
        _resolve_layer_url(layers.get("main")),
        _resolve_layer_url(layers.get("foreground")),
    ]
    art_layers = [url for url in art_layers if url]

    stages_total = badge.required_stages or 0
    stages_done = stages_total  # showcase default; overridden per-viewer below
    state = "earned"
    earned_date = None
    progress_pct = None
    earn_rank = None         # permanent "Nth profile to earn this tier" (engraving)
    current_rank_v = None    # live per-series earners leaderboard position (resolved below)
    series_xp_v = None       # viewer's XP for this badge's series (resolved below)

    if profile is not None:
        earned_obj = (
            UserBadge.objects.filter(profile=profile, badge=badge).first()
            if earned is _UNSET else earned
        )
        if earned_obj:
            # status is 'earned' or 'maintenance' (a lapsed-but-never-deleted badge);
            # the Frame component has a dedicated maintenance/repair variant.
            state = earned_obj.status
            earned_date = date_filter(earned_obj.earned_at, "M j, Y")
            earn_rank = earned_obj.earn_rank
            # Live back-of-card stats: a pre-fetched (batched) value wins; otherwise compute
            # per-badge only when include_live_stats is on (the single-hero path).
            if current_rank is not _UNSET:
                current_rank_v = current_rank
            elif include_live_stats:
                from trophies.services.redis_leaderboard_service import get_earners_rank
                # get_earners_rank is already 1-indexed (or None if not on the board).
                current_rank_v = get_earners_rank(badge.series_slug, profile.id)
            if series_xp is not _UNSET:
                series_xp_v = series_xp
            elif include_live_stats:
                from trophies.services.xp_service import calculate_series_xp
                series_xp_v = calculate_series_xp(profile, badge.series_slug)
            if state == "maintenance":
                # Lapsed: show the current repair progress, not the full earned bar.
                prog = (
                    UserBadgeProgress.objects.filter(profile=profile, badge=badge).first()
                    if progress is _UNSET else progress
                )
                stages_done = prog.completed_concepts if prog else 0
                progress_pct = (
                    round(stages_done / stages_total * 100) if stages_total else 0
                )
            else:
                stages_done = stages_total
        else:
            prog = (
                UserBadgeProgress.objects.filter(profile=profile, badge=badge).first()
                if progress is _UNSET else progress
            )
            completed = prog.completed_concepts if prog else 0
            if completed > 0:
                state = "in_progress"
                stages_done = completed
                progress_pct = (
                    round(completed / stages_total * 100) if stages_total else 0
                )
            else:
                state = "unearned"
                stages_done = 0

    # Showcase (catalog Gallery): keep the personal state as `owned_state` for the card marker, then
    # force the medallion itself back to the full-colour 'earned' look. Anonymous (profile is None) keeps
    # owned_state None -> pure catalog, no marker. earned_date/earn_rank are per-viewer, so drop them off a
    # showcase frame (the medallion is "the badge as it exists", not the viewer's copy).
    owned_state = None
    owned_progress_pct = None
    if showcase and profile is not None:
        owned_state = state
        owned_progress_pct = progress_pct
        state = "earned"
        stages_done = stages_total
        progress_pct = None
        earned_date = None
        earn_rank = None

    frame = {
        "tier": _TIER_NAME.get(badge.tier, "gold"),
        "state": state,
        "size": size,
        "series_name": badge.effective_display_series or badge.name,
        "badge_name": badge.effective_display_title or badge.name,
        "description": badge.effective_description,
        "art_layers": art_layers,
        "stages_done": stages_done,
        "stages_total": stages_total,
        "next_tier_label": _NEXT_TIER_LABEL.get(badge.tier, "Maxed"),
        "allow_flip": allow_flip,
    }
    if earned_date is not None:
        frame["earned_date"] = earned_date
    if progress_pct is not None:
        frame["progress_pct"] = progress_pct
    # Showcase card marker: the viewer's real ownership of a badge shown in full-colour 'earned' glory.
    if owned_state is not None:
        frame["owned_state"] = owned_state
        if owned_progress_pct is not None:
            frame["owned_progress_pct"] = owned_progress_pct
    # Segmented progress meter cells (the medallion's in-progress/maintenance states render these as a
    # multi-bar). One cell per platinum/100%, filled up to stages_done; only when countable (<= cap).
    # Above the cap the medallion falls back to a smooth bar off progress_pct instead.
    if 0 < stages_total <= SEGMENT_CAP:
        frame["segments"] = [i < stages_done for i in range(stages_total)]

    # --- STEP 2 data: type, franchise/developer, set mark, rarity, engraving,
    # live leaderboard position, series XP. The front face already renders the
    # rarity / engraving / set-mark / current-rank slots; the rest feed the
    # type + franchise/developer + back-of-card stats. ---
    frame["badge_type"] = badge.get_badge_type_display()
    franchise = badge.effective_franchise
    collection = badge.effective_collection
    developer = badge.effective_developer
    if franchise:
        frame["franchise"] = franchise.name
    if collection:
        frame["collection"] = collection.name
    if developer:
        frame["developer"] = developer.name
    if badge.set_number:
        frame["set_number"] = badge.set_number
    if badge.rarity_pct is not None:
        frame["rarity_pct"] = round(badge.rarity_pct, 1)
        frame["rarity_rank"] = badge.rarity_rank
        if badge.rarity_class:
            frame["rarity_class"] = badge.rarity_class
    # Holographic "chase card" foil -- platinum-tier badges only (the top of a series), so the shimmer stays
    # a special payoff. The component renders it only when ALSO earned, so the flourish is a scarce reward.
    frame["is_holographic"] = badge.tier == 4
    if badge.earned_count:
        frame["earned_count"] = badge.earned_count   # how many hunters hold this tier
    funder = badge.effective_funded_by
    if funder:
        # display_psn_username is blank/null-able; fall back to the canonical
        # psn_username so a real donor without a display name still gets credited.
        frame["funded_by"] = funder.display_psn_username or funder.psn_username  # artwork-funder credit (back footer)
    if earn_rank:
        frame["engraving_rank"] = earn_rank   # permanent "Nth to earn" engraving
    if current_rank_v is not None:
        frame["current_rank"] = current_rank_v  # live per-series position (labeled "Current")
    if series_xp_v:
        frame["series_xp"] = series_xp_v

    # FUTURE: user frame customization layers onto `frame` here (see module docstring).
    return frame
