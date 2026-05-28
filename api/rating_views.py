"""Rating API views.

The structured game-rating system (difficulty / grindiness / hours /
fun / overall, per concept + optional DLC trophy group) lives here,
fully independent of the text-review system. Reviews were archived in
2026-05; ratings are kept because they're simple, self-contained, and
widely consumed (game detail, dashboard, share cards, milestones).

Endpoints are mounted under `/api/v1/ratings/` (NOT the historical
`/reviews/` prefix the rating endpoints used to share with reviews).

Business logic lives in RatingService + ConceptTrophyGroupService.
"""
import logging

from django.db.models import Max, Sum
from django.db.models.functions import Lower
from django.utils.decorators import method_decorator
from django_ratelimit.decorators import ratelimit
from rest_framework import status
from rest_framework.authentication import SessionAuthentication, TokenAuthentication
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from trophies.models import Concept, ConceptTrophyGroup
from api.utils import safe_int

logger = logging.getLogger('psn_api')


def _get_profile_or_error(request):
    """Return (profile, None) or (None, Response)."""
    profile = getattr(request.user, 'profile', None)
    if not profile:
        return None, Response(
            {'error': 'Linked profile required.'},
            status=status.HTTP_403_FORBIDDEN,
        )
    return profile, None


def _get_concept_and_group(concept_id, group_id_str):
    """Resolve Concept + ConceptTrophyGroup from URL params.

    Shovelware gate: if ALL games in the concept are shovelware, returns 403.
    The base ('default') group is auto-created when missing (a freshly
    synced concept may not have it yet); other groups must already exist.

    Returns:
        (concept, ctg, None) on success
        (None, None, Response) on error
    """
    try:
        concept = Concept.objects.get(id=concept_id)
    except Concept.DoesNotExist:
        return None, None, Response(
            {'error': 'Concept not found.'},
            status=status.HTTP_404_NOT_FOUND,
        )

    # Shovelware gate: reject if every game in the concept is flagged.
    # Concepts with zero games are allowed through (not-yet-synced state).
    games = concept.games.all()
    if games.exists() and not games.exclude(
        shovelware_status__in=['auto_flagged', 'manually_flagged'],
    ).exists():
        return None, None, Response(
            {'error': 'Ratings are not available for this game.'},
            status=status.HTTP_403_FORBIDDEN,
        )

    if group_id_str == 'default':
        ctg, _ = ConceptTrophyGroup.objects.get_or_create(
            concept=concept,
            trophy_group_id='default',
            defaults={'display_name': 'Base Game', 'sort_order': 0},
        )
    else:
        try:
            ctg = ConceptTrophyGroup.objects.get(
                concept=concept, trophy_group_id=group_id_str,
            )
        except ConceptTrophyGroup.DoesNotExist:
            return None, None, Response(
                {'error': 'Trophy group not found.'},
                status=status.HTTP_404_NOT_FOUND,
            )

    return concept, ctg, None


