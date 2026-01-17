"""
Tests for the comment system.

This test suite covers:
- Comment model and manager
- CommentVote model
- CommentReport model
- CommentService business logic
- Signals for denormalization
"""
from django.test import TestCase
from django.utils import timezone
from django.contrib.contenttypes.models import ContentType
from trophies.models import Profile, Game, Trophy, Comment, CommentVote, CommentReport
from trophies.services.comment_service import CommentService
from users.models import CustomUser


class CommentModelTest(TestCase):
    """Test Comment model functionality."""

    def setUp(self):
        """Set up test data."""
        # Create user and profile
        self.user = CustomUser.objects.create_user(
            email='test@example.com',
            password='testpass123'
        )
        self.profile = Profile.objects.create(
            user=self.user,
            psn_username='testuser',
            account_id='12345',
            is_linked=True
        )

        # Create a second user for voting tests
        self.user2 = CustomUser.objects.create_user(
            email='test2@example.com',
            password='testpass123',
            username='testuser2'
        )
        self.profile2 = Profile.objects.create(
            user=self.user2,
            psn_username='testuser2',
            account_id='67890',
            is_linked=True
        )

        # Create a game
        self.game = Game.objects.create(
            np_communication_id='NPWR12345_00',
            title_name='Test Game'
        )

        # Create a trophy
        self.trophy = Trophy.objects.create(
            trophy_id=1,
            trophy_name='Test Trophy',
            trophy_type='bronze',
            game=self.game
        )

    def test_create_comment_on_game(self):
        """Test creating a comment on a game."""
        comment = Comment.objects.create(
            content_type=ContentType.objects.get_for_model(self.game),
            object_id=self.game.id,
            profile=self.profile,
            body='This is a test comment'
        )

        self.assertEqual(comment.profile, self.profile)
        self.assertEqual(comment.content_object, self.game)
        self.assertEqual(comment.body, 'This is a test comment')
        self.assertEqual(comment.depth, 0)
        self.assertFalse(comment.is_deleted)
        self.assertFalse(comment.is_edited)
        self.assertEqual(comment.upvote_count, 0)

    def test_create_comment_on_trophy(self):
        """Test creating a comment on a trophy."""
        comment = Comment.objects.create(
            content_type=ContentType.objects.get_for_model(self.trophy),
            object_id=self.trophy.id,
            profile=self.profile,
            body='Trophy tip: do this first'
        )

        self.assertEqual(comment.content_object, self.trophy)
        self.assertEqual(comment.body, 'Trophy tip: do this first')

    def test_threaded_comments(self):
        """Test creating nested replies."""
        # Create parent comment
        parent = Comment.objects.create(
            content_type=ContentType.objects.get_for_model(self.game),
            object_id=self.game.id,
            profile=self.profile,
            body='Parent comment'
        )

        # Create reply
        reply = Comment.objects.create(
            content_type=ContentType.objects.get_for_model(self.game),
            object_id=self.game.id,
            profile=self.profile2,
            body='Reply to parent',
            parent=parent
        )

        self.assertEqual(reply.parent, parent)
        self.assertEqual(reply.depth, 1)
        self.assertIn(reply, parent.replies.all())

    def test_nested_reply_depth(self):
        """Test depth calculation for deeply nested replies."""
        # Create comment chain
        parent = Comment.objects.create(
            content_type=ContentType.objects.get_for_model(self.game),
            object_id=self.game.id,
            profile=self.profile,
            body='Level 0'
        )

        reply1 = Comment.objects.create(
            content_type=ContentType.objects.get_for_model(self.game),
            object_id=self.game.id,
            profile=self.profile2,
            body='Level 1',
            parent=parent
        )

        reply2 = Comment.objects.create(
            content_type=ContentType.objects.get_for_model(self.game),
            object_id=self.game.id,
            profile=self.profile,
            body='Level 2',
            parent=reply1
        )

        self.assertEqual(parent.depth, 0)
        self.assertEqual(reply1.depth, 1)
        self.assertEqual(reply2.depth, 2)

    def test_soft_delete(self):
        """Test soft delete functionality."""
        comment = Comment.objects.create(
            content_type=ContentType.objects.get_for_model(self.game),
            object_id=self.game.id,
            profile=self.profile,
            body='Comment to delete'
        )

        comment.soft_delete()

        self.assertTrue(comment.is_deleted)
        self.assertIsNotNone(comment.deleted_at)
        self.assertEqual(comment.body, '[deleted]')
        self.assertEqual(comment.display_body, '[deleted]')

    def test_display_body_property(self):
        """Test display_body property shows correct content."""
        comment = Comment.objects.create(
            content_type=ContentType.objects.get_for_model(self.game),
            object_id=self.game.id,
            profile=self.profile,
            body='Normal comment'
        )

        self.assertEqual(comment.display_body, 'Normal comment')

        comment.soft_delete()
        self.assertEqual(comment.display_body, '[deleted]')


