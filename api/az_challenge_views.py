"""
A-Z Challenge API views.

Handles REST endpoints for A-Z Platinum Challenges: CRUD, slot assignment/clearing,
and game search with filtering (platform, region, shovelware, progress).
"""
import logging

from django.utils import timezone
from django.utils.decorators import method_decorator
from django_ratelimit.decorators import ratelimit
from rest_framework import status
from rest_framework.authentication import SessionAuthentication, TokenAuthentication
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from django.db.models import Avg, Count, F, IntegerField, OuterRef, Subquery
from django.db.models.functions import Lower

from trophies.models import (
    Challenge, AZChallengeSlot, Game, Trophy, UserConceptRating,
)
from trophies.services.challenge_service import (
    create_az_challenge, recalculate_challenge_counts, get_excluded_game_ids,
    auto_set_cover_letter,
)
from trophies.util_modules.constants import ALL_PLATFORMS, REGIONS
from api.utils import safe_int

logger = logging.getLogger('psn_api')


# ─── Helpers ─────────────────────────────────────────────────────────────────────

def _get_profile_or_error(request):
    """Return (profile, None) or (None, Response) for error."""
    profile = getattr(request.user, 'profile', None)
    if not profile:
        return None, Response(
            {'error': 'Linked profile required.'},
            status=status.HTTP_403_FORBIDDEN,
        )
    return profile, None


def _get_owned_challenge(challenge_id, profile):
    """Return (challenge, None) or (None, Response) for error."""
    try:
        challenge = Challenge.objects.get(id=challenge_id, is_deleted=False)
    except Challenge.DoesNotExist:
        return None, Response(
            {'error': 'Challenge not found.'},
            status=status.HTTP_404_NOT_FOUND,
        )
    if challenge.profile_id != profile.id:
        return None, Response(
            {'error': 'Not your challenge.'},
            status=status.HTTP_403_FORBIDDEN,
        )
    return challenge, None


def _serialize_slot(slot):
    """Serialize a single AZChallengeSlot to a dict."""
    data = {
        'letter': slot.letter,
        'is_completed': slot.is_completed,
        'completed_at': slot.completed_at.isoformat() if slot.completed_at else None,
        'assigned_at': slot.assigned_at.isoformat() if slot.assigned_at else None,
        'game': None,
    }
    if slot.game:
        data['game'] = {
            'id': slot.game.id,
            'title_name': slot.game.title_name,
            'title_image': slot.game.title_image or '',
            'title_icon_url': slot.game.title_icon_url or '',
            'title_platform': slot.game.title_platform or [],
            'region': slot.game.region or [],
            'defined_trophies': slot.game.defined_trophies or {},
        }
    return data


def _resolve_cover_image(challenge):
    """Resolve cover image URL from cover_letter → slot → game."""
    if not challenge.cover_letter:
        return ''
    try:
        # Use prefetched az_slots if available, otherwise query
        for slot in challenge.az_slots.all():
            if slot.letter == challenge.cover_letter and slot.game:
                return slot.game.title_icon_url or slot.game.title_image or ''
    except Exception:
        pass
    return ''


def _serialize_challenge(challenge, include_slots=False):
    """Serialize a Challenge to a dict."""
    data = {
        'id': challenge.id,
        'challenge_type': challenge.challenge_type,
        'name': challenge.name,
        'description': challenge.description,
        'total_items': challenge.total_items,
        'filled_count': challenge.filled_count,
        'completed_count': challenge.completed_count,
        'progress_percentage': challenge.progress_percentage,
        'view_count': challenge.view_count,
        'is_complete': challenge.is_complete,
        'completed_at': challenge.completed_at.isoformat() if challenge.completed_at else None,
        'created_at': challenge.created_at.isoformat(),
        'updated_at': challenge.updated_at.isoformat(),
        'cover_letter': challenge.cover_letter,
        'cover_image_url': _resolve_cover_image(challenge),
        'author': {
            'psn_username': challenge.profile.psn_username,
            'avatar_url': challenge.profile.avatar_url or '',
        },
    }
    if include_slots:
        slots = challenge.az_slots.select_related('game').all()
        data['slots'] = [_serialize_slot(s) for s in slots]
    return data


# ─── API Views ───────────────────────────────────────────────────────────────────

