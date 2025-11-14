from django import template
from urllib.parse import urlencode

register = template.Library()

@register.simple_tag(takes_context=True)
def query_transform(context, **kwargs):
    """Transform query params while preserving others."""
    request = context['request']
    query = request.GET.copy()
    for key, new_value in kwargs.items():
        if new_value is None:
            query.pop(key, None)
        else:
            query[key] = str(new_value)
    return urlencode(query)