from django.core.management.base import BaseCommand
from trophies.models import Concept
from trophies.utils import TITLE_STATS_SUPPORTED_PLATFORMS, count_unique_game_groups

class Command(BaseCommand):
    def handle(self, *args, **options):
        concepts_qs = Concept.objects.all().prefetch_related('games')

        for concept in concepts_qs:
            concept_games = concept.games.all()
            for platform in TITLE_STATS_SUPPORTED_PLATFORMS:
                games = concept_games.filter(title_platform__contains=platform)
                if count_unique_game_groups(games) > 1:
                    for game in games:
                        game.is_regional = True
                        game.save(update_fields=['is_regional'])