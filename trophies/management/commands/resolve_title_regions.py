"""Walk every title ID on a trophy list through the storefronts, to find where each is sold.

    python manage.py resolve_title_regions --status      # progress only
    python manage.py resolve_title_regions --limit 50    # one trial batch, no chain
    python manage.py resolve_title_regions               # the whole walk, most-played lists first

Each title ID is one `resolve_title_id_region` job asking all six storefronts (US, GB, JP, HK, KR, CN)
whether they sell it; the answer is saved on TitleID and the lists carrying it are rebuilt. Jobs go to
the BULK queue, below every hunter's sync. The full walk is a refill chain: a batch, then a refill job
behind it that queues the next batch, so the queue stays short (Redis memory, and the Admin Hub's
backlog tile) however large the walk is. The chain stops itself when a batch walks nothing (a PSN
outage) or every remaining title ID is cooling down after a failed lookup; the nightly re-runs this
command, so a stopped walk resumes on its own. Only one chain runs at a time. Safe to re-run at any
time: walked title IDs are skipped, and with nothing left it queues nothing.
"""
from django.core.management.base import BaseCommand

from trophies.services import region_service

DEFAULT_BATCH = 500


class Command(BaseCommand):
    help = "Queue the storefront walk that finds where each title ID on a trophy list is sold."

    def add_arguments(self, parser):
        parser.add_argument('--status', action='store_true', help='Print progress and queue nothing.')
        parser.add_argument('--limit', type=int,
                            help='Queue just this many title IDs, once, with no refill chain.')
        parser.add_argument('--batch', type=int, default=DEFAULT_BATCH,
                            help=f'Title IDs per refill batch for the full walk (default {DEFAULT_BATCH}).')

    def handle(self, *args, **opts):
        remaining = region_service.unwalked_count()
        if opts['status'] or not remaining:
            self.stdout.write(f'{remaining:,} title IDs on lists still to walk.')
            return
        if opts['limit'] is not None:
            queued = sum(region_service.queue_lookup(tid, plat, np_id, queue='bulk_priority')
                         for tid, plat, np_id in region_service.walk_candidates(max(opts['limit'], 0)))
            self.stdout.write(f'Queued {queued:,} title IDs (no chain); {remaining:,} were still to walk.')
            return
        if not region_service.start_walk(opts['batch']):
            self.stdout.write(f'A walk is already running ({remaining:,} title IDs to go); check progress '
                              f'with --status.')
            return
        self.stdout.write(f'Started the walk: {remaining:,} title IDs to go, {opts["batch"]:,} per batch, '
                          f'on the bulk queue. Check progress with --status.')
