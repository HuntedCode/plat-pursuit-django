"""
Guide image service - Handles image upload validation and management.

This service manages:
- Validating image uploads (type, size, count limits)
- Creating and deleting GuideImage records
- Enforcing tier-based limits (basic vs premium)
"""
import os
import logging
from django.core.exceptions import ValidationError, PermissionDenied

from trophies.constants import PREMIUM_MAX_IMAGES, PREMIUM_MAX_FILE_SIZE, BASIC_MAX_IMAGES, BASIC_MAX_FILE_SIZE, ALLOWED_CONTENT_TYPES, ALLOWED_EXTENSIONS
from trophies.models import GuideImage

logger = logging.getLogger(__name__)


class GuideImageService:
    """Handles guide image operations with tier-based limits."""

    @staticmethod
    def get_limits(profile):
        """
        Get image limits for a profile based on premium status.

        Returns:
            dict: {max_images, max_file_size, max_file_size_mb}
        """
        is_premium = profile.user_is_premium

        if is_premium:
            return {
                'max_images': PREMIUM_MAX_IMAGES,
                'max_file_size': PREMIUM_MAX_FILE_SIZE,
                'max_file_size_mb': 5,
            }
        else:
            return {
                'max_images': BASIC_MAX_IMAGES,
                'max_file_size': BASIC_MAX_FILE_SIZE,
                'max_file_size_mb': 3,
            }

    @staticmethod
    def get_remaining_uploads(guide, profile):
        """
        Get remaining upload capacity for a guide.

        Returns:
            dict: {current, max, remaining, can_upload}
        """
        limits = GuideImageService.get_limits(profile)
        current_count = guide.images.count()

        return {
            'current': current_count,
            'max': limits['max_images'],
            'remaining': max(0, limits['max_images'] - current_count),
            'can_upload': current_count < limits['max_images'],
        }

    @staticmethod
    def validate_upload(guide, image_file, profile):
        """
        Validate an image upload.

        Args:
            guide: Guide instance
            image_file: Uploaded file object
            profile: Profile of uploader

        Returns:
            tuple[bool, str]: (is_valid, error_message)
        """
        # Check extension
        filename = image_file.name.lower()
        ext = os.path.splitext(filename)[1].lstrip('.')
        if ext not in ALLOWED_EXTENSIONS:
            allowed = ', '.join(ALLOWED_EXTENSIONS)
            return (False, f"File type '.{ext}' is not allowed. Use: {allowed}")

        # Check content type
        content_type = getattr(image_file, 'content_type', None)
        if content_type and content_type not in ALLOWED_CONTENT_TYPES:
            return (False, f"Invalid image type: {content_type}")

        # Check file size
        limits = GuideImageService.get_limits(profile)
        if image_file.size > limits['max_file_size']:
            max_mb = limits['max_file_size_mb']
            return (False, f"File too large. Maximum size is {max_mb}MB")

        # Check image count
        current_count = guide.images.count()
        if current_count >= limits['max_images']:
            return (False, f"Image limit reached ({limits['max_images']} images). Delete some images to upload more.")

        return (True, "")

    @staticmethod
    def upload_image(guide, image_file, profile, alt_text='', caption=''):
        """
        Upload and create GuideImage record.

        Args:
            guide: Guide instance
            image_file: Uploaded file object
            profile: Profile of uploader
            alt_text: Optional alt text for accessibility
            caption: Optional caption

        Returns:
            GuideImage: The created image record

        Raises:
            PermissionDenied: If not guide author
            ValidationError: If validation fails
        """
        # Check permission
        if profile.id != guide.author_id:
            raise PermissionDenied("Only the guide author can upload images")

        # Validate
        is_valid, error = GuideImageService.validate_upload(guide, image_file, profile)
        if not is_valid:
            raise ValidationError(error)

        # Create image (model's save() handles file_size, width, height)
        image = GuideImage.objects.create(
            guide=guide,
            image=image_file,
            alt_text=alt_text,
            caption=caption
        )

        logger.info(
            f"Image uploaded: {image.id} for guide {guide.id} "
            f"by {profile.psn_username} ({image.file_size} bytes)"
        )

        return image

    @staticmethod
    def delete_image(image, profile):
        """
        Delete an image.

        Args:
            image: GuideImage instance
            profile: Profile requesting deletion

        Returns:
            bool: True if deleted

        Raises:
            PermissionDenied: If not author or staff
        """
        # Check permission (author or staff)
        is_author = profile.id == image.guide.author_id
        is_staff = profile.user and profile.user.is_staff

        if not (is_author or is_staff):
            raise PermissionDenied("You don't have permission to delete this image")

        # Store info for logging
        guide_id = image.guide_id
        image_id = image.id

        # Delete file from storage
        if image.image:
            try:
                image.image.delete(save=False)
            except Exception as e:
                logger.warning(f"Failed to delete image file: {e}")

        # Delete record
        image.delete()

        logger.info(f"Image deleted: {image_id} from guide {guide_id} by {profile.psn_username}")

        return True

    @staticmethod
    def get_guide_images(guide):
        """Get all images for a guide, ordered by upload time."""
        return guide.images.all().order_by('uploaded_at')

    @staticmethod
    def get_image_by_id(guide, image_id):
        """
        Get a specific image, ensuring it belongs to the guide.

        Returns:
            GuideImage or None
        """
        try:
            return guide.images.get(id=image_id)
        except GuideImage.DoesNotExist:
            return None
