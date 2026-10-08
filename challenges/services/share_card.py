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

from django.utils import dateformat, timezone

from core.services.completion_card_service import DISCIPLINE_COLOURS, JOB_ICON_PATHS
from core.services.share_image_cache import ShareImageCache
from users.services.marks import mark_style

from challenges.models import (CALENDAR_MONTH_DAYS, CHALLENGE_TYPE_AZ, CHALLENGE_TYPE_CALENDAR,
                               CHALLENGE_TYPE_JOBS)
from challenges.services import calendar_render, rewards
from challenges.services.slot_render import slot_groups

logger = logging.getLogger(__name__)

#: Card types that have a share card yet. Built one at a time (owner, 2026-10-08), so a type joins this
#: set when its card ships rather than inheriting another type's layout.
SHAREABLE_TYPES = frozenset({CHALLENGE_TYPE_AZ, CHALLENGE_TYPE_JOBS, CHALLENGE_TYPE_CALENDAR})

#: The Plat Calendar's twelve month hues, January first -- the `--cal-c` table in
#: static/css/components/challenges.css, ported because the card renders with no stylesheet. Chromium
#: renders `oklch()` natively, so they are copied verbatim rather than converted, and
#: `test_the_calendar_card_wears_the_pages_month_hues` fails the moment the two tables disagree.
MONTH_HUES = (
    'oklch(0.74 0.13 255)',     # January   -- deep winter blue
    'oklch(0.72 0.14 290)',     # February  -- late-winter violet
    'oklch(0.80 0.14 150)',     # March     -- first green
    'oklch(0.84 0.15 128)',     # April     -- spring
    'oklch(0.86 0.16 105)',     # May       -- lime
    'oklch(0.88 0.15 88)',      # June      -- high sun
    'oklch(0.84 0.16 70)',      # July      -- gold
    'oklch(0.78 0.17 52)',      # August    -- amber
    'oklch(0.72 0.17 35)',      # September -- russet
    'oklch(0.68 0.18 18)',      # October   -- autumn red
    'oklch(0.66 0.15 345)',     # November  -- berry
    'oklch(0.90 0.045 235)',    # December  -- frost, near-white
)

#: The text-mute grey, for a job square or shelf whose discipline has no colour: a square whose `Job` was
#: deleted (no atom at all, so `slot_groups` puts it on a shelf of its own) or a discipline missing from
#: `DISCIPLINE_COLOURS`. Such a square is kept drawable rather than dropped, so the card needs a colour.
_NO_DISCIPLINE_COLOUR = '#8a939f'

#: The glyph for a job square that has none of its own -- a deleted `Job`, a blank `Job.icon` (the model's
#: default) or an icon name the library does not carry. Without it the template had only the slug to draw,
#: and "card-shark" at 26px overflows a 64px well. A briefcase, because whatever the square was, it was a job.
_FALLBACK_JOB_GLYPH = JOB_ICON_PATHS['briefcase']

#: Job Coverage board geometry, in px: the card's content width, the gap between covers, the least gap
#: between shelves, and the widest a cover gets. A shelf is THREE ROWS deep (see _challenge_card_jobs.html),
#: so the cover's ceiling is set by the card's HEIGHT: 78x104 leaves the rows ~20px of air above and below once
#: the header and the plaque have theirs (81 fitted, touching). Width allows far more on the designed shape
#: (~101px), so height is what binds.
_BOARD_WIDTH = 1112
_COVER_GAP = 6
_SHELF_GAP_MIN = 16
_COVER_W_MAX = 78
_SHELF_ROWS = 3

