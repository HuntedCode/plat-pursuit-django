"""
Audit a user's calendar challenge against their platinum data.

Shows every earned platinum, whether it qualifies for the calendar,
and the specific reason for any exclusion. Cross-references with
actual CalendarChallengeDay rows to find mismatches.

Usage:
    python manage.py audit_calendar --username Jlowe
"""
import calendar as cal_module

from django.core.management.base import BaseCommand

from trophies.models import Challenge, EarnedTrophy, Profile
from trophies.services.challenge_service import _get_user_tz


SHOVELWARE_EXCLUDED = {'auto_flagged', 'manually_flagged'}


class Command(BaseCommand):
    help = "Audit a user's calendar challenge against their platinum trophy data"

    def add_arguments(self, parser):
        parser.add_argument(
            '--username', required=True,
            help='PSN username to audit',
        )

    def handle(self, *args, **options):
        username = options['username']

        # ── Profile ──────────────────────────────────────────────
        try:
            profile = Profile.objects.select_related('user').get(
                psn_username__iexact=username,
            )
        except Profile.DoesNotExist:
            self.stderr.write(self.style.ERROR(f'Profile not found: {username}'))
            return

        user_tz = _get_user_tz(profile)
        self.stdout.write(f'\n{"=" * 60}')
        self.stdout.write(self.style.SUCCESS(f'  Calendar Audit: {profile.psn_username}'))
        self.stdout.write(f'{"=" * 60}\n')
        self.stdout.write(f'PROFILE')
        self.stdout.write(f'  Timezone: {user_tz}\n')

        # ── Calendar Challenge(s) ────────────────────────────────
        challenges = Challenge.objects.filter(
            profile=profile, challenge_type='calendar',
        ).order_by('-created_at')

        if not challenges.exists():
            self.stdout.write(self.style.ERROR('  No calendar challenges found for this user.'))
            return

        self.stdout.write('CALENDAR CHALLENGE(S)')
        target_challenge = None
        for ch in challenges:
            status_parts = []
            if ch.is_deleted:
                status_parts.append('DELETED')
            if ch.is_complete:
                status_parts.append('COMPLETE')
            if not ch.is_deleted and not ch.is_complete:
                status_parts.append('active')
            status = ', '.join(status_parts)
            label = f'  ID: {ch.id}, Name: "{ch.name}", Status: {status} ({ch.filled_count}/{ch.total_items} filled)'
            if ch.is_deleted:
                self.stdout.write(self.style.WARNING(label))
            elif ch.is_complete:
                self.stdout.write(self.style.SUCCESS(label))
            else:
                self.stdout.write(label)
                if target_challenge is None:
                    target_challenge = ch

        # Fall back to most recent non-deleted challenge (even if complete)
        if target_challenge is None:
            target_challenge = challenges.filter(is_deleted=False).first()
        if target_challenge is None:
            self.stdout.write(self.style.ERROR('\n  No non-deleted calendar challenge found.'))
            return

        self.stdout.write(f'\n  Auditing: "{target_challenge.name}" (ID: {target_challenge.id})\n')

        # ── All earned platinums (minimal filter) ────────────────
        all_plats = list(
            EarnedTrophy.objects.filter(
                profile=profile,
                trophy__trophy_type='platinum',
                earned=True,
            ).select_related('trophy__game').order_by('earned_date_time')
        )

        self.stdout.write(f'PLATINUM TROPHIES')
        self.stdout.write(f'  Found {len(all_plats)} earned platinums (earned=True, trophy_type="platinum")\n')

        # ── Check each filter individually ───────────────────────
        qualifying = []
        excluded = []

        for et in all_plats:
            reasons = []
            game = et.trophy.game

            if et.earned_date_time is None:
                reasons.append('earned_date_time is NULL')
            if et.user_hidden:
                reasons.append('user_hidden=True')
            if game.shovelware_status in SHOVELWARE_EXCLUDED:
                reasons.append(f'shovelware: {game.shovelware_status}')

            if reasons:
                excluded.append((et, reasons))
            else:
                # Check Feb 29 skip (only for qualifying)
                local_dt = et.earned_date_time.astimezone(user_tz)
                if (local_dt.month, local_dt.day) == (2, 29):
                    excluded.append((et, ['falls on Feb 29 (skipped by calendar)']))
                else:
                    qualifying.append(et)

        self.stdout.write(f'  QUALIFYING (passes all calendar filters): {len(qualifying)}')
        self.stdout.write(f'  EXCLUDED: {len(excluded)}\n')

        # ── Excluded platinums ───────────────────────────────────
        if excluded:
            self.stdout.write(self.style.WARNING('  --- EXCLUDED PLATINUMS ---'))
            for i, (et, reasons) in enumerate(excluded, 1):
                game = et.trophy.game
                game_name = game.title_name if game else '(no game)'
                self.stdout.write(self.style.WARNING(
                    f'  #{i}: "{game_name}" (Game ID: {game.id if game else "N/A"})'
                ))
                edt = et.earned_date_time
                edt_str = edt.astimezone(user_tz).strftime('%Y-%m-%d %I:%M %p %Z') if edt else 'None'
                self.stdout.write(f'      earned_date_time: {edt_str}')
                self.stdout.write(f'      user_hidden: {et.user_hidden}')
                self.stdout.write(f'      shovelware_status: {game.shovelware_status if game else "N/A"}')
                for reason in reasons:
                    self.stdout.write(self.style.ERROR(f'      >> EXCLUDED: {reason}'))
                self.stdout.write('')

        # ── Qualifying platinums + calendar day mapping ──────────
        # Build (month, day) -> [platinums] mapping
        day_plats = {}  # (month, day) -> list of EarnedTrophy
        for et in qualifying:
            local_dt = et.earned_date_time.astimezone(user_tz)
            key = (local_dt.month, local_dt.day)
            day_plats.setdefault(key, []).append(et)

        # Fetch actual calendar day rows
        cal_days = {
            (d.month, d.day): d
            for d in target_challenge.calendar_days.select_related('game').all()
        }

        self.stdout.write('  --- QUALIFYING PLATINUMS ---')
        for i, et in enumerate(qualifying, 1):
            game = et.trophy.game
            game_name = game.title_name if game else '(no game)'
            local_dt = et.earned_date_time.astimezone(user_tz)
            key = (local_dt.month, local_dt.day)
            month_name = cal_module.month_abbr[key[0]]
            day_count = len(day_plats.get(key, []))

            cal_day = cal_days.get(key)
            if cal_day and cal_day.is_filled:
                status = self.style.SUCCESS('[FILLED]')
            else:
                status = self.style.ERROR('[NOT FILLED]')

            self.stdout.write(
                f'  #{i}: "{game_name}" -> {month_name} {key[1]} '
                f'(plats on day: {day_count}) {status}'
            )

        # ── Calendar day mismatches ──────────────────────────────
        self.stdout.write(f'\nCALENDAR DAY MISMATCHES')
        phantoms = []
        missing = []

        for key, day_obj in sorted(cal_days.items()):
            has_plats = key in day_plats
            is_filled = day_obj.is_filled

            if is_filled and not has_plats:
                month_name = cal_module.month_abbr[key[0]]
                game_name = day_obj.game.title_name if day_obj.game else '(unknown)'
                phantoms.append((key, game_name))
            elif has_plats and not is_filled:
                month_name = cal_module.month_abbr[key[0]]
                first_game = day_plats[key][0].trophy.game
                game_name = first_game.title_name if first_game else '(unknown)'
                missing.append((key, game_name))

        if phantoms:
            self.stdout.write(self.style.WARNING(f'  Phantom days (filled but no qualifying plat): {len(phantoms)}'))
            for key, game_name in phantoms:
                month_name = cal_module.month_abbr[key[0]]
                self.stdout.write(self.style.WARNING(
                    f'    {month_name} {key[1]}: filled with "{game_name}" but no qualifying platinum maps here'
                ))
        else:
            self.stdout.write(self.style.SUCCESS('  No phantom days found.'))

        if missing:
            self.stdout.write(self.style.ERROR(f'  Missing days (has qualifying plat, not filled): {len(missing)}'))
            for key, game_name in missing:
                month_name = cal_module.month_abbr[key[0]]
                self.stdout.write(self.style.ERROR(
                    f'    {month_name} {key[1]}: "{game_name}" qualifies but day is NOT filled'
                ))
        else:
            self.stdout.write(self.style.SUCCESS('  No missing days found.'))

        # ── Summary ──────────────────────────────────────────────
        filled_count = sum(1 for d in cal_days.values() if d.is_filled)
        self.stdout.write(f'\nSUMMARY')
        self.stdout.write(f'  Earned platinums (total): {len(all_plats)}')
        self.stdout.write(f'  Qualifying for calendar: {len(qualifying)}')
        self.stdout.write(f'  Excluded from calendar: {len(excluded)}')
        self.stdout.write(f'  Filled calendar days: {filled_count}')
        self.stdout.write(f'  Phantom days: {len(phantoms)}')
        self.stdout.write(f'  Missing days: {len(missing)}')
        self.stdout.write('')
