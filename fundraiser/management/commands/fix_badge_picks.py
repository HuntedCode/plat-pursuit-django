"""
Retroactively fix badge_picks_earned for users with multiple donations.

The original calculation computed picks per-donation in isolation:
    floor(amount / 10)
This lost remainder dollars across donations. A $25 + $5 sequence gave
2 picks instead of 3 ($30 cumulative should yield floor(30/10) = 3).

This command replays the cumulative calculation in completed_at order
and reports (or fixes) any discrepancies.

Usage:
    python manage.py fix_badge_picks             # dry-run (default)
    python manage.py fix_badge_picks --apply      # write corrections
"""
import math
from collections import defaultdict
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db.models import Sum

from fundraiser.models import Donation, Fundraiser


class Command(BaseCommand):
    help = "Fix badge_picks_earned for users affected by the per-donation calculation bug."

    def add_arguments(self, parser):
        parser.add_argument(
            '--apply',
            action='store_true',
            default=False,
            help='Apply corrections. Without this flag, only a dry-run report is shown.',
        )

    def handle(self, *args, **options):
        apply = options['apply']
        mode = 'APPLY' if apply else 'DRY-RUN'
        self.stdout.write(f"\n=== fix_badge_picks ({mode}) ===\n")

        # Find all badge_artwork fundraisers
        fundraisers = Fundraiser.objects.filter(campaign_type='badge_artwork')
        if not fundraisers.exists():
            self.stdout.write("No badge_artwork fundraisers found.")
            return

        total_fixed = 0

        for fundraiser in fundraisers:
            self.stdout.write(f"\nFundraiser: {fundraiser.name} (#{fundraiser.id})")

            # Get all completed donations grouped by user
            donations = (
                Donation.objects
                .filter(fundraiser=fundraiser, status='completed', user__isnull=False)
                .order_by('user_id', 'completed_at')
                .select_related('user')
            )

            # Group by user
            user_donations = defaultdict(list)
            for d in donations:
                user_donations[d.user_id].append(d)

            for user_id, user_dons in user_donations.items():
                if len(user_dons) < 2:
                    continue

                cumulative_amount = Decimal('0')
                cumulative_picks_assigned = 0
                fixes = []

                for d in user_dons:
                    cumulative_amount += d.amount
                    correct_picks = math.floor(cumulative_amount / Fundraiser.BADGE_PICK_DIVISOR) - cumulative_picks_assigned

                    if d.badge_picks_earned != correct_picks:
                        # Never reduce below picks already used
                        safe_picks = max(correct_picks, d.badge_picks_used)
                        fixes.append((d, d.badge_picks_earned, safe_picks))
                        cumulative_picks_assigned += safe_picks
                    else:
                        cumulative_picks_assigned += d.badge_picks_earned

                if fixes:
                    username = user_dons[0].user.username if user_dons[0].user else f"user#{user_id}"
                    self.stdout.write(
                        f"\n  User: {username} (id={user_id}) "
                        f"- {len(user_dons)} donations, ${cumulative_amount} total"
                    )
                    for d, old_picks, new_picks in fixes:
                        delta = new_picks - old_picks
                        self.stdout.write(
                            f"    Donation #{d.id} (${d.amount}): "
                            f"{old_picks} -> {new_picks} picks (delta: +{delta})"
                        )
                        if apply:
                            Donation.objects.filter(pk=d.pk).update(badge_picks_earned=new_picks)
                            self.stdout.write(f"      APPLIED")
                        total_fixed += 1

        if total_fixed == 0:
            self.stdout.write("\nNo discrepancies found.")
        else:
            action = "Fixed" if apply else "Would fix"
            self.stdout.write(f"\n{action} {total_fixed} donation(s).")

        self.stdout.write("")
