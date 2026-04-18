import logging
import time

from django.utils import timezone

from trophies.util_modules.cache import redis_client

logger = logging.getLogger("psn_api")


class ShovelwareDetectionService:
    """Rule-based shovelware detection.

    Flagging rules (evaluated per concept):
      1. Earn-rate rule: any game in the concept has a platinum earn rate
         >= ``FLAG_THRESHOLD`` (80%). The whole concept is flagged and, if
         the concept has a trusted IGDB match, its primary developer is
         added to ``DeveloperBlacklist``. This propagates an automatic flag
         to every other concept whose primary developer matches.
      2. Developer-blacklist rule: the concept's primary developer is on
         ``DeveloperBlacklist``. The concept is flagged unless shielded.

    Shield (blocks rule 2 only, never rule 1):
      If a game in the concept has platinum earn rate < ``UNFLAG_THRESHOLD``
      (30%) AND no game in the concept has rate >= ``FLAG_THRESHOLD``, we
      treat the concept as legitimate and do NOT flag it even when the
      primary developer is blacklisted. An 80%+ game is direct evidence
      and always wins.

    Primary developer (required for propagation):
      First ``ConceptCompany`` row on the concept with ``is_developer=True``,
      ordered by ``id`` (matches IGDB's ``involved_companies`` array order).
      Only used when the concept has an ``IGDBMatch`` with ``is_trusted``.

    Locked games (``shovelware_lock=True``) are never changed by auto-detection.
    """

    FLAG_THRESHOLD = 80.0   # Earn rate >= this triggers flagging (rule 1)
    UNFLAG_THRESHOLD = 30.0  # Earn rate < this enables the shield

    @classmethod
    def evaluate_game(cls, game):
        """Sync entry point. Delegates to concept-level evaluation."""
        if game.shovelware_lock:
            return
        concept = game.concept
        if concept is None:
            cls._evaluate_standalone_game(game)
            return
        cls.evaluate_concept(concept)

    @classmethod
    def on_igdb_match_trusted(cls, concept):
        """Hook: re-evaluate a concept whose IGDBMatch just became trusted.

        Called from the IGDB service after a match is created with a trusted
        status (``auto_accepted``) or approved (``pending_review`` -> ``accepted``).
        Before this moment the concept had no accessible primary developer, so
        any rule-1 flag that fired during earlier platinum sync could not
        propagate to ``DeveloperBlacklist``. This hook closes that gap.

        Defensive: never raises. Shovelware evaluation must not break the
        IGDB ingest / approval flow.
        """
        try:
            cls.evaluate_concept(concept)
        except Exception:
            logger.exception(
                f"Shovelware re-evaluation failed for concept {concept.concept_id} "
                f"after IGDB match became trusted."
            )

    @classmethod
    def evaluate_concept(cls, concept):
        """Apply the full flagging algorithm to every game in the concept."""
        rates = cls._concept_plat_rates(concept)
        has_high = any(r >= cls.FLAG_THRESHOLD for r in rates)
        has_low = any(r < cls.UNFLAG_THRESHOLD for r in rates)
        primary_dev = cls._get_primary_developer(concept)

        if has_high:
            now = timezone.now()
            cls._flag_concept(concept, now)
            if primary_dev is not None:
                cls._register_developer_flag(primary_dev, concept, now)
            return

        if primary_dev is not None:
            entry = cls._get_blacklist_entry(primary_dev)
            if entry is not None and entry.is_blacklisted:
                if has_low:
                    cls._unflag_concept(concept, timezone.now())
                    entry.remove_concept(concept.concept_id)
                else:
                    cls._flag_concept(concept, timezone.now())
                return

        cls._unflag_concept(concept, timezone.now())

    @classmethod
    def _evaluate_standalone_game(cls, game):
        """Handle the rare case of a game with no concept.

        The developer blacklist requires a concept (for IGDBMatch + ConceptCompany),
        so the only rule available is the earn-rate threshold on this game alone.
        """
        plat = game.trophies.filter(trophy_type='platinum').only('trophy_earn_rate').first()
        if not plat:
            return

        rate = plat.trophy_earn_rate
        now = timezone.now()

        if rate >= cls.FLAG_THRESHOLD:
            if game.shovelware_status != 'manually_flagged':
                game.shovelware_status = 'auto_flagged'
                game.shovelware_updated_at = now
                game.save(update_fields=['shovelware_status', 'shovelware_updated_at'])
        elif rate < cls.UNFLAG_THRESHOLD and game.shovelware_status == 'auto_flagged':
            game.shovelware_status = 'clean'
            game.shovelware_updated_at = now
            game.save(update_fields=['shovelware_status', 'shovelware_updated_at'])

    @classmethod
    def _concept_plat_rates(cls, concept):
        """Return platinum earn rates for every game in the concept (one query)."""
        from trophies.models import Trophy

        return list(
            Trophy.objects.filter(
                game__concept=concept,
                trophy_type='platinum',
            ).values_list('trophy_earn_rate', flat=True)
        )

    @classmethod
    def _get_primary_developer(cls, concept):
        """Return the Company designated as primary developer, or None.

        Returns None when:
          - The concept has no IGDBMatch, or the match is not trusted.
          - No ConceptCompany row has is_developer=True.
        """
        from trophies.models import IGDBMatch

        try:
            match = concept.igdb_match
        except IGDBMatch.DoesNotExist:
            return None

        if not match.is_trusted:
            return None

        cc = concept.concept_companies.filter(
            is_developer=True,
        ).select_related('company').order_by('id').first()

        return cc.company if cc else None

    @classmethod
    def _get_blacklist_entry(cls, company):
        from trophies.models import DeveloperBlacklist

        return DeveloperBlacklist.objects.filter(company=company).first()

    @classmethod
    def _register_developer_flag(cls, company, concept, now):
        """Add concept to developer blacklist; cascade-flag other concepts if newly blacklisted."""
        from trophies.models import DeveloperBlacklist

        entry, _ = DeveloperBlacklist.objects.get_or_create(company=company)
        was_added = entry.add_concept(concept.concept_id)
        if was_added and entry.is_blacklisted:
            cls._flag_developer_concepts(company, exclude_concept_id=concept.concept_id, now=now)

    @classmethod
    def _flag_developer_concepts(cls, company, exclude_concept_id, now):
        """Cascade: flag every OTHER concept whose primary developer is ``company`` (respecting shield).

        Streams the candidate queryset with ``.iterator()`` so a prolific
        developer with hundreds of concepts doesn't materialize them all
        into memory at once.
        """
        from trophies.models import Concept

        candidate_qs = (
            Concept.objects
            .filter(concept_companies__company=company, concept_companies__is_developer=True)
            .exclude(concept_id=exclude_concept_id)
            .only('id', 'concept_id')
            .distinct()
        )

        for concept in candidate_qs.iterator(chunk_size=200):
            if cls._get_primary_developer(concept) != company:
                continue  # Not primary developer here; skip
            rates = cls._concept_plat_rates(concept)
            if any(r >= cls.FLAG_THRESHOLD for r in rates):
                continue  # Already handled by its own earn-rate rule on next eval
            has_low = any(r < cls.UNFLAG_THRESHOLD for r in rates)
            if has_low:
                continue  # Shielded
            cls._flag_concept(concept, now)

    @classmethod
    def _flag_concept(cls, concept, now):
        # Exclude 'auto_flagged' so reconciliation passes don't churn
        # shovelware_updated_at on already-flagged games.
        cls._update_concept_games_with_lock(
            concept,
            exclude_statuses=['manually_flagged', 'manually_cleared', 'auto_flagged'],
            new_status='auto_flagged',
            now=now,
        )

    @classmethod
    def _unflag_concept(cls, concept, now):
        cls._update_concept_games_with_lock(
            concept,
            exclude_statuses=['manually_flagged', 'manually_cleared', 'clean'],
            new_status='clean',
            now=now,
        )

    @classmethod
    def _update_concept_games_with_lock(cls, concept, exclude_statuses, new_status, now):
        """Update all games in a concept under a Redis lock to prevent deadlocks.

        Concurrent sync_trophies workers processing games in the same concept
        can cause AB/BA deadlocks via the bulk Game.objects.filter().update() call.
        This serializes concept-level writes with a short Redis lock.
        """
        from trophies.models import Game

        concept_lock_key = f"shovelware_concept_lock:{concept.concept_id}"
        for attempt in range(3):
            if redis_client.set(concept_lock_key, "1", nx=True, ex=10):
                try:
                    Game.objects.filter(
                        concept=concept, shovelware_lock=False,
                    ).exclude(
                        shovelware_status__in=exclude_statuses,
                    ).update(shovelware_status=new_status, shovelware_updated_at=now)
                finally:
                    redis_client.delete(concept_lock_key)
                return
            time.sleep(0.1 * (attempt + 1))

        logger.warning(
            f"Could not acquire concept lock for {concept.concept_id} after 3 attempts, "
            f"proceeding without lock."
        )
        Game.objects.filter(
            concept=concept, shovelware_lock=False,
        ).exclude(
            shovelware_status__in=exclude_statuses,
        ).update(shovelware_status=new_status, shovelware_updated_at=now)
