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

from trophies.models import Contract, Job

logger = logging.getLogger(__name__)

EMBED_COLOR = 0x003791          # Platinum brand blue, same as the trophy tracker's default
BOARD_URL = '/career/?tab=contracts&new=1'   # lands on the board with Latest already applied

#: Discord hard-caps an embed description at 4096 characters and we want to stay well clear of it,
#: so a very large wave lists its biggest jobs and counts the rest. Chosen over truncating the
#: title list inside each job: "and 40 more" under a job someone follows is a worse read than a
#: complete picture of the jobs, which is the grouping the post exists to give.
MAX_JOB_LINES = 12
#: Per job, before the same treatment applies to its titles.
MAX_TITLES_PER_JOB = 6


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
    lines = [
        f"**{n} new contract{'' if n == 1 else 's'}** just hit the Job Board.",
        '',
    ]
    for job_name, titles in list(groups.items())[:MAX_JOB_LINES]:
        shown = titles[:MAX_TITLES_PER_JOB]
        extra = len(titles) - len(shown)
        tail = f" *and {extra} more*" if extra else ''
        lines.append(f"**{job_name}** — {', '.join(shown)}{tail}")
    hidden_jobs = len(groups) - MAX_JOB_LINES
    if hidden_jobs > 0:
        lines.append(f"*…and {hidden_jobs} more job{'' if hidden_jobs == 1 else 's'}.*")

    lines += ['', f"[See them on your board]({settings.SITE_URL}{BOARD_URL})"]

    return {'embeds': [{
        'title': '📋 New Contracts',
        'description': '\n'.join(lines),
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
