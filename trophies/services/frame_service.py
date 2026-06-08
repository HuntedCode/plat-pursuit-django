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

    if profile is not None:
        earned = UserBadge.objects.filter(profile=profile, badge=badge).first()
        if earned:
            state = "earned"
            stages_done = stages_total
            earned_date = date_filter(earned.earned_at, "M j, Y")
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

    # Intentionally omitted this pass (need a rarity source / earn-rank compute):
    # rarity_pct, rarity_rank, rarity_class, engraving_rank.
    # FUTURE: user frame customization layers onto `frame` here (see module docstring).
    return frame