class CommentManagerTest(TestCase):
    """Test Comment manager and queryset methods."""

    def setUp(self):
        """Set up test data."""
        self.user = CustomUser.objects.create_user(
            email='test@example.com',
            password='testpass123'
        )
        self.profile = Profile.objects.create(
            user=self.user,
            psn_username='testuser',
            account_id='12345',
            is_linked=True
        )

        self.game = Game.objects.create(
            np_communication_id='NPWR12345_00',
            title_name='Test Game'
        )

        # Create mix of active and deleted comments
        self.active_comment = Comment.objects.create(
            content_type=ContentType.objects.get_for_model(self.game),
            object_id=self.game.id,
            profile=self.profile,
            body='Active comment'
        )

        self.deleted_comment = Comment.objects.create(
            content_type=ContentType.objects.get_for_model(self.game),
            object_id=self.game.id,
            profile=self.profile,
            body='Deleted comment',
            is_deleted=True
        )

    def test_active_filter(self):
        """Test active() queryset method."""
        active_comments = Comment.objects.active()

        self.assertIn(self.active_comment, active_comments)
        self.assertNotIn(self.deleted_comment, active_comments)

    def test_for_content_object(self):
        """Test for_content_object() method."""
        comments = Comment.objects.for_content_object(self.game)

        self.assertEqual(comments.count(), 2)
        self.assertIn(self.active_comment, comments)
        self.assertIn(self.deleted_comment, comments)

    def test_top_level_filter(self):
        """Test top_level() method."""
        parent = Comment.objects.create(
            content_type=ContentType.objects.get_for_model(self.game),
            object_id=self.game.id,
            profile=self.profile,
            body='Parent'
        )

        reply = Comment.objects.create(
            content_type=ContentType.objects.get_for_model(self.game),
            object_id=self.game.id,
            profile=self.profile,
            body='Reply',
            parent=parent
        )

        top_level = Comment.objects.top_level()

        self.assertIn(parent, top_level)
        self.assertNotIn(reply, top_level)

    def test_by_top_sorting(self):
        """Test by_top() sorting by upvote count."""
        comment1 = Comment.objects.create(
            content_type=ContentType.objects.get_for_model(self.game),
            object_id=self.game.id,
            profile=self.profile,
            body='Low votes',
            upvote_count=1
        )

        comment2 = Comment.objects.create(
            content_type=ContentType.objects.get_for_model(self.game),
            object_id=self.game.id,
            profile=self.profile,
            body='High votes',
            upvote_count=10
        )

        comments = Comment.objects.by_top()

        self.assertEqual(list(comments)[0], comment2)

    def test_by_new_sorting(self):
        """Test by_new() sorting by creation date."""
        old_comment = Comment.objects.create(
            content_type=ContentType.objects.get_for_model(self.game),
            object_id=self.game.id,
            profile=self.profile,
            body='Old comment'
        )

        new_comment = Comment.objects.create(
            content_type=ContentType.objects.get_for_model(self.game),
            object_id=self.game.id,
            profile=self.profile,
            body='New comment'
        )

        comments = Comment.objects.by_new()

        # Newest should be first
        self.assertEqual(list(comments)[0], new_comment)


