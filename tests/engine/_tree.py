"""One definition of "walk our own source tree", for the guards that assert things about the repo.

THREE TESTS HAD THREE HAND-WRITTEN COPIES OF THIS, and they had already drifted: one skipped
`migrations`, one did not; one carried `static/vendor`, the others did not; and none of them skipped
`.claude`. That last omission is what forced the extraction. An agent working in a git worktree at
`.claude/worktrees/<name>/` puts a SECOND COPY OF THE WHOLE REPO inside `BASE_DIR`, and:

  - `test_every_team_preview_door_is_the_same_door` FAILS, because its finding is a path and the
    copy's paths are not in its allow-list. It failed twice in one afternoon during an audit round,
    each time looking like a real regression in code nobody had touched.
  - `test_no_module_defines_the_same_name_twice` and the control-character scan do not fail (their
    assertions are per-file), but they silently double their work -- and the control-character scan
    reads `.claude/settings.local.json`, which is untracked local config, directly contradicting its
    own docstring's "the tracked tree only".

`.claude` is in `.gitignore`, which buys nothing here: `rglob` does not read gitignore. A fourth
walker would have got this wrong too, which is the argument for one list rather than a fourth copy.

ONE THING TO WATCH. Excluding `.claude` is safe only while NOTHING UNDER IT IS TRACKED, which is true
today. Committing `.claude/settings.json` or `.claude/agents/*.md` is common practice, and `CLAUDE.md`
is already tracked at the root -- so the day that happens, the control-character guard silently stops
scanning tracked project config while its docstring still says "the tracked tree only". At that point
this needs to exclude `.claude/worktrees/` specifically rather than the whole directory.
"""
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]

#: Directories that are never our source, wherever they appear in the path. Matched per PATH PART,
#: so `.claude/worktrees/agent-x/core/` is excluded by its first component.
#:
#: `migrations` is NOT here: it is generated code that one guard wants excluded and another does not,
#: so it stays a per-caller decision rather than being decided for everyone by this list.
SKIP_DIRS = frozenset({
    'node_modules', 'venv', '.venv', '.git', '.claude', 'staticfiles', '__pycache__',
    '.pytest_cache', 'htmlcov', 'dist', 'build', '.ruff_cache',
})


def source_files(pattern='*', *, suffixes=None, skip_dirs=(), skip_paths=()):
    """Yield `(path, rel)` for files under `ROOT` that are our own source.

    `rel` is POSIX-relative to `ROOT`, which is what every caller reports offences with.

    `skip_dirs` adds to `SKIP_DIRS` for this walk (part-matched); `skip_paths` skips by `rel` PREFIX,
    which is the right shape for a specific subtree like `static/vendor` that must not become a
    global rule. `suffixes` is matched lowercased, or None for every file.
    """
    skip = SKIP_DIRS.union(skip_dirs)
    for path in ROOT.rglob(pattern):
        if not path.is_file():
            continue
        relative = path.relative_to(ROOT)
        if any(part in skip for part in relative.parts):
            continue
        if suffixes is not None and path.suffix.lower() not in suffixes:
            continue
        rel = relative.as_posix()
        if any(rel.startswith(prefix) for prefix in skip_paths):
            continue
        yield path, rel
