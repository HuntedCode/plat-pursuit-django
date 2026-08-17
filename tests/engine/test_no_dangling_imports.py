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


def _imports(path):
    """Every (module, [names]) imported in the file, module-level and function-level alike."""
    tree = ast.parse(path.read_text(encoding='utf-8'))
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out.extend((a.name, []) for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            # `from . import x` has no module and resolves relative to the package; out of scope here,
            # and the project does not use it in these files.
            if node.module and not node.level:
                out.append((node.module, [a.name for a in node.names]))
    return out


def _top_level_names(module_name):
    """Names a module defines at top level, WITHOUT importing it -- parsed from source, so a module with
    side effects (token_keeper registers an atexit handler) is never executed."""
    spec = importlib.util.find_spec(module_name)
    if spec is None or not spec.origin or not spec.origin.endswith('.py'):
        return None            # namespace package / C extension / stdlib builtin: cannot check names
    tree = ast.parse(Path(spec.origin).read_text(encoding='utf-8'))
    names = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            names.update(t.id for t in node.targets if isinstance(t, ast.Name))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            names.update(a.asname or a.name.split('.')[0] for a in node.names)
        elif isinstance(node, ast.If):
            # `if TYPE_CHECKING:` and friends; be permissive rather than raise false alarms.
            for sub in ast.walk(node):
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    names.add(sub.name)
    return names


@pytest.mark.parametrize('rel', DEFERRED_IMPORT_HEAVY)
def test_every_module_a_hot_path_imports_still_exists(rel):
    """Resolves each imported module WITHOUT importing it -- `find_spec` reads the finder, so a module
    with side effects is not executed by this test."""
    path = ROOT / rel
    if not path.exists():
        pytest.skip(f'{rel} has been deleted')

    missing = []
    for name, _ in _imports(path):
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


@pytest.mark.parametrize('rel', DEFERRED_IMPORT_HEAVY)
def test_every_name_a_hot_path_imports_still_exists(rel):
    """The other half, and the more likely one from here.

    The originating bug was a whole-MODULE deletion, but the same cutover also deleted FUNCTIONS out of
    modules that survived (`verification_service`'s Redis backfill, `psn_api_service`'s badge-group
    creator, several signal helpers). `from x import gone_function` resolves the module fine and explodes
    only when the line runs -- identical failure mode, invisible to the module-level check above.

    Only first-party modules are checked; third-party name resolution is their problem, not ours.
    """
    path = ROOT / rel
    if not path.exists():
        pytest.skip(f'{rel} has been deleted')

    first_party = ('trophies', 'core', 'api', 'notifications', 'users', 'milestones', 'fundraiser',
                   'art_reveal', 'plat_pursuit')
    missing = []
    for module_name, names in _imports(path):
        if not names or not module_name.startswith(first_party):
            continue
        defined = _top_level_names(module_name)
        if defined is None:
            continue
        for name in names:
            if name == '*' or name in defined:
                continue
            # `from trophies.services import collection_service` imports a SUBMODULE, which is not a name
            # in the package's __init__.py. Resolve it as a module before calling it missing.
            try:
                if importlib.util.find_spec(f'{module_name}.{name}') is not None:
                    continue
            except (ImportError, ModuleNotFoundError, ValueError):
                pass
            missing.append(f'{module_name}.{name}')

    assert not missing, (
        f'{rel} imports names that no longer exist: {missing}. The module resolves, so this fails only '
        f'when the line actually runs.'
    )
