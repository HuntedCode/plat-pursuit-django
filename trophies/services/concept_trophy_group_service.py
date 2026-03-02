"""
ConceptTrophyGroup service layer.

Handles syncing concept-level trophy groups from game-level TrophyGroup records,
and checking DLC completion for review/rating access.
"""
import logging

from django.db.models import Count

logger = logging.getLogger('psn_api')


class ConceptTrophyGroupService:
    """Manages ConceptTrophyGroup records and DLC access checks."""

    @staticmethod
    def sync_for_concept(concept):
        """Discover all unique trophy_group_ids across all games in this concept
        and create/update ConceptTrophyGroup entries.

        Called after trophy groups are synced in token_keeper._job_sync_trophy_groups().

        Args:
            concept: Concept instance to sync trophy groups for
        """
        from trophies.models import TrophyGroup, ConceptTrophyGroup

        # Get all TrophyGroups for games belonging to this concept
        trophy_groups = (
            TrophyGroup.objects
            .filter(game__concept=concept)
            .values('trophy_group_id', 'trophy_group_name', 'trophy_group_icon_url')
            .order_by('trophy_group_id')
        )

        # Group by trophy_group_id, pick first name/icon found
        seen = {}
        for tg in trophy_groups:
            gid = tg['trophy_group_id']
            if gid not in seen:
                seen[gid] = {
                    'name': tg['trophy_group_name'] or gid,
                    'icon_url': tg['trophy_group_icon_url'],
                }

        # Ensure 'default' always gets a nice display name
        if 'default' in seen:
            seen['default']['name'] = 'Base Game'

        # Create/update ConceptTrophyGroup records
        sort_order = 0
        for gid in sorted(seen.keys(), key=lambda x: (x != 'default', x)):
            info = seen[gid]
            ConceptTrophyGroup.objects.update_or_create(
                concept=concept,
                trophy_group_id=gid,
                defaults={
                    'display_name': info['name'] if gid != 'default' else 'Base Game',
                    'icon_url': info['icon_url'],
                    'sort_order': sort_order,
                },
            )
            sort_order += 1

        logger.info(f"Synced {len(seen)} ConceptTrophyGroups for concept {concept.id}")

    @staticmethod
    def detect_mismatches(concept):
        """Compare trophy groups across all game stacks in a concept to find
        structural differences that may indicate the groups should not be unified.

        Uses a cascading check to filter out cosmetic differences:
        1. Missing groups: always flagged (structural difference)
        2. Name mismatch with different trophy counts: flagged as count mismatch
        3. Name mismatch with same counts but different trophy type distribution:
           flagged as structure mismatch (same number of trophies but different
           bronze/silver/gold/platinum breakdown means different content)
        4. Name mismatch with same counts AND same type distribution: ignored
           (cosmetic difference only, e.g. localization, ® symbols)

        Args:
            concept: Concept instance

        Returns:
            list[dict]: List of mismatch findings, empty if all clean.
                Each dict has: {type, group_id, detail, games}
        """
        from trophies.models import TrophyGroup

        mismatches = []

        games = list(concept.games.select_related().all())
        if len(games) <= 1:
            return mismatches

        # Build per-game group info: {game_id: {title, groups: {group_id: {name}}}}
        game_groups = {}
        for game in games:
            game_groups[game.id] = {
                'title': game.title_name,
                'groups': {},
            }

        # Use TrophyGroup.defined_trophies (PSN API metadata) for counts and type
        # distribution. This is more reliable than counting Trophy records, which
        # may be missing if sync_trophies failed independently of sync_trophy_groups.
        tg_data = (
            TrophyGroup.objects
            .filter(game__concept=concept)
            .values('game_id', 'trophy_group_id', 'trophy_group_name', 'defined_trophies')
        )
        for row in tg_data:
            dt = row['defined_trophies'] or {}
            bronze = dt.get('bronze', 0)
            silver = dt.get('silver', 0)
            gold = dt.get('gold', 0)
            platinum = dt.get('platinum', 0)
            total = bronze + silver + gold + platinum

            type_dist = {
                'bronze': bronze,
                'silver': silver,
                'gold': gold,
                'platinum': platinum,
            }

            game_groups[row['game_id']]['groups'][row['trophy_group_id']] = {
                'name': row['trophy_group_name'] or row['trophy_group_id'],
                'trophy_count': total,
                'type_dist': type_dist,
            }

        # Collect all unique group IDs across stacks
        all_group_ids = set()
        for gdata in game_groups.values():
            all_group_ids.update(gdata['groups'].keys())

        for gid in sorted(all_group_ids):
            present_in = {}
            for game_id, gdata in game_groups.items():
                if gid in gdata['groups']:
                    present_in[game_id] = gdata['groups'][gid]

            # Missing group: always flagged
            if len(present_in) < len(games):
                missing_games = [
                    game_groups[gid_]['title']
                    for gid_ in game_groups
                    if gid_ not in present_in
                ]
                mismatches.append({
                    'type': 'missing_group',
                    'group_id': gid,
                    'detail': f"Group '{gid}' missing from: {', '.join(missing_games)}",
                    'games': missing_games,
                })

            if len(present_in) < 2:
                continue

            # Check counts
            counts = {info.get('trophy_count', 0) for info in present_in.values()}
            has_count_mismatch = len(counts) > 1

            if has_count_mismatch:
                count_details = [
                    f"{game_groups[gid_]['title']}: {info.get('trophy_count', 0)} trophies"
                    for gid_, info in present_in.items()
                ]
                mismatches.append({
                    'type': 'trophy_count_mismatch',
                    'group_id': gid,
                    'detail': f"Group '{gid}' has different trophy counts: {'; '.join(count_details)}",
                    'games': [game_groups[g]['title'] for g in present_in],
                })
                continue  # Count mismatch already tells the story, no need for name/type checks

            # Counts match. Check if names differ.
            names = {info.get('name', '') for info in present_in.values()}
            if len(names) <= 1:
                continue  # Same name, same count: nothing to report

            # Names differ but counts match. Check trophy type distribution.
            type_dists = [
                info.get('type_dist', {})
                for info in present_in.values()
            ]
            # Compare all distributions to the first one
            all_types_match = all(d == type_dists[0] for d in type_dists[1:])

            if all_types_match:
                continue  # Same counts, same type breakdown: cosmetic name difference only

            # Names differ, counts match, but type distribution differs.
            # This means different content mapped to the same group ID.
            type_details = []
            for gid_, info in present_in.items():
                dist = info.get('type_dist', {})
                dist_str = ', '.join(
                    f"{count}{t[0].upper()}" for t, count in sorted(dist.items())
                )
                type_details.append(
                    f"{game_groups[gid_]['title']}: \"{info.get('name', '')}\" [{dist_str}]"
                )
            mismatches.append({
                'type': 'structure_mismatch',
                'group_id': gid,
                'detail': f"Group '{gid}' has same trophy count but different content: {'; '.join(type_details)}",
                'games': [game_groups[g]['title'] for g in present_in],
            })

        return mismatches

    @staticmethod
    def ensure_base_group(concept):
        """Ensure at least a 'default' (Base Game) group exists.

        Used when the hub page loads for a concept that may not have
        synced trophy groups yet.

        Args:
            concept: Concept instance

        Returns:
            ConceptTrophyGroup: The base game group
        """
        from trophies.models import ConceptTrophyGroup

        ctg, _ = ConceptTrophyGroup.objects.get_or_create(
            concept=concept,
            trophy_group_id='default',
            defaults={'display_name': 'Base Game', 'sort_order': 0},
        )
        return ctg

    @staticmethod
    def can_rate_group(profile, concept, concept_trophy_group):
        """Check if a user can rate a specific trophy group.

        Base game (group_id='default'): Must have earned platinum in any stack.
        DLC (group_id != 'default'): Must have 100% of that group's trophies
        earned in at least one game stack.

        Args:
            profile: Profile instance
            concept: Concept instance
            concept_trophy_group: ConceptTrophyGroup instance

        Returns:
            tuple: (bool can_rate, str reason_or_None)
        """
        from trophies.models import Trophy, EarnedTrophy

        if not profile:
            return False, "You must be logged in to rate."
        if not profile.is_linked:
            return False, "You must link a PSN profile to rate."

        if concept_trophy_group.trophy_group_id == 'default':
            # Base game: must have platinum
            if concept.has_user_earned_platinum(profile):
                return True, None
            return False, "Earn the platinum in this game to submit ratings."

        # DLC: must have 100% of that group's trophies in at least one stack.
        # Two bulk queries instead of 2N (one per game stack).
        group_id = concept_trophy_group.trophy_group_id

        # Total trophies per game in this group
        totals = dict(
            Trophy.objects.filter(
                game__concept=concept,
                trophy_group_id=group_id,
            ).values('game_id').annotate(total=Count('id')).values_list('game_id', 'total')
        )

        if not totals:
            return False, "Complete 100% of this DLC's trophies to submit ratings."

        # Earned trophies per game in this group for the profile
        earned = dict(
            EarnedTrophy.objects.filter(
                profile=profile,
                trophy__game_id__in=totals.keys(),
                trophy__trophy_group_id=group_id,
                earned=True,
            ).values('trophy__game_id').annotate(cnt=Count('id')).values_list('trophy__game_id', 'cnt')
        )

        for game_id, total in totals.items():
            if total > 0 and earned.get(game_id, 0) >= total:
                return True, None

        return False, "Complete 100% of this DLC's trophies to submit ratings."

    @staticmethod
    def can_review_group(profile, concept, concept_trophy_group):
        """Check if a user can write a review for a specific trophy group.

        Lower barrier than rating:
        Base game (group_id='default'): Must have a ProfileGame entry for any
        game in the concept (i.e., the game is on their trophy list).
        DLC (group_id != 'default'): Must have 1+ earned trophy in that group
        across any of the concept's game stacks.

        Args:
            profile: Profile instance
            concept: Concept instance
            concept_trophy_group: ConceptTrophyGroup instance

        Returns:
            tuple: (bool can_review, str reason_or_None)
        """
        from trophies.models import ProfileGame, EarnedTrophy

        if not profile:
            return False, "You must be logged in to write a review."
        if not profile.is_linked:
            return False, "You must link a PSN profile to write a review."
        if not profile.guidelines_agreed:
            return False, "You must agree to the community guidelines before writing a review."

        if concept_trophy_group.trophy_group_id == 'default':
            # Base game: must have any ProfileGame for this concept
            has_game = ProfileGame.objects.filter(
                profile=profile,
                game__concept=concept,
            ).exists()
            if has_game:
                return True, None
            return False, "This game must be on your trophy list to write a review."

        # DLC: must have 1+ earned trophy in that group
        group_id = concept_trophy_group.trophy_group_id
        has_earned = EarnedTrophy.objects.filter(
            profile=profile,
            trophy__game__concept=concept,
            trophy__trophy_group_id=group_id,
            earned=True,
        ).exists()

        if has_earned:
            return True, None
        return False, "Earn at least one trophy in this DLC to write a review."
