from django.core.management.base import BaseCommand
from django.utils import timezone
from datetime import timedelta
from trophies.models import Profile, ScoutAccount
from trophies.psn_manager import PSNManager
from trophies.services import sync_backoff
import logging

logger = logging.getLogger('psn_api')

class Command(BaseCommand):
    help = 'Automatically queue profiles for refresh based on tier and last update time.'
    PREMIUM_THRESHOLD_HOURS = 6
    BASIC_THRESHOLD_HOURS = 12
    DISCORD_VERIFIED_THRESHOLD_HOURS = 12
    UNREGISTERED_THRESHOLD_DAYS = 7
    MAX_SCOUTS_PER_RUN = 50
    #: 'error' profiles that look public, retried per run ahead of their tier cadence.
    ERROR_RECOVERY_PER_RUN = 10

    def add_arguments(self, parser):
        parser.add_argument('--premium-hours', type=int, default=self.PREMIUM_THRESHOLD_HOURS, help='Hours for premium tier.')
        parser.add_argument('--basic-hours', type=int, default=self.BASIC_THRESHOLD_HOURS, help='Hours for basic tier.')
        parser.add_argument('--discord-hours', type=int, default=self.DISCORD_VERIFIED_THRESHOLD_HOURS, help='Hours for Discord verified.')
        parser.add_argument('--unreg-days', type=int, default=self.UNREGISTERED_THRESHOLD_DAYS, help='Days for unregistered.')
        parser.add_argument('--max-scouts', type=int, default=self.MAX_SCOUTS_PER_RUN, help='Max scout profiles to queue per run (rate limit safety).')

    def handle(self, *args, **options):
        from trophies.util_modules.cache import redis_client
        if redis_client.get('site:psn_outage'):
            logger.info("PSN outage active. Skipping profile refresh.")
            self.stdout.write(self.style.WARNING("Skipped: PSN outage active."))
            return

        now = timezone.now()

        # Calculate thresholds from args
        premium_threshold = now - timedelta(hours=options['premium_hours'])
        basic_threshold = now - timedelta(hours=options['basic_hours'])
        discord_verified_threshold = now - timedelta(hours=options['discord_hours'])
        unregistered_threshold = now - timedelta(days=options['unreg_days'])

        # Error profiles still inside their retry backoff sit out every pass below. Without this, an
        # account whose sync fails on its first PSN call (NotFound) never stamps last_synced, looks
        # overdue forever and was re-queued on every run. See trophies/services/sync_backoff.py.
        waiting = sync_backoff.backing_off(
            Profile.objects.filter(sync_status='error').values_list('id', flat=True)
        )
        queued = set()
        scout_count = 0

        # Scouts first (tightest cadence, per-scout frequency)
        max_scouts = options['max_scouts']
        scouts = ScoutAccount.objects.filter(status='active').select_related('profile')
        for scout in scouts.iterator():
            if scout_count >= max_scouts:
                logger.warning(f"Hit max-scouts cap ({max_scouts}). Remaining scouts deferred to next run.")
                break
            threshold = now - timedelta(hours=scout.refresh_frequency_hours)
            if scout.profile.last_synced >= threshold:
                continue
            if self._queue(scout.profile, 'scout', queued, waiting):
                scout_count += 1

        tiers = (
            ('premium', Profile.objects.filter(is_linked=True, sync_tier='preferred', last_synced__lt=premium_threshold)),
            ('basic', Profile.objects.filter(is_linked=True, sync_tier='basic', last_synced__lt=basic_threshold)),
            ('discord', Profile.objects.filter(is_linked=False, is_discord_verified=True, last_synced__lt=discord_verified_threshold)),
            ('unregistered', Profile.objects.filter(is_linked=False, is_discord_verified=False, last_synced__lt=unregistered_threshold)),
        )
        for label, qs in tiers:
            for profile in qs.iterator():
                self._queue(profile, label, queued, waiting)

        # Error recovery: an 'error' profile NOT KNOWN TO BE PRIVATE failed for some other reason (a
        # timeout, a PSN wobble, a one-off job error). psn_history_public defaults to True and only a
        # PSN 403 or a level-0 profile lookup sets it False, so True means "not known private", not
        # "confirmed public". Retry these as soon as their backoff allows rather than waiting out a
        # tier cadence of up to a week. Capped per run so a burst of failures from a PSN wobble is
        # retried in a trickle, not all at once while PSN is still shaky.
        recovered = 0
        candidates = Profile.objects.filter(sync_status='error', psn_history_public=True).order_by('last_synced')
        for profile in candidates.iterator():
            if recovered >= self.ERROR_RECOVERY_PER_RUN:
                break
            if self._queue(profile, 'error recovery', queued, waiting):
                recovered += 1

        logger.info(
            f"Queued {len(queued)} profiles for refresh across all tiers "
            f"({scout_count} scouts, {recovered} error recoveries, {len(waiting)} backing off)."
        )
        self.stdout.write(self.style.SUCCESS(
            f"Success: Queued {len(queued)} profiles for refresh ({scout_count} scouts, "
            f"{recovered} error recoveries, {len(waiting)} backing off)."
        ))

    @staticmethod
    def _queue(profile, label, queued, waiting):
        """Queue one refresh, at most once per run and never while the profile is backing off.
        Returns whether it was queued. The backoff skip happens here, not as an `id__in` exclude, so
        a large backing-off population never rides along in every tier query."""
        if profile.id in queued or profile.id in waiting:
            return False
        try:
            PSNManager.profile_refresh(profile)
        except Exception as e:
            logger.error(f"Error queuing {label} profile {profile.id}: {e}")
            return False
        queued.add(profile.id)
        return True
