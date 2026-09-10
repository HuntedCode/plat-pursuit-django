"""The Discord announcement for newly published Contracts.

WHAT MAKES A CONTRACT ANNOUNCEABLE is `is_live=True` with a `went_live_at` stamp and no
`announced_at` -- three conditions that between them answer the question the pipeline raised:
what about the games that need admin review? Nothing. A staged or review-queued candidate is
`is_live=False`, so it has no `went_live_at`, so it cannot reach this module. Publishing is the
only act that makes a contract announceable, which is exactly the editorial gate we want: staff
decide when the community hears about a game, and the announcement follows the decision rather
than the pipeline run.

The LAUNCH SET is excluded by the same rule for free. Those ~1,000 badge-derived contracts carry
`went_live_at = NULL` by decision, so the first run after the cutover announces nothing rather
than dumping the whole catalogue into the channel.

SHAPE: a lead embed carrying the count, the reach-ranked headliners with cover art and the board
link, then ONE EMBED PER DISCIPLINE that gained work, tinted with that discipline's colour and
listing its jobs with counts.

The per-discipline split is not decoration. A Discord embed carries exactly one colour and cannot
tint individual lines, so grouping by discipline turns that limit into the labelling: the colour
says which discipline without spending a word or needing an emoji uploaded to the guild.

Counts rather than titles below the fold, deliberately. Twenty contracts as twenty title lines is a
wall nobody reads, and the titles worth acting on are already at the top with their art. The trade
is that a hunter following one job learns THAT it gained work, not which game did it -- the board
link is what answers that.
"""
import logging

from django.conf import settings
from django.db.models import Prefetch, Subquery, Value
from django.db.models.functions import Coalesce
from django.utils import timezone

from trophies.discord_utils.discord_notifications import escape_md
from trophies.models import Contract, Job
from trophies.services.contracts_service import new_contract_cutoff

logger = logging.getLogger(__name__)

EMBED_COLOR = 0x003791          # Platinum brand blue, same as the trophy tracker's default

#: THE FIVE DISCIPLINE COLOURS, as Discord integers.
#:
#: A Discord embed carries exactly ONE colour and cannot tint individual lines, so the post uses one
#: embed PER DISCIPLINE and lets that constraint do the grouping. This is the owner's design and it is
#: better than the alternatives: the colour is the label, so no emoji need uploading to the guild and
#: no icon has to survive Discord's markdown.
#:
#: CONVERTED FROM oklch, which is what `static/css/components/elements.css` declares and which Discord
#: cannot parse -- the same conversion the email work had to make for the brand colours. The oklch
#: source is recorded beside each value so a change to the stylesheet is DETECTABLE here rather than
#: silently leaving the channel a shade off the site. A test pins the pair.
DISCIPLINE_COLORS = {
    'combat':      (0xFC5855, 'oklch(0.68 0.20 25)'),
    'exploration': (0x59D38C, 'oklch(0.78 0.15 155)'),
    'mind':        (0x9C93FF, 'oklch(0.72 0.16 285)'),
    'heart':       (0xFF68A0, 'oklch(0.72 0.19 0)'),
    'finesse':     (0xFCB442, 'oklch(0.82 0.15 75)'),
}

#: Canonical order, matching `Job.DISCIPLINES` and the Career page's own bands. Deliberately NOT
#: biggest-first: this post recurs, and a reader who learns where Combat sits should find it there
#: every time. Ordering by size would reshuffle the message on every wave for no gain.
DISCIPLINE_ORDER = ('combat', 'exploration', 'mind', 'heart', 'finesse')

#: Named headliners; everything past them is counted as "and N more". Three is a judgement about the
#: lead embed's shape, not about art: every pick is titled whether or not a cover exists, and the one
#: image the embed can carry comes from the first pick that has one.
MAX_HIGHLIGHTS = 3

