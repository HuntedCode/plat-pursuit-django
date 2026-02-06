"""
Management command to audit ProfileGamification XP values.

Compares stored denormalized values against recalculated totals from
UserBadgeProgress and UserBadge records.

Usage:
    python manage.py audit_profile_gamification
    python manage.py audit_profile_gamification --profile "username"
    python manage.py audit_profile_gamification --fix --verbose
"""
from django.core.management.base import BaseCommand
from django.contrib.messages import constants as messages
from trophies.models import Profile, ProfileGamification
from trophies.services.xp_service import calculate_total_xp
import logging

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Audit ProfileGamification XP values against recalculated totals'

    def add_arguments(self, parser):
        parser.add_argument(
            '--fix',
            action='store_true',
            help='Fix discrepancies by updating ProfileGamification records',
        )
        parser.add_argument(
            '--profile',
            type=str,
            help='Audit specific profile by PSN username',
        )
        parser.add_argument(
            '--verbose',
            action='store_true',
            help='Show detailed output for each profile (including matches)',
        )

    def handle(self, *args, **options):
        fix_mode = options['fix']
        profile_username = options.get('profile')
        verbose = options['verbose']

        # Get profiles to audit
        if profile_username:
            profiles = Profile.objects.filter(psn_username=profile_username)
            if not profiles.exists():
                self.stdout.write(
                    self.style.ERROR(f'Profile not found: {profile_username}')
                )
                return
        else:
            # Audit all profiles with gamification records
            profiles = Profile.objects.filter(gamification__isnull=False)

        total_audited = 0
        total_mismatches = 0
        total_fixed = 0

        self.stdout.write(
            self.style.MIGRATE_HEADING(
                f'\nAuditing ProfileGamification for {profiles.count()} profile(s)...\n'
            )
        )

        for profile in profiles.iterator(chunk_size=100):
            try:
                # Get stored values
                try:
                    gamification = ProfileGamification.objects.get(profile=profile)
                    stored_xp = gamification.total_badge_xp
                    stored_badges = gamification.total_badges_earned
                    stored_series_xp = gamification.series_badge_xp
                except ProfileGamification.DoesNotExist:
                    self.stdout.write(
                        self.style.WARNING(
                            f'No gamification record for {profile.psn_username}'
                        )
                    )
                    continue

                # Recalculate from source
                calculated_xp, calculated_series_xp, calculated_badges = calculate_total_xp(profile)

                # Check for discrepancies
                xp_mismatch = stored_xp != calculated_xp
                badges_mismatch = stored_badges != calculated_badges
                series_mismatch = stored_series_xp != calculated_series_xp

                if xp_mismatch or badges_mismatch or series_mismatch:
                    total_mismatches += 1

                    self.stdout.write(
                        self.style.ERROR(f'\n❌ Mismatch found: {profile.psn_username}')
                    )

                    if xp_mismatch:
                        diff = calculated_xp - stored_xp
                        self.stdout.write(
                            f'  XP: stored={stored_xp:,}, calculated={calculated_xp:,} '
                            f'(diff={diff:+,})'
                        )

                    if badges_mismatch:
                        diff = calculated_badges - stored_badges
                        self.stdout.write(
                            f'  Badges: stored={stored_badges}, calculated={calculated_badges} '
                            f'(diff={diff:+})'
                        )

                    if series_mismatch:
                        self.stdout.write(f'  Series XP mismatches:')
                        all_series = set(stored_series_xp.keys()) | set(calculated_series_xp.keys())
                        for series in sorted(all_series):
                            stored_val = stored_series_xp.get(series, 0)
                            calc_val = calculated_series_xp.get(series, 0)
                            if stored_val != calc_val:
                                diff = calc_val - stored_val
                                self.stdout.write(
                                    f'    {series}: stored={stored_val:,}, '
                                    f'calculated={calc_val:,} (diff={diff:+,})'
                                )

                    if fix_mode:
                        gamification.total_badge_xp = calculated_xp
                        gamification.series_badge_xp = calculated_series_xp
                        gamification.total_badges_earned = calculated_badges
                        gamification.save(
                            update_fields=[
                                'total_badge_xp',
                                'series_badge_xp',
                                'total_badges_earned'
                            ]
                        )
                        total_fixed += 1
                        self.stdout.write(self.style.SUCCESS(f'  ✓ Fixed'))

                elif verbose:
                    self.stdout.write(
                        self.style.SUCCESS(
                            f'✓ {profile.psn_username}: XP={stored_xp:,}, Badges={stored_badges}'
                        )
                    )

                total_audited += 1

            except Exception as e:
                self.stdout.write(
                    self.style.ERROR(f'Error auditing {profile.psn_username}: {e}')
                )
                logger.exception(f'Audit error for {profile.psn_username}')

        # Summary
        self.stdout.write('\n' + '=' * 60)
        self.stdout.write(
            self.style.MIGRATE_HEADING('\nAudit Summary:')
        )
        self.stdout.write(f'  Total profiles audited: {total_audited}')

        if total_mismatches > 0:
            self.stdout.write(
                self.style.ERROR(f'  Mismatches found: {total_mismatches}')
            )
        else:
            self.stdout.write(
                self.style.SUCCESS(f'  Mismatches found: {total_mismatches}')
            )

        if fix_mode:
            self.stdout.write(
                self.style.SUCCESS(f'  Profiles fixed: {total_fixed}')
            )
        else:
            if total_mismatches > 0:
                self.stdout.write(
                    self.style.WARNING(
                        f'\n  Run with --fix to correct discrepancies'
                    )
                )
