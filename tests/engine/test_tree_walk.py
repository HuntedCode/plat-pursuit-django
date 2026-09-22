"""The shared source walk excludes what it claims to, and still reaches the source.

Guards a helper rather than a feature, which is worth it here: every repo-walking guard in the suite
now trusts `_tree.source_files`, so a hole in it is a hole in all of them at once -- and the failure
mode is silence, not an error.
"""
import pathlib

from tests.engine._tree import ROOT, SKIP_DIRS, source_files


def test_an_agent_worktree_is_not_part_of_the_source_tree():
    """A git worktree under `.claude/` is a second copy of the whole repo inside `BASE_DIR`.

    PROBED ON THE REAL FILESYSTEM rather than asserted against `SKIP_DIRS`, because what matters is
    that the WALK skips it -- `.claude` being in a set proves nothing about a walker that matches on
    the wrong thing, and the original bug was three walkers agreeing on a set that did not contain
    it. The probe file is created and removed here so this cannot pass merely because no agent
    happens to be running.
    """
    nested = ROOT / '.claude' / 'worktrees' / '_probe' / 'core'
    nested.mkdir(parents=True, exist_ok=True)
    probe = nested / 'views.py'
    probe.write_text("GET.get('preview')\n", encoding='utf-8')
    try:
        found = [rel for _, rel in source_files('*.py')]
        assert not any(rel.startswith('.claude/') for rel in found), \
            'the walk descends into .claude, so every repo guard sees a second copy of the repo'

        # ANTI-VACUITY. A walk that returned nothing at all would pass the assertion above, and that
        # is exactly how the guards this helper serves would go quiet without anyone noticing.
        assert 'core/previews.py' in found, 'the walk no longer reaches our own source'
    finally:
        probe.unlink(missing_ok=True)
        for directory in (nested, nested.parent):
            try:
                directory.rmdir()
            except OSError:
                break                      # somebody else's worktree lives here; leave it alone


def test_the_skip_list_matches_whole_path_parts():
    """`venv` must not also exclude `my_venv_notes.py`, and `.claude` must exclude every depth of a
    worktree rather than only a top-level file."""
    relative = pathlib.Path('.claude/worktrees/agent-x/gamelists/views.py')
    assert any(part in SKIP_DIRS for part in relative.parts)

    assert not any(part in SKIP_DIRS for part in pathlib.Path('core/venv_notes.py').parts)


def test_skip_paths_are_prefixes_and_do_not_become_global_rules():
    """`static/vendor` is skipped by one guard as a PREFIX. A part-match would also drop any
    directory named `vendor` anywhere, which is a different and larger claim than the caller made."""
    walked = {rel for _, rel in source_files('*.css', skip_paths=('static/vendor',))}
    assert not any(rel.startswith('static/vendor') for rel in walked)
    assert any(rel.startswith('static/css/') for rel in walked), 'the walk missed our own CSS'