#: Discord caps the SUM of every embed's text in one message at 6000, on top of the per-description
#: limit below. The single-embed version never had to know this. It matters for the same reason the
#: description cap does: overrunning is a 400, the command raises, the wave is never stamped, and the
#: identical wave fails identically every night until someone runs --limit by hand. A fail-closed
#: retry is what makes a transient error safe and a deterministic one permanent.
DISCORD_TOTAL_LIMIT = 6000
#: Headroom under that, so the lead embed and its link always fit whatever the discipline blocks do.
_TOTAL_BUDGET = DISCORD_TOTAL_LIMIT - 512
#: Career deep-links on `?view=`, NOT `?tab=` (that is job detail's param). Getting it wrong does
#: not 404 or look broken -- the contracts panel renders correctly filtered but stays `hidden`, so
#: the reader lands on the Jobs tab and has to go hunting for what the post just told them about.
BOARD_URL = '/career/?view=contracts'
#: Added only when the whole wave is still inside the Latest window. `announced_at` and
#: NEW_CONTRACT_WINDOW_DAYS answer different questions and share no floor: a webhook misconfigured
#: for a fortnight, or a backlog trickled out with `--limit`, produces a legitimate post about
#: contracts that have already aged out. Filtering the board to Latest would then land the reader
#: on an EMPTY board -- the one place the post promised its contents would be.
BOARD_URL_LATEST = BOARD_URL + '&new=1'

#: Job lines inside ONE discipline block before the rest are counted.
#:
#: FIVE, because the catalogue has exactly five jobs per discipline (migration 0247) -- so this is a
#: backstop against the catalogue growing, not a working limit, and it is unreachable today. It was 12
#: while the cap applied across the WHOLE post; per discipline that could never fire, which is how a
#: test asserting its "and N more jobs" tail came to be unsatisfiable. Set to the real ceiling so the
#: number states a fact rather than implying a trimming that cannot happen.
MAX_JOB_LINES = 5

#: Discord's hard cap on an embed description. This is ENFORCED against the assembled string, not
#: approximated by the line caps above -- those bound the line COUNT, and PlayStation titles are
#: long enough that twelve jobs of six titles can clear 4096 well inside MAX_WAVE. Overrunning it
#: is not a cosmetic failure: Discord answers 400, the command raises, the wave is never stamped,
#: and the identical wave fails identically every night until someone runs --limit by hand. The
#: fail-closed retry that makes transient errors safe is exactly what makes a deterministic one
#: permanent, so the deterministic one must not be reachable.
DISCORD_DESCRIPTION_LIMIT = 4096
#: Headroom under the cap, so the closing link and tail always fit.
_BUDGET = DISCORD_DESCRIPTION_LIMIT - 256

#: Contract and job names are curator-authored free text going into a markdown description, so a
#: game legitimately titled "Sam & Max: *Beyond* Time and Space" should render as its own title and
#: a mistyped `[text](url)` should not become a live link in the channel. Imported rather than
#: written here: `discord_notifications` has had this escape set since the badge announcements, and
#: two definitions of "which characters are dangerous to Discord" is exactly one too many.
escape_markdown = escape_md


def pending_contracts():
    """Live, stamped, not yet announced -- oldest publish first, so a wave reads in the order it
    was published rather than alphabetically."""
    return (Contract.objects
            .filter(is_live=True, went_live_at__isnull=False, announced_at__isnull=True)
            .order_by('went_live_at', 'name')
            .prefetch_related(Prefetch('jobs', queryset=Job.objects.order_by('name'))))


