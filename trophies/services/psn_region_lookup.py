import requests
import csv
from io import StringIO
from django.core.cache import cache

TSV_URL = 'https://raw.githubusercontent.com/andshrew/PlayStation-Titles/refs/heads/main/All_Titles.tsv'

def get_region_lookup(refresh=False):
    cache_key = 'psn_region_lookup'
    lookup = cache.get(cache_key)
    if lookup is None or refresh:
        try:
            response = requests.get(TSV_URL, timeout=10)
            response.raise_for_status()
            tsv_content = response.text
            reader = csv.DictReader(StringIO(tsv_content), delimiter='\t')
            lookup = {}
            for row in reader:
                title_id = row.get('titleId')
                concept_id = row.get('conceptId')
                region = row.get('region')
                if title_id:
                    lookup[title_id] = {
                        'concept_id': concept_id if concept_id else '',
                        'region': region if region else ''
                    }
            cache.set(cache_key, lookup, 86400)
        except Exception as e:
            lookup = {}
    return lookup

def get_data_for_title_id(title_id):
    lookup = get_region_lookup()
    return lookup.get(title_id, 'Unknown')