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