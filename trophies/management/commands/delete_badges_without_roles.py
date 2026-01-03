from django.core.management.base import BaseCommand
from django.db.models import Q
from trophies.models import Badge

class Command(BaseCommand):

    def handle(self, *args, **options):
        Badge.objects.filter(Q(user_title='') & (Q(base_badge__isnull=True) | Q(base_badge__user_title=''))).delete()
        self.stdout.write('Deletion successful!')