class CommentVoteTest(TestCase):
    """Test CommentVote model."""

    def setUp(self):
        """Set up test data."""
        self.user = CustomUser.objects.create_user(
            email='test@example.com',
            password='testpass123'
        )
        self.profile = Profile.objects.create(
            user=self.user,
            psn_username='testuser',
            account_id='12345',
            is_linked=True
        )

        self.game = Game.objects.create(
            np_communication_id='NPWR12345_00',
            title_name='Test Game'
        )

        self.comment = Comment.objects.create(
            content_type=ContentType.objects.get_for_model(self.game),
            object_id=self.game.id,
            profile=self.profile,
            body='Test comment'
        )

    def test_create_vote(self):
        """Test creating a vote."""
        vote = CommentVote.objects.create(
            comment=self.comment,
            profile=self.profile
        )

        self.assertEqual(vote.comment, self.comment)
        self.assertEqual(vote.profile, self.profile)
        self.assertIsNotNone(vote.created_at)

    def test_unique_vote_constraint(self):
        """Test that a user can only vote once per comment."""
        CommentVote.objects.create(
            comment=self.comment,
            profile=self.profile
        )

        # Attempting to vote again should raise error
        from django.db import IntegrityError
        with self.assertRaises(IntegrityError):
            CommentVote.objects.create(
                comment=self.comment,
                profile=self.profile
            )


class CommentReportTest(TestCase):
    """Test CommentReport model."""

    def setUp(self):
        """Set up test data."""
        self.user = CustomUser.objects.create_user(
            email='test@example.com',
            password='testpass123'
        )
        self.profile = Profile.objects.create(
            user=self.user,
            psn_username='testuser',
            account_id='12345',
            is_linked=True
        )

        self.game = Game.objects.create(
            np_communication_id='NPWR12345_00',
            title_name='Test Game'
        )

        self.comment = Comment.objects.create(
            content_type=ContentType.objects.get_for_model(self.game),
            object_id=self.game.id,
            profile=self.profile,
            body='Test comment'
        )

    def test_create_report(self):
        """Test creating a comment report."""
        report = CommentReport.objects.create(
            comment=self.comment,
            reporter=self.profile,
            reason='spam',
            details='This looks like spam'
        )

        self.assertEqual(report.comment, self.comment)
        self.assertEqual(report.reporter, self.profile)
        self.assertEqual(report.reason, 'spam')
        self.assertEqual(report.status, 'pending')

    def test_unique_report_constraint(self):
        """Test that a user can only report a comment once."""
        CommentReport.objects.create(
            comment=self.comment,
            reporter=self.profile,
            reason='spam'
        )

        from django.db import IntegrityError
        with self.assertRaises(IntegrityError):
            CommentReport.objects.create(
                comment=self.comment,
                reporter=self.profile,
                reason='harassment'
            )


