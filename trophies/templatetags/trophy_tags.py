"""
Template tags for trophy-related functionality.
"""
from django import template
from trophies.models import Trophy, TrophyGroup

register = template.Library()


@register.simple_tag
def get_trophy(trophy_id):
    """
    Get trophy by ID.

    Usage in templates:
        {% get_trophy item.trophy_id as trophy %}
        {{ trophy.trophy_name }}

    Returns Trophy object or None if not found.
    """
    if not trophy_id:
        return None

    try:
        return Trophy.objects.get(id=trophy_id)
    except Trophy.DoesNotExist:
        return None


@register.filter
def trophy_rarity_label(rarity):
    """
    Convert trophy rarity int to label.

    Usage:
        {{ trophy.trophy_rarity|trophy_rarity_label }}
    """
    labels = {
        0: 'Ultra Rare',
        1: 'Very Rare',
        2: 'Rare',
        3: 'Common'
    }
    return labels.get(rarity, 'Unknown')


@register.simple_tag
def get_trophy_group(trophy):
    """
    Get trophy group information for a trophy.

    Usage in templates:
        {% get_trophy_group trophy as trophy_group %}
        {{ trophy_group.trophy_group_name }}

    Returns TrophyGroup object or None if not found or default group.
    """
    if not trophy or not trophy.trophy_group_id:
        return None

    # Don't return anything for default/base game
    if trophy.trophy_group_id == 'default':
        return None

    try:
        return TrophyGroup.objects.get(
            game=trophy.game,
            trophy_group_id=trophy.trophy_group_id
        )
    except TrophyGroup.DoesNotExist:
        return None


@register.filter
def is_dlc_trophy(trophy):
    """
    Check if trophy is from DLC (not base game).

    Usage:
        {% if trophy|is_dlc_trophy %}DLC{% endif %}
    """
    if not trophy or not trophy.trophy_group_id:
        return False
    return trophy.trophy_group_id != 'default'


@register.filter
def in_set(value, the_set):
    """
    Check if value is in a set or collection.

    Usage:
        {% if item.id|in_set:earned_trophy_item_ids %}...{% endif %}
    """
    if the_set is None:
        return False
    return value in the_set
