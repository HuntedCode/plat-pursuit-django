"""
Review system service layer.

Handles all business logic for reviews, votes, replies, and reports.
Follows the CommentService pattern for consistency.
"""
import logging

from django.db import transaction
from django.db.models import F, Count, Q, Value
from django.db.models.functions import Greatest
from django.core.cache import cache
from django.utils import timezone

from trophies.services.comment_service import CommentService
from trophies.services.concept_trophy_group_service import ConceptTrophyGroupService

logger = logging.getLogger('psn_api')


class ReviewService:
    """Handles review operations including CRUD, voting, replies, and reporting."""

    REVIEW_CACHE_TIMEOUT = 1800  # 30 minutes for recommendation stats
    MIN_BODY_LENGTH = 50
    MAX_BODY_LENGTH = 8000
    MAX_REPLY_LENGTH = 2000
    HELPFUL_MILESTONES = [5, 10, 25, 50]

    # ------------------------------------------------------------------ #
    #  CRUD: Reviews
    # ------------------------------------------------------------------ #

    @staticmethod
    @transaction.atomic
    def create_review(profile, concept, concept_trophy_group, body, recommended):
        """Create a new review for a concept trophy group.

        Args:
            profile: Profile instance (author)
            concept: Concept instance
            concept_trophy_group: ConceptTrophyGroup instance
            body: Review text (raw markdown, 50-8000 chars)
            recommended: bool (True = thumbs up, False = thumbs down)

        Returns:
            tuple: (Review instance, error_message or None)
        """
        from trophies.models import Review

        # Access check
        can, reason = ConceptTrophyGroupService.can_review_group(
            profile, concept, concept_trophy_group,
        )
        if not can:
            return None, reason

        # Validate body length (raw text, stripped whitespace)
        stripped = body.strip() if body else ''
        if len(stripped) < ReviewService.MIN_BODY_LENGTH:
            return None, f"Review must be at least {ReviewService.MIN_BODY_LENGTH} characters."
        if len(stripped) > ReviewService.MAX_BODY_LENGTH:
            return None, f"Review must be under {ReviewService.MAX_BODY_LENGTH} characters."

        # Banned words check on raw text
        contains_banned, matched = CommentService.check_banned_words(stripped)
        if contains_banned:
            logger.warning(
                f"Review blocked for {profile.psn_username}: banned word '{matched}'"
            )
            return None, "Your review contains inappropriate content and cannot be posted."

        # Check for duplicate (unique_together enforces at DB level, but surface a friendly message)
        if Review.objects.filter(
            profile=profile,
            concept=concept,
            concept_trophy_group=concept_trophy_group,
        ).exists():
            return None, "You have already reviewed this. Edit your existing review instead."

        try:
            review = Review.objects.create(
                concept=concept,
                concept_trophy_group=concept_trophy_group,
                profile=profile,
                body=stripped,
                recommended=recommended,
            )
            ReviewService._invalidate_recommendation_cache(concept, concept_trophy_group)
            logger.info(
                f"Review {review.id} created by {profile.psn_username} "
                f"on concept {concept.id} group {concept_trophy_group.trophy_group_id}"
            )
            return review, None

        except Exception as e:
            logger.exception(f"Error creating review: {e}")
            return None, "An error occurred creating your review."

    @staticmethod
    @transaction.atomic
    def update_review(review, profile, body=None, recommended=None):
        """Edit an existing review.

        Args:
            review: Review instance
            profile: Profile making the edit (must be owner)
            body: New body text (optional, None = no change)
            recommended: New recommendation (optional, None = no change)

        Returns:
            tuple: (success bool, error_message or None)
        """
        if review.profile != profile:
            return False, "You can only edit your own reviews."

        if review.is_deleted:
            return False, "Cannot edit a deleted review."

        update_fields = ['is_edited', 'updated_at']
        recommendation_changed = False

        if body is not None:
            stripped = body.strip()
            if len(stripped) < ReviewService.MIN_BODY_LENGTH:
                return False, f"Review must be at least {ReviewService.MIN_BODY_LENGTH} characters."
            if len(stripped) > ReviewService.MAX_BODY_LENGTH:
                return False, f"Review must be under {ReviewService.MAX_BODY_LENGTH} characters."

            contains_banned, matched = CommentService.check_banned_words(stripped)
            if contains_banned:
                logger.warning(
                    f"Review edit blocked for {profile.psn_username}: banned word '{matched}'"
                )
                return False, "Your review contains inappropriate content and cannot be saved."

            review.body = stripped
            update_fields.append('body')

        if recommended is not None and recommended != review.recommended:
            review.recommended = recommended
            update_fields.append('recommended')
            recommendation_changed = True

        review.is_edited = True
        review.save(update_fields=update_fields)

        if recommendation_changed:
            ReviewService._invalidate_recommendation_cache(
                review.concept, review.concept_trophy_group,
            )

        logger.info(f"Review {review.id} edited by {profile.psn_username}")
        return True, None

    @staticmethod
    @transaction.atomic
    def delete_review(review, profile, is_admin=False, moderator=None, reason="", request=None):
        """Soft delete a review.

        Args:
            review: Review instance
            profile: Profile requesting deletion
            is_admin: Whether this is an admin action
            moderator: CustomUser performing moderation (for ReviewModerationLog)
            reason: Reason for deletion (for audit trail)
            request: HttpRequest for IP capture

        Returns:
            tuple: (success bool, error_message or None)
        """
        if not is_admin and review.profile != profile:
            return False, "You can only delete your own reviews."

        if review.is_deleted:
            return False, "Review is already deleted."

        review.soft_delete(moderator=moderator, reason=reason, request=request)

        ReviewService._invalidate_recommendation_cache(
            review.concept, review.concept_trophy_group,
        )

        action = "admin deleted" if is_admin else "deleted"
        logger.info(f"Review {review.id} {action} by {profile.psn_username}")
        return True, None

    # ------------------------------------------------------------------ #
    #  Voting
    # ------------------------------------------------------------------ #

    @staticmethod
    @transaction.atomic
    def toggle_vote(review, profile, vote_type):
        """Toggle a helpful or funny vote on a review.

        Args:
            review: Review instance
            profile: Profile voting
            vote_type: 'helpful' or 'funny'

        Returns:
            tuple: (bool_or_None, error_message_or_None)
                True = vote added, False = vote removed, None = error
        """
        from trophies.models import ReviewVote

        can, reason = CommentService.can_interact(profile)
        if not can:
            return None, reason

        if review.is_deleted:
            return None, "Cannot vote on deleted reviews."

        if review.profile == profile:
            return None, "Cannot vote on your own review."

        if vote_type not in ('helpful', 'funny'):
            return None, "Invalid vote type."

        count_field = 'helpful_count' if vote_type == 'helpful' else 'funny_count'

        existing = ReviewVote.objects.filter(
            review=review, profile=profile, vote_type=vote_type,
        ).first()

        if existing:
            # Remove vote
            existing.delete()
            setattr(review, count_field, F(count_field) - 1)
            review.save(update_fields=[count_field])
            review.refresh_from_db(fields=[count_field])
            return False, None
        else:
            # Add vote
            ReviewVote.objects.create(
                review=review, profile=profile, vote_type=vote_type,
            )
            setattr(review, count_field, F(count_field) + 1)
            review.save(update_fields=[count_field])
            review.refresh_from_db(fields=[count_field])

            # Check helpful milestones (only for helpful votes)
            if vote_type == 'helpful':
                ReviewService._check_helpful_milestones(review)

            return True, None

    @staticmethod
    def _check_helpful_milestones(review):
        """Send a notification when a review crosses a helpful milestone.

        Only notifies if the count exactly matches a milestone value,
        preventing re-fire on vote toggle.

        Args:
            review: Review instance (must be refreshed from DB)
        """
        from notifications.services.notification_service import NotificationService

        count = review.helpful_count
        if count not in ReviewService.HELPFUL_MILESTONES:
            return

        # Only notify the review author (not the voter)
        user = review.profile.user
        if not user:
            return

        concept_title = review.concept.unified_title or "a game"
        NotificationService.create_notification(
            recipient=user,
            notification_type='review_milestone',
            title=f"Your review hit {count} helpful votes!",
            message=(
                f"Your review of {concept_title} has been marked helpful "
                f"by {count} people. Keep sharing your insights!"
            ),
            icon='👍',
            action_url=f"/community/{review.concept.slug}/",
            action_text='View Review',
        )

    # ------------------------------------------------------------------ #
    #  Replies
    # ------------------------------------------------------------------ #

    @staticmethod
    @transaction.atomic
    def create_reply(review, profile, body):
        """Create a reply on a review.

        Args:
            review: Review instance
            profile: Profile writing the reply
            body: Reply text (plain text, 1-2000 chars)

        Returns:
            tuple: (ReviewReply instance, error_message or None)
        """
        from trophies.models import ReviewReply

        can, reason = CommentService.can_comment(profile)
        if not can:
            return None, reason

        if review.is_deleted:
            return None, "Cannot reply to a deleted review."

        # Sanitize plain text (no markdown for replies)
        body = CommentService.sanitize_text(body)

        if not body:
            return None, "Reply cannot be empty."
        if len(body) > ReviewService.MAX_REPLY_LENGTH:
            return None, f"Reply must be under {ReviewService.MAX_REPLY_LENGTH} characters."

        # Banned words
        contains_banned, matched = CommentService.check_banned_words(body)
        if contains_banned:
            logger.warning(
                f"Reply blocked for {profile.psn_username}: banned word '{matched}'"
            )
            return None, "Your reply contains inappropriate content and cannot be posted."

        try:
            reply = ReviewReply.objects.create(
                review=review,
                profile=profile,
                body=body,
            )

            # Increment denormalized count
            review.reply_count = F('reply_count') + 1
            review.save(update_fields=['reply_count'])
            review.refresh_from_db(fields=['reply_count'])

            # Send notification to review author (unless self-reply)
            if review.profile != profile and review.profile.user:
                from notifications.services.notification_service import NotificationService

                concept_title = review.concept.unified_title or "a game"
                NotificationService.create_notification(
                    recipient=review.profile.user,
                    notification_type='review_reply',
                    title=f"{profile.display_psn_username or profile.psn_username} replied to your review",
                    message=f"New reply on your review of {concept_title}.",
                    icon='💬',
                    action_url=f"/community/{review.concept.slug}/",
                    action_text='View Reply',
                )

            logger.info(f"Reply {reply.id} created on review {review.id} by {profile.psn_username}")
            return reply, None

        except Exception as e:
            logger.exception(f"Error creating reply: {e}")
            return None, "An error occurred creating your reply."

    @staticmethod
    @transaction.atomic
    def edit_reply(reply, profile, new_body):
        """Edit an existing reply.

        Args:
            reply: ReviewReply instance
            profile: Profile making the edit (must be owner)
            new_body: New reply text

        Returns:
            tuple: (success bool, error_message or None)
        """
        if reply.profile != profile:
            return False, "You can only edit your own replies."

        if reply.is_deleted:
            return False, "Cannot edit a deleted reply."

        new_body = CommentService.sanitize_text(new_body)

        if not new_body:
            return False, "Reply cannot be empty."
        if len(new_body) > ReviewService.MAX_REPLY_LENGTH:
            return False, f"Reply must be under {ReviewService.MAX_REPLY_LENGTH} characters."

        contains_banned, matched = CommentService.check_banned_words(new_body)
        if contains_banned:
            logger.warning(
                f"Reply edit blocked for {profile.psn_username}: banned word '{matched}'"
            )
            return False, "Your reply contains inappropriate content and cannot be saved."

        reply.body = new_body
        reply.is_edited = True
        reply.save(update_fields=['body', 'is_edited', 'updated_at'])

        logger.info(f"Reply {reply.id} edited by {profile.psn_username}")
        return True, None

    @staticmethod
    @transaction.atomic
    def delete_reply(reply, profile, is_admin=False):
        """Soft delete a reply and decrement the review's reply count.

        Args:
            reply: ReviewReply instance
            profile: Profile requesting deletion
            is_admin: Whether this is an admin action

        Returns:
            tuple: (success bool, error_message or None)
        """
        if not is_admin and reply.profile != profile:
            return False, "You can only delete your own replies."

        if reply.is_deleted:
            return False, "Reply is already deleted."

        reply.is_deleted = True
        reply.deleted_at = timezone.now()
        reply.body = '[deleted]'
        reply.save(update_fields=['is_deleted', 'deleted_at', 'body'])

        # Decrement denormalized count (clamped to 0 in a single query)
        review = reply.review
        review.reply_count = Greatest(F('reply_count') - 1, Value(0))
        review.save(update_fields=['reply_count'])

        action = "admin deleted" if is_admin else "deleted"
        logger.info(f"Reply {reply.id} {action} by {profile.psn_username}")
        return True, None

    # ------------------------------------------------------------------ #
    #  Reporting
    # ------------------------------------------------------------------ #

    @staticmethod
    def report_review(review, reporter, reason, details=''):
        """Submit a report for a review.

        Args:
            review: Review instance
            reporter: Profile submitting report
            reason: Report reason code
            details: Additional details text

        Returns:
            tuple: (ReviewReport or None, error_message or None)
        """
        from trophies.models import ReviewReport

        can, err = CommentService.can_interact(reporter)
        if not can:
            return None, err

        if review.is_deleted:
            return None, "Cannot report deleted reviews."

        existing = ReviewReport.objects.filter(
            review=review, reporter=reporter,
        ).first()
        if existing:
            return None, "You have already reported this review."

        report = ReviewReport.objects.create(
            review=review,
            reporter=reporter,
            reason=reason,
            details=details[:500] if details else '',
        )

        logger.info(f"Review {review.id} reported by {reporter.psn_username}: {reason}")
        return report, None

    # ------------------------------------------------------------------ #
    #  Aggregation & Stats
    # ------------------------------------------------------------------ #

    @staticmethod
    def get_recommendation_stats(concept, concept_trophy_group):
        """Get recommendation stats for a concept trophy group.

        Cached for 30 minutes.

        Args:
            concept: Concept instance
            concept_trophy_group: ConceptTrophyGroup instance

        Returns:
            dict or None: {recommended, not_recommended, total, percent} or None
        """
        cache_key = (
            f"review:recommend:{concept.id}:{concept_trophy_group.id}"
        )
        stats = cache.get(cache_key)
        if stats is not None:
            return stats

        stats = ReviewService._compute_recommendation_stats(
            concept, concept_trophy_group,
        )
        if stats:
            cache.set(cache_key, stats, ReviewService.REVIEW_CACHE_TIMEOUT)
        return stats

    @staticmethod
    def _compute_recommendation_stats(concept, concept_trophy_group):
        """Compute recommendation stats from the database.

        Args:
            concept: Concept instance
            concept_trophy_group: ConceptTrophyGroup instance

        Returns:
            dict or None
        """
        from trophies.models import Review

        qs = Review.objects.filter(
            concept=concept,
            concept_trophy_group=concept_trophy_group,
            is_deleted=False,
        )
        agg = qs.aggregate(
            recommended=Count('id', filter=Q(recommended=True)),
            not_recommended=Count('id', filter=Q(recommended=False)),
            total=Count('id'),
        )

        if agg['total'] == 0:
            return None

        agg['percent'] = round(agg['recommended'] / agg['total'] * 100, 1)
        return agg

    @staticmethod
    def get_reviewer_stats(profile, concept):
        """Player stats shown on review cards.

        Args:
            profile: Profile instance (review author)
            concept: Concept instance

        Returns:
            dict: {completion_pct, has_plat, hours_played}
        """
        from trophies.models import ProfileGame

        pgs = ProfileGame.objects.filter(
            profile=profile,
            game__concept=concept,
        ).values('progress', 'has_plat', 'play_duration')

        best_progress = 0
        has_plat = False
        total_hours = None

        for pg in pgs:
            if pg['progress'] > best_progress:
                best_progress = pg['progress']
            if pg['has_plat']:
                has_plat = True
            if pg['play_duration'] is not None:
                if total_hours is None:
                    total_hours = pg['play_duration']
                else:
                    total_hours += pg['play_duration']

        return {
            'completion_pct': best_progress,
            'has_plat': has_plat,
            'hours_played': total_hours,
        }

    @staticmethod
    def get_reviewer_stats_bulk(profile_ids, concept):
        """Batch version of get_reviewer_stats for review feeds.

        Pre-fetches all stats in a single query.

        Args:
            profile_ids: list of Profile IDs
            concept: Concept instance

        Returns:
            dict: {profile_id: {completion_pct, has_plat, hours_played}}
        """
        from trophies.models import ProfileGame

        if not profile_ids:
            return {}

        pgs = ProfileGame.objects.filter(
            profile_id__in=profile_ids,
            game__concept=concept,
        ).values('profile_id', 'progress', 'has_plat', 'play_duration')

        result = {}
        for pg in pgs:
            pid = pg['profile_id']
            if pid not in result:
                result[pid] = {
                    'completion_pct': 0,
                    'has_plat': False,
                    'hours_played': None,
                }

            entry = result[pid]
            if pg['progress'] > entry['completion_pct']:
                entry['completion_pct'] = pg['progress']
            if pg['has_plat']:
                entry['has_plat'] = True
            if pg['play_duration'] is not None:
                if entry['hours_played'] is None:
                    entry['hours_played'] = pg['play_duration']
                else:
                    entry['hours_played'] += pg['play_duration']

        # Fill in missing profiles with defaults
        for pid in profile_ids:
            if pid not in result:
                result[pid] = {
                    'completion_pct': 0,
                    'has_plat': False,
                    'hours_played': None,
                }

        return result

    # ------------------------------------------------------------------ #
    #  Cache helpers
    # ------------------------------------------------------------------ #

    @staticmethod
    def _invalidate_recommendation_cache(concept, concept_trophy_group):
        """Invalidate cached recommendation stats."""
        cache_key = (
            f"review:recommend:{concept.id}:{concept_trophy_group.id}"
        )
        cache.delete(cache_key)
