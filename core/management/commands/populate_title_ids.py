import requests
import csv
from io import StringIO
from django.core.management.base import BaseCommand
from trophies.models import TitleID

PS4_TSV_FILE = "https://raw.githubusercontent.com/andshrew/PlayStation-Titles/refs/heads/main/PS4_Titles.tsv"
PS5_TSV_FILE = "https://raw.githubusercontent.com/andshrew/PlayStation-Titles/refs/heads/main/PS5_Titles.tsv"

class Command(BaseCommand):
    def handle(self, *args, **options):
        response = requests.get(PS4_TSV_FILE, timeout=10)
        response.raise_for_status()
        tsv_content = response.text
        reader = csv.DictReader(StringIO(tsv_content), delimiter='\t')
        for row in reader:
            title_id_str = row.get('titleId')
            platform = 'PS4'
            region = row.get('region')
            created_count = 0
            if title_id_str and region:
                title_id, created = TitleID.objects.get_or_create(title_id=title_id_str, defaults={'platform': platform, 'region': region})
                if created:
                    created_count += 1
        
        response = requests.get(PS5_TSV_FILE, timeout=10)
        response.raise_for_status()
        tsv_content = response.text
        reader = csv.DictReader(StringIO(tsv_content), delimiter='\t')
        for row in reader:
            title_id_str = row.get('titleId')
            platform = 'PS5'
            region = row.get('region')
            if title_id_str and region:
                title_id, created = TitleID.objects.get_or_create(title_id=title_id_str, defaults={'platform': platform, 'region': region})
                if created:
                    created_count += 1
        
        print(f"Title IDs update complete. {created_count} IDs added.")