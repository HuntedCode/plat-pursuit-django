from collections import defaultdict

from django.core.management.base import BaseCommand

from trophies.models import Game, PublisherBlacklist


class Command(BaseCommand):
    help = "Populate publisher blacklist from currently flagged games."

    def handle(self, *args, **options):
        # Collect flagged concept IDs per publisher
        publisher_concepts = defaultdict(set)
        flagged_games = Game.objects.filter(
            shovelware_status__in=['auto_flagged', 'manually_flagged'],
            concept__publisher_name__isnull=False,
        ).exclude(
            concept__publisher_name='',
        ).values_list('concept__publisher_name', 'concept__concept_id').distinct()

        for pub_name, concept_id in flagged_games:
            publisher_concepts[pub_name].add(concept_id)

        created_count = 0
        updated_count = 0

        for pub_name, concept_ids in publisher_concepts.items():
            entry, created = PublisherBlacklist.objects.get_or_create(name=pub_name)
            # Merge with any existing flagged concepts
            existing = set(entry.flagged_concepts)
            merged = existing | concept_ids
            entry.flagged_concepts = list(merged)
            entry.is_blacklisted = bool(merged)
            entry.save(update_fields=['flagged_concepts', 'is_blacklisted'])

            if created:
                created_count += 1
            else:
                updated_count += 1

        self.stdout.write(self.style.SUCCESS(
            f"Done! {created_count} publisher(s) created, "
            f"{updated_count} updated, "
            f"{len(publisher_concepts)} total with flagged concepts."
        ))
