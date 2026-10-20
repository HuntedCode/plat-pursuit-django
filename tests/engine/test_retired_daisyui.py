"""Retired DaisyUI components may not spread (SW-01, docs/design/reviews/README.md).

`btn`, `badge` and `tabs` are retired in favour of the house `pp-cta`, `.bd-chip` and `.pp-switch`
(docs/reference/design-system.md, "DaisyUI: What Stays, What Is Retired"). Pages still using them are
on `retired_daisyui_allowlist.json`, and the counts there are a RATCHET:

- a file may not gain a use (that is new code reaching for a retired component);
- a file that loses uses must lower its entry in the same commit, so the allowance it gave up cannot be
  quietly spent again later.

After converting a page, refresh the list rather than hand-editing it (it refuses to raise anything):
    python -c "from tests.engine.retired_daisyui import refresh_allowlist; refresh_allowlist()"
"""
import json

import pytest

from tests.engine.retired_daisyui import (
    ALLOWLIST, _all_names, _class_attr_values, _js_counts, _py_counts, _strip_template_comments,
    current_counts, python_packages, refresh_allowlist, sources,
)


def _allowed():
    return json.loads(ALLOWLIST.read_text(encoding='utf-8'))


def test_the_guard_still_reads_the_codebase():
    templates, scripts, python = sources()
    assert len(templates) > 100 and len(scripts) > 30 and len(python) > 100, (
        'a guard that reads no files passes forever; did templates/, static/js/ or an app move?')
    names = {p.name for p in python_packages()}
    assert {'trophies', 'core', 'users', 'gamelists', 'api'} <= names, f'apps not discovered: {sorted(names)}'


def test_no_file_gains_a_retired_daisyui_class():
    allowed, now = _allowed(), current_counts()
    grew = [f'{path}: {family} {allowed.get(path, {}).get(family, 0)} -> {n}'
            for path, families in now.items() for family, n in families.items()
            if n > allowed.get(path, {}).get(family, 0)]
    assert not grew, (
        'Retired DaisyUI classes were added. Use pp-cta (buttons), .bd-chip (badges) or .pp-switch (tabs);\n'
        'see "DaisyUI: What Stays, What Is Retired" in docs/reference/design-system.md.\n' + '\n'.join(grew))


def test_the_allowlist_shrinks_with_the_code():
    allowed, now = _allowed(), current_counts()
    slack = [f'{path}: {family} allowed {n}, now {now.get(path, {}).get(family, 0)}'
             for path, families in allowed.items() for family, n in families.items()
             if now.get(path, {}).get(family, 0) < n]
    assert not slack, (
        'These files use fewer retired classes than the allowlist grants. Lower the entries (run '
        'refresh_allowlist()) so the freed allowance cannot be spent again:\n' + '\n'.join(slack))


def test_refreshing_can_lower_the_allowlist_but_never_raise_it(tmp_path):
    """The audit's catch: a refresh that writes today's counts would let anyone add a `btn` and then
    'refresh' it into the allowlist, with both ratchet tests passing."""
    now = current_counts()
    path, (family, n) = next((p, next(iter(f.items()))) for p, f in now.items())

    generous = tmp_path / 'generous.json'
    generous.write_text(json.dumps({**now, path: {**now[path], family: n + 5}}), encoding='utf-8')
    refresh_allowlist(generous)
    assert json.loads(generous.read_text(encoding='utf-8'))[path][family] == n, 'a refresh must lower slack'

    stingy = tmp_path / 'stingy.json'
    stingy.write_text(json.dumps({**now, path: {**now[path], family: n - 1}}), encoding='utf-8')
    with pytest.raises(ValueError, match='refusing to raise'):
        refresh_allowlist(stingy)
    assert json.loads(stingy.read_text(encoding='utf-8'))[path][family] == n - 1, 'a refused refresh wrote anyway'


def test_the_reader_counts_classes_not_words():
    """Pin the reader: a blind reader passes forever, and a word-counting one fails on every toast."""
    markup = _strip_template_comments(
        '{# btn btn-primary #}<!-- badge --><p>You earned a badge. Open the tab.</p>'
        '<a class="btn {% if x %}btn-primary{% endif %} md:btn-sm">Go</a><span class="bd-chip tab-count">1</span>')
    assert _all_names(_class_attr_values(markup)) == {'btn': 3}

    js = ("// btn btn-primary in a comment\nT.show('Your badge is ready');\n"
          "el.classList.add('badge', 'badge-success'); b.className = 'tab tab-active';\n"
          "document.querySelector('a.btn-primary'); html = `<b class=\"btn btn-sm\">`;\n"
          "chip = `<span class=\"x ${on ? 'badge-info' : 'badge-ghost'}\">`; el.setAttribute('class', 'btn');\n"
          "if (target.id === 'tab-content') {}")
    assert _js_counts(js) == {'badge': 4, 'tabs': 2, 'btn': 4}

    # The lexer. An odd number of backticks in a regex (the admin-notifications.js shape, which hid 160
    # lines from the first cut) must not open a string that runs on and swallows the class set after it.
    assert _js_counts("md = s.replace(/`([^`]+)`/g, '<code>$1</code>');\nel.className = 'btn';\n") == {'btn': 1}
    # Quotes inside a regex, division, and '//' inside a string are not strings or comments.
    assert _js_counts("q = s.replace(/['\"]/g, ''); h = a / 2 / b; u = 'https://x/'; el.className = 'badge';") == {'badge': 1}
    # A template nested in a ${} expression stays inside its outer template.
    assert _js_counts("t = `<i class=\"${a ? `x-${s}` : 'badge-info'}\">`; el.className = 'btn';") == {'badge': 1, 'btn': 1}

    py = ('"""btn badge-ghost in a docstring"""\nW = {"class": "btn btn-ghost"}\nmsg = "Earn a badge"\n'
          'attrs = {}\nattrs["class"] = "badge"\nPHASES = [("story", "badge-info")]\n')
    assert _py_counts(py) == {'btn': 2, 'badge': 2}
