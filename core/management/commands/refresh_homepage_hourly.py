import logging

from django.utils import timezone
from django.core.management.base import BaseCommand
from django.core.cache import cache

from core.services.site_heartbeat import compute_site_heartbeat

logger = logging.getLogger(__name__)

HOURLY_JOBS = [
    {
        'name': 'Site Heartbeat',
        'key': 'site_heartbeat',
        'timeout': 3600,
        'func': compute_site_heartbeat,
    },
]


class Command(BaseCommand):
    help = 'Refresh hourly site cache data (PlatPursuit at a Glance heartbeat)'

    def handle(self, *args, **options):
        now_utc = timezone.now()
        today_utc = now_utc.date().isoformat()
        hour = now_utc.hour

        for job in HOURLY_JOBS:
            cache_key = f"{job['key']}_{today_utc}_{hour:02d}"
            try:
                data = job['func']()
                cache.set(cache_key, data, job['timeout'] * 2)
                self.stdout.write(self.style.SUCCESS(f"{job['name']} cached: {cache_key}"))
            except Exception as e:
                logger.exception(f"Failed to refresh {job['name']}")
                self.stdout.write(self.style.ERROR(f"{job['name']} failed: {e}"))

        # The landing's showcase Profile Card -- a stable key, not hour-bucketed: it is an
        # artifact, not a stat, and the landing falls back to its literal fixture when unset.
        try:
            from core.services.landing_service import render_showcase_card
            if render_showcase_card():
                self.stdout.write(self.style.SUCCESS("Landing showcase card cached"))
            else:
                self.stdout.write("Landing showcase card skipped (LANDING_SHOWCASE_PSN unset or unknown)")
        except Exception as e:
            logger.exception("Failed to render the landing showcase card")
            self.stdout.write(self.style.ERROR(f"Landing showcase card failed: {e}"))
