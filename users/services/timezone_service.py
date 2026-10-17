"""The one writer for a user's timezone.

Three surfaces let a user set their timezone (the Settings page, the recap timezone modal via
UpdateTimezoneAPIView, and the quick-settings API), and before this service existed they had
three different side-effect sets: only the recap modal stamped `timezone_confirmed_at`, and the
Settings page didn't un-finalize recaps at all -- so a settings-page change left every recap
rendered in the OLD zone and the recap prompt eligible to nag again. Every writer now routes
here so the side effects cannot drift apart.

A DETECTED ZONE IS NOT AN ANSWER (owner, 2026-10-17). Accounts used to start at UTC until the hunter
picked a zone, so anyone who never did read every date in UTC. Now a new account takes its browser's
zone at signup, and an existing one still on the untouched UTC default takes it once, silently, on its
next visit (`adopt_detected_timezone`). Neither stamps `timezone_confirmed_at`: that stamp means "you
answered", and the Recap's prompt still asks these hunters to confirm what was guessed.
"""
import logging

import pytz
from django.contrib.auth import get_user_model
from django.utils import timezone as dj_timezone

logger = logging.getLogger(__name__)

#: OLD NAMES BROWSERS STILL REPORT, mapped to the current name the field accepts (`pytz.common_timezones`).
#: Chrome and Safari answer `Intl...timeZone` with the zone's legacy link for some places, so a hunter in India
#: reports `Asia/Calcutta`, which is not in the field's choices; without this map they would stay on UTC forever.
#: Curated from the tz database's `backward` links for zones a browser is known to report; every target is checked
#: against the common set on use, so a stale entry degrades to "not adopted", never to a bad value.
LEGACY_ZONE_NAMES = {
    'Asia/Calcutta': 'Asia/Kolkata', 'Europe/Kiev': 'Europe/Kyiv', 'Europe/Uzhgorod': 'Europe/Kyiv',
    'Europe/Zaporozhye': 'Europe/Kyiv', 'America/Buenos_Aires': 'America/Argentina/Buenos_Aires',
    'America/Catamarca': 'America/Argentina/Catamarca', 'America/Cordoba': 'America/Argentina/Cordoba',
    'Asia/Saigon': 'Asia/Ho_Chi_Minh', 'Asia/Katmandu': 'Asia/Kathmandu', 'Asia/Rangoon': 'Asia/Yangon',
    'America/Godthab': 'America/Nuuk', 'Atlantic/Faeroe': 'Atlantic/Faroe', 'Asia/Ulan_Bator': 'Asia/Ulaanbaatar',
    'Pacific/Ponape': 'Pacific/Pohnpei', 'Pacific/Truk': 'Pacific/Chuuk', 'Pacific/Enderbury': 'Pacific/Kanton',
    'America/Indianapolis': 'America/Indiana/Indianapolis', 'America/Louisville': 'America/Kentucky/Louisville',
    'Asia/Dacca': 'Asia/Dhaka', 'Asia/Thimbu': 'Asia/Thimphu', 'Asia/Macao': 'Asia/Macau',
    'Asia/Chongqing': 'Asia/Shanghai', 'Asia/Ujung_Pandang': 'Asia/Makassar', 'Australia/Canberra': 'Australia/Sydney',
    'Asia/Istanbul': 'Europe/Istanbul', 'Asia/Tel_Aviv': 'Asia/Jerusalem',
}


def normalize_zone(name):
    """A browser-reported zone as the field stores it: a current name from `pytz.common_timezones`, an old name
    mapped to its current one (`LEGACY_ZONE_NAMES`), or '' for anything else."""
    if not isinstance(name, str):
        return ''
    name = LEGACY_ZONE_NAMES.get(name.strip(), name.strip())
    return name if name in pytz.common_timezones_set else ''


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
    return old != tz_name, _reset_recaps(user, old, tz_name)


def adopt_detected_timezone(user, tz_name):
    """Take the browser's zone for an account still on the untouched default. Returns True if it did.

    ONLY while the account has never chosen: `user_timezone` is the UTC default AND it was never
    confirmed. The guard and the write are ONE conditional UPDATE, so a zone the hunter picked in
    another tab a moment ago can never be overwritten by a guess. An old name a browser reports is mapped
    to its current one (`normalize_zone`); anything else outside the field's choices is ignored, and so is UTC
    itself (nothing to change). Never stamps the confirmation, and writes through its own conditional UPDATE
    rather than `set_user_timezone` (which always confirms): see the module docstring.
    """
    tz_name = normalize_zone(tz_name)
    if not tz_name or tz_name == 'UTC':
        return False
    # `get_user_model()`, never `type(user)`: inside a request `request.user` is a lazy wrapper.
    claimed = get_user_model().objects.filter(
        pk=user.pk, user_timezone='UTC', timezone_confirmed_at__isnull=True,
    ).update(user_timezone=tz_name)
    if not claimed:
        return False
    user.user_timezone = tz_name
    _reset_recaps(user, 'UTC', tz_name)
    return True


def _reset_recaps(user, old, new):
    """Monthly recaps are rendered in the user's zone, so a change un-finalizes them to regenerate."""
    changed = old != new
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
                    recaps_reset, profile.id, old, new,
                )
    return recaps_reset
