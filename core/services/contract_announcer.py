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

Grouped by JOB rather than listed flat: twenty contracts as twenty title lines is a wall nobody
reads, and the thing a hunter actually wants to know is which of their jobs just gained work.
"""
import logging

from django.conf import settings
from django.db.models import Prefetch
from django.utils import timezone

from trophies.discord_utils.discord_notifications import escape_md
from trophies.models import Contract, Job
from trophies.services.contracts_service import new_contract_cutoff

logger = logging.getLogger(__name__)

EMBED_COLOR = 0x003791          # Platinum brand blue, same as the trophy tracker's default
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

#: A very large wave lists its biggest jobs and counts the rest. Chosen over truncating the title
#: list inside each job: "and 40 more" under a job someone follows is a worse read than a complete
#: picture of the jobs, which is the grouping the post exists to give.
MAX_JOB_LINES = 12
#: Per job, before the same treatment applies to its titles.
MAX_TITLES_PER_JOB = 6

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


def _by_job(contracts):
    """{job_name: [contract names]}, biggest group first. A contract feeding several jobs appears
    under each of them -- that IS the fact being reported (one game levelling three jobs), and
    picking a single 'primary' job would invent a hierarchy the model does not have."""
    groups = {}
    for c in contracts:
        for job in c.jobs.all():
            groups.setdefault(job.name, []).append(c.name)
    return dict(sorted(groups.items(), key=lambda kv: (-len(kv[1]), kv[0])))


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
    groups = _by_job(contracts)
    # Only filter the board to Latest when the whole wave is still inside that window (see
    # BOARD_URL_LATEST). Every contract here has a stamp -- pending_contracts() requires one.
    cutoff = new_contract_cutoff()
    board = BOARD_URL_LATEST if all(c.went_live_at >= cutoff for c in contracts) else BOARD_URL
    head = f"**{n} new contract{'' if n == 1 else 's'}** just hit the Job Board."
    lines = [head, '']
    used = len(head) + 1

    # Take job lines while they FIT, not just while they are under MAX_JOB_LINES. Whatever does not
    # fit rolls into the same "and N more jobs" tail that the line cap already produces, so an
    # oversized wave degrades into a shorter post instead of an unpostable one.
    shown_jobs = 0
    for job_name, titles in list(groups.items())[:MAX_JOB_LINES]:
        shown = titles[:MAX_TITLES_PER_JOB]
        extra = len(titles) - len(shown)
        tail = f" *and {extra} more*" if extra else ''
        line = f"**{escape_markdown(job_name)}** — {', '.join(escape_markdown(t) for t in shown)}{tail}"
        if used + len(line) + 1 > _BUDGET:
            break
        lines.append(line)
        used += len(line) + 1
        shown_jobs += 1

    hidden_jobs = len(groups) - shown_jobs
    if hidden_jobs > 0:
        lines.append(f"*…and {hidden_jobs} more job{'' if hidden_jobs == 1 else 's'}.*")

    lines += ['', f"[See them on your board]({settings.SITE_URL}{board})"]
    description = '\n'.join(lines)

    # Belt and braces. The budget above is computed from the pieces; this measures the result, so a
    # future edit to the header, tail or link cannot quietly reintroduce a 400.
    if len(description) > DISCORD_DESCRIPTION_LIMIT:
        logger.error("Contract announcement description was %d chars; falling back to the summary.",
                     len(description))
        description = (f"{head}\n\n[See them on your board]"
                       f"({settings.SITE_URL}{BOARD_URL})")

    return {'embeds': [{
        'title': '📋 New Contracts',
        'description': description,
        'color': EMBED_COLOR,
        'footer': {'text': 'Contracts are curated | Powered by Plat Pursuit'},
    }]}


def mark_announced(contracts, when=None):
    """Stamp a wave as announced. Called ONLY after a confirmed 2xx, so a failed post leaves the
    whole wave pending for the next run rather than silently swallowing it."""
    ids = [c.pk for c in contracts]
    if not ids:
        return 0
    return Contract.objects.filter(pk__in=ids).update(announced_at=when or timezone.now())
