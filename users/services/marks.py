"""The one answer to "what mark does this person wear".

Three registers feed one slot: service (staff wrench / mod shield), then the supporter ladder
(including the grandfathered legacy mapping). PRECEDENCE IS BAKED HERE and nowhere else --
staff > mod > supporter level -- and `Profile.display_mark` denormalises the answer so every
surface reads one field with zero extra queries (leaderboards and browse pages render hundreds
of names; whale-safety is the point of the denorm).

Writers: `SubscriptionService.reconcile_premium` (the premium truth-writer) and
`CustomUser.save` on role changes. Both call `refresh_display_mark`.
"""
from users.constants import LADDER_SLUGS, LEGACY_TIER_LEVEL_MAP, SERVICE_MARKS, SUPPORT_TIERS


def worn_supporter_level(premium_tier):
    """The ladder slug a supporter WEARS: their own level, or the price-nearest level for a
    grandfathered legacy tier (moved here from the storefront view so the wall, the denorm and
    every future surface resolve identically)."""
    if premium_tier in LADDER_SLUGS:
        return premium_tier
    return LEGACY_TIER_LEVEL_MAP.get(premium_tier)


def resolve_display_mark(user, is_premium=None):
    """The worn mark key: 'staff' | 'mod' | a ladder slug | ''.

    `is_premium` may be passed by callers mid-transition (reconcile computes it before the
    profile denorm lands); otherwise the profile's stored flag is used.
    """
    role = getattr(user, 'role', '')
    if role == 'admin' or getattr(user, 'is_staff', False):
        # Bare is_staff (no role yet) counts as staff, matching the backfill: the role field is
        # the intended source, but a directly-flagged admin must not render unmarked.
        return 'staff'
    if role == 'moderator':
        return 'mod'
    if is_premium is None:
        profile = getattr(user, 'profile', None)
        is_premium = bool(profile and profile.user_is_premium)
    if is_premium:
        return worn_supporter_level(user.premium_tier) or ''
    return ''


def refresh_display_mark(user, is_premium=None):
    """Write the denorm if it changed. Safe to call from any writer; no-ops without a profile."""
    profile = getattr(user, 'profile', None)
    if profile is None:
        return
    mark = resolve_display_mark(user, is_premium=is_premium)
    if profile.display_mark != mark:
        profile.display_mark = mark
        profile.save(update_fields=['display_mark'])


def mark_style(mark):
    """(colour, label, kind) for a mark key; kind is 'service' or 'supporter'. None for ''."""
    if not mark:
        return None
    if mark in SERVICE_MARKS:
        m = SERVICE_MARKS[mark]
        return {'colour': m['colour'], 'label': m['label'], 'kind': 'service', 'key': mark}
    for tier in SUPPORT_TIERS:
        if tier['slug'] == mark:
            return {'colour': tier['colour'], 'label': f"PlatPursuit {tier['name']}",
                    'kind': 'supporter', 'key': mark,
                    'stars': tier['stars'], 'outline': tier['outline']}
    return None
