"""
Guide service - Handles guide CRUD, publishing, moderation, and trust management.

This service consolidates all guide-related business logic including:
- Creating and editing guides and sections
- Publishing workflow (submit, approve, reject)
- Author trust management and auto-promotion
- Draft changes for published guides
"""
import logging
from django.db import transaction
from django.db.models import F
from django.utils import timezone
from django.core.exceptions import ValidationError, PermissionDenied

from trophies.models import Guide, GuideSection, AuthorTrust, GuideTag
from trophies.constants import SUMMARY_CHAR_LIMIT, BASIC_SECTION_CHAR_LIMIT, PREMIUM_SECTION_CHAR_LIMIT, BASIC_MAX_SECTIONS, PREMIUM_MAX_SECTIONS, TRUSTED_MIN_APPROVED_GUIDES, TRUSTED_MIN_TOTAL_STARS

logger = logging.getLogger(__name__)


class GuideService:
    """Handles guide operations and author trust management."""

    # === Permission Checks ===

    @staticmethod
    def can_create_guide(profile):
        """
        Check if a profile can create guides.

        Args:
            profile: Profile instance to check

        Returns:
            tuple[bool, str]: (can_create, reason)
        """
        if not profile.is_linked:
            return (False, "You must link your PSN account to create guides")

        try:
            author_trust = AuthorTrust.objects.get(profile=profile)
            if author_trust.trust_level == 'banned':
                return (False, "You are banned from creating guides")
        except AuthorTrust.DoesNotExist:
            pass

        return (True, "")

    # === Guide CRUD ===

    @staticmethod
    @transaction.atomic
    def create_guide(profile, game, title, summary, tags=None):
        """
        Create a new draft guide.

        Args:
            profile: Author profile
            game: Game instance
            title: Guide title
            summary: Guide summary (max 500 chars)
            tags: Optional list of GuideTag instances

        Returns:
            Guide instance

        Raises:
            PermissionError: If profile cannot create guides
            ValidationError: If summary exceeds limit
        """
        can_create, reason = GuideService.can_create_guide(profile)
        if not can_create:
            raise PermissionDenied(reason)

        if len(summary) > SUMMARY_CHAR_LIMIT:
            raise ValidationError(
                f"Summary must be {SUMMARY_CHAR_LIMIT} characters or less"
            )

        guide = Guide.objects.create(
            author=profile,
            game=game,
            concept=game.concept,
            title=title,
            summary=summary,
            status='draft'
        )

        if tags:
            guide.tags.set(tags)

        GuideService.get_or_create_author_trust(profile)

        logger.info(f"Guide created: {guide.id} by {profile.psn_username}")
        return guide

    @staticmethod
    @transaction.atomic
    def create_roadmap_guide(profile, game, title, summary):
        """
        Create a Trophy Roadmap guide with automatic sections for all trophies.

        Creates:
        - 3 fixed intro sections (Overview, Trophy Roadmap, General Tips)
        - One section per trophy (ordered by trophy_id)
        - Auto-applies "Roadmap" tag
        - Template content for all sections

        Args:
            profile: Author profile
            game: Game instance
            title: Guide title
            summary: Guide summary (max 500 chars)

        Returns:
            Guide instance

        Raises:
            PermissionDenied: If profile cannot create guides
            ValidationError: If summary too long, no trophies, or trophy count exceeds limits
        """
        # Check permissions
        can_create, reason = GuideService.can_create_guide(profile)
        if not can_create:
            raise PermissionDenied(reason)

        # Validate summary length
        if len(summary) > SUMMARY_CHAR_LIMIT:
            raise ValidationError(
                f"Summary must be {SUMMARY_CHAR_LIMIT} characters or less"
            )

        # Fetch trophies
        trophies = game.trophies.all().order_by('trophy_id')
        trophy_count = trophies.count()

        # Validate that game has trophies
        if trophy_count == 0:
            raise ValidationError(
                "Cannot create Trophy Roadmap guide for a game with no trophies"
            )

        # Calculate total sections and check limits
        total_sections = 3 + trophy_count
        max_sections = PREMIUM_MAX_SECTIONS if profile.user_is_premium else BASIC_MAX_SECTIONS

        if total_sections > max_sections:
            if profile.user_is_premium:
                raise ValidationError(
                    f"This game has {trophy_count} trophies, which would require "
                    f"{total_sections} total sections (including 3 intro sections). "
                    f"Your Premium account allows up to {max_sections} sections. "
                    f"This game has too many trophies for a Trophy Roadmap guide."
                )
            else:
                raise ValidationError(
                    f"This game has {trophy_count} trophies, which would require "
                    f"{total_sections} total sections (including 3 intro sections). "
                    f"Your Basic account allows up to {max_sections} sections. "
                    f"Upgrade to Premium for more sections."
                )

        # Get roadmap tag
        try:
            roadmap_tag = GuideTag.objects.get(slug='roadmap')
        except GuideTag.DoesNotExist:
            raise ValidationError(
                "Roadmap tag does not exist. Please create it in the admin panel."
            )

        # Create guide
        guide = Guide.objects.create(
            author=profile,
            game=game,
            concept=game.concept,
            title=title,
            summary=summary,
            guide_type='roadmap',
            status='draft'
        )

        # Apply roadmap tag
        guide.tags.set([roadmap_tag])

        # Create 3 intro sections
        GuideSection.objects.create(
            guide=guide,
            title="Overview",
            content=GuideService._get_roadmap_overview_template(game),
            section_order=0
        )

        GuideSection.objects.create(
            guide=guide,
            title="Trophy Roadmap",
            content=GuideService._get_roadmap_roadmap_template(game),
            section_order=1
        )

        GuideSection.objects.create(
            guide=guide,
            title="General Tips",
            content=GuideService._get_roadmap_tips_template(game),
            section_order=2
        )

        # Create trophy sections
        for idx, trophy in enumerate(trophies):
            GuideSection.objects.create(
                guide=guide,
                title=trophy.trophy_name,
                content=GuideService._get_trophy_section_template(trophy),
                section_order=3 + idx
            )

        # Create or get author trust
        GuideService.get_or_create_author_trust(profile)

        logger.info(
            f"Roadmap guide created: {guide.id} by {profile.psn_username} "
            f"for {game.title_name} with {trophy_count} trophies"
        )
        return guide

    @staticmethod
    def add_section(guide, title, content, section_order=None):
        """
        Add a section to a guide.

        Args:
            guide: Guide instance
            title: Section title
            content: Section content (markdown)
            section_order: Optional position (defaults to end)

        Returns:
            GuideSection instance

        Raises:
            ValidationError: If limits exceeded
        """
        if guide.status not in ['draft', 'published']:
            raise ValidationError("Can only add sections to draft or published guides")

        is_premium = guide.author.user_is_premium
        char_limit = (PREMIUM_SECTION_CHAR_LIMIT if is_premium
                     else BASIC_SECTION_CHAR_LIMIT)

        if len(content) > char_limit:
            raise ValidationError(
                f"Section content must be {char_limit} characters or less"
            )

        max_sections = (PREMIUM_MAX_SECTIONS if is_premium
                       else BASIC_MAX_SECTIONS)

        if guide.sections.count() >= max_sections:
            raise ValidationError(
                f"Maximum of {max_sections} sections allowed"
            )

        if section_order is None:
            section_order = guide.sections.count()

        section = GuideSection.objects.create(
            guide=guide,
            title=title,
            content=content,
            section_order=section_order
        )

        logger.info(f"Section added to guide {guide.id}: {section.id}")
        return section

    @staticmethod
    def update_section(section, title=None, content=None):
        """
        Update an existing section.

        Args:
            section: GuideSection instance
            title: Optional new title
            content: Optional new content

        Returns:
            GuideSection instance

        Raises:
            ValidationError: If content exceeds limit
        """
        if content is not None:
            is_premium = section.guide.author.user_is_premium
            char_limit = (PREMIUM_SECTION_CHAR_LIMIT if is_premium
                         else BASIC_SECTION_CHAR_LIMIT)

            if len(content) > char_limit:
                raise ValidationError(
                    f"Section content must be {char_limit} characters or less"
                )

            section.content = content

        if title is not None:
            section.title = title

        section.save()
        logger.info(f"Section updated: {section.id}")
        return section

    @staticmethod
    @transaction.atomic
    def reorder_sections(guide, section_ids):
        """
        Reorder sections by list of section IDs.

        Args:
            guide: Guide instance
            section_ids: List of section IDs in desired order

        Raises:
            ValidationError: If section IDs don't match guide's sections
        """
        sections = list(guide.sections.all())
        section_id_set = {s.id for s in sections}

        if set(section_ids) != section_id_set:
            raise ValidationError("Section IDs must match all guide sections")

        # Two-phase update to avoid unique constraint violations:
        # Phase 1: Set all sections to temporary high values (offset by 10000)
        for i, section in enumerate(sections):
            section.section_order = 10000 + i
            section.save()

        # Phase 2: Set final order values
        for order, section_id in enumerate(section_ids):
            GuideSection.objects.filter(id=section_id).update(section_order=order)

        logger.info(f"Sections reordered for guide {guide.id}")

    @staticmethod
    def delete_section(section):
        """
        Delete a section and renumber remaining sections.

        Args:
            section: GuideSection instance to delete
        """
        guide = section.guide
        section.delete()

        remaining_sections = guide.sections.order_by('section_order')
        for order, sec in enumerate(remaining_sections):
            if sec.section_order != order:
                sec.section_order = order
                sec.save()

        logger.info(f"Section deleted and guide {guide.id} sections renumbered")

    # === Publishing & Moderation ===

    @staticmethod
    @transaction.atomic
    def submit_for_review(guide, profile):
        """
        Submit a guide for review.

        Args:
            guide: Guide instance
            profile: Profile submitting (must be author)

        Returns:
            tuple[str, str]: (status, message)

        Raises:
            PermissionDenied: If profile is not the author
            ValidationError: If guide doesn't meet requirements
        """
        if guide.author != profile:
            raise PermissionDenied("Only the author can submit their guide")

        if guide.status not in ['draft', 'rejected']:
            raise ValidationError("Guide must be in draft or rejected status")

        if guide.sections.count() == 0:
            raise ValidationError("Guide must have at least one section")

        author_trust = GuideService.get_or_create_author_trust(profile)

        if author_trust.can_auto_publish():
            guide.status = 'published'
            guide.published_at = timezone.now()
            guide.save()
            logger.info(f"Guide {guide.id} auto-published for trusted author")
            return ('published', 'Your guide has been published!')
        else:
            guide.status = 'pending'
            guide.save()
            logger.info(f"Guide {guide.id} submitted for review")
            return ('pending', 'Your guide has been submitted for review.')

    @staticmethod
    @transaction.atomic
    def approve_guide(guide, moderator):
        """
        Moderator approves a pending guide.

        Args:
            guide: Guide instance
            moderator: Moderator profile

        Returns:
            Guide instance

        Raises:
            ValidationError: If guide is not pending
        """
        if guide.status != 'pending':
            raise ValidationError("Only pending guides can be approved")

        guide.status = 'published'
        guide.moderated_by = moderator
        guide.moderated_at = timezone.now()
        guide.published_at = timezone.now()
        guide.save()

        author_trust = guide.author.author_trust
        author_trust.approved_guide_count = F('approved_guide_count') + 1
        author_trust.save()
        author_trust.refresh_from_db()

        GuideService._check_and_promote(author_trust)

        logger.info(f"Guide {guide.id} approved by {moderator.psn_username}")
        return guide

    @staticmethod
    @transaction.atomic
    def reject_guide(guide, moderator, reason):
        """
        Moderator rejects a pending guide.

        Args:
            guide: Guide instance
            moderator: Moderator profile
            reason: Rejection reason

        Returns:
            Guide instance
        """
        guide.status = 'rejected'
        guide.rejection_reason = reason
        guide.moderated_by = moderator
        guide.moderated_at = timezone.now()
        guide.save()

        logger.info(f"Guide {guide.id} rejected by {moderator.psn_username}")
        return guide

    @staticmethod
    def unlist_guide(guide):
        """
        Unlist a published guide.

        Args:
            guide: Guide instance

        Returns:
            Guide instance

        Raises:
            ValidationError: If guide is not published
        """
        if guide.status != 'published':
            raise ValidationError("Only published guides can be unlisted")

        guide.status = 'unlisted'
        guide.save()

        logger.info(f"Guide {guide.id} unlisted")
        return guide

    @staticmethod
    def republish_guide(guide):
        """
        Republish an unlisted guide.

        Args:
            guide: Guide instance

        Returns:
            Guide instance

        Raises:
            ValidationError: If guide is not unlisted
        """
        if guide.status != 'unlisted':
            raise ValidationError("Only unlisted guides can be republished")

        guide.status = 'published'
        guide.save()

        logger.info(f"Guide {guide.id} republished")
        return guide

    # === Draft Changes (for published guides) ===

    @staticmethod
    def save_section_draft(section, draft_content):
        """
        Save draft changes to a published guide's section.

        Args:
            section: GuideSection instance
            draft_content: Draft content to save

        Returns:
            GuideSection instance

        Raises:
            ValidationError: If content exceeds limit
        """
        is_premium = section.guide.author.user_is_premium
        char_limit = (PREMIUM_SECTION_CHAR_LIMIT if is_premium
                     else BASIC_SECTION_CHAR_LIMIT)

        if len(draft_content) > char_limit:
            raise ValidationError(
                f"Draft content must be {char_limit} characters or less"
            )

        section.draft_content = draft_content
        section.has_pending_edits = True
        section.save()

        logger.info(f"Draft saved for section {section.id}")
        return section

    @staticmethod
    @transaction.atomic
    def publish_section_drafts(guide):
        """
        Apply all pending section drafts to live content.

        Args:
            guide: Guide instance
        """
        sections_with_drafts = guide.sections.filter(has_pending_edits=True)

        for section in sections_with_drafts:
            section.content = section.draft_content
            section.draft_content = ''
            section.has_pending_edits = False
            section.save()

        logger.info(f"Published {sections_with_drafts.count()} section drafts for guide {guide.id}")

    @staticmethod
    def discard_section_drafts(guide):
        """
        Discard all pending section drafts.

        Args:
            guide: Guide instance
        """
        count = guide.sections.filter(has_pending_edits=True).update(
            draft_content='',
            has_pending_edits=False
        )

        logger.info(f"Discarded {count} section drafts for guide {guide.id}")

    # === Trust Management ===

    @staticmethod
    def get_or_create_author_trust(profile):
        """
        Get or create AuthorTrust record for a profile.

        Args:
            profile: Profile instance

        Returns:
            AuthorTrust instance
        """
        author_trust, created = AuthorTrust.objects.get_or_create(profile=profile)
        if created:
            logger.info(f"AuthorTrust created for profile {profile.psn_username}")
        return author_trust

    @staticmethod
    def check_auto_promotion(author_trust):
        """
        Check if author qualifies for trusted status.

        Args:
            author_trust: AuthorTrust instance

        Returns:
            bool: True if qualifies for promotion
        """
        return (
            author_trust.trust_level == 'new' and
            author_trust.approved_guide_count >= TRUSTED_MIN_APPROVED_GUIDES and
            author_trust.total_stars_received >= TRUSTED_MIN_TOTAL_STARS
        )

    @staticmethod
    @transaction.atomic
    def promote_to_trusted(author_trust):
        """
        Promote author to trusted status.

        Args:
            author_trust: AuthorTrust instance

        Returns:
            AuthorTrust instance
        """
        author_trust.trust_level = 'trusted'
        author_trust.promoted_at = timezone.now()
        author_trust.save()

        logger.info(f"Author {author_trust.profile.psn_username} promoted to trusted")
        return author_trust

    @staticmethod
    def _check_and_promote(author_trust):
        """
        Internal helper to check and promote if qualified.

        Args:
            author_trust: AuthorTrust instance

        Returns:
            bool: True if promoted, False otherwise
        """
        if GuideService.check_auto_promotion(author_trust):
            GuideService.promote_to_trusted(author_trust)
            return True
        return False

    @staticmethod
    @transaction.atomic
    def ban_author(author_trust, reason):
        """
        Ban an author from creating guides.

        Args:
            author_trust: AuthorTrust instance
            reason: Ban reason

        Returns:
            AuthorTrust instance
        """
        author_trust.trust_level = 'banned'
        author_trust.banned_at = timezone.now()
        author_trust.ban_reason = reason
        author_trust.save()

        logger.info(f"Author {author_trust.profile.psn_username} banned: {reason}")
        return author_trust

    # === Query Helpers ===

    @staticmethod
    def get_published_guides_for_game(game, limit=None):
        """
        Get published guides for a specific game.

        Args:
            game: Game instance
            limit: Optional result limit

        Returns:
            QuerySet of Guide instances
        """
        qs = Guide.objects.filter(
            game=game,
            status='published'
        ).select_related('author').order_by('-average_rating', '-published_at')

        if limit:
            qs = qs[:limit]
        return qs

    @staticmethod
    def get_published_guides_for_concept(concept, limit=None):
        """
        Get published guides for a specific concept.

        Args:
            concept: Concept instance
            limit: Optional result limit

        Returns:
            QuerySet of Guide instances
        """
        qs = Guide.objects.filter(
            concept=concept,
            status='published'
        ).select_related('author', 'game').order_by('-average_rating', '-published_at')

        if limit:
            qs = qs[:limit]
        return qs

    @staticmethod
    def get_pending_guides():
        """
        Get all pending guides for moderation.

        Returns:
            QuerySet of Guide instances
        """
        return Guide.objects.filter(
            status='pending'
        ).select_related('author', 'game').order_by('created_at')

    @staticmethod
    def get_user_guides(profile):
        """
        Get all guides authored by a profile.

        Args:
            profile: Profile instance

        Returns:
            QuerySet of Guide instances
        """
        return Guide.objects.filter(
            author=profile
        ).select_related('game').order_by('-updated_at')

    # === Utility Methods ===

    @staticmethod
    def get_limits_for_profile(profile):
        """
        Get all limits for a profile based on premium status.

        Args:
            profile: Profile instance

        Returns:
            dict: Dictionary of limits
        """
        is_premium = profile.user_is_premium

        return {
            'max_sections': PREMIUM_MAX_SECTIONS if is_premium else BASIC_MAX_SECTIONS,
            'section_char_limit': PREMIUM_SECTION_CHAR_LIMIT if is_premium else BASIC_SECTION_CHAR_LIMIT,
            'summary_char_limit': SUMMARY_CHAR_LIMIT,
        }

    @staticmethod
    def increment_view_count(guide):
        """
        Increment view count for a guide.

        Args:
            guide: Guide instance
        """
        Guide.objects.filter(pk=guide.pk).update(view_count=F('view_count') + 1)

    # === Template Helpers for Roadmap Guides ===

    @staticmethod
    def _get_roadmap_overview_template(game):
        """
        Get template content for the Overview section of a roadmap guide.

        Args:
            game: Game instance

        Returns:
            str: Markdown template content
        """
        return f"""This is a Trophy Roadmap guide for **{game.title_name}**.

**Estimated Platinum Difficulty:** [Rate 1-10]

**Estimated Time to Platinum:** [Time estimate]

**Missable Trophies:** [Yes/No - List any missables]

**Minimum Playthroughs:** [Number]

**Glitched/Unobtainable Trophies:** [Yes/No - Describe any issues]

## Introduction

[Brief introduction to the game and what to expect from the platinum journey]

## Recommended Roadmap Outline

[High-level overview of the recommended approach to earning the platinum]

**Step 1:** [Brief description]
**Step 2:** [Brief description]
**Step 3:** [Brief description]
"""

    @staticmethod
    def _get_roadmap_roadmap_template(game):
        """
        Get template content for the Trophy Roadmap section.

        Args:
            game: Game instance

        Returns:
            str: Markdown template content
        """
        return f"""This section provides a detailed step-by-step roadmap for earning all trophies in **{game.title_name}**.

**Note:** Trophy references like [trophy:123] will automatically link to the specific trophy section below.

## Step 1: [Phase Name]

[Describe what to do in this phase]

**Trophies you should earn:**
- [trophy:1] - [Trophy Name]
- [trophy:2] - [Trophy Name]
- [trophy:3] - [Trophy Name]

## Step 2: [Phase Name]

[Describe what to do in this phase]

**Trophies you should earn:**
- [trophy:4] - [Trophy Name]
- [trophy:5] - [Trophy Name]

## Step 3: Cleanup

[Describe cleanup phase for any remaining trophies]

**Remaining trophies:**
- [trophy:X] - [Trophy Name]
- [trophy:Y] - [Trophy Name]
"""

    @staticmethod
    def _get_roadmap_tips_template(game):
        """
        Get template content for the General Tips section.

        Args:
            game: Game instance

        Returns:
            str: Markdown template content
        """
        return f"""This section contains general tips and information for earning trophies in **{game.title_name}**.

## General Tips

**Tip 1:** [First general tip]

**Tip 2:** [Second general tip]

**Tip 3:** [Third general tip]

## Difficulty Settings

[Information about difficulty settings and their impact on trophies]

## Collectibles

[Information about collectibles if applicable]

## Online Trophies

[Information about online trophies, if applicable. Note if servers are active/inactive]

## Glitches & Bugs

[Known glitches, bugs, or workarounds]

## Useful Resources

[Links to helpful resources, videos, or other guides]
"""

    @staticmethod
    def _get_trophy_section_template(trophy):
        """
        Get template content for an individual trophy section.

        Args:
            trophy: Trophy instance

        Returns:
            str: Markdown template content
        """
        # Map trophy types to emojis
        emoji_map = {
            'bronze': '🥉',
            'silver': '🥈',
            'gold': '🥇',
            'platinum': '🏆'
        }
        emoji = emoji_map.get(trophy.trophy_type.lower(), '')

        trophy_description = trophy.trophy_detail if trophy.trophy_detail else '[Trophy description from PSN]'

        return f"""{emoji} **{trophy.trophy_type.title()} Trophy**

{trophy_description}

## How to Earn

[Detailed instructions on how to earn [trophy:{trophy.trophy_id}]]

## Important Notes

**Missable:** [Yes/No]

**Difficulty:** [Easy/Medium/Hard]

**Time Required:** [Time estimate]

**Location/Requirements:** [Where to earn this trophy or what's required]

## Tips & Strategies

[Helpful tips, strategies, or things to watch out for when earning this trophy]
"""
