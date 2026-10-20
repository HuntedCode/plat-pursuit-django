"""The reader behind the retired-DaisyUI guard (see test_retired_daisyui.py).

Two kinds of name, read two ways:

- HYPHENATED names (`btn-sm`, `badge-success`, `tab-active`) are never English, so they are counted in
  ANY string: a class built in a JS ternary (`${on ? 'badge-success' : 'badge-ghost'}`), a selector
  (`'a.btn-primary'`), an f-string piece. Ids are not classes, so `#tab-content` is skipped.
- BARE names (`btn`, `badge`, `tab`, `tabs`) are ordinary words in this product's copy ("You earned a
  badge"), so they are counted only where they are USED AS A CLASS: a class attribute, a classList call,
  className, setAttribute('class'), a `.btn` selector, a Python `'class'` widget attribute.

Comments and docstrings are stripped first: they legitimately name the old classes.

Kept import-light (no Django) so the allowlist helpers run from a plain shell after converting a page.
"""
import ast
import json
import re
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ALLOWLIST = Path(__file__).with_name('retired_daisyui_allowlist.json')
_NOT_APP_CODE = {'tests', 'venv', 'node_modules', 'staticfiles', 'media', 'docs', 'static', 'templates'}

#: family -> the DaisyUI class names it covers (exact tokens).
FAMILIES = {
    'btn': re.compile(r'^btn(?:-(?:xs|sm|md|lg|xl|square|circle|ghost|outline|link|soft|dash|active|disabled|wide|block'
                      r'|primary|secondary|accent|neutral|info|success|warning|error))?$'),
    'badge': re.compile(r'^badge(?:-(?:xs|sm|md|lg|xl|outline|ghost|soft|dash|neutral'
                        r'|primary|secondary|accent|info|success|warning|error))?$'),
    'tabs': re.compile(r'^(?:tabs(?:-(?:box|border|lift|xs|sm|md|lg|xl|top|bottom))?'
                       r'|tab(?:-(?:active|disabled|content))?)$'),
}
#: Counted only where used as a class. `tab-content` is hyphenated but doubles as a common element id
#: (`#tab-content` on the profile page), so it cannot be read in every string.
BARE = {'btn', 'badge', 'tab', 'tabs', 'tab-content'}
_HYPHENATED = re.compile(r'(?<![\w#-])((?:btn|badge|tabs?)-[\w-]+)')
_TEMPLATE_TAG = re.compile(r'{%.*?%}|{{.*?}}', re.S)


def _family(token):
    return next((f for f, pattern in FAMILIES.items() if pattern.match(token)), None)


def _strip_template_comments(src):
    src = re.sub(r'{%\s*comment\s*%}.*?{%\s*endcomment\s*%}', ' ', src, flags=re.S)
    src = re.sub(r'{#.*?#}', ' ', src)
    return re.sub(r'<!--.*?-->', ' ', src, flags=re.S)


#: A `/` after one of these starts a regex literal; after anything else (an identifier, a number, `)`,
#: `]`) it is division. The standard lexer heuristic, and enough for this codebase: the first cut had no
#: notion of regexes at all, and one with backticks inside it (admin-notifications.js) flipped the
#: string state for 160 lines and hid a real `badge`.
_REGEX_AFTER_CHARS = set('(,=:[!&|?{};+-*%<>~^')
_REGEX_AFTER_WORDS = {'return', 'typeof', 'instanceof', 'in', 'of', 'new', 'delete', 'void', 'throw',
                      'case', 'do', 'else', 'yield', 'await'}


