"""Controller-icon shortcode rendering for roadmap markdown.

Authors type tokens like ``:square:`` or ``:l2:`` in step bodies, trophy guide
bodies, and general tips. At render time those tokens become small inline
``<img>`` tags pointing at SVGs in ``static/images/controller/``.

The PS4 vs PS5 selection is driven by ``Game.controller_icon_set``: only the
system buttons (Share/Create, Options, Touchpad) live in platform folders;
universal glyphs (face buttons, D-pad, triggers, sticks, PS5-exclusive
Create/Mute) live in ``shared/``.

Replacement runs *before* markdown conversion so the injected ``<img>`` survives
markdown2 untouched. Shortcodes inside fenced or inline code blocks are left
literal.
"""
import json
import re
from functools import lru_cache
from pathlib import Path

from django.conf import settings

SHORTCODE_PATTERN = re.compile(r':([a-z0-9-]+):')
CODE_BLOCK_PATTERN = re.compile(r'```[\s\S]*?```|`[^`\n]+`')
SENTINEL_PREFIX = 'PSICON_CODE_'
SENTINEL_SUFFIX = ''


@lru_cache(maxsize=1)
def _load_manifest():
    """Read manifest.json once and cache the parsed dict."""
    manifest_path = Path(settings.BASE_DIR) / 'static' / 'images' / 'controller' / 'manifest.json'
    with manifest_path.open('r', encoding='utf-8') as fh:
        return json.load(fh)


def _resolve(shortcode):
    """Return (canonical_name, spec) for a shortcode, or (None, None) if unknown."""
    manifest = _load_manifest()
    canonical = manifest['aliases'].get(shortcode, shortcode)
    spec = manifest['shortcodes'].get(canonical)
    if not spec:
        return None, None
    return canonical, spec


def render_shortcodes(text, icon_set='ps4'):
    """Replace ``:shortcode:`` tokens with inline ``<img>`` tags.

    Code blocks (fenced and inline) are stashed before replacement and
    restored after, so authors can show literal ``:square:`` tokens by
    wrapping them in backticks.
    """
    if not text or ':' not in text:
        return text

    if icon_set not in ('ps4', 'ps5'):
        icon_set = 'ps4'

    code_blocks = []

    def stash(match):
        code_blocks.append(match.group(0))
        return f'{SENTINEL_PREFIX}{len(code_blocks) - 1}{SENTINEL_SUFFIX}'

    text = CODE_BLOCK_PATTERN.sub(stash, text)

    def replace(match):
        canonical, spec = _resolve(match.group(1).lower())
        if not spec:
            return match.group(0)
        folder = 'shared' if spec['set'] == 'shared' else icon_set
        url = f'{settings.STATIC_URL}images/controller/{folder}/{canonical}.svg'
        return f'<img class="ps-icon" src="{url}" alt="{spec["label"]}" title="{spec["label"]}" />'

    text = SHORTCODE_PATTERN.sub(replace, text)

    for idx, block in enumerate(code_blocks):
        text = text.replace(f'{SENTINEL_PREFIX}{idx}{SENTINEL_SUFFIX}', block)

    return text
