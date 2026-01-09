from django import template

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
    return query.urlencode()

@register.simple_tag(takes_context=True)
def querystring(context, exclude=None):
    request = context['request']
    params = request.GET.copy()
    if exclude:
        params.pop(exclude, None)
    return params.urlencode()