"""
Template filters for markdown processing.
"""
from django import template
from django.utils.safestring import mark_safe
from trophies.services.checklist_service import ChecklistService

register = template.Library()


@register.filter(name='render_markdown')
def render_markdown(text):
    """
    Render markdown text to HTML.

    Usage in templates:
        {{ item.text|render_markdown }}

    Returns safe HTML string.
    """
    if not text:
        return ''

    # SAFETY: ChecklistService.process_markdown() runs text through markdown2.markdown()
    # then bleach.clean() with a restrictive allowlist of tags/attributes/protocols.
    # mark_safe() is valid here because bleach strips all non-allowlisted HTML.
    html = ChecklistService.process_markdown(text)
    return mark_safe(html)
