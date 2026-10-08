"""The share card for a run: one 1200x630 PNG a hunter can post, built at click time.

THE SAME FAMILY AS THE PLAT AND PROFILE CARDS, so the same rules: rendered by headless Chromium from
`templates/shareables/challenge_card.html` with no stylesheet, external images cached to same-origin temp
files, and nothing on it that a stranger could not read off the run's public page. See
`docs/features/share-images.md` for the pipeline.

RENDERED WHEN ASKED FOR, NEVER STORED. Minting was cut (plan, 2026-10-03), so the card is accurate at the
moment it is requested and there is no frozen number to drift -- the `PlatinumShareImage` lesson, applied
by not having the stored image at all.

TWO CALLERS, AND THEY WANT THE IMAGES DIFFERENTLY. The share modal's preview is a real page on the site
origin, so it hands the browser the REMOTE cover URLs and lets it fetch them, which costs the request
nothing. Only the PNG download caches them locally, because the renderer works in `about:blank` and can
only embed what is on disk. Caching on preview too would turn opening the modal into up to 27 synchronous
`requests.get` calls on a cold cache -- the exact cost `api.shareable_views._art_path` was written to
avoid for the plat card.
"""
import logging
from concurrent.futures import ThreadPoolExecutor

from django.utils import timezone

from core.services.share_image_cache import ShareImageCache
from users.services.marks import mark_style

from challenges.models import CHALLENGE_TYPE_AZ
from challenges.services import rewards
from challenges.services.slot_render import slot_cards

logger = logging.getLogger(__name__)

#: Card types that have a share card yet. Built one at a time (owner, 2026-10-08), so a type joins this
#: set when its card ships rather than inheriting another type's layout.
SHAREABLE_TYPES = frozenset({CHALLENGE_TYPE_AZ})

#: How many images download at once on a cold cache. A fully cold card is 27 (26 covers and the avatar),
#: so 8 at a time is about four waves, where in series it would be 27. That is a REDUCTION, not a bound:
#: `requests`' `timeout=10` limits the connect and each read separately rather than the whole download, so
#: a slow CDN can still take tens of seconds before the render starts. The common case is warm, because
#: covers are shared across every hunter who picked the same game.
_FETCH_WORKERS = 8

CARD_TEMPLATE = 'shareables/challenge_card.html'

#: Square states, as the template branches on them.
DONE, ASSIGNED, OPEN = 'done', 'assigned', 'open'


def is_shareable(challenge):
    """Whether `challenge` has a card at all. Finished AND in-progress runs do (owner, 2026-10-08)."""
    return challenge.challenge_type in SHAREABLE_TYPES and not challenge.is_deleted


def build_card_context(challenge, *, cache_images=False):
    """Everything `challenge_card.html` draws, flat.

    `cache_images=True` for the PNG (see the module docstring). The squares keep their run order, which
    for A-Z is the alphabet, and the template splits them into two rows of thirteen.
    """
    profile = challenge.profile
    cards = slot_cards(challenge)

    squares = []
    for card in cards:
        game = card['cover']    # None on an empty square: `slot_cards` keys covers by the slot's contract
        squares.append({
            'key': card['key'],
            'state': DONE if card['is_completed'] else ASSIGNED if card['is_filled'] else OPEN,
            # The SMALL variant (180x256): a square is 80x107, and `cover_big` would download about twice
            # the bytes for the renderer to shrink anyway. The Hall of Fame board uses it for the same reason.
            'cover': game.display_image_url_small if game else '',
            # The generic PS placeholder is not art and must not be cropped -- the same switch the live
            # board makes with `.pp-csq__art--icon`.
            'cover_is_art': bool(game and game.has_cover_art),
        })

    avatar = profile.avatar_url or ''
    if cache_images:
        (avatar,), covers = _cached([avatar], [s['cover'] for s in squares])
        for square, cover in zip(squares, covers):
            square['cover'] = cover

    half = (len(squares) + 1) // 2
    return {
        'username': profile.display_psn_username or profile.psn_username,
        'mark': mark_style(profile.display_mark),
        'avatar_image': avatar,
        'is_complete': challenge.is_complete,
        'completed_count': challenge.completed_count,
        'assigned_count': challenge.filled_count - challenge.completed_count,
        'total_slots': challenge.total_slots,
        'letters_left': challenge.total_slots - challenge.completed_count,
        'title': rewards.granted_titles_for([challenge]).get(challenge.pk) if challenge.is_complete else None,
        'started_at': challenge.created_at,
        'completed_at': challenge.completed_at,
        'days': _days(challenge),
        'rows': [squares[:half], squares[half:]],
    }


def filename_for(challenge):
    """`<hunter>-<run name>.png`, ASCII-safe for a Content-Disposition header. The run's name already carries
    its ordinal ("A-Z Challenge (Run 2)"), so two runs' cards do not overwrite each other in Downloads."""
    raw = '%s %s' % (challenge.profile.display_psn_username or challenge.profile.psn_username, challenge.name)
    safe = ''.join(c if c.isascii() and (c.isalnum() or c in '-_') else '-' for c in raw)
    return ('-'.join(part for part in safe.split('-') if part) or 'challenge-card') + '.png'


def _days(challenge, *, now=None):
    """Start to finish for a finished run, start to now for a live one -- counted the way a hunter
    would say it, so a run started and finished on the same day took 1 day, not 0.

    IN THE HUNTER'S OWN DAYS, not UTC's. `TimezoneMiddleware` activates their timezone for the request, and
    the card prints its date through `|date` in that zone, so counting UTC days put "Started Oct 1" beside a
    figure that disagreed with it for anyone whose evening is the next UTC day.
    """
    end = challenge.completed_at or now or timezone.now()
    return max(1, (timezone.localdate(end) - timezone.localdate(challenge.created_at)).days + 1)


def _cached(*groups):
    """Each group of remote URLs, mapped to share-temp paths, in parallel. Order is preserved, and a URL
    that fails (or was empty) comes back '' so the template falls back to its no-art square."""
    wanted = sorted({url for group in groups for url in group if url})
    with ThreadPoolExecutor(max_workers=_FETCH_WORKERS) as pool:
        resolved = dict(zip(wanted, pool.map(ShareImageCache.fetch_and_cache, wanted)))
    for url in wanted:
        if not resolved[url]:
            logger.warning("[CHALLENGE-CARD] failed to cache image: %s", url)

    return [[resolved.get(url, '') for url in group] for group in groups]