def _js_code_and_literals(src):
    """(the source with comments removed, the body of every string literal and template literal).

    A template literal is ONE literal, `${...}` expressions included (strings nested inside them are not
    collected again), matching what a real parser (acorn) reports, so nothing is counted twice."""
    n = len(src)
    code, literals = [], []

    def skip_string(i, quote):
        j = i + 1
        while j < n:
            if src[j] == '\\':
                j += 2
                continue
            if src[j] == quote:
                return j + 1
            if src[j] == '\n':
                return j  # unterminated: stop at the line end rather than swallow the file
            j += 1
        return n

    def skip_regex(i):
        j, in_class = i + 1, False
        while j < n:
            ch = src[j]
            if ch == '\\':
                j += 2
                continue
            if ch == '\n':
                return j
            if in_class:
                in_class = ch != ']'
            elif ch == '[':
                in_class = True
            elif ch == '/':
                j += 1
                while j < n and (src[j].isalnum() or src[j] == '_'):
                    j += 1  # flags
                return j
            j += 1
        return n

    def skip_template(i):
        j = i + 1
        while j < n:
            if src[j] == '\\':
                j += 2
                continue
            if src[j] == '`':
                return j + 1
            if src.startswith('${', j):
                j = scan(j + 2, emit=False, until_brace=True)
                continue
            j += 1
        return n

    def scan(i, emit, until_brace=False):
        depth, prev_char, prev_word = 0, '', ''
        while i < n:
            c = src[i]
            if c.isspace():
                if emit:
                    code.append(c)
                i += 1
                continue
            if src.startswith('//', i):
                j = src.find('\n', i)
                i = n if j == -1 else j
                continue
            if src.startswith('/*', i):
                j = src.find('*/', i + 2)
                i = n if j == -1 else j + 2
                continue
            if c in '\'"`' or (c == '/' and (prev_char == '' or prev_char in _REGEX_AFTER_CHARS
                                             or prev_word in _REGEX_AFTER_WORDS)):
                j = skip_template(i) if c == '`' else skip_regex(i) if c == '/' else skip_string(i, c)
                if emit:
                    code.append(src[i:j])
                    if c != '/':
                        closed = j - 1 if src[j - 1:j] == c and j - 1 > i else j
                        literals.append(src[i + 1:closed])
                i, prev_char, prev_word = j, ('r' if c == '/' else c), ''
                continue
            if until_brace:
                if c == '{':
                    depth += 1
                elif c == '}':
                    if depth == 0:
                        return i + 1
                    depth -= 1
            if c.isalnum() or c in '_$':
                j = i
                while j < n and (src[j].isalnum() or src[j] in '_$'):
                    j += 1
                if emit:
                    code.append(src[i:j])
                i, prev_char, prev_word = j, src[j - 1], src[i:j]
                continue
            if emit:
                code.append(c)
            i, prev_char, prev_word = i + 1, c, ''
        return n

    scan(0, emit=True)
    return ''.join(code), literals


def _class_attr_values(markup):
    """Every class="..." / class='...' value, with template tags blanked so `{% if %}btn-x{% endif %}`
    still yields `btn-x`."""
    values = re.findall(r'\bclass\s*=\s*(?:"([^"]*)"|\'([^\']*)\')', markup)
    return [_TEMPLATE_TAG.sub(' ', a or b) for a, b in values]


def _bare(strings):
    counts = Counter()
    for s in strings:
        for token in s.split():
            token = token.split(':')[-1]  # md:btn
            if token in BARE:
                counts[_family(token)] += 1
    return counts


def _hyphenated(strings):
    counts = Counter()
    for s in strings:
        for token in _HYPHENATED.findall(_TEMPLATE_TAG.sub(' ', s)):
            family = _family(token)
            if family and token not in BARE:
                counts[family] += 1
    return counts


def _all_names(strings):
    """Every retired name in class-attribute values (both kinds)."""
    return _bare(strings) + _hyphenated(strings)


def _js_counts(src):
    code, literals = _js_code_and_literals(src)
    class_contexts = []
    for lit in literals:
        class_contexts += _class_attr_values(lit)                                 # markup in strings
        class_contexts += re.findall(r'(?<![\w-])\.([A-Za-z][\w-]*)', lit) if '.' in lit else []  # selectors
    for args in re.findall(r'classList\.(?:add|remove|toggle|contains|replace)\(([^)]*)\)', code):
        class_contexts += re.findall(r'[\'"`]([^\'"`]*)[\'"`]', args)
    class_contexts += [a or b for a, b in re.findall(r'className\s*\+?=\s*(?:"([^"]*)"|\'([^\']*)\')', code)]
    class_contexts += [a or b for a, b in re.findall(
        r'setAttribute\(\s*[\'"]class[\'"]\s*,\s*(?:"([^"]*)"|\'([^\']*)\')', code)]
    return _bare(class_contexts) + _hyphenated(literals)


