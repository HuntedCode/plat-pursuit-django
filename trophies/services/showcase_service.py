"""
Profile Showcase service layer.

Steam-style profile customization: users pick showcase types to feature on their
profile (2 slots free, 5 premium). Each showcase type has a descriptor in
SHOWCASE_REGISTRY with metadata, a provider function that fetches the display
data, and an editor partial for user-controlled item selection.

New showcase types are added by registering a descriptor and implementing the
provider function.
"""
import logging

from django.db import transaction

from trophies.models import ProfileShowcase

logger = logging.getLogger('psn_api')


# ──────────────────────────────────────────────────────────────────────
# Exceptions
# ──────────────────────────────────────────────────────────────────────

class ShowcaseError(Exception):
    """Base class for showcase service domain errors."""


class ShowcaseTypeNotFound(ShowcaseError):
    """Requested showcase type has no registry entry."""


class ShowcaseLimitReached(ShowcaseError):
    """User is at their slot limit (2 free / 5 premium)."""


class ShowcaseAlreadyActive(ShowcaseError):
    """That showcase type is already active on the profile."""


class ShowcasePremiumRequired(ShowcaseError):
    """Non-premium user tried to add a premium-only showcase type."""


class ShowcaseInvalidConfig(ShowcaseError):
    """Config payload failed validation for this showcase type."""


# ──────────────────────────────────────────────────────────────────────
# Slot limits
# ──────────────────────────────────────────────────────────────────────

FREE_SLOT_LIMIT = 2
PREMIUM_SLOT_LIMIT = 5


def slot_limit_for(is_premium):
    return PREMIUM_SLOT_LIMIT if is_premium else FREE_SLOT_LIMIT


# ──────────────────────────────────────────────────────────────────────
# Provider functions (each returns a dict for template rendering)
# ──────────────────────────────────────────────────────────────────────

def provide_platinum_case(profile, config):
    """Platinum Trophy Case: up to 20 user-selected platinums.

    Displays 1 row of 10 when the user has 10 or fewer selections, 2 rows of 10
    when they have more. Reads from the existing UserTrophySelection table.
    """
    from trophies.models import UserTrophySelection

    max_items = 20
    selections = list(
        UserTrophySelection.objects.filter(profile=profile)
        .select_related(
            'earned_trophy__trophy__game',
            'earned_trophy__trophy__game__concept',
            'earned_trophy__trophy__game__concept__igdb_match',
        )
        .order_by('-earned_trophy__earned_date_time')[:max_items]
    )
    # Display size: 1 row (10) if count <= 10, else 2 rows (20)
    display_size = 10 if len(selections) <= 10 else 20
    padded = selections + [None] * (display_size - len(selections))
    return {
        'items': padded,
        'has_items': bool(selections),
        'max_items': max_items,
        'display_size': display_size,
    }


def provide_favorite_games(profile, config):
    """Favorite Games: up to 6 user-selected games from their library.

    Config schema: {"game_ids": [id1, id2, ...]} - order is preserved from
    the JSON list so users can arrange their favorites.
    """
    from trophies.models import ProfileGame

    max_items = 6
    game_ids = (config or {}).get('game_ids', [])[:max_items]
    if not game_ids:
        return {
            'items': [None] * max_items,
            'has_items': False,
            'max_items': max_items,
        }

    pg_map = {
        pg.game_id: pg for pg in ProfileGame.objects.filter(
            profile=profile, game_id__in=game_ids,
        ).select_related('game', 'game__concept', 'game__concept__igdb_match').defer(
            # Defer the IGDB raw_response blob — unused by showcase rendering.
            # See CLAUDE.md "IGDB cover-art querysets".
            'game__concept__igdb_match__raw_response',
        )
    }
    # Preserve order from config, drop any missing
    ordered = [pg_map[gid] for gid in game_ids if gid in pg_map]
    padded = ordered + [None] * (max_items - len(ordered))
    return {
        'items': padded,
        'has_items': bool(ordered),
        'max_items': max_items,
    }


