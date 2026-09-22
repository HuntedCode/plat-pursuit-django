"""No module defines the same top-level name twice.

This exists because it happened, on `main`, and nothing caught it. `trophies/views/badge_views.py`
carried 620 duplicated lines: `GroupBadgeInspectView`, `BadgeDetailView`, `BadgeRanksPanelView` and
`OverallBadgeLeaderboardsView` were each defined twice, and Python bound the second of each. The first
copy of all four was unreachable.

Nothing announced it. The duplicate was byte-identical to the live copy, so the site behaved correctly,
`manage.py check` passed, and the full suite stayed green -- there is no behavioural difference for a
test to catch. It was found only because an edit anchored on a line inside the dead copy and the tool
reported two matches.

That is the actual damage, and it is the reason this guard is worth its runtime. A shadowed copy is a
trap for the NEXT reader: `grep` finds the dead definition first, an editor opens at it first, and a
change made there is correct, committed, reviewed, and inert. The failure is silent in both directions
-- the code does nothing and the test suite says everything is fine.

Only DIRECT children of the module body count. A definition inside `if TYPE_CHECKING:`, a `try/except
ImportError` fallback, or a platform branch is a deliberate alternative binding, not a shadow, and each
lives one level down in the tree.
"""
import ast

from tests.engine._tree import source_files

#: Migrations are excluded per-caller rather than in the shared list: they are generated, never read
#: for their definitions, and a `Migration` class per file is the whole convention. The other
#: exclusions (including `.claude`, which is where an agent worktree puts a second copy of the repo)
#: live in `_tree.SKIP_DIRS`, because three tests had three drifting copies of them.
DEFINITIONS = (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)


def _python_files():
    return source_files('*.py', skip_dirs={'migrations'})


def _shadowed(tree):
    """Top-level names bound by a def/class more than once, with the line of each binding."""
    seen = {}
    for node in tree.body:
        if isinstance(node, DEFINITIONS):
            seen.setdefault(node.name, []).append(node.lineno)
    return {name: lines for name, lines in seen.items() if len(lines) > 1}


def test_no_module_defines_the_same_name_twice():
    offences = []
    for path, rel in _python_files():
        try:
            tree = ast.parse(path.read_text(encoding='utf-8'))
        except (SyntaxError, UnicodeDecodeError, OSError):
            continue          # not parseable as this interpreter's Python; not this guard's business
        for name, lines in sorted(_shadowed(tree).items()):
            kept = lines[-1]
            dead = ', '.join(str(n) for n in lines[:-1])
            offences.append(
                f'{rel}: {name} is defined at lines {dead}, {kept}. Python binds the one at {kept}; '
                f'the rest are unreachable and any edit made to them does nothing.'
            )

    assert not offences, 'shadowed top-level definitions:\n  ' + '\n  '.join(offences)
