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
_SPOILER_REPLACEMENT = (
    r'<span class="spoiler" role="button" tabindex="0" '
    r'aria-pressed="false" aria-label="Spoiler, click to reveal" '
    r'title="Click to reveal">\1</span>'
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
        parts[i] = _SPOILER_RE.sub(_SPOILER_REPLACEMENT, parts[i])
    return ''.join(parts)
