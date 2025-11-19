from django import template
from django.utils import timezone
from datetime import datetime
from humanize import naturaltime

register = template.Library()

@register.filter(name='iso_naturaltime')
def iso_naturaltime(value):
    """Parse ISO string to datetime and apply naturaltime."""
    if not isinstance(value, str):
        return value
    try:
        if value.endswith('Z'):
            value = value[:-1] + '+00:00'
        dt = datetime.fromisoformat(value)
        if not timezone.is_aware(dt):
            dt = timezone.make_aware(dt, timezone.utc)
        return naturaltime(dt)
    except (ValueError, TypeError):
        return value

@register.filter
def platform_color(game):
    platforms = game.title_platform
    if "PS5" in platforms:
        return 'primary'
    elif "PS4" in platforms:
        return 'accent'
    elif "PS3" in platforms:
        return 'warning'
    else:
        return 'secondary'

@register.filter
def platform_color_str(platform_str):
    if platform_str == "PS5":
        return 'primary'
    elif platform_str == "PS4":
        return 'accent'
    elif platform_str == "PS3":
        return 'warning'
    else:
        return 'secondary'

@register.filter
def trophy_color(trophy):
    type = trophy.trophy_type
    if type == 'bronze':
        return 'trophy-bronze'
    elif type == 'silver':
        return 'trophy-silver'
    elif type == 'gold':
        return 'trophy-gold'
    elif type == 'platinum':
        return 'trophy-platinum'

@register.filter
def multiply(value, arg):
    try:
        return float(value) * float(arg)
    except:
        return ''

@register.filter
def psn_rarity(rarity_int):
    if rarity_int == 0:
        return 'Ultra Rare'
    elif rarity_int == 1:
        return 'Rare'
    elif rarity_int == 2:
        return 'Uncommon'
    elif rarity_int == 3:
        return 'Common'