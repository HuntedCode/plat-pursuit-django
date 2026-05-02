# Checklist service removed during roadmap migration.
# DB tables retained for data preservation.
#
# process_markdown() kept here as a standalone function because it is
# used by review_views.py and markdown_filters.py (not checklist-specific).
import re

import bleach
import markdown2

from trophies.util_modules.controller_icons import render_shortcodes

MARKDOWN_EXTRAS = [
    'strike',
    'fenced-code-blocks',
    'cuddled-lists',
    'break-on-newline',
]

ALLOWED_HTML_TAGS = [
    'p', 'br', 'strong', 'em', 'u', 'del', 's',
    'ul', 'ol', 'li', 'blockquote', 'code', 'pre',
    'a', 'img',
    # Section headings authors can use to break up long guides. h1 is
    # intentionally excluded so authors can't compete with the page's own
    # title hierarchy; h2/h3/h4 cover the realistic depth needed inside a
    # roadmap or trophy-guide body.
    'h2', 'h3', 'h4',
]

ALLOWED_HTML_ATTRS = {
    '*': ['class'],
    'a': ['href', 'title', 'rel', 'target', 'class'],
    'img': ['src', 'alt', 'title', 'loading', 'class'],
}


class ChecklistService:
    """Retained only for process_markdown (used by reviews and template filters)."""

    MARKDOWN_EXTRAS = MARKDOWN_EXTRAS
    ALLOWED_HTML_TAGS = ALLOWED_HTML_TAGS
    ALLOWED_HTML_ATTRS = ALLOWED_HTML_ATTRS

    @staticmethod
    def process_markdown(text, icon_set='ps4', enable_spoilers=False):
        """Process markdown text to HTML with sanitization.

        ``icon_set`` selects the PlayStation controller-icon variant for
        ``:shortcode:`` tokens (``'ps4'`` or ``'ps5'``). Pass the value of
        ``Game.controller_icon_set`` from the calling context so glyphs match
        the game's hardware.

        ``enable_spoilers`` opts in to Discord-style ``||spoiler||`` syntax.
        Off by default so callers like reviews keep their existing behavior;
        roadmap templates pass ``True`` via ``render_roadmap_markdown``.
        """
        if not text or not text.strip():
            return ''

        try:
            # Pre-process: Convert __text__ to <u>text</u> for underline
            text = re.sub(r'(?<![_\w])__([^_\n]+?)__(?![_\w])', r'<u>\1</u>', text)

            # Replace controller-icon shortcodes (:square:, :l2:, etc.) before
            # markdown processing so the injected <img> survives untouched.
            text = render_shortcodes(text, icon_set=icon_set)

            html_output = markdown2.markdown(text, extras=MARKDOWN_EXTRAS)

            clean_html = bleach.clean(
                html_output,
                tags=ALLOWED_HTML_TAGS,
                attributes=ALLOWED_HTML_ATTRS,
                protocols=['http', 'https'],
                strip=True
            )

            # Loose-list fix: markdown2 wraps every item of a list in <p> when
            # any blank line appears between items. With list-inside styling +
            # paragraph margin, that strands the bullet on a line above its text.
            # Strip the wrapper for single-paragraph items; the tempered inner
            # group refuses to match across <p>/</p> so multi-paragraph items
            # are left untouched.
            clean_html = re.sub(
                r'<li>\s*<p>((?:(?!<p>|</p>).)*)</p>\s*</li>',
                r'<li>\1</li>',
                clean_html,
                flags=re.DOTALL,
            )

            # GitHub-style callouts: convert blockquotes whose first paragraph
            # opens with [!NOTE]/[!TIP]/[!WARNING]/[!IMPORTANT] into a styled
            # callout div. Done BEFORE the blockquote-styling regex below so
            # plain blockquotes (anything not opening with a [!TYPE] marker)
            # still get the regular styling pass.
            clean_html = _apply_callouts(clean_html)

            # Style links: external get target="_blank", internal anchors stay on-page
            def _style_link(m):
                pre, href, post = m.group(1), m.group(2), m.group(3)
                if href.startswith('#trophy-guide-'):
                    return f'<a {pre}href="{href}"{post} class="trophy-mention">'
                if href.startswith('#'):
                    return f'<a {pre}href="{href}"{post} class="link link-primary">'
                return f'<a {pre}href="{href}"{post} class="link link-primary" target="_blank" rel="noopener noreferrer">'

            clean_html = re.sub(
                r'<a\s+([^>]*?)href="([^"]*)"([^>]*?)>',
                _style_link,
                clean_html
            )
            clean_html = re.sub(
                r'<blockquote>',
                r'<blockquote class="border-l-4 border-base-300 pl-4 py-2 my-2 italic text-base-content/80 bg-base-200/30">',
                clean_html
            )
            clean_html = re.sub(r'<ul>', r'<ul class="list-disc list-inside ml-4 my-2 space-y-1">', clean_html)
            clean_html = re.sub(r'<ol>', r'<ol class="list-decimal list-inside ml-4 my-2 space-y-1">', clean_html)
            clean_html = re.sub(r'<p>', r'<p class="my-2">', clean_html)
            # Style large content images, but skip <img> that already carry a
            # class attribute (controller-icon shortcodes ship with class="ps-icon"
            # and would break with a duplicate class attribute).
            clean_html = re.sub(
                r'<img (?![^>]*class=)',
                r'<img class="rounded-lg border border-base-content/10 max-w-full md:max-w-[75%] h-auto my-3 mx-auto block" ',
                clean_html
            )

            if enable_spoilers:
                clean_html = _apply_spoilers(clean_html)

            return clean_html.strip()
        except Exception:
            import logging
            logging.getLogger('psn_api').exception("Markdown processing failed")
            from trophies.util_modules.language import escape_html
            return escape_html(text)