def provide_badge_showcase(profile, config):
    """Badge Showcase: up to 5 user-selected badges.

    Reads from the existing ProfileBadgeShowcase model (a separate table that
    already handles display_order + uniqueness). This provider formats each
    badge for rendering.
    """
    from trophies.models import ProfileBadgeShowcase

    max_items = 5
    tier_names = {1: 'Bronze', 2: 'Silver', 3: 'Gold', 4: 'Platinum'}

    entries = list(
        ProfileBadgeShowcase.objects
        .filter(profile=profile)
        .select_related(
            'badge', 'badge__base_badge',
            'badge__most_recent_concept', 'badge__most_recent_concept__igdb_match',
        )
        .order_by('display_order')[:max_items]
    )

    items = []
    for entry in entries:
        badge = entry.badge
        try:
            layers = badge.get_badge_layers()
        except Exception:
            continue
        if not layers.get('has_custom_image'):
            continue
        concept = badge.most_recent_concept
        bg_url = concept.get_cover_url() if concept else ''
        items.append({
            'layers': layers,
            'name': badge.effective_display_series or badge.series_slug,
            'tier': badge.tier,
            'tier_name': tier_names.get(badge.tier, ''),
            'series_slug': badge.series_slug,
            'bg_url': bg_url or '',
        })

    padded = items + [None] * (max_items - len(items))
    return {
        'items': padded,
        'has_items': bool(items),
        'max_items': max_items,
    }


def provide_rarest_trophies(profile, config):
    """Rarest Trophies: top 6 earned non-platinum trophies by rarity.

    Ordering: PSN global earn rate ascending (rarer first), then PP earn rate
    ascending as tiebreaker. Platinums are excluded because they're always the
    rarest in their stack and would dominate the list.

    Config options:
      - `one_per_game` (bool, default True): enforce unique game per trophy
        with graceful fallback to fill remaining slots if not enough games.
    """
    from trophies.models import EarnedTrophy

    max_items = 6
    one_per_game = (config or {}).get('one_per_game', True)

    base_qs = (
        EarnedTrophy.objects
        .filter(
            profile=profile, earned=True,
            trophy__trophy_earn_rate__gt=0,
        )
        .exclude(trophy__trophy_type='platinum')
        .exclude(
            trophy__game__shovelware_status__in=['auto_flagged', 'manually_flagged'],
        )
        .select_related(
            'trophy',
            'trophy__game',
            'trophy__game__concept',
            'trophy__game__concept__igdb_match',
        )
        .order_by('trophy__trophy_earn_rate', 'trophy__earn_rate')
    )

    if one_per_game:
        # Two-pass: fill from unique games first, then top up with duplicates
        # if the user doesn't have enough unique games.
        candidates = list(base_qs[:max_items * 6])  # small overfetch cap
        unique_picks = []
        seen_games = set()
        leftovers = []
        for et in candidates:
            game_id = et.trophy.game_id
            if game_id in seen_games:
                leftovers.append(et)
                continue
            seen_games.add(game_id)
            unique_picks.append(et)
            if len(unique_picks) >= max_items:
                break

        if len(unique_picks) < max_items:
            # Graceful fallback: fill remaining slots from the rest
            needed = max_items - len(unique_picks)
            unique_picks.extend(leftovers[:needed])
            if len(unique_picks) < max_items:
                # Still not enough from overfetch; pull more ignoring dedup
                extra = base_qs.exclude(
                    pk__in=[et.pk for et in unique_picks]
                )[: max_items - len(unique_picks)]
                unique_picks.extend(extra)

        rarest = unique_picks[:max_items]
    else:
        rarest = list(base_qs[:max_items])

    return {
        'items': rarest,
        'has_items': bool(rarest),
        'max_items': max_items,
    }


