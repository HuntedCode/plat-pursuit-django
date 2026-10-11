"""The Career page's two sources, for the source-text pins (there is no JS runner).

`templates/trophies/career.html` is the MARKUP. Its script lives in `static/js/career.js` (moved out of
the template 2026-10). A pin about the script must read the script: read off the template instead, an
`assert 'x' not in ...` passes on a file that no longer holds any JS at all, which is the worst kind of
green. `test_career_view.test_the_board_script_lives_in_career_js_not_the_template` keeps the two apart.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CAREER_TEMPLATE = ROOT / 'templates' / 'trophies' / 'career.html'
CAREER_SCRIPT = ROOT / 'static' / 'js' / 'career.js'


def career_template():
    return CAREER_TEMPLATE.read_text(encoding='utf-8')


def career_script():
    return CAREER_SCRIPT.read_text(encoding='utf-8')
