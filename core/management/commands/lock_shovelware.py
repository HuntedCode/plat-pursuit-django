from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from trophies.models import Game


class Command(BaseCommand):
    help = "Lock or unlock a game's shovelware status. Propagates to all games in the same concept."

    def add_arguments(self, parser):
        parser.add_argument(
            'np_communication_id', type=str,
            help='The np_communication_id of the game to lock/unlock.',
        )
        group = parser.add_mutually_exclusive_group(required=True)
        group.add_argument(
            '--flag', action='store_true',
            help='Lock the game (and concept siblings) as shovelware.',
        )
        group.add_argument(
            '--clear', action='store_true',
            help='Lock the game (and concept siblings) as clean (not shovelware).',
        )
        group.add_argument(
            '--unlock', action='store_true',
            help='Remove the lock, allowing auto-detection to manage the status.',
        )

    def handle(self, *args, **options):
        np_id = options['np_communication_id']

        try:
            game = Game.objects.select_related('concept').get(np_communication_id=np_id)
        except Game.DoesNotExist:
            raise CommandError(f"Game with np_communication_id '{np_id}' not found.")

        concept = game.concept
        now = timezone.now()

        if concept:
            games = Game.objects.filter(concept=concept)
            game_count = games.count()
            concept_label = f"concept {concept.concept_id} ({game_count} game(s))"
        else:
            games = Game.objects.filter(id=game.id)
            game_count = 1
            concept_label = "no concept (single game)"

        if options['flag']:
            games.update(
                shovelware_status='manually_flagged',
                shovelware_lock=True,
                shovelware_updated_at=now,
            )
            self.stdout.write(self.style.SUCCESS(
                f"Locked {game_count} game(s) as shovelware ({concept_label})."
            ))

        elif options['clear']:
            games.update(
                shovelware_status='manually_cleared',
                shovelware_lock=True,
                shovelware_updated_at=now,
            )
            self.stdout.write(self.style.SUCCESS(
                f"Locked {game_count} game(s) as clean ({concept_label})."
            ))

        elif options['unlock']:
            games.update(
                shovelware_lock=False,
                shovelware_updated_at=now,
            )
            self.stdout.write(self.style.SUCCESS(
                f"Unlocked {game_count} game(s) for auto-detection ({concept_label})."
            ))
            self.stdout.write(
                "Run 'python manage.py update_shovelware' to re-evaluate these games."
            )
