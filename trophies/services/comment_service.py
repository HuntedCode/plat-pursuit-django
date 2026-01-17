"""
Comment system service layer.

Handles all business logic for comments, votes, and reports.
Follows the RatingService pattern for consistency.
"""
import logging
from django.db import transaction
from django.db.models import F
from django.core.cache import cache
from django.utils import timezone
from django.contrib.contenttypes.models import ContentType

logger = logging.getLogger('psn_api')


class CommentService:
    """Handles comment operations including CRUD, voting, and moderation."""

    COMMENTS_CACHE_TIMEOUT = 300  # 5 minutes
    MAX_COMMENT_LENGTH = 2000
    MAX_DEPTH = 10  # Limit nesting depth

    @staticmethod
    def can_comment(profile):
        """
        Check if profile has permission to comment.

        Args:
            profile: Profile instance

        Returns:
            tuple: (bool can_comment, str reason)
        """
        if not profile:
            return False, "You must be logged in to comment."
        if not profile.is_linked:
            return False, "You must link a PSN profile to comment."
        return True, None

    @staticmethod
    def can_attach_image(profile):
        """
        Check if profile can attach images (premium only).

        Args:
            profile: Profile instance

        Returns:
            bool: True if premium user
        """
        return profile and profile.user_is_premium

    @staticmethod
    @transaction.atomic
    def create_comment(profile, content_object, body, parent=None, image=None):
        """
        Create a new comment.

        Args:
            profile: Profile instance (author)
            content_object: Game or Trophy instance
            body: Comment text
            parent: Optional parent Comment for replies
            image: Optional ImageFile

        Returns:
            tuple: (Comment instance, error_message or None)
        """
        from trophies.models import Comment

        # Validate permission
        can_comment, reason = CommentService.can_comment(profile)
        if not can_comment:
            return None, reason

        # Validate body length
        if len(body) > CommentService.MAX_COMMENT_LENGTH:
            return None, f"Comment must be under {CommentService.MAX_COMMENT_LENGTH} characters."

        if len(body.strip()) == 0:
            return None, "Comment cannot be empty."

        # Validate image permission
        if image and not CommentService.can_attach_image(profile):
            return None, "Only premium users can attach images."

        # Validate depth for replies
        if parent:
            if parent.depth >= CommentService.MAX_DEPTH:
                return None, "Maximum reply depth reached."
            # Ensure parent is for the same content object
            if parent.content_object != content_object:
                return None, "Invalid parent comment."

        try:
            ct = ContentType.objects.get_for_model(content_object)
            comment = Comment.objects.create(
                content_type=ct,
                object_id=content_object.id,
                profile=profile,
                parent=parent,
                body=body.strip(),
                image=image if image and CommentService.can_attach_image(profile) else None
            )

            # Update denormalized count
            CommentService._update_comment_count(content_object, delta=1)

            # Invalidate cache
            CommentService.invalidate_cache(content_object)

            logger.info(f"Comment {comment.id} created by {profile.psn_username}")
            return comment, None

        except Exception as e:
            logger.error(f"Error creating comment: {e}")
            return None, "An error occurred creating your comment."

    @staticmethod
    @transaction.atomic
    def edit_comment(comment, profile, new_body):
        """
        Edit an existing comment.

        Args:
            comment: Comment instance
            profile: Profile making the edit
            new_body: New comment text

        Returns:
            tuple: (success bool, error_message or None)
        """
        # Validate ownership
        if comment.profile != profile:
            return False, "You can only edit your own comments."

        if comment.is_deleted:
            return False, "Cannot edit a deleted comment."

        if len(new_body) > CommentService.MAX_COMMENT_LENGTH:
            return False, f"Comment must be under {CommentService.MAX_COMMENT_LENGTH} characters."

        if len(new_body.strip()) == 0:
            return False, "Comment cannot be empty."

        comment.body = new_body.strip()
        comment.is_edited = True
        comment.save(update_fields=['body', 'is_edited', 'updated_at'])

        CommentService.invalidate_cache(comment.content_object)

        logger.info(f"Comment {comment.id} edited by {profile.psn_username}")
        return True, None

    @staticmethod
    @transaction.atomic
    def delete_comment(comment, profile, is_admin=False):
        """
        Soft delete a comment.

        Args:
            comment: Comment instance
            profile: Profile requesting deletion
            is_admin: Whether this is an admin action

        Returns:
            tuple: (success bool, error_message or None)
        """
        # Validate ownership (unless admin)
        if not is_admin and comment.profile != profile:
            return False, "You can only delete your own comments."

        if comment.is_deleted:
            return False, "Comment is already deleted."

        comment.soft_delete()

        # Update count (don't decrement - preserves thread structure)
        # We keep the comment in the count but mark it deleted

        CommentService.invalidate_cache(comment.content_object)

        action = "admin deleted" if is_admin else "deleted"
        logger.info(f"Comment {comment.id} {action} by {profile.psn_username}")
        return True, None

    @staticmethod
    @transaction.atomic
    def toggle_vote(comment, profile):
        """
        Toggle upvote on a comment.

        Args:
            comment: Comment instance
            profile: Profile voting

        Returns:
            tuple: (voted bool, error_message or None)
            voted is True if now voted, False if vote removed
        """
        from trophies.models import CommentVote

        can_vote, reason = CommentService.can_comment(profile)
        if not can_vote:
            return None, reason

        if comment.is_deleted:
            return None, "Cannot vote on deleted comments."

        # Can't vote on own comment
        if comment.profile == profile:
            return None, "Cannot vote on your own comment."

        existing_vote = CommentVote.objects.filter(
            comment=comment,
            profile=profile
        ).first()

        if existing_vote:
            # Remove vote
            existing_vote.delete()
            comment.upvote_count = F('upvote_count') - 1
            comment.save(update_fields=['upvote_count'])
            comment.refresh_from_db(fields=['upvote_count'])
            return False, None
        else:
            # Add vote
            CommentVote.objects.create(comment=comment, profile=profile)
            comment.upvote_count = F('upvote_count') + 1
            comment.save(update_fields=['upvote_count'])
            comment.refresh_from_db(fields=['upvote_count'])
            return True, None

    @staticmethod
    def get_comments_for_object(obj, profile=None, sort='top'):
        """
        Get comments for a Game or Trophy.

        Args:
            obj: Game or Trophy instance
            profile: Optional viewing profile (for vote status)
            sort: 'top', 'new', or 'old'

        Returns:
            QuerySet: Comments for the object
        """
        from trophies.models import Comment
        return Comment.objects.get_threaded_comments(obj, profile, sort)

    @staticmethod
    def get_comment_context_for_profile(comment, viewing_profile):
        """
        Build context dict for displaying a comment with author info.

        Args:
            comment: Comment instance
            viewing_profile: Profile viewing the comment

        Returns:
            dict: Comment data with author indicators
        """
        author_profile = comment.profile
        content_obj = comment.content_object

        context = {
            'comment': comment,
            'author_username': author_profile.display_psn_username or author_profile.psn_username,
            'author_avatar': author_profile.avatar_url,
            'author_country_flag': author_profile.flag,
            'is_deleted': comment.is_deleted,
            'is_edited': comment.is_edited,
        }

        # Author indicators based on content type
        if hasattr(content_obj, 'np_communication_id'):  # Game
            # Check if author has played/completed this game
            from trophies.models import ProfileGame
            try:
                pg = ProfileGame.objects.get(profile=author_profile, game=content_obj)
                context['author_progress'] = pg.progress
                context['author_has_platinum'] = pg.has_plat
            except ProfileGame.DoesNotExist:
                context['author_progress'] = None
                context['author_has_platinum'] = False
        else:  # Trophy
            # Check if author has earned this trophy
            from trophies.models import EarnedTrophy
            try:
                et = EarnedTrophy.objects.get(profile=author_profile, trophy=content_obj)
                context['author_has_trophy'] = et.earned
                context['author_earned_date'] = et.earned_date_time
            except EarnedTrophy.DoesNotExist:
                context['author_has_trophy'] = False
                context['author_earned_date'] = None

        return context

    @staticmethod
    def _update_comment_count(content_object, delta=1):
        """Update denormalized comment count on Game/Trophy."""
        content_object.comment_count = F('comment_count') + delta
        content_object.save(update_fields=['comment_count'])
        content_object.refresh_from_db(fields=['comment_count'])

    @staticmethod
    def invalidate_cache(content_object):
        """Invalidate cached comments for a content object."""
        ct = ContentType.objects.get_for_model(content_object)
        cache_key = f"comments:{ct.model}:{content_object.id}"
        cache.delete(cache_key)

    @staticmethod
    def report_comment(comment, reporter, reason, details=''):
        """
        Submit a report for a comment.

        Args:
            comment: Comment instance
            reporter: Profile submitting report
            reason: Report reason code
            details: Additional details

        Returns:
            tuple: (CommentReport or None, error_message or None)
        """
        from trophies.models import CommentReport

        if comment.is_deleted:
            return None, "Cannot report deleted comments."

        # Check for existing report
        existing = CommentReport.objects.filter(
            comment=comment,
            reporter=reporter
        ).first()
        if existing:
            return None, "You have already reported this comment."

        report = CommentReport.objects.create(
            comment=comment,
            reporter=reporter,
            reason=reason,
            details=details[:500] if details else ''
        )

        logger.info(f"Comment {comment.id} reported by {reporter.psn_username}: {reason}")
        return report, None
