"""
Comprehensive test suite for GuideService.

Tests all functionality outlined in the implementation document including:
- Permission checks
- Guide CRUD operations
- Publishing and moderation workflow
- Draft changes
- Trust management and auto-promotion
- Query helpers
- Utility methods
"""
from django.test import TestCase
from django.core.exceptions import ValidationError, PermissionDenied
from django.utils import timezone
from django.contrib.auth import get_user_model

from trophies.models import (
    Profile, Game, Concept, Guide, GuideSection, GuideTag,
    AuthorTrust
)
from trophies.services.guide_service import GuideService
from trophies.constants import (
    SUMMARY_CHAR_LIMIT, BASIC_SECTION_CHAR_LIMIT,
    PREMIUM_SECTION_CHAR_LIMIT, BASIC_MAX_SECTIONS,
    PREMIUM_MAX_SECTIONS, TRUSTED_MIN_APPROVED_GUIDES,
    TRUSTED_MIN_TOTAL_STARS
)

User = get_user_model()


class GuideServiceTestCase(TestCase):
    """Base test case with common fixtures for all guide service tests."""

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
        self.moderator_user = User.objects.create_user(
            username='moderator',
            email='moderator@example.com',
            password='testpass123',
            is_staff=True
        )

        # Create profiles
        self.profile_linked = Profile.objects.create(
            psn_username='LinkedUser',
            is_linked=True,
            user=self.user1
        )
        self.profile_unlinked = Profile.objects.create(
            psn_username='UnlinkedUser',
            is_linked=False
        )
        self.profile_premium = Profile.objects.create(
            psn_username='PremiumUser',
            is_linked=True,
            user_is_premium=True,
            user=self.user2
        )
        self.moderator_profile = Profile.objects.create(
            psn_username='ModeratorUser',
            is_linked=True,
            user=self.moderator_user
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
        self.game2 = Game.objects.create(
            title_name='Test Game 2',
            np_communication_id='NPWR54321_00',
            concept=self.concept
        )

        # Create tags
        self.tag1 = GuideTag.objects.create(
            name='Roadmap',
            slug='roadmap',
            display_order=1
        )
        self.tag2 = GuideTag.objects.create(
            name='Collectibles',
            slug='collectibles',
            display_order=2
        )


class PermissionCheckTests(GuideServiceTestCase):
    """Tests for can_create_guide permission checks."""

    def test_can_create_guide_linked_profile(self):
        """Linked profiles without ban should be allowed to create guides."""
        can_create, reason = GuideService.can_create_guide(self.profile_linked)
        self.assertTrue(can_create)
        self.assertEqual(reason, "")

    def test_can_create_guide_unlinked_profile(self):
        """Unlinked profiles should not be allowed to create guides."""
        can_create, reason = GuideService.can_create_guide(self.profile_unlinked)
        self.assertFalse(can_create)
        self.assertEqual(reason, "You must link your PSN account to create guides")

    def test_can_create_guide_banned_author(self):
        """Banned authors should not be allowed to create guides."""
        AuthorTrust.objects.create(
            profile=self.profile_linked,
            trust_level='banned',
            ban_reason='Spam'
        )
        can_create, reason = GuideService.can_create_guide(self.profile_linked)
        self.assertFalse(can_create)
        self.assertEqual(reason, "You are banned from creating guides")

    def test_can_create_guide_no_author_trust(self):
        """Profiles without AuthorTrust record should be allowed."""
        can_create, reason = GuideService.can_create_guide(self.profile_linked)
        self.assertTrue(can_create)
        self.assertEqual(reason, "")


class GuideCRUDTests(GuideServiceTestCase):
    """Tests for Guide CRUD operations."""

    def test_create_guide_success(self):
        """Successfully create a draft guide."""
        guide = GuideService.create_guide(
            profile=self.profile_linked,
            game=self.game,
            title='Test Guide',
            summary='A test guide summary',
            tags=[self.tag1, self.tag2]
        )

        self.assertIsNotNone(guide.id)
        self.assertEqual(guide.title, 'Test Guide')
        self.assertEqual(guide.summary, 'A test guide summary')
        self.assertEqual(guide.author, self.profile_linked)
        self.assertEqual(guide.game, self.game)
        self.assertEqual(guide.concept, self.concept)
        self.assertEqual(guide.status, 'draft')
        self.assertEqual(list(guide.tags.all()), [self.tag1, self.tag2])

    def test_create_guide_creates_author_trust(self):
        """Creating a guide should create AuthorTrust if it doesn't exist."""
        self.assertFalse(
            AuthorTrust.objects.filter(profile=self.profile_linked).exists()
        )
        GuideService.create_guide(
            profile=self.profile_linked,
            game=self.game,
            title='Test Guide',
            summary='A test guide summary'
        )
        self.assertTrue(
            AuthorTrust.objects.filter(profile=self.profile_linked).exists()
        )

    def test_create_guide_unlinked_raises_permission_denied(self):
        """Creating guide with unlinked profile should raise PermissionDenied."""
        with self.assertRaises(PermissionDenied) as context:
            GuideService.create_guide(
                profile=self.profile_unlinked,
                game=self.game,
                title='Test Guide',
                summary='A test guide summary'
            )
        self.assertIn('link your PSN account', str(context.exception))

    def test_create_guide_summary_too_long(self):
        """Creating guide with summary over limit should raise ValidationError."""
        long_summary = 'x' * (SUMMARY_CHAR_LIMIT + 1)
        with self.assertRaises(ValidationError) as context:
            GuideService.create_guide(
                profile=self.profile_linked,
                game=self.game,
                title='Test Guide',
                summary=long_summary
            )
        self.assertIn('500 characters or less', str(context.exception))

    def test_add_section_to_draft(self):
        """Successfully add section to draft guide."""
        guide = GuideService.create_guide(
            profile=self.profile_linked,
            game=self.game,
            title='Test Guide',
            summary='Summary'
        )
        section = GuideService.add_section(
            guide=guide,
            title='Introduction',
            content='# Welcome\n\nThis is the intro.'
        )

        self.assertIsNotNone(section.id)
        self.assertEqual(section.title, 'Introduction')
        self.assertEqual(section.content, '# Welcome\n\nThis is the intro.')
        self.assertEqual(section.section_order, 0)
        self.assertEqual(section.guide, guide)

    def test_add_section_basic_char_limit(self):
        """Basic tier users should be limited to BASIC_SECTION_CHAR_LIMIT."""
        guide = GuideService.create_guide(
            profile=self.profile_linked,
            game=self.game,
            title='Test Guide',
            summary='Summary'
        )

        # Should succeed at exactly the limit
        content_at_limit = 'x' * BASIC_SECTION_CHAR_LIMIT
        section = GuideService.add_section(
            guide=guide,
            title='Section 1',
            content=content_at_limit
        )
        self.assertIsNotNone(section.id)

        # Should fail over the limit
        content_over_limit = 'x' * (BASIC_SECTION_CHAR_LIMIT + 1)
        with self.assertRaises(ValidationError) as context:
            GuideService.add_section(
                guide=guide,
                title='Section 2',
                content=content_over_limit
            )
        self.assertIn('8000 characters or less', str(context.exception))

    def test_add_section_premium_char_limit(self):
        """Premium users should be limited to PREMIUM_SECTION_CHAR_LIMIT."""
        guide = GuideService.create_guide(
            profile=self.profile_premium,
            game=self.game,
            title='Test Guide',
            summary='Summary'
        )

        # Should succeed at exactly the limit
        content_at_limit = 'x' * PREMIUM_SECTION_CHAR_LIMIT
        section = GuideService.add_section(
            guide=guide,
            title='Section 1',
            content=content_at_limit
        )
        self.assertIsNotNone(section.id)

        # Should fail over the limit
        content_over_limit = 'x' * (PREMIUM_SECTION_CHAR_LIMIT + 1)
        with self.assertRaises(ValidationError) as context:
            GuideService.add_section(
                guide=guide,
                title='Section 2',
                content=content_over_limit
            )
        self.assertIn('12000 characters or less', str(context.exception))

    def test_add_section_basic_max_sections(self):
        """Basic tier users should be limited to BASIC_MAX_SECTIONS."""
        guide = GuideService.create_guide(
            profile=self.profile_linked,
            game=self.game,
            title='Test Guide',
            summary='Summary'
        )

        # Add max sections
        for i in range(BASIC_MAX_SECTIONS):
            GuideService.add_section(
                guide=guide,
                title=f'Section {i+1}',
                content='Content'
            )

        # Adding one more should fail
        with self.assertRaises(ValidationError) as context:
            GuideService.add_section(
                guide=guide,
                title='Extra Section',
                content='Content'
            )
        self.assertIn('Maximum of 20 sections', str(context.exception))

    def test_add_section_premium_max_sections(self):
        """Premium users should be limited to PREMIUM_MAX_SECTIONS."""
        guide = GuideService.create_guide(
            profile=self.profile_premium,
            game=self.game,
            title='Test Guide',
            summary='Summary'
        )

        # Add max sections
        for i in range(PREMIUM_MAX_SECTIONS):
            GuideService.add_section(
                guide=guide,
                title=f'Section {i+1}',
                content='Content'
            )

        # Adding one more should fail
        with self.assertRaises(ValidationError) as context:
            GuideService.add_section(
                guide=guide,
                title='Extra Section',
                content='Content'
            )
        self.assertIn('Maximum of 30 sections', str(context.exception))

    def test_add_section_explicit_order(self):
        """Sections should be added with explicit section_order if provided."""
        guide = GuideService.create_guide(
            profile=self.profile_linked,
            game=self.game,
            title='Test Guide',
            summary='Summary'
        )
        section = GuideService.add_section(
            guide=guide,
            title='Section',
            content='Content',
            section_order=5
        )
        self.assertEqual(section.section_order, 5)

    def test_update_section_content(self):
        """Successfully update section content."""
        guide = GuideService.create_guide(
            profile=self.profile_linked,
            game=self.game,
            title='Test Guide',
            summary='Summary'
        )
        section = GuideService.add_section(
            guide=guide,
            title='Section',
            content='Original content'
        )

        updated_section = GuideService.update_section(
            section=section,
            content='Updated content'
        )
        self.assertEqual(updated_section.content, 'Updated content')

    def test_update_section_title(self):
        """Successfully update section title."""
        guide = GuideService.create_guide(
            profile=self.profile_linked,
            game=self.game,
            title='Test Guide',
            summary='Summary'
        )
        section = GuideService.add_section(
            guide=guide,
            title='Original Title',
            content='Content'
        )

        updated_section = GuideService.update_section(
            section=section,
            title='New Title'
        )
        self.assertEqual(updated_section.title, 'New Title')

    def test_update_section_validates_char_limit(self):
        """Updating section should validate character limits."""
        guide = GuideService.create_guide(
            profile=self.profile_linked,
            game=self.game,
            title='Test Guide',
            summary='Summary'
        )
        section = GuideService.add_section(
            guide=guide,
            title='Section',
            content='Original'
        )

        content_over_limit = 'x' * (BASIC_SECTION_CHAR_LIMIT + 1)
        with self.assertRaises(ValidationError):
            GuideService.update_section(
                section=section,
                content=content_over_limit
            )

    def test_reorder_sections(self):
        """Successfully reorder sections."""
        guide = GuideService.create_guide(
            profile=self.profile_linked,
            game=self.game,
            title='Test Guide',
            summary='Summary'
        )
        section1 = GuideService.add_section(guide, 'Section 1', 'Content 1')
        section2 = GuideService.add_section(guide, 'Section 2', 'Content 2')
        section3 = GuideService.add_section(guide, 'Section 3', 'Content 3')

        # Reorder: 3, 1, 2
        GuideService.reorder_sections(
            guide=guide,
            section_ids=[section3.id, section1.id, section2.id]
        )

        section1.refresh_from_db()
        section2.refresh_from_db()
        section3.refresh_from_db()

        self.assertEqual(section3.section_order, 0)
        self.assertEqual(section1.section_order, 1)
        self.assertEqual(section2.section_order, 2)

    def test_reorder_sections_invalid_ids(self):
        """Reordering with wrong section IDs should raise ValidationError."""
        guide = GuideService.create_guide(
            profile=self.profile_linked,
            game=self.game,
            title='Test Guide',
            summary='Summary'
        )
        section1 = GuideService.add_section(guide, 'Section 1', 'Content 1')

        with self.assertRaises(ValidationError) as context:
            GuideService.reorder_sections(
                guide=guide,
                section_ids=[section1.id, 999999]  # Invalid ID
            )
        self.assertIn('must match all guide sections', str(context.exception))

    def test_delete_section_renumbers(self):
        """Deleting a section should renumber remaining sections."""
        guide = GuideService.create_guide(
            profile=self.profile_linked,
            game=self.game,
            title='Test Guide',
            summary='Summary'
        )
        section1 = GuideService.add_section(guide, 'Section 1', 'Content 1')
        section2 = GuideService.add_section(guide, 'Section 2', 'Content 2')
        section3 = GuideService.add_section(guide, 'Section 3', 'Content 3')

        # Delete middle section
        GuideService.delete_section(section2)

        section1.refresh_from_db()
        section3.refresh_from_db()

        self.assertEqual(section1.section_order, 0)
        self.assertEqual(section3.section_order, 1)
        self.assertFalse(GuideSection.objects.filter(id=section2.id).exists())


class PublishingAndModerationTests(GuideServiceTestCase):
    """Tests for publishing and moderation workflow."""

    def test_submit_for_review_new_author_pending(self):
        """New authors should have guides set to pending."""
        guide = GuideService.create_guide(
            profile=self.profile_linked,
            game=self.game,
            title='Test Guide',
            summary='Summary'
        )
        GuideService.add_section(guide, 'Section', 'Content')

        status, message = GuideService.submit_for_review(guide, self.profile_linked)

        guide.refresh_from_db()
        self.assertEqual(status, 'pending')
        self.assertEqual(guide.status, 'pending')
        self.assertIn('submitted for review', message)

    def test_submit_for_review_trusted_author_auto_publish(self):
        """Trusted authors should have guides auto-published."""
        AuthorTrust.objects.create(
            profile=self.profile_linked,
            trust_level='trusted'
        )
        guide = GuideService.create_guide(
            profile=self.profile_linked,
            game=self.game,
            title='Test Guide',
            summary='Summary'
        )
        GuideService.add_section(guide, 'Section', 'Content')

        status, message = GuideService.submit_for_review(guide, self.profile_linked)

        guide.refresh_from_db()
        self.assertEqual(status, 'published')
        self.assertEqual(guide.status, 'published')
        self.assertIsNotNone(guide.published_at)
        self.assertIn('has been published', message)

    def test_submit_for_review_requires_section(self):
        """Submitting guide without sections should raise ValidationError."""
        guide = GuideService.create_guide(
            profile=self.profile_linked,
            game=self.game,
            title='Test Guide',
            summary='Summary'
        )

        with self.assertRaises(ValidationError) as context:
            GuideService.submit_for_review(guide, self.profile_linked)
        self.assertIn('at least one section', str(context.exception))

    def test_submit_for_review_wrong_author(self):
        """Non-authors cannot submit guide for review."""
        guide = GuideService.create_guide(
            profile=self.profile_linked,
            game=self.game,
            title='Test Guide',
            summary='Summary'
        )
        GuideService.add_section(guide, 'Section', 'Content')

        with self.assertRaises(PermissionDenied) as context:
            GuideService.submit_for_review(guide, self.profile_premium)
        self.assertIn('Only the author', str(context.exception))

    def test_submit_for_review_from_rejected(self):
        """Rejected guides should be allowed to be resubmitted."""
        guide = GuideService.create_guide(
            profile=self.profile_linked,
            game=self.game,
            title='Test Guide',
            summary='Summary'
        )
        GuideService.add_section(guide, 'Section', 'Content')
        guide.status = 'rejected'
        guide.save()

        status, message = GuideService.submit_for_review(guide, self.profile_linked)
        self.assertEqual(status, 'pending')

    def test_approve_guide(self):
        """Moderator should be able to approve pending guide."""
        guide = GuideService.create_guide(
            profile=self.profile_linked,
            game=self.game,
            title='Test Guide',
            summary='Summary'
        )
        GuideService.add_section(guide, 'Section', 'Content')
        guide.status = 'pending'
        guide.save()

        approved_guide = GuideService.approve_guide(
            guide=guide,
            moderator=self.moderator_profile
        )

        self.assertEqual(approved_guide.status, 'published')
        self.assertEqual(approved_guide.moderated_by, self.moderator_profile)
        self.assertIsNotNone(approved_guide.moderated_at)
        self.assertIsNotNone(approved_guide.published_at)

    def test_approve_guide_increments_approved_count(self):
        """Approving guide should increment author's approved_guide_count."""
        author_trust = AuthorTrust.objects.create(
            profile=self.profile_linked,
            approved_guide_count=0
        )
        guide = GuideService.create_guide(
            profile=self.profile_linked,
            game=self.game,
            title='Test Guide',
            summary='Summary'
        )
        GuideService.add_section(guide, 'Section', 'Content')
        guide.status = 'pending'
        guide.save()

        GuideService.approve_guide(guide, self.moderator_profile)

        author_trust.refresh_from_db()
        self.assertEqual(author_trust.approved_guide_count, 1)

    def test_approve_guide_only_pending(self):
        """Only pending guides can be approved."""
        guide = GuideService.create_guide(
            profile=self.profile_linked,
            game=self.game,
            title='Test Guide',
            summary='Summary'
        )

        with self.assertRaises(ValidationError) as context:
            GuideService.approve_guide(guide, self.moderator_profile)
        self.assertIn('pending guides', str(context.exception))

    def test_reject_guide(self):
        """Moderator should be able to reject pending guide."""
        guide = GuideService.create_guide(
            profile=self.profile_linked,
            game=self.game,
            title='Test Guide',
            summary='Summary'
        )
        GuideService.add_section(guide, 'Section', 'Content')
        guide.status = 'pending'
        guide.save()

        rejected_guide = GuideService.reject_guide(
            guide=guide,
            moderator=self.moderator_profile,
            reason='Spam content'
        )

        self.assertEqual(rejected_guide.status, 'rejected')
        self.assertEqual(rejected_guide.rejection_reason, 'Spam content')
        self.assertEqual(rejected_guide.moderated_by, self.moderator_profile)
        self.assertIsNotNone(rejected_guide.moderated_at)

    def test_unlist_guide(self):
        """Published guides should be able to be unlisted."""
        guide = GuideService.create_guide(
            profile=self.profile_linked,
            game=self.game,
            title='Test Guide',
            summary='Summary'
        )
        guide.status = 'published'
        guide.save()

        unlisted_guide = GuideService.unlist_guide(guide)
        self.assertEqual(unlisted_guide.status, 'unlisted')

    def test_unlist_guide_only_published(self):
        """Only published guides can be unlisted."""
        guide = GuideService.create_guide(
            profile=self.profile_linked,
            game=self.game,
            title='Test Guide',
            summary='Summary'
        )

        with self.assertRaises(ValidationError) as context:
            GuideService.unlist_guide(guide)
        self.assertIn('published guides', str(context.exception))

    def test_republish_guide(self):
        """Unlisted guides should be able to be republished."""
        guide = GuideService.create_guide(
            profile=self.profile_linked,
            game=self.game,
            title='Test Guide',
            summary='Summary'
        )
        guide.status = 'unlisted'
        guide.save()

        republished_guide = GuideService.republish_guide(guide)
        self.assertEqual(republished_guide.status, 'published')

    def test_republish_guide_only_unlisted(self):
        """Only unlisted guides can be republished."""
        guide = GuideService.create_guide(
            profile=self.profile_linked,
            game=self.game,
            title='Test Guide',
            summary='Summary'
        )

        with self.assertRaises(ValidationError) as context:
            GuideService.republish_guide(guide)
        self.assertIn('unlisted guides', str(context.exception))


class DraftChangesTests(GuideServiceTestCase):
    """Tests for draft changes functionality."""

    def test_save_section_draft(self):
        """Successfully save draft changes to a section."""
        guide = GuideService.create_guide(
            profile=self.profile_linked,
            game=self.game,
            title='Test Guide',
            summary='Summary'
        )
        section = GuideService.add_section(
            guide, 'Section', 'Original content'
        )

        updated_section = GuideService.save_section_draft(
            section=section,
            draft_content='Draft content'
        )

        self.assertEqual(updated_section.draft_content, 'Draft content')
        self.assertTrue(updated_section.has_pending_edits)
        self.assertEqual(updated_section.content, 'Original content')

    def test_save_section_draft_validates_char_limit(self):
        """Saving draft should validate character limits."""
        guide = GuideService.create_guide(
            profile=self.profile_linked,
            game=self.game,
            title='Test Guide',
            summary='Summary'
        )
        section = GuideService.add_section(guide, 'Section', 'Content')

        content_over_limit = 'x' * (BASIC_SECTION_CHAR_LIMIT + 1)
        with self.assertRaises(ValidationError):
            GuideService.save_section_draft(section, content_over_limit)

    def test_publish_section_drafts(self):
        """Successfully publish all pending section drafts."""
        guide = GuideService.create_guide(
            profile=self.profile_linked,
            game=self.game,
            title='Test Guide',
            summary='Summary'
        )
        section1 = GuideService.add_section(guide, 'Section 1', 'Content 1')
        section2 = GuideService.add_section(guide, 'Section 2', 'Content 2')

        GuideService.save_section_draft(section1, 'Draft 1')
        GuideService.save_section_draft(section2, 'Draft 2')

        GuideService.publish_section_drafts(guide)

        section1.refresh_from_db()
        section2.refresh_from_db()

        self.assertEqual(section1.content, 'Draft 1')
        self.assertEqual(section1.draft_content, '')
        self.assertFalse(section1.has_pending_edits)

        self.assertEqual(section2.content, 'Draft 2')
        self.assertEqual(section2.draft_content, '')
        self.assertFalse(section2.has_pending_edits)

    def test_discard_section_drafts(self):
        """Successfully discard all pending section drafts."""
        guide = GuideService.create_guide(
            profile=self.profile_linked,
            game=self.game,
            title='Test Guide',
            summary='Summary'
        )
        section1 = GuideService.add_section(guide, 'Section 1', 'Content 1')
        section2 = GuideService.add_section(guide, 'Section 2', 'Content 2')

        GuideService.save_section_draft(section1, 'Draft 1')
        GuideService.save_section_draft(section2, 'Draft 2')

        GuideService.discard_section_drafts(guide)

        section1.refresh_from_db()
        section2.refresh_from_db()

        self.assertEqual(section1.content, 'Content 1')
        self.assertEqual(section1.draft_content, '')
        self.assertFalse(section1.has_pending_edits)

        self.assertEqual(section2.content, 'Content 2')
        self.assertEqual(section2.draft_content, '')
        self.assertFalse(section2.has_pending_edits)


class TrustManagementTests(GuideServiceTestCase):
    """Tests for trust management and auto-promotion."""

    def test_get_or_create_author_trust_creates(self):
        """get_or_create_author_trust should create if doesn't exist."""
        self.assertFalse(
            AuthorTrust.objects.filter(profile=self.profile_linked).exists()
        )

        author_trust = GuideService.get_or_create_author_trust(self.profile_linked)

        self.assertIsNotNone(author_trust.id)
        self.assertEqual(author_trust.profile, self.profile_linked)
        self.assertEqual(author_trust.trust_level, 'new')

    def test_get_or_create_author_trust_gets_existing(self):
        """get_or_create_author_trust should return existing record."""
        existing = AuthorTrust.objects.create(
            profile=self.profile_linked,
            trust_level='trusted'
        )

        author_trust = GuideService.get_or_create_author_trust(self.profile_linked)

        self.assertEqual(author_trust.id, existing.id)
        self.assertEqual(author_trust.trust_level, 'trusted')

    def test_check_auto_promotion_requires_both_conditions(self):
        """Auto-promotion requires BOTH approved guides AND total stars."""
        author_trust = AuthorTrust.objects.create(
            profile=self.profile_linked,
            trust_level='new',
            approved_guide_count=TRUSTED_MIN_APPROVED_GUIDES,
            total_stars_received=TRUSTED_MIN_TOTAL_STARS - 1
        )
        self.assertFalse(GuideService.check_auto_promotion(author_trust))

        author_trust.approved_guide_count = TRUSTED_MIN_APPROVED_GUIDES - 1
        author_trust.total_stars_received = TRUSTED_MIN_TOTAL_STARS
        self.assertFalse(GuideService.check_auto_promotion(author_trust))

    def test_check_auto_promotion_true_when_qualified(self):
        """Auto-promotion check returns True when both conditions met."""
        author_trust = AuthorTrust.objects.create(
            profile=self.profile_linked,
            trust_level='new',
            approved_guide_count=TRUSTED_MIN_APPROVED_GUIDES,
            total_stars_received=TRUSTED_MIN_TOTAL_STARS
        )
        self.assertTrue(GuideService.check_auto_promotion(author_trust))

    def test_check_auto_promotion_false_for_trusted(self):
        """Auto-promotion check returns False if already trusted."""
        author_trust = AuthorTrust.objects.create(
            profile=self.profile_linked,
            trust_level='trusted',
            approved_guide_count=TRUSTED_MIN_APPROVED_GUIDES,
            total_stars_received=TRUSTED_MIN_TOTAL_STARS
        )
        self.assertFalse(GuideService.check_auto_promotion(author_trust))

    def test_promote_to_trusted(self):
        """Successfully promote author to trusted status."""
        author_trust = AuthorTrust.objects.create(
            profile=self.profile_linked,
            trust_level='new'
        )

        promoted = GuideService.promote_to_trusted(author_trust)

        self.assertEqual(promoted.trust_level, 'trusted')
        self.assertIsNotNone(promoted.promoted_at)

    def test_check_and_promote_promotes_when_qualified(self):
        """_check_and_promote promotes when qualified and returns True."""
        author_trust = AuthorTrust.objects.create(
            profile=self.profile_linked,
            trust_level='new',
            approved_guide_count=TRUSTED_MIN_APPROVED_GUIDES,
            total_stars_received=TRUSTED_MIN_TOTAL_STARS
        )

        result = GuideService._check_and_promote(author_trust)

        self.assertTrue(result)
        author_trust.refresh_from_db()
        self.assertEqual(author_trust.trust_level, 'trusted')

    def test_check_and_promote_does_not_promote_when_not_qualified(self):
        """_check_and_promote doesn't promote when not qualified."""
        author_trust = AuthorTrust.objects.create(
            profile=self.profile_linked,
            trust_level='new',
            approved_guide_count=1,
            total_stars_received=10
        )

        result = GuideService._check_and_promote(author_trust)

        self.assertFalse(result)
        author_trust.refresh_from_db()
        self.assertEqual(author_trust.trust_level, 'new')

    def test_approve_guide_promotes_to_trusted_when_thresholds_met(self):
        """Approving guide should auto-promote when thresholds are met."""
        author_trust = AuthorTrust.objects.create(
            profile=self.profile_linked,
            trust_level='new',
            approved_guide_count=TRUSTED_MIN_APPROVED_GUIDES - 1,
            total_stars_received=TRUSTED_MIN_TOTAL_STARS
        )

        guide = GuideService.create_guide(
            profile=self.profile_linked,
            game=self.game,
            title='Test Guide',
            summary='Summary'
        )
        GuideService.add_section(guide, 'Section', 'Content')
        guide.status = 'pending'
        guide.save()

        GuideService.approve_guide(guide, self.moderator_profile)

        author_trust.refresh_from_db()
        self.assertEqual(author_trust.trust_level, 'trusted')
        self.assertIsNotNone(author_trust.promoted_at)

    def test_ban_author(self):
        """Successfully ban an author."""
        author_trust = AuthorTrust.objects.create(
            profile=self.profile_linked,
            trust_level='new'
        )

        banned = GuideService.ban_author(
            author_trust=author_trust,
            reason='Repeated spam violations'
        )

        self.assertEqual(banned.trust_level, 'banned')
        self.assertEqual(banned.ban_reason, 'Repeated spam violations')
        self.assertIsNotNone(banned.banned_at)


class QueryHelperTests(GuideServiceTestCase):
    """Tests for query helper methods."""

    def setUp(self):
        super().setUp()
        # Create multiple guides for testing queries
        self.guide1 = GuideService.create_guide(
            profile=self.profile_linked,
            game=self.game,
            title='Guide 1',
            summary='Summary 1'
        )
        self.guide1.status = 'published'
        self.guide1.average_rating = 4.5
        self.guide1.published_at = timezone.now()
        self.guide1.save()

        self.guide2 = GuideService.create_guide(
            profile=self.profile_premium,
            game=self.game,
            title='Guide 2',
            summary='Summary 2'
        )
        self.guide2.status = 'published'
        self.guide2.average_rating = 4.8
        self.guide2.published_at = timezone.now()
        self.guide2.save()

        self.guide3 = GuideService.create_guide(
            profile=self.profile_linked,
            game=self.game2,
            title='Guide 3',
            summary='Summary 3'
        )
        self.guide3.status = 'draft'
        self.guide3.save()

        self.guide4 = GuideService.create_guide(
            profile=self.profile_premium,
            game=self.game2,
            title='Guide 4',
            summary='Summary 4'
        )
        self.guide4.status = 'pending'
        self.guide4.save()

    def test_get_published_guides_for_game(self):
        """Should return only published guides for a specific game."""
        guides = GuideService.get_published_guides_for_game(self.game)
        guide_ids = [g.id for g in guides]

        self.assertIn(self.guide1.id, guide_ids)
        self.assertIn(self.guide2.id, guide_ids)
        self.assertNotIn(self.guide3.id, guide_ids)
        self.assertNotIn(self.guide4.id, guide_ids)

    def test_get_published_guides_for_game_ordered_by_rating(self):
        """Should order guides by average_rating descending."""
        guides = list(GuideService.get_published_guides_for_game(self.game))
        self.assertEqual(guides[0].id, self.guide2.id)  # 4.8 rating
        self.assertEqual(guides[1].id, self.guide1.id)  # 4.5 rating

    def test_get_published_guides_for_game_with_limit(self):
        """Should respect limit parameter."""
        guides = GuideService.get_published_guides_for_game(self.game, limit=1)
        self.assertEqual(len(guides), 1)

    def test_get_published_guides_for_concept(self):
        """Should return published guides for a concept."""
        guides = GuideService.get_published_guides_for_concept(self.concept)
        guide_ids = [g.id for g in guides]

        self.assertIn(self.guide1.id, guide_ids)
        self.assertIn(self.guide2.id, guide_ids)
        self.assertNotIn(self.guide3.id, guide_ids)
        self.assertNotIn(self.guide4.id, guide_ids)

    def test_get_published_guides_for_concept_with_limit(self):
        """Should respect limit parameter for concept queries."""
        guides = GuideService.get_published_guides_for_concept(
            self.concept, limit=1
        )
        self.assertEqual(len(guides), 1)

    def test_get_pending_guides(self):
        """Should return only pending guides."""
        guides = GuideService.get_pending_guides()
        guide_ids = [g.id for g in guides]

        self.assertIn(self.guide4.id, guide_ids)
        self.assertNotIn(self.guide1.id, guide_ids)
        self.assertNotIn(self.guide2.id, guide_ids)
        self.assertNotIn(self.guide3.id, guide_ids)

    def test_get_user_guides(self):
        """Should return all guides by a specific author."""
        guides = GuideService.get_user_guides(self.profile_linked)
        guide_ids = [g.id for g in guides]

        self.assertIn(self.guide1.id, guide_ids)
        self.assertIn(self.guide3.id, guide_ids)
        self.assertNotIn(self.guide2.id, guide_ids)
        self.assertNotIn(self.guide4.id, guide_ids)


class UtilityMethodTests(GuideServiceTestCase):
    """Tests for utility methods."""

    def test_get_limits_for_profile_basic(self):
        """Basic tier users should get basic limits."""
        limits = GuideService.get_limits_for_profile(self.profile_linked)

        self.assertEqual(limits['max_sections'], BASIC_MAX_SECTIONS)
        self.assertEqual(
            limits['section_char_limit'], BASIC_SECTION_CHAR_LIMIT
        )
        self.assertEqual(limits['summary_char_limit'], SUMMARY_CHAR_LIMIT)

    def test_get_limits_for_profile_premium(self):
        """Premium users should get premium limits."""
        limits = GuideService.get_limits_for_profile(self.profile_premium)

        self.assertEqual(limits['max_sections'], PREMIUM_MAX_SECTIONS)
        self.assertEqual(
            limits['section_char_limit'], PREMIUM_SECTION_CHAR_LIMIT
        )
        self.assertEqual(limits['summary_char_limit'], SUMMARY_CHAR_LIMIT)

    def test_increment_view_count(self):
        """Should increment guide view count."""
        guide = GuideService.create_guide(
            profile=self.profile_linked,
            game=self.game,
            title='Test Guide',
            summary='Summary'
        )
        initial_count = guide.view_count

        GuideService.increment_view_count(guide)

        guide.refresh_from_db()
        self.assertEqual(guide.view_count, initial_count + 1)

    def test_increment_view_count_multiple_times(self):
        """Should increment view count multiple times."""
        guide = GuideService.create_guide(
            profile=self.profile_linked,
            game=self.game,
            title='Test Guide',
            summary='Summary'
        )
        initial_count = guide.view_count

        GuideService.increment_view_count(guide)
        GuideService.increment_view_count(guide)
        GuideService.increment_view_count(guide)

        guide.refresh_from_db()
        self.assertEqual(guide.view_count, initial_count + 3)
