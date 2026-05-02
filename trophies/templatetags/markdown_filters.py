"""
Template filters for markdown processing.
"""
from django import template
from django.utils.safestring import mark_safe
from trophies.services.checklist_service import ChecklistService

register = template.Library()


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
    """
    if not text:
        return ''
    html = ChecklistService.process_markdown(
        text, icon_set=icon_set, enable_spoilers=True,
    )
    return mark_safe(html)