def provide_recent_platinums(profile, config):
    """Recent Platinums: most recent 6 earned platinums."""
    from trophies.models import EarnedTrophy

    max_items = 6
    recent = list(
        EarnedTrophy.objects
        .filter(profile=profile, earned=True, trophy__trophy_type='platinum')
        .select_related(
            'trophy',
            'trophy__game',
            'trophy__game__concept',
            'trophy__game__concept__igdb_match',
        )
        .order_by('-earned_date_time')[:max_items]
    )
    return {
        'items': recent,
        'has_items': bool(recent),
        'max_items': max_items,
    }


def provide_review_showcase(profile, config):
    """Review Showcase: up to 2 user-selected reviews.

    Config schema: {"review_ids": [id1, id2]} - order preserved from JSON list.
    """
    from trophies.models import Review

    max_items = 2
    review_ids = (config or {}).get('review_ids', [])[:max_items]
    if not review_ids:
        return {'items': [], 'has_items': False, 'max_items': max_items}

    reviews_map = {
        r.id: r for r in Review.objects.filter(
            id__in=review_ids, profile=profile, is_deleted=False,
        ).select_related('concept', 'concept__igdb_match', 'concept_trophy_group').defer(
            # Defer the IGDB raw_response blob — unused by review-card rendering.
            'concept__igdb_match__raw_response',
        )
    }
    ordered = [reviews_map[rid] for rid in review_ids if rid in reviews_map]
    return {
        'items': ordered,
        'has_items': bool(ordered),
        'max_items': max_items,
    }


def provide_title_showcase(profile, config):
    """Title Showcase: 4-6 user-selected earned titles.

    Config schema: {"user_title_ids": [id1, id2, ...]} - order preserved.
    """
    from trophies.models import UserTitle

    max_items = 6
    user_title_ids = (config or {}).get('user_title_ids', [])[:max_items]
    if not user_title_ids:
        return {'items': [], 'has_items': False, 'max_items': max_items}

    titles_map = {
        ut.id: ut for ut in UserTitle.objects.filter(
            id__in=user_title_ids, profile=profile,
        ).select_related('title')
    }
    ordered = [titles_map[uid] for uid in user_title_ids if uid in titles_map]
    return {
        'items': ordered,
        'has_items': bool(ordered),
        'max_items': max_items,
    }


# ──────────────────────────────────────────────────────────────────────
# Config validation
# ──────────────────────────────────────────────────────────────────────

def _validate_rarest_trophies_config(profile, config):
    """Validate the rarest_trophies config. Only `one_per_game` (bool) is allowed."""
    one_per_game = config.get('one_per_game', True)
    if not isinstance(one_per_game, bool):
        raise ShowcaseInvalidConfig("one_per_game must be a boolean.")
    return {'one_per_game': one_per_game}


def _validate_review_showcase_config(profile, config):
    """Ensure review_ids belong to the profile and respect max_items (2)."""
    from trophies.models import Review

    review_ids = config.get('review_ids', [])
    if not isinstance(review_ids, list):
        raise ShowcaseInvalidConfig("review_ids must be a list.")
    if len(review_ids) > 2:
        raise ShowcaseInvalidConfig("Maximum 2 reviews allowed.")

    if review_ids:
        owned = set(
            Review.objects.filter(
                id__in=review_ids, profile=profile, is_deleted=False,
            ).values_list('id', flat=True)
        )
        missing = [rid for rid in review_ids if rid not in owned]
        if missing:
            raise ShowcaseInvalidConfig(
                f"Review IDs not found: {missing}"
            )
    return {'review_ids': review_ids}


def _validate_title_showcase_config(profile, config):
    """Ensure user_title_ids belong to the profile and respect max_items (6)."""
    from trophies.models import UserTitle

    user_title_ids = config.get('user_title_ids', [])
    if not isinstance(user_title_ids, list):
        raise ShowcaseInvalidConfig("user_title_ids must be a list.")
    if len(user_title_ids) > 6:
        raise ShowcaseInvalidConfig("Maximum 6 titles allowed.")

    if user_title_ids:
        owned = set(
            UserTitle.objects.filter(
                id__in=user_title_ids, profile=profile,
            ).values_list('id', flat=True)
        )
        missing = [uid for uid in user_title_ids if uid not in owned]
        if missing:
            raise ShowcaseInvalidConfig(
                f"UserTitle IDs not found: {missing}"
            )
    return {'user_title_ids': user_title_ids}


