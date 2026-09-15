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
    # Either installer satisfies THIS test -- it only asks that dependencies arrive at all.
    # `test_ci_installs_from_the_lockfile` is the one that insists on `npm ci` specifically.
    assert re.search(r'run:\s*npm (ci|install)\b', ci), 'CI never installs the JS dependencies'
    assert re.search(r'run:\s*npm run lint', ci), 'CI never runs the lint'


def test_the_lockfile_is_committed():
    """It was gitignored, and CI therefore re-resolved the whole transitive dependency tree from the
    network on every run and executed its install scripts -- so a patch release anywhere in that tree
    landed on the runner unreviewed. Nothing npm-installed here reaches production unreviewed (the
    Tailwind output is committed, and the runtime JS libraries are vendored), so the argument for
    committing it was never build reproducibility. It was that CI executes third-party code.

    Deleting the file or re-ignoring it silently restores that exposure, and `npm ci` would start
    failing for a reason that reads as unrelated -- hence a test rather than a comment.
    """
    lock = ROOT / 'package-lock.json'
    assert lock.exists(), 'package-lock.json is gone'

    gitignore = (ROOT / '.gitignore').read_text(encoding='utf-8')
    assert not re.search(r'^\s*package-lock\.json\s*$', gitignore, re.M), (
        'package-lock.json is ignored again -- CI would resolve dependencies fresh on every run'
    )


def test_ci_installs_from_the_lockfile():
    """`npm ci`, not `npm install`. `install` re-resolves and will happily drift from the lockfile;
    `ci` installs exactly what is locked and REFUSES if package.json and the lockfile disagree. That
    refusal is the feature -- it is what makes the integrity hashes mean anything.

    Comments stripped first: the workflow explains this in prose right beside the settings, so a
    search over the raw file matches the explanation rather than the step. That is not hypothetical
    -- an earlier version of this test did exactly that and failed on a correct file.
    """
    ci = '\n'.join(
        line for line in WORKFLOW.read_text(encoding='utf-8').splitlines()
        if not line.lstrip().startswith('#')
    )

    assert re.search(r'run:\s*npm ci\b', ci), (
        'CI no longer installs from the lockfile, so the pinning it provides is not being used'
    )
    assert not re.search(r'run:\s*npm install\b', ci), (
        '`npm install` re-resolves and can drift from the lockfile -- use `npm ci`'
    )


@pytest.mark.parametrize('dep', ['eslint', '@eslint/js', 'globals'])
def test_the_lint_dependencies_are_locked(dep):
    """package.json may carry a caret range now -- the LOCKFILE is the pin, which is the whole reason
    it is committed. This asserts the two agree that the dependency exists at all; `npm ci` enforces
    the stronger version-level agreement on every run and refuses if they diverge.

    (This replaced a test requiring EXACT versions in package.json. That was the right rule while
    package.json was the only pin, and became a worse one the moment the lockfile arrived -- exact
    ranges there would fight `npm update` for no benefit the lockfile does not already give.)
    """
    deps = json.loads(PACKAGE.read_text(encoding='utf-8'))['devDependencies']
    assert dep in deps, f'{dep} is no longer a devDependency'

    lock = json.loads((ROOT / 'package-lock.json').read_text(encoding='utf-8'))
    assert f'node_modules/{dep}' in lock['packages'], (
        f'{dep} is in package.json but not the lockfile -- run `npm install` and commit the result'
    )
