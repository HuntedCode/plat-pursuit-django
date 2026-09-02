"""Every image that calls `static()` must build a staticfiles manifest.

THE INCIDENT. The hourly cron died with `Missing staticfiles manifest entry for
'images/badges/backdrops/4_backdrop.png'` -- a file committed in 2025, present in the web image's
manifest, and not remotely special. `staticfiles/` is in `.dockerignore` (correctly: it is a build
artifact) and `Dockerfile.worker` never ran `collectstatic`, so that image had NO manifest at all.
With `ManifestStaticFilesStorage` a missing manifest makes `stored_name()` raise for the FIRST
`static()` call whatever it asks for -- so the error named the first backdrop the showcase card
happened to reach, and pointed at a healthy file.

That mis-direction is the reason this is pinned at the Dockerfile level rather than the asset level:
the symptom names an asset, the cause is an image.

It was also invisible for as long as it had existed, because `refresh_homepage_hourly` caught the
error, wrote it to stdout and exited 0 -- so the cron platform reported a green run while the landing
served its static fixture. It only surfaced once that command started exiting non-zero.
"""
import re
from pathlib import Path

import pytest
from django.conf import settings

ROOT = Path(settings.BASE_DIR)

#: Images whose containers run Django code that can reach `static()`.
DOCKERFILES = ['Dockerfile', 'Dockerfile.worker']


@pytest.mark.parametrize('name', DOCKERFILES)
def test_every_django_image_collects_static(name):
    path = ROOT / name
    if not path.exists():
        pytest.skip(f'{name} does not exist')

    body = path.read_text(encoding='utf-8')
    # Comment-stripped: the explanation above the command NAMES it, so scanning raw source would pass
    # with the RUN line deleted.
    code = '\n'.join(ln for ln in body.splitlines() if not ln.lstrip().startswith('#'))

    assert re.search(r'RUN\s+python\s+manage\.py\s+collectstatic', code), (
        f'{name} never builds a staticfiles manifest. Any `static()` call in that container raises '
        f'"Missing staticfiles manifest entry" for whatever it asks for FIRST, which reads as a '
        f'missing asset and is not one.'
    )


def test_the_manifest_is_a_build_artifact_not_a_committed_one():
    """The other half of why the worker image had none: `staticfiles/` is deliberately excluded, so an
    image that does not generate one does not get one. If this ever stops being true, the images could
    inherit a STALE manifest instead, which fails the same way for a different reason."""
    ignore = (ROOT / '.dockerignore').read_text(encoding='utf-8')

    assert any(ln.strip().rstrip('/') == 'staticfiles' for ln in ignore.splitlines()), (
        '.dockerignore no longer excludes staticfiles/ -- images may now copy a stale host manifest'
    )


def test_safe_static_degrades_instead_of_raising(monkeypatch):
    """Defence in depth for the blast radius, not for the cause."""
    from trophies.util_modules import assets

    monkeypatch.setattr(assets, 'static', lambda p: (_ for _ in ()).throw(
        ValueError(f"Missing staticfiles manifest entry for '{p}'")))

    assert assets.safe_static('images/badges/default.png') is None


def test_safe_static_does_not_swallow_real_bugs(monkeypatch):
    """Only the two manifest failures raise ValueError. A TypeError from a bad call is a bug and must
    still surface -- a helper that eats everything is how a broken asset path lives forever."""
    from trophies.util_modules import assets

    monkeypatch.setattr(assets, 'static', lambda p: (_ for _ in ()).throw(TypeError('bad call')))

    with pytest.raises(TypeError):
        assets.safe_static('images/badges/default.png')


@pytest.mark.django_db
def test_the_whole_medallion_path_survives_a_missing_manifest(monkeypatch):
    """BOTH layers, which is what the first version of this fix got wrong.

    `art_layers()` resolves the SUBJECT art and runs before the backdrop, so for a badge with no
    custom art it is the first `static()` call on the path -- guarding only the backdrop left the
    common case still raising. This drives the real entry point with every static lookup failing.
    """
    from trophies.services import badge_detail_service as svc
    from trophies.util_modules import assets
    from tests.factories import GroupBadgeFactory

    monkeypatch.setattr(assets, 'static', lambda p: (_ for _ in ()).throw(ValueError('missing')))

    gb = GroupBadgeFactory()          # no custom art -> takes the placeholder branch
    tier, layers, is_avatar = svc.group_medallion_layers(gb)

    assert tier, 'the backing metal is CSS-driven and unaffected'
    assert layers == [], 'unresolvable art degrades to the bare metal plate'
    assert is_avatar is False
