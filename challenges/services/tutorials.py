"""Which challenge tutorial a hunter is owed, read from `CustomUser.ui_flags` at no query.

Two kinds, with two different shapes, and the difference is the reason this module exists:

  THE SYSTEM INTRO is a VERSION MARKER. Its copy changes when the beta ends (creation opens to everybody),
  so "has this person dismissed it" is the wrong question: a beta hunter who dismissed the beta intro has
  not read the live one. The key stores the newest version the hunter was SHOWN, the
  `new_contracts_modal` lesson ("a marker, not a receipt"), and a later version supersedes an earlier one.

  A TYPE TUTORIAL is a STICKY BOOLEAN, one per type, on the endpoint's existing `ui_flag` branch. How
  A-Z works does not change with the beta, so presence of the key is the whole answer.

ZERO QUERIES. `ui_flags` rides the user row the request already loaded, which is why every one-shot on
the site keys off it.
"""
from challenges.models import CHALLENGE_TYPE_AZ, CHALLENGE_TYPE_CALENDAR, CHALLENGE_TYPE_JOBS
from challenges.services import challenge_service

#: The `ui_flags` key for the system intro's version marker.
INTRO_FLAG = 'challenges_intro_seen'

INTRO_BETA = 'beta'
INTRO_LIVE = 'live'

#: ORDERED, oldest first. A version supersedes every one before it, so a hunter who has seen `live` is
#: never shown `beta` again, even if the beta flag were switched back on.
INTRO_VERSIONS = (INTRO_BETA, INTRO_LIVE)

#: One sticky `ui_flags` key per type. The endpoint's `UI_FLAGS` allow-list is built from these values, so
#: a new type's key cannot be rendered here and refused there.
TYPE_FLAGS = {
    CHALLENGE_TYPE_AZ: 'challenge_tutorial_az',
    CHALLENGE_TYPE_JOBS: 'challenge_tutorial_jobs',
    CHALLENGE_TYPE_CALENDAR: 'challenge_tutorial_calendar',
}


def _flags(user):
    """The account's flags, or `{}` for an anonymous visitor (who has no `ui_flags` at all)."""
    return getattr(user, 'ui_flags', None) or {}


def _rank(version):
    """A version's place in `INTRO_VERSIONS`, or -1 for anything else (absent, or a junk value)."""
    return INTRO_VERSIONS.index(version) if version in INTRO_VERSIONS else -1


def current_intro_version():
    """`beta` while the members-first beta runs, `live` once creation is open to everybody."""
    return INTRO_BETA if challenge_service.beta_is_on() else INTRO_LIVE


def intro_is_due(user):
    """Whether this account has not yet been shown the CURRENT version of the system intro.

    False for an anonymous visitor: there is nowhere to record the dismissal, so an auto-open would
    return on every visit. They reach it through the recall link instead.
    """
    if not getattr(user, 'is_authenticated', False):
        return False
    return _rank(_flags(user).get(INTRO_FLAG)) < _rank(current_intro_version())


def merged_intro_marker(user, version):
    """What to STORE when `version` is reported seen, or None when the report is not acceptable.

    REFUSED ABOVE THE CURRENT VERSION. Accepting `live` during the beta would let one POST suppress a
    modal that has not been written yet, permanently, with nothing in the UI to undo it.

    NEVER REWINDS. A stale tab that rendered `beta` and is dismissed after the hunter has seen `live`
    keeps `live`, so the newer intro is not shown a second time.
    """
    if _rank(version) < 0 or _rank(version) > _rank(current_intro_version()):
        return None
    stored = _flags(user).get(INTRO_FLAG)
    return version if _rank(version) >= _rank(stored) else stored


def type_tutorial_is_due(user, challenge_type):
    """Whether this account has not yet dismissed `challenge_type`'s tutorial. False for anonymous."""
    if not getattr(user, 'is_authenticated', False):
        return False
    flag = TYPE_FLAGS.get(challenge_type)
    return flag is not None and flag not in _flags(user)
