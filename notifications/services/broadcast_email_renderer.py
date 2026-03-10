"""
Broadcast email renderer: converts in-app notification content to email-safe HTML.

This is the server-side equivalent of the JS formatStructuredContent() and
renderStructuredSections() in notification-inbox.js. It renders the same
mini-markup syntax (*bold*, _italic_, `code`, [link](url), - bullets)
into inline-styled HTML safe for email clients.
"""
import re
import logging

from django.utils.html import escape

logger = logging.getLogger(__name__)

PRIORITY_COLORS = {
    'low':    ('#667eea', '#764ba2'),
    'normal': ('#667eea', '#764ba2'),
    'high':   ('#e53e3e', '#dd6b20'),
    'urgent': ('#e53e3e', '#dd6b20'),
}


def render_mini_markup(text):
    """
    Convert mini-markup text to email-safe HTML.

    Supported syntax (matches JS formatStructuredContent):
      *bold*     -> <strong>
      _italic_   -> <em>
      `code`     -> <code>
      [text](url) -> <a>
      - item     -> <ul><li>

    Input is HTML-escaped first, then markup transforms are applied.
    """
    if not text:
        return ''

    formatted = escape(text)

    # Bold: *text*
    formatted = re.sub(r'\*([^*]+)\*', r'<strong>\1</strong>', formatted)

    # Italic: _text_
    formatted = re.sub(r'_([^_]+)_', r'<em>\1</em>', formatted)

    # Code: `text`
    formatted = re.sub(
        r'`([^`]+)`',
        r'<code style="background-color:#e8e8e8;padding:2px 6px;border-radius:4px;'
        r'font-family:monospace;font-size:13px;">\1</code>',
        formatted,
    )

    # Links: [text](url) - only allow http/https URLs
    def _replace_link(match):
        text, url = match.group(1), match.group(2)
        if url.startswith(('http://', 'https://')):
            return f'<a href="{url}" style="color:#667eea;text-decoration:underline;">{text}</a>'
        return match.group(0)  # leave non-http links as-is

    formatted = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', _replace_link, formatted)

    # Bullet lists and paragraphs
    lines = formatted.split('\n')
    result = ''
    in_list = False

    for line in lines:
        trimmed = line.strip()
        if trimmed.startswith('- '):
            if not in_list:
                result += '<ul style="margin:8px 0;padding-left:20px;">'
                in_list = True
            result += f'<li style="margin-bottom:4px;">{trimmed[2:]}</li>'
        else:
            if in_list:
                result += '</ul>'
                in_list = False
            if trimmed:
                result += f'<p style="margin:4px 0;">{line}</p>'

    if in_list:
        result += '</ul>'

    return result


def render_sections_to_email_html(sections, accent_color='#667eea'):
    """
    Render a list of section dicts to email-safe HTML cards.

    Each section: {id, header, icon, content, order}
    """
    if not sections or not isinstance(sections, list):
        return ''

    sorted_sections = sorted(sections, key=lambda s: s.get('order', 0))
    html_parts = []

    for section in sorted_sections:
        icon = escape(section.get('icon', ''))
        header = escape(section.get('header', ''))
        content_html = render_mini_markup(section.get('content', ''))

        html_parts.append(f'''
        <div style="border-left:4px solid {accent_color};background-color:#f8f9fa;
                    border-radius:0 8px 8px 0;padding:16px 20px;margin-bottom:16px;">
            <div style="font-size:16px;font-weight:700;color:#333333;margin:0 0 8px 0;
                        padding-bottom:8px;border-bottom:1px solid #e0e0e0;">
                <span style="font-size:20px;margin-right:8px;vertical-align:middle;">{icon}</span>
                {header}
            </div>
            <div style="font-size:14px;line-height:1.6;color:#555555;">
                {content_html}
            </div>
        </div>''')

    return '\n'.join(html_parts)


def render_message_to_email_html(message):
    """
    Render notification message body for email.
    Preserves whitespace/newlines (whitespace-pre-wrap equivalent).
    """
    if not message:
        return ''
    return escape(message).replace('\n', '<br>')


def render_detail_to_email_html(detail):
    """
    Render legacy markdown detail to email-safe HTML.
    Uses markdown.markdown() with standard extensions.
    """
    if not detail:
        return ''
    try:
        import markdown
        return markdown.markdown(
            detail,
            extensions=['extra', 'nl2br', 'sane_lists'],
        )
    except Exception:
        logger.exception("Failed to render broadcast detail markdown")
        return escape(detail).replace('\n', '<br>')


def build_broadcast_email_context(
    title, message, icon, priority, sections, detail,
    banner_image, action_url, action_text,
    username, site_url, preference_url,
):
    """
    Build the full template context dict for broadcast.html.

    Calls the renderers above and assembles all pieces needed
    by the broadcast email template.

    Returns:
        dict with all template context variables.
    """
    gradient_start, gradient_end = PRIORITY_COLORS.get(
        priority, PRIORITY_COLORS['normal']
    )
    accent_color = gradient_start
    is_urgent = priority in ('high', 'urgent')

    message_html = render_message_to_email_html(message)

    has_sections = bool(sections)
    sections_html = render_sections_to_email_html(sections, accent_color) if has_sections else ''
    detail_html = render_detail_to_email_html(detail) if not has_sections else ''

    banner_image_url = None
    if banner_image:
        try:
            banner_image_url = banner_image.url
            if banner_image_url and not banner_image_url.startswith(('http://', 'https://')):
                banner_image_url = f"{site_url}{banner_image_url}"
        except (ValueError, AttributeError):
            pass

    return {
        'subject': title,
        'icon': icon or '\U0001F4E2',  # megaphone fallback
        'priority': priority,
        'is_urgent': is_urgent,
        'accent_color': accent_color,
        'accent_gradient_start': gradient_start,
        'accent_gradient_end': gradient_end,
        'message_html': message_html,
        'has_sections': has_sections,
        'sections_html': sections_html,
        'detail_html': detail_html,
        'banner_image_url': banner_image_url,
        'cta_url': action_url or '',
        'cta_text': action_text or '',
        'username': username,
        'site_url': site_url,
        'preference_url': preference_url,
    }
