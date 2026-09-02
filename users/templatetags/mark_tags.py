"""The name-mark inclusion tag: one renderer for every surface that shows a marked name.

{% name_mark name=... mark=profile.display_mark %} -- the mark key comes from the denorm (or a
serialized copy of it); resolution to colour/glyph happens once here via users/services/marks.
"""
from django import template

from users.services.marks import mark_style

register = template.Library()


@register.inclusion_tag('components/name_mark.html')
def name_mark(name, mark=None, size=None, index=None):
    # A row index desynchronises the name's flow against its neighbours (negative delay = start
    # mid-cycle immediately). 700ms against the 16s loop spreads adjacent rows well apart.
    delay = None
    if index is not None:
        try:
            delay = f'-{(int(index) * 700) % 16000}ms'
        except (TypeError, ValueError):
            delay = None
    return {'name': name, 'mark': mark_style(mark), 'size': size, 'delay': delay}


@register.filter
def mark_colour(mark):
    """The mark's colour alone, for legacy surfaces that colour a name without the glyph."""
    style = mark_style(mark)
    return style['colour'] if style else 'inherit'


@register.filter
def mark_title_label(mark):
    """The mark's title-line label ("PlatPursuit Backer" / "Staff" / "Moderator"), or empty
    for the unmarked -- for template conditionals; rendering goes through {% mark_title %}."""
    style = mark_style(mark)
    return style['label'] if style else ''


@register.inclusion_tag('components/mark_title.html')
def mark_title(mark, sep=False):
    """The mark's title as a coloured, STATIC span -- every register, each in its own colour;
    `sep` renders the joining dot when the line already carries an earned title."""
    return {'mark': mark_style(mark), 'sep': sep}

