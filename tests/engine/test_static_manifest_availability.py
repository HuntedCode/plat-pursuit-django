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


def test_a_missing_backdrop_plate_does_not_break_the_medallion(monkeypatch):
    """Defence in depth for the blast radius, not for the cause.

    `group_medallion_layers` is reached from the request path (badge detail, collection, browse
    cards), from the profile card, and from cron. `static()` raising there took out a whole cron run
    over one decorative plate. The plate is already optional by design -- a tier outside
    _TIER_BACKDROP renders without one -- so an unresolvable one degrades to that same state.
    """
    from trophies.services import badge_detail_service as svc

    def boom(path):
        raise ValueError(f"Missing staticfiles manifest entry for '{path}'")

    monkeypatch.setattr(svc, 'static', boom)

    assert svc._backdrop_url(4) is None, 'a missing plate must degrade, not raise'


@pytest.mark.django_db
def test_the_medallion_still_composes_without_its_plate(monkeypatch):
    """And the layers a caller gets back are still usable: the subject art survives."""
    from trophies.services import badge_detail_service as svc
    from tests.factories import GroupBadgeFactory

    monkeypatch.setattr(svc, 'static', lambda p: (_ for _ in ()).throw(ValueError('missing')))

    gb = GroupBadgeFactory()
    tier, layers, is_avatar = svc.group_medallion_layers(gb)

    assert tier, 'the backing metal is unaffected by the plate'
    assert isinstance(layers, list), 'the caller still gets a layer list rather than an exception'
