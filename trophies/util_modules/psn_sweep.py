"""Shared plumbing for catalogue-wide PSN sweeps run as management commands.

A "sweep" enqueues one TokenKeeper job per Game across the whole catalogue, drained on the
`bulk_priority` queue so it never starves live user syncs. Both sweeps that exist need the same two
things, and both got them wrong in slightly different ways when they were separate copies:

  * a DRIVER profile, because every PSN call needs an authenticated account even when the endpoint
    itself is title-level and account-agnostic, and
  * the API PLATFORM for a Game, which is not simply `title_platform[0]`.

Kept here rather than in a command module so neither command imports the other.
"""
from trophies.models import Profile, ScoutAccount


class SweepConfigurationError(Exception):
    """No usable driver profile. Raised as a plain exception so callers can re-raise it as whatever
    their context wants (CommandError for a management command)."""


def resolve_driver_profile(username=None):
    """Resolve the profile that supplies auth/context for a sweep.

    The driver does NOT need to own the games being swept: the PSN endpoints these sweeps call are
    title-level, so any authenticated account can ask about any title. It does, however, carry the
    bulk_priority job counter for the entire sweep, which is why a dedicated scout is strongly
    preferred over a real user -- that user's own sync would queue behind the whole sweep.
    """
    if username:
        try:
            return Profile.objects.get(psn_username=username.lower())
        except Profile.DoesNotExist:
            raise SweepConfigurationError(f"No profile with psn_username '{username}'.")

    scout = ScoutAccount.objects.filter(status='active').select_related('profile').first()
    if not scout:
        raise SweepConfigurationError(
            "No driver profile given and no active ScoutAccount found. "
            "Pass --driver-profile <psn_username>."
        )
    return scout.profile


def resolve_api_platform(title_platform):
    """Resolve the platform to send to PSN for a Game, or None if there isn't one.

    PSPC (PC) titles report `title_platform[0] == 'PSPC'` and carry the real console platform at
    [1]. Sending 'PSPC' straight through produces a sparse response, which downstream reads as "PSN
    has no data for this title" rather than "we asked the wrong question". Returns None when the
    list is empty, or holds only 'PSPC' with nothing behind it, so callers skip instead of
    IndexError-ing mid-sweep.
    """
    if not title_platform:
        return None
    first = title_platform[0]
    if first != 'PSPC':
        return first
    return title_platform[1] if len(title_platform) > 1 else None