class GroupRatingView(APIView):
    """Submit or update a rating for a concept trophy group (base game or DLC)."""
    authentication_classes = [SessionAuthentication, TokenAuthentication]
    permission_classes = [IsAuthenticated]

    @method_decorator(ratelimit(key='user', rate='30/m', method='POST', block=True))
    def post(self, request, concept_id, group_id):
        """
        POST /api/v1/ratings/<concept_id>/group/<group_id>/rate/
        Body: {difficulty, grindiness, hours_to_platinum, fun_ranking, overall_rating}
        """
        try:
            profile, err = _get_profile_or_error(request)
            if err:
                return err

            concept, ctg, err = _get_concept_and_group(concept_id, group_id)
            if err:
                return err

            from trophies.services.concept_trophy_group_service import ConceptTrophyGroupService

            can, reason = ConceptTrophyGroupService.can_rate_group(profile, concept, ctg)
            if not can:
                return Response({'error': reason}, status=status.HTTP_403_FORBIDDEN)

            from trophies.models import UserConceptRating
            from trophies.forms import UserConceptRatingForm
            from trophies.services.rating_service import RatingService

            # Base game (trophy_group_id='default'): concept_trophy_group=None
            # for backward compat. DLC groups store the FK.
            ctg_fk = None if ctg.trophy_group_id == 'default' else ctg

            existing_rating = UserConceptRating.objects.filter(
                profile=profile,
                concept=concept,
                concept_trophy_group=ctg_fk,
            ).first()

            form = UserConceptRatingForm(request.data, instance=existing_rating)
            if not form.is_valid():
                return Response(
                    {'success': False, 'errors': form.errors},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            rating = form.save(commit=False)
            rating.profile = profile
            rating.concept = concept
            rating.concept_trophy_group = ctg_fk
            rating.save()

            RatingService.invalidate_cache(concept)
            RatingService.invalidate_group_cache(concept, ctg)

            from trophies.services.milestone_service import check_all_milestones_for_user
            check_all_milestones_for_user(profile, criteria_type='rating_count')

            updated_averages = RatingService.get_community_averages_for_group(concept, ctg)

            return Response({
                'success': True,
                'message': 'Rating updated!' if existing_rating else 'Rating submitted successfully!',
                'community_averages': updated_averages,
            })

        except Exception as e:
            logger.exception(f"Group rating error: {e}")
            return Response(
                {'error': 'Internal error.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class WizardQueueView(APIView):
    """Queue of ratable games waiting to be rated for the Rate My Games wizard.

    Ratings-only (the review half was removed when reviews were archived).
    """
    authentication_classes = [SessionAuthentication, TokenAuthentication]
    permission_classes = [IsAuthenticated]

    def get(self, request):
        """
        GET /api/v1/ratings/wizard/queue/?queue_type=base|dlc&limit=20&offset=0

        queue_type=base: base game concepts missing a rating.
        queue_type=dlc: DLC groups (missing a rating) grouped by parent concept.
        """
        try:
            profile, err = _get_profile_or_error(request)
            if err:
                return err

            from trophies.models import EarnedTrophy, UserConceptRating
            from trophies.services.review_hub_service import ReviewHubService

            queue_type = request.query_params.get('queue_type', 'base')
            if queue_type not in ('base', 'dlc'):
                queue_type = 'base'
            limit = min(safe_int(request.query_params.get('limit', 20), 20), 50)
            offset = max(safe_int(request.query_params.get('offset', 0), 0), 0)

            # All ratable concept IDs (platinumed + 100% non-plat).
            ratable_concept_ids = ReviewHubService.get_ratable_concept_ids(profile)

            if not ratable_concept_ids:
                if queue_type == 'dlc':
                    return Response({'groups': [], 'total_items': 0, 'has_more': False})
                return Response({'queue': [], 'count': 0, 'has_more': False})

            if queue_type == 'dlc':
                return self._get_dlc_queue(
                    profile, ratable_concept_ids, limit, offset,
                )

            # ── Base game queue ──────────────────────────────────────── #
            rated_concept_ids = set(
                UserConceptRating.objects.filter(
                    profile=profile,
                    concept_id__in=ratable_concept_ids,
                    concept_trophy_group__isnull=True,
                ).values_list('concept_id', flat=True)
            )

            wanted_ids = [cid for cid in ratable_concept_ids if cid not in rated_concept_ids]

            concepts = list(
                Concept.objects.filter(id__in=wanted_ids)
                .order_by(Lower('unified_title'))
                .values('id', 'unified_title', 'concept_icon_url', 'slug')
            )

            total_count = len(concepts)
            paginated = concepts[offset:offset + limit]
            has_more = (offset + limit) < total_count

            paginated_ids = [c['id'] for c in paginated]

            # Pre-fetch user's gameplay stats for these concepts
            from trophies.models import ProfileGame
            game_stats = {}
            for row in ProfileGame.objects.filter(
                profile=profile,
                game__concept_id__in=paginated_ids,
            ).values('game__concept_id').annotate(
                max_progress=Max('progress'),
                total_earned=Sum('earned_trophies_count'),
                total_unearned=Sum('unearned_trophies_count'),
                total_play=Sum('play_duration'),
            ):
                cid = row['game__concept_id']
                hours = None
                if row['total_play']:
                    hours = int(row['total_play'].total_seconds()) // 3600
                earned = row['total_earned'] or 0
                unearned = row['total_unearned'] or 0
                game_stats[cid] = {
                    'progress': row['max_progress'] or 0,
                    'earned_trophies': earned,
                    'total_trophies': earned + unearned,
                    'play_hours': hours,
                }

            plat_dates = {}
            for et in EarnedTrophy.objects.filter(
                profile=profile,
                earned=True,
                trophy__trophy_type='platinum',
                trophy__game__concept_id__in=paginated_ids,
            ).values('trophy__game__concept_id', 'earned_date_time'):
                cid = et['trophy__game__concept_id']
                dt = et['earned_date_time']
                if dt and (cid not in plat_dates or dt > plat_dates[cid]):
                    plat_dates[cid] = dt

            from trophies.models import Trophy
            concepts_with_plat = set(
                Trophy.objects.filter(
                    game__concept_id__in=paginated_ids,
                    trophy_type='platinum',
                ).values_list('game__concept_id', flat=True).distinct()
            )

            queue = []
            for c in paginated:
                cid = c['id']
                has_plat = cid in concepts_with_plat
                item = {
                    'concept_id': cid,
                    'unified_title': c['unified_title'],
                    'concept_icon_url': c['concept_icon_url'] or '',
                    'slug': c['slug'],
                    'has_rating': cid in rated_concept_ids,
                    'trophy_group_id': 'default',
                    'trophy_group_name': 'Base Game',
                    'hours_label': 'Hours to Platinum' if has_plat else 'Hours to Complete',
                }
                if cid in game_stats:
                    item['stats'] = game_stats[cid]
                if cid in plat_dates:
                    item['platinum_date'] = plat_dates[cid].isoformat()
                queue.append(item)

            return Response({
                'queue': queue,
                'count': total_count,
                'has_more': has_more,
                'next_offset': offset + limit,
            })

        except Exception as e:
            logger.exception(f"Wizard queue error: {e}")
            return Response(
                {'error': 'Failed to load game queue.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    def _get_dlc_queue(self, profile, ratable_concept_ids, limit, offset):
        """Build the DLC rating queue grouped by parent concept (ratings-only)."""
        from collections import OrderedDict
        from django.db.models import Count
        from trophies.models import Trophy, EarnedTrophy, UserConceptRating

        all_dlc_groups = list(
            ConceptTrophyGroup.objects.filter(
                concept_id__in=ratable_concept_ids,
            ).exclude(
                trophy_group_id='default',
            ).select_related('concept')
            .order_by(Lower('concept__unified_title'), 'sort_order')
        )

        if not all_dlc_groups:
            return Response({'groups': [], 'total_items': 0, 'has_more': False})

        dlc_concept_ids = {g.concept_id for g in all_dlc_groups}
        dlc_group_ids = {g.trophy_group_id for g in all_dlc_groups}

        totals = {}
        for row in Trophy.objects.filter(
            game__concept_id__in=dlc_concept_ids,
            trophy_group_id__in=dlc_group_ids,
        ).values('game_id', 'game__concept_id', 'trophy_group_id').annotate(
            total=Count('id'),
        ):
            totals[(row['game_id'], row['trophy_group_id'])] = (row['total'], row['game__concept_id'])

        earned = {}
        if totals:
            for row in EarnedTrophy.objects.filter(
                profile=profile,
                trophy__game_id__in={k[0] for k in totals},
                trophy__trophy_group_id__in=dlc_group_ids,
                earned=True,
            ).values('trophy__game_id', 'trophy__trophy_group_id').annotate(
                cnt=Count('id'),
            ):
                earned[(row['trophy__game_id'], row['trophy__trophy_group_id'])] = row['cnt']

        completed_pairs = set()
        for (game_id, group_id), (total, concept_id) in totals.items():
            if total > 0 and earned.get((game_id, group_id), 0) >= total:
                completed_pairs.add((concept_id, group_id))

        dlc_groups = [
            g for g in all_dlc_groups
            if (g.concept_id, g.trophy_group_id) in completed_pairs
        ]

        if not dlc_groups:
            return Response({'groups': [], 'total_items': 0, 'has_more': False})

        dlc_ctg_ids = [g.id for g in dlc_groups]
        dlc_rated = set(
            UserConceptRating.objects.filter(
                profile=profile,
                concept_trophy_group_id__in=dlc_ctg_ids,
            ).values_list('concept_trophy_group_id', flat=True)
        )

        groups_dict = OrderedDict()
        total_items = 0

        for g in dlc_groups:
            has_rating = g.id in dlc_rated
            if has_rating:
                continue  # ratings-only wizard: skip already-rated DLC

            cid = g.concept_id
            if cid not in groups_dict:
                groups_dict[cid] = {
                    'concept_id': cid,
                    'unified_title': g.concept.unified_title,
                    'concept_icon_url': g.concept.concept_icon_url or '',
                    'slug': g.concept.slug,
                    'items': [],
                }

            groups_dict[cid]['items'].append({
                'trophy_group_id': g.trophy_group_id,
                'trophy_group_name': g.display_name,
                'has_rating': has_rating,
                'is_dlc': True,
                'hours_label': 'Hours to Complete',
            })
            total_items += 1

        all_groups = list(groups_dict.values())

        flat_items = []
        for grp in all_groups:
            for item in grp['items']:
                flat_items.append((grp, item))

        page_items = flat_items[offset:offset + limit]
        has_more = (offset + limit) < len(flat_items)

        page_groups_dict = OrderedDict()
        for grp, item in page_items:
            cid = grp['concept_id']
            if cid not in page_groups_dict:
                page_groups_dict[cid] = {
                    k: v for k, v in grp.items() if k != 'items'
                }
                page_groups_dict[cid]['items'] = []
            page_groups_dict[cid]['items'].append(item)

        return Response({
            'groups': list(page_groups_dict.values()),
            'total_items': total_items,
            'has_more': has_more,
            'next_offset': offset + limit,
        })