def _validate_favorite_games_config(profile, config):
    """Ensure game_ids belong to the profile and respect max_items."""
    from trophies.models import ProfileGame

    game_ids = config.get('game_ids', [])
    if not isinstance(game_ids, list):
        raise ShowcaseInvalidConfig("game_ids must be a list.")
    if len(game_ids) > 6:
        raise ShowcaseInvalidConfig("Maximum 6 favorite games allowed.")

    if game_ids:
        owned = set(
            ProfileGame.objects.filter(
                profile=profile, game_id__in=game_ids,
            ).values_list('game_id', flat=True)
        )
        missing = [gid for gid in game_ids if gid not in owned]
        if missing:
            raise ShowcaseInvalidConfig(
                f"Game IDs not found in your library: {missing}"
            )

    return {'game_ids': game_ids}


# ──────────────────────────────────────────────────────────────────────
# Registry
# ──────────────────────────────────────────────────────────────────────

SHOWCASE_REGISTRY = {
    ProfileShowcase.SHOWCASE_PLATINUM_CASE: {
        'slug': ProfileShowcase.SHOWCASE_PLATINUM_CASE,
        'name': 'Platinum Trophy Case',
        'description': 'Display up to 20 of your favorite platinum trophies.',
        'template': 'trophies/partials/profile_showcases/showcase_platinum_case.html',
        'editor_template': 'trophies/partials/profile_editor/picker_platinum_case.html',
        'provider': provide_platinum_case,
        'validator': None,
        'requires_premium': False,
        'is_automatic': False,
        'max_items': 20,
    },
    ProfileShowcase.SHOWCASE_FAVORITE_GAMES: {
        'slug': ProfileShowcase.SHOWCASE_FAVORITE_GAMES,
        'name': 'Favorite Games',
        'description': 'Feature up to 6 games from your library.',
        'template': 'trophies/partials/profile_showcases/showcase_favorite_games.html',
        'editor_template': 'trophies/partials/profile_editor/picker_favorite_games.html',
        'provider': provide_favorite_games,
        'validator': _validate_favorite_games_config,
        'requires_premium': False,
        'is_automatic': False,
        'max_items': 6,
    },
    ProfileShowcase.SHOWCASE_BADGE: {
        'slug': ProfileShowcase.SHOWCASE_BADGE,
        'name': 'Badge Showcase',
        'description': 'Display up to 5 of your earned badges.',
        'template': 'trophies/partials/profile_showcases/showcase_badge.html',
        'editor_template': 'trophies/partials/profile_editor/picker_badges.html',
        'provider': provide_badge_showcase,
        'validator': None,
        'requires_premium': True,
        'is_automatic': False,
        'max_items': 5,
    },
    ProfileShowcase.SHOWCASE_RAREST: {
        'slug': ProfileShowcase.SHOWCASE_RAREST,
        'name': 'Rarest Trophies',
        'description': 'Your 6 rarest earned trophies (excludes platinums).',
        'template': 'trophies/partials/profile_showcases/showcase_rarest_trophies.html',
        'editor_template': None,
        'provider': provide_rarest_trophies,
        'validator': _validate_rarest_trophies_config,
        'requires_premium': True,
        'is_automatic': False,
        'max_items': 6,
    },
    ProfileShowcase.SHOWCASE_RECENT_PLATS: {
        'slug': ProfileShowcase.SHOWCASE_RECENT_PLATS,
        'name': 'Recent Platinums',
        'description': 'Your 6 most recently earned platinum trophies.',
        'template': 'trophies/partials/profile_showcases/showcase_recent_platinums.html',
        'editor_template': None,
        'provider': provide_recent_platinums,
        'validator': None,
        'requires_premium': True,
        'is_automatic': True,
        'max_items': 6,
    },
    ProfileShowcase.SHOWCASE_REVIEW: {
        'slug': ProfileShowcase.SHOWCASE_REVIEW,
        'name': 'Review Showcase',
        'description': 'Feature 2 of your written game reviews.',
        'template': 'trophies/partials/profile_showcases/showcase_reviews.html',
        'editor_template': 'trophies/partials/profile_editor/picker_reviews.html',
        'provider': provide_review_showcase,
        'validator': _validate_review_showcase_config,
        'requires_premium': True,
        'is_automatic': False,
        'max_items': 2,
    },
    ProfileShowcase.SHOWCASE_TITLE: {
        'slug': ProfileShowcase.SHOWCASE_TITLE,
        'name': 'Title Showcase',
        'description': 'Show off up to 6 of your earned titles.',
        'template': 'trophies/partials/profile_showcases/showcase_titles.html',
        'editor_template': 'trophies/partials/profile_editor/picker_titles.html',
        'provider': provide_title_showcase,
        'validator': _validate_title_showcase_config,
        'requires_premium': True,
        'is_automatic': False,
        'max_items': 6,
    },
}