# Match Discord's ``||spoiler||`` syntax. Non-greedy + DOTALL so a single
# spoiler can wrap inline markup (bold, links, controller icons) and even span
# lines if an author breaks the content across them.
_SPOILER_RE = re.compile(r'\|\|(.+?)\|\|', re.DOTALL)
# Split on code/pre regions so literal ``||`` inside a fenced block stays raw.
# bleach has already balanced these tags, so a simple non-greedy alternation is
# sufficient.
_CODE_REGION_RE = re.compile(
    r'(<(?:code|pre)\b[^>]*>.*?</(?:code|pre)>)',
    re.DOTALL | re.IGNORECASE,
)
# Image-only spoiler content: a single <img> with optional surrounding
# whitespace or <br>. Inline spoiler spans wrapping a block image generate
# tiny clickable line-fragments above and below the image (because <span> is
# inline but the img has display:block). Detecting this case lets us swap to
# a block-level wrapper that matches the image's bounding box.
_SPOILER_IMAGE_ONLY_RE = re.compile(
    r'\s*(?:<br\s*/?>\s*)*<img\b[^>]*>\s*(?:<br\s*/?>\s*)*',
    re.IGNORECASE,
)


# GitHub-style callout markers: ``> [!NOTE]`` / ``[!TIP]`` / ``[!WARNING]`` /
# ``[!IMPORTANT]`` on the first line of a blockquote. The blockquote element
# itself is the wrapper; markdown2 renders the marker as plain text inside the
# first ``<p>``, which we then strip and replace the whole blockquote with a
# semantic ``<div class="callout callout-{type}">`` so the styling pipeline
# treats it differently from a plain quote.
_CALLOUT_RE = re.compile(
    r'<blockquote>\s*<p>\s*\[!(NOTE|TIP|WARNING|IMPORTANT)\]\s*'
    r'(?:<br\s*/?>)?\s*'   # optional <br> after the marker (break-on-newline)
    r'(.*?)</p>\s*'        # rest of the first <p> (may be empty if marker was alone)
    r'(.*?)</blockquote>', # any subsequent <p>/list/etc. inside the blockquote
    re.DOTALL | re.IGNORECASE,
)

