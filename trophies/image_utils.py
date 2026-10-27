"""
Image processing utilities for checklist images.
Handles optimization, resizing, and compression.
"""
import logging
import os
from io import BytesIO

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import InMemoryUploadedFile
from PIL import Image, ImageDraw, ImageFont, ImageOps, UnidentifiedImageError

logger = logging.getLogger('psn_api')


# Also the only decoders Pillow may run on an upload (see open_upload), so its
# other parsers (PSD, EPS, JPEG2000, TIFF, ...) are never reachable from user input.
ALLOWED_IMAGE_FORMATS = ['JPEG', 'PNG', 'WEBP', 'GIF']
ALLOWED_FORMATS_TEXT = 'JPEG, PNG, WEBP, or GIF'
MAX_IMAGE_DIMENSION = 3840  # 4K resolution

# Roadmap-specific upload cap. Phone photos and screenshots are typically 2-4K
# wide; larger than this is wasted bandwidth for in-line guide imagery.
ROADMAP_MAX_DIMENSION = 2400
ROADMAP_WATERMARK_TEXT = 'www.platpursuit.com'

#: The extension each pipeline output gets. A stored name never keeps the uploader's own
#: extension: storage serves by extension, so a PNG named `x.html` would be served as HTML.
SAVED_EXTENSIONS = {'JPEG': 'jpg', 'PNG': 'png', 'WEBP': 'webp', 'GIF': 'gif'}

#: Shown when a pipeline cannot process an upload. The upload is refused rather than stored
#: raw, because a raw file keeps its metadata (EXIF, including GPS) and its own extension.
PROCESSING_FAILED_MESSAGE = (
    "We couldn't process this image. Try saving it as a JPEG or PNG and uploading it again."
)

#: Transparency is flattened onto this colour (the guides and banners sit on light cards).
FLATTEN_BACKGROUND = (255, 255, 255)

#: Longest name stem kept from an upload. Storage names have length limits (255 bytes on most
#: filesystems, before the roadmap UUID prefix), and nothing past this helps anyone.
MAX_STORED_STEM_LENGTH = 64

#: 16-bit samples are scaled by this to 8 bits before conversion.
SIXTEEN_TO_EIGHT_BIT = 1 / 256


def open_upload(image_file):
    """Open an uploaded file with only the allowed decoders.

    Without `formats=`, Pillow sniffs the bytes and runs whichever parser
    matches, so a crafted PSD or EPS reaches that parser before any format
    check runs. A file in any other format raises UnidentifiedImageError.
    """
    return Image.open(image_file, formats=ALLOWED_IMAGE_FORMATS)


def _flatten_to_rgb(img):
    """Return `img` as RGB: transparency composited onto white, any other mode converted.

    Everything downstream (the watermark, the JPEG encoder) expects RGB. Greyscale (L) and
    CMYK photos used to slip through unconverted, and the watermark then produced RGBA,
    which JPEG cannot store.
    """
    if img.mode == 'RGB':
        return img
    if img.mode in ('RGBA', 'LA', 'PA') or (img.mode == 'P' and 'transparency' in img.info):
        rgba = img.convert('RGBA')
        background = Image.new('RGB', img.size, FLATTEN_BACKGROUND)
        background.paste(rgba, mask=rgba.split()[-1])
        return background
    if img.mode.startswith('I'):
        # 16-bit greyscale (how Pillow opens a 16-bit PNG). A straight convert clips every
        # sample above 255, which turns almost the whole image white, so scale it down first.
        img = img.convert('I').point(lambda v: v * SIXTEEN_TO_EIGHT_BIT).convert('L')
    return img.convert('RGB')


def _as_upload(output, original_name, save_format):
    """Wrap encoded bytes as an upload named for the format they were saved in.

    The stem is trimmed of leading dots (so `.png` cannot become a hidden `.png.png`) and
    capped at MAX_STORED_STEM_LENGTH.
    """
    base_stem = os.path.splitext(os.path.basename(original_name or ''))[0]
    stem = base_stem.lstrip('.')[:MAX_STORED_STEM_LENGTH] or 'image'
    output.seek(0)
    return InMemoryUploadedFile(
        output, 'ImageField', f'{stem}.{SAVED_EXTENSIONS[save_format]}',
        f'image/{save_format.lower()}', output.getbuffer().nbytes, None,
    )


