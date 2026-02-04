"""
Image processing utilities for checklist images.
Handles optimization, resizing, and compression.
"""
from PIL import Image
from io import BytesIO
from django.core.files.uploadedfile import InMemoryUploadedFile
from django.core.exceptions import ValidationError
import sys
import logging

logger = logging.getLogger('psn_api')


ALLOWED_IMAGE_FORMATS = ['JPEG', 'PNG', 'WEBP', 'GIF']
MAX_IMAGE_DIMENSION = 2048


def optimize_image(image_file, max_width=2048, max_height=2048, quality=85):
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