#: The plaque's sizes, per type. A-Z keeps the plaque it shipped with; Job Coverage's is slimmer, because its
#: shelves are three covers deep and the height they need comes out of the header and the plaque.
_PLAQUE = {
    'full': {'avatar': 72, 'pad': '20px 26px', 'name': 32, 'line': 18, 'num': 44, 'num_sub': 25,
             'date': 27, 'label_gap': 8, 'stat_gap': 40},
    'slim': {'avatar': 60, 'pad': '14px 24px', 'name': 28, 'line': 16, 'num': 38, 'num_sub': 22,
             'date': 24, 'label_gap': 6, 'stat_gap': 32},
}

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

    `cache_images=True` for the PNG (see the module docstring).

    ONE SHELL, A BOARD PER TYPE. The header, the brand and the plaque are the same on every card; the board
    is what differs, so the context carries `kind` and exactly one of:

    - `rows` for A-Z: the alphabet in two rows of thirteen, A-M over N-Z.
    - `shelves` for Job Coverage: one per discipline, in the radar's order, each with its colour, glyph and
      tally. Taken from `slot_render.slot_groups`, the same grouping the live board draws, so a square sits
      in the same discipline on the card as on the page.
    - `months` for the Plat Calendar: the page's year overview, twelve rows of up to 31 days. Taken from
      `calendar_render.calendar_groups`, the page's own builder, so a day filled on the card is filled on
      the page -- shovelware-free, the one lens there is.

    `stats` IS THE PLAQUE'S NUMBERS, built here per type rather than branched on in the template: each type
    counts different things (letters, jobs and job XP, days and struck months), and four `{% if kind %}`
    blocks in one row was the shape that list replaced.
    """
    profile = challenge.profile
    kind = challenge.challenge_type
    if kind == CHALLENGE_TYPE_CALENDAR:
        return _calendar_context(challenge, profile, cache_images=cache_images)
    groups = slot_groups(challenge)
    is_jobs = kind == CHALLENGE_TYPE_JOBS

    shelves = [{
        'label': group['label'],
        'colour': DISCIPLINE_COLOURS.get(group['slug'], _NO_DISCIPLINE_COLOUR),
        'glyph': JOB_ICON_PATHS.get(group['icon'], ''),
        'done': group['done'],
        'total': group['total'],
        'squares': [_square(card) for card in group['cards']],
    } for group in groups]
    squares = [square for shelf in shelves for square in shelf['squares']]

    avatar = profile.avatar_url or ''
    if cache_images:
        (avatar,), covers = _cached([avatar], [sq['cover'] for sq in squares])
        for square, cover in zip(squares, covers):
            square['cover'] = cover

    context = _shell(challenge, profile, avatar, plaque='slim' if is_jobs else 'full')
    context['assigned_count'] = challenge.filled_count - challenge.completed_count
    count = {'num': challenge.completed_count, 'of': challenge.total_slots,
             'label': 'Jobs' if is_jobs else 'Letters'}
    if is_jobs:
        context['shelves'] = shelves
        context['board'] = _shelf_geometry(shelves)
        # WHAT THE RUN HAS PAID, never what it is owed: XP still waiting on a Claim button is not the hunter's
        # yet, and a card claiming it would disagree with Career until they press it. Read from
        # `rewards.summary`, the run page's own reward panel, so the two cannot quote different figures.
        xp = {'num': rewards.summary(challenge)['paid_xp'], 'label': 'Job XP'}
        context['stats'] = [count, xp, _days_stat(challenge), _date_stat(challenge)]
    else:
        half = (len(squares) + 1) // 2
        context['rows'] = [squares[:half], squares[half:]]
        context['left'] = challenge.total_slots - challenge.completed_count     # the A-Z subline's "to go"
        context['stats'] = [count, _days_stat(challenge), _date_stat(challenge)]
    return context


def _shell(challenge, profile, avatar, *, plaque):
    """What every card carries whatever its board: who, the run's state, the title, the plaque's sizes.

    THE TITLE IS READ FOR EVERY RUN, finished or not, because the Calendar's ladder is climbed DURING a run
    (Calendar Marker at 50 days, Keeper at 100, ...). The two contract-atom types grant theirs only on the
    finish, so for them an unfinished run simply has none and the line drops.
    """
    return {
        'kind': challenge.challenge_type,
        'username': profile.display_psn_username or profile.psn_username,
        'mark': mark_style(profile.display_mark),
        'avatar_image': avatar,
        'is_complete': challenge.is_complete,
        'completed_count': challenge.completed_count,
        'title': rewards.granted_titles_for([challenge]).get(challenge.pk),
        'plaque': _PLAQUE[plaque],
    }


def _days_stat(challenge):
    """How long the run took, or has been going. `Days in` on a live run, so it never reads as a total."""
    days = _days(challenge)
    label = 'Day' if days == 1 else 'Days'
    return {'num': days, 'label': label if challenge.is_complete else label + ' in'}


def _date_stat(challenge):
    """The finish date, or the start date on a live run. Formatted in the ACTIVE timezone -- the hunter's,
    which `TimezoneMiddleware` sets for the request -- exactly as the template's `|date` filter would."""
    when = challenge.completed_at if challenge.is_complete else challenge.created_at
    return {'text': dateformat.format(timezone.localtime(when), 'M j, Y'),
            'label': 'Finished' if challenge.is_complete else 'Started'}


