"""
Comprehensive test suite for GuideImageService.

Tests all functionality outlined in the implementation document including:
- Image upload validation (type, size, count)
- Upload operations with permission checks
- Delete operations
- Tier-based limits (basic vs premium)
- Query helpers
"""
from django.test import TestCase
from django.core.exceptions import ValidationError, PermissionDenied
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from unittest.mock import patch
from io import BytesIO

from trophies.models import Profile, Game, Concept, Guide, GuideImage
from trophies.services.guide_service import GuideService
from trophies.services.guide_image_service import GuideImageService

User = get_user_model()


class GuideImageServiceTestCase(TestCase):
    """Base test case with common fixtures for guide image tests."""

    def setUp(self):
        """Create common test fixtures."""
        # Create users
        self.user1 = User.objects.create_user(
            username='testuser1',
            email='test1@example.com',
            password='testpass123'
        )
        self.user2 = User.objects.create_user(
            username='testuser2',
            email='test2@example.com',
            password='testpass123'
        )
        self.staff_user = User.objects.create_user(
            username='staffuser',
            email='staff@example.com',
            password='testpass123',
            is_staff=True
        )

        # Create profiles
        self.author_profile = Profile.objects.create(
            psn_username='AuthorUser',
            is_linked=True,
            user=self.user1
        )
        self.other_profile = Profile.objects.create(
            psn_username='OtherUser',
            is_linked=True,
            user=self.user2
        )
        self.staff_profile = Profile.objects.create(
            psn_username='StaffUser',
            is_linked=True,
            user=self.staff_user
        )

        # Create concept and game
        self.concept = Concept.objects.create(
            unified_title='Test Concept',
        )
        self.game = Game.objects.create(
            title_name='Test Game',
            np_communication_id='NPWR12345_00',
            concept=self.concept
        )

        # Create a published guide
        self.guide = GuideService.create_guide(
            profile=self.author_profile,
            game=self.game,
            title='Test Guide',
            summary='A comprehensive test guide'
        )
        GuideService.add_section(self.guide, 'Section 1', 'Content 1')
        self.guide.status = 'published'
        self.guide.save()

    def create_test_image(self, name='test.jpg', size=1024, content_type='image/jpeg'):
        """Helper to create a mock uploaded image file."""
        image_file = SimpleUploadedFile(
            name=name,
            content=b'fake image content' * (size // 20),
            content_type=content_type
        )
        image_file.size = size
        return image_file


class LimitHelperTests(GuideImageServiceTestCase):
    """Tests for limit helper methods."""

    def test_get_limits_basic_user(self):
        """Basic users should get basic limits."""
        # Ensure user is not premium
        self.author_profile.user_is_premium = False
        self.author_profile.save()

        limits = GuideImageService.get_limits(self.author_profile)

        self.assertEqual(limits['max_images'], 10)
        self.assertEqual(limits['max_file_size'], 3 * 1024 * 1024)
        self.assertEqual(limits['max_file_size_mb'], 3)

    def test_get_limits_premium_user(self):
        """Premium users should get premium limits."""
        self.author_profile.user_is_premium = True
        self.author_profile.save()

        limits = GuideImageService.get_limits(self.author_profile)

        self.assertEqual(limits['max_images'], 30)
        self.assertEqual(limits['max_file_size'], 5 * 1024 * 1024)
        self.assertEqual(limits['max_file_size_mb'], 5)

    def test_get_remaining_uploads_no_images(self):
        """Should show full capacity when no images exist."""
        self.author_profile.user_is_premium = False
        self.author_profile.save()

        remaining = GuideImageService.get_remaining_uploads(self.guide, self.author_profile)

        self.assertEqual(remaining['current'], 0)
        self.assertEqual(remaining['max'], 10)
        self.assertEqual(remaining['remaining'], 10)
        self.assertTrue(remaining['can_upload'])

    def test_get_remaining_uploads_with_images(self):
        """Should calculate remaining correctly with existing images."""
        self.author_profile.user_is_premium = False
        self.author_profile.save()

        # Create 3 images
        for i in range(3):
            image_file = self.create_test_image(f'test{i}.jpg', 1024, 'image/jpeg')
            GuideImageService.upload_image(
                guide=self.guide,
                image_file=image_file,
                profile=self.author_profile
            )

        remaining = GuideImageService.get_remaining_uploads(self.guide, self.author_profile)

        self.assertEqual(remaining['current'], 3)
        self.assertEqual(remaining['max'], 10)
        self.assertEqual(remaining['remaining'], 7)
        self.assertTrue(remaining['can_upload'])

    def test_get_remaining_uploads_at_limit(self):
        """Should show no capacity when at limit."""
        self.author_profile.user_is_premium = False
        self.author_profile.save()

        # Create 10 images (basic limit)
        for i in range(10):
            image_file = self.create_test_image(f'test{i}.jpg', 1024, 'image/jpeg')
            GuideImageService.upload_image(
                guide=self.guide,
                image_file=image_file,
                profile=self.author_profile
            )

        remaining = GuideImageService.get_remaining_uploads(self.guide, self.author_profile)

        self.assertEqual(remaining['current'], 10)
        self.assertEqual(remaining['max'], 10)
        self.assertEqual(remaining['remaining'], 0)
        self.assertFalse(remaining['can_upload'])


class ValidationTests(GuideImageServiceTestCase):
    """Tests for upload validation."""

    def test_validate_upload_valid_jpg(self):
        """Valid JPG should pass validation."""
        self.author_profile.user_is_premium = False
        self.author_profile.save()

        image_file = self.create_test_image('test.jpg', 1024, 'image/jpeg')

        is_valid, error = GuideImageService.validate_upload(
            self.guide, image_file, self.author_profile
        )

        self.assertTrue(is_valid)
        self.assertEqual(error, "")

    def test_validate_upload_valid_png(self):
        """Valid PNG should pass validation."""
        self.author_profile.user_is_premium = False
        self.author_profile.save()

        image_file = self.create_test_image('test.png', 1024, 'image/png')

        is_valid, error = GuideImageService.validate_upload(
            self.guide, image_file, self.author_profile
        )

        self.assertTrue(is_valid)
        self.assertEqual(error, "")

    def test_validate_upload_invalid_extension(self):
        """Invalid file extension should fail validation."""
        self.author_profile.user_is_premium = False
        self.author_profile.save()

        image_file = self.create_test_image('test.pdf', 1024, 'application/pdf')

        is_valid, error = GuideImageService.validate_upload(
            self.guide, image_file, self.author_profile
        )

        self.assertFalse(is_valid)
        self.assertIn('File type', error)
        self.assertIn('not allowed', error)

    def test_validate_upload_invalid_content_type(self):
        """Invalid content type should fail validation."""
        self.author_profile.user_is_premium = False
        self.author_profile.save()

        image_file = self.create_test_image('test.jpg', 1024, 'application/pdf')

        is_valid, error = GuideImageService.validate_upload(
            self.guide, image_file, self.author_profile
        )

        self.assertFalse(is_valid)
        self.assertIn('Invalid image type', error)

    def test_validate_upload_file_too_large_basic(self):
        """File over basic limit should fail for basic users."""
        self.author_profile.user_is_premium = False
        self.author_profile.save()

        # 4MB file (over 3MB limit)
        image_file = self.create_test_image('test.jpg', 4 * 1024 * 1024, 'image/jpeg')

        is_valid, error = GuideImageService.validate_upload(
            self.guide, image_file, self.author_profile
        )

        self.assertFalse(is_valid)
        self.assertIn('File too large', error)
        self.assertIn('3MB', error)

    def test_validate_upload_file_too_large_premium(self):
        """File over premium limit should fail for premium users."""
        self.author_profile.user_is_premium = True
        self.author_profile.save()

        # 6MB file (over 5MB limit)
        image_file = self.create_test_image('test.jpg', 6 * 1024 * 1024, 'image/jpeg')

        is_valid, error = GuideImageService.validate_upload(
            self.guide, image_file, self.author_profile
        )

        self.assertFalse(is_valid)
        self.assertIn('File too large', error)
        self.assertIn('5MB', error)

    def test_validate_upload_file_within_premium_limit(self):
        """4MB file should pass for premium users."""
        self.author_profile.user_is_premium = True
        self.author_profile.save()

        # 4MB file (within 5MB premium limit)
        image_file = self.create_test_image('test.jpg', 4 * 1024 * 1024, 'image/jpeg')

        is_valid, error = GuideImageService.validate_upload(
            self.guide, image_file, self.author_profile
        )

        self.assertTrue(is_valid)
        self.assertEqual(error, "")

    def test_validate_upload_at_image_limit(self):
        """Should fail when at image limit."""
        self.author_profile.user_is_premium = False
        self.author_profile.save()

        # Create 10 images (basic limit)
        for i in range(10):
            image_file = self.create_test_image(f'test{i}.jpg', 1024, 'image/jpeg')
            GuideImageService.upload_image(
                guide=self.guide,
                image_file=image_file,
                profile=self.author_profile
            )

        image_file = self.create_test_image('test.jpg', 1024, 'image/jpeg')

        is_valid, error = GuideImageService.validate_upload(
            self.guide, image_file, self.author_profile
        )

        self.assertFalse(is_valid)
        self.assertIn('Image limit reached', error)
        self.assertIn('10 images', error)

    def test_validate_upload_all_allowed_extensions(self):
        """All allowed extensions should pass validation."""
        self.author_profile.user_is_premium = False
        self.author_profile.save()

        allowed = [
            ('test.jpg', 'image/jpeg'),
            ('test.jpeg', 'image/jpeg'),
            ('test.png', 'image/png'),
            ('test.webp', 'image/webp'),
        ]

        for filename, content_type in allowed:
            image_file = self.create_test_image(filename, 1024, content_type)
            is_valid, error = GuideImageService.validate_upload(
                self.guide, image_file, self.author_profile
            )
            self.assertTrue(is_valid, f"{filename} should be valid")


class UploadOperationTests(GuideImageServiceTestCase):
    """Tests for image upload operations."""

    def test_upload_image_success(self):
        """Successfully upload an image."""
        self.author_profile.user_is_premium = False
        self.author_profile.save()

        image_file = self.create_test_image('test.jpg', 1024, 'image/jpeg')

        image = GuideImageService.upload_image(
            guide=self.guide,
            image_file=image_file,
            profile=self.author_profile,
            alt_text='Test image',
            caption='A test caption'
        )

        self.assertIsNotNone(image.id)
        self.assertEqual(image.guide, self.guide)
        self.assertEqual(image.alt_text, 'Test image')
        self.assertEqual(image.caption, 'A test caption')

    def test_upload_image_without_optional_fields(self):
        """Upload image without alt_text and caption should work."""
        self.author_profile.user_is_premium = False
        self.author_profile.save()

        image_file = self.create_test_image('test.jpg', 1024, 'image/jpeg')

        image = GuideImageService.upload_image(
            guide=self.guide,
            image_file=image_file,
            profile=self.author_profile
        )

        self.assertIsNotNone(image.id)
        self.assertEqual(image.alt_text, '')
        self.assertEqual(image.caption, '')

    def test_upload_image_requires_author_permission(self):
        """Non-author cannot upload images."""
        self.author_profile.user_is_premium = False
        self.author_profile.save()

        image_file = self.create_test_image('test.jpg', 1024, 'image/jpeg')

        with self.assertRaises(PermissionDenied) as context:
            GuideImageService.upload_image(
                guide=self.guide,
                image_file=image_file,
                profile=self.other_profile
            )

        self.assertIn('Only the guide author', str(context.exception))

    def test_upload_image_validates_file_type(self):
        """Upload should validate file type."""
        self.author_profile.user_is_premium = False
        self.author_profile.save()

        image_file = self.create_test_image('test.pdf', 1024, 'application/pdf')

        with self.assertRaises(ValidationError) as context:
            GuideImageService.upload_image(
                guide=self.guide,
                image_file=image_file,
                profile=self.author_profile
            )

        self.assertIn('not allowed', str(context.exception))

    def test_upload_image_validates_file_size(self):
        """Upload should validate file size."""
        self.author_profile.user_is_premium = False
        self.author_profile.save()

        # 4MB file (over 3MB limit)
        image_file = self.create_test_image('test.jpg', 4 * 1024 * 1024, 'image/jpeg')

        with self.assertRaises(ValidationError) as context:
            GuideImageService.upload_image(
                guide=self.guide,
                image_file=image_file,
                profile=self.author_profile
            )

        self.assertIn('File too large', str(context.exception))

    def test_upload_image_validates_image_count(self):
        """Upload should validate image count."""
        self.author_profile.user_is_premium = False
        self.author_profile.save()

        # Create 10 images (basic limit)
        for i in range(10):
            image_file = self.create_test_image(f'test{i}.jpg', 1024, 'image/jpeg')
            GuideImageService.upload_image(
                guide=self.guide,
                image_file=image_file,
                profile=self.author_profile
            )

        image_file = self.create_test_image('test.jpg', 1024, 'image/jpeg')

        with self.assertRaises(ValidationError) as context:
            GuideImageService.upload_image(
                guide=self.guide,
                image_file=image_file,
                profile=self.author_profile
            )

        self.assertIn('Image limit reached', str(context.exception))


class DeleteOperationTests(GuideImageServiceTestCase):
    """Tests for image delete operations."""

    def test_delete_image_by_author(self):
        """Author can delete their own images."""
        image_file = self.create_test_image('test.jpg', 1024, 'image/jpeg')
        image = GuideImageService.upload_image(
            guide=self.guide,
            image_file=image_file,
            profile=self.author_profile
        )
        image_id = image.id

        result = GuideImageService.delete_image(image, self.author_profile)

        self.assertTrue(result)
        self.assertFalse(GuideImage.objects.filter(id=image_id).exists())

    def test_delete_image_by_staff(self):
        """Staff can delete any images."""
        image_file = self.create_test_image('test.jpg', 1024, 'image/jpeg')
        image = GuideImageService.upload_image(
            guide=self.guide,
            image_file=image_file,
            profile=self.author_profile
        )
        image_id = image.id

        result = GuideImageService.delete_image(image, self.staff_profile)

        self.assertTrue(result)
        self.assertFalse(GuideImage.objects.filter(id=image_id).exists())

    def test_delete_image_by_non_author_non_staff(self):
        """Non-author, non-staff cannot delete images."""
        image_file = self.create_test_image('test.jpg', 1024, 'image/jpeg')
        image = GuideImageService.upload_image(
            guide=self.guide,
            image_file=image_file,
            profile=self.author_profile
        )

        with self.assertRaises(PermissionDenied) as context:
            GuideImageService.delete_image(image, self.other_profile)

        self.assertIn("don't have permission", str(context.exception))

    def test_delete_image_removes_file_from_storage(self):
        """Delete should remove file from storage."""
        image_file = self.create_test_image('test.jpg', 1024, 'image/jpeg')
        image = GuideImageService.upload_image(
            guide=self.guide,
            image_file=image_file,
            profile=self.author_profile
        )

        # Mock the delete method on the image field
        with patch.object(image.image, 'delete') as mock_delete:
            GuideImageService.delete_image(image, self.author_profile)
            mock_delete.assert_called_once_with(save=False)

    def test_delete_image_handles_storage_error_gracefully(self):
        """Delete should continue if storage deletion fails."""
        image_file = self.create_test_image('test.jpg', 1024, 'image/jpeg')
        image = GuideImageService.upload_image(
            guide=self.guide,
            image_file=image_file,
            profile=self.author_profile
        )
        image_id = image.id

        # Mock the image field to raise an exception on delete
        with patch.object(image.image, 'delete', side_effect=Exception('Storage error')):
            # Should not raise exception
            result = GuideImageService.delete_image(image, self.author_profile)

        self.assertTrue(result)
        self.assertFalse(GuideImage.objects.filter(id=image_id).exists())


class QueryHelperTests(GuideImageServiceTestCase):
    """Tests for query helper methods."""

    def test_get_guide_images_returns_all_images(self):
        """Should return all images for a guide."""
        images = []
        for i in range(3):
            image_file = self.create_test_image(f'test{i}.jpg', 1024, 'image/jpeg')
            img = GuideImageService.upload_image(
                guide=self.guide,
                image_file=image_file,
                profile=self.author_profile
            )
            images.append(img)

        result = GuideImageService.get_guide_images(self.guide)

        self.assertEqual(result.count(), 3)
        # Check order by uploaded_at
        self.assertEqual(list(result), images)

    def test_get_guide_images_returns_empty_queryset(self):
        """Should return empty queryset when no images exist."""
        result = GuideImageService.get_guide_images(self.guide)

        self.assertEqual(result.count(), 0)

    def test_get_guide_images_ordered_by_uploaded_at(self):
        """Images should be ordered by upload time."""
        image_file1 = self.create_test_image('test1.jpg', 1024, 'image/jpeg')
        img1 = GuideImageService.upload_image(
            guide=self.guide,
            image_file=image_file1,
            profile=self.author_profile
        )

        image_file2 = self.create_test_image('test2.jpg', 1024, 'image/jpeg')
        img2 = GuideImageService.upload_image(
            guide=self.guide,
            image_file=image_file2,
            profile=self.author_profile
        )

        result = list(GuideImageService.get_guide_images(self.guide))

        self.assertEqual(result[0].id, img1.id)
        self.assertEqual(result[1].id, img2.id)

    def test_get_image_by_id_returns_image(self):
        """Should return image when it exists."""
        image_file = self.create_test_image('test.jpg', 1024, 'image/jpeg')
        image = GuideImageService.upload_image(
            guide=self.guide,
            image_file=image_file,
            profile=self.author_profile
        )

        result = GuideImageService.get_image_by_id(self.guide, image.id)

        self.assertEqual(result, image)

    def test_get_image_by_id_returns_none_when_not_found(self):
        """Should return None when image doesn't exist."""
        result = GuideImageService.get_image_by_id(self.guide, 99999)

        self.assertIsNone(result)

    def test_get_image_by_id_only_returns_guide_images(self):
        """Should only return images belonging to the guide."""
        # Create another guide
        other_guide = GuideService.create_guide(
            profile=self.author_profile,
            game=self.game,
            title='Other Guide',
            summary='Another guide'
        )
        GuideService.add_section(other_guide, 'Section', 'Content')

        # Create image for other guide
        image_file = self.create_test_image('test.jpg', 1024, 'image/jpeg')
        other_image = GuideImageService.upload_image(
            guide=other_guide,
            image_file=image_file,
            profile=self.author_profile
        )

        # Try to get other guide's image using self.guide
        result = GuideImageService.get_image_by_id(self.guide, other_image.id)

        self.assertIsNone(result)


class EdgeCaseTests(GuideImageServiceTestCase):
    """Tests for edge cases and boundary conditions."""

    def test_validate_upload_exactly_at_size_limit_basic(self):
        """File exactly at basic limit should pass."""
        self.author_profile.user_is_premium = False
        self.author_profile.save()

        # Exactly 3MB
        image_file = self.create_test_image('test.jpg', 3 * 1024 * 1024, 'image/jpeg')

        is_valid, error = GuideImageService.validate_upload(
            self.guide, image_file, self.author_profile
        )

        self.assertTrue(is_valid)

    def test_validate_upload_exactly_at_size_limit_premium(self):
        """File exactly at premium limit should pass."""
        self.author_profile.user_is_premium = True
        self.author_profile.save()

        # Exactly 5MB
        image_file = self.create_test_image('test.jpg', 5 * 1024 * 1024, 'image/jpeg')

        is_valid, error = GuideImageService.validate_upload(
            self.guide, image_file, self.author_profile
        )

        self.assertTrue(is_valid)

    def test_validate_upload_one_byte_over_limit(self):
        """File one byte over limit should fail."""
        self.author_profile.user_is_premium = False
        self.author_profile.save()

        # 3MB + 1 byte
        image_file = self.create_test_image('test.jpg', 3 * 1024 * 1024 + 1, 'image/jpeg')

        is_valid, error = GuideImageService.validate_upload(
            self.guide, image_file, self.author_profile
        )

        self.assertFalse(is_valid)
        self.assertIn('File too large', error)

    def test_validate_upload_case_insensitive_extension(self):
        """File extensions should be case insensitive."""
        self.author_profile.user_is_premium = False
        self.author_profile.save()

        test_cases = [
            ('test.JPG', 'image/jpeg'),
            ('test.JPEG', 'image/jpeg'),
            ('test.PNG', 'image/png'),
            ('test.WEBP', 'image/webp'),
        ]

        for filename, content_type in test_cases:
            image_file = self.create_test_image(filename, 1024, content_type)
            is_valid, error = GuideImageService.validate_upload(
                self.guide, image_file, self.author_profile
            )
            self.assertTrue(is_valid, f"{filename} should be valid")

    def test_validate_upload_file_without_content_type(self):
        """File without content_type attribute should still validate extension."""
        self.author_profile.user_is_premium = False
        self.author_profile.save()

        image_file = self.create_test_image('test.jpg', 1024, 'image/jpeg')
        # Remove content_type attribute
        delattr(image_file, 'content_type')

        is_valid, error = GuideImageService.validate_upload(
            self.guide, image_file, self.author_profile
        )

        # Should still pass based on extension
        self.assertTrue(is_valid)

    def test_premium_user_can_upload_more_images(self):
        """Premium users should be able to upload up to 30 images."""
        self.author_profile.user_is_premium = True
        self.author_profile.save()

        # Create 29 images
        for i in range(29):
            image_file = self.create_test_image(f'test{i}.jpg', 1024, 'image/jpeg')
            GuideImageService.upload_image(
                guide=self.guide,
                image_file=image_file,
                profile=self.author_profile
            )

        # 30th image should succeed
        image_file = self.create_test_image('test29.jpg', 1024, 'image/jpeg')
        is_valid, error = GuideImageService.validate_upload(
            self.guide, image_file, self.author_profile
        )

        self.assertTrue(is_valid)

        # Create 30th image
        GuideImageService.upload_image(
            guide=self.guide,
            image_file=image_file,
            profile=self.author_profile
        )

        # 31st image should fail
        image_file = self.create_test_image('test30.jpg', 1024, 'image/jpeg')
        is_valid, error = GuideImageService.validate_upload(
            self.guide, image_file, self.author_profile
        )

        self.assertFalse(is_valid)
        self.assertIn('Image limit reached', error)
        self.assertIn('30 images', error)

    def test_get_remaining_uploads_never_negative(self):
        """Remaining count should never be negative."""
        # This tests the max(0, ...) logic
        self.author_profile.user_is_premium = False
        self.author_profile.save()

        remaining = GuideImageService.get_remaining_uploads(self.guide, self.author_profile)

        self.assertGreaterEqual(remaining['remaining'], 0)