def get_descriptor(showcase_type):
    """Get the registry descriptor for a showcase type, raising if unknown."""
    descriptor = SHOWCASE_REGISTRY.get(showcase_type)
    if not descriptor:
        raise ShowcaseTypeNotFound(f"Unknown showcase type: {showcase_type}")
    return descriptor


# ──────────────────────────────────────────────────────────────────────
# Service
# ──────────────────────────────────────────────────────────────────────

class ProfileShowcaseService:
    """CRUD + rendering for profile showcases."""

    @staticmethod
    def get_active_showcases(profile):
        """Active showcases ordered by sort_order."""
        return list(
            ProfileShowcase.objects.filter(profile=profile, is_active=True)
            .order_by('sort_order', 'created_at')
        )

    @staticmethod
    def get_all_showcases(profile):
        """All showcases (including inactive, e.g. preserved after downgrade)."""
        return list(
            ProfileShowcase.objects.filter(profile=profile)
            .order_by('sort_order', 'created_at')
        )

    @staticmethod
    def get_rendered_showcases(profile):
        """Active showcases with their provider data pre-resolved for the template.

        Returns a list of dicts: {showcase, descriptor, data}.
        """
        active = ProfileShowcaseService.get_active_showcases(profile)
        rendered = []
        for showcase in active:
            try:
                descriptor = get_descriptor(showcase.showcase_type)
            except ShowcaseTypeNotFound:
                logger.warning(
                    f"Showcase {showcase.id} has unknown type "
                    f"{showcase.showcase_type}; skipping."
                )
                continue
            data = descriptor['provider'](profile, showcase.config)
            rendered.append({
                'showcase': showcase,
                'descriptor': descriptor,
                'data': data,
            })
        return rendered

    @staticmethod
    @transaction.atomic
    def add_showcase(profile, showcase_type, *, is_premium):
        """Add a showcase to the profile. Validates premium + slot limit + uniqueness.

        If an inactive row exists for this type (e.g. preserved from a previous
        premium period), it is reactivated and its config is kept intact. This
        gives re-subscribers a one-click restore.
        """
        descriptor = get_descriptor(showcase_type)

        if descriptor['requires_premium'] and not is_premium:
            raise ShowcasePremiumRequired(
                f"{descriptor['name']} requires a premium subscription."
            )

        active_count = ProfileShowcase.objects.filter(
            profile=profile, is_active=True
        ).count()
        limit = slot_limit_for(is_premium)
        if active_count >= limit:
            raise ShowcaseLimitReached(
                f"You've reached your limit of {limit} showcases."
            )

        existing = ProfileShowcase.objects.filter(
            profile=profile, showcase_type=showcase_type,
        ).first()

        from django.db.models import Max
        agg = ProfileShowcase.objects.filter(profile=profile).aggregate(m=Max('sort_order'))
        next_order = (agg['m'] or 0) + 1

        if existing:
            if existing.is_active:
                raise ShowcaseAlreadyActive(
                    f"{descriptor['name']} is already on your profile."
                )
            # Reactivate with preserved config, moved to the end of the list
            existing.is_active = True
            existing.sort_order = next_order
            existing.save(update_fields=['is_active', 'sort_order', 'updated_at'])
            return existing

        return ProfileShowcase.objects.create(
            profile=profile,
            showcase_type=showcase_type,
            sort_order=next_order,
            is_active=True,
            config={},
        )

    @staticmethod
    @transaction.atomic
    def remove_showcase(profile, showcase_type):
        """Remove a showcase entirely (does not preserve like downgrade does)."""
        get_descriptor(showcase_type)  # validate type exists
        deleted, _ = ProfileShowcase.objects.filter(
            profile=profile, showcase_type=showcase_type
        ).delete()
        if not deleted:
            raise ShowcaseTypeNotFound(
                f"You don't have a {showcase_type} showcase to remove."
            )

    @staticmethod
    @transaction.atomic
    def reorder_showcases(profile, ordered_types):
        """Reorder active showcases by the given list of slugs.

        The list must exactly match the user's active showcase slugs (no adds,
        no removes). Sort order is reassigned 1..N in list order.
        """
        current = list(
            ProfileShowcase.objects.filter(profile=profile, is_active=True)
            .select_for_update()
        )
        current_types = {s.showcase_type for s in current}
        ordered_set = set(ordered_types)

        if ordered_set != current_types:
            raise ShowcaseError(
                "Reorder list must match your active showcases exactly."
            )
        if len(ordered_types) != len(current):
            raise ShowcaseError("Duplicate entries in reorder list.")

        by_type = {s.showcase_type: s for s in current}
        for idx, showcase_type in enumerate(ordered_types, start=1):
            showcase = by_type[showcase_type]
            if showcase.sort_order != idx:
                showcase.sort_order = idx
                showcase.save(update_fields=['sort_order', 'updated_at'])

    @staticmethod
    @transaction.atomic
    def update_showcase_config(profile, showcase_type, config):
        """Update config for a user-controlled showcase. Validates per-type."""
        descriptor = get_descriptor(showcase_type)
        showcase = ProfileShowcase.objects.select_for_update().filter(
            profile=profile, showcase_type=showcase_type
        ).first()
        if not showcase:
            raise ShowcaseTypeNotFound(
                f"You don't have a {showcase_type} showcase."
            )

        validator = descriptor.get('validator')
        if validator:
            config = validator(profile, config or {})
        else:
            config = config or {}

        showcase.config = config
        showcase.save(update_fields=['config', 'updated_at'])
        return showcase

    @staticmethod
    @transaction.atomic
    def handle_premium_downgrade(profile):
        """Deactivate premium-only showcases, keep free ones active.

        Preserves configuration for restoration on re-subscription.
        Compacts sort_order on the remaining active showcases.
        """
        all_showcases = list(
            ProfileShowcase.objects.filter(profile=profile)
            .select_for_update()
            .order_by('sort_order', 'created_at')
        )

        kept_free = []
        for showcase in all_showcases:
            try:
                descriptor = get_descriptor(showcase.showcase_type)
            except ShowcaseTypeNotFound:
                continue
            if descriptor['requires_premium']:
                if showcase.is_active:
                    showcase.is_active = False
                    showcase.save(update_fields=['is_active', 'updated_at'])
            else:
                if showcase.is_active:
                    kept_free.append(showcase)

        # Compact sort_order on remaining active (free) showcases
        for idx, showcase in enumerate(kept_free[:FREE_SLOT_LIMIT], start=1):
            if showcase.sort_order != idx:
                showcase.sort_order = idx
                showcase.save(update_fields=['sort_order', 'updated_at'])
