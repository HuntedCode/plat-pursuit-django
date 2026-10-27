"""Template tags for the site chrome."""
from django import template

from core.hub_subnav import footer_columns

register = template.Library()


@register.simple_tag(takes_context=True)
def footer_hubs(context):
    """The footer's hub columns for the viewing user (see `core.hub_subnav.footer_columns`)."""
    user = context.get('user')
    is_auth = bool(user and user.is_authenticated)
    return footer_columns(is_authenticated=is_auth,
                          is_member=is_auth and bool(getattr(user, 'premium_tier', '')))
