"""
Image processing utilities for checklist images.
Handles optimization, resizing, and compression.
"""
from PIL import Image
from io import BytesIO
from django.core.files.uploadedfile import InMemoryUploadedFile
import sys
import logging

logger = logging.getLogger('psn_api')


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