def optimize_image(image_file, max_width=3840, max_height=3840, quality=85):
    """
    Optimize uploaded image: resize, compress, strip metadata.

    Args:
        image_file: UploadedFile instance
        max_width: Maximum width in pixels
        max_height: Maximum height in pixels
        quality: JPEG quality (1-100)

    Returns:
        InMemoryUploadedFile: Optimized image, named for its format

    Raises:
        ValidationError: If the image cannot be processed. The raw upload is never
            returned in its place (see PROCESSING_FAILED_MESSAGE).
    """
    try:
        img = open_upload(image_file)
        original_format = img.format
        img = _flatten_to_rgb(img)

        # Resize if too large
        if img.width > max_width or img.height > max_height:
            img.thumbnail((max_width, max_height), Image.Resampling.LANCZOS)

        # Save to BytesIO (re-encoding drops all metadata)
        output = BytesIO()
        save_format = 'JPEG' if original_format in ['JPEG', 'JPG'] else original_format
        img.save(
            output,
            format=save_format,
            quality=quality,
            optimize=True
        )

        logger.info(f"Optimized {image_file.name}: {img.width}x{img.height}")
        return _as_upload(output, image_file.name, save_format)

    except Exception as e:
        logger.exception("Image optimization failed; refusing the upload.")
        raise ValidationError(PROCESSING_FAILED_MESSAGE) from e


def validate_image(image_file, max_size_mb=5, image_type='image'):
    """
    Validate uploaded image file.

    Args:
        image_file: UploadedFile instance
        max_size_mb: Maximum file size in megabytes
        image_type: Description of image type for error messages

    Raises:
        ValidationError: If image fails validation

    Returns:
        True if validation passes
    """
    # Check file size
    max_size_bytes = max_size_mb * 1024 * 1024
    if image_file.size > max_size_bytes:
        raise ValidationError(
            f"{image_type.capitalize()} must be under {max_size_mb}MB. "
            f"Uploaded file is {image_file.size / (1024 * 1024):.1f}MB."
        )

    # Validate image format and dimensions
    try:
        img = open_upload(image_file)
        img.verify()  # Verify it's a valid image

        # Re-open after verify (verify closes the file)
        image_file.seek(0)
        img = open_upload(image_file)

        # Still needed: a JPEG decoder can hand back a variant such as MPO
        # (multi-picture phone photos), which reports its own format name.
        if img.format not in ALLOWED_IMAGE_FORMATS:
            raise ValidationError(
                f"{image_type.capitalize()} must be {ALLOWED_FORMATS_TEXT} format. "
                f"Uploaded file is {img.format}."
            )

        # Check dimensions
        if img.width > MAX_IMAGE_DIMENSION or img.height > MAX_IMAGE_DIMENSION:
            raise ValidationError(
                f"{image_type.capitalize()} dimensions must be {MAX_IMAGE_DIMENSION}px or less. "
                f"Uploaded image is {img.width}x{img.height}px."
            )

        # Reset file pointer for subsequent operations
        image_file.seek(0)
        return True

    except UnidentifiedImageError as e:
        # Another format, not an image at all, or an allowed format whose decoder
        # rejected the contents (Pillow reports all three the same way). Caught
        # before OSError, its parent, so the message names the accepted formats.
        logger.warning(f"Image validation rejected an unreadable upload: {e}")
        raise ValidationError(
            f"{image_type.capitalize()} must be a valid {ALLOWED_FORMATS_TEXT} image."
        )
    except (IOError, OSError) as e:
        logger.error(f"Image validation failed: {e}")
        raise ValidationError(
            f"{image_type.capitalize()} file appears to be corrupted or invalid."
        )


# Watermark sizing constants. Tuned so the watermark reads as obvious
# attribution on a 1080p screenshot without dominating the image. Values
# are fractions of the image width.
WATERMARK_FONT_SIZE_PCT = 0.025    # ~48px on 1080p, ~60px on 2400p
WATERMARK_MARGIN_PCT = 0.015       # ~29px on 1080p, ~36px on 2400p
WATERMARK_SHADOW_PCT = 0.0015      # ~3px on 1080p


