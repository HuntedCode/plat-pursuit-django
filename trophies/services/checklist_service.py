# Checklist service removed during roadmap migration.
# DB tables retained for data preservation.
#
# process_markdown() kept here as a standalone function because it is
# used by review_views.py and markdown_filters.py (not checklist-specific).
import re

import bleach
import markdown2

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
    def process_markdown(text):
        """Process markdown text to HTML with sanitization."""
        if not text or not text.strip():
            return ''

        try:
            # Pre-process: Convert __text__ to <u>text</u> for underline
            text = re.sub(r'(?<![_\w])__([^_\n]+?)__(?![_\w])', r'<u>\1</u>', text)

            html_output = markdown2.markdown(text, extras=MARKDOWN_EXTRAS)

            clean_html = bleach.clean(
                html_output,
                tags=ALLOWED_HTML_TAGS,
                attributes=ALLOWED_HTML_ATTRS,
                protocols=['http', 'https'],
                strip=True
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
            clean_html = re.sub(
                r'<img ',
                r'<img class="rounded-lg border border-base-content/10 max-w-full md:max-w-[75%] h-auto my-3 mx-auto block" ',
                clean_html
            )

            return clean_html.strip()
        except Exception:
            import logging
            logging.getLogger('psn_api').exception("Markdown processing failed")
            from trophies.util_modules.language import escape_html
            return escape_html(text)