# Icons sized to sit in a tight callout header next to the type label. Match
# the stroke style used elsewhere in the app (Heroicons-ish, stroke-width=2).
_CALLOUT_ICONS = {
    'note': (
        '<svg xmlns="http://www.w3.org/2000/svg" class="callout-icon w-4 h-4" '
        'fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">'
        '<path stroke-linecap="round" stroke-linejoin="round" '
        'd="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"/></svg>'
    ),
    'tip': (
        '<svg xmlns="http://www.w3.org/2000/svg" class="callout-icon w-4 h-4" '
        'fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">'
        '<path stroke-linecap="round" stroke-linejoin="round" '
        'd="M9.663 17h4.673M12 3v1m6.364 1.636l-.707.707M21 12h-1M4 12H3m3.343-5.657'
        'l-.707-.707m2.828 9.9a5 5 0 117.072 0l-.548.547A3.374 3.374 0 0014 18.469V19'
        'a2 2 0 11-4 0v-.531c0-.895-.356-1.754-.988-2.386l-.548-.547z"/></svg>'
    ),
    'warning': (
        '<svg xmlns="http://www.w3.org/2000/svg" class="callout-icon w-4 h-4" '
        'fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">'
        '<path stroke-linecap="round" stroke-linejoin="round" '
        'd="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3'
        'L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z"/></svg>'
    ),
    'important': (
        '<svg xmlns="http://www.w3.org/2000/svg" class="callout-icon w-4 h-4" '
        'fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">'
        '<path stroke-linecap="round" stroke-linejoin="round" '
        'd="M11 5.882V19.24a1.76 1.76 0 01-3.417.592l-2.147-6.15M18 13a3 3 0 100-6'
        'M5.436 13.683A4.001 4.001 0 017 6h1.832c4.1 0 7.625-1.234 9.168-3v14'
        'c-1.543-1.766-5.067-3-9.168-3H7a3.988 3.988 0 01-1.564-.317z"/></svg>'
    ),
}


def _build_callout(match):
    """Replacement function for ``_CALLOUT_RE``.

    Splits the captured blockquote into the header (icon + label) and body
    (remaining HTML, with any inline content from the marker line preserved
    as its own paragraph). Output is a div with semantic classes so CSS can
    style each type independently.
    """
    callout_type = match.group(1).lower()
    inline_after_marker = match.group(2).strip()
    rest_paragraphs = match.group(3).strip()

    body_parts = []
    if inline_after_marker:
        body_parts.append(f'<p>{inline_after_marker}</p>')
    if rest_paragraphs:
        body_parts.append(rest_paragraphs)
    body_html = ''.join(body_parts) or '<p></p>'

    icon_svg = _CALLOUT_ICONS.get(callout_type, '')
    label = callout_type.title()

    return (
        f'<div class="callout callout-{callout_type}">'
        f'<div class="callout-header">{icon_svg}'
        f'<span class="callout-label">{label}</span></div>'
        f'<div class="callout-body">{body_html}</div>'
        f'</div>'
    )


def _apply_callouts(html):
    """Convert ``> [!TYPE]`` blockquotes into semantic callout divs.

    Runs between the loose-list fix and the blockquote-styling regex so plain
    blockquotes still get their generic styling, but ones that opened with a
    ``[!TYPE]`` marker emerge as a different element entirely. Available types:
    NOTE, TIP, WARNING, IMPORTANT.
    """
    return _CALLOUT_RE.sub(_build_callout, html)


def _build_spoiler(match):
    """Replacement for ``_SPOILER_RE``.

    Emits an inline ``<span class="spoiler">`` for the common text case, but
    swaps to ``spoiler-block`` when the captured content is a single image
    with optional whitespace/<br>. The image variant is needed because the
    inner ``<img>`` has ``display: block`` (from the existing image-styling
    pass), which makes a plain inline span generate empty clickable line
    fragments above and below the image instead of a single block-aligned bar.
    """
    content = match.group(1)
    extra_class = ' spoiler-block' if _SPOILER_IMAGE_ONLY_RE.fullmatch(content) else ''
    return (
        f'<span class="spoiler{extra_class}" role="button" tabindex="0" '
        f'aria-pressed="false" aria-label="Spoiler, click to reveal" '
        f'title="Click to reveal">{content}</span>'
    )


def _apply_spoilers(html):
    """Wrap ``||text||`` runs in spoiler spans, leaving code blocks alone.

    Runs after bleach so we don't have to add ``span`` to the allowlist (which
    would let authors hand-write their own spans). The transform splits the
    document on code/pre regions and only rewrites the parts in between, so
    fenced code containing literal ``||`` survives unchanged.
    """
    parts = _CODE_REGION_RE.split(html)
    # Even-indexed parts are outside code; odd-indexed are the captured code
    # regions themselves and must be left untouched.
    for i in range(0, len(parts), 2):
        parts[i] = _SPOILER_RE.sub(_build_spoiler, parts[i])
    return ''.join(parts)
