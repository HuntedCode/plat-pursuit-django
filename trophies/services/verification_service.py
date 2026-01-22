"""
PSN profile verification service.

This module handles the logic for verifying PSN profile ownership through
verification codes placed in the user's PSN "About Me" section.
"""
import secrets
from datetime import timedelta
from django.utils import timezone


class VerificationService:
    """Handles PSN profile verification logic."""

    @staticmethod
    def generate_code(profile):
        """
        Generate and set a secure, time-limited verification code for a profile.

        Creates a random 6-character hexadecimal code that expires after 1 hour.
        The code is saved to the profile's verification_code field.

        Args:
            profile: Profile instance to generate code for

        Returns:
            str: The generated verification code (6 uppercase hex characters)

        Example:
            >>> code = VerificationService.generate_code(profile)
            >>> code
            'A3F2B1'
        """
        code = secrets.token_hex(3).upper()
        profile.verification_code = code
        profile.verification_expires_at = timezone.now() + timedelta(hours=1)
        profile.save(update_fields=['verification_code', 'verification_expires_at'])
        return code

    @staticmethod
    def verify_code(profile, fetched_about_me):
        """
        Verify that the profile's verification code appears in PSN About Me section.

        Checks if:
        1. Profile has a verification code set
        2. Code has not expired (within 1 hour)
        3. Code appears in the fetched About Me text

        If verification succeeds, the code is cleared for security.
        If verification fails due to expiry, the code is also cleared.

        Args:
            profile: Profile instance to verify
            fetched_about_me: About Me text fetched from PSN API

        Returns:
            bool: True if verification successful, False otherwise

        Example:
            >>> # User adds code "A3F2B1" to their PSN About Me
            >>> about_me = "My code is A3F2B1"
            >>> VerificationService.verify_code(profile, about_me)
            True
        """
        if not profile.verification_code or not profile.verification_expires_at:
            return False

        # Check if code has expired
        if timezone.now() > profile.verification_expires_at:
            VerificationService.clear_code(profile)
            return False

        # Check if code appears in About Me text
        if profile.verification_code in fetched_about_me:
            VerificationService.clear_code(profile)
            return True

        return False

    @staticmethod
    def clear_code(profile):
        """
        Clear verification code and expiry from profile.

        Called after successful verification or when code expires.
        Ensures codes cannot be reused for security.

        Args:
            profile: Profile instance to clear code from
        """
        profile.verification_code = None
        profile.verification_expires_at = None
        profile.save(update_fields=['verification_code', 'verification_expires_at'])

    @staticmethod
    def link_profile_to_user(profile, user):
        """
        Link a verified PSN profile to a user account.

        Sets the profile's user field and marks it as linked.
        Updates premium status based on user's subscription tier.
        Checks for PSN linking milestones.

        Args:
            profile: Profile instance to link
            user: CustomUser instance to link to

        Example:
            >>> VerificationService.link_profile_to_user(profile, request.user)
        """
        if not profile.user:
            profile.user = user
            profile.is_linked = True
            profile.save(update_fields=['user', 'is_linked'])

            # Update premium status based on user's tier
            is_premium = user.premium_tier in ['premium_monthly', 'premium_yearly', 'supporter']
            profile.update_profile_premium(is_premium)

            # Check for PSN linking milestones
            from trophies.services.milestone_service import check_all_milestones_for_user
            check_all_milestones_for_user(profile, criteria_type='psn_linked')

    @staticmethod
    def unlink_profile_from_user(profile):
        """
        Unlink a PSN profile from its user account.

        Removes the user association and marks profile as not linked.
        Removes premium status.

        Args:
            profile: Profile instance to unlink

        Example:
            >>> VerificationService.unlink_profile_from_user(profile)
        """
        if profile.user:
            profile.user = None
            profile.is_linked = False
            profile.save(update_fields=['user', 'is_linked'])
            profile.update_profile_premium(False)
