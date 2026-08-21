"""Close out gift grants whose time has run.

Scheduled DAILY as a Render cron job (see docs/guides/cron-jobs.md). There is no webhook for time
passing, so this sweep is the only thing that ever moves a grant `redeemed -> expired` -- and the
only thing that can flip the premium denorm back off for a grant-holder. Each grant reconciles
through `SubscriptionService.reconcile_premium`, so a user with a live subscription (or a second
grant) keeps premium: the sweep closes the grant, not the person.

Also voids `pending` rows older than seven days: abandoned checkouts, pure hygiene.
"""
from django.core.management.base import BaseCommand
from django.utils import timezone

from users.services.gift_service import GiftService


class Command(BaseCommand):
    help = "Expire redeemed premium gifts past their end date; void week-old abandoned checkouts."

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true',
                            help='Report what would change without changing it.')
        parser.add_argument(
            '--expire-now', metavar='CODE',
            help='DEV HELPER: force one code to expire this instant, then sweep. Lets the whole '
                 'expiry path be exercised end-to-end without waiting a month.',
        )

    def handle(self, *args, **options):
        if options['expire_now']:
            from users.models import PremiumGrant
            grant = PremiumGrant.objects.filter(code=options['expire_now'].strip().upper()).first()
            if grant is None or grant.status != 'redeemed':
                self.stderr.write(self.style.ERROR('No redeemed grant with that code.'))
                return
            grant.expires_at = timezone.now()
            grant.save(update_fields=['expires_at'])
            self.stdout.write(f'{grant.code} forced due.')

        counts = GiftService.expire_due_grants(dry_run=options['dry_run'])
        prefix = 'Would expire' if options['dry_run'] else 'Expired'
        self.stdout.write(self.style.SUCCESS(
            f"{prefix} {counts['expired']} grant(s) "
            f"({counts['still_premium']} holder(s) kept premium via another source); "
            f"voided {counts['voided_pending']} abandoned pending row(s)."
        ))
