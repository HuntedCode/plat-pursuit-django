"""Game flag badges: `{% flag_badges keys %}` draws them, `game|flag_keys` reads one trophy list's own.

The glyphs live ONCE, in the sprite `base.html` mounts (`partials/icons/_game_flag_sprite.html`); a badge is a
`<use>` of its symbol, so a template, a JSON-built row (`PlatPursuit.flagBadges` in utils.js) and the Game page's
labelled chips all draw the same icon. Which keys a surface passes is `trophies.services.flag_badges`' business.
"""
from django import template
from django.utils.html import format_html, format_html_join

from trophies.services.flag_badges import FLAG_META, FLAG_TIPS, own_keys

register = template.Library()

#: `md` sits on a full card's cover; `sm` on a row, a challenge square or a small card.
SIZES = ('md', 'sm')
#: `stack` is a column in the cover's top-left corner (the caller's container is `position: relative`); `strip` is
#: the same corner as a row, for art too short for a column (a profile card's band); `row` is inline, for the end
#: of a list row.
LAYOUTS = ('stack', 'strip', 'row')


def flag_icon_markup(key, css_class='pp-gflag__i'):
    """The `<svg><use></svg>` for one flag's glyph. Shared with the Game page's labelled chips."""
    cls = format_html(' class="{}"', css_class) if css_class else ''
    return format_html('<svg{} viewBox="0 0 24 24" aria-hidden="true"><use href="#pp-gflag-{}"/></svg>', cls, key)


@register.simple_tag
def flag_icon(key, css_class='pp-gflag__i'):
    return flag_icon_markup(key, css_class) if key in FLAG_META else ''


@register.simple_tag
def flag_badges(keys, size='md', layout='stack'):
    """The badges for `keys` (an ordered iterable of flag keys), or nothing at all when there are none: an empty
    container would still be a hit target and a layout box. Unknown keys are skipped rather than drawn blank."""
    keys = [k for k in (keys or ()) if k in FLAG_META]
    if not keys:
        return ''
    size = size if size in SIZES else 'md'
    layout = layout if layout in LAYOUTS else 'stack'
    badges = format_html_join(
        '', '<span class="pp-gflag pp-gflag--{}" title="{}">{}<span class="sr-only">{}</span></span>',
        ((FLAG_META[k][1], FLAG_META[k][0], flag_icon_markup(k), FLAG_META[k][0]) for k in keys))
    return format_html('<span class="pp-gflags pp-gflags--{} pp-gflags--{}">{}</span>', layout, size, badges)


@register.filter
def flag_keys(game, buggy=False):
    """`game|flag_keys` for one trophy list's own flags; `game|flag_keys:True` keeps Buggy (Game pages, browse)."""
    return own_keys(game, buggy=bool(buggy)) if game is not None else ()


@register.filter
def flag_label(key):
    return FLAG_META.get(key, ('', ''))[0]


@register.filter
def flag_tone(key):
    return FLAG_META.get(key, ('', ''))[1]


@register.filter
def flag_tip(key):
    return FLAG_TIPS.get(key, '')


@register.filter
def flag_drop(keys, key):
    """`keys|flag_drop:'shovelware'`: the keys without one, for a surface that shows that flag elsewhere."""
    return tuple(k for k in (keys or ()) if k != key)
