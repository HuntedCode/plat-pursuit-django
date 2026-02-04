# Generated manually for data migration
# Run this AFTER the schema migration (0123_gamification_models)

from django.db import migrations


def populate_stat_types(apps, schema_editor):
    """Populate initial StatType records."""
    StatType = apps.get_model('trophies', 'StatType')

    StatType.objects.get_or_create(
        slug='badge_xp',
        defaults={
            'name': 'Badge XP',
            'description': 'Experience points earned from badge progress and completion',
            'icon': '⚡',
            'color': '#FFD700',
            'is_active': True,
            'display_order': 1,
        }
    )


def populate_gamification(apps, schema_editor):
    """
    Populate ProfileGamification for all existing users with badge progress.

    This is a one-time migration to backfill denormalized data.
    Uses the same calculation logic as compute_badge_xp_leaderboard().
    """
    Profile = apps.get_model('trophies', 'Profile')
    UserBadgeProgress = apps.get_model('trophies', 'UserBadgeProgress')
    UserBadge = apps.get_model('trophies', 'UserBadge')
    ProfileGamification = apps.get_model('trophies', 'ProfileGamification')

    # XP constants (duplicated since we can't import from non-migration code)
    TIER_XP = {1: 250, 2: 75, 3: 250, 4: 75}
    BADGE_TIER_XP = 3000

    # Get all profiles with any badge progress
    profiles = Profile.objects.filter(badge_progress__isnull=False).distinct()

    created_count = 0
    for profile in profiles.iterator(chunk_size=100):
        # Calculate progress XP
        progress_records = UserBadgeProgress.objects.filter(
            profile=profile
        ).select_related('badge')

        series_xp = {}
        total_progress_xp = 0

        for prog in progress_records:
            tier_xp = TIER_XP.get(prog.badge.tier, 0)
            xp = prog.completed_concepts * tier_xp
            series_slug = prog.badge.series_slug

            if series_slug:
                if series_slug not in series_xp:
                    series_xp[series_slug] = 0
                series_xp[series_slug] += xp
            total_progress_xp += xp

        # Add badge completion bonuses
        earned_badges = UserBadge.objects.filter(profile=profile).select_related('badge')
        total_badges = 0

        for user_badge in earned_badges:
            series_slug = user_badge.badge.series_slug
            if series_slug:
                if series_slug not in series_xp:
                    series_xp[series_slug] = 0
                series_xp[series_slug] += BADGE_TIER_XP
            total_badges += 1

        total_xp = total_progress_xp + (total_badges * BADGE_TIER_XP)

        # Only create if there's actual XP or badges
        if total_xp > 0 or total_badges > 0:
            ProfileGamification.objects.create(
                profile=profile,
                total_badge_xp=total_xp,
                series_badge_xp=series_xp,
                total_badges_earned=total_badges,
            )
            created_count += 1

    print(f"Created ProfileGamification for {created_count} profiles")


def reverse_gamification(apps, schema_editor):
    """Reverse migration - delete all ProfileGamification records."""
    ProfileGamification = apps.get_model('trophies', 'ProfileGamification')
    ProfileGamification.objects.all().delete()


def reverse_stat_types(apps, schema_editor):
    """Reverse migration - delete badge_xp StatType."""
    StatType = apps.get_model('trophies', 'StatType')
    StatType.objects.filter(slug='badge_xp').delete()


class Migration(migrations.Migration):

    dependencies = [
        # This should reference your schema migration
        # Update this to match your actual migration name after running makemigrations
        ('trophies', '0123_stattype_profilegamification_stagestatvalue'),
    ]

    operations = [
        migrations.RunPython(populate_stat_types, reverse_stat_types),
        migrations.RunPython(populate_gamification, reverse_gamification),
    ]
