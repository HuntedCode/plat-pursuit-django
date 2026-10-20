"""Every literal `{% static %}` path a template names must exist.

Deleting a script or stylesheet and missing one of the tags that loads it leaves a page requesting a file
that is not there: a 404 and a console error on every visit, which the forgiving manifest storage
(plat_pursuit/storage.py) keeps quiet at render time. The review leftovers branch deleted two such files.
"""
import re
from pathlib import Path

from django.contrib.staticfiles import finders

ROOT = Path(__file__).resolve().parents[2]
STATIC_TAG = re.compile(r"""\{%\s*static\s+(['"])([^'"]+)\1""")


def _literal_refs():
    for path in sorted((ROOT / 'templates').rglob('*.html')):
        src = path.read_text(encoding='utf-8')
        for m in STATIC_TAG.finditer(src):
            yield path.relative_to(ROOT).as_posix(), m.group(2)


def test_the_reader_finds_the_static_tags():
    refs = list(_literal_refs())
    assert len(refs) > 100, 'the template reader stopped seeing {% static %} tags'
    assert ('templates/base.html', 'css/output.css') in refs


def test_every_static_path_a_template_names_exists():
    missing = [f'{tpl}: {ref}' for tpl, ref in _literal_refs() if not finders.find(ref)]
    assert not missing, 'templates load static files that do not exist:\n' + '\n'.join(missing)
