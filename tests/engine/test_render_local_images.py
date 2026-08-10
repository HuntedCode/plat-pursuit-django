"""Local images must be inlined before a card is rendered to PNG.

`page.set_content()` runs in an `about:blank` origin, so a root-relative `src` has nothing to resolve
against and loads as nothing -- silently, with no console error surfaced to us and no exception. That
makes this class of bug uniquely invisible: the browser PREVIEW is a real page on the site origin, so
the same card looks complete there and loses images only in the download.

That is exactly how /media/ went unhandled. `/static/` was inlined from the start, so a badge's
backdrop plate (a `static(...)` fallback) rendered while its custom subject art (a FileField `.url`,
root-relative whenever media is served locally) vanished from every downloaded card.
"""
import base64
import re
from pathlib import Path

import pytest
from django.conf import settings

from core.services import playwright_renderer as pr
from core.services.playwright_renderer import _resolve_urls

# A one-pixel PNG. Contents are irrelevant; only whether it gets embedded matters.
PIXEL = base64.b64decode(
    b'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=='
)


@pytest.fixture
def media_image(tmp_path, monkeypatch):
    """A real file under a temporary MEDIA_ROOT, plus the URL the template would carry for it."""
    root = tmp_path / 'media'
    (root / 'badges').mkdir(parents=True)
    (root / 'badges' / 'subject.png').write_bytes(PIXEL)
    monkeypatch.setattr(pr, 'MEDIA_ROOT', root)
    monkeypatch.setattr(pr, 'MEDIA_URL_PREFIX', '/media/')
    return root


def test_media_urls_are_inlined(media_image):
    """The regression: uploaded badge art reached the renderer as a root-relative /media/ path."""
    html = '<img src="/media/badges/subject.png">'

    out = _resolve_urls(html)

    assert '/media/badges/subject.png' not in out, 'left as a root-relative path; renders as nothing'
    assert out.startswith('<img src="data:image/png;base64,')


def test_static_urls_are_still_inlined():
    """The pass that already worked, pinned so the shared helper refactor can't quietly break it."""
    existing = Path(settings.STATIC_ROOT) / 'images' / 'badges' / 'default.png'
    if not existing.exists():
        pytest.skip('STATIC_ROOT is not collected in this environment')

    out = _resolve_urls('<img src="/static/images/badges/default.png">')

    assert 'data:image/png;base64,' in out
    assert '/static/images/badges/default.png' not in out


def test_a_missing_file_is_left_alone_rather_than_mangled(media_image):
    """No file, no substitution. A broken image is bad; a corrupted `src` attribute is worse."""
    html = '<img src="/media/badges/nope.png">'

    assert _resolve_urls(html) == html


def test_paths_escaping_the_media_root_are_refused(media_image, tmp_path):
    """Media filenames can originate from user uploads (community badge submissions), and this turns a
    URL in the rendered HTML into a filesystem read. Traversal must not reach outside MEDIA_ROOT."""
    secret = tmp_path / 'secret.png'
    secret.write_bytes(PIXEL)

    out = _resolve_urls('<img src="/media/../secret.png">')

    assert 'data:image' not in out, 'read a file outside MEDIA_ROOT'


def test_remote_urls_are_untouched(media_image):
    """Remote art is cached to a same-origin temp file upstream; this pass must not try to path it."""
    html = '<img src="https://image.api.playstation.com/badge.png">'

    assert _resolve_urls(html) == html


@pytest.mark.parametrize('url', [
    'https://cdn.example.com/media/badges/subject.png',
    'https://cdn.example.com/static/images/badges/default.png',
])
def test_an_absolute_url_containing_the_prefix_is_not_spliced(media_image, url):
    """A CDN URL CONTAINS "/media/..." as a substring, so an unanchored pattern matches mid-URL.

    The failure is worse than a missing image: the substitution splices a data URI into the middle of
    the href, leaving "https://cdn.example.comdata:image/png;base64,..." -- a corrupted src rather
    than an absent one."""
    html = f'<img src="{url}">'

    assert _resolve_urls(html) == html


def test_bucket_hosted_media_disables_the_local_pass(monkeypatch, tmp_path):
    """With MEDIA_URL absolute, FileField.url is already remote and there is no local path to read.

    Guarded at import-derived config rather than per-call so the regex never runs against an
    absolute URL prefix."""
    monkeypatch.setattr(pr, 'MEDIA_URL_PREFIX', None)
    monkeypatch.setattr(pr, 'MEDIA_ROOT', tmp_path)
    html = '<img src="https://cdn.example.com/media/badges/subject.png">'

    assert _resolve_urls(html) == html


def test_uploaded_art_is_capped_before_embedding(tmp_path, monkeypatch):
    """Contributor-uploaded badge art is authored at source resolution and renders at 52px.

    Inlining it at full size (850x850 is typical) put up to ~647 KB of base64 into every card's HTML.
    The cap is what keeps this fix from trading a missing image for a bloated render."""
    from PIL import Image

    root = tmp_path / 'media'
    (root / 'badges').mkdir(parents=True)
    big = root / 'badges' / 'huge.png'
    Image.new('RGBA', (850, 850), (255, 0, 0, 128)).save(big)
    monkeypatch.setattr(pr, 'MEDIA_ROOT', root)
    monkeypatch.setattr(pr, 'MEDIA_URL_PREFIX', '/media/')

    out = _resolve_urls('<img src="/media/badges/huge.png">')

    assert 'data:image/png;base64,' in out, 'alpha must survive: a medallion subject is transparent'
    assert len(out) < 200_000, f'embedded at {len(out)} bytes; the cap did not apply'


def test_our_own_static_assets_are_embedded_unresized():
    """/static/ is right-sized by us at author time, so it keeps the no-resize contract it always had.

    Only /media/ gets a ceiling, because only /media/ is arbitrary upload."""
    import inspect

    src = inspect.getsource(pr._resolve_urls)
    static_call = src[src.index('def replace_static'):src.index('def replace_media')]

    assert 'max_size' not in static_call


def test_data_uris_survive_a_second_pass(media_image):
    """Embedded base64 contains arbitrary characters; the media regex must not chew into one."""
    out = _resolve_urls('<img src="/media/badges/subject.png">')

    assert _resolve_urls(out) == out
