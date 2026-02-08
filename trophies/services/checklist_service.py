"""
Checklist system service layer.

Handles all business logic for checklists, sections, items, votes, progress, and reports.
Follows the CommentService pattern for consistency.
"""
import html
import logging
import bleach
from django.db import transaction
from django.db.models import F
from django.core.cache import cache
from django.utils import timezone

logger = logging.getLogger('psn_api')


class ChecklistService:
    """Handles checklist operations including CRUD, voting, progress tracking, and moderation."""

    CACHE_TIMEOUT = 300  # 5 minutes
    MAX_TITLE_LENGTH = 200
    MAX_DESCRIPTION_LENGTH = 2000
    MAX_SECTION_SUBTITLE_LENGTH = 200
    MAX_SECTION_DESCRIPTION_LENGTH = 1000
    MAX_ITEM_TEXT_LENGTH = 2000  # Increased from 500 for text_area support

    # Image size limits (bytes)
    MAX_CHECKLIST_IMAGE_SIZE = 5 * 1024 * 1024  # 5MB
    MAX_SECTION_IMAGE_SIZE = 5 * 1024 * 1024     # 5MB
    MAX_ITEM_IMAGE_SIZE = 5 * 1024 * 1024        # 5MB

    # Image format whitelist
    ALLOWED_IMAGE_FORMATS = ['JPEG', 'PNG', 'WEBP', 'GIF']

    # Image dimension limits
    MAX_IMAGE_DIMENSION = 3840  # pixels (4K resolution)

    # Markdown configuration
    MARKDOWN_EXTRAS = [
        'strike',           # ~~strikethrough~~
        'fenced-code-blocks',  # ```code blocks```
        'cuddled-lists',    # Lists without blank lines between items
        'break-on-newline', # Single newline creates <br>
    ]

    ALLOWED_HTML_TAGS = [
        'p', 'br', 'strong', 'em', 'u', 'del', 's',
        'ul', 'ol', 'li', 'blockquote', 'code', 'pre',
        'a',  # Links
    ]

    ALLOWED_HTML_ATTRS = {
        '*': ['class'],
        'a': ['href', 'title', 'rel', 'target', 'class'],  # Link attributes with class
    }

    # ---------- Permission Checks ----------

    @staticmethod
    def can_create_checklist(profile):
        """
        Check if profile can create checklists.

        Args:
            profile: Profile instance

        Returns:
            tuple: (bool can_create, str reason or None)
        """
        if not profile:
            return False, "You must be logged in to create checklists."
        if not profile.is_linked:
            return False, "You must link a PSN profile to create checklists."
        if not profile.guidelines_agreed:
            return False, "You must agree to the community guidelines."
        return True, None

    @staticmethod
    def can_edit_checklist(checklist, profile):
        """
        Check if profile can edit a checklist (metadata only: title, description).

        Args:
            checklist: Checklist instance
            profile: Profile instance

        Returns:
            tuple: (bool can_edit, str reason or None)
        """
        if not profile:
            return False, "You must be logged in."
        if checklist.profile != profile:
            return False, "You can only edit your own checklists."
        if checklist.is_deleted:
            return False, "Cannot edit a deleted checklist."
        return True, None

    @staticmethod
    def can_edit_checklist_structure(checklist, profile):
        """
        Check if profile can make structural edits to a checklist.
        Structural edits include adding/editing/deleting sections and items.

        Published checklists cannot have structural changes because users
        may be tracking progress, and changes would corrupt their progress data.

        Args:
            checklist: Checklist instance
            profile: Profile instance

        Returns:
            tuple: (bool can_edit, str reason or None)
        """
        # First check basic edit permission
        can_edit, reason = ChecklistService.can_edit_checklist(checklist, profile)
        if not can_edit:
            return False, reason

        # Published checklists cannot have structural changes
        if checklist.is_published:
            return False, "Cannot modify sections or items on a published checklist. Users may be tracking progress."

        return True, None

    @staticmethod
    def can_save_progress(checklist, profile):
        """
        Check if profile can save progress on a checklist.

        Premium users: Can save progress on ANY checklist
        Non-premium users: Can only save progress on their OWN checklists

        Args:
            checklist: Checklist instance
            profile: Profile instance

        Returns:
            tuple: (bool can_save, str reason or None)
        """
        if not profile:
            return False, "You must be logged in to save progress."
        if not profile.is_linked:
            return False, "You must link a PSN profile."

        # Authors can always save progress on their own checklists
        if checklist.profile == profile:
            return True, None

        # Premium users can save progress on any checklist
        if profile.user_is_premium:
            return True, None

        return False, "Premium subscription required to track progress on other users' checklists."

    @staticmethod
    def can_edit_checklist_images(checklist, profile):
        """
        Check if profile can edit checklist images (thumbnails).
        Images can be edited even on published checklists.

        Args:
            checklist: Checklist instance
            profile: Profile instance

        Returns:
            tuple: (can_edit: bool, reason: str or None)
        """
        if not profile:
            return False, "You must be logged in."
        if checklist.profile != profile:
            return False, "You can only edit your own checklists."
        if checklist.is_deleted:
            return False, "Cannot edit a deleted checklist."
        return True, None

    @staticmethod
    def can_add_inline_image(checklist, profile):
        """
        Check if user can add inline image items.
        Requires: structure editing permission.

        Args:
            checklist: Checklist instance
            profile: Profile instance

        Returns:
            tuple: (can_add: bool, reason: str or None)
        """
        # Check structure editing permission (draft checklist, author)
        can_edit, reason = ChecklistService.can_edit_checklist_structure(checklist, profile)
        if not can_edit:
            return False, reason

        return True, None

    # ---------- Text Sanitization ----------

    @staticmethod
    def _sanitize_text(text):
        """
        Sanitize user input to prevent XSS while preserving special characters.

        Uses bleach to strip HTML tags, then html.unescape to restore
        special characters like & that bleach encodes as HTML entities.

        Args:
            text: Raw user input

        Returns:
            str: Sanitized text with HTML stripped but special chars preserved
        """
        if not text:
            return ""
        # First strip all HTML tags
        cleaned = bleach.clean(text, tags=[], attributes={}, strip=True)
        # Then unescape HTML entities (& -> &, etc.) since we're storing plain text
        return html.unescape(cleaned).strip()

    @staticmethod
    def process_markdown(text):
        """
        Process markdown text to HTML with sanitization.

        Args:
            text: Raw markdown string

        Returns:
            str: Sanitized HTML string
        """
        if not text or not text.strip():
            return ''

        try:
            import markdown2
            import re

            # Pre-process: Convert __text__ to <u>text</u> for underline
            # We need to do this before markdown processing to avoid conflict with __bold__
            # Match __ at word boundaries, allowing spaces and multiple words
            text = re.sub(r'(?<![_\w])__([^_\n]+?)__(?![_\w])', r'<u>\1</u>', text)

            # Process markdown
            html_output = markdown2.markdown(
                text,
                extras=ChecklistService.MARKDOWN_EXTRAS
            )

            # Sanitize HTML to prevent XSS and restrict link protocols
            clean_html = bleach.clean(
                html_output,
                tags=ChecklistService.ALLOWED_HTML_TAGS,
                attributes=ChecklistService.ALLOWED_HTML_ATTRS,
                protocols=['http', 'https'],  # Only allow http/https links
                strip=True
            )

            # Add security attributes and styling to links (open in new tab, noopener, classes)
            clean_html = re.sub(
                r'<a\s+([^>]*?)href="([^"]*)"([^>]*?)>',
                r'<a \1href="\2"\3 class="link link-primary" target="_blank" rel="noopener noreferrer">',
                clean_html
            )

            # Add styling to blockquotes
            clean_html = re.sub(
                r'<blockquote>',
                r'<blockquote class="border-l-4 border-base-300 pl-4 py-2 my-2 italic text-base-content/80 bg-base-200/30">',
                clean_html
            )

            # Add styling to unordered lists
            clean_html = re.sub(
                r'<ul>',
                r'<ul class="list-disc list-inside ml-4 my-2 space-y-1">',
                clean_html
            )

            # Add styling to ordered lists
            clean_html = re.sub(
                r'<ol>',
                r'<ol class="list-decimal list-inside ml-4 my-2 space-y-1">',
                clean_html
            )

            # Add spacing to paragraphs for visual separation
            clean_html = re.sub(
                r'<p>',
                r'<p class="my-2">',
                clean_html
            )

            return clean_html
        except ImportError:
            logger.error("markdown2 library not installed")
            # Fallback: return escaped text wrapped in paragraph
            return f"<p>{html.escape(text)}</p>"
        except Exception as e:
            logger.error(f"Markdown processing error: {e}")
            # Fallback: return escaped text wrapped in paragraph
            return f"<p>{html.escape(text)}</p>"

    @staticmethod
    def _check_banned_words(text):
        """
        Check for banned words (reuses CommentService logic).

        Args:
            text: Text to check

        Returns:
            tuple: (contains_banned: bool, matched_word: str or None)
        """
        from trophies.services.comment_service import CommentService
        return CommentService.check_banned_words(text)

    # ---------- Image Validation & Upload ----------

    @staticmethod
    def validate_image(image_file, max_size, image_type='image'):
        """
        Validate uploaded image file.

        Args:
            image_file: UploadedFile instance
            max_size: Max file size in bytes
            image_type: Description for error messages

        Returns:
            tuple: (is_valid: bool, error_message: str or None)
        """
        from PIL import Image

        # Check file size
        if image_file.size > max_size:
            max_mb = max_size / (1024 * 1024)
            return False, f"{image_type} must be under {max_mb:.1f}MB."

        # Validate format
        try:
            img = Image.open(image_file)
            if img.format not in ChecklistService.ALLOWED_IMAGE_FORMATS:
                return False, f"Invalid format. Allowed: {', '.join(ChecklistService.ALLOWED_IMAGE_FORMATS)}"

            # Check dimensions
            if max(img.size) > ChecklistService.MAX_IMAGE_DIMENSION:
                return False, f"Image too large. Max {ChecklistService.MAX_IMAGE_DIMENSION}px per side."

            # Reset file pointer
            image_file.seek(0)
            return True, None

        except Exception as e:
            logger.error(f"Image validation error: {e}")
            return False, "Invalid or corrupted image file."

    @staticmethod
    @transaction.atomic
    def update_checklist_thumbnail(checklist, profile, thumbnail):
        """Update checklist thumbnail image."""
        can_edit, reason = ChecklistService.can_edit_checklist_images(checklist, profile)
        if not can_edit:
            return False, reason

        is_valid, error = ChecklistService.validate_image(
            thumbnail,
            ChecklistService.MAX_CHECKLIST_IMAGE_SIZE,
            'Checklist thumbnail'
        )
        if not is_valid:
            return False, error

        # Delete old thumbnail if exists
        if checklist.thumbnail:
            checklist.thumbnail.delete(save=False)

        # Optimize and save
        from trophies.image_utils import optimize_image
        optimized = optimize_image(thumbnail, max_width=1200, max_height=1200)

        checklist.thumbnail = optimized
        checklist.save(update_fields=['thumbnail', 'updated_at'])
        logger.info(f"Checklist {checklist.id} thumbnail updated by {profile.psn_username}")
        return True, None

    @staticmethod
    @transaction.atomic
    def update_section_thumbnail(section, profile, thumbnail):
        """Update section thumbnail image."""
        checklist = section.checklist
        can_edit, reason = ChecklistService.can_edit_checklist_images(checklist, profile)
        if not can_edit:
            return False, reason

        is_valid, error = ChecklistService.validate_image(
            thumbnail,
            ChecklistService.MAX_SECTION_IMAGE_SIZE,
            'Section thumbnail'
        )
        if not is_valid:
            return False, error

        if section.thumbnail:
            section.thumbnail.delete(save=False)

        from trophies.image_utils import optimize_image
        optimized = optimize_image(thumbnail, max_width=600, max_height=600)

        section.thumbnail = optimized
        section.save(update_fields=['thumbnail', 'updated_at'])
        logger.info(f"Section {section.id} thumbnail updated")
        return True, None

    @staticmethod
    @transaction.atomic
    def remove_checklist_thumbnail(checklist, profile):
        """Remove checklist thumbnail."""
        can_edit, reason = ChecklistService.can_edit_checklist_images(checklist, profile)
        if not can_edit:
            return False, reason

        if checklist.thumbnail:
            checklist.thumbnail.delete()
            checklist.save(update_fields=['thumbnail', 'updated_at'])
            logger.info(f"Checklist {checklist.id} thumbnail removed")

        return True, None

    @staticmethod
    @transaction.atomic
    def remove_section_thumbnail(section, profile):
        """Remove section thumbnail."""
        checklist = section.checklist
        can_edit, reason = ChecklistService.can_edit_checklist_images(checklist, profile)
        if not can_edit:
            return False, reason

        if section.thumbnail:
            section.thumbnail.delete()
            section.save(update_fields=['thumbnail', 'updated_at'])
            logger.info(f"Section {section.id} thumbnail removed")

        return True, None

    # ---------- CRUD Operations ----------

    @staticmethod
    @transaction.atomic
    def create_checklist(profile, concept, title, description=""):
        """
        Create a new checklist (as draft).

        Args:
            profile: Profile instance (author)
            concept: Concept instance
            title: Checklist title
            description: Optional description

        Returns:
            tuple: (Checklist instance or None, error_message or None)
        """
        from trophies.models import Checklist

        can_create, reason = ChecklistService.can_create_checklist(profile)
        if not can_create:
            return None, reason

        if not concept:
            return None, "Cannot create checklist without a concept."

        # Sanitize inputs
        title = ChecklistService._sanitize_text(title)
        description = ChecklistService._sanitize_text(description)

        if not title or len(title) == 0:
            return None, "Title cannot be empty."
        if len(title) > ChecklistService.MAX_TITLE_LENGTH:
            return None, f"Title must be under {ChecklistService.MAX_TITLE_LENGTH} characters."
        if len(description) > ChecklistService.MAX_DESCRIPTION_LENGTH:
            return None, f"Description must be under {ChecklistService.MAX_DESCRIPTION_LENGTH} characters."

        # Check for banned words
        contains_banned, _ = ChecklistService._check_banned_words(title + " " + description)
        if contains_banned:
            return None, "Your content contains inappropriate language."

        try:
            checklist = Checklist.objects.create(
                concept=concept,
                profile=profile,
                title=title,
                description=description,
                status='draft',
                view_count=0
            )
            logger.info(f"Checklist {checklist.id} created by {profile.psn_username}")
            return checklist, None
        except Exception as e:
            logger.error(f"Error creating checklist: {e}")
            return None, "An error occurred creating your checklist."

    @staticmethod
    @transaction.atomic
    def update_checklist(checklist, profile, title=None, description=None):
        """
        Update checklist title and/or description.

        Args:
            checklist: Checklist instance
            profile: Profile making the update
            title: New title (or None to keep current)
            description: New description (or None to keep current)

        Returns:
            tuple: (success bool, error_message or None)
        """
        can_edit, reason = ChecklistService.can_edit_checklist(checklist, profile)
        if not can_edit:
            return False, reason

        update_fields = ['updated_at']

        if title is not None:
            title = ChecklistService._sanitize_text(title)
            if not title:
                return False, "Title cannot be empty."
            if len(title) > ChecklistService.MAX_TITLE_LENGTH:
                return False, f"Title must be under {ChecklistService.MAX_TITLE_LENGTH} characters."
            checklist.title = title
            update_fields.append('title')

        if description is not None:
            description = ChecklistService._sanitize_text(description)
            if len(description) > ChecklistService.MAX_DESCRIPTION_LENGTH:
                return False, f"Description must be under {ChecklistService.MAX_DESCRIPTION_LENGTH} characters."
            checklist.description = description
            update_fields.append('description')

        # Check for banned words in updated content
        text_to_check = (title or checklist.title) + " " + (description or checklist.description)
        contains_banned, _ = ChecklistService._check_banned_words(text_to_check)
        if contains_banned:
            return False, "Your content contains inappropriate language."

        checklist.save(update_fields=update_fields)
        ChecklistService._invalidate_cache(checklist.concept)
        logger.info(f"Checklist {checklist.id} updated by {profile.psn_username}")
        return True, None

    @staticmethod
    @transaction.atomic
    def publish_checklist(checklist, profile):
        """
        Publish a draft checklist.

        Requirements:
        - Must have at least one section
        - Each section must have at least one item

        Args:
            checklist: Checklist instance
            profile: Profile publishing

        Returns:
            tuple: (success bool, error_message or None)
        """
        can_edit, reason = ChecklistService.can_edit_checklist(checklist, profile)
        if not can_edit:
            return False, reason

        if checklist.status != 'draft':
            return False, "Checklist is already published."

        # Validate structure
        sections = checklist.sections.all()
        if sections.count() == 0:
            return False, "Checklist must have at least one section before publishing."

        for section in sections:
            if section.items.count() == 0:
                return False, f"Section '{section.subtitle}' must have at least one item."

        checklist.status = 'published'
        checklist.published_at = timezone.now()
        checklist.save(update_fields=['status', 'published_at', 'updated_at'])

        ChecklistService._invalidate_cache(checklist.concept)
        logger.info(f"Checklist {checklist.id} published by {profile.psn_username}")
        return True, None

    @staticmethod
    def get_tracker_count(checklist):
        """
        Get the number of users (excluding the author) tracking progress on a checklist.

        Args:
            checklist: Checklist instance

        Returns:
            int: Number of non-author users with progress records
        """
        from trophies.models import UserChecklistProgress
        return UserChecklistProgress.objects.filter(
            checklist=checklist
        ).exclude(
            profile=checklist.profile
        ).count()

    @staticmethod
    @transaction.atomic
    def unpublish_checklist(checklist, profile):
        """
        Revert a published checklist to draft (author action).

        Args:
            checklist: Checklist instance
            profile: Profile unpublishing

        Returns:
            tuple: (success bool, error_message or None)
        """
        can_edit, reason = ChecklistService.can_edit_checklist(checklist, profile)
        if not can_edit:
            return False, reason

        if checklist.status != 'published':
            return False, "Checklist is not published."

        checklist.status = 'draft'
        checklist.save(update_fields=['status', 'updated_at'])

        ChecklistService._invalidate_cache(checklist.concept)
        logger.info(f"Checklist {checklist.id} unpublished by {profile.psn_username}")
        return True, None

    @staticmethod
    @transaction.atomic
    def delete_checklist(checklist, profile, is_admin=False):
        """
        Soft delete a checklist.

        Args:
            checklist: Checklist instance
            profile: Profile requesting deletion
            is_admin: Whether this is an admin action

        Returns:
            tuple: (success bool, error_message or None)
        """
        if not is_admin and checklist.profile != profile:
            return False, "You can only delete your own checklists."

        if checklist.is_deleted:
            return False, "Checklist is already deleted."

        checklist.soft_delete()
        ChecklistService._invalidate_cache(checklist.concept)

        action = "admin deleted" if is_admin else "deleted"
        logger.info(f"Checklist {checklist.id} {action} by {profile.psn_username}")
        return True, None

    # ---------- Section Operations ----------

    @staticmethod
    @transaction.atomic
    def add_section(checklist, profile, subtitle, description="", order=None):
        """
        Add a section to a checklist.

        Args:
            checklist: Checklist instance
            profile: Profile adding section
            subtitle: Section subtitle/header
            description: Optional section description
            order: Display order (defaults to end)

        Returns:
            tuple: (ChecklistSection instance or None, error_message or None)
        """
        from trophies.models import ChecklistSection

        can_edit, reason = ChecklistService.can_edit_checklist_structure(checklist, profile)
        if not can_edit:
            return None, reason

        subtitle = ChecklistService._sanitize_text(subtitle)
        description = ChecklistService._sanitize_text(description)

        if not subtitle:
            return None, "Section subtitle cannot be empty."
        if len(subtitle) > ChecklistService.MAX_SECTION_SUBTITLE_LENGTH:
            return None, f"Section subtitle must be under {ChecklistService.MAX_SECTION_SUBTITLE_LENGTH} characters."
        if len(description) > ChecklistService.MAX_SECTION_DESCRIPTION_LENGTH:
            return None, f"Section description must be under {ChecklistService.MAX_SECTION_DESCRIPTION_LENGTH} characters."

        # Check for banned words
        contains_banned, _ = ChecklistService._check_banned_words(subtitle + " " + description)
        if contains_banned:
            return None, "Your content contains inappropriate language."

        if order is None:
            order = checklist.sections.count()

        section = ChecklistSection.objects.create(
            checklist=checklist,
            subtitle=subtitle,
            description=description,
            order=order
        )
        logger.info(f"Section {section.id} added to checklist {checklist.id}")
        return section, None

    @staticmethod
    @transaction.atomic
    def update_section(section, profile, subtitle=None, description=None, order=None):
        """
        Update a section.

        Args:
            section: ChecklistSection instance
            profile: Profile making update
            subtitle: New subtitle (or None to keep current)
            description: New description (or None to keep current)
            order: New order (or None to keep current)

        Returns:
            tuple: (success bool, error_message or None)
        """
        can_edit, reason = ChecklistService.can_edit_checklist_structure(section.checklist, profile)
        if not can_edit:
            return False, reason

        update_fields = ['updated_at']

        if subtitle is not None:
            subtitle = ChecklistService._sanitize_text(subtitle)
            if not subtitle:
                return False, "Section subtitle cannot be empty."
            if len(subtitle) > ChecklistService.MAX_SECTION_SUBTITLE_LENGTH:
                return False, f"Section subtitle must be under {ChecklistService.MAX_SECTION_SUBTITLE_LENGTH} characters."
            section.subtitle = subtitle
            update_fields.append('subtitle')

        if description is not None:
            description = ChecklistService._sanitize_text(description)
            if len(description) > ChecklistService.MAX_SECTION_DESCRIPTION_LENGTH:
                return False, f"Section description must be under {ChecklistService.MAX_SECTION_DESCRIPTION_LENGTH} characters."
            section.description = description
            update_fields.append('description')

        if order is not None:
            section.order = order
            update_fields.append('order')

        # Check for banned words
        text_to_check = (subtitle or section.subtitle) + " " + (description or section.description)
        contains_banned, _ = ChecklistService._check_banned_words(text_to_check)
        if contains_banned:
            return False, "Your content contains inappropriate language."

        section.save(update_fields=update_fields)
        logger.info(f"Section {section.id} updated")
        return True, None

    @staticmethod
    @transaction.atomic
    def delete_section(section, profile):
        """
        Delete a section and all its items, cleaning up user progress records.

        Args:
            section: ChecklistSection instance
            profile: Profile requesting deletion

        Returns:
            tuple: (success bool, error_message or None)
        """
        can_edit, reason = ChecklistService.can_edit_checklist_structure(section.checklist, profile)
        if not can_edit:
            return False, reason

        section_id = section.id
        checklist = section.checklist

        # Collect all item IDs in this section before deleting
        item_ids = list(section.items.values_list('id', flat=True))

        # Delete the section (cascades to items)
        section.delete()

        # Clean up progress records that reference any of these items
        if item_ids:
            ChecklistService._cleanup_progress_for_deleted_items(checklist, item_ids)

        logger.info(f"Section {section_id} deleted from checklist {checklist.id} (cleaned up {len(item_ids)} item references)")
        return True, None

    @staticmethod
    @transaction.atomic
    def reorder_sections(checklist, profile, section_ids):
        """
        Reorder sections by providing ordered list of section IDs.

        Args:
            checklist: Checklist instance
            profile: Profile making update
            section_ids: List of section IDs in desired order

        Returns:
            tuple: (success bool, error_message or None)
        """
        can_edit, reason = ChecklistService.can_edit_checklist_structure(checklist, profile)
        if not can_edit:
            return False, reason

        sections = checklist.sections.all()
        existing_ids = set(s.id for s in sections)

        if set(section_ids) != existing_ids:
            return False, "Section IDs must match existing sections."

        for order, section_id in enumerate(section_ids):
            sections.filter(id=section_id).update(order=order)

        logger.info(f"Sections reordered for checklist {checklist.id}")
        return True, None

    # ---------- Item Operations ----------

    @staticmethod
    @transaction.atomic
    def add_item(section, profile, text='', item_type='item', trophy_id=None, order=None, image=None):
        """
        Add an item to a section. Supports regular items, sub-headers, images, and text areas.

        Args:
            section: ChecklistSection instance
            profile: Profile adding item
            text: Item text (required for item/sub_header/text_area, optional for image as caption)
            item_type: 'item', 'sub_header', 'image', or 'text_area' (default: 'item')
            trophy_id: Optional trophy ID link
            order: Display order (defaults to end)
            image: UploadedFile for item_type='image'

        Returns:
            tuple: (ChecklistItem instance or None, error_message or None)
        """
        from trophies.models import ChecklistItem

        checklist = section.checklist

        # Check structure editing permission
        can_edit, reason = ChecklistService.can_edit_checklist_structure(checklist, profile)
        if not can_edit:
            return None, reason

        # Validate item_type
        if item_type not in ['item', 'sub_header', 'image', 'text_area']:
            return None, "Invalid item type."

        # Special handling for image items
        if item_type == 'image':
            # Premium check
            can_add, reason = ChecklistService.can_add_inline_image(checklist, profile)
            if not can_add:
                return None, reason

            # Image required
            if not image:
                return None, "Image file required for image items."

            # Validate image
            is_valid, error = ChecklistService.validate_image(
                image,
                ChecklistService.MAX_ITEM_IMAGE_SIZE,
                'Inline image'
            )
            if not is_valid:
                return None, error

            # Optimize image
            from trophies.image_utils import optimize_image
            image = optimize_image(image, max_width=1200, max_height=1200)

            # text is optional for images (acts as caption)
            text = ChecklistService._sanitize_text(text) if text else ''

        # Handle text_area items
        elif item_type == 'text_area':
            text = ChecklistService._sanitize_text(text)
            if not text or not text.strip():
                return None, "Text area content cannot be empty."

            # Validate length
            if len(text) > ChecklistService.MAX_ITEM_TEXT_LENGTH:
                return None, f"Text area content too long (max {ChecklistService.MAX_ITEM_TEXT_LENGTH} characters)."

            # Check for banned words
            contains_banned, _ = ChecklistService._check_banned_words(text)
            if contains_banned:
                return None, "Your content contains inappropriate language."

            # No trophy linking for text areas
            trophy_id = None
            image = None

        else:
            # Regular items and sub-headers require text
            text = ChecklistService._sanitize_text(text)
            if not text:
                return None, "Item text cannot be empty."

            # Validate text length
            if len(text) > ChecklistService.MAX_ITEM_TEXT_LENGTH:
                return None, f"Item text must be under {ChecklistService.MAX_ITEM_TEXT_LENGTH} characters."

            # Check for banned words
            contains_banned, _ = ChecklistService._check_banned_words(text)
            if contains_banned:
                return None, "Your content contains inappropriate language."

            # Clear image for non-image items
            image = None

        # Calculate order
        if order is None:
            order = section.items.count()

        # Create item
        item = ChecklistItem.objects.create(
            section=section,
            text=text,
            item_type=item_type,
            trophy_id=trophy_id if item_type == 'item' else None,
            image=image,
            order=order
        )

        logger.info(f"Item {item.id} (type: {item_type}) added to section {section.id}")
        return item, None

    @staticmethod
    def validate_trophy_for_checklist(checklist, trophy_id, profile):
        """
        Validate that a trophy can be added to the checklist.

        Args:
            checklist: Checklist instance
            trophy_id: Trophy ID to validate
            profile: Profile adding the trophy

        Returns:
            tuple: (is_valid: bool, error_message: str or None)
        """
        from trophies.models import Trophy, ChecklistItem

        if not checklist.selected_game:
            return False, "Checklist must have a selected game before adding trophies."

        try:
            trophy = Trophy.objects.get(id=trophy_id)
        except Trophy.DoesNotExist:
            return False, "Trophy not found."

        if trophy.game != checklist.selected_game:
            return False, "Trophy does not belong to the selected game."

        # Check for duplicates across all sections
        existing = ChecklistItem.objects.filter(
            section__checklist=checklist,
            item_type='trophy',
            trophy_id=trophy_id
        )

        if existing.exists():
            return False, f"Trophy '{trophy.trophy_name}' is already in this checklist."

        return True, None

    @staticmethod
    @transaction.atomic
    def add_trophy_item(section, profile, trophy_id, order=None):
        """
        Add a trophy item to a section.

        Args:
            section: ChecklistSection instance
            profile: Profile adding the item
            trophy_id: Trophy ID to add
            order: Display order (auto-calculated if None)

        Returns:
            tuple: (ChecklistItem instance or None, error message or None)
        """
        from trophies.models import ChecklistItem, Trophy

        checklist = section.checklist

        # Check structure editing permission
        can_edit, reason = ChecklistService.can_edit_checklist_structure(checklist, profile)
        if not can_edit:
            return None, reason

        # Validate trophy
        is_valid, error = ChecklistService.validate_trophy_for_checklist(
            checklist, trophy_id, profile
        )
        if not is_valid:
            return None, error

        # Get trophy details
        trophy = Trophy.objects.get(id=trophy_id)

        if order is None:
            order = section.items.count()

        # Create item with trophy data
        item = ChecklistItem.objects.create(
            section=section,
            text=trophy.trophy_name,
            item_type='trophy',
            trophy_id=trophy_id,
            order=order
        )

        logger.info(f"Trophy item {item.id} (trophy_id: {trophy_id}) added to section {section.id}")
        return item, None

    @staticmethod
    @transaction.atomic
    def set_checklist_game(checklist, game_id, profile):
        """
        Set the selected game for a checklist.

        Args:
            checklist: Checklist instance
            game_id: Game ID to select
            profile: Profile making the change

        Returns:
            tuple: (success: bool, error_message or None)
        """
        from trophies.models import Game, ChecklistItem

        if checklist.profile != profile:
            return False, "You don't have permission to edit this checklist."

        if checklist.is_published:
            return False, "Cannot change game selection for published checklists."

        try:
            game = Game.objects.get(id=game_id)
        except Game.DoesNotExist:
            return False, "Game not found."

        if game.concept != checklist.concept:
            return False, "Game does not belong to this checklist's concept."

        # Check if checklist already has trophy items with different game
        existing_trophies = ChecklistItem.objects.filter(
            section__checklist=checklist,
            item_type='trophy',
            trophy_id__isnull=False
        )

        if existing_trophies.exists() and checklist.selected_game and checklist.selected_game != game:
            return False, "Cannot change game - checklist already has trophy items."

        # Set game
        checklist.selected_game = game
        checklist.save(update_fields=['selected_game', 'updated_at'])

        logger.info(f"Checklist {checklist.id} game set to {game.id}")
        return True, None

    @staticmethod
    def get_available_trophies_for_checklist(checklist):
        """
        Get trophies that can be added to the checklist.

        Returns list of Trophy objects with 'is_used' and 'is_base_game' annotations,
        along with trophy group information.
        """
        from trophies.models import Trophy, ChecklistItem, TrophyGroup
        from django.db.models import Exists, OuterRef, Case, When, Value, BooleanField, F

        if not checklist.selected_game:
            return Trophy.objects.none()

        # Get all trophies for the selected game with group info
        trophies = Trophy.objects.filter(
            game=checklist.selected_game
        ).select_related('game').order_by('trophy_group_id', 'trophy_id')

        # Annotate with usage status
        used_trophy_ids = ChecklistItem.objects.filter(
            section__checklist=checklist,
            item_type='trophy',
            trophy_id=OuterRef('id')
        )

        # Annotate with is_used and is_base_game
        trophies = trophies.annotate(
            is_used=Exists(used_trophy_ids),
            is_base_game=Case(
                When(trophy_group_id='default', then=Value(True)),
                default=Value(False),
                output_field=BooleanField()
            )
        )

        return trophies

    @staticmethod
    @transaction.atomic
    def bulk_add_items(section, profile, items_data):
        """
        Bulk create items in a section with atomic transaction.

        All items are validated first. If any validation fails, no items are created.
        This ensures atomic all-or-nothing behavior.

        Args:
            section: ChecklistSection instance
            profile: Profile creating items
            items_data: List of dicts [{"text": "...", "item_type": "item"}, ...]

        Returns:
            tuple: (list of created ChecklistItem instances or None, error dict or None)

            On success: ([ChecklistItem, ...], None)
            On error: (None, {
                "failed_items": [{"index": 0, "text": "...", "error": "..."}, ...],
                "summary": {"total_submitted": N, "valid": M, "failed": K}
            })
        """
        from trophies.models import ChecklistItem
        from django.db.models import Max

        # Permission check
        can_edit, reason = ChecklistService.can_edit_checklist_structure(section.checklist, profile)
        if not can_edit:
            return None, {
                "error": reason,
                "failed_items": [],
                "summary": {"total_submitted": len(items_data), "valid": 0, "failed": len(items_data)}
            }

        # Pre-validate ALL items before creating any
        validated_items = []
        failed_items = []

        for index, item_data in enumerate(items_data):
            text = item_data.get('text', '')
            item_type = item_data.get('item_type', 'item')

            # Validate item_type
            if item_type not in ['item', 'sub_header']:
                failed_items.append({
                    "index": index,
                    "text": text,
                    "error": "Invalid item type."
                })
                continue

            # Sanitize text
            text = ChecklistService._sanitize_text(text)

            # Empty check
            if not text:
                failed_items.append({
                    "index": index,
                    "text": item_data.get('text', ''),
                    "error": "Item text cannot be empty."
                })
                continue

            # Length check
            if len(text) > ChecklistService.MAX_ITEM_TEXT_LENGTH:
                failed_items.append({
                    "index": index,
                    "text": text,
                    "error": f"Item text must be under {ChecklistService.MAX_ITEM_TEXT_LENGTH} characters."
                })
                continue

            # Banned words check
            contains_banned, _ = ChecklistService._check_banned_words(text)
            if contains_banned:
                failed_items.append({
                    "index": index,
                    "text": text,
                    "error": "Your content contains inappropriate language."
                })
                continue

            # Validation passed
            validated_items.append({
                "text": text,
                "item_type": item_type,
                "index": index
            })

        # If any validation errors, return them (no items created)
        if failed_items:
            return None, {
                "error": f"Validation failed for {len(failed_items)} items",
                "failed_items": failed_items,
                "summary": {
                    "total_submitted": len(items_data),
                    "valid": len(validated_items),
                    "failed": len(failed_items)
                }
            }

        # All items valid - create them with sequential ordering
        # Get max order in section
        max_order_result = section.items.aggregate(Max('order'))
        max_order = max_order_result['order__max']
        base_order = (max_order + 1) if max_order is not None else 0

        created_items = []
        for i, item_data in enumerate(validated_items):
            item = ChecklistItem.objects.create(
                section=section,
                text=item_data['text'],
                item_type=item_data['item_type'],
                trophy_id=None,  # Not supported in bulk upload v1
                order=base_order + i
            )
            created_items.append(item)

        logger.info(f"Bulk created {len(created_items)} items in section {section.id}")
        return created_items, None

    @staticmethod
    @transaction.atomic
    def update_item(item, profile, text=None, item_type=None, trophy_id=None, order=None):
        """
        Update an item.

        Args:
            item: ChecklistItem instance
            profile: Profile making update
            text: New text (or None to keep current)
            item_type: New type (or None to keep current)
            trophy_id: New trophy ID (or None to keep current)
            order: New order (or None to keep current)

        Returns:
            tuple: (success bool, error_message or None)
        """
        can_edit, reason = ChecklistService.can_edit_checklist_structure(item.section.checklist, profile)
        if not can_edit:
            return False, reason

        update_fields = ['updated_at']

        if text is not None:
            text = ChecklistService._sanitize_text(text)
            # Text areas and regular items/sub-headers require text
            if not text and item.item_type != 'image':
                return False, "Item text cannot be empty."
            if len(text) > ChecklistService.MAX_ITEM_TEXT_LENGTH:
                return False, f"Item text must be under {ChecklistService.MAX_ITEM_TEXT_LENGTH} characters."

            # Check for banned words (skip for images since text is optional caption)
            if item.item_type != 'image':
                contains_banned, _ = ChecklistService._check_banned_words(text)
                if contains_banned:
                    return False, "Your content contains inappropriate language."

            item.text = text
            update_fields.append('text')

        if item_type is not None:
            if item_type not in ['item', 'sub_header', 'text_area']:
                return False, "Invalid item type."
            item.item_type = item_type
            update_fields.append('item_type')

            # Clear trophy_id when converting to sub_header or text_area
            if item_type in ['sub_header', 'text_area']:
                item.trophy_id = None
                update_fields.append('trophy_id')

        if trophy_id is not None and item.item_type == 'item':
            item.trophy_id = trophy_id if trophy_id else None
            update_fields.append('trophy_id')

        if order is not None:
            item.order = order
            update_fields.append('order')

        item.save(update_fields=update_fields)
        logger.info(f"Item {item.id} updated (type: {item.item_type})")
        return True, None

    @staticmethod
    @transaction.atomic
    def bulk_update_items(checklist, profile, items_data):
        """
        Bulk update multiple items in a single transaction.

        Args:
            checklist: Checklist instance
            profile: Profile making updates
            items_data: List of dicts with 'id', 'text', 'item_type' keys

        Returns:
            tuple: (updated_count int, error dict or None)
        """
        from trophies.models import ChecklistItem

        can_edit, reason = ChecklistService.can_edit_checklist_structure(checklist, profile)
        if not can_edit:
            return 0, {'error': reason}

        if not items_data:
            return 0, None

        # Get all item IDs that belong to this checklist
        valid_item_ids = set(
            ChecklistItem.objects.filter(
                section__checklist=checklist
            ).values_list('id', flat=True)
        )

        # Validate all items first
        errors = []
        validated_items = []

        for idx, item_data in enumerate(items_data):
            item_id = item_data.get('id')
            text = item_data.get('text', '').strip() if item_data.get('text') else ''
            item_type = item_data.get('item_type')

            # Validate item belongs to this checklist
            if item_id not in valid_item_ids:
                errors.append({'index': idx, 'id': item_id, 'error': 'Item not found in this checklist'})
                continue

            # Sanitize text
            if text:
                text = ChecklistService._sanitize_text(text)

            # Validate text length
            if text and len(text) > ChecklistService.MAX_ITEM_TEXT_LENGTH:
                errors.append({'index': idx, 'id': item_id, 'error': f'Text exceeds {ChecklistService.MAX_ITEM_TEXT_LENGTH} characters'})
                continue

            # Check for banned words (skip empty text which might be image captions)
            if text:
                contains_banned, _ = ChecklistService._check_banned_words(text)
                if contains_banned:
                    errors.append({'index': idx, 'id': item_id, 'error': 'Content contains inappropriate language'})
                    continue

            # Validate item type (image items can have captions updated)
            if item_type and item_type not in ['item', 'sub_header', 'text_area', 'image']:
                errors.append({'index': idx, 'id': item_id, 'error': 'Invalid item type'})
                continue

            validated_items.append({
                'id': item_id,
                'text': text,
                'item_type': item_type
            })

        if errors:
            return 0, {
                'error': f'Validation failed for {len(errors)} items',
                'failed_items': errors,
                'summary': {
                    'total_submitted': len(items_data),
                    'valid': len(validated_items),
                    'failed': len(errors)
                }
            }

        # Fetch all items to update
        items_to_update = ChecklistItem.objects.filter(
            id__in=[v['id'] for v in validated_items]
        )
        items_by_id = {item.id: item for item in items_to_update}

        # Apply updates to item instances
        updated_items = []

        for item_data in validated_items:
            item = items_by_id.get(item_data['id'])
            if not item:
                continue

            changed = False

            # Handle text update
            if item_data['text'] is not None:
                # For non-image items, text is required
                if not item_data['text'] and item.item_type not in ['image']:
                    continue  # Skip items that would end up with empty text
                if item.text != item_data['text']:
                    item.text = item_data['text']
                    changed = True

            # Handle item_type update
            if item_data['item_type'] and item.item_type != item_data['item_type']:
                item.item_type = item_data['item_type']
                # Clear trophy_id when converting to sub_header or text_area
                if item_data['item_type'] in ['sub_header', 'text_area']:
                    item.trophy_id = None
                changed = True

            if changed:
                updated_items.append(item)

        # Perform bulk update
        if updated_items:
            ChecklistItem.objects.bulk_update(
                updated_items,
                fields=['text', 'item_type', 'trophy_id']
            )
            logger.info(f"Bulk updated {len(updated_items)} items in checklist {checklist.id}")

        return len(updated_items), None

    @staticmethod
    @transaction.atomic
    def delete_item(item, profile):
        """
        Delete an item and clean up any user progress records that reference it.

        Args:
            item: ChecklistItem instance
            profile: Profile requesting deletion

        Returns:
            tuple: (success bool, error_message or None)
        """
        can_edit, reason = ChecklistService.can_edit_checklist_structure(item.section.checklist, profile)
        if not can_edit:
            return False, reason

        item_id = item.id
        checklist = item.section.checklist

        # Delete the item
        item.delete()

        # Clean up progress records that reference this item
        ChecklistService._cleanup_progress_for_deleted_items(checklist, [item_id])

        logger.info(f"Item {item_id} deleted from checklist {checklist.id}")
        return True, None

    @staticmethod
    def _cleanup_progress_for_deleted_items(checklist, deleted_item_ids):
        """
        Remove deleted item IDs from all user progress records for a checklist.

        This ensures that when items are deleted, users' progress records
        are updated to remove references to those items and recalculate percentages.

        Args:
            checklist: Checklist instance
            deleted_item_ids: List of item IDs that were deleted
        """
        from trophies.models import UserChecklistProgress

        if not deleted_item_ids:
            return

        deleted_ids_set = set(deleted_item_ids)

        # Get all progress records for this checklist
        progress_records = UserChecklistProgress.objects.filter(checklist=checklist)

        updated_count = 0
        for progress in progress_records:
            # Filter out deleted item IDs from completed_items
            original_items = set(progress.completed_items)
            cleaned_items = [item_id for item_id in progress.completed_items if item_id not in deleted_ids_set]

            # Only update if items were actually removed
            if len(cleaned_items) != len(progress.completed_items):
                progress.completed_items = cleaned_items
                progress.update_progress()
                updated_count += 1

        if updated_count > 0:
            logger.info(f"Cleaned up {updated_count} progress records after deleting items {deleted_item_ids} from checklist {checklist.id}")

    @staticmethod
    @transaction.atomic
    def reorder_items(section, profile, item_ids):
        """
        Reorder items by providing ordered list of item IDs.

        Args:
            section: ChecklistSection instance
            profile: Profile making update
            item_ids: List of item IDs in desired order

        Returns:
            tuple: (success bool, error_message or None)
        """
        can_edit, reason = ChecklistService.can_edit_checklist_structure(section.checklist, profile)
        if not can_edit:
            return False, reason

        items = section.items.all()
        existing_ids = set(i.id for i in items)

        if set(item_ids) != existing_ids:
            return False, "Item IDs must match existing items."

        for order, item_id in enumerate(item_ids):
            items.filter(id=item_id).update(order=order)

        logger.info(f"Items reordered for section {section.id}")
        return True, None

    # ---------- Voting ----------

    @staticmethod
    @transaction.atomic
    def toggle_vote(checklist, profile):
        """
        Toggle upvote on a checklist.

        Args:
            checklist: Checklist instance
            profile: Profile voting

        Returns:
            tuple: (voted bool or None, error_message or None)
            voted is True if now voted, False if vote removed, None on error
        """
        from trophies.models import ChecklistVote

        if not profile or not profile.is_linked:
            return None, "You must be logged in with a linked profile to vote."

        if checklist.is_deleted:
            return None, "Cannot vote on deleted checklists."

        if checklist.status != 'published':
            return None, "Cannot vote on draft checklists."

        if checklist.profile == profile:
            return None, "Cannot vote on your own checklist."

        existing_vote = ChecklistVote.objects.filter(
            checklist=checklist,
            profile=profile
        ).first()

        if existing_vote:
            existing_vote.delete()
            checklist.upvote_count = F('upvote_count') - 1
            checklist.save(update_fields=['upvote_count'])
            checklist.refresh_from_db(fields=['upvote_count'])
            logger.info(f"Vote removed from checklist {checklist.id} by {profile.psn_username}")
            return False, None
        else:
            ChecklistVote.objects.create(checklist=checklist, profile=profile)
            checklist.upvote_count = F('upvote_count') + 1
            checklist.save(update_fields=['upvote_count'])
            checklist.refresh_from_db(fields=['upvote_count'])
            logger.info(f"Vote added to checklist {checklist.id} by {profile.psn_username}")

            # Check for checklist upvote milestones for the checklist author
            from trophies.services.milestone_service import check_all_milestones_for_user
            check_all_milestones_for_user(checklist.profile, criteria_type='checklist_upvotes')

            return True, None

    # ---------- Progress Tracking ----------

    @staticmethod
    @transaction.atomic
    def toggle_item_progress(checklist, profile, item_id):
        """
        Toggle an item's completion status for a user.

        Sub-headers cannot be marked as complete.
        Premium check enforced here.

        Args:
            checklist: Checklist instance
            profile: Profile tracking progress
            item_id: ID of ChecklistItem to toggle

        Returns:
            tuple: (completed bool or None, error_message or None)
            completed is True if now complete, False if now incomplete, None on error
        """
        from trophies.models import UserChecklistProgress, ChecklistItem

        can_save, reason = ChecklistService.can_save_progress(checklist, profile)
        if not can_save:
            return None, reason

        if checklist.is_deleted:
            return None, "Cannot track progress on deleted checklists."

        # Ensure item_id is an integer for consistent comparison in JSONField
        item_id = int(item_id)

        # Verify item belongs to this checklist
        try:
            item = ChecklistItem.objects.get(id=item_id, section__checklist=checklist)
        except ChecklistItem.DoesNotExist:
            return None, "Item not found."

        # Sub-headers cannot be marked as complete
        if item.item_type == 'sub_header':
            return None, "Sub-headers cannot be marked as complete."

        # Get or create progress record
        progress, created = UserChecklistProgress.objects.get_or_create(
            profile=profile,
            checklist=checklist,
            defaults={'total_items': checklist.total_items}
        )

        if created:
            # Update checklist's progress_save_count
            checklist.progress_save_count = F('progress_save_count') + 1
            checklist.save(update_fields=['progress_save_count'])

        # Toggle item
        if item_id in progress.completed_items:
            progress.mark_item_incomplete(item_id)
            logger.info(f"Item {item_id} unmarked by {profile.psn_username}")
            return False, None
        else:
            progress.mark_item_complete(item_id)
            logger.info(f"Item {item_id} marked complete by {profile.psn_username}")
            return True, None

    @staticmethod
    @transaction.atomic
    def bulk_update_section_progress(checklist, profile, section_id, mark_complete):
        """
        Bulk update all items in a section to be complete or incomplete.

        Args:
            checklist: Checklist instance
            profile: Profile tracking progress
            section_id: ID of ChecklistSection to update
            mark_complete: Boolean - True to check all, False to uncheck all

        Returns:
            tuple: (updated_count int, error_message or None)
        """
        from trophies.models import UserChecklistProgress, ChecklistItem, ChecklistSection

        can_save, reason = ChecklistService.can_save_progress(checklist, profile)
        if not can_save:
            return 0, reason

        if checklist.is_deleted:
            return 0, "Cannot track progress on deleted checklists."

        # Verify section belongs to this checklist
        try:
            section = ChecklistSection.objects.get(id=section_id, checklist=checklist)
        except ChecklistSection.DoesNotExist:
            return 0, "Section not found."

        # Get all checkable items in the section (exclude sub_headers, images, text_areas)
        checkable_items = ChecklistItem.objects.filter(
            section=section,
            item_type__in=['item', 'trophy']
        ).values_list('id', flat=True)

        if not checkable_items:
            return 0, "No checkable items in section."

        # Get or create progress record
        progress, created = UserChecklistProgress.objects.get_or_create(
            profile=profile,
            checklist=checklist,
            defaults={'total_items': checklist.total_items}
        )

        if created:
            # Update checklist's progress_save_count
            checklist.progress_save_count = F('progress_save_count') + 1
            checklist.save(update_fields=['progress_save_count'])

        updated_count = 0
        for item_id in checkable_items:
            if mark_complete:
                if item_id not in progress.completed_items:
                    progress.mark_item_complete(item_id)
                    updated_count += 1
            else:
                if item_id in progress.completed_items:
                    progress.mark_item_incomplete(item_id)
                    updated_count += 1

        logger.info(f"Bulk update: {updated_count} items in section {section_id} {'checked' if mark_complete else 'unchecked'} by {profile.psn_username}")
        return updated_count, None

    @staticmethod
    def get_user_progress(checklist, profile):
        """
        Get user's progress on a checklist.

        Args:
            checklist: Checklist instance
            profile: Profile to get progress for

        Returns:
            UserChecklistProgress instance or None
        """
        from trophies.models import UserChecklistProgress

        if not profile:
            return None

        try:
            return UserChecklistProgress.objects.get(
                checklist=checklist,
                profile=profile
            )
        except UserChecklistProgress.DoesNotExist:
            return None

    @staticmethod
    def get_user_checklists_in_progress(profile, limit=10):
        """
        Get checklists a user has started but not completed.

        Args:
            profile: Profile instance
            limit: Maximum number to return

        Returns:
            QuerySet of UserChecklistProgress
        """
        from trophies.models import UserChecklistProgress

        return UserChecklistProgress.objects.filter(
            profile=profile,
            progress_percentage__gt=0,
            progress_percentage__lt=100,
            checklist__is_deleted=False
        ).select_related('checklist', 'checklist__concept', 'checklist__profile').order_by('-last_activity')[:limit]

    # ---------- Reporting ----------

    @staticmethod
    @transaction.atomic
    def report_checklist(checklist, reporter, reason, details=''):
        """
        Submit a report for a checklist.

        Args:
            checklist: Checklist instance
            reporter: Profile submitting report
            reason: Report reason code
            details: Additional details

        Returns:
            tuple: (ChecklistReport or None, error_message or None)
        """
        from trophies.models import ChecklistReport

        if checklist.is_deleted:
            return None, "Cannot report deleted checklists."

        if checklist.profile == reporter:
            return None, "Cannot report your own checklist."

        existing = ChecklistReport.objects.filter(
            checklist=checklist,
            reporter=reporter
        ).first()
        if existing:
            return None, "You have already reported this checklist."

        valid_reasons = [r[0] for r in ChecklistReport.REPORT_REASONS]
        if reason not in valid_reasons:
            return None, "Invalid report reason."

        report = ChecklistReport.objects.create(
            checklist=checklist,
            reporter=reporter,
            reason=reason,
            details=details[:500] if details else ''
        )

        logger.info(f"Checklist {checklist.id} reported by {reporter.psn_username}: {reason}")
        return report, None

    # ---------- Query Helpers ----------

    @staticmethod
    def get_checklists_for_concept(concept, profile=None, sort='top'):
        """
        Get published checklists for a concept.

        Args:
            concept: Concept instance
            profile: Optional viewing profile (for vote/progress status)
            sort: 'top', 'new', or 'popular'

        Returns:
            QuerySet of Checklists
        """
        from trophies.models import Checklist
        return Checklist.objects.get_checklists_for_concept(concept, profile, sort)

    @staticmethod
    def get_user_drafts(profile):
        """
        Get a user's draft checklists.

        Args:
            profile: Profile instance

        Returns:
            QuerySet of Checklists
        """
        from trophies.models import Checklist
        return Checklist.objects.drafts().by_author(profile).with_author_data().order_by('-updated_at')

    @staticmethod
    def get_user_published(profile):
        """
        Get a user's published checklists.

        Args:
            profile: Profile instance

        Returns:
            QuerySet of Checklists
        """
        from trophies.models import Checklist
        return Checklist.objects.published().by_author(profile).with_author_data().order_by('-published_at')

    @staticmethod
    def get_preview_data(checklist, profile):
        """
        Get full checklist data for preview.

        Only accessible by author for drafts.

        Args:
            checklist: Checklist instance
            profile: Profile requesting preview

        Returns:
            tuple: (dict preview_data or None, error_message or None)
        """
        if checklist.status == 'draft' and checklist.profile != profile:
            return None, "Cannot preview another user's draft."

        sections = checklist.sections.prefetch_related('items').order_by('order')

        preview_data = {
            'id': checklist.id,
            'title': checklist.title,
            'description': checklist.description,
            'status': checklist.status,
            'author': {
                'id': checklist.profile.id,
                'username': checklist.profile.display_psn_username or checklist.profile.psn_username,
                'avatar_url': checklist.profile.avatar_url,
            },
            'upvote_count': checklist.upvote_count,
            'progress_save_count': checklist.progress_save_count,
            'created_at': checklist.created_at.isoformat(),
            'published_at': checklist.published_at.isoformat() if checklist.published_at else None,
            'sections': []
        }

        total_items = 0
        for section in sections:
            section_data = {
                'id': section.id,
                'subtitle': section.subtitle,
                'description': section.description,
                'order': section.order,
                'items': []
            }
            for item in section.items.order_by('order'):
                section_data['items'].append({
                    'id': item.id,
                    'text': item.text,
                    'trophy_id': item.trophy_id,
                    'order': item.order
                })
                total_items += 1
            preview_data['sections'].append(section_data)

        preview_data['total_items'] = total_items

        return preview_data, None

    # ---------- Cache Management ----------

    @staticmethod
    def _invalidate_cache(concept):
        """Invalidate cached checklists for a concept."""
        cache_key = f"checklists:concept:{concept.id}"
        cache.delete(cache_key)