def highlights(contracts, limit=MAX_HIGHLIGHTS):
    """The wave's headline games: the ones the most hunters here have actually played.

    `Game.played_count` is a denormalized, indexed count of profiles that have played the game --
    PP-specific, so it measures reach on THIS site rather than globally, which is the more useful
    figure for deciding what to lead a post to this community with.

    Reached through `_member_at_igdb`, the same correlated-subquery shape `annotated_contracts` uses,
    rather than a membership join: contract membership is DERIVED from the raw igdb id, so there is no
    join to make.

    ART IS NOT A REQUIREMENT TO BE A HIGHLIGHT. The first cut skipped contracts without a usable
    cover, reasoning that a highlight with nothing to look at wastes a slot -- but the slot is a TITLE
    LINE, and the image is one separate thing on the embed. On a wave whose games had no art that rule
    listed nothing at all: "5 new contracts … and 5 more", strictly worse than the post it replaced.
    So every pick is titled, and `art` is '' when there is none; the caller takes the image from the
    first pick that has one.
    """
    from trophies.models import Game               # local: keeps the module import-light
    from trophies.services.contracts_service import _member_at_igdb

    reach = Subquery(
        Game.objects.filter(**_member_at_igdb('concept__'))
        .order_by('-played_count').values('played_count')[:1]
    )
    ranked = (Contract.objects.filter(pk__in=[c.pk for c in contracts])
              .annotate(reach=Coalesce(reach, Value(0)))
              .order_by('-reach', 'went_live_at', 'name')
              .select_related())

    return [(contract, cover_url_for(contract)) for contract in ranked[:limit]]


def cover_url_for(contract):
    """The cover of the contract's most-played member game, or ''.

    Goes through `Game.display_image_url`, the single source of truth for the fallback chain
    (trusted IGDB cover -> PSN concept icon -> title image -> title icon). Reimplementing that chain
    inline is explicitly forbidden by CLAUDE.md, and it would be wrong here in a way that shows: a PSN
    fallback is square where an IGDB cover is 3:4, and Discord renders whatever it is given.

    `.defer(...raw_response)` is not decoration either -- that column is the ~30 KB IGDB blob that
    caused the May 2026 web-server OOM, and nothing in a cover URL reads it.
    """
    from trophies.models import Game
    from trophies.services.contracts_service import _member_gate

    game = (Game.objects
            .filter(**_member_gate('concept__'), concept__igdb_match__igdb_id=contract.igdb_id)
            .select_related('concept', 'concept__igdb_match')
            .defer('concept__igdb_match__raw_response')
            .order_by('-played_count')
            .first())
    if game is None:
        return ''
    url = game.display_image_url or ''
    # Discord fetches this itself, anonymously, so it has to be absolute and public. A relative
    # /media/ path renders as a broken embed rather than as no embed, which is worse.
    if url.startswith('/'):
        url = f"{settings.SITE_URL}{url}"
    return url if url.startswith('http') else ''


def _by_discipline(contracts):
    """{discipline: {job_name: contract_count}}, in canonical discipline order.

    A contract feeding jobs in three disciplines is counted under all three -- that IS the fact being
    reported (one game levelling work across three disciplines), and picking a single "primary" would
    invent a hierarchy the model does not have. The consequence is that the per-discipline counts sum
    to MORE than the wave total, which is why the header carries the authoritative number and the
    blocks answer a different question: which of your jobs just gained work.
    """
    groups = {}
    for contract in contracts:
        for job in contract.jobs.all():
            disc = groups.setdefault(job.discipline, {})
            disc[job.name] = disc.get(job.name, 0) + 1
    return {d: dict(sorted(groups[d].items(), key=lambda kv: (-kv[1], kv[0])))
            for d in DISCIPLINE_ORDER if d in groups}


