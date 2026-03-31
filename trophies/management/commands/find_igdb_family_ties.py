from collections import defaultdict

from django.core.management.base import BaseCommand

from trophies.models import IGDBMatch, GameFamilyProposal


class Command(BaseCommand):
    help = 'Find concepts that share the same IGDB game but are not in the same GameFamily'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run', action='store_true',
            help='Report findings without creating family proposals',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']

        # Group all accepted/auto-accepted matches by igdb_id
        matches = (
            IGDBMatch.objects
            .filter(status__in=('auto_accepted', 'accepted'))
            .select_related('concept')
        )

        igdb_groups = defaultdict(list)
        for match in matches:
            igdb_groups[match.igdb_id].append(match)

        # Find groups with multiple concepts
        shared = {igdb_id: group for igdb_id, group in igdb_groups.items() if len(group) > 1}

        if not shared:
            self.stdout.write('No shared IGDB games found across concepts.')
            return

        proposals_created = 0
        already_in_family = 0
        already_proposed = 0

        for igdb_id, group in shared.items():
            igdb_name = group[0].igdb_name

            # Check each pair in the group
            for i, match_a in enumerate(group):
                for match_b in group[i + 1:]:
                    concept_a = match_a.concept
                    concept_b = match_b.concept

                    # Already in the same family
                    if concept_a.family_id and concept_a.family_id == concept_b.family_id:
                        already_in_family += 1
                        continue

                    # Check if proposal already exists
                    existing = GameFamilyProposal.objects.filter(
                        status='pending',
                        concepts=concept_a,
                    ).filter(concepts=concept_b).exists()

                    if existing:
                        already_proposed += 1
                        continue

                    self.stdout.write(
                        f'  IGDB #{igdb_id} "{igdb_name}": '
                        f'{concept_a.concept_id} "{concept_a.unified_title}" <-> '
                        f'{concept_b.concept_id} "{concept_b.unified_title}"'
                    )

                    if not dry_run:
                        proposal = GameFamilyProposal.objects.create(
                            proposed_name=igdb_name,
                            confidence=0.95,
                            match_reason=f'Both concepts matched to the same IGDB game: "{igdb_name}" (ID {igdb_id})',
                            match_signals={
                                'source': 'igdb_family_scan',
                                'igdb_id': igdb_id,
                                'igdb_name': igdb_name,
                                'concept_a': concept_a.concept_id,
                                'concept_b': concept_b.concept_id,
                            },
                        )
                        proposal.concepts.add(concept_a, concept_b)

                    proposals_created += 1

        prefix = '[DRY RUN] ' if dry_run else ''
        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS(f'{prefix}Family Tie Scan Complete'))
        self.stdout.write(f'  Shared IGDB games:    {len(shared)}')
        self.stdout.write(f'  Already in family:    {already_in_family}')
        self.stdout.write(f'  Already proposed:     {already_proposed}')
        self.stdout.write(f'  New proposals:        {proposals_created}')
