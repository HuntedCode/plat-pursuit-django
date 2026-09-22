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


def test_the_skip_list_matches_at_every_depth_not_just_the_top():
    """A skipped directory is excluded WHEREVER it appears, not only as the first path component.

    THIS TEST USED TO ASSERT AGAINST `SKIP_DIRS` DIRECTLY -- it rebuilt the `any(part in ...)`
    expression and checked its own copy, so it was testing the assertion rather than the walker.
    Narrowing `_tree.source_files` to `relative.parts[0] in skip` survived it, and survived every
    other guard too: the `.claude` probe could not catch it either, because that probe's FIRST part
    is already `.claude`.

    So this probes through the real walk at DEPTH. The file is created and removed here, because a
    tree that happens to contain no nested skip directory would make the assertion vacuous.
    """
    nested = ROOT / 'core' / '__pycache__' / '_probe_pkg'
    probe = nested / 'deep.py'
    try:
        nested.mkdir(parents=True, exist_ok=True)
        probe.write_text('x = 1\n', encoding='utf-8')

        found = [rel for _, rel in source_files('*.py')]
        assert not any('__pycache__' in rel for rel in found), \
            'a skipped directory below the top level is walked anyway'
        assert 'core/previews.py' in found, 'the walk no longer reaches our own source'
    finally:
        probe.unlink(missing_ok=True)
        try:
            nested.rmdir()
        except OSError:
            pass

    # ...and the match is on whole parts, so a directory is not excluded by merely containing the
    # name of one. `venv` must not take `core/venv_notes.py` with it.
    assert not any(part in SKIP_DIRS for part in pathlib.Path('core/venv_notes.py').parts)


def test_skip_paths_are_prefixes_and_do_not_become_global_rules():
    """`static/vendor` is skipped by one guard as a PREFIX. A part-match would also drop any
    directory named `vendor` anywhere, which is a different and larger claim than the caller made.

    `*.js`, NOT `*.css`. This walked CSS -- and `static/vendor` holds exactly one file, `phaser.min.js`
    -- so there was nothing there to exclude and the assertion could not fail. Deleting the whole
    `skip_paths` filter left this green.
    """
    walked = {rel for _, rel in source_files('*.js', skip_paths=('static/vendor',))}
    assert any(rel.startswith('static/vendor') for rel in
               (r for _, r in source_files('*.js'))), \
        'nothing lives under static/vendor any more, so this proves nothing'
    assert not any(rel.startswith('static/vendor') for rel in walked)
    assert any(rel.startswith('static/js/') for rel in walked), 'the walk missed our own JS'
