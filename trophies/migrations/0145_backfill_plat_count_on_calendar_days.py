"""
Data migration: backfill plat_count on existing CalendarChallengeDay rows.

For each active calendar challenge, counts all non-shovelware, non-hidden
platinum EarnedTrophies per (month, day) in the user's timezone, then sets
plat_count on the corresponding CalendarChallengeDay rows.
"""
from collections import Counter

import pytz
from django.db import migrations


def backfill_plat_counts(apps, schema_editor):
    Challenge = apps.get_model('trophies', 'Challenge')
    CalendarChallengeDay = apps.get_model('trophies', 'CalendarChallengeDay')
    EarnedTrophy = apps.get_model('trophies', 'EarnedTrophy')

    calendar_challenges = Challenge.objects.filter(
        challenge_type='calendar', is_deleted=False,
    ).select_related('profile__user')

    for challenge in calendar_challenges:
        # Resolve user timezone
        try:
            tz_name = challenge.profile.user.user_timezone if challenge.profile.user else 'UTC'
            user_tz = pytz.timezone(tz_name or 'UTC')
        except (pytz.exceptions.UnknownTimeZoneError, AttributeError):
            user_tz = pytz.UTC

        # Count all platinums per (month, day) in user's timezone
        platinum_dts = EarnedTrophy.objects.filter(
            profile=challenge.profile,
            trophy__trophy_type='platinum',
            earned=True,
            earned_date_time__isnull=False,
            trophy__game__is_shovelware=False,
            user_hidden=False,
        ).values_list('earned_date_time', flat=True)

        plat_counter = Counter()
        for dt in platinum_dts:
            local = dt.astimezone(user_tz)
            key = (local.month, local.day)
            if key != (2, 29):
                plat_counter[key] += 1

        if not plat_counter:
            continue

        # Update CalendarChallengeDay rows in bulk
        days = CalendarChallengeDay.objects.filter(challenge=challenge)
        to_update = []
        for day_obj in days:
            count = plat_counter.get((day_obj.month, day_obj.day), 0)
            if count != day_obj.plat_count:
                day_obj.plat_count = count
                to_update.append(day_obj)

        if to_update:
            CalendarChallengeDay.objects.bulk_update(to_update, ['plat_count'], batch_size=500)


class Migration(migrations.Migration):

    dependencies = [
        ('trophies', '0144_add_plat_count_to_calendarchallengeday'),
    ]

    operations = [
        migrations.RunPython(backfill_plat_counts, migrations.RunPython.noop),
    ]