class CommentServiceTest(TestCase):
    """Test CommentService business logic."""

    def setUp(self):
        """Set up test data."""
        self.user = CustomUser.objects.create_user(
            email='test@example.com',
            password='testpass123'
        )
        self.profile = Profile.objects.create(
            user=self.user,
            psn_username='testuser',
            account_id='12345',
            is_linked=True,
            user_is_premium=False
        )

        self.premium_user = CustomUser.objects.create_user(
            email='premium@example.com',
            password='testpass123',
            username='premiumuser'
        )
        self.premium_profile = Profile.objects.create(
            user=self.premium_user,
            psn_username='premiumuser',
            account_id='54321',
            is_linked=True,
            user_is_premium=True
        )

        self.unlinked_user = CustomUser.objects.create_user(
            email='unlinked@example.com',
            password='testpass123',
            username='unlinkeduser'
        )
        self.unlinked_profile = Profile.objects.create(
            user=self.unlinked_user,
            psn_username='unlinkeduser',
            account_id='99999',
            is_linked=False
        )

        self.game = Game.objects.create(
            np_communication_id='NPWR12345_00',
            title_name='Test Game'
        )

    def test_can_comment_linked_user(self):
        """Test that linked users can comment."""
        can_comment, reason = CommentService.can_comment(self.profile)

        self.assertTrue(can_comment)
        self.assertIsNone(reason)

    def test_cannot_comment_unlinked_user(self):
        """Test that unlinked users cannot comment."""
        can_comment, reason = CommentService.can_comment(self.unlinked_profile)

        self.assertFalse(can_comment)
        self.assertIn('link', reason.lower())

    def test_cannot_comment_no_profile(self):
        """Test that None profile cannot comment."""
        can_comment, reason = CommentService.can_comment(None)

        self.assertFalse(can_comment)
        self.assertIn('logged in', reason.lower())

    def test_can_attach_image_premium(self):
        """Test that premium users can attach images."""
        can_attach = CommentService.can_attach_image(self.premium_profile)

        self.assertTrue(can_attach)

    def test_cannot_attach_image_non_premium(self):
        """Test that non-premium users cannot attach images."""
        can_attach = CommentService.can_attach_image(self.profile)

        self.assertFalse(can_attach)

    def test_create_comment_success(self):
        """Test successful comment creation."""
        comment, error = CommentService.create_comment(
            profile=self.profile,
            content_object=self.game,
            body='Great game!'
        )

        self.assertIsNotNone(comment)
        self.assertIsNone(error)
        self.assertEqual(comment.body, 'Great game!')
        self.assertEqual(comment.profile, self.profile)

    def test_create_comment_too_long(self):
        """Test comment creation with body too long."""
        long_body = 'x' * 2001
        comment, error = CommentService.create_comment(
            profile=self.profile,
            content_object=self.game,
            body=long_body
        )

        self.assertIsNone(comment)
        self.assertIn('2000', error)

    def test_create_comment_empty(self):
        """Test comment creation with empty body."""
        comment, error = CommentService.create_comment(
            profile=self.profile,
            content_object=self.game,
            body='   '
        )

        self.assertIsNone(comment)
        self.assertIn('empty', error.lower())

    def test_create_comment_unlinked(self):
        """Test comment creation fails for unlinked user."""
        comment, error = CommentService.create_comment(
            profile=self.unlinked_profile,
            content_object=self.game,
            body='Test'
        )

        self.assertIsNone(comment)
        self.assertIsNotNone(error)

    def test_edit_comment_success(self):
        """Test successful comment edit."""
        comment, _ = CommentService.create_comment(
            profile=self.profile,
            content_object=self.game,
            body='Original text'
        )

        success, error = CommentService.edit_comment(
            comment=comment,
            profile=self.profile,
            new_body='Edited text'
        )

        self.assertTrue(success)
        self.assertIsNone(error)
        comment.refresh_from_db()
        self.assertEqual(comment.body, 'Edited text')
        self.assertTrue(comment.is_edited)

    def test_edit_comment_wrong_owner(self):
        """Test editing someone else's comment fails."""
        comment, _ = CommentService.create_comment(
            profile=self.profile,
            content_object=self.game,
            body='Original text'
        )

        success, error = CommentService.edit_comment(
            comment=comment,
            profile=self.premium_profile,  # Different user
            new_body='Hacked'
        )

        self.assertFalse(success)
        self.assertIn('own', error.lower())

    def test_delete_comment_success(self):
        """Test successful comment deletion."""
        comment, _ = CommentService.create_comment(
            profile=self.profile,
            content_object=self.game,
            body='Delete me'
        )

        success, error = CommentService.delete_comment(
            comment=comment,
            profile=self.profile
        )

        self.assertTrue(success)
        self.assertIsNone(error)
        comment.refresh_from_db()
        self.assertTrue(comment.is_deleted)

    def test_toggle_vote_add(self):
        """Test adding a vote."""
        comment, _ = CommentService.create_comment(
            profile=self.profile,
            content_object=self.game,
            body='Vote on me'
        )

        voted, error = CommentService.toggle_vote(
            comment=comment,
            profile=self.premium_profile  # Different user voting
        )

        self.assertTrue(voted)
        self.assertIsNone(error)
        comment.refresh_from_db()
        self.assertEqual(comment.upvote_count, 1)

    def test_toggle_vote_remove(self):
        """Test removing a vote."""
        comment, _ = CommentService.create_comment(
            profile=self.profile,
            content_object=self.game,
            body='Vote on me'
        )

        # Add vote
        CommentService.toggle_vote(comment, self.premium_profile)

        # Remove vote
        voted, error = CommentService.toggle_vote(comment, self.premium_profile)

        self.assertFalse(voted)
        self.assertIsNone(error)
        comment.refresh_from_db()
        self.assertEqual(comment.upvote_count, 0)

    def test_cannot_vote_own_comment(self):
        """Test that users cannot vote on their own comments."""
        comment, _ = CommentService.create_comment(
            profile=self.profile,
            content_object=self.game,
            body='My comment'
        )

        voted, error = CommentService.toggle_vote(comment, self.profile)

        self.assertIsNone(voted)
        self.assertIn('own', error.lower())

    def test_report_comment_success(self):
        """Test successful comment reporting."""
        comment, _ = CommentService.create_comment(
            profile=self.profile,
            content_object=self.game,
            body='Reportable content'
        )

        report, error = CommentService.report_comment(
            comment=comment,
            reporter=self.premium_profile,
            reason='spam',
            details='This is spam'
        )

        self.assertIsNotNone(report)
        self.assertIsNone(error)
        self.assertEqual(report.reason, 'spam')

    def test_cannot_report_twice(self):
        """Test that a user cannot report the same comment twice."""
        comment, _ = CommentService.create_comment(
            profile=self.profile,
            content_object=self.game,
            body='Reportable content'
        )

        CommentService.report_comment(comment, self.premium_profile, 'spam')

        report, error = CommentService.report_comment(
            comment, self.premium_profile, 'harassment'
        )

        self.assertIsNone(report)
        self.assertIn('already', error.lower())


