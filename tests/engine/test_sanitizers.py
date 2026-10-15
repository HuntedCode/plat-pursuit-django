"""The two HTML sanitizers: comments (no HTML at all) and markdown (a fixed allowlist).

Both run on user input that every visitor sees (comments; roadmaps, reviews and guides), so these
pin what must and must not get through. They assert on behavior, not on exact markup, so a
sanitizer upgrade that only changes serialization (nh3 writes `&quot;` as `"` in text) passes,
while one that lets a tag, attribute or URL scheme through fails. The sanitizer moved from bleach
to nh3 in 2026-10 (bleach is unmaintained); these were written then, against both.
"""
import pytest

from trophies.services.checklist_service import ChecklistService
from trophies.services.comment_service import CommentService

clean_comment = CommentService.sanitize_text


def render(markdown):
    return ChecklistService.process_markdown(markdown, enable_spoilers=True)


# --- comments: plain text only ----------------------------------------------------------------

@pytest.mark.parametrize('text', ['Great list! Thanks.', "Tom & Jerry's \"best\" run", '5 < 6 and 7 > 3', 'emoji \U0001F3C6 ok'])
def test_a_plain_comment_is_kept_as_typed(text):
    assert clean_comment(text) == text


def test_tags_are_removed_but_their_text_stays():
    assert clean_comment('<b>bold</b> claim and <a href="https://x.com">a link</a>') == 'bold claim and a link'


@pytest.mark.parametrize('text', [
    '<script>alert(1)</script>after', '<style>body{display:none}</style>after',
    '<img src=x onerror=alert(1)>after', '<!-- hidden -->after',
])
def test_scripts_styles_images_and_html_comments_leave_no_trace(text):
    assert clean_comment(text) == 'after'


def test_surrounding_whitespace_is_trimmed():
    assert clean_comment('   padded   ') == 'padded'


@pytest.mark.parametrize('text', ['hold <R2 then press X', 'a<b', '<unclosed', 'line one\nline <two', 'I <3 this', 'x < y > z'])
def test_an_unclosed_angle_bracket_keeps_the_rest_of_the_comment(text):
    """An HTML parser reads `<R2 then press X` as an unfinished tag and drops it; bleach kept the
    text and gamers type these, so the sanitizer escapes such a `<` before parsing."""
    assert clean_comment(text) == text


def test_a_closed_tag_after_an_unclosed_bracket_is_still_stripped():
    assert clean_comment('a <b and <i>x</i> tail') == 'a x tail'


# --- markdown: the allowlist ------------------------------------------------------------------

def test_authored_formatting_survives():
    html = render('## Section\n\nSome **bold**, *italic*, ~~gone~~, `code`.\n\n| A | B |\n|:--|--:|\n| x | y |')
    for fragment in ['<h2', '<strong>bold</strong>', '<em>italic</em>', '<code>code</code>', '<table', 'class="text-right"']:
        assert fragment in html, fragment


@pytest.mark.parametrize('markdown, gone', [
    ('[x](javascript:alert(1))', 'javascript:'),
    ('<a href="JaVaScRiPt:alert(1)">x</a>', 'javascript:'),
    ('<a href="data:text/html,hi">x</a>', 'data:'),
    ('![x](javascript:alert(1))', 'javascript:'),
    ('<img src="https://x/a.png" onerror="alert(1)">', 'onerror'),
    ('<a href="https://x.com" onclick="steal()">x</a>', 'onclick'),
    ('<p style="color:red">x</p>', 'style='),
    ('<p id="takeover">x</p>', 'id='),
    ('<iframe src="https://evil"></iframe>', '<iframe'),
    ('<script>alert(1)</script>', 'alert(1)'),
    ('<h1>Rival title</h1>', '<h1'),
])
def test_dangerous_markup_is_removed(markdown, gone):
    html = render(markdown).lower()
    # process_markdown falls back to escaping the raw text if rendering fails, which would also
    # hide every tag; a rendered result proves the sanitizer, not the fallback, removed it.
    assert '&lt;' not in html
    assert gone not in html


def test_http_https_and_relative_links_are_kept():
    html = render('[a](https://platpursuit.com/x?a=1&b=2) [b](http://example.com) [c](/games/123/) ![d](/media/a.png)')
    for url in ['href="https://platpursuit.com/x?a=1&amp;b=2"', 'href="http://example.com"', 'href="/games/123/"', 'src="/media/a.png"']:
        assert url in html, url


def test_external_links_always_get_the_sites_own_target_and_rel():
    """An author's `rel`/`target` used to survive and, coming first, win over the site's: a
    `rel="opener"` link let the opened page redirect the reader's tab (reverse tabnabbing)."""
    html = render('<a href="https://evil.example" rel="opener" target="_self" title="t">x</a>')
    assert 'opener"' not in html.replace('noopener', '')
    assert '_self' not in html
    assert html.count('rel=') == 1 and 'rel="noopener noreferrer"' in html
    assert 'target="_blank"' in html and 'title="t"' in html


def test_an_on_page_anchor_gets_no_target():
    html = render('[jump](#section-2)')
    assert 'href="#section-2"' in html and 'target=' not in html


def test_classes_are_allowed_on_any_tag():
    assert 'class="rounded"' in render('<img src="https://x/a.png" class="rounded">')


def test_unclosed_tags_are_closed():
    """The spoiler and code-region passes after sanitizing rely on balanced tags."""
    html = render('Unclosed <strong>bold and <em>both')
    assert html.count('<strong>') == html.count('</strong>')
    assert html.count('<em>') == html.count('</em>')


def test_spoilers_still_wrap_after_sanitizing():
    assert 'spoiler' in render('This is ||a spoiler|| inside.')
