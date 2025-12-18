from django.core.management.base import BaseCommand
from django.utils import timezone
from datetime import timedelta
from trophies.models import Profile
from trophies.psn_manager import PSNManager
import logging

logger = logging.getLogger('psn_api')

class Command(BaseCommand):
    help = 'Refresh verified profiles not updated in 12+ hours'

    def handle(self, *args, **options):
        threshold = timezone.now() - timedelta(hours=12)
        verified_profiles = Profile.objects.filter(is_discord_verified=True, last_synced__lt=threshold)

        count = verified_profiles.count()
        if count == 0:
            logger.info('No profiles need refreshing.')
            return
        
        for profile in verified_profiles:
            try:
                PSNManager.profile_refresh(profile)
                logger.info(f"Queued refresh for verified profile {profile.id} ({profile.psn_username})")
            except Exception as e:
                logger.error(f"Error queuing refresh for profile {profile.id}: {e}")

        logger.info(f"Queued refreshes for {count} verified profiles")