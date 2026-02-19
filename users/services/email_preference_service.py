"""
Email Preference Service for managing user email notification preferences.

This service centralizes all email preference logic including:
- Token generation and validation for standalone email preference pages
- Getting and updating user preferences
- Checking if emails should be sent based on preferences
"""

import logging
from typing import Dict, Optional
from django.conf import settings
from django.core import signing

logger = logging.getLogger(__name__)

# Default email preferences for new users
DEFAULT_EMAIL_PREFERENCES = {
    "monthly_recap": True,
    "badge_notifications": True,
    "milestone_notifications": True,
    "subscription_notifications": True,
    "admin_announcements": True,
    "global_unsubscribe": False,
}

# Token expiration: 90 days in seconds
TOKEN_MAX_AGE = 90 * 24 * 60 * 60


class EmailPreferenceService:
    """Service for managing user email preferences and tokens."""

    @staticmethod
    def generate_preference_token(user_id: int) -> str:
        """
        Generate a secure, signed token for email preference links.

        Uses Django's TimestampSigner for cryptographic security with built-in expiration.
        Token is stateless (no database storage required).

        Args:
            user_id: The user's ID to encode in the token

        Returns:
            Signed token string that can be used in email preference URLs

        Example:
            token = EmailPreferenceService.generate_preference_token(123)
            url = f"{settings.SITE_URL}/users/email-preferences/?token={token}"
        """
        signer = signing.TimestampSigner()
        return signer.sign(str(user_id))

    @staticmethod
    def validate_preference_token(token: str) -> int:
        """
        Validate a preference token and extract the user_id.

        Args:
            token: The signed token from the URL

        Returns:
            user_id (int) if token is valid and not expired

        Raises:
            signing.BadSignature: If token signature is invalid
            signing.SignatureExpired: If token has expired (> 90 days)
            ValueError: If token format is invalid

        Example:
            try:
                user_id = EmailPreferenceService.validate_preference_token(token)
            except (signing.BadSignature, signing.SignatureExpired, ValueError):
                return "Invalid or expired token"
        """
        signer = signing.TimestampSigner()

        try:
            # Validate signature and check expiration
            unsigned_value = signer.unsign(token, max_age=TOKEN_MAX_AGE)
            user_id = int(unsigned_value)
            return user_id
        except signing.SignatureExpired:
            logger.warning(f"Expired preference token attempted")
            raise
        except signing.BadSignature:
            logger.warning(f"Invalid preference token signature")
            raise
        except (ValueError, TypeError) as e:
            logger.warning(f"Invalid preference token format: {e}")
            raise ValueError("Invalid token format")

    @staticmethod
    def get_user_preferences(user) -> Dict[str, bool]:
        """
        Get user's email preferences with defaults for missing keys.

        Args:
            user: CustomUser instance

        Returns:
            Dictionary with all preference keys (monthly_recap, badge_notifications, etc.)

        Example:
            prefs = EmailPreferenceService.get_user_preferences(user)
            # {'monthly_recap': True, 'badge_notifications': True, ...}
        """
        # Get user's preferences or empty dict if None
        user_prefs = user.email_preferences if user.email_preferences else {}

        # Merge with defaults (user preferences override defaults)
        preferences = DEFAULT_EMAIL_PREFERENCES.copy()
        preferences.update(user_prefs)

        return preferences

    @staticmethod
    def update_user_preferences(user, preferences: Dict[str, bool]) -> None:
        """
        Update user's email preferences safely.

        Validates preference keys and handles global_unsubscribe logic.
        If global_unsubscribe is True, all other preferences are set to False.

        Args:
            user: CustomUser instance
            preferences: Dictionary of preferences to update

        Example:
            EmailPreferenceService.update_user_preferences(user, {
                'monthly_recap': False,
                'badge_notifications': True,
            })
        """
        # Get current preferences
        current_prefs = EmailPreferenceService.get_user_preferences(user)

        # Update only valid keys
        valid_keys = DEFAULT_EMAIL_PREFERENCES.keys()
        for key, value in preferences.items():
            if key in valid_keys:
                current_prefs[key] = bool(value)

        # Handle global unsubscribe
        if current_prefs.get('global_unsubscribe', False):
            # If global unsubscribe is True, set all others to False
            for key in valid_keys:
                if key != 'global_unsubscribe':
                    current_prefs[key] = False

        # Save to user model
        user.email_preferences = current_prefs
        user.save(update_fields=['email_preferences'])

        logger.info(f"Updated email preferences for user {user.id}")

    @staticmethod
    def should_send_email(user, email_type: str) -> bool:
        """
        Check if an email should be sent to a user based on their preferences.

        Args:
            user: CustomUser instance
            email_type: Type of email (e.g., 'monthly_recap', 'badge_notifications')

        Returns:
            True if email should be sent, False if user has opted out

        Example:
            if EmailPreferenceService.should_send_email(user, 'monthly_recap'):
                send_recap_email(user)
        """
        preferences = EmailPreferenceService.get_user_preferences(user)

        # Check global unsubscribe first
        if preferences.get('global_unsubscribe', False):
            return False

        # Check specific email type preference
        return preferences.get(email_type, True)  # Default to True if key missing

    @staticmethod
    def get_default_preferences() -> Dict[str, bool]:
        """
        Get the default email preferences for new users.

        Returns:
            Dictionary of default preferences
        """
        return DEFAULT_EMAIL_PREFERENCES.copy()
