"""Every module a hot path imports must actually exist (2026-08).

Django's system check and the test suite both import modules at MODULE level. A function-level
`from x import y` is invisible to both until the line runs -- and `token_keeper._job_sync_complete` is
full of them, wrapped in a `try/except Exception` that turns a `ModuleNotFoundError` into a log line and
skips everything after it.

That is not hypothetical. Deleting `dashboard_service` left this behind in `_job_sync_complete`:

    from trophies.services.dashboard_service import invalidate_dashboard_cache
    invalidate_dashboard_cache(profile_id)

`manage.py check` passed. The whole suite passed. Every sync would have raised, been swallowed, and
silently skipped the rest of the job -- discoverable only by reading the logs of a worker nobody watches.

So this walks the AST of the files that do the most deferred importing and resolves every module they
name, without executing anything. It is the cheap general form of the specific gap the 5a audit found:
the sync path is barely reachable from tests, so its imports need checking some other way.
"""
import ast
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

#: Hot paths that import lazily and are poorly covered by tests. Not the whole tree: a full sweep would
#: mostly re-assert what module-level imports already prove at startup.
#:
#: `admin.py` is here for the same reason `token_keeper` is: its actions import services inside the method,
#: so a deleted service leaves a registration that looks fine until a staff member clicks the button. The
#: 5b cutover left exactly that (a `ProfileGamification` action importing the deleted `xp_service`).
DEFERRED_IMPORT_HEAVY = [
    'trophies/token_keeper.py',
    'trophies/services/badge_apply.py',
    'trophies/services/badge_xp.py',
    'trophies/services/badge_leaderboards.py',
    'core/services/home_service.py',
    'trophies/psn_manager.py',
    'trophies/admin.py',
]


def _imported_modules(path):
    """Every module name imported in the file, module-level and function-level alike."""
    tree = ast.parse(path.read_text(encoding='utf-8'))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            # `from . import x` has no module and resolves relative to the package; out of scope here,
            # and the project does not use it in these files.
            if node.module and not node.level:
                names.add(node.module)
    return names


@pytest.mark.parametrize('rel', DEFERRED_IMPORT_HEAVY)
def test_every_module_a_hot_path_imports_still_exists(rel):
    """Resolves each imported module WITHOUT importing it -- `find_spec` reads the finder, so a module
    with side effects (token_keeper registers an atexit handler) is not executed by this test."""
    path = ROOT / rel
    if not path.exists():
        pytest.skip(f'{rel} has been deleted')

    missing = []
    for name in sorted(_imported_modules(path)):
        try:
            if importlib.util.find_spec(name) is None:
                missing.append(name)
        except (ImportError, ModuleNotFoundError, ValueError):
            # A parent package that no longer exists raises rather than returning None.
            missing.append(name)

    assert not missing, (
        f'{rel} imports modules that no longer exist: {missing}. If any of these sit inside a '
        f'`try/except Exception`, the failure is a log line and a silently skipped code path.'
    )
