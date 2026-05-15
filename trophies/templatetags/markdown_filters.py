"""
Template filters for markdown processing.
"""
import re

from django import template
from django.utils.html import escape
from django.utils.safestring import mark_safe
from trophies.services.checklist_service import ChecklistService

register = template.Library()


# Matches `[[...]]` reference tokens emitted by the roadmap editor.
# Two shapes:
#   - typed:  [[step:42]]  [[area:chapter-1]]  [[section:tips]]
#   - bare:   [[journals]]   (legacy / collectible-type slug)
# The bare form is preserved so existing content keeps working without
# migration; new content can use either. Both shapes cap the key at 50
# chars (SlugField max_length) and refuse leading hyphens to mirror
# Django slugify's output.
_ROADMAP_REF_RE = re.compile(
    r'\[\['
    r'(?:'
        r'(step|area|section):([a-z0-9](?:[a-z0-9-]{0,49}))'  # typed: kind:key
    r'|'
        r'([a-z0-9](?:[a-z0-9-]{0,49}))'                       # bare: collectible slug
    r')'
    r'\]\]'
)

# Static metadata for `[[section:*]]` refs — these target hardcoded
# anchors on the reader page (general-tips, roadmap-steps, collectibles)
# so we don't need a runtime lookup, just a label + icon + color tint.
_SECTION_REFS = {
    'tips': {
        'anchor': 'general-tips',
        'label': 'General Tips',
        'color': 'warning',
        'icon_path': (
            'M9.663 17h4.673M12 3v1m6.364 1.636l-.707.707M21 12h-1M4 12H3'
            'm3.343-5.657l-.707-.707m2.828 9.9a5 5 0 117.072 0l-.548.547'
            'A3.374 3.374 0 0014 18.469V19a2 2 0 11-4 0v-.531c0-.895'
            '-.356-1.754-.988-2.386l-.548-.547z'
        ),
    },
    'steps': {
        'anchor': 'roadmap-steps',
        'label': 'Steps',
        'color': 'primary',
        'icon_path': (
            'M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 '
            '00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2'
            ' 2 0 012 2m-3 7h3m-3 4h3m-6-4h.01M9 16h.01'
        ),
    },
    'collectibles': {
        'anchor': 'collectibles',
        'label': 'Collectibles',
        'color': 'secondary',
        'icon_path': (
            'M9.813 15.904L9 18.75l-.813-2.846a4.5 4.5 0 00-3.09-3.09'
            'L2.25 12l2.846-.813a4.5 4.5 0 003.09-3.09L9 5.25l.813 2.846'
            'a4.5 4.5 0 003.09 3.09L15.75 12l-2.846.813a4.5 4.5 0 00'
            '-3.09 3.09zM18.259 8.715L18 9.75l-.259-1.035a3.375 3.375 0 '
            '00-2.455-2.456L14.25 6l1.036-.259a3.375 3.375 0 002.455'
            '-2.456L18 2.25l.259 1.035a3.375 3.375 0 002.456 2.456L21.75 '
            '6l-1.035.259a3.375 3.375 0 00-2.456 2.456z'
        ),
    },
    # Trophy guides — accent color matches the Trophy Guides section
    # header on both the reader detail page and the editor.
    'trophy-guides': {
        'anchor': 'trophy-guides',
        'label': 'Trophy Guides',
        'color': 'accent',
        'icon_path': (
            'M16.5 18.75h-9m9 0a3 3 0 013 3h-15a3 3 0 013-3m9 0v-3.375'
            'c0-.621-.503-1.125-1.125-1.125h-.871M7.5 18.75v-3.375c0-.621'
            '.504-1.125 1.125-1.125h.872m5.007 0H9.497m5.007 0a7.454 '
            '7.454 0 01-.982-3.172M9.497 14.25a7.454 7.454 0 00.981-3.172'
            'M5.25 4.236c-.982.143-1.954.317-2.916.52A6.003 6.003 0 007.73 '
            '9.728M5.25 4.236V4.5c0 2.108.966 3.99 2.48 5.228M5.25 4.236'
            'V2.721C7.456 2.41 9.71 2.25 12 2.25c2.291 0 4.545.16 6.75.47'
            'v1.516M7.73 9.728a6.726 6.726 0 002.748 1.35m8.272-6.842V4.5'
            'c0 2.108-.966 3.99-2.48 5.228m2.48-5.492a46.32 46.32 0 0'
            '12.916.52 6.003 6.003 0 01-5.395 4.972m0 0a6.726 6.726 0 0'
            '1-2.749 1.35m0 0a6.772 6.772 0 01-3.044 0'
        ),
    },
}


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