def _py_counts(src):
    tree = ast.parse(src)
    docstrings = {id(node.body[0].value) for node in ast.walk(tree)
                  if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
                  and node.body and isinstance(node.body[0], ast.Expr) and isinstance(node.body[0].value, ast.Constant)}
    strings, class_contexts = [], []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings:
            strings.append(node.value)
            if 'class=' in node.value:
                class_contexts += _class_attr_values(node.value)
        elif isinstance(node, ast.Dict):                                             # {'class': '...'}
            for k, v in zip(node.keys, node.values):
                if isinstance(k, ast.Constant) and k.value == 'class' and isinstance(v, ast.Constant) and isinstance(v.value, str):
                    class_contexts.append(v.value)
        elif isinstance(node, ast.Assign):                                           # attrs['class'] = '...'
            for target in node.targets:
                if (isinstance(target, ast.Subscript) and isinstance(target.slice, ast.Constant)
                        and target.slice.value == 'class' and isinstance(node.value, ast.Constant)
                        and isinstance(node.value.value, str)):
                    class_contexts.append(node.value.value)
    return _bare(class_contexts) + _hyphenated(strings)


def python_packages():
    """Every top-level Python package that is application code: found, not listed, so a new app is read
    the day it is added."""
    return sorted(p.parent for p in ROOT.glob('*/__init__.py') if p.parent.name not in _NOT_APP_CODE)


def sources():
    templates = sorted((ROOT / 'templates').rglob('*.html'))
    scripts = sorted(p for p in (ROOT / 'static' / 'js').rglob('*.js') if 'vendor' not in p.parts)
    python = sorted(p for pkg in python_packages() for p in pkg.rglob('*.py')
                    if 'migrations' not in p.parts and 'tests' not in p.parts)
    return templates, scripts, python


def count_file(path):
    src = path.read_text(encoding='utf-8')
    if path.suffix == '.html':
        src = _strip_template_comments(src)
        inline = ' '.join(re.findall(r'<script\b[^>]*>(.*?)</script>', src, flags=re.S | re.I))
        markup = re.sub(r'<script\b.*?</script>', ' ', src, flags=re.S | re.I)
        return _all_names(_class_attr_values(markup)) + _js_counts(inline)
    if path.suffix == '.js':
        return _js_counts(src)
    return _py_counts(src)


def current_counts():
    """{relative path: {family: count}} for every file with at least one retired class."""
    out = {}
    for group in sources():
        for path in group:
            counts = count_file(path)
            if counts:
                out[path.relative_to(ROOT).as_posix()] = dict(sorted(counts.items()))
    return dict(sorted(out.items()))


def refresh_allowlist(allowlist=ALLOWLIST):
    """Rewrite the allowlist to today's (LOWER) counts after converting a page:
        python -c "from tests.engine.retired_daisyui import refresh_allowlist; refresh_allowlist()"
    Refuses any increase or any new file, so it can only ever lower the numbers: it cannot be used to
    legitimise new uses of a retired class."""
    old = json.loads(Path(allowlist).read_text(encoding='utf-8'))
    now = current_counts()
    grew = [f'{path}: {family} {old.get(path, {}).get(family, 0)} -> {n}'
            for path, families in now.items() for family, n in families.items()
            if n > old.get(path, {}).get(family, 0)]
    if grew:
        raise ValueError('refusing to raise the allowlist; convert these instead:\n' + '\n'.join(grew))
    Path(allowlist).write_text(json.dumps(now, indent=1) + '\n', encoding='utf-8')
