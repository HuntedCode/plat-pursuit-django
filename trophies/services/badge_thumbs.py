"""Display-size copies of badge art (SW-06 / AC-01).

WHY. Badge art is uploaded at 1000 x 1000 (280 KB to 420 KB a PNG) and drawn at 38 to 260px. Every medallion
surface sent the upload itself: the fundraiser page downloaded 17 MB of art on a laptop, `/badges/` 3.4 MB of its
4.7 MB. A 512px WebP is sharp at the largest medallion (the 260px inspector) on a 2x screen and weighs 25 KB to
50 KB.

NAMING IS THE STALENESS CHECK FOR READS. A copy's storage name is `<hash of the SOURCE's name>-<hash of the
copy's own bytes>.webp` (`thumb_prefix`, `write_thumb`). `GroupBadge.art_layers()` serves it only when its name
starts with the prefix of the source as named today, so a source renamed by a path that skipped `save()` (a
queryset `.update()`, a data fix) draws the full upload until the copy is remade. The check is string work, no
storage I/O, so it costs nothing per render. The content hash means a re-uploaded picture gets a NEW url (a
returning browser never shows the cached old copy), and a file already under that name is by construction the
same bytes, so it is reused without a delete.

WHEN A COPY IS MADE. `BadgeSeries.save()` / `GroupBadge.save()` re-render when the art CHANGED on that row: its
source name differs from the one the row was loaded with (`art_changed`). That covers an admin upload (it arrives
under its bare upload name, never the stored path, even when the S3 storage will overwrite the same key) and
`ArtRevealItem.release()` (it writes through `FieldFile.save`, which commits the file before the model's save
runs, but the row was loaded with no art). Every write renders from the source. A save that changes no
art does nothing at all, and never retries a render that failed. `python manage.py make_badge_thumbs` backfills
and repairs the rest.

What stays undetectable: a source file overwritten in storage with no change to the row at all. Nothing in the
app does that; after a manual S3 upload over an existing name, run `make_badge_thumbs --force`.

FAILURE. A copy is an optimisation. If rendering or the storage write fails (an S3 error, a permission), the
column is cleared, the full image keeps being served, and the failure is logged once; the save that triggered it
never fails. That matters most inside `ArtRevealItem.release()`, which runs in the reveal's transaction: a raise
here would roll back every reveal in the run.
"""
import hashlib
import logging
from io import BytesIO

from django.core.files.base import ContentFile
from PIL import Image

from trophies.image_utils import open_upload

logger = logging.getLogger(__name__)

#: Longest side of a display copy. The largest medallion is the inspector's 260px, so 512 is sharp at 2x.
THUMB_PX = 512
#: WebP quality: 85 keeps the hand-drawn line work clean at 512 (25 KB to 50 KB measured on prod art).
THUMB_QUALITY = 85
THUMB_DIR = 'badges/thumbs'


def thumb_prefix(source_name):
    """Every display copy of a source starts with this: the name IS the record of which source a copy was made
    from (see the module docstring)."""
    digest = hashlib.sha1(source_name.encode('utf-8')).hexdigest()[:20]
    return f'{THUMB_DIR}/{digest}-'


def thumb_is_current(source, thumb):
    """True when `thumb` is a display copy of `source` as named today. Pure string work: safe per render."""
    return bool(source) and bool(thumb) and thumb.name.startswith(thumb_prefix(source.name))


def render_thumb(source):
    """The WebP bytes of `source` scaled to fit THUMB_PX, transparency kept. None if it cannot be read."""
    try:
        source.open('rb')
        try:
            img = open_upload(source)
            img.load()
        finally:
            source.close()
        img = img.convert('RGBA')
        img.thumbnail((THUMB_PX, THUMB_PX), Image.Resampling.LANCZOS)
        out = BytesIO()
        img.save(out, format='WEBP', quality=THUMB_QUALITY, method=6)
        return out.getvalue()
    except Exception:
        logger.exception('Badge thumbnail render failed for %s; the full image will be served.', source.name)
        return None


def write_thumb(source):
    """Render `source`'s display copy into storage; return its name, or '' when there is none to make. Never
    raises: a failed render or storage write leaves the full image in use."""
    if not source:
        return ''
    data = render_thumb(source)
    if data is None:
        return ''
    name = f'{thumb_prefix(source.name)}{hashlib.sha1(data).hexdigest()[:12]}.webp'
    try:
        storage = source.storage
        if storage.exists(name):      # same name, same bytes: the content hash guarantees it
            return name
        return storage.save(name, ContentFile(data))
    except Exception:
        logger.exception('Badge thumbnail write failed for %s; the full image will be served.', source.name)
        return ''


def sync_thumb(instance, source_attr, thumb_attr, *, render):
    """After a save: re-render `instance.<thumb_attr>` from `instance.<source_attr>` when `render`, clear it when
    the source is gone, otherwise leave it. Writes the one column with a queryset update (no second `save()`, no
    signals). Returns True when the column changed."""
    source = getattr(instance, source_attr)
    thumb = getattr(instance, thumb_attr)
    if not source:
        want = ''
    elif render:
        want = write_thumb(source)
    else:
        return False
    if (thumb.name or '') == want:
        return False
    type(instance).objects.filter(pk=instance.pk).update(**{thumb_attr: want})
    setattr(instance, thumb_attr, want)
    return True


def art_changed(instance, source_attr):
    """Did this save change the art? True when the source name differs from the one the row was loaded with
    (`remember_source` stores it). A new upload always does: it carries its bare upload name until the save
    commits it under `upload_to`. A row that was never loaded counts as changed when it has art."""
    source = getattr(instance, source_attr)
    loaded = getattr(instance, f'_loaded_{source_attr}', None)
    if loaded is None:
        return bool(source)
    return (source.name or '') != loaded


def remember_source(instance, source_attr, field_names):
    """Record the source name a row was loaded with, for `art_changed`. Skips a deferred field, so loading with
    `.only()` never pays a query for it (a later save of that instance re-renders once, harmlessly)."""
    if source_attr in field_names:
        setattr(instance, f'_loaded_{source_attr}', getattr(instance, source_attr).name or '')
