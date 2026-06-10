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


def _resolve_layer_url(path):
    """get_badge_layers() returns either an already-usable URL (badge_image.url,
    an avatar URL) or a relative static path. Static-resolve the latter; pass
    through anything that's already a URL/absolute path."""
    if not path:
        return None
    if path.startswith(("http://", "https://", "/")):
        return path
    return static(path)


def build_badge_frame(badge, profile=None, *, size="default", allow_flip=True):
    """Build the Frame data dict for a single-tier Badge.

    state is derived from the viewer's progress:
      - earned      : viewer holds the UserBadge (also the anonymous showcase look)
      - in_progress : some stage progress, not yet earned
      - unearned    : no progress
    Anonymous viewers (profile=None) get the showcase 'earned' look so public
    badge pages present the artwork in full rather than locked.

    PERFORMANCE: with a profile this issues two profile-scoped queries (UserBadge,
    UserBadgeProgress) plus the FK reads in get_badge_layers(). Fine for a single
    hero. When rendering MANY frames for one profile (e.g. the badge gallery),
    do NOT call this in a loop — batch-fetch the viewer's UserBadge/UserBadgeProgress
    in the view and add an optional pre-fetched-progress arg here to avoid N+1.
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
    earn_rank = None       # permanent "Nth profile to earn this tier" (engraving)
    current_rank = None    # live per-series earners leaderboard position
    series_xp = None       # viewer's XP for this badge's series

    if profile is not None:
        earned = UserBadge.objects.filter(profile=profile, badge=badge).first()
        if earned:
            state = "earned"
            stages_done = stages_total
            earned_date = date_filter(earned.earned_at, "M j, Y")
            earn_rank = earned.earn_rank
            from trophies.services.redis_leaderboard_service import get_earners_rank
            from trophies.services.xp_service import calculate_series_xp
            # get_earners_rank is already 1-indexed (or None if not on the board).
            current_rank = get_earners_rank(badge.series_slug, profile.id)
            series_xp = calculate_series_xp(profile, badge.series_slug)
        else:
            prog = UserBadgeProgress.objects.filter(
                profile=profile, badge=badge
            ).first()
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

    # --- STEP 2 data: type, franchise/developer, set mark, rarity, engraving,
    # live leaderboard position, series XP. The front face already renders the
    # rarity / engraving / set-mark / current-rank slots; the rest feed the
    # type + franchise/developer + back-of-card stats. ---
    frame["badge_type"] = badge.get_badge_type_display()
    franchise = badge.effective_franchise
    developer = badge.effective_developer
    if franchise:
        frame["franchise"] = franchise.name
    if developer:
        frame["developer"] = developer.name
    if badge.set_number:
        frame["set_number"] = badge.set_number
    if badge.rarity_pct is not None:
        frame["rarity_pct"] = round(badge.rarity_pct, 1)
        frame["rarity_rank"] = badge.rarity_rank
        if badge.rarity_class:
            frame["rarity_class"] = badge.rarity_class
    if earn_rank:
        frame["engraving_rank"] = earn_rank   # permanent "Nth to earn" engraving
    if current_rank is not None:
        frame["current_rank"] = current_rank  # live per-series position (labeled "Current")
    if series_xp:
        frame["series_xp"] = series_xp

    # FUTURE: user frame customization layers onto `frame` here (see module docstring).
    return frame
