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

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from core.services.contract_announcer import (
    build_announcement,
    mark_announced,
    pending_contracts,
)
from trophies.discord_utils.discord_notifications import WebhookError, post_webhook_sync
from trophies.models import Contract

logger = logging.getLogger(__name__)

#: Refuse a wave bigger than this without an explicit override. A legitimate publishing wave is ten
#: to thirty contracts; far past that means a bulk operation stamped `went_live_at` on a backlog --
#: a staff sweep publishing hundreds of staged candidates in one changelist action being the live
#: case. Being un-postable is the only way this command can protest before the wall is already in
#: the channel. The operator's answer is --baseline (record the backlog as known) or --force.
#:
#: NOT the launch set, despite what the deploy notes first said. Those ~1,000 contracts went live
#: before `went_live_at` existed, so they carry NULL and `pending_contracts()` never sees them --
#: and the transition rule in `Contract.save()` keeps it that way when one is edited.
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
        limit = opts['limit']
        if limit is not None and limit < 1:
            # `--limit 0` used to fall through the falsy check below and post the ENTIRE wave: the
            # natural "do nothing" value doing the most destructive thing. `--limit -1` reached
            # Django's slice and raised a raw ValueError traceback, alone among this command's
            # inputs. Both are now the same clean refusal.
            raise CommandError(f"--limit must be 1 or more (got {limit}).")

        qs = pending_contracts()

        if opts['baseline']:
            # Ahead of the size check AND of materialising anything: baselining a wave too big to
            # POST is precisely the case this exists for. It needs no objects at all, so it stays a
            # single UPDATE -- --baseline is what you reach for after a bulk accident, which is the
            # worst moment to pull every pending row into memory first.
            # Through a pk subquery: Django refuses .update() on a sliced queryset, and refuses to
            # nest a sliced subquery on some backends, so the ids are resolved first.
            ids = list((qs[:limit] if limit else qs).values_list('pk', flat=True))
            stamped = Contract.objects.filter(pk__in=ids).update(announced_at=timezone.now())
            self.stdout.write(self.style.SUCCESS(
                f"Baselined {stamped} contract(s) as already announced. Nothing was posted."))
            return

        if limit:
            qs = qs[:limit]
        contracts = list(qs)

        # The read-only modes are exempt. Being unable to LOOK at an oversized wave is exactly
        # backwards -- inspecting it is how an operator decides between --baseline and --force --
        # and gating a preview behind the flag that otherwise means "post this for real to the live
        # channel" trains the wrong reflex. Neither writes or posts to the live channel.
        read_only = opts['dry_run'] or opts['test_webhook']
        if len(contracts) > MAX_WAVE and not opts['force'] and not read_only:
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
        """Thin wrapper: the POST itself is shared (see `post_webhook_sync`), this only translates
        its error into the CommandError a cron run needs to fail visibly."""
        try:
            return post_webhook_sync(payload, webhook_url, label=label)
        except WebhookError as e:
            raise CommandError(str(e))
