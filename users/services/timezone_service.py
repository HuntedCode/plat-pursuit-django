"""The one writer for a user's timezone.

Three surfaces let a user set their timezone (the Settings page, the recap timezone modal via
UpdateTimezoneAPIView, and the quick-settings API), and before this service existed they had
three different side-effect sets: only the recap modal stamped `timezone_confirmed_at`, and the
Settings page didn't un-finalize recaps at all -- so a settings-page change left every recap
rendered in the OLD zone and the recap prompt eligible to nag again. Every writer now routes
here so the side effects cannot drift apart.
"""
import logging

from django.utils import timezone as dj_timezone

logger = logging.getLogger(__name__)


def set_user_timezone(user, tz_name):
    """Write the timezone plus its two coupled side effects. Returns (changed, recaps_reset).

    Callers must have validated `tz_name` against `pytz.common_timezones_set` first (the two
    API views and the settings form all validate before calling; this function trusts its
    input so it never has to answer "what does an invalid zone do to the stamp").

    Side effects:
    - `timezone_confirmed_at` is stamped on EVERY successful save, including one that picks
      the same zone back. The point of the stamp is not "what did you pick" -- the field
      already holds that -- but "have you ever answered", which `user_timezone` cannot express
      because it defaults to UTC and is non-null. Confirming UTC is an answer, and a hunter
      who does it must not be asked again.
    - Monthly recaps un-finalize when the zone actually CHANGES: they are rendered in the
      user's zone, so a finalized recap in the old zone is wrong in the new one. They
      regenerate on next access.

    The `update_fields` list deliberately misses `MARK_FIELDS`, so `CustomUser.save()`'s
    display-mark refresh does not fire on a timezone change.
    """
    old = user.user_timezone or 'UTC'
    user.user_timezone = tz_name
    user.timezone_confirmed_at = dj_timezone.now()
    user.save(update_fields=['user_timezone', 'timezone_confirmed_at'])

    changed = old != tz_name
    recaps_reset = 0
    if changed:
        profile = getattr(user, 'profile', None)
        if profile:
            from trophies.models import MonthlyRecap
            recaps_reset = MonthlyRecap.objects.filter(
                profile=profile, is_finalized=True,
            ).update(is_finalized=False)
            if recaps_reset:
                logger.info(
                    "Un-finalized %d recaps for profile %s after timezone change: %s -> %s",
                    recaps_reset, profile.id, old, tz_name,
                )
    return changed, recaps_reset
