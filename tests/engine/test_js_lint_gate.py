"""The JS lint gate: it exists, it runs in CI, and it covers our JavaScript.

WHY. `static/js/game-flag.js` shipped with `maxVersions` used in four places and declared in none.
That is valid JavaScript: `node --check` passed, `npm run build` passed, `collectstatic` passed, and
every Django test stayed green -- the markup contract those tests pin was correct, and the bug was
purely in the script. It reached a browser and silently killed a button, because a ReferenceError
inside a click handler aborts the handler and logs to a console nobody is watching.

Nothing in this repo could have caught it. There is no JS test harness, and this is not an argument
for one -- it is the cheap half: a static pass that asks "does every name resolve" with nobody
writing a test.

These tests do NOT run eslint. CI does that (`npm run lint`), and shelling out to node from pytest
would make the Python suite depend on a node_modules that developers may not have installed. What
they pin is that the gate is still WIRED: the config exists, covers the right files, keeps `no-undef`
fatal, and is still invoked by the workflow. A lint step quietly dropped from CI is indistinguishable
from a lint step that never existed.
"""
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / 'eslint.config.mjs'
WORKFLOW = ROOT / '.github' / 'workflows' / 'tests.yml'
PACKAGE = ROOT / 'package.json'


def test_the_eslint_config_exists():
    assert CONFIG.exists(), 'eslint.config.mjs is gone -- the lint gate has no rules to run'


def test_no_undef_is_fatal_not_advisory():
    """THE rule the gate exists for. Demoted to 'warn' it would still print, and eslint exits 0 on
    warnings -- so CI would go green on exactly the bug this was built to stop."""
    body = CONFIG.read_text(encoding='utf-8')
    match = re.search(r"'no-undef':\s*'([a-z]+)'", body)
    assert match, "no-undef is not configured at all"
    assert match.group(1) == 'error', (
        f"no-undef is '{match.group(1)}'; eslint exits 0 on warnings, so CI would not fail on an "
        f"undefined variable"
    )


def test_the_lint_script_covers_our_javascript():
    """`static/js` including `games/`, which is ours. `vendor/` is excluded in the config's ignores --
    third-party bundles, minified past the point of useful analysis."""
    scripts = json.loads(PACKAGE.read_text(encoding='utf-8'))['scripts']
    assert 'lint' in scripts, 'npm run lint is gone'
    assert 'static/js' in scripts['lint'], f"lint no longer covers static/js: {scripts['lint']!r}"

    ignores = CONFIG.read_text(encoding='utf-8')
    assert 'static/js/vendor/**' in ignores, 'vendor bundles are no longer excluded'
    assert 'static/js/games/**' not in ignores, 'our own minigame code was excluded from the gate'


def test_ci_actually_runs_the_lint():
    """A gate nobody invokes is a file. Pins the three steps it needs, because dropping any one of
    them leaves the others looking present and doing nothing."""
    ci = WORKFLOW.read_text(encoding='utf-8')

    assert 'actions/setup-node' in ci, 'CI has no node, so it cannot lint'
    assert re.search(r'run:\s*npm install', ci), 'CI never installs the JS dependencies'
    assert re.search(r'run:\s*npm run lint', ci), 'CI never runs the lint'


def test_ci_does_not_ask_npm_for_anything_that_needs_a_lockfile():
    """`package-lock.json` is gitignored in this repo, and TWO separate workflow features quietly
    require one. `npm ci` refuses outright. `cache: npm` on actions/setup-node hard fails with
    "Dependencies lock file is not found" -- which is the one that actually broke CI, because it
    reads as an unrelated performance option sitting three lines above the install step that had
    already been written around the same constraint.

    Pinned as one rule so the constraint is stated once, in the place a future edit would trip it.
    If the lockfile is ever committed, delete this test and use `npm ci` + caching -- both are
    better, and this only exists because the lockfile is not there.
    """
    gitignore = (ROOT / '.gitignore').read_text(encoding='utf-8')
    if 'package-lock.json' not in gitignore:
        pytest.skip('lockfile is committed now -- npm ci and cache: npm are both available again')

    # COMMENTS STRIPPED FIRST. The workflow explains both traps in prose right beside the
    # settings, so a search over the raw file matches the EXPLANATION and fails on a correct
    # file -- which is what the first version of this test did. Assert on what the runner reads.
    ci = '\n'.join(
        line for line in WORKFLOW.read_text(encoding='utf-8').splitlines()
        if not line.lstrip().startswith('#')
    )

    assert not re.search(r'run:\s*npm ci\b', ci), (
        'npm ci needs a lockfile and package-lock.json is gitignored -- use `npm install`'
    )
    assert not re.search(r'cache:\s*npm', ci), (
        "`cache: npm` keys on a lockfile and fails the run without one: "
        '"Dependencies lock file is not found"'
    )


@pytest.mark.parametrize('dep', ['eslint', '@eslint/js', 'globals'])
def test_the_lint_dependencies_are_pinned_exactly(dep):
    """`package-lock.json` is gitignored here, so package.json is the ONLY pin. With a caret range a
    future eslint patch could add a rule and turn CI red with no code change -- a lint gate that goes
    red on its own is a lint gate somebody disables."""
    deps = json.loads(PACKAGE.read_text(encoding='utf-8'))['devDependencies']
    assert dep in deps, f'{dep} is no longer a devDependency'
    assert re.fullmatch(r'\d+\.\d+\.\d+', deps[dep]), (
        f'{dep} is pinned as {deps[dep]!r}; with no lockfile committed this must be an exact version'
    )
