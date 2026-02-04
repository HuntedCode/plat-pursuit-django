"""
Validators for notification content, particularly structured sections.
"""
import re


class SectionValidator:
    """Validator for structured notification sections."""

    MAX_SECTIONS = 5
    MAX_HEADER_LENGTH = 100
    MAX_ICON_LENGTH = 10
    MAX_CONTENT_LENGTH = 800
    REQUIRED_FIELDS = {'id', 'header', 'icon', 'content', 'order'}

    @staticmethod
    def validate_sections(sections):
        """
        Validate entire sections array.

        Args:
            sections: List of section dictionaries

        Returns:
            Tuple of (is_valid: bool, error_message: str or None)
        """
        if not sections:
            return True, None

        if not isinstance(sections, list):
            return False, "Sections must be a list"

        if len(sections) > SectionValidator.MAX_SECTIONS:
            return False, f"Maximum {SectionValidator.MAX_SECTIONS} sections allowed"

        for idx, section in enumerate(sections):
            is_valid, error = SectionValidator.validate_section(section, idx)
            if not is_valid:
                return False, error

        return True, None

    @staticmethod
    def validate_section(section, idx=0):
        """
        Validate a single section.

        Args:
            section: Section dictionary
            idx: Section index for error messages

        Returns:
            Tuple of (is_valid: bool, error_message: str or None)
        """
        if not isinstance(section, dict):
            return False, f"Section {idx+1} must be a dictionary"

        # Check required fields
        if not SectionValidator.REQUIRED_FIELDS.issubset(section.keys()):
            missing = SectionValidator.REQUIRED_FIELDS - section.keys()
            return False, f"Section {idx+1} missing required fields: {', '.join(missing)}"

        # Validate header length
        header = section.get('header', '')
        if not isinstance(header, str):
            return False, f"Section {idx+1} header must be a string"
        if len(header) > SectionValidator.MAX_HEADER_LENGTH:
            return False, f"Section {idx+1} header exceeds {SectionValidator.MAX_HEADER_LENGTH} characters"

        # Validate icon length
        icon = section.get('icon', '')
        if not isinstance(icon, str):
            return False, f"Section {idx+1} icon must be a string"
        if len(icon) > SectionValidator.MAX_ICON_LENGTH:
            return False, f"Section {idx+1} icon exceeds {SectionValidator.MAX_ICON_LENGTH} characters"

        # Validate content length
        content = section.get('content', '')
        if not isinstance(content, str):
            return False, f"Section {idx+1} content must be a string"
        if len(content) > SectionValidator.MAX_CONTENT_LENGTH:
            return False, f"Section {idx+1} content exceeds {SectionValidator.MAX_CONTENT_LENGTH} characters"

        # Validate order is an integer
        order = section.get('order')
        if not isinstance(order, int):
            return False, f"Section {idx+1} order must be an integer"

        return True, None

    @staticmethod
    def sanitize_content(content):
        """
        Strip HTML tags for security.

        Args:
            content: String content to sanitize

        Returns:
            Sanitized string
        """
        if not content:
            return ''

        # Strip HTML tags
        content = re.sub(r'<[^>]+>', '', content)

        return content.strip()
