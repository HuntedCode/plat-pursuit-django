"""Uploads may only ever reach Pillow's JPEG, PNG, WEBP and GIF decoders.

Pillow ships many more parsers (PSD, EPS, JPEG2000, TIFF, ...), and several have had memory
corruption bugs. Without `formats=`, `Image.open()` sniffs the bytes and runs whichever parser
matches, so a crafted file reached that parser before `validate_image` ever checked the format.
These tests pin the allowlist at the point it matters: the parser for a disallowed format must
never run, not just be rejected afterwards.
"""
from io import BytesIO

import pytest
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import InMemoryUploadedFile, SimpleUploadedFile
from PIL import Image, TiffImagePlugin

from trophies import image_utils
from trophies.image_utils import (
    ALLOWED_FORMATS_TEXT,
    ALLOWED_IMAGE_FORMATS,
    MAX_IMAGE_DIMENSION,
    PROCESSING_FAILED_MESSAGE,
    optimize_image,
    process_roadmap_image,
    validate_image,
)
from tests.images import gps_of, photo_with_gps

DISALLOWED = ['TIFF', 'BMP']


def _upload(fmt, size=(4, 4), name=None, mode='RGB', **save_kwargs):
    buf = BytesIO()
    Image.new(mode, size).save(buf, format=fmt, **save_kwargs)
    return SimpleUploadedFile(name or f'upload.{fmt.lower()}', buf.getvalue())


@pytest.fixture
def tiff_parser_runs(monkeypatch):
    """Record every run of the TIFF parser (and fail that open).

    Tests assert on the record rather than on the error, because the pipelines turn every
    exception into a ValidationError, which would mask a raised tripwire.
    """
    runs = []

    def tripwire(self):
        runs.append(self)
        raise AssertionError('the TIFF parser ran on an upload')
    monkeypatch.setattr(TiffImagePlugin.TiffImageFile, '_open', tripwire)
    return runs


# --- validate_image ---------------------------------------------------------------------------

@pytest.mark.parametrize('fmt', ALLOWED_IMAGE_FORMATS)
def test_allowed_formats_pass_and_rewind(fmt):
    upload = _upload(fmt)
    assert validate_image(upload) is True
    assert upload.tell() == 0


def test_transparent_png_passes():
    assert validate_image(_upload('PNG', mode='RGBA')) is True


def test_animated_gif_passes():
    frames = [Image.new('P', (4, 4), color) for color in (1, 2, 3)]
    buf = BytesIO()
    frames[0].save(buf, format='GIF', save_all=True, append_images=frames[1:])
    assert validate_image(SimpleUploadedFile('anim.gif', buf.getvalue())) is True


@pytest.mark.parametrize('fmt', DISALLOWED)
def test_other_formats_are_rejected_with_the_accepted_list(fmt):
    with pytest.raises(ValidationError) as exc:
        validate_image(_upload(fmt), image_type='banner image')
    assert exc.value.messages[0] == f'Banner image must be a valid {ALLOWED_FORMATS_TEXT} image.'


def test_tripwire_fires_without_the_allowlist(tiff_parser_runs):
    """Guards the tests below: an unrestricted open really would run the TIFF parser."""
    with pytest.raises(AssertionError, match='TIFF parser ran'):
        Image.open(_upload('TIFF'))
    assert len(tiff_parser_runs) == 1


def test_disallowed_parser_never_runs(tiff_parser_runs):
    with pytest.raises(ValidationError):
        validate_image(_upload('TIFF'))
    assert tiff_parser_runs == []


def test_jpeg_variant_is_rejected_by_name():
    """MPO (multi-picture phone photos) comes back from the JPEG decoder under its own name."""
    second_frame = Image.new('RGB', (4, 4), 'blue')
    upload = _upload('MPO', name='photo.jpg', save_all=True, append_images=[second_frame])
    with pytest.raises(ValidationError, match='Uploaded file is MPO'):
        validate_image(upload)


def test_non_image_bytes_are_rejected():
    with pytest.raises(ValidationError, match=ALLOWED_FORMATS_TEXT):
        validate_image(SimpleUploadedFile('notes.png', b'definitely not an image'))


def test_truncated_image_is_rejected():
    """A real PNG header with the rest cut off: opens, then fails verification."""
    whole = _upload('PNG', size=(64, 64)).read()
    with pytest.raises(ValidationError):
        validate_image(SimpleUploadedFile('cut.png', whole[: len(whole) // 2]))


def test_oversized_file_is_rejected_before_decoding(tiff_parser_runs):
    upload = _upload('TIFF')
    with pytest.raises(ValidationError, match='must be under 0.0001MB'):
        validate_image(upload, max_size_mb=0.0001)
    assert tiff_parser_runs == []


def test_oversized_dimensions_are_rejected():
    with pytest.raises(ValidationError, match=f'{MAX_IMAGE_DIMENSION}px or less'):
        validate_image(_upload('PNG', size=(MAX_IMAGE_DIMENSION + 1, 1)))


# --- the processing pipelines -----------------------------------------------------------------

def test_optimize_image_processes_an_allowed_format():
    result = optimize_image(_upload('PNG', size=(64, 64)), max_width=32, max_height=32)
    assert isinstance(result, InMemoryUploadedFile)
    assert Image.open(result).size == (32, 32)


def test_roadmap_pipeline_processes_an_allowed_format():
    result = process_roadmap_image(_upload('PNG', size=(64, 64)), watermark=False, max_dimension=32)
    assert isinstance(result, InMemoryUploadedFile)
    assert Image.open(result).size == (32, 32)


@pytest.mark.parametrize('pipeline', [
    lambda f: optimize_image(f),
    lambda f: process_roadmap_image(f, watermark=False),
], ids=['optimize_image', 'process_roadmap_image'])
def test_pipelines_never_run_a_disallowed_parser(pipeline, tiff_parser_runs):
    upload = _upload('TIFF')
    with pytest.raises(ValidationError):
        pipeline(upload)
    assert tiff_parser_runs == []


def test_card_renderer_embeds_other_formats_without_parsing_them(tmp_path, tiff_parser_runs):
    """Share cards inline /media/ files, which include admin uploads that skipped validate_image."""
    from core.services.playwright_renderer import _file_to_data_uri_resized

    path = tmp_path / 'badge.tif'
    path.write_bytes(_upload('TIFF').read())
    assert _file_to_data_uri_resized(str(path)).startswith('data:image/tiff;base64,')
    assert tiff_parser_runs == []


# --- what the pipelines store -----------------------------------------------------------------
# A stored upload must carry none of the uploader's metadata and must be named for what it
# really is. Both used to fail: a greyscale or CMYK JPEG crashed the watermark step, and the
# fallback stored the raw file, GPS included; and an upload kept its own extension, so a PNG
# named `x.html` was stored (and would be served) as HTML.

def test_the_test_photo_really_carries_gps():
    """Guards the tests below: without this, 'GPS was stripped' would pass vacuously."""
    assert gps_of(photo_with_gps()) is not None


@pytest.mark.parametrize('mode', ['RGB', 'L', 'CMYK'])
def test_roadmap_photos_of_every_colour_mode_are_watermarked_and_stripped(mode):
    stored = process_roadmap_image(photo_with_gps(mode=mode), watermark=True)
    assert stored.name == 'photo.jpg'
    assert Image.open(stored).mode == 'RGB'
    assert gps_of(stored) is None


@pytest.mark.parametrize('mode', ['RGB', 'L', 'CMYK'])
def test_banner_photos_of_every_colour_mode_are_stripped(mode):
    stored = optimize_image(photo_with_gps(mode=mode))
    assert stored.name == 'photo.jpg'
    assert gps_of(stored) is None


def test_roadmap_photos_are_still_turned_upright():
    """EXIF orientation is applied before the metadata goes: a sideways phone shot stands up."""
    stored = process_roadmap_image(photo_with_gps(size=(64, 32), orientation=6), watermark=False)
    assert Image.open(stored).size == (32, 64)


def test_sixteen_bit_greyscale_keeps_its_tones():
    """A straight convert clips 16-bit samples, which turned a mid-grey PNG solid white."""
    buf = BytesIO()
    Image.new('I;16', (4, 4), 30000).save(buf, format='PNG')
    stored = optimize_image(SimpleUploadedFile('scan.png', buf.getvalue()))
    assert Image.open(stored).convert('L').getpixel((0, 0)) == 30000 // 256


def _png(img, **save_kwargs):
    buf = BytesIO()
    img.save(buf, format='PNG', **save_kwargs)
    return SimpleUploadedFile('sticker.png', buf.getvalue())


def _first_pixel(stored):
    return Image.open(stored).convert('RGB').getpixel((0, 0))


def test_transparent_greyscale_is_flattened_onto_white():
    stored = optimize_image(_png(Image.new('LA', (4, 4), (0, 0))))
    assert _first_pixel(stored) == image_utils.FLATTEN_BACKGROUND


def test_a_transparent_palette_colour_is_flattened_onto_white():
    img = Image.new('P', (4, 4), 0)
    img.putpalette([10, 20, 30] + [0] * 765)  # index 0 is a dark colour, marked transparent
    stored = optimize_image(_png(img, transparency=0))
    assert _first_pixel(stored) == image_utils.FLATTEN_BACKGROUND


@pytest.mark.parametrize('pipeline', [
    lambda f: process_roadmap_image(f, watermark=False),
    optimize_image,
], ids=['process_roadmap_image', 'optimize_image'])
@pytest.mark.parametrize('fmt, uploaded_as, stored_as', [
    ('PNG', 'x.html', 'x.png'),
    ('WEBP', 'evil.svg', 'evil.webp'),
    ('JPEG', 'shot.jpeg', 'shot.jpg'),
    ('PNG', 'no-extension', 'no-extension.png'),
    ('PNG', '.png', 'png.png'),  # never a hidden dotfile
    ('PNG', ('a' * 300) + '.png', ('a' * image_utils.MAX_STORED_STEM_LENGTH) + '.png'),
])
def test_stored_names_follow_the_saved_format(pipeline, fmt, uploaded_as, stored_as):
    assert pipeline(_upload(fmt, name=uploaded_as)).name == stored_as


def test_stored_size_is_the_encoded_size():
    stored = optimize_image(_upload('PNG', size=(64, 64)))
    assert stored.size == len(stored.read())


@pytest.mark.parametrize('pipeline, break_step', [
    (lambda f: process_roadmap_image(f, watermark=True), '_apply_watermark'),
    (optimize_image, '_flatten_to_rgb'),
], ids=['process_roadmap_image', 'optimize_image'])
def test_an_unprocessable_upload_is_refused_not_stored_raw(pipeline, break_step, monkeypatch):
    def broken(*args, **kwargs):
        raise OSError('simulated Pillow failure')
    monkeypatch.setattr(image_utils, break_step, broken)
    with pytest.raises(ValidationError) as exc:
        pipeline(photo_with_gps())
    assert exc.value.messages == [PROCESSING_FAILED_MESSAGE]
