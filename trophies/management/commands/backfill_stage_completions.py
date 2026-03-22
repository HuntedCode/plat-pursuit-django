"""
Backfill StageCompletionEvent records for all profiles with badge progress.

For each profile that has completed any badge stages, evaluates stage completion
and creates StageCompletionEvent records with accurate timestamps:
- If game was completed AFTER badge was created: uses game completion date
- If game was completed BEFORE badge existed: uses badge.created_at (retroactive credit)
- If multiple concepts satisfy a stage: uses the earliest qualifying completion

Safe to run multiple times (unique_together prevents duplicates).

Usage:
    python manage.py backfill_stage_completions
    python manage.py backfill_stage_completions --profile-id 42
    python manage.py backfill_stage_completions --dry-run
"""
import logging

from django.core.management.base import BaseCommand
from django.db.models import Q

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Backfill StageCompletionEvent records from existing badge progress data'

    def add_arguments(self, parser):
        parser.add_argument('--profile-id', type=int, help='Backfill a single profile by ID')
        parser.add_argument('--dry-run', action='store_true', help='Count events without creating them')

    def handle(self, *args, **options):
        from trophies.models import (
            Profile, Badge, Stage, UserBadgeProgress, StageCompletionEvent, ProfileGame,
        )
        from trophies.services.badge_service import _find_stage_completion_details

        profile_id = options.get('profile_id')
        dry_run = options.get('dry_run', False)

        # Find profiles with badge progress
        progress_qs = UserBadgeProgress.objects.filter(completed_concepts__gt=0)
        if profile_id:
            progress_qs = progress_qs.filter(profile_id=profile_id)

        # Get unique profile IDs
        profile_ids = list(progress_qs.values_list('profile_id', flat=True).distinct())
        self.stdout.write(f"Found {len(profile_ids)} profile(s) with badge progress")

        total_created = 0
        total_skipped = 0

        for idx, pid in enumerate(profile_ids):
            try:
                profile = Profile.objects.get(id=pid)
            except Profile.DoesNotExist:
                continue

            # Get all badges this profile has progress on
            badge_ids = list(
                UserBadgeProgress.objects
                .filter(profile=profile, completed_concepts__gt=0)
                .values_list('badge_id', flat=True)
            )
            badges = Badge.objects.filter(id__in=badge_ids, is_live=True)

            # Pre-fetch completion data for this profile
            plat_game_ids = set(
                ProfileGame.objects
                .filter(profile=profile, has_plat=True)
                .values_list('game_id', flat=True)
            )
            complete_game_ids = set(
                ProfileGame.objects
                .filter(profile=profile, progress=100)
                .values_list('game_id', flat=True)
            )
            _context = {
                'plat_game_ids': plat_game_ids,
                'complete_game_ids': complete_game_ids,
            }

            profile_created = 0

            for badge in badges:
                if not badge.series_slug:
                    continue

                # Get stages for this series + tier
                stages = Stage.objects.filter(
                    series_slug=badge.series_slug
                ).prefetch_related('concepts__games')

                is_plat_check = badge.tier in [1, 3]

                for stage in stages:
                    if stage.stage_number == 0:
                        continue
                    if not stage.applies_to_tier(badge.tier):
                        continue

                    # Check if stage is complete
                    game_ids = set()
                    for concept in stage.concepts.all():
                        for game in concept.games.all():
                            game_ids.add(game.id)

                    check_ids = plat_game_ids if is_plat_check else complete_game_ids
                    is_complete = bool(game_ids & check_ids)

                    if not is_complete:
                        continue

                    # Check if event already exists
                    if StageCompletionEvent.objects.filter(
                        profile=profile, badge=badge, stage=stage
                    ).exists():
                        total_skipped += 1
                        continue

                    if dry_run:
                        concept, completed_at = _find_stage_completion_details(
                            profile, stage, badge, _context
                        )
                        self.stdout.write(
                            f"  [DRY RUN] {profile.display_psn_username} - "
                            f"{badge.name} Stage {stage.stage_number} - "
                            f"{completed_at.date()}"
                        )
                        profile_created += 1
                        continue

                    # Create event
                    concept, completed_at = _find_stage_completion_details(
                        profile, stage, badge, _context
                    )
                    try:
                        StageCompletionEvent.objects.create(
                            profile=profile,
                            badge=badge,
                            stage=stage,
                            concept=concept,
                            completed_at=completed_at,
                        )
                        profile_created += 1
                    except Exception as e:
                        logger.debug("Skipping duplicate: %s", e)
                        total_skipped += 1

            total_created += profile_created

            if (idx + 1) % 50 == 0 or idx == len(profile_ids) - 1:
                self.stdout.write(
                    f"  Processed {idx + 1}/{len(profile_ids)} profiles "
                    f"({total_created} created, {total_skipped} skipped)"
                )

        action = "Would create" if dry_run else "Created"
        self.stdout.write(self.style.SUCCESS(
            f"\n{action} {total_created} StageCompletionEvent records "
            f"({total_skipped} skipped/existing)"
        ))