@register.filter(name='render_roadmap_refs', is_safe=True)
def render_roadmap_refs(html, refs):
    """Replace `[[...]]` reference tokens in rendered roadmap HTML with pills.

    Four token shapes, all dispatched through the same regex pass:

      * ``[[slug]]`` — collectible type (legacy / shorthand)
      * ``[[step:<id>]]`` — individual step
      * ``[[area:<slug>]]`` — individual collectible area
      * ``[[section:<key>]]`` — top-level section anchor (`tips`, `steps`,
        `collectibles`)

    Chains after :func:`render_roadmap_markdown` so markdown + bleach
    have already run; we inject our own trusted span / anchor markup on
    top. The regex restricts keys to ``[a-z0-9-]`` so values are safe
    to interpolate. Display fields come from author-authored content
    and ARE escaped.

    Unknown / orphaned refs render as a muted "broken" pill so the
    author can spot them after a delete rather than having references
    silently disappear.

    Expects ``refs`` as a dict::

        {
            'collectibles': {slug: type_obj},
            'steps':        {step_id_str: {'title': ..., 'position': ...}},
            'areas':        {area_slug: {'name': ...}},
        }

    Section refs (`[[section:tips]]`) are static metadata, no lookup.
    Reader-side JS wires click handlers off the rendered classes
    (`.collectible-pill`, `.roadmap-ref-pill`).
    """
    if not html:
        return html
    refs = refs or {}
    collectibles = refs.get('collectibles') or {}
    steps = refs.get('steps') or {}
    areas = refs.get('areas') or {}

    def _replace(match):
        kind = match.group(1)
        key = match.group(2)
        bare_slug = match.group(3)

        if bare_slug is not None:
            return _render_collectible_pill(bare_slug, collectibles)
        if kind == 'step':
            return _render_step_pill(key, steps)
        if kind == 'area':
            return _render_area_pill(key, areas)
        if kind == 'section':
            return _render_section_pill(key)
        # Unreachable given the regex, but defensive.
        return match.group(0)

    return mark_safe(_ROADMAP_REF_RE.sub(_replace, html))


# Backward-compat alias — older content / templates may still reference
# `render_collectible_pills`. Redirects to the unified renderer with a
# compat shim that wraps the bare collectibles dict in the new refs
# shape, so behavior is identical.
@register.filter(name='render_collectible_pills', is_safe=True)
def render_collectible_pills(html, collectibles_by_slug):
    """Compat alias — prefer ``render_roadmap_refs:roadmap_refs``."""
    return render_roadmap_refs(html, {'collectibles': collectibles_by_slug or {}})


def _render_collectible_pill(slug, collectibles):
    ctype = collectibles.get(slug)
    if ctype is None:
        return (
            f'<span class="collectible-pill is-broken" data-slug="{escape(slug)}" '
            f'title="No collectible defined for [[{escape(slug)}]]">'
            f'[[{escape(slug)}]]'
            f'</span>'
        )
    icon = (getattr(ctype, 'icon', '') or '📦')
    # Inline `[[slug]]` pills always read as a *category* reference
    # ("see all the [[journals]] in this chapter"), so display the
    # plural form. Falls back to singular when name_plural is blank
    # (legacy types from before the field shipped).
    display = (
        getattr(ctype, 'display_name_plural', None)
        or getattr(ctype, 'name_plural', '')
        or getattr(ctype, 'name', '')
        or slug
    )
    name_singular = getattr(ctype, 'name', '') or slug
    color = getattr(ctype, 'color', 'primary') or 'primary'
    description = getattr(ctype, 'description', '') or ''
    total = getattr(ctype, 'total_count', None)
    total_attr = f' data-total="{total}"' if total else ''
    return (
        f'<span class="collectible-pill" '
        f'data-slug="{escape(slug)}" '
        f'data-color="{escape(color)}" '
        f'data-name="{escape(name_singular)}" '
        f'data-name-plural="{escape(display)}" '
        f'data-icon="{escape(icon)}" '
        f'data-description="{escape(description)}"'
        f'{total_attr}>'
        f'{escape(icon)}'
        f'<span class="collectible-pill-name">{escape(display)}</span>'
        f'</span>'
    )


