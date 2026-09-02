"""Static-asset URL resolution that cannot take down its caller.

`django.templatetags.static.static()` RAISES under ManifestStaticFilesStorage when a name is not in
the manifest -- and, because a missing manifest reads as "every name is missing", it raises for the
FIRST call in a container that never ran collectstatic. The worker/cron image was exactly that, which
killed the hourly job with an error naming a badge backdrop committed in 2025 and present in the web
image's manifest. The asset was innocent; the image was the bug.

That cause is fixed in Dockerfile.worker. This exists for the blast radius, because the call sites are
badly placed for a hard failure: medallion art is resolved from `GroupBadge.art_layers()` and
`group_medallion_layers()`, which are reached from the badge detail page, the collection, browse
cards, the profile card, the share-card renderer AND cron. A decorative plate should not be able to
500 a page render or end a cron run.

Both medallion layers degrade the same way: absent. A badge whose art cannot be resolved renders as
its bare metal plate, which is a state the design already supports (`badge_views._forge_medallions`
calls it "the plain metal plate" and falls back to it deliberately on an empty catalogue).

Lives in util_modules because `trophies/models.py` needs it and cannot import from
`trophies/services/` without a cycle.
"""
import logging

from django.templatetags.static import static

logger = logging.getLogger(__name__)


def safe_static(path):
    """The static URL for `path`, or None if it cannot be resolved.

    Only swallows ValueError, which is what both failure modes raise: `MissingFileError` subclasses
    it, and so does the missing-manifest-entry error. Anything else is a real bug and propagates.
    """
    try:
        return static(path)
    except ValueError:
        # WARNING rather than DEBUG: this is always wrong, even when it is survivable. A quiet
        # degrade that nobody sees is how the cron ran green for weeks while the landing served a
        # fixture.
        logger.warning('static asset could not be resolved: %s', path)
        return None
