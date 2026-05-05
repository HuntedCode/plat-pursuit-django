"""
Template filters for markdown processing.
"""
import re

from django import template
from django.utils.html import escape
from django.utils.safestring import mark_safe
from trophies.services.checklist_service import ChecklistService

register = template.Library()


# Matches `[[slug]]` collectible references emitted by the roadmap editor.
# Slugs are server-generated via Django's slugify so they only contain
# `[a-z0-9-]`. The regex caps slug length to match SlugField(max_length=50)
# and refuses leading hyphens to mirror slugify's output.
_COLLECTIBLE_REF_RE = re.compile(r'\[\[([a-z0-9](?:[a-z0-9-]{0,49}))\]\]')


@register.filter(name='render_markdown')
def render_markdown(text, icon_set='ps4'):
    """
    Render markdown text to HTML.

    Usage in templates:
        {{ item.text|render_markdown }}
        {{ tips|render_markdown:game.controller_icon_set }}

    Pass the game's ``controller_icon_set`` ('ps4' or 'ps5') as the filter
    argument to render PlayStation controller-icon shortcodes (``:square:``,
    ``:l2:``, etc.) with the appropriate platform glyphs. Defaults to 'ps4'.

    Returns safe HTML string.
    """
    if not text:
        return ''

    # SAFETY: ChecklistService.process_markdown() runs text through markdown2.markdown()
    # then bleach.clean() with a restrictive allowlist of tags/attributes/protocols.
    # mark_safe() is valid here because bleach strips all non-allowlisted HTML.
    html = ChecklistService.process_markdown(text, icon_set=icon_set)
    return mark_safe(html)


@register.filter(name='render_roadmap_markdown')
def render_roadmap_markdown(text, icon_set='ps4'):
    """Render markdown for roadmap content, with ``||spoiler||`` support.

    Same sanitization guarantees as :func:`render_markdown`; the only
    difference is that Discord-style spoilers are enabled. Use this filter on
    roadmap-owned fields (trophy guides, step descriptions, general tips) so
    spoiler syntax stays scoped to the roadmap surface and doesn't leak into
    reviews or other markdown callers.

    Inline collectible references (``[[slug]]``) pass through as plain text
    — chain ``|render_collectible_pills:collectibles_by_slug`` after this
    filter to swap them for color-coded pills.
    """
    if not text:
        return ''
    html = ChecklistService.process_markdown(
        text, icon_set=icon_set, enable_spoilers=True,
    )
    return mark_safe(html)


@register.filter(name='render_collectible_pills', is_safe=True)
def render_collectible_pills(html, collectibles_by_slug):
    """Replace ``[[slug]]`` tokens in rendered roadmap HTML with pills.

    Chains after :func:`render_roadmap_markdown` so markdown + bleach have
    already run; we inject our own trusted span markup on top. The slug
    regex restricts to ``[a-z0-9-]`` so the value is safe to interpolate.
    Display fields (name, description) come from author-authored content
    and ARE escaped.

    Unknown slugs (renamed type / typo / deleted type) render as a muted
    "broken" pill so the author can spot them after a delete rather than
    having references silently disappear.

    Expects ``collectibles_by_slug`` as ``{slug: type_obj}`` where
    ``type_obj`` exposes ``name``, ``color``, ``icon``, ``description``,
    and ``total_count``. The reader-side JS reads ``data-slug`` to wire
    the click handler that scrolls to the Collectibles Tracker section.
    """
    if not html:
        return html
    types = collectibles_by_slug or {}

    def _replace(match):
        slug = match.group(1)
        ctype = types.get(slug)
        if ctype is None:
            return (
                f'<span class="collectible-pill is-broken" data-slug="{escape(slug)}" '
                f'title="No collectible defined for [[{escape(slug)}]]">'
                f'[[{escape(slug)}]]'
                f'</span>'
            )
        icon = (getattr(ctype, 'icon', '') or '📦')
        name = getattr(ctype, 'name', '') or slug
        color = getattr(ctype, 'color', 'primary') or 'primary'
        description = getattr(ctype, 'description', '') or ''
        total = getattr(ctype, 'total_count', None)
        total_attr = f' data-total="{total}"' if total else ''
        return (
            f'<span class="collectible-pill" '
            f'data-slug="{escape(slug)}" '
            f'data-color="{escape(color)}" '
            f'data-name="{escape(name)}" '
            f'data-description="{escape(description)}"'
            f'{total_attr}>'
            f'{escape(icon)}'
            f'<span class="collectible-pill-name">{escape(name)}</span>'
            f'</span>'
        )

    return mark_safe(_COLLECTIBLE_REF_RE.sub(_replace, html))