def _render_step_pill(step_key, steps):
    step = steps.get(step_key)
    if step is None:
        return (
            f'<span class="roadmap-ref-pill is-broken" '
            f'title="No step found for [[step:{escape(step_key)}]]">'
            f'[[step:{escape(step_key)}]]</span>'
        )
    title = step.get('title') or ''
    position = step.get('position') or '?'
    # Display reads "Step N" — short and uniform regardless of how long
    # the step's title is. The full "Step N: Title" lives in the `title`
    # attr (native browser tooltip) so readers can hover to see what
    # the step is about without bloating inline prose.
    full_label = f'Step {position}' + (f': {title}' if title else '')
    short_label = f'Step {position}'
    return (
        f'<a href="#step-{escape(step_key)}" '
        f'class="roadmap-ref-pill" data-color="primary" '
        f'data-ref-kind="step" data-ref-id="{escape(step_key)}" '
        f'title="Jump to {escape(full_label)}">'
        f'<span class="roadmap-ref-pill-name">{escape(short_label)}</span>'
        f'</a>'
    )


def _render_area_pill(area_slug, areas):
    area = areas.get(area_slug)
    if area is None:
        return (
            f'<span class="roadmap-ref-pill is-broken" '
            f'title="No area found for [[area:{escape(area_slug)}]]">'
            f'[[area:{escape(area_slug)}]]</span>'
        )
    name = area.get('name') or area_slug
    return (
        f'<a href="#collectible-area-{escape(area_slug)}" '
        f'class="roadmap-ref-pill" data-color="accent" '
        f'data-ref-kind="area" data-ref-slug="{escape(area_slug)}" '
        f'title="Jump to {escape(name)}">'
        # Inline location-pin SVG (Heroicons map-pin outline). Avoid
        # emoji here so the icon scales with text-color/opacity rules
        # the same way the rest of the pill does.
        f'<svg xmlns="http://www.w3.org/2000/svg" class="roadmap-ref-pill-icon" '
        f'fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">'
        f'<path stroke-linecap="round" stroke-linejoin="round" '
        f'd="M17.657 16.657L13.414 20.9a1.998 1.998 0 01-2.827 0l-4.244-4.243a8 8 0 1111.314 0z"/>'
        f'<path stroke-linecap="round" stroke-linejoin="round" '
        f'd="M15 11a3 3 0 11-6 0 3 3 0 016 0z"/>'
        f'</svg>'
        f'<span class="roadmap-ref-pill-name">{escape(name)}</span>'
        f'</a>'
    )


def _render_section_pill(section_key):
    meta = _SECTION_REFS.get(section_key)
    if meta is None:
        return (
            f'<span class="roadmap-ref-pill is-broken" '
            f'title="No section found for [[section:{escape(section_key)}]]">'
            f'[[section:{escape(section_key)}]]</span>'
        )
    return (
        f'<a href="#{escape(meta["anchor"])}" '
        f'class="roadmap-ref-pill" data-color="{escape(meta["color"])}" '
        f'data-ref-kind="section" data-ref-section="{escape(section_key)}" '
        f'title="Jump to the {escape(meta["label"])} section">'
        f'<svg xmlns="http://www.w3.org/2000/svg" class="roadmap-ref-pill-icon" '
        f'fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">'
        f'<path stroke-linecap="round" stroke-linejoin="round" d="{meta["icon_path"]}"/>'
        f'</svg>'
        f'<span class="roadmap-ref-pill-name">{escape(meta["label"])}</span>'
        f'</a>'
    )
