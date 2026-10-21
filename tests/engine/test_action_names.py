"""One name per action (FV-04, docs/design/reviews/first-visit.md).

The 2026-10 review found account creation called four things ("Create a free account", "Register",
"Sign Up", "Sign up") and signing in two ("Log in", "Sign In"). The owner chose **Sign in / Sign up /
Sign out**, sentence case. This guard reads every place a visitor can meet the words, so a retired
name cannot creep back on a page nobody thought to check:

- templates: visible text, quoted arguments inside template tags (`{% include ... with label="..." %}`
  is how the email CTAs get their words), user-facing attributes, and inline scripts;
- JS: every string literal, template literals across lines included;
- Python: every string literal in the app packages, read with `ast` so comments and docstrings, which
  legitimately describe the old names, are never mistaken for UI copy.

Bare lowercase `login` is NOT banned: it is an identifier everywhere (`account_login`,
`collectibles-login-cta`). The capitalised `Login` / `Logout` a visitor would read are.
"""
import ast
import re
from pathlib import Path

from tests.engine._tree import SKIP_DIRS

ROOT = Path(__file__).resolve().parents[2]

#: The app packages whose string literals can reach a visitor (messages, JSON errors, context).
PY_PACKAGES = ('api', 'art_reveal', 'challenges', 'core', 'fundraiser', 'gamelists', 'milestones',
               'notifications', 'plat_pursuit', 'profile_sigs', 'trophies', 'users')

RETIRED = [
    re.compile(r'\blog (?:in|out)\b', re.I),         # "Log in", "Log out", "log in to vote"
    re.compile(r'\bLog(?:in|out)\b'),                 # "Login" as a page title, "Logout"
    re.compile(r'\bSign(?:ed)? (?:In|Up|Out)\b'),     # title case: the house style is sentence case
    re.compile(r'\bRegister\b|\bregister (?:again|and)\b'),
    re.compile(r'\bCreate a free account\b'),
]

_QUOTED = r'"([^"]*)"|\'([^\']*)\''
_ATTRS = re.compile(r'\b(?:aria-label|title|placeholder|alt|value|content)=(?:' + _QUOTED + ')')


def _visible_template_text(src):
    """(text a visitor can see or hear, the source of any inline scripts)."""
    src = re.sub(r'{%\s*comment\s*%}.*?{%\s*endcomment\s*%}', ' ', src, flags=re.S)
    src = re.sub(r'{#.*?#}', ' ', src)
    src = re.sub(r'<!--.*?-->', ' ', src, flags=re.S)
    scripts = ' '.join(re.findall(r'<script\b[^>]*>(.*?)</script>', src, flags=re.S | re.I))
    src = re.sub(r'<(script|style)\b.*?</\1>', ' ', src, flags=re.S | re.I)
    tag_args = ' '.join(a or b for tag in re.findall(r'{%(.*?)%}', src, flags=re.S)
                        for a, b in re.findall(_QUOTED, tag))
    attrs = ' '.join(a or b for a, b in _ATTRS.findall(src))
    text = re.sub(r'{%.*?%}|{{.*?}}', ' ', src, flags=re.S)
    text = re.sub(r'<[^>]+>', ' ', text)
    return f'{text} {tag_args} {attrs}', scripts


def _js_string_literals(src):
    one_line = (m.group(2) for m in re.finditer(r'([\'"])((?:\\.|(?!\1)[^\n])*?)\1', src))
    templates = re.findall(r'`((?:\\.|[^`])*)`', src, flags=re.S)
    return ' '.join([*one_line, *templates])


def _py_string_literals(src):
    tree = ast.parse(src)
    docstrings = {id(node.body[0].value) for node in ast.walk(tree)
                  if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
                  and node.body and isinstance(node.body[0], ast.Expr)
                  and isinstance(node.body[0].value, ast.Constant)}
    return ' '.join(node.value for node in ast.walk(tree)
                    if isinstance(node, ast.Constant) and isinstance(node.value, str)
                    and id(node) not in docstrings)


def _retired_names_in(blob):
    return sorted({m.group(0) for pattern in RETIRED for m in pattern.finditer(blob)})


def _sources():
    """Every file the guard reads, by kind. Counted by the caller, so a moved folder fails loudly
    instead of turning the guard into a scan of nothing."""
    templates = sorted((ROOT / 'templates').rglob('*.html'))
    scripts = sorted(p for p in (ROOT / 'static' / 'js').rglob('*.js') if 'vendor' not in p.parts)
    # The shared walker's SKIP_DIRS too: `__pycache__` is never our source, and `test_tree_walk` creates and
    # deletes a probe file inside one mid-suite, so a walk that lists it can lose it before reading it
    # (the intermittent FileNotFoundError seen under -n 12 in 2026-10).
    python = sorted(p for pkg in PY_PACKAGES for p in (ROOT / pkg).rglob('*.py')
                    if 'migrations' not in p.parts and 'tests' not in p.parts and not SKIP_DIRS.intersection(p.parts))
    return templates, scripts, python


def _offenders():
    templates, scripts, python = _sources()
    found = []
    for path in templates:
        text, inline = _visible_template_text(path.read_text(encoding='utf-8'))
        names = _retired_names_in(text) + _retired_names_in(_js_string_literals(inline))
        if names:
            found.append(f'{path.relative_to(ROOT).as_posix()}: {names}')
    for path, reader in [(p, _js_string_literals) for p in scripts] + [(p, _py_string_literals) for p in python]:
        names = _retired_names_in(reader(path.read_text(encoding='utf-8')))
        if names:
            found.append(f'{path.relative_to(ROOT).as_posix()}: {names}')
    return found


def test_the_guard_still_reads_the_codebase():
    templates, scripts, python = _sources()
    assert len(templates) > 100 and len(scripts) > 30 and len(python) > 100, (
        'a guard that finds no files passes forever; did templates/, static/js/ or an app move?')


def test_no_retired_action_name_is_shown_anywhere():
    offenders = _offenders()
    assert not offenders, (
        'Use "Sign in" / "Sign up" / "Sign out" (sentence case). Retired names found:\n'
        + '\n'.join(offenders))


def test_the_readers_ignore_comments_and_catch_what_a_visitor_sees():
    """A guard whose reader is blind passes forever, so pin the readers themselves."""
    src = (
        '{# Log in used to live here #}'
        '{% comment %}the menu held Register{% endcomment %}'
        '<!-- Sign In -->'
        '<a href="{% url \'account_login\' %}" class="collectibles-login-cta">Sign in</a>'
    )
    text, _ = _visible_template_text(src)
    assert _retired_names_in(text) == []

    for shown in ('<a>Log in</a>', '<button aria-label="Register now"></button>',
                  "<input type='submit' value='Sign Up'>", '{% block title %}Login{% endblock %}',
                  '{% include "emails/_button.html" with label="Log in" %}',
                  '<script>toast("Log in to vote")</script>'):
        text, inline = _visible_template_text(shown)
        assert _retired_names_in(text) + _retired_names_in(_js_string_literals(inline)), shown

    assert _retired_names_in(_js_string_literals('const t = `Welcome!\n  Log in to continue`;'))
    assert _retired_names_in(_js_string_literals("// don't Log in here\nconst a = 1;")) == []

    py = '"""Log in was the old name."""\n# Register\ndef f():\n    """Sign In docs."""\n    return "Log in to rate"\n'
    assert _retired_names_in(_py_string_literals(py)) == ['Log in']
