import logging
import time

from django.utils import timezone

from trophies.util_modules.cache import redis_client

logger = logging.getLogger("psn_api")


class ShovelwareDetectionService:
    """Rule-based shovelware detection.

    Flagging rules (evaluated per concept):
      1. Earn-rate rule: any non-admin-locked game in the concept has a
         platinum earn rate >= ``FLAG_THRESHOLD`` (80%). The whole concept
         is flagged. If the concept has a trusted IGDB match, its primary
         developer is then evaluated for the developer blacklist (rule 2).
      2. Developer-blacklist rule: the concept's primary developer is on
         ``DeveloperReputation`` with ``is_blacklisted=True``. The concept is
         flagged unless shielded.

    Proportional blacklisting:
      A developer is blacklisted when MORE THAN ``BLACKLIST_PROPORTION`` (50%)
      of their platinum-bearing, primary-developed concepts are independently
      shovelware, provided they have at least ``BLACKLIST_MIN_CONCEPTS`` (3)
      such concepts. "Independently shovelware" means the concept has a
      non-locked game at or above a rate threshold ON ITS OWN MERIT, never
      because of the developer-blacklist cascade (this avoids a feedback loop
      where cascade-flagging inflates the proportion and pins the developer on
      the blacklist forever).

      Hysteresis lives in the rate threshold of the numerator:
        - ENTER  when proportion at >= FLAG_THRESHOLD (80%) is > 50%.
        - STAY   while proportion at >= EVIDENCE_THRESHOLD (70%) is > 50%.
        - RELEASE when the 70% proportion drops to <= 50%.
      Since 70% admits at least as many concepts as 80%, a developer must earn
      a strong majority to enter but only leaves once even the looser bar
      fails. On release, every cascade-only flag clears immediately.

    Shield (blocks rule 2 only, never rule 1):
      If a game in the concept has platinum earn rate < ``UNFLAG_THRESHOLD``
      (30%) AND no game in the concept has rate >= ``FLAG_THRESHOLD``, we
      treat the concept as legitimate and do NOT flag it even when the
      primary developer is blacklisted. An 80%+ game is direct evidence
      and always wins.

    Developer whitelist (full exemption):
      A whitelisted primary developer's concepts are NEVER auto-flagged
      (rule 1 included), and the developer is never evaluated for
      blacklisting. Whitelist wins over blacklist. Per-concept
      ``manually_flagged`` locks remain the escape hatch for an individual
      bad title.

    Primary developer (required for developer-blacklist participation):
      First ``ConceptCompany`` row on the concept with ``is_developer=True``,
      ordered by ``id``. Requires a trusted ``IGDBMatch`` on the concept.

    Admin-locked games (``shovelware_lock=True``) are invisible to all
    rate-based calculations. Their earn rates do not contribute to rule 1
    on siblings, do not contribute to the shield, and do not contribute to
    the blacklist proportion (numerator or denominator). Admin has the final
    say.
    """

    FLAG_THRESHOLD = 80.0       # Earn rate >= this triggers rule 1 (and the enter-numerator)
    UNFLAG_THRESHOLD = 30.0     # Earn rate < this enables the shield
    EVIDENCE_THRESHOLD = 70.0   # Stay-numerator rate; 10% deadband below FLAG_THRESHOLD
    BLACKLIST_PROPORTION = 0.50  # Blacklist when > this fraction of concepts are independently shovelware
    BLACKLIST_MIN_CONCEPTS = 3   # Floor: proportional rule applies only at or above this many concepts

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
        contribute to the developer blacklist. This hook closes that gap.

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
        now = timezone.now()
        primary_dev = cls._get_primary_developer(concept)

        # Whitelist short-circuit (full exemption): a whitelisted primary
        # developer's concepts are never auto-flagged. Manual statuses still
        # win via _unflag_concept's exclude list.
        if primary_dev is not None and cls._is_whitelisted(primary_dev):
            cls._unflag_concept(concept, now)
            return

        rates = cls._concept_plat_rates(concept)
        has_high = any(r >= cls.FLAG_THRESHOLD for r in rates)
        has_low = any(r < cls.UNFLAG_THRESHOLD for r in rates)

        if has_high:
            cls._flag_concept(concept, now)  # rule 1: always flags on direct evidence
            if primary_dev is not None:
                cls._maybe_blacklist_developer(primary_dev, concept, now)
            return

        if primary_dev is not None:
            entry = cls._get_reputation_entry(primary_dev)
            if entry is not None and entry.is_blacklisted:
                # Stay gate: does the developer still clear the proportional
                # threshold at the looser evidence rate (70%)?
                if cls._dev_meets_blacklist_threshold(primary_dev, cls.EVIDENCE_THRESHOLD):
                    if has_low:
                        cls._unflag_concept(concept, now)  # shielded
                    else:
                        cls._flag_concept(concept, now)
                    return
                else:
                    # Proportion has dropped; release the developer (which
                    # cascades an immediate unflag) and fall through to the
                    # default unflag for this concept.
                    cls._release_developer(primary_dev, now)

        cls._unflag_concept(concept, now)

    @classmethod
    def on_developer_whitelisted(cls, company, now=None):
        """Admin set a developer to whitelisted: clear all auto-flags on their
        primary-developed concepts and drop any blacklist status.

        Full exemption (Option B): every auto-flagged game in the developer's
        primary-developed concepts goes clean regardless of earn rate.
        Per-concept ``manually_flagged`` / ``manually_cleared`` locks survive
        via ``_unflag_concept``'s exclude list.
        """
        now = now or timezone.now()

        entry = cls._get_reputation_entry(company)
        if entry is not None and entry.is_blacklisted:
            entry.is_blacklisted = False
            entry.save(update_fields=['is_blacklisted'])

        # Unflag across ALL primary-developed concepts (not just the
        # platinum-bearing ones), since the cascade can flag no-platinum
        # concepts too. Full exemption means every auto-flag clears.
        for concept in cls._primary_developed_candidates(company):
            if cls._get_primary_developer(concept) != company:
                continue
            cls._unflag_concept(concept, now)

    @classmethod
    def on_developer_unwhitelisted(cls, company):
        """Admin cleared a developer's whitelist: re-evaluate their concepts so
        flags reappear per the normal rules."""
        for concept in cls._primary_developed_candidates(company):
            if cls._get_primary_developer(concept) != company:
                continue
            cls.evaluate_concept(concept)

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
        """Return platinum earn rates for every non-admin-locked game in the concept.

        Admin-locked games (``shovelware_lock=True``) are excluded so their
        rate never contributes to rule 1 or the shield. Admin has the final
        say on whether a specific game is shovelware.
        """
        from trophies.models import Trophy

        return list(
            Trophy.objects.filter(
                game__concept=concept,
                game__shovelware_lock=False,
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
    def _get_reputation_entry(cls, company):
        from trophies.models import DeveloperReputation

        return DeveloperReputation.objects.filter(company=company).first()

    @classmethod
    def _is_whitelisted(cls, company):
        """True if the company has a reputation entry flagged is_whitelisted."""
        entry = cls._get_reputation_entry(company)
        return entry is not None and entry.is_whitelisted

    @classmethod
    def _dev_meets_blacklist_threshold(cls, company, rate_threshold):
        """True if MORE THAN BLACKLIST_PROPORTION of the company's
        platinum-bearing primary-developed concepts have a non-locked game at
        or above ``rate_threshold`` plat earn rate.

        Numerator and denominator are both DB-aggregated counts (no Python
        iteration), and the numerator query is a strict subset of the
        denominator query, so the ratio is always well-defined. Returns False
        below the ``BLACKLIST_MIN_CONCEPTS`` floor.
        """
        from trophies.models import DeveloperReputation

        denom = DeveloperReputation.primary_developed_concepts(company).count()
        if denom < cls.BLACKLIST_MIN_CONCEPTS:
            return False
        num = DeveloperReputation.qualifying_concepts_for(company, threshold=rate_threshold).count()
        return (num / denom) > cls.BLACKLIST_PROPORTION

    @classmethod
    def _maybe_blacklist_developer(cls, company, concept, now):
        """Blacklist the developer if the enter threshold (80% proportion) is
        met; cascade-flag their other concepts on a fresh activation.

        Only creates a ``DeveloperReputation`` row when the threshold is met,
        so developers who merely have one easy-platinum concept don't litter
        the table with inactive entries. Whitelisted developers are never
        blacklisted.
        """
        from trophies.models import DeveloperReputation

        # Cheap pre-check: a developer already blacklisted or whitelisted needs
        # no work, so we skip the two proportion count-queries on the hot path.
        entry = cls._get_reputation_entry(company)
        if entry is not None and (entry.is_whitelisted or entry.is_blacklisted):
            return

        if not cls._dev_meets_blacklist_threshold(company, cls.FLAG_THRESHOLD):
            return

        entry, _ = DeveloperReputation.objects.get_or_create(company=company)
        if entry.is_whitelisted or entry.is_blacklisted:
            return

        entry.is_blacklisted = True
        entry.save(update_fields=['is_blacklisted'])
        cls._flag_developer_concepts(company, exclude_concept_id=concept.concept_id, now=now)

    @classmethod
    def _release_developer(cls, company, now):
        """Drop a developer's blacklist status and immediately clear every
        cascade-only flag across their primary-developed concepts."""
        entry = cls._get_reputation_entry(company)
        if entry is not None and entry.is_blacklisted:
            entry.is_blacklisted = False
            entry.save(update_fields=['is_blacklisted'])
        cls._unflag_developer_concepts(company, now)

    @classmethod
    def _primary_developed_candidates(cls, company):
        """Iterator of every concept where ``company`` appears as a developer.

        The caller MUST still confirm primary-developer status per concept via
        ``_get_primary_developer`` (this query matches any developer credit;
        the guard narrows it to the lead studio). ``igdb_match`` is
        select_related so the per-concept primary-developer check doesn't
        re-query for it. Streams with ``.iterator()`` so a prolific
        developer's catalog never materializes all at once.
        """
        from trophies.models import Concept

        return (
            Concept.objects
            .filter(concept_companies__company=company, concept_companies__is_developer=True)
            .select_related('igdb_match')
            .distinct()
            .iterator(chunk_size=200)
        )

    @classmethod
    def _flag_developer_concepts(cls, company, exclude_concept_id, now):
        """Cascade: flag every OTHER concept whose primary developer is ``company`` (respecting shield)."""
        for concept in cls._primary_developed_candidates(company):
            if concept.concept_id == exclude_concept_id:
                continue  # The triggering concept is already flagged
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
    def _unflag_developer_concepts(cls, company, now):
        """Cascade unflag (mirror of ``_flag_developer_concepts``): clear flags
        on every concept primary-developed by ``company`` that is NOT
        independently shovelware.

        Concepts with a game at or above ``FLAG_THRESHOLD`` stay flagged by
        rule 1 on their own merit. Manual statuses survive via
        ``_unflag_concept``.
        """
        for concept in cls._primary_developed_candidates(company):
            if cls._get_primary_developer(concept) != company:
                continue  # Not primary developer here; skip
            rates = cls._concept_plat_rates(concept)
            if any(r >= cls.FLAG_THRESHOLD for r in rates):
                continue  # Independently shovelware (rule 1); leave flagged
            cls._unflag_concept(concept, now)

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
