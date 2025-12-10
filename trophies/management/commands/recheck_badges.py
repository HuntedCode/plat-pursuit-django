from django.core.management.base import BaseCommand
from django.core.paginator import Paginator
from django.db.models import Prefetch
from trophies.models import Profile, Badge, UserBadge
from trophies.utils import process_badge
import logging

logger = logging.getLogger('psn_api')

class Command(BaseCommand):
    help = 'Rechecks and updates badge progress/awards for all users, optionally filtered by badge series_slug.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--series-slug',
            type=str,
            help='Filter to a specific series_slug (e.g. "spider-man")'
        )
        parser.add_argument(
            '--batch-size',
            type=int,
            default=100,
            help='Number of profiles to process per batch (default: 100)'
        )

    def handle(self, *args, **options):
        series_slug = options['series_slug']
        batch_size = options['batch_size']

        badges_qs = Badge.objects.filter(badge_type='series').prefetch_related('concepts')
        if series_slug:
            badges_qs = badges_qs.filter(series_slug=series_slug)
            if not badges_qs.exists():
                print(f"No badges found for series_slug")
                return
            
        print(f"Rechecking {badges_qs.count()} badges across all profiles...")

        profiles_qs = Profile.objects.all().order_by('id')
        paginator = Paginator(profiles_qs, batch_size)
        total_awarded = 0

        for page_num in paginator.page_range:
            page = paginator.page(page_num)
            for profile in page.object_list:
                for badge in badges_qs:
                    is_new_badge = process_badge(profile, badge)
                    if is_new_badge:
                        total_awarded += 1

            print(f"Processed batch {page_num}/{paginator.num_pages} ({len(page)} profiles.)")

        print(f"Recheck complete! Total new awards: {total_awarded}")
        logger.info(f"Badge recheck command run: series_slug={series_slug}, batch={paginator.num_pages}, awards={total_awarded}")