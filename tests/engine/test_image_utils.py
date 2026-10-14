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

from trophies.image_utils import (
    ALLOWED_FORMATS_TEXT,
    ALLOWED_IMAGE_FORMATS,
    MAX_IMAGE_DIMENSION,
    optimize_image,
    process_roadmap_image,
    validate_image,
)

DISALLOWED = ['TIFF', 'BMP']


def _upload(fmt, size=(4, 4), name=None, mode='RGB', **save_kwargs):
    buf = BytesIO()
    Image.new(mode, size).save(buf, format=fmt, **save_kwargs)
    return SimpleUploadedFile(name or f'upload.{fmt.lower()}', buf.getvalue())


@pytest.fixture
def tiff_parser_runs(monkeypatch):
    """Record every run of the TIFF parser (and fail that open).

    Tests assert on the record rather than on the error, because the pipelines catch every
    exception and fall back to the raw upload, which would swallow a raised tripwire.
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
    """Both fall back to the untouched upload on failure; the point is the parser never runs."""
    upload = _upload('TIFF')
    assert pipeline(upload) is upload
    assert tiff_parser_runs == []


def test_card_renderer_embeds_other_formats_without_parsing_them(tmp_path, tiff_parser_runs):
    """Share cards inline /media/ files, which include admin uploads that skipped validate_image."""
    from core.services.playwright_renderer import _file_to_data_uri_resized

    path = tmp_path / 'badge.tif'
    path.write_bytes(_upload('TIFF').read())
    assert _file_to_data_uri_resized(str(path)).startswith('data:image/tiff;base64,')
    assert tiff_parser_runs == []
