import requests
import csv
from io import StringIO
from django.core.management.base import BaseCommand
from trophies.models import TitleID

PS4_TSV_FILE = "https://raw.githubusercontent.com/andshrew/PlayStation-Titles/refs/heads/main/PS4_Titles.tsv"
PS5_TSV_FILE = "https://raw.githubusercontent.com/andshrew/PlayStation-Titles/refs/heads/main/PS5_Titles.tsv"

class Command(BaseCommand):
    help = "Populate TitleID table from PlayStation Titles GitHub repo"

    def _process_tsv(self, url, platform):
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        reader = csv.DictReader(StringIO(response.text), delimiter='\t')
        created_count = 0
        for row in reader:
            title_id_str = row.get('titleId')
            if not title_id_str:
                continue
            region = row.get('region')
            if not region:
                region = 'IP'
            title_id, created = TitleID.objects.get_or_create(
                title_id=title_id_str,
                defaults={'platform': platform, 'region': region},
            )
            if not title_id.region:
                title_id.region = 'IP'
                title_id.save(update_fields=['region'])
            if created:
                created_count += 1
        return created_count

    def handle(self, *args, **options):
        ps4_count = self._process_tsv(PS4_TSV_FILE, 'PS4')
        ps5_count = self._process_tsv(PS5_TSV_FILE, 'PS5')
        total = ps4_count + ps5_count
        self.stdout.write(f"Title IDs update complete. {total} IDs added (PS4: {ps4_count}, PS5: {ps5_count}).")