class CommentSignalTest(TestCase):
    """Test comment-related signals."""

    def setUp(self):
        """Set up test data."""
        self.user = CustomUser.objects.create_user(
            email='test@example.com',
            password='testpass123'
        )
        self.profile = Profile.objects.create(
            user=self.user,
            psn_username='testuser',
            account_id='12345',
            is_linked=True
        )

        self.game = Game.objects.create(
            np_communication_id='NPWR12345_00',
            title_name='Test Game'
        )

        self.trophy = Trophy.objects.create(
            trophy_id=1,
            trophy_name='Test Trophy',
            trophy_type='bronze',
            game=self.game
        )

    def test_comment_count_incremented_on_game(self):
        """Test that comment_count is incremented when comment is created on game."""
        initial_count = self.game.comment_count

        Comment.objects.create(
            content_type=ContentType.objects.get_for_model(self.game),
            object_id=self.game.id,
            profile=self.profile,
            body='Test comment'
        )

        self.game.refresh_from_db()
        self.assertEqual(self.game.comment_count, initial_count + 1)

    def test_comment_count_incremented_on_trophy(self):
        """Test that comment_count is incremented when comment is created on trophy."""
        initial_count = self.trophy.comment_count

        Comment.objects.create(
            content_type=ContentType.objects.get_for_model(self.trophy),
            object_id=self.trophy.id,
            profile=self.profile,
            body='Trophy tip'
        )

        self.trophy.refresh_from_db()
        self.assertEqual(self.trophy.comment_count, initial_count + 1)

    def test_comment_count_not_incremented_for_deleted(self):
        """Test that deleted comments don't increment count via signal."""
        initial_count = self.game.comment_count

        Comment.objects.create(
            content_type=ContentType.objects.get_for_model(self.game),
            object_id=self.game.id,
            profile=self.profile,
            body='Deleted from start',
            is_deleted=True
        )

        self.game.refresh_from_db()
        # Signal should not increment for deleted comments
        self.assertEqual(self.game.comment_count, initial_count)
