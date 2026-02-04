"""
TemplateService - Service for managing notification templates.
Handles variable substitution using Python's str.format().
"""
from notifications.models import NotificationTemplate


class TemplateService:
    """Service for managing and rendering notification templates."""

    @staticmethod
    def render_template(template, context):
        """
        Render template with variable substitution.
        Uses Python's str.format() for {variable} replacement.

        Args:
            template: NotificationTemplate instance
            context: Dict with variables like {'username': 'John', 'game_name': 'Elden Ring'}

        Returns:
            dict: {
                'title': rendered title string,
                'message': rendered message string,
                'action_url': rendered action URL (if template has one)
            }

        Raises:
            KeyError: If required variable is missing from context
            ValueError: If template formatting fails
        """
        try:
            # Render title
            title = template.title_template.format(**context)

            # Render message
            message = template.message_template.format(**context)

            # Render action URL if present
            action_url = None
            if template.action_url_template:
                action_url = template.action_url_template.format(**context)

            return {
                'title': title,
                'message': message,
                'action_url': action_url,
            }

        except KeyError as e:
            raise KeyError(f"Missing required variable in context: {e}")
        except ValueError as e:
            raise ValueError(f"Template formatting error: {e}")

    @staticmethod
    def get_template_by_type(notification_type):
        """
        Get default template for notification type.

        Args:
            notification_type: Type of notification (from NOTIFICATION_TYPES choices)

        Returns:
            NotificationTemplate instance or None if not found
        """
        try:
            return NotificationTemplate.objects.get(
                notification_type=notification_type,
                auto_trigger_enabled=True
            )
        except NotificationTemplate.DoesNotExist:
            return None
        except NotificationTemplate.MultipleObjectsReturned:
            # If multiple templates exist, return the most recently updated
            return NotificationTemplate.objects.filter(
                notification_type=notification_type,
                auto_trigger_enabled=True
            ).order_by('-updated_at').first()

    @staticmethod
    def get_template_by_name(template_name):
        """
        Get template by unique name.

        Args:
            template_name: Unique name of template

        Returns:
            NotificationTemplate instance or None if not found
        """
        try:
            return NotificationTemplate.objects.get(name=template_name)
        except NotificationTemplate.DoesNotExist:
            return None

    @staticmethod
    def validate_context(template, context):
        """
        Validate that context contains all required variables for template.

        Args:
            template: NotificationTemplate instance
            context: Dict with variables

        Returns:
            tuple: (bool, list) - (is_valid, missing_variables)
        """
        import re

        # Extract all {variable} placeholders from templates
        title_vars = set(re.findall(r'\{(\w+)\}', template.title_template))
        message_vars = set(re.findall(r'\{(\w+)\}', template.message_template))
        action_url_vars = set()
        if template.action_url_template:
            action_url_vars = set(re.findall(r'\{(\w+)\}', template.action_url_template))

        # Combine all required variables
        required_vars = title_vars | message_vars | action_url_vars

        # Check which variables are missing from context
        missing_vars = required_vars - set(context.keys())

        return len(missing_vars) == 0, list(missing_vars)

    @staticmethod
    def preview_template(template, context):
        """
        Preview template rendering without creating notification.
        Useful for admin UI previews.

        Args:
            template: NotificationTemplate instance
            context: Dict with sample variables

        Returns:
            dict: {
                'success': bool,
                'rendered': dict (if success) with title, message, action_url,
                'error': str (if failure)
            }
        """
        try:
            # Validate context first
            is_valid, missing_vars = TemplateService.validate_context(template, context)
            if not is_valid:
                return {
                    'success': False,
                    'error': f"Missing required variables: {', '.join(missing_vars)}"
                }

            # Render template
            rendered = TemplateService.render_template(template, context)

            return {
                'success': True,
                'rendered': rendered
            }

        except Exception as e:
            return {
                'success': False,
                'error': str(e)
            }