def build_announcement(contracts):
    """The Discord webhook payload for a wave, or None when there is nothing to say.

    Returning None rather than an empty post is load-bearing: this runs on a schedule beside a
    pipeline that publishes in bursts, so most runs have nothing new. A channel that gets a
    "0 new contracts" post every day is a channel people mute.
    """
    contracts = list(contracts)
    if not contracts:
        return None

    n = len(contracts)
    # Only filter the board to Latest when the whole wave is still inside that window (see
    # BOARD_URL_LATEST). Every contract here has a stamp -- pending_contracts() requires one.
    cutoff = new_contract_cutoff()
    board = BOARD_URL_LATEST if all(c.went_live_at >= cutoff for c in contracts) else BOARD_URL
    link = f"[See them on your board]({settings.SITE_URL}{board})"

    # ── the lead embed: what landed, with the reach-ranked headliners ────────────────────────────
    picks = highlights(contracts)
    head = f"**{n} new contract{'' if n == 1 else 's'}** just hit the Job Board."
    lines = [head, '']
    lines += [f"**{escape_markdown(c.name)}**" for c, _art in picks]
    rest = n - len(picks)
    if rest > 0:
        lines.append(f"*…and {rest} more*")
    lines += ['', link]

    lead = {
        'title': '📋 New Contracts',
        'description': _capped('\n'.join(lines), fallback=f"{head}\n\n{link}"),
        'color': EMBED_COLOR,
        'footer': {'text': 'Contracts are curated | Powered by Plat Pursuit'},
    }
    # ONE image, so it belongs to the top-ranked highlight. Discord fetches this itself, from the
    # public game-art CDN -- nothing is proxied through us.
    art = next((url for _c, url in picks if url), '')
    if art:
        lead['image'] = {'url': art}

    embeds = [lead]
    used = _embed_len(lead)

    # ── one embed per discipline that gained work, canonical order ───────────────────────────────
    # Counts, not titles. The titles a reader can act on are already above with their art; repeating
    # every game under every job is the wall the previous version fought with two line caps, and it
    # is what the room in a Discord post is actually short of. The trade is real: a hunter following
    # one job sees THAT it gained work, not which game did it. The board link answers that.
    labels = dict(Job.DISCIPLINES)
    for disc, jobs in _by_discipline(contracts).items():
        colour, _oklch = DISCIPLINE_COLORS[disc]
        shown = list(jobs.items())[:MAX_JOB_LINES]
        job_lines = [
            f"**{escape_markdown(name)}** — {count} new contract{'' if count == 1 else 's'}"
            for name, count in shown
        ]
        hidden = len(jobs) - len(shown)
        if hidden > 0:
            job_lines.append(f"*…and {hidden} more job{'' if hidden == 1 else 's'}.*")

        block = {
            'title': labels.get(disc, disc.title()),
            'description': _capped('\n'.join(job_lines)),
            'color': colour,
        }
        # DEGRADE BY DROPPING WHOLE BLOCKS, never by overrunning. The per-description cap above and
        # this total are different limits (4096 vs 6000 across the message), and only the total can
        # be breached by a post whose every individual embed is legal.
        if used + _embed_len(block) > _TOTAL_BUDGET:
            logger.warning("Contract announcement hit the %d-char message budget; dropping the "
                           "remaining discipline blocks.", DISCORD_TOTAL_LIMIT)
            break
        embeds.append(block)
        used += _embed_len(block)

    return {'embeds': embeds}


def _embed_len(embed):
    """What Discord counts toward the 6000-per-message total: title + description + footer text."""
    return (len(embed.get('title', ''))
            + len(embed.get('description', ''))
            + len(embed.get('footer', {}).get('text', '')))


def _capped(description, fallback=None):
    """Belt and braces on the PER-DESCRIPTION limit.

    The line caps above bound the line COUNT; this measures the assembled result, so a future edit to
    a header, a tail or the link cannot quietly reintroduce a 400 that would strand the wave forever.
    """
    if len(description) <= DISCORD_DESCRIPTION_LIMIT:
        return description
    logger.error("Contract announcement description was %d chars; falling back.", len(description))
    if fallback is not None:
        return fallback
    return description[:DISCORD_DESCRIPTION_LIMIT - 1].rstrip() + '…'


def mark_announced(contracts, when=None):
    """Stamp a wave as announced. Called ONLY after a confirmed 2xx, so a failed post leaves the
    whole wave pending for the next run rather than silently swallowing it."""
    ids = [c.pk for c in contracts]
    if not ids:
        return 0
    return Contract.objects.filter(pk__in=ids).update(announced_at=when or timezone.now())
