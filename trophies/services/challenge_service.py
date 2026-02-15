"""
Challenge Service — Core service for challenge creation, progress checking, and management.
Handles A-Z Platinum Challenges with extensibility for future challenge types.
"""
import random
import logging
from django.db.models import Q
from django.utils import timezone

from trophies.models import Challenge, AZChallengeSlot, ProfileGame, Game

logger = logging.getLogger("psn_api")


def create_az_challenge(profile, name='My A-Z Challenge'):
    """
    Create a new A-Z Challenge with 26 empty slots (A-Z).
    Validates that no existing active AZ challenge exists for this user.

    Returns:
        Challenge instance

    Raises:
        ValueError if user already has an active AZ challenge
    """
    if Challenge.objects.filter(
        profile=profile, challenge_type='az', is_deleted=False, is_complete=False
    ).exists():
        raise ValueError("You already have an active A-Z Challenge.")

    challenge = Challenge.objects.create(
        profile=profile,
        challenge_type='az',
        name=name,
        total_items=26,
    )
    slots = [
        AZChallengeSlot(challenge=challenge, letter=chr(i))
        for i in range(65, 91)  # A-Z
    ]
    AZChallengeSlot.objects.bulk_create(slots)
    return challenge


def check_az_challenge_progress(profile):
    """
    Check all active A-Z challenges for newly completed slots.
    Called during sync in _job_sync_complete().

    Uses a batch query on ProfileGame.has_plat instead of N+1
    Concept.has_user_earned_platinum() calls.
    """
    challenges = Challenge.objects.filter(
        profile=profile, challenge_type='az', is_deleted=False, is_complete=False
    ).prefetch_related('az_slots__game')

    for challenge in challenges:
        pending_slots = list(
            challenge.az_slots.filter(game__isnull=False, is_completed=False)
        )
        if not pending_slots:
            continue

        game_ids = [s.game_id for s in pending_slots]

        # Batch: which of these games has the user platted?
        platted_game_ids = set(
            ProfileGame.objects.filter(
                profile=profile, game_id__in=game_ids, has_plat=True
            ).values_list('game_id', flat=True)
        )

        newly_completed = 0
        for slot in pending_slots:
            if slot.game_id in platted_game_ids:
                slot.is_completed = True
                slot.completed_at = timezone.now()
                slot.save(update_fields=['is_completed', 'completed_at'])
                newly_completed += 1

        if newly_completed > 0:
            recalculate_challenge_counts(challenge)
            if challenge.completed_count == 26:
                challenge.is_complete = True
                challenge.completed_at = timezone.now()
                challenge.save(update_fields=[
                    'completed_count', 'filled_count', 'is_complete',
                    'completed_at', 'updated_at',
                ])
                _create_completion_notification(challenge)
            else:
                challenge.save(update_fields=[
                    'completed_count', 'filled_count', 'updated_at',
                ])


def recalculate_challenge_counts(challenge):
    """Refresh filled_count and completed_count from actual slot data."""
    challenge.filled_count = challenge.az_slots.filter(game__isnull=False).count()
    challenge.completed_count = challenge.az_slots.filter(is_completed=True).count()


def auto_set_cover_letter(challenge):
    """
    Pick a random assigned slot's letter as the cover.
    Called when cover_letter is empty (first assignment) or when the
    current cover slot's game is cleared.
    Returns the chosen letter or '' if no games are assigned.
    """
    assigned_letters = list(
        challenge.az_slots.filter(game__isnull=False).values_list('letter', flat=True)
    )
    if not assigned_letters:
        challenge.cover_letter = ''
    else:
        challenge.cover_letter = random.choice(assigned_letters)
    challenge.save(update_fields=['cover_letter'])
    return challenge.cover_letter


def get_excluded_game_ids(profile):
    """
    Return set of game IDs to exclude from AZ search:
    - Games user has >50% progress on (that specific game only)
    - Games user already has platinum for, PLUS all related versions:
      - Concept siblings (same Concept, different platform/region)
      - GameFamily siblings (different Concept, same GameFamily)
    """
    # Tier 1: >50% progress — excludes only the specific game
    progress_excluded = set(
        ProfileGame.objects.filter(
            profile=profile, progress__gt=50,
        ).values_list('game_id', flat=True)
    )

    # Tier 2: Platinumed games + concept/family expansion
    plat_game_ids = set(
        ProfileGame.objects.filter(
            profile=profile, has_plat=True,
        ).values_list('game_id', flat=True)
    )

    if not plat_game_ids:
        return progress_excluded

    # Expand via Concept: all games sharing a Concept with any platted game
    concept_ids = set(
        Game.objects.filter(
            id__in=plat_game_ids, concept__isnull=False,
        ).values_list('concept_id', flat=True)
    )
    concept_siblings = set(
        Game.objects.filter(concept_id__in=concept_ids)
        .values_list('id', flat=True)
    ) if concept_ids else set()

    # Expand via GameFamily: all games in families that contain any platted game's concept
    family_ids = set(
        Game.objects.filter(
            id__in=plat_game_ids, concept__family__isnull=False,
        ).values_list('concept__family_id', flat=True)
    )
    family_siblings = set(
        Game.objects.filter(concept__family_id__in=family_ids)
        .values_list('id', flat=True)
    ) if family_ids else set()

    return progress_excluded | plat_game_ids | concept_siblings | family_siblings


def _create_completion_notification(challenge):
    """Create in-app notification for A-Z challenge completion."""
    try:
        from notifications.services.notification_service import NotificationService
        NotificationService.create_notification(
            recipient=challenge.profile.user,
            notification_type='challenge_completed',
            title='A-Z Challenge Complete!',
            message=f'You completed your A-Z Challenge "{challenge.name}"! Welcome to the Hall of Fame!',
            icon='🏆',
            action_url=f'/challenges/az/{challenge.id}/',
            action_text='View Challenge',
            metadata={
                'challenge_id': challenge.id,
                'challenge_type': 'az',
                'challenge_name': challenge.name,
                'completed_count': challenge.completed_count,
                'total_items': challenge.total_items,
            },
        )
    except Exception as e:
        logger.exception(f"Failed to create completion notification for challenge {challenge.id}: {e}")