class AZChallengeCreateAPIView(APIView):
    """Create a new A-Z Challenge."""
    authentication_classes = [SessionAuthentication, TokenAuthentication]
    permission_classes = [IsAuthenticated]

    @method_decorator(ratelimit(key='user', rate='10/h', method='POST', block=True))
    def post(self, request):
        try:
            profile, error = _get_profile_or_error(request)
            if error:
                return error

            name = (request.data.get('name') or 'My A-Z Challenge').strip()[:75]
            if not name:
                name = 'My A-Z Challenge'

            challenge = create_az_challenge(profile, name=name)
            return Response(
                _serialize_challenge(challenge, include_slots=True),
                status=status.HTTP_201_CREATED,
            )

        except ValueError as e:
            return Response({'error': str(e)}, status=status.HTTP_409_CONFLICT)
        except Exception as e:
            logger.exception(f"AZ Challenge create error: {e}")
            return Response(
                {'error': 'Internal error.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class AZChallengeDetailAPIView(APIView):
    """Get challenge details with all slots."""
    authentication_classes = [SessionAuthentication, TokenAuthentication]
    permission_classes = []

    @method_decorator(ratelimit(key='ip', rate='60/m', method='GET', block=True))
    def get(self, request, challenge_id):
        try:
            try:
                challenge = Challenge.objects.select_related('profile').get(
                    id=challenge_id, is_deleted=False, challenge_type='az',
                )
            except Challenge.DoesNotExist:
                return Response(
                    {'error': 'Challenge not found.'},
                    status=status.HTTP_404_NOT_FOUND,
                )

            return Response(_serialize_challenge(challenge, include_slots=True))

        except Exception as e:
            logger.exception(f"AZ Challenge detail error: {e}")
            return Response(
                {'error': 'Internal error.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class AZChallengeUpdateAPIView(APIView):
    """Update challenge name/description."""
    authentication_classes = [SessionAuthentication, TokenAuthentication]
    permission_classes = [IsAuthenticated]

    @method_decorator(ratelimit(key='user', rate='30/m', method='PATCH', block=True))
    def patch(self, request, challenge_id):
        try:
            profile, error = _get_profile_or_error(request)
            if error:
                return error

            challenge, error = _get_owned_challenge(challenge_id, profile)
            if error:
                return error

            update_fields = ['updated_at']

            name = request.data.get('name')
            if name is not None:
                name = name.strip()[:75]
                if name:
                    challenge.name = name
                    update_fields.append('name')

            description = request.data.get('description')
            if description is not None:
                challenge.description = description.strip()[:2000]
                update_fields.append('description')

            cover_letter = request.data.get('cover_letter')
            if cover_letter is not None:
                cover_letter = cover_letter.strip().upper()
                if len(cover_letter) != 1 or not cover_letter.isalpha():
                    return Response(
                        {'error': 'cover_letter must be a single letter A-Z.'},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                # Validate the slot has a game assigned
                if not challenge.az_slots.filter(letter=cover_letter, game__isnull=False).exists():
                    return Response(
                        {'error': 'That slot has no game assigned.'},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                challenge.cover_letter = cover_letter
                update_fields.append('cover_letter')

            if len(update_fields) > 1:
                challenge.save(update_fields=update_fields)

            return Response(_serialize_challenge(challenge))

        except Exception as e:
            logger.exception(f"AZ Challenge update error: {e}")
            return Response(
                {'error': 'Internal error.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class AZChallengeDeleteAPIView(APIView):
    """Soft delete a challenge."""
    authentication_classes = [SessionAuthentication, TokenAuthentication]
    permission_classes = [IsAuthenticated]

    @method_decorator(ratelimit(key='user', rate='10/m', method='DELETE', block=True))
    def delete(self, request, challenge_id):
        try:
            profile, error = _get_profile_or_error(request)
            if error:
                return error

            challenge, error = _get_owned_challenge(challenge_id, profile)
            if error:
                return error

            challenge.soft_delete()
            return Response({'success': True})

        except Exception as e:
            logger.exception(f"AZ Challenge delete error: {e}")
            return Response(
                {'error': 'Internal error.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class AZSlotAssignAPIView(APIView):
    """Assign a game to a letter slot."""
    authentication_classes = [SessionAuthentication, TokenAuthentication]
    permission_classes = [IsAuthenticated]

    @method_decorator(ratelimit(key='user', rate='60/m', method='POST', block=True))
    def post(self, request, challenge_id, letter):
        try:
            profile, error = _get_profile_or_error(request)
            if error:
                return error

            challenge, error = _get_owned_challenge(challenge_id, profile)
            if error:
                return error

            # Validate letter
            letter = letter.upper()
            if len(letter) != 1 or not letter.isalpha():
                return Response(
                    {'error': 'Invalid letter.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            # Get slot
            try:
                slot = challenge.az_slots.get(letter=letter)
            except AZChallengeSlot.DoesNotExist:
                return Response(
                    {'error': 'Slot not found.'},
                    status=status.HTTP_404_NOT_FOUND,
                )

            # Can't change completed slots
            if slot.is_completed:
                return Response(
                    {'error': 'Cannot change a completed slot.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            # Validate game
            game_id = safe_int(request.data.get('game_id'), None)
            if not game_id:
                return Response(
                    {'error': 'game_id is required.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            try:
                game = Game.objects.get(id=game_id)
            except Game.DoesNotExist:
                return Response(
                    {'error': 'Game not found.'},
                    status=status.HTTP_404_NOT_FOUND,
                )

            # Validate game title starts with this letter
            if not game.title_name or game.title_name[0].upper() != letter:
                return Response(
                    {'error': f'Game title must start with "{letter}".'},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            # Validate game isn't excluded (platted or >50% progress)
            excluded_ids = get_excluded_game_ids(profile)
            if game.id in excluded_ids:
                return Response(
                    {'error': 'This game is excluded. You\'ve platted it (or a related version), or have over 50% progress.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            # Validate game isn't assigned to another slot in this challenge
            if challenge.az_slots.filter(game=game).exclude(letter=letter).exists():
                return Response(
                    {'error': 'This game is already assigned to another letter.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            # Assign
            slot.game = game
            slot.assigned_at = timezone.now()
            slot.save(update_fields=['game', 'assigned_at'])

            # Recalculate counts
            recalculate_challenge_counts(challenge)
            challenge.save(update_fields=['filled_count', 'completed_count', 'updated_at'])

            # Auto-set cover letter on first assignment
            if not challenge.cover_letter:
                auto_set_cover_letter(challenge)

            response_data = _serialize_slot(slot)
            response_data['cover_letter'] = challenge.cover_letter
            return Response(response_data)

        except Exception as e:
            logger.exception(f"AZ slot assign error: {e}")
            return Response(
                {'error': 'Internal error.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class AZSlotClearAPIView(APIView):
    """Clear a game from a letter slot."""
    authentication_classes = [SessionAuthentication, TokenAuthentication]
    permission_classes = [IsAuthenticated]

    @method_decorator(ratelimit(key='user', rate='60/m', method='DELETE', block=True))
    def delete(self, request, challenge_id, letter):
        try:
            profile, error = _get_profile_or_error(request)
            if error:
                return error

            challenge, error = _get_owned_challenge(challenge_id, profile)
            if error:
                return error

            letter = letter.upper()
            if len(letter) != 1 or not letter.isalpha():
                return Response(
                    {'error': 'Invalid letter.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            try:
                slot = challenge.az_slots.get(letter=letter)
            except AZChallengeSlot.DoesNotExist:
                return Response(
                    {'error': 'Slot not found.'},
                    status=status.HTTP_404_NOT_FOUND,
                )

            if slot.is_completed:
                return Response(
                    {'error': 'Cannot clear a completed slot.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            if not slot.game:
                return Response(
                    {'error': 'Slot is already empty.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            was_cover = (slot.letter == challenge.cover_letter)

            slot.game = None
            slot.assigned_at = None
            slot.save(update_fields=['game', 'assigned_at'])

            recalculate_challenge_counts(challenge)
            challenge.save(update_fields=['filled_count', 'completed_count', 'updated_at'])

            # Re-pick cover if the cleared slot was the cover
            if was_cover:
                auto_set_cover_letter(challenge)

            response_data = _serialize_slot(slot)
            response_data['cover_letter'] = challenge.cover_letter
            return Response(response_data)

        except Exception as e:
            logger.exception(f"AZ slot clear error: {e}")
            return Response(
                {'error': 'Internal error.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class AZGameSearchAPIView(APIView):
    """Search games filtered by letter, with multi-select platform/region filters,
    sort options, pagination, plat earner counts, and community ratings."""
    authentication_classes = [SessionAuthentication, TokenAuthentication]
    permission_classes = [IsAuthenticated]

    @method_decorator(ratelimit(key='user', rate='120/m', method='GET', block=True))
    def get(self, request):
        """
        GET /api/v1/challenges/az/game-search/?letter=A&q=<query>&challenge_id=X
            &platform=PS5,PS4&region=NA,EU&sort=popular&offset=0&limit=20
        """
        try:
            profile, error = _get_profile_or_error(request)
            if error:
                return error

            letter = (request.query_params.get('letter') or '').strip().upper()
            if len(letter) != 1 or not letter.isalpha():
                return Response(
                    {'error': 'A single letter (A-Z) is required.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            query = (request.query_params.get('q') or '').strip()
            limit = min(safe_int(request.query_params.get('limit', 20), 20), 50)
            offset = max(safe_int(request.query_params.get('offset', 0), 0), 0)
            challenge_id = safe_int(request.query_params.get('challenge_id'), None)
            sort = (request.query_params.get('sort') or 'popular').strip()

            # Parse filter params
            platforms_raw = (request.query_params.get('platform') or '').strip()
            platforms = [
                p for p in platforms_raw.split(',') if p in ALL_PLATFORMS
            ] if platforms_raw else []

            regions_raw = (request.query_params.get('region') or '').strip()
            valid_regions = REGIONS + ['global']
            regions = [
                r for r in regions_raw.split(',') if r in valid_regions
            ] if regions_raw else []

            # Base query: games starting with this letter, quality-filtered
            games = Game.objects.filter(
                title_name__istartswith=letter,
                is_obtainable=True,
                is_shovelware=False,
            )

            # Only games that have a platinum trophy
            games = games.filter(trophies__trophy_type='platinum').distinct()

            # Optional text filter within the letter
            if query and len(query) >= 2:
                games = games.filter(title_name__icontains=query)

            # Platform filter
            if platforms:
                games = games.for_platform(platforms)

            # Region filter
            if regions:
                games = games.for_region(regions)

            # Exclude platted + >50% progress games
            excluded_ids = get_excluded_game_ids(profile)
            if excluded_ids:
                games = games.exclude(id__in=excluded_ids)

            # Exclude games already assigned in this challenge
            if challenge_id:
                used_game_ids = set(
                    AZChallengeSlot.objects.filter(
                        challenge_id=challenge_id, game__isnull=False,
                    ).values_list('game_id', flat=True)
                )
                if used_game_ids:
                    games = games.exclude(id__in=used_game_ids)

            # Annotate plat earner count (always, for display)
            plat_sub = Trophy.objects.filter(
                game=OuterRef('pk'), trophy_type='platinum',
            ).values('earned_count')[:1]
            games = games.annotate(
                plat_earner_count=Subquery(plat_sub, output_field=IntegerField()),
            )

            # Sort
            if sort == 'alpha':
                games = games.order_by(Lower('title_name'))
            elif sort == 'plat_earners':
                games = games.order_by(
                    F('plat_earner_count').desc(nulls_last=True),
                )
            else:  # 'popular' default
                games = games.order_by('-played_count')

            # Pagination: fetch limit+1 to detect has_more
            games_list = list(games[offset:offset + limit + 1])
            has_more = len(games_list) > limit
            games_list = games_list[:limit]

            # Batch community ratings for the fetched games
            concept_ids = [g.concept_id for g in games_list if g.concept_id]
            ratings_map = {}
            if concept_ids:
                ratings_qs = UserConceptRating.objects.filter(
                    concept_id__in=concept_ids,
                ).values('concept_id').annotate(
                    avg_difficulty=Avg('difficulty'),
                    avg_grindiness=Avg('grindiness'),
                    avg_fun=Avg('fun_ranking'),
                    avg_overall=Avg('overall_rating'),
                    avg_hours=Avg('hours_to_platinum'),
                    rating_count=Count('id'),
                )
                for r in ratings_qs:
                    ratings_map[r['concept_id']] = {
                        'difficulty': round(r['avg_difficulty'], 1) if r['avg_difficulty'] else None,
                        'grindiness': round(r['avg_grindiness'], 1) if r['avg_grindiness'] else None,
                        'fun': round(r['avg_fun'], 1) if r['avg_fun'] else None,
                        'overall': round(r['avg_overall'], 1) if r['avg_overall'] else None,
                        'hours': round(r['avg_hours'], 1) if r['avg_hours'] else None,
                        'count': r['rating_count'],
                    }

            results = []
            for g in games_list:
                results.append({
                    'id': g.id,
                    'title_name': g.title_name,
                    'title_image': g.title_image or '',
                    'title_icon_url': g.title_icon_url or '',
                    'title_platform': g.title_platform or [],
                    'is_regional': g.is_regional,
                    'region': g.region or [],
                    'defined_trophies': g.defined_trophies or {},
                    'played_count': g.played_count,
                    'plat_earners': getattr(g, 'plat_earner_count', None) or 0,
                    'community_ratings': ratings_map.get(g.concept_id, {}),
                })

            return Response({'results': results, 'has_more': has_more})

        except Exception as e:
            logger.exception(f"AZ game search error: {e}")
            return Response(
                {'error': 'Internal error.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
