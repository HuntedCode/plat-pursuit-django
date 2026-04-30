"""
Image processing utilities for checklist images.
Handles optimization, resizing, and compression.
"""
import logging
import os
import sys
from io import BytesIO

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import InMemoryUploadedFile
from PIL import Image, ImageDraw, ImageFont, ImageOps

logger = logging.getLogger('psn_api')


ALLOWED_IMAGE_FORMATS = ['JPEG', 'PNG', 'WEBP', 'GIF']
MAX_IMAGE_DIMENSION = 3840  # 4K resolution

# Roadmap-specific upload cap. Phone photos and screenshots are typically 2-4K
# wide; larger than this is wasted bandwidth for in-line guide imagery.
ROADMAP_MAX_DIMENSION = 2400
ROADMAP_WATERMARK_TEXT = 'www.platpursuit.com'


def optimize_image(image_file, max_width=3840, max_height=3840, quality=85):
    """
    Optimize uploaded image: resize, compress, strip metadata.

    Args:
        image_file: UploadedFile instance
        max_width: Maximum width in pixels
        max_height: Maximum height in pixels
        quality: JPEG quality (1-100)

    Returns:
        InMemoryUploadedFile: Optimized image
    """
    try:
        img = Image.open(image_file)
        original_format = img.format

        # Convert RGBA/LA to RGB (handle transparency)
        if img.mode in ('RGBA', 'LA', 'P'):
            background = Image.new('RGB', img.size, (255, 255, 255))
            if img.mode == 'RGBA':
                background.paste(img, mask=img.split()[-1])
            else:
                background.paste(img)
            img = background

        # Resize if too large
        if img.width > max_width or img.height > max_height:
            img.thumbnail((max_width, max_height), Image.Resampling.LANCZOS)

        # Save to BytesIO
        output = BytesIO()
        save_format = 'JPEG' if original_format in ['JPEG', 'JPG'] else original_format

        # Save with optimization
        img.save(
            output,
            format=save_format,
            quality=quality,
            optimize=True
        )
        output.seek(0)

        # Create InMemoryUploadedFile
        optimized_file = InMemoryUploadedFile(
            output,
            'ImageField',
            image_file.name,
            f'image/{save_format.lower()}',
            sys.getsizeof(output),
            None
        )

        logger.info(f"Optimized {image_file.name}: {img.width}x{img.height}")
        return optimized_file

    except Exception as e:
        logger.error(f"Image optimization failed: {e}")
        # Return original on failure
        image_file.seek(0)
        return image_file


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
        img = Image.open(image_file)
        img.verify()  # Verify it's a valid image

        # Re-open after verify (verify closes the file)
        image_file.seek(0)
        img = Image.open(image_file)

        # Check format
        if img.format not in ALLOWED_IMAGE_FORMATS:
            raise ValidationError(
                f"{image_type.capitalize()} must be JPEG, PNG, WEBP, or GIF format. "
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

    Writes onto an RGBA overlay and composites back to the source's mode so
    JPEG output stays JPEG. Adds a soft drop shadow for legibility on light
    and dark images alike.
    """
    if img.mode != 'RGBA':
        base = img.convert('RGBA')
    else:
        base = img.copy()

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

    composited = Image.alpha_composite(base, overlay)
    if img.mode == 'RGB':
        return composited.convert('RGB')
    return composited


def process_roadmap_image(image_file, watermark=True, max_dimension=ROADMAP_MAX_DIMENSION,
                          quality=85):
    """Full pipeline for roadmap image uploads.

    EXIF auto-rotate, metadata strip, max-dimension cap, optional watermark,
    re-encode at the given quality. Returns an `InMemoryUploadedFile` ready
    for `default_storage.save()`.

    Falls back to returning the original file (rewound) on any processing
    error so an unexpected Pillow edge case can't block an upload entirely.
    """
    try:
        img = Image.open(image_file)
        original_format = img.format

        # Auto-rotate based on EXIF orientation BEFORE stripping metadata.
        # exif_transpose returns a rotated copy with EXIF orientation cleared.
        img = ImageOps.exif_transpose(img) or img

        # Convert RGBA / palette / LA to RGB for non-PNG output paths.
        if img.mode in ('RGBA', 'LA', 'P'):
            background = Image.new('RGB', img.size, (255, 255, 255))
            if img.mode == 'RGBA':
                background.paste(img, mask=img.split()[-1])
            elif img.mode == 'LA':
                background.paste(img.convert('RGBA'), mask=img.convert('RGBA').split()[-1])
            else:
                background.paste(img.convert('RGBA'))
            img = background

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
        output.seek(0)

        new_name = image_file.name
        if save_format == 'JPEG' and not new_name.lower().endswith(('.jpg', '.jpeg')):
            new_name = new_name.rsplit('.', 1)[0] + '.jpg'

        return InMemoryUploadedFile(
            output, 'ImageField', new_name,
            f'image/{save_format.lower()}',
            sys.getsizeof(output), None,
        )
    except Exception:
        logger.exception("Roadmap image processing failed; falling back to raw upload.")
        try:
            image_file.seek(0)
        except Exception:
            pass
        return image_file
