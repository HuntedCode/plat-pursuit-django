from django.core.management.base import BaseCommand
from django.utils import timezone
from datetime import timedelta
from trophies.models import Profile
from trophies.psn_manager import PSNManager
import logging

logger = logging.getLogger('psn_api')

class Command(BaseCommand):
    help = 'Automatically queue profiles for refresh based on tier and last update time.'
    PREMIUM_THRESHOLD_HOURS = 6
    BASIC_THRESHOLD_HOURS = 12
    DISCORD_VERIFIED_THRESHOLD_HOURS = 24
    UNREGISTERED_THRESHOLD_DAYS = 7

    def add_arguments(self, parser):
        parser.add_argument('--premium-hours', type=int, default=self.PREMIUM_THRESHOLD_HOURS, help='Hours for premium tier.')
        parser.add_argument('--basic-hours', type=int, default=self.BASIC_THRESHOLD_HOURS, help='Hours for basic tier.')
        parser.add_argument('--discord-hours', type=int, default=self.DISCORD_VERIFIED_THRESHOLD_HOURS, help='Hours for Discord verified.')
        parser.add_argument('--unreg-days', type=int, default=self.UNREGISTERED_THRESHOLD_DAYS, help='Days for unregistered.')

    def handle(self, *args, **options):
        # Calculate thresholds from args
        premium_threshold = timezone.now() - timedelta(hours=options['premium_hours'])
        basic_threshold = timezone.now() - timedelta(hours=options['basic_hours'])
        discord_verified_threshold = timezone.now() - timedelta(hours=options['discord_hours'])
        unregistered_threshold = timezone.now() - timedelta(days=options['unreg_days'])

        queued_count = 0

        premium_qs = Profile.objects.filter(is_linked=True, sync_tier='preferred', last_synced__lt=premium_threshold)
        for profile in premium_qs.iterator():
            try:
                PSNManager.profile_refresh(profile)
                queued_count += 1
            except Exception as e:
                logger.error(f"Error queuing premium profile {profile.id}: {e}")
        
        basic_qs = Profile.objects.filter(is_linked=True, sync_tier='basic', last_synced__lt=basic_threshold)
        for profile in basic_qs.iterator():
            try:
                PSNManager.profile_refresh(profile)
                queued_count += 1
            except Exception as e:
                logger.error(f"Error queuing basic profile {profile.id}: {e}")
        
        discord_qs = Profile.objects.filter(is_linked=False, is_discord_verified=True, last_synced__lt=discord_verified_threshold)
        for profile in discord_qs.iterator():
            try:
                PSNManager.profile_refresh(profile)
                queued_count += 1
            except Exception as e:
                logger.error(f"Error queuing discord profile {profile.id}: {e}")
        
        unregistered_qs = Profile.objects.filter(is_linked=False, is_discord_verified=False, last_synced__lt=unregistered_threshold)
        for profile in unregistered_qs.iterator():
            try:
                PSNManager.profile_refresh(profile)
                queued_count += 1
            except Exception as e:
                logger.error(f"Error queuing unregistered profile {profile.id}: {e}")
        
        logger.info(f"Queued {queued_count} profiles for refresh across all tiers.")
        self.stdout.write(self.style.SUCCESS(f"Success: Queued {queued_count} profiles for refresh."))