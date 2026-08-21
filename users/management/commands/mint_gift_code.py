"""Mint a comp code: a gift grant with no payment behind it.

The staff lever the gift primitive gives us for free -- giveaways, apologies, event prizes, and
Jeffrey handing out premium for whatever reason he likes. Identical to a paid grant from `issued`
onward, redeemed at /support/redeem/ like any other code.

    python manage.py mint_gift_code --tier patron --duration year --note "community event prize"
"""
from django.core.management.base import BaseCommand, CommandError

from users.constants import LADDER_SLUGS
from users.services.gift_service import GiftService


class Command(BaseCommand):
    help = "Mint an unpaid gift code (staff comp). Prints the code; nothing is emailed."

    def add_arguments(self, parser):
        parser.add_argument('--tier', required=True, choices=LADDER_SLUGS)
        parser.add_argument('--duration', required=True, choices=['month', 'year'])
        parser.add_argument('--note', default='', help='Why this comp exists; lands in admin.')
        parser.add_argument('--count', type=int, default=1, help='Mint several at once.')

    def handle(self, *args, **options):
        months = 12 if options['duration'] == 'year' else 1
        for _ in range(max(1, options['count'])):
            grant = GiftService.mint_comp(options['tier'], months, note=options['note'])
            self.stdout.write(self.style.SUCCESS(
                f"{grant.code}  ({options['tier']}, one {options['duration']})"
            ))
        self.stdout.write('Redeemable at /support/redeem/. No email was sent -- comps print only.')
