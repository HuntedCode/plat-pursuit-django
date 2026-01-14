"""
Custom exception classes for the trophies app.

This module defines specific exception types for better error handling
and more informative error messages throughout the application.
"""


class TrophiesBaseException(Exception):
    """Base exception class for all trophies app exceptions."""
    pass


class SyncError(TrophiesBaseException):
    """Raised when profile synchronization fails."""

    def __init__(self, message="Profile sync failed", profile=None, reason=None):
        self.profile = profile
        self.reason = reason
        super().__init__(message)


class SyncCooldownError(SyncError):
    """Raised when sync is attempted during cooldown period."""

    def __init__(self, seconds_remaining, profile=None):
        self.seconds_remaining = seconds_remaining
        message = f"Sync cooldown active: {seconds_remaining} seconds remaining"
        super().__init__(message, profile=profile, reason="cooldown")


class SyncInProgressError(SyncError):
    """Raised when sync is already in progress for a profile."""

    def __init__(self, profile=None):
        message = "Sync already in progress for this profile"
        super().__init__(message, profile=profile, reason="in_progress")


class VerificationError(TrophiesBaseException):
    """Raised when PSN profile verification fails."""

    def __init__(self, message="Verification failed", profile=None, reason=None):
        self.profile = profile
        self.reason = reason
        super().__init__(message)


class VerificationCodeExpiredError(VerificationError):
    """Raised when verification code has expired."""

    def __init__(self, profile=None):
        message = "Verification code has expired"
        super().__init__(message, profile=profile, reason="expired")


class VerificationCodeNotFoundError(VerificationError):
    """Raised when verification code is not found in About Me section."""

    def __init__(self, profile=None):
        message = "Verification code not found in PSN About Me section"
        super().__init__(message, profile=profile, reason="not_found")


class ProfileNotFoundError(TrophiesBaseException):
    """Raised when a requested profile does not exist."""

    def __init__(self, psn_username=None):
        self.psn_username = psn_username
        message = f"Profile not found: {psn_username}" if psn_username else "Profile not found"
        super().__init__(message)


class ProfileAlreadyLinkedError(TrophiesBaseException):
    """Raised when attempting to link a profile that is already linked."""

    def __init__(self, profile=None):
        self.profile = profile
        message = "This PSN profile is already linked to another account"
        super().__init__(message)


class RateLimitError(TrophiesBaseException):
    """Raised when API rate limit is exceeded."""

    def __init__(self, retry_after=None):
        self.retry_after = retry_after
        message = f"Rate limit exceeded. Retry after {retry_after} seconds" if retry_after else "Rate limit exceeded"
        super().__init__(message)


class PSNAPIError(TrophiesBaseException):
    """Raised when PSN API request fails."""

    def __init__(self, message="PSN API request failed", status_code=None, response=None):
        self.status_code = status_code
        self.response = response
        super().__init__(message)


class GameNotFoundError(TrophiesBaseException):
    """Raised when a requested game does not exist."""

    def __init__(self, np_comm_id=None, game_title=None):
        self.np_comm_id = np_comm_id
        self.game_title = game_title
        if np_comm_id:
            message = f"Game not found: {np_comm_id}"
        elif game_title:
            message = f"Game not found: {game_title}"
        else:
            message = "Game not found"
        super().__init__(message)


class BadgeNotFoundError(TrophiesBaseException):
    """Raised when a requested badge does not exist."""

    def __init__(self, series_slug=None):
        self.series_slug = series_slug
        message = f"Badge series not found: {series_slug}" if series_slug else "Badge not found"
        super().__init__(message)


class TrophySelectionLimitError(TrophiesBaseException):
    """Raised when user exceeds trophy selection limit."""

    def __init__(self, limit, current_count):
        self.limit = limit
        self.current_count = current_count
        message = f"Trophy selection limit exceeded: {current_count}/{limit}"
        super().__init__(message)


class InvalidTrophyTypeError(TrophiesBaseException):
    """Raised when an invalid trophy type is specified."""

    def __init__(self, trophy_type):
        self.trophy_type = trophy_type
        message = f"Invalid trophy type: {trophy_type}"
        super().__init__(message)


class InvalidSyncTierError(TrophiesBaseException):
    """Raised when an invalid sync tier is specified."""

    def __init__(self, sync_tier):
        self.sync_tier = sync_tier
        message = f"Invalid sync tier: {sync_tier}"
        super().__init__(message)


class DataIntegrityError(TrophiesBaseException):
    """Raised when data integrity issues are detected."""

    def __init__(self, message="Data integrity error", model=None, field=None):
        self.model = model
        self.field = field
        super().__init__(message)


class CacheError(TrophiesBaseException):
    """Raised when cache operations fail."""

    def __init__(self, message="Cache operation failed", key=None):
        self.key = key
        super().__init__(message)


class PermissionDeniedError(TrophiesBaseException):
    """Raised when user lacks permission for an action."""

    def __init__(self, message="Permission denied", required_permission=None):
        self.required_permission = required_permission
        super().__init__(message)


class InvalidFilterError(TrophiesBaseException):
    """Raised when invalid filter parameters are provided."""

    def __init__(self, message="Invalid filter parameters", filter_name=None, filter_value=None):
        self.filter_name = filter_name
        self.filter_value = filter_value
        super().__init__(message)
