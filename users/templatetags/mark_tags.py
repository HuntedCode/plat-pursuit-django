"""The name-mark inclusion tag: one renderer for every surface that shows a marked name.

{% name_mark name=... mark=profile.display_mark %} -- the mark key comes from the denorm (or a
serialized copy of it); resolution to colour/glyph happens once here via users/services/marks.
"""
from django import template

from users.services.marks import mark_style

register = template.Library()


@register.inclusion_tag('components/name_mark.html')
def name_mark(name, mark=None, size=None):
    return {'name': name, 'mark': mark_style(mark), 'size': size}


@register.filter
def mark_colour(mark):
    """The mark's colour alone, for legacy surfaces that colour a name without the glyph."""
    style = mark_style(mark)
    return style['colour'] if style else 'inherit'
