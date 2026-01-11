from django.core.management.base import BaseCommand
from trophies.models import Badge, Title, UserBadge, UserTitle

class Command(BaseCommand):
    def handle(self, *args, **options):
        for badge in Badge.objects.exclude(user_title=''):
            title, created = Title.objects.get_or_create(name=badge.user_title)
            badge.title = title
            badge.save(update_fields=['title'])
        
            for user_badge in UserBadge.objects.filter(badge=badge):
                UserTitle.objects.get_or_create(
                    profile=user_badge.profile,
                    title=title,
                    source_type='badge',
                    source_id=badge.id,
                    earned_at=user_badge.earned_at,
                    is_displayed=user_badge.profile.selected_title == title.name
                )
        self.stdout.write(self.style.SUCCESS(f"Titles migrated successfully!"))