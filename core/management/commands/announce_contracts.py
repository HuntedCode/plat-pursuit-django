"""Post newly published Contracts to Discord.

Runs on a schedule beside `process_contracts`, and is SILENT when there is nothing new -- most
runs, since publishing happens in bursts. A channel that receives a "0 new contracts" post every
day is a channel people mute.

Webhook posting is a SYNCHRONOUS direct POST rather than the trophies queue/worker, for the same
reasons `post_community_trophy_tracker` gives: a one-shot command's daemon worker thread dies
when the process exits, which can drop a message mid-flight, and a direct POST surfaces HTTP
errors as CommandError so a cron failure is visible in Render's UI instead of buried in a logger.

Idempotency is a COLUMN (`Contract.announced_at`), stamped only after a confirmed 2xx. So a
failed post leaves the whole wave pending for the next run, and a second run inside the same
window says nothing rather than re-posting.
"""
import json
import logging

import requests
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from core.services.contract_announcer import (
    build_announcement,
    mark_announced,
    pending_contracts,
)
from trophies.discord_utils.discord_notifications import PROXIES

logger = logging.getLogger(__name__)

#: Refuse a wave bigger than this without an explicit override. A legitimate publishing wave is
#: ten to thirty contracts; anything far past that means a bulk operation stamped `went_live_at`
#: on a backlog -- the cutover seed being the concrete case, where ~1,000 badge-derived contracts
#: are created live at once. The announcement would be a wall, and being un-postable is the only
#: way this command can protest before it has already happened. The operator's answer is either
#: --baseline (record the backlog as already known) or --force.
MAX_WAVE = 40


class Command(BaseCommand):
    help = "Post newly published contracts to Discord. Silent when there is nothing new."

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run', action='store_true',
            help='Print the embed JSON and what would be stamped; post nothing, write nothing.',
        )
        parser.add_argument(
            '--test-webhook', action='store_true',
            help=('Post to DISCORD_TEST_WEBHOOK_URL instead of the live channel, and do NOT stamp '
                  'announced_at -- so the same wave can be previewed repeatedly and still announces '
                  'for real later.'),
        )
        parser.add_argument(
            '--limit', type=int, default=None,
            help='Announce at most N contracts this run (oldest publish first). The rest stay '
                 'pending for the next run.',
        )
        parser.add_argument(
            '--baseline', action='store_true',
            help=('Stamp every pending contract as announced WITHOUT posting. The cutover step: '
                  'run it once after the launch seed so the first real announcement covers what '
                  'was published after launch, not the whole seeded catalogue.'),
        )
        parser.add_argument(
            '--force', action='store_true',
            help=f'Post a wave larger than {MAX_WAVE}, which is otherwise refused.',
        )

    def handle(self, *args, **opts):
        qs = pending_contracts()
        if opts['limit']:
            qs = qs[:opts['limit']]
        contracts = list(qs)

        if opts['baseline']:
            # Before the payload is built: baselining a wave too big to POST is precisely the case
            # this exists for, so it must not be gated behind the size check below.
            stamped = mark_announced(contracts)
            self.stdout.write(self.style.SUCCESS(
                f"Baselined {stamped} contract(s) as already announced. Nothing was posted."))
            return

        if len(contracts) > MAX_WAVE and not opts['force']:
            raise CommandError(
                f"{len(contracts)} contracts are pending, over the {MAX_WAVE} safety limit. That "
                f"usually means a bulk operation published a backlog (the launch seed is the "
                f"known case), and announcing it would post a wall. Run with --baseline to record "
                f"them as already known, --limit N to trickle, or --force if the wave is real.")

        payload = build_announcement(contracts)
        if payload is None:
            self.stdout.write("No new contracts to announce.")
            return

        if opts['dry_run']:
            self.stdout.write(self.style.WARNING(
                f"DRY RUN: would announce {len(contracts)} contract(s) and stamp announced_at."))
            self.stdout.write(json.dumps(payload, indent=2, ensure_ascii=False))
            return

        if opts['test_webhook']:
            url = getattr(settings, 'DISCORD_TEST_WEBHOOK_URL', None)
            if not url:
                raise CommandError(
                    "DISCORD_TEST_WEBHOOK_URL is not set. Configure it in your .env, or drop "
                    "--test-webhook to post to the live channel.")
            self._post(payload, url, label="Test webhook")
            # Deliberately NOT stamped: a preview that consumed the wave would mean the community
            # never heard about it, with nothing in the DB to show what went missing.
            self.stdout.write(self.style.SUCCESS(
                f"Preview of {len(contracts)} contract(s) sent to the test channel. "
                f"Nothing was stamped -- they will still announce for real."))
            return

        self._post(payload, settings.DISCORD_PLATINUM_WEBHOOK_URL, label="Contract announcement")
        stamped = mark_announced(contracts)
        self.stdout.write(self.style.SUCCESS(f"Announced and stamped {stamped} contract(s)."))

    def _post(self, payload, webhook_url, *, label="Webhook"):
        try:
            response = requests.post(webhook_url, json=payload, proxies=PROXIES, timeout=10)
        except requests.RequestException as e:
            logger.exception("%s direct POST raised", label)
            raise CommandError(f"{label} POST failed: {e}")
        if response.status_code >= 400:
            raise CommandError(
                f"{label} returned HTTP {response.status_code}: {response.text[:500]}")
        return response
