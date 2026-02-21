import logging

from django.utils import timezone

logger = logging.getLogger("psn_api")


class ShovelwareDetectionService:
    """Rule-based shovelware detection.

    Flagging (at sync time, when platinum trophy data arrives):
      1. Game's plat earn rate >= 90% -> flag game + all concept siblings
      2. Track concept on PublisherBlacklist
      3. If publisher hits 5+ concepts -> fully blacklisted -> flag publisher games

    Concept Shield (universal, applies to ALL flagging scenarios):
      A concept is only flagged if at least one game in it has 80%+ plat rate.
      If a game in the concept has <=50% and no sibling is 80%+, the concept
      stays clean. This check applies during sync unflagging AND publisher
      blacklist cascades.

    Unflagging (at sync time, when plat data updates):
      1. Game's plat earn rate <= 50%
      2. Check ALL games in concept: if ANY has 80%+ plat rate, concept stays flagged
      3. On unflag: remove concept from publisher tracking, potentially un-blacklist

    Locked games (shovelware_lock=True) are never changed by auto-detection.
    """

    FLAG_THRESHOLD = 90.0
    UNFLAG_THRESHOLD = 50.0
    SIBLING_HOLD = 80.0

    @classmethod
    def evaluate_game(cls, game):
        """Main entry point: called during sync when platinum trophy is created/updated."""
        if game.shovelware_lock:
            return

        plat = game.trophies.filter(trophy_type='platinum').only('trophy_earn_rate').first()
        if not plat:
            return

        rate = plat.trophy_earn_rate

        if rate >= cls.FLAG_THRESHOLD:
            cls._flag_game_and_concept(game)
        elif rate <= cls.UNFLAG_THRESHOLD:
            cls._maybe_unflag_game(game)

    @classmethod
    def _flag_game_and_concept(cls, game):
        """Flag this game, all concept siblings, and track on publisher blacklist."""
        from trophies.models import Game, PublisherBlacklist

        now = timezone.now()
        concept = game.concept

        if concept:
            Game.objects.filter(
                concept=concept, shovelware_lock=False,
            ).exclude(
                shovelware_status='manually_flagged',
            ).update(shovelware_status='auto_flagged', shovelware_updated_at=now)

            # Track concept on publisher blacklist
            if concept.publisher_name:
                entry, _ = PublisherBlacklist.objects.get_or_create(name=concept.publisher_name)
                was_added = entry.add_concept(concept.concept_id)
                if entry.is_blacklisted and was_added:
                    cls._flag_all_publisher_games(concept.publisher_name, now)
        else:
            if game.shovelware_status != 'manually_flagged':
                game.shovelware_status = 'auto_flagged'
                game.shovelware_updated_at = now
                game.save(update_fields=['shovelware_status', 'shovelware_updated_at'])

    @classmethod
    def _concept_is_shielded(cls, concept):
        """Check if a concept is shielded from flagging.

        A concept is shielded if ANY game in it has <=50% plat rate AND no
        game in the concept has 80%+ plat rate. This means no game qualifies
        as "hot", so the entire concept should not be considered shovelware.
        """
        from trophies.models import Game

        has_low_rate = False

        for game in Game.objects.filter(concept=concept):
            plat = game.trophies.filter(trophy_type='platinum').only('trophy_earn_rate').first()
            if not plat:
                continue
            if plat.trophy_earn_rate >= cls.SIBLING_HOLD:
                return False  # One hot game means concept is NOT shielded
            if plat.trophy_earn_rate <= cls.UNFLAG_THRESHOLD:
                has_low_rate = True

        return has_low_rate

    @classmethod
    def _maybe_unflag_game(cls, game):
        """Check if a game/concept can be unflagged (<=50% rate, no hot siblings).

        The concept shield check overrides everything, including publisher blacklists.
        If no game in the concept has 80%+ plat rate, the concept is unflagged.
        """
        from trophies.models import Game, PublisherBlacklist

        concept = game.concept

        if concept:
            # Check if any game in concept has 80%+ plat rate
            for sibling in Game.objects.filter(concept=concept):
                sibling_plat = sibling.trophies.filter(
                    trophy_type='platinum',
                ).only('trophy_earn_rate').first()
                if sibling_plat and sibling_plat.trophy_earn_rate >= cls.SIBLING_HOLD:
                    return  # A game in concept is hot: stays flagged

            # Safe to unflag entire concept
            now = timezone.now()
            Game.objects.filter(
                concept=concept, shovelware_lock=False,
            ).exclude(
                shovelware_status__in=['manually_flagged', 'clean'],
            ).update(shovelware_status='clean', shovelware_updated_at=now)

            # Remove concept from publisher tracking (may un-blacklist)
            if concept.publisher_name:
                try:
                    entry = PublisherBlacklist.objects.get(name=concept.publisher_name)
                    entry.remove_concept(concept.concept_id)
                except PublisherBlacklist.DoesNotExist:
                    pass
        else:
            if game.shovelware_status == 'auto_flagged':
                game.shovelware_status = 'clean'
                game.shovelware_updated_at = timezone.now()
                game.save(update_fields=['shovelware_status', 'shovelware_updated_at'])

    @classmethod
    def _flag_all_publisher_games(cls, publisher_name, now):
        """When a publisher becomes fully blacklisted, flag their games per-concept.

        Applies concept shield: concepts where no game has 80%+ plat rate
        (and at least one game has <=50%) are exempt from the blacklist cascade.
        """
        from trophies.models import Concept, Game

        concepts = Concept.objects.filter(
            publisher_name=publisher_name,
            games__isnull=False,
        ).distinct()

        for concept in concepts:
            if cls._concept_is_shielded(concept):
                continue

            Game.objects.filter(
                concept=concept,
                shovelware_lock=False,
            ).exclude(
                shovelware_status='manually_flagged',
            ).update(shovelware_status='auto_flagged', shovelware_updated_at=now)
