"""Repo-wide template checks for mistakes that render as VISIBLE TEXT rather than failing.

Nothing here needs a database or a request. These are the failure modes that don't raise: the page
loads, the tests pass, and the comment you wrote is sitting in the middle of the UI.
"""
import re
from pathlib import Path

import pytest

TEMPLATES = Path(__file__).resolve().parents[2] / 'templates'

#: `{#` ... `#}` spanning a newline. Django's lexer matches `{#.*?#}` WITHOUT DOTALL, so a multi-line
#: one is never recognised as a comment and every character of it renders.
MULTILINE_HASH_COMMENT = re.compile(r'\{#(?:(?!#\}).)*?\n(?:(?!#\}).)*?#\}', re.S)


def _templates():
    return sorted(TEMPLATES.rglob('*.html'))


def test_no_multiline_hash_comments():
    """`{# #}` is SINGLE-LINE only. Spanning a newline leaks the comment onto the page -- and into a
    <script> block it lands mid-object-literal and takes the page's JS down with it, which is how the
    retired platinum grid was sitting. Use `{% comment %} ... {% endcomment %}` for anything multi-line.

    This has bitten repeatedly, always silently, which is why it is a test and not a note."""
    offenders = []
    for path in _templates():
        text = path.read_text(encoding='utf-8')
        for match in MULTILINE_HASH_COMMENT.finditer(text):
            line = text[:match.start()].count('\n') + 1
            snippet = ' '.join(match.group(0).split())[:70]
            offenders.append(f'{path.relative_to(TEMPLATES)}:{line}  {snippet}...')

    assert not offenders, (
        'Multi-line {# #} comments render to the page. Use {% comment %}:\n  '
        + '\n  '.join(offenders)
    )


STATIC_JS = Path(__file__).resolve().parents[2] / 'static' / 'js'


def test_the_quick_rate_hosts_do_not_re_implement_the_driver():
    """The quick-rate modal (`#gd-qr-modal`) is composed on two surfaces -- Game Detail's Ratings tab and
    the plat-card share modal -- and each used to carry its own driver. They had already drifted: only
    Game Detail surfaced the endpoint's field-level `errors`, so a blurb rejected for a banned word
    explained itself on one page and failed generically on the other.

    Both now call `PlatPursuit.QuickRate`, so neither may POST the rating itself. Scoped to these two
    files on purpose -- other surfaces (the dashboard's legacy prompt, Rate My Games, the review hub)
    legitimately post ratings from their own forms and are not what this guards."""
    for name in ('game-detail.js', 'plat-cards.js'):
        src = (STATIC_JS / name).read_text(encoding='utf-8')
        assert '/group/' not in src or '/rate/' not in src, (
            f'{name} posts a rating directly again -- it should call PlatPursuit.QuickRate'
        )
        assert 'QuickRate' in src, f'{name} no longer uses the shared controller'


def test_both_hosts_load_the_shared_controller():
    """`quick-rate.js` defines PlatPursuit.QuickRate and must load BEFORE the page controller that calls
    it, or the rate button is silently inert."""
    root = Path(__file__).resolve().parents[2] / 'templates'
    for page, controller in [
        ('trophies/game_detail.html', 'js/game-detail.js'),
        ('shareables/plat_cards.html', 'js/plat-cards.js'),
    ]:
        html = (root / page).read_text(encoding='utf-8')
        assert 'js/quick-rate.js' in html, page
        assert html.index('js/quick-rate.js') < html.index(controller), f'{page}: load order'


@pytest.mark.parametrize('name', [
    'shareables/partials/share_modal.html',
    'partials/rate_before_download_modal.html',
    'shareables/plat_card.html',
    'trophies/partials/game_detail/quick_rate_modal.html',
])
def test_share_flow_templates_have_balanced_comment_tags(name):
    """An unclosed `{% comment %}` swallows the rest of the file instead of erroring -- controls simply
    stop existing. Spot-checked on the share flow, where the comment density is highest."""
    text = (TEMPLATES / name).read_text(encoding='utf-8')

    assert text.count('{% comment %}') == text.count('{% endcomment %}'), name