def _calendar_context(challenge, profile, *, cache_images):
    """The Plat Calendar card: the page's year overview, and the plaque counting days and struck months.

    NO COVERS, by design: a day carries no art on the page either (owner, 2026-10-03), so the only image to
    cache is the avatar. `calendar_groups` is one query over the run's 365 rows.

    "DAYS IN" IS NOT ON THIS PLAQUE, unlike the other two. A card whose headline is "164/365 days" beside
    "87 days in" reads as two different day counts arguing; the second figure here is the struck months.
    """
    groups = calendar_render.calendar_groups(challenge)
    totals = calendar_render.totals_for(groups)
    avatar = profile.avatar_url or ''
    if cache_images:
        (avatar,), = _cached([avatar])

    context = _shell(challenge, profile, avatar, plaque='slim')
    context['months'] = [{
        'abbr': group['slug'].upper(),
        'hue': MONTH_HUES[index],
        'done': group['done'],
        'total': group['total'],
        'struck': group['is_struck'],
        'filled': [cell['filled'] for cell in group['cards']],
    } for index, group in enumerate(groups)]
    context['day_numbers'] = range(1, 32)
    context['stats'] = [
        {'num': totals['done'], 'of': challenge.total_slots, 'label': 'Days'},
        # TWELVE, NOT `len(groups)`: a run with no day rows gets no groups, and an `of` of 0 is dropped by
        # the template, so the plaque read "0 MONTHS" rather than 0/12.
        {'num': totals['struck'], 'of': len(CALENDAR_MONTH_DAYS), 'label': 'Months'},
        _date_stat(challenge),
    ]
    return context


def _shelf_geometry(shelves):
    """Cover and shelf sizes that FIT, whatever the catalogue did to the run.

    Designed at five shelves of five, each TWO covers across and THREE deep, the sixth cell the tally, covers
    78x104 -- near the A-Z card's 80x107. (It was three across and two deep at 64x85 first, and the
    owner's verdict was that the art was too small to make out, which is the point of the card. Fifteen
    columns was the cost; ten is what standing the shelves on end buys.)

    Two catalogue changes break the designed shape, and both are real: deleting a `Job` (its square lands on
    a shelf of its own, so SIX shelves) and a staff edit to `Job.discipline` (a shelf of SIX, which needs a
    seventh cell for its tally). So the rows stay at three, the columns grow to what the biggest shelf needs,
    and the covers shrink until every shelf fits the width. On the designed shape it returns the designed
    numbers.
    """
    count = max(len(shelves), 1)
    cells = max((len(shelf['squares']) for shelf in shelves), default=0) + 1     # the tally takes a cell
    columns = max(2, -(-cells // _SHELF_ROWS))                                  # ceil
    room = (_BOARD_WIDTH - (count - 1) * _SHELF_GAP_MIN) // count
    cover_w = min(_COVER_W_MAX, (room - (columns - 1) * _COVER_GAP) // columns)
    scale = cover_w / _COVER_W_MAX
    return {
        'cover_w': cover_w,
        'cover_h': round(cover_w * 4 / 3),
        'shelf_w': columns * cover_w + (columns - 1) * _COVER_GAP,
        'mark_px': round(26 * scale),
        'well_px': round(33 * scale),
        'tally_px': round(36 * scale),
        'tally_sub_px': round(22 * scale),
    }


def _square(card):
    """One square of the board, for either type. A job square also carries its glyph and discipline colour,
    which is what stands in for the letter."""
    game = card['cover']    # None on an empty square: `slot_cards` keys covers by the slot's contract
    job = card['job']
    # A JOB KEY IS NEVER ONE CHARACTER and a letter always is -- the same test `slot_render.label_for_key`
    # makes, and the only one that still works when the square's `Job` row (and so its atom) is gone.
    is_job = len(card['key']) > 1
    return {
        'key': card['key'],
        'state': DONE if card['is_completed'] else ASSIGNED if card['is_filled'] else OPEN,
        # The SMALL variant (180x256): a square is at most 80x107, and `cover_big` would download about twice
        # the bytes for the renderer to shrink anyway. The Hall of Fame board uses it for the same reason.
        'cover': game.display_image_url_small if game else '',
        # The generic PS placeholder is not art and must not be cropped -- the same switch the live board
        # makes with `.pp-csq__art--icon`.
        'cover_is_art': bool(game and game.has_cover_art),
        # A job square ALWAYS has a glyph (the fallback covers a deleted or icon-less job), so the template
        # never has to draw its slug. An A-Z square has none, and draws its letter.
        'is_job': is_job,
        'glyph': ((job and JOB_ICON_PATHS.get(job['icon'])) or _FALLBACK_JOB_GLYPH) if is_job else '',
        'colour': ((job and DISCIPLINE_COLOURS.get(job['disc_slug'])) or _NO_DISCIPLINE_COLOUR) if is_job else '',
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
    the card prints its date in that zone (`_date_stat`), so counting UTC days put "Started Oct 1" beside a
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