def _watermark_font(image_width):
    """Load the bold watermark font sized proportionally to the image width.

    Raises if the TTF can't be loaded. Falling back to PIL's default
    bitmap font silently produced ~10px text regardless of image size,
    which is exactly the bug we don't want to ship.

    Note: this uses Poppins-Bold instead of Inter_24pt-Bold because the
    Inter Bold/SemiBold TTFs in `static/fonts/` were corrupted at some
    point (CRLF mangling on a Windows git checkout, header bytes
    rewritten to 0x0A). Poppins-Bold is already bundled, intact, and
    reads well as a stamped watermark.
    """
    target_size = max(20, int(image_width * WATERMARK_FONT_SIZE_PCT))
    font_path = os.path.join(settings.BASE_DIR, 'static', 'fonts', 'Poppins-Bold.ttf')
    font = ImageFont.truetype(font_path, target_size)
    logger.info(
        "Watermark font loaded: %s @ %dpx (image_width=%d)",
        font_path, target_size, image_width,
    )
    return font


def _apply_watermark(img, text=ROADMAP_WATERMARK_TEXT):
    """Composite a semi-transparent watermark onto the bottom-right corner.

    Takes and returns RGB (process_roadmap_image flattens first), writing
    through an RGBA overlay. Adds a soft drop shadow for legibility on light
    and dark images alike.
    """
    base = img.convert('RGBA')

    overlay = Image.new('RGBA', base.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    font = _watermark_font(base.width)

    # Margin scales with image so the watermark doesn't crowd small images.
    margin = max(10, int(base.width * WATERMARK_MARGIN_PCT))

    # Anchor right-bottom so the rendered glyphs sit exactly `margin` pixels
    # from the right and bottom edges (avoids the off-by-ascent we'd get from
    # using anchor=(0,0) and trying to subtract bbox top/bottom manually).
    x = base.width - margin
    y = base.height - margin

    shadow_offset = max(2, int(base.width * WATERMARK_SHADOW_PCT))
    # `rd` anchor = right-descender: pins the right edge + lowest descender
    # to (x, y), so all glyphs (including the `p` descenders in
    # "platpursuit") stay inside the image with consistent margin.
    draw.text(
        (x + shadow_offset, y + shadow_offset),
        text, font=font, fill=(0, 0, 0, 200), anchor='rd',
    )
    draw.text((x, y), text, font=font, fill=(255, 255, 255, 235), anchor='rd')

    return Image.alpha_composite(base, overlay).convert('RGB')


def process_roadmap_image(image_file, watermark=True, max_dimension=ROADMAP_MAX_DIMENSION,
                          quality=85):
    """Full pipeline for roadmap image uploads.

    EXIF auto-rotate, metadata strip, max-dimension cap, optional watermark,
    re-encode at the given quality. Returns an `InMemoryUploadedFile`, named
    for its output format, ready for `default_storage.save()`.

    Raises ValidationError if anything fails. It used to fall back to the raw
    upload, which stored the file with its EXIF (GPS included) and its own
    extension; refusing the upload is the safe failure.
    """
    try:
        img = open_upload(image_file)
        original_format = img.format

        # Auto-rotate based on EXIF orientation BEFORE stripping metadata.
        # exif_transpose returns a rotated copy with EXIF orientation cleared.
        img = ImageOps.exif_transpose(img) or img
        img = _flatten_to_rgb(img)

        if img.width > max_dimension or img.height > max_dimension:
            img.thumbnail((max_dimension, max_dimension), Image.Resampling.LANCZOS)

        if watermark:
            img = _apply_watermark(img)

        save_format = 'JPEG' if original_format in ('JPEG', 'JPG', None) else original_format
        if save_format not in ('JPEG', 'PNG', 'WEBP'):
            # Animated GIFs lose their animation through this pipeline; we
            # accept that to standardize on a still-image output for guides.
            save_format = 'JPEG'

        output = BytesIO()
        save_kwargs = {'format': save_format, 'optimize': True}
        if save_format == 'JPEG':
            save_kwargs['quality'] = quality
        elif save_format == 'WEBP':
            save_kwargs['quality'] = quality
            save_kwargs['method'] = 6
        img.save(output, **save_kwargs)
        return _as_upload(output, image_file.name, save_format)
    except Exception as e:
        logger.exception("Roadmap image processing failed; refusing the upload.")
        raise ValidationError(PROCESSING_FAILED_MESSAGE) from e
