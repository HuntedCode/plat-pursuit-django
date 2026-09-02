"""Shared display helper for trophy notifications.

This was the platinum share-card data layer until the 2026-08 plat card rebuild moved that job to
`core/services/completion_card_service.py` -- which is keyed on a completion rather than a platinum
EarnedTrophy, and reads the NEW grouping-badge system instead of the legacy Badge/UserBadgeProgress
tier rows. All that is left here is the rarity label, which the notification pipeline shares.
"""
import logging

logger = logging.getLogger(__name__)


class ShareableDataService:
    """Display helpers shared by the notification pipeline."""

    @staticmethod
    def get_rarity_label(rarity):
        """Convert trophy_rarity (0-3) to display label."""
        rarity_map = {
            0: 'Ultra Rare',
            1: 'Very Rare',
            2: 'Rare',
            3: 'Common',
        }
        return rarity_map.get(rarity, 'Unknown')
