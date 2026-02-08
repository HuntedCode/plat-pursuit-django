"""
Monthly Recap Message Service - Builds shared context for emails and notifications.

Centralizes the logic for building message context from MonthlyRecap instances,
ensuring consistency between email and in-app notification content.
"""
import logging
from django.conf import settings
from users.services.email_preference_service import EmailPreferenceService

logger = logging.getLogger(__name__)


class MonthlyRecapMessageService:
    """Service for building monthly recap message context."""

    @staticmethod
    def get_trophy_tier(count):
        """
        Get rounded-down trophy tier for display.
        Returns a string like '10+', '50+', '100+', etc.

        Extracted from send_monthly_recap_emails.py for reuse.

        Args:
            count: Integer trophy count

        Returns:
            str: Trophy tier display string
        """
        if count == 0:
            return '0'
        elif count < 10:
            return str(count)
        elif count < 25:
            return '10+'
        elif count < 50:
            return '25+'
        elif count < 100:
            return '50+'
        elif count < 250:
            return '100+'
        elif count < 500:
            return '250+'
        elif count < 1000:
            return '500+'
        else:
            return '1000+'

    @staticmethod
    def build_base_context(recap):
        """
        Build base context dictionary from MonthlyRecap instance.

        This context is shared between emails and notifications, containing
        all the personalized data about the user's monthly performance.

        Args:
            recap: MonthlyRecap instance

        Returns:
            dict with keys: username, month_name, year, active_days, trophy_tier,
                 games_started, total_trophies, platinums_earned, games_completed,
                 badges_earned, has_streak, recap_url
        """
        profile = recap.profile

        # Get active days from activity calendar or streak data
        active_days = recap.activity_calendar.get('total_active_days', 0)
        if not active_days:
            active_days = recap.streak_data.get('total_active_days', 0)

        return {
            'username': profile.display_psn_username or profile.psn_username,
            'month_name': recap.month_name,
            'year': recap.year,
            'active_days': active_days,
            'trophy_tier': MonthlyRecapMessageService.get_trophy_tier(recap.total_trophies_earned),
            'games_started': recap.games_started,
            'total_trophies': recap.total_trophies_earned,
            'platinums_earned': recap.platinums_earned,
            'games_completed': recap.games_completed,
            'badges_earned': recap.badges_earned_count,
            'has_streak': bool(recap.streak_data.get('longest_streak', 0) > 1),
            'recap_url': f"{settings.SITE_URL}/recap/{recap.year}/{recap.month}/",
        }

    @staticmethod
    def build_email_context(recap):
        """
        Build complete email context including preference URL.

        Args:
            recap: MonthlyRecap instance

        Returns:
            dict with all email template variables
        """
        user = recap.profile.user
        context = MonthlyRecapMessageService.build_base_context(recap)

        # Generate preference token for email footer
        try:
            preference_token = EmailPreferenceService.generate_preference_token(user.id)
            preference_url = f"{settings.SITE_URL}/users/email-preferences/?token={preference_token}"
        except Exception as e:
            logger.exception(f"Failed to generate preference_url for user {user.id}: {e}")
            preference_url = f"{settings.SITE_URL}/users/email-preferences/"

        # Add email-specific fields
        context['site_url'] = settings.SITE_URL
        context['preference_url'] = preference_url

        return context

    @staticmethod
    def build_notification_message(recap):
        """
        Build teaser notification message text from recap data.

        Keeps it vague to entice the user to click through to the full recap.

        Args:
            recap: MonthlyRecap instance

        Returns:
            str: Formatted notification message
        """
        month_name = recap.month_name
        return (
            f"Your {month_name} trophy hunting journey has been wrapped up "
            f"and your personalized recap is waiting for you!"
        )

    @staticmethod
    def build_notification_context(recap):
        """
        Build rich notification metadata for the in-app notification detail view.

        DEPRECATED: The notification detail now uses a teaser-style display that
        only requires base context fields (from build_base_context). This method
        is retained for reference and potential future use.

        Extends base context with trophy breakdown, highlights, and comparison
        data. Keeps payload reasonable by excluding full activity_calendar days
        array and quiz data.

        Args:
            recap: MonthlyRecap instance

        Returns:
            dict with all fields needed for a rich recap detail view
        """
        context = MonthlyRecapMessageService.build_base_context(recap)

        # Trophy type breakdown
        context['bronzes_earned'] = recap.bronzes_earned
        context['silvers_earned'] = recap.silvers_earned
        context['golds_earned'] = recap.golds_earned

        # Platinums detail (capped at 5 to keep metadata size reasonable)
        context['platinums_data'] = (recap.platinums_data or [])[:5]

        # Highlight data
        context['rarest_trophy'] = recap.rarest_trophy_data or {}
        context['most_active_day'] = recap.most_active_day or {}
        context['streak'] = recap.streak_data or {}
        context['time_analysis'] = recap.time_analysis_data or {}

        # Badge summary (capped at 5)
        context['badge_xp_earned'] = recap.badge_xp_earned
        context['badges_data'] = (recap.badges_data or [])[:5]

        # Month-over-month comparison
        context['comparison'] = recap.comparison_data or {}

        return context
