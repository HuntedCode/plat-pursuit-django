"""
Comment system service layer.

Handles all business logic for comments, votes, and reports.
Follows the RatingService pattern for consistency.
"""
import logging
import bleach
from django.db import transaction
from django.db.models import F
from django.core.cache import cache
from django.utils import timezone

logger = logging.getLogger('psn_api')


class CommentService:
    """Handles comment operations including CRUD, voting, and moderation."""

    COMMENTS_CACHE_TIMEOUT = 300  # 5 minutes
    MAX_COMMENT_LENGTH = 2000
    MAX_DEPTH = 10  # Limit nesting depth

    # Bleach configuration - allow no HTML tags at all for comments
    ALLOWED_TAGS = []  # No HTML tags allowed
    ALLOWED_ATTRIBUTES = {}  # No attributes allowed
    STRIP_TAGS = True  # Strip all tags rather than escape them

    @staticmethod
    def sanitize_text(text):
        """
        Sanitize user-provided text to prevent XSS attacks.

        Removes all HTML tags and dangerous content while preserving plain text.

        Args:
            text: Raw user input text

        Returns:
            str: Sanitized text safe for display
        """
        if not text:
            return ""

        # Strip all HTML tags and attributes
        clean_text = bleach.clean(
            text,
            tags=CommentService.ALLOWED_TAGS,
            attributes=CommentService.ALLOWED_ATTRIBUTES,
            strip=CommentService.STRIP_TAGS
        )

        return clean_text.strip()

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
    @transaction.atomic
    def create_comment(profile, concept, body, parent=None, trophy_id=None):
        """
        Create a new comment on a Concept or Trophy within a Concept.

        Args:
            profile: Profile instance (author)
            concept: Concept instance
            body: Comment text
            parent: Optional parent Comment for replies
            trophy_id: Optional trophy_id for trophy-level comments (None = concept-level)

        Returns:
            tuple: (Comment instance, error_message or None)
        """
        from trophies.models import Comment

        # Validate permission
        can_comment, reason = CommentService.can_comment(profile)
        if not can_comment:
            return None, reason

        # Validate concept
        if not concept:
            return None, "Cannot comment on games without a concept."

        # Sanitize the body text to prevent XSS
        body = CommentService.sanitize_text(body)

        # Validate body length (after sanitization)
        if len(body) > CommentService.MAX_COMMENT_LENGTH:
            return None, f"Comment must be under {CommentService.MAX_COMMENT_LENGTH} characters."

        if len(body) == 0:
            return None, "Comment cannot be empty."

        # Validate depth for replies
        if parent:
            if parent.depth >= CommentService.MAX_DEPTH:
                return None, "Maximum reply depth reached."
            # Ensure parent is for the same concept and trophy_id
            if parent.concept != concept or parent.trophy_id != trophy_id:
                return None, "Invalid parent comment."

        try:
            comment = Comment.objects.create(
                concept=concept,
                trophy_id=trophy_id,
                profile=profile,
                parent=parent,
                body=body.strip()
            )

            # Note: comment_count is updated automatically by signal for concept-level comments

            # Invalidate cache
            CommentService.invalidate_cache(concept, trophy_id)

            logger.info(f"Comment {comment.id} created by {profile.psn_username} on concept {concept.id} (trophy_id={trophy_id})")
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

        # Sanitize the new body text
        new_body = CommentService.sanitize_text(new_body)

        if len(new_body) > CommentService.MAX_COMMENT_LENGTH:
            return False, f"Comment must be under {CommentService.MAX_COMMENT_LENGTH} characters."

        if len(new_body) == 0:
            return False, "Comment cannot be empty."

        comment.body = new_body
        comment.is_edited = True
        comment.save(update_fields=['body', 'is_edited', 'updated_at'])

        CommentService.invalidate_cache(comment.concept, comment.trophy_id)

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

        CommentService.invalidate_cache(comment.concept, comment.trophy_id)

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
    def get_comments_for_concept(concept, profile=None, sort='top', trophy_id=None):
        """
        Get comments for a Concept or Trophy within a Concept.

        Args:
            concept: Concept instance
            profile: Optional viewing profile (for vote status)
            sort: 'top', 'new', or 'old'
            trophy_id: Optional trophy_id for trophy-level comments (None = concept-level)

        Returns:
            QuerySet: Comments for the concept/trophy
        """
        from trophies.models import Comment
        return Comment.objects.get_threaded_comments(concept, profile, sort, trophy_id)

    @staticmethod
    def get_comment_context_for_profile(comment, viewing_profile, game=None):
        """
        Build context dict for displaying a comment with author info.

        Args:
            comment: Comment instance
            viewing_profile: Profile viewing the comment
            game: Optional specific Game instance to check author's progress on

        Returns:
            dict: Comment data with author indicators
        """
        author_profile = comment.profile
        concept = comment.concept
        trophy_id = comment.trophy_id

        context = {
            'comment': comment,
            'author_username': author_profile.display_psn_username or author_profile.psn_username,
            'author_avatar': author_profile.avatar_url,
            'author_country_flag': author_profile.flag,
            'is_deleted': comment.is_deleted,
            'is_edited': comment.is_edited,
        }

        # Author indicators based on comment type
        if trophy_id is None:
            # Concept-level comment: Show author's game completion status
            # Check across all games in the concept for any completion
            from trophies.models import ProfileGame

            # Get any game in this concept that the author has played
            pg = ProfileGame.objects.filter(
                profile=author_profile,
                game__concept=concept
            ).order_by('-progress').first()

            if pg:
                context['author_progress'] = pg.progress
                context['author_has_platinum'] = pg.has_plat
            else:
                context['author_progress'] = None
                context['author_has_platinum'] = False
        else:
            # Trophy-level comment: Show if author has earned this trophy
            # trophy_id is consistent across stacks, check any stack in concept
            from trophies.models import EarnedTrophy, Trophy

            # Find trophy by trophy_id in any game stack of this concept
            trophy = Trophy.objects.filter(
                game__concept=concept,
                trophy_id=trophy_id
            ).first()

            if trophy:
                # Check if author earned it in any stack
                et = EarnedTrophy.objects.filter(
                    profile=author_profile,
                    trophy__game__concept=concept,
                    trophy__trophy_id=trophy_id,
                    earned=True
                ).first()

                if et:
                    context['author_has_trophy'] = True
                    context['author_earned_date'] = et.earned_date_time
                else:
                    context['author_has_trophy'] = False
                    context['author_earned_date'] = None
            else:
                context['author_has_trophy'] = False
                context['author_earned_date'] = None

        return context

    @staticmethod
    def _update_comment_count(concept, delta=1):
        """Update denormalized comment count on Concept."""
        concept.comment_count = F('comment_count') + delta
        concept.save(update_fields=['comment_count'])
        concept.refresh_from_db(fields=['comment_count'])

    @staticmethod
    def invalidate_cache(concept, trophy_id=None):
        """Invalidate cached comments for a concept/trophy."""
        if trophy_id is not None:
            cache_key = f"comments:concept:{concept.id}:trophy:{trophy_id}"
        else:
            cache_key = f"comments:concept:{concept.id}"
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
