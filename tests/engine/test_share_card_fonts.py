"""Share-card fonts must actually ship.

`_build_font_faces()` skips any TTF it can't find -- no exception, no log. The card then renders in
whatever Chromium picks as a fallback, which looks *almost* right, so the failure survives review and
shows up as "the share card font looks a bit off". These pin the map against the tracked source
directory so a rename or a missed asset fails loudly here instead.
"""
from pathlib import Path

import pytest
from django.conf import settings

from core.services.playwright_renderer import _build_font_faces

SOURCE_FONTS = Path(settings.BASE_DIR) / 'static' / 'fonts'


def _font_map():
    """The filename -> (family, style, weight) map, read from the function that owns it."""
    import inspect
    import re

    src = inspect.getsource(_build_font_faces)
    body = src[src.index('font_map = {'):src.index('}', src.index('font_map = {'))]
    return re.findall(r"'([^']+\.ttf)':\s*\('([^']+)'", body)


def test_every_declared_font_exists_in_the_tracked_source():
    missing = [name for name, _ in _font_map() if not (SOURCE_FONTS / name).exists()]

    assert not missing, f"declared in _build_font_faces but absent from static/fonts/: {missing}"


def test_the_display_face_is_registered():
    """Cards speak in --pp-font-display like the rest of the site; without these three the plat card
    silently falls back to Inter."""
    families = {family for _, family in _font_map()}

    assert 'Bricolage Grotesque' in families
    weights = [n for n, f in _font_map() if f == 'Bricolage Grotesque']
    assert len(weights) == 3, weights


@pytest.mark.skipif(
    not (Path(settings.STATIC_ROOT) / 'fonts').exists(),
    reason="STATIC_ROOT is a collectstatic artifact and CI doesn't build it; the tracked source is "
           "covered by test_every_declared_font_exists_in_the_tracked_source",
)
def test_font_faces_build_without_dropping_a_family():
    """Guards the end of the pipeline on a machine that HAS collected static: the renderer reads
    STATIC_ROOT, not static/, so a stale collect yields fewer rules than the map declares."""
    css = _build_font_faces()
    declared = len(_font_map())

    assert css.count('@font-face') == declared, (
        f"{declared} fonts declared but {css.count('@font-face')} embedded -- run collectstatic"
    )
