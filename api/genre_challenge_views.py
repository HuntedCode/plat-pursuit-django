"""
Genre Challenge API views.

Handles REST endpoints for Genre Challenges: CRUD, slot assignment/clearing,
and concept search with genre/subgenre filtering, platform/region filters,
and community ratings.
"""
import logging

from django.db.models import Avg, Count, F, Q
from django.utils import timezone
from django.utils.decorators import method_decorator
from django_ratelimit.decorators import ratelimit
from rest_framework import status
from rest_framework.authentication import SessionAuthentication, TokenAuthentication
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from django.db.models.functions import Lower

from trophies.models import (
    Challenge, GenreChallengeSlot, GenreBonusSlot, Concept, Game,
    UserConceptRating, Badge, ProfileGame,
)
from trophies.services.challenge_service import (
    create_genre_challenge, recalculate_challenge_counts, auto_set_cover_genre,
    get_genre_excluded_concept_ids, get_subgenre_status, get_collected_subgenres,
    resolve_subgenres, get_genre_swap_targets,
)
from trophies.util_modules.constants import (
    GENRE_CHALLENGE_GENRES, GENRE_DISPLAY_NAMES, GENRE_MERGE_MAP,
    GENRE_CHALLENGE_SUBGENRES, SUBGENRE_DISPLAY_NAMES,
    ALL_PLATFORMS, REGIONS,
)
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
        challenge = Challenge.objects.get(
            id=challenge_id, is_deleted=False, challenge_type='genre',
        )
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
    """Serialize a single GenreChallengeSlot to a dict."""
    data = {
        'genre': slot.genre,
        'genre_display': slot.genre_display,
        'is_completed': slot.is_completed,
        'completed_at': slot.completed_at.isoformat() if slot.completed_at else None,
        'assigned_at': slot.assigned_at.isoformat() if slot.assigned_at else None,
        'concept': None,
    }
    if slot.concept:
        data['concept'] = {
            'id': slot.concept.id,
            'concept_id': slot.concept.concept_id,
            'unified_title': slot.concept.unified_title,
            'concept_icon_url': slot.concept.concept_icon_url or '',
            'genres': slot.concept.genres or [],
            'subgenres': slot.concept.subgenres or [],
            'resolved_subgenres': [
                {'key': sg, 'display': SUBGENRE_DISPLAY_NAMES.get(sg, sg)}
                for sg in sorted(resolve_subgenres(slot.concept.subgenres or []))
            ],
        }
    return data


def _resolve_cover_image(challenge):
    """Resolve cover image URL from cover_genre -> slot -> concept.
    Uses prefetch cache when available (iterates .all()), falls back to
    a single targeted query otherwise."""
    if not challenge.cover_genre:
        return ''
    # If genre_slots are prefetched, iterate in-memory to avoid extra query
    if hasattr(challenge, '_prefetched_objects_cache') and 'genre_slots' in challenge._prefetched_objects_cache:
        for slot in challenge.genre_slots.all():
            if slot.genre == challenge.cover_genre and slot.concept:
                return slot.concept.concept_icon_url or ''
        return ''
    slot = challenge.genre_slots.filter(
        genre=challenge.cover_genre, concept__isnull=False,
    ).select_related('concept').first()
    return slot.concept.concept_icon_url or '' if slot else ''


def _serialize_challenge(challenge, include_slots=False):
    """Serialize a Genre Challenge to a dict."""
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
        'cover_genre': challenge.cover_genre,
        'cover_image_url': _resolve_cover_image(challenge),
        'subgenre_count': challenge.subgenre_count,
        'subgenre_total': len(GENRE_CHALLENGE_SUBGENRES),
        'author': {
            'psn_username': challenge.profile.psn_username,
            'avatar_url': challenge.profile.avatar_url or '',
        },
    }
    if include_slots:
        subgenre_status = get_subgenre_status(challenge)
        slots = challenge.genre_slots.all()
        data['slots'] = [_serialize_slot(s) for s in slots]
        data['collected_subgenres'] = [
            {
                'key': sg,
                'display': SUBGENRE_DISPLAY_NAMES.get(sg, sg),
                'status': subgenre_status[sg],
            }
            for sg in sorted(subgenre_status.keys())
        ]
    return data


# ─── API Views ───────────────────────────────────────────────────────────────────

class GenreChallengeCreateAPIView(APIView):
    """Create a new Genre Challenge."""
    authentication_classes = [SessionAuthentication, TokenAuthentication]
    permission_classes = [IsAuthenticated]

    @method_decorator(ratelimit(key='user', rate='10/h', method='POST', block=True))
    def post(self, request):
        try:
            profile, error = _get_profile_or_error(request)
            if error:
                return error

            name = (request.data.get('name') or 'My Genre Challenge').strip()[:75]
            if not name:
                name = 'My Genre Challenge'

            challenge = create_genre_challenge(profile, name=name)
            return Response(
                _serialize_challenge(challenge, include_slots=True),
                status=status.HTTP_201_CREATED,
            )

        except ValueError as e:
            return Response({'error': str(e)}, status=status.HTTP_409_CONFLICT)
        except Exception:
            logger.exception("Genre Challenge create error")
            return Response(
                {'error': 'Internal error.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class GenreChallengeDetailAPIView(APIView):
    """Get genre challenge details with all slots and subgenre tracker."""
    authentication_classes = [SessionAuthentication, TokenAuthentication]
    permission_classes = []

    @method_decorator(ratelimit(key='ip', rate='60/m', method='GET', block=True))
    def get(self, request, challenge_id):
        try:
            try:
                challenge = Challenge.objects.select_related('profile').prefetch_related(
                    'genre_slots__concept', 'bonus_slots__concept',
                ).get(
                    id=challenge_id, is_deleted=False, challenge_type='genre',
                )
            except Challenge.DoesNotExist:
                return Response(
                    {'error': 'Challenge not found.'},
                    status=status.HTTP_404_NOT_FOUND,
                )

            return Response(_serialize_challenge(challenge, include_slots=True))

        except Exception:
            logger.exception("Genre Challenge detail error")
            return Response(
                {'error': 'Internal error.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class GenreChallengeUpdateAPIView(APIView):
    """Update genre challenge name/description/cover_genre."""
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

            cover_genre = request.data.get('cover_genre')
            if cover_genre is not None:
                cover_genre = cover_genre.strip().upper()
                if cover_genre not in GENRE_CHALLENGE_GENRES:
                    return Response(
                        {'error': 'Invalid genre.'},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                # Validate the slot has a concept assigned
                if not challenge.genre_slots.filter(
                    genre=cover_genre, concept__isnull=False
                ).exists():
                    return Response(
                        {'error': 'That genre slot has no game assigned.'},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                challenge.cover_genre = cover_genre
                update_fields.append('cover_genre')

            if len(update_fields) > 1:
                challenge.save(update_fields=update_fields)

            return Response(_serialize_challenge(challenge))

        except Exception:
            logger.exception("Genre Challenge update error")
            return Response(
                {'error': 'Internal error.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class GenreChallengeDeleteAPIView(APIView):
    """Soft delete a genre challenge."""
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

        except Exception:
            logger.exception("Genre Challenge delete error")
            return Response(
                {'error': 'Internal error.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class GenreSlotAssignAPIView(APIView):
    """Assign a concept to a genre slot."""
    authentication_classes = [SessionAuthentication, TokenAuthentication]
    permission_classes = [IsAuthenticated]

    @method_decorator(ratelimit(key='user', rate='60/m', method='POST', block=True))
    def post(self, request, challenge_id, genre):
        try:
            profile, error = _get_profile_or_error(request)
            if error:
                return error

            challenge, error = _get_owned_challenge(challenge_id, profile)
            if error:
                return error

            # Validate genre
            genre = genre.upper()
            if genre not in GENRE_CHALLENGE_GENRES:
                return Response(
                    {'error': 'Invalid genre.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            # Get slot
            try:
                slot = challenge.genre_slots.get(genre=genre)
            except GenreChallengeSlot.DoesNotExist:
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

            # Validate concept
            concept_id = safe_int(request.data.get('concept_id'), None)
            if not concept_id:
                return Response(
                    {'error': 'A game selection is required.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            try:
                concept = Concept.objects.get(id=concept_id)
            except Concept.DoesNotExist:
                return Response(
                    {'error': 'Game not found.'},
                    status=status.HTTP_404_NOT_FOUND,
                )

            # Reject PP_ stubs (no genre data)
            if concept.concept_id.startswith('PP_'):
                return Response(
                    {'error': 'This game is not eligible.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            # Validate concept has a PS4/PS5 game
            has_modern = Game.objects.filter(
                concept=concept,
            ).filter(
                Q(title_platform__contains='PS4') | Q(title_platform__contains='PS5')
            ).exists()
            if not has_modern:
                return Response(
                    {'error': 'This game needs at least one PS4 or PS5 version.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            # Validate concept has this genre (handle SIMULATION/SIMULATOR merge)
            concept_genres = concept.genres or []
            target_genres = [genre]
            # Add reverse-mapped genres (e.g. for SIMULATION, also accept SIMULATOR)
            for raw_key, mapped_key in GENRE_MERGE_MAP.items():
                if mapped_key == genre:
                    target_genres.append(raw_key)
            if not any(g in concept_genres for g in target_genres):
                return Response(
                    {'error': f'This game is not tagged as {GENRE_DISPLAY_NAMES.get(genre, genre)}.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            # Validate concept isn't excluded (platted or >50% progress)
            excluded_ids = get_genre_excluded_concept_ids(profile)
            if concept.id in excluded_ids:
                return Response(
                    {'error': "This game is excluded. You've already platted it or have over 50% progress."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            # Validate concept isn't assigned to another slot in this challenge
            if challenge.genre_slots.filter(concept=concept).exclude(genre=genre).exists():
                return Response(
                    {'error': 'This game is already assigned to another genre slot.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            # Validate concept isn't already in a bonus slot
            if challenge.bonus_slots.filter(concept=concept).exists():
                return Response(
                    {'error': 'This game is already in your bonus list. Move it to a genre slot instead.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            # Assign
            slot.concept = concept
            slot.assigned_at = timezone.now()
            slot.save(update_fields=['concept', 'assigned_at'])

            # Recalculate counts and persist
            recalculate_challenge_counts(challenge)
            challenge.save(update_fields=[
                'filled_count', 'completed_count', 'subgenre_count',
                'bonus_count', 'updated_at',
            ])

            # Auto-set cover genre on first assignment
            if not challenge.cover_genre:
                auto_set_cover_genre(challenge)

            response_data = _serialize_slot(slot)
            response_data['cover_genre'] = challenge.cover_genre
            response_data['subgenre_count'] = challenge.subgenre_count
            response_data['subgenre_total'] = len(GENRE_CHALLENGE_SUBGENRES)
            response_data['bonus_count'] = challenge.bonus_count
            # Return updated collected subgenres with status for live tracker update
            sg_status = get_subgenre_status(challenge)
            response_data['collected_subgenres'] = [
                {
                    'key': sg,
                    'display': SUBGENRE_DISPLAY_NAMES.get(sg, sg),
                    'status': sg_status[sg],
                }
                for sg in sorted(sg_status.keys())
            ]
            return Response(response_data)

        except Exception:
            logger.exception("Genre slot assign error")
            return Response(
                {'error': 'Internal error.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class GenreSlotClearAPIView(APIView):
    """Clear a concept from a genre slot."""
    authentication_classes = [SessionAuthentication, TokenAuthentication]
    permission_classes = [IsAuthenticated]

    @method_decorator(ratelimit(key='user', rate='60/m', method='DELETE', block=True))
    def delete(self, request, challenge_id, genre):
        try:
            profile, error = _get_profile_or_error(request)
            if error:
                return error

            challenge, error = _get_owned_challenge(challenge_id, profile)
            if error:
                return error

            genre = genre.upper()
            if genre not in GENRE_CHALLENGE_GENRES:
                return Response(
                    {'error': 'Invalid genre.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            try:
                slot = challenge.genre_slots.get(genre=genre)
            except GenreChallengeSlot.DoesNotExist:
                return Response(
                    {'error': 'Slot not found.'},
                    status=status.HTTP_404_NOT_FOUND,
                )

            if slot.is_completed:
                return Response(
                    {'error': 'Cannot clear a completed slot.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            if not slot.concept:
                return Response(
                    {'error': 'Slot is already empty.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            was_cover = (slot.genre == challenge.cover_genre)

            slot.concept = None
            slot.assigned_at = None
            slot.save(update_fields=['concept', 'assigned_at'])

            recalculate_challenge_counts(challenge)
            challenge.save(update_fields=[
                'filled_count', 'completed_count', 'subgenre_count',
                'bonus_count', 'updated_at',
            ])

            # Re-pick cover if the cleared slot was the cover
            if was_cover:
                auto_set_cover_genre(challenge)

            response_data = _serialize_slot(slot)
            response_data['cover_genre'] = challenge.cover_genre
            response_data['subgenre_count'] = challenge.subgenre_count
            response_data['subgenre_total'] = len(GENRE_CHALLENGE_SUBGENRES)
            response_data['bonus_count'] = challenge.bonus_count
            sg_status = get_subgenre_status(challenge)
            response_data['collected_subgenres'] = [
                {
                    'key': sg,
                    'display': SUBGENRE_DISPLAY_NAMES.get(sg, sg),
                    'status': sg_status[sg],
                }
                for sg in sorted(sg_status.keys())
            ]
            return Response(response_data)

        except Exception:
            logger.exception("Genre slot clear error")
            return Response(
                {'error': 'Internal error.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class GenreConceptSearchAPIView(APIView):
    """Search concepts filtered by genre, with subgenre/platform/region filters,
    sort options, pagination, and community ratings."""
    authentication_classes = [SessionAuthentication, TokenAuthentication]
    permission_classes = [IsAuthenticated]

    @method_decorator(ratelimit(key='user', rate='120/m', method='GET', block=True))
    def get(self, request):
        """
        GET /api/v1/challenges/genre/concept-search/?genre=ACTION&q=<query>&challenge_id=X
            &subgenre=PLATFORMER&platform=PS5,PS4&region=NA,EU&sort=popular&offset=0&limit=20
        """
        try:
            profile, error = _get_profile_or_error(request)
            if error:
                return error

            mode = (request.query_params.get('mode') or 'genre').strip()
            genre = (request.query_params.get('genre') or '').strip().upper()

            if mode == 'genre':
                if genre not in GENRE_CHALLENGE_GENRES:
                    return Response(
                        {'error': 'A valid genre is required.'},
                        status=status.HTTP_400_BAD_REQUEST,
                    )

            query = (request.query_params.get('q') or '').strip()
            limit = min(safe_int(request.query_params.get('limit', 20), 20), 50)
            offset = max(safe_int(request.query_params.get('offset', 0), 0), 0)
            challenge_id = safe_int(request.query_params.get('challenge_id'), None)
            sort = (request.query_params.get('sort') or 'popular').strip()

            # Optional subgenre filter(s) - comma-separated for multi-select
            subgenre_raw = (request.query_params.get('subgenre') or '').strip().upper()
            subgenre_filters = [
                s for s in subgenre_raw.split(',') if s in SUBGENRE_DISPLAY_NAMES
            ] if subgenre_raw else []

            # Parse platform/region filters
            platforms_raw = (request.query_params.get('platform') or '').strip()
            platforms = [
                p for p in platforms_raw.split(',') if p in ALL_PLATFORMS
            ] if platforms_raw else []

            regions_raw = (request.query_params.get('region') or '').strip()
            valid_regions = REGIONS + ['global']
            regions = [
                r for r in regions_raw.split(',') if r in valid_regions
            ] if regions_raw else []

            # Boolean toggle filters
            in_badge = request.query_params.get('in_badge', '') == '1'
            my_backlog = request.query_params.get('my_backlog', '') == '1'
            new_subgenres_only = request.query_params.get('new_subgenres_only', '') == '1'

            # Base query: exclude PP_ stubs
            concepts = Concept.objects.exclude(concept_id__startswith='PP_')

            # Genre filter (skip for bonus mode: any genre is fine)
            if mode == 'genre' and genre:
                genre_q = Q(genres__contains=[genre])
                # Handle SIMULATION/SIMULATOR merge
                for raw_key, mapped_key in GENRE_MERGE_MAP.items():
                    if mapped_key == genre:
                        genre_q = genre_q | Q(genres__contains=[raw_key])
                concepts = concepts.filter(genre_q)

            # Must have at least one PS4/PS5 game with a platinum
            concepts = concepts.filter(
                Q(games__title_platform__contains='PS4') |
                Q(games__title_platform__contains='PS5'),
                games__trophies__trophy_type='platinum',
            ).distinct()

            # Optional text search
            if query and len(query) >= 2:
                concepts = concepts.filter(unified_title__icontains=query)

            # Subgenre filter: concepts must have ALL selected subgenres (AND logic)
            if subgenre_filters:
                from trophies.util_modules.constants import SUBGENRE_MERGE_MAP as SGM
                for sg_filter in subgenre_filters:
                    # Build list of raw PSN keys that map to this curated subgenre
                    raw_keys = [sg_filter]
                    for raw_key, mapped_key in GENRE_MERGE_MAP.items():
                        if mapped_key == sg_filter:
                            raw_keys.append(raw_key)
                    for raw_key, mapped_key in SGM.items():
                        if mapped_key == sg_filter:
                            raw_keys.append(raw_key)
                    subgenre_q = Q()
                    for key in raw_keys:
                        subgenre_q = subgenre_q | Q(subgenres__contains=[key])
                    concepts = concepts.filter(subgenre_q)

            # Platform filter (through related games)
            if platforms:
                platform_q = Q()
                for p in platforms:
                    platform_q = platform_q | Q(games__title_platform__contains=p)
                concepts = concepts.filter(platform_q).distinct()

            # Region filter (through related games)
            if regions:
                region_q = Q()
                for r in regions:
                    if r == 'global':
                        region_q = region_q | Q(games__is_regional=False)
                    else:
                        region_q = region_q | Q(games__region__contains=[r])
                concepts = concepts.filter(region_q).distinct()

            # "In a Badge" filter: concepts in non-optional stages of live badges
            if in_badge:
                concepts = concepts.filter(
                    stages__stage_number__gt=0,
                    stages__series_slug__in=Badge.objects.filter(
                        is_live=True, series_slug__isnull=False,
                    ).values_list('series_slug', flat=True),
                ).distinct()

            # "My Backlog" filter: concepts where user has played a game under it
            if my_backlog:
                concepts = concepts.filter(
                    games__played_by__profile=profile,
                ).distinct()

            # Exclude concepts where ALL games are shovelware
            shovelware_ids = Concept.objects.filter(
                games__isnull=False,
            ).annotate(
                total_games=Count('games'),
                sw_games=Count('games', filter=Q(
                    games__shovelware_status__in=['auto_flagged', 'manually_flagged']
                )),
            ).filter(
                total_games=F('sw_games'),
            ).values_list('id', flat=True)
            concepts = concepts.exclude(id__in=shovelware_ids)

            # Exclude platted / >50% progress concepts
            excluded_ids = get_genre_excluded_concept_ids(profile)
            if excluded_ids:
                concepts = concepts.exclude(id__in=excluded_ids)

            # Exclude concepts already assigned in this challenge (genre slots + bonus slots)
            if challenge_id:
                used_concept_ids = set(
                    GenreChallengeSlot.objects.filter(
                        challenge_id=challenge_id, concept__isnull=False,
                    ).values_list('concept_id', flat=True)
                )
                bonus_concept_ids = set(
                    GenreBonusSlot.objects.filter(
                        challenge_id=challenge_id, concept__isnull=False,
                    ).values_list('concept_id', flat=True)
                )
                all_used = used_concept_ids | bonus_concept_ids
                if all_used:
                    concepts = concepts.exclude(id__in=all_used)

            # Collect subgenres already in this challenge (used for "new" indicator and new_subgenres_only filter)
            already_collected = set()
            if challenge_id:
                try:
                    ch = Challenge.objects.get(id=challenge_id, challenge_type='genre')
                    already_collected = get_collected_subgenres(ch)
                except Challenge.DoesNotExist:
                    pass

            # "New Subgenres Only" filter: exclude games whose subgenres are all already collected
            if new_subgenres_only:
                candidates = list(concepts.values_list('id', 'subgenres'))
                exclude_ids = set()
                for cid, raw_sgs in candidates:
                    resolved = resolve_subgenres(raw_sgs or [])
                    if not resolved or resolved.issubset(already_collected):
                        exclude_ids.add(cid)
                if exclude_ids:
                    concepts = concepts.exclude(id__in=exclude_ids)

            # Annotate played_count (sum of played_count across all games)
            concepts = concepts.annotate(
                total_played=Count('games__played_by', distinct=True),
            )

            # Sort
            if sort == 'alpha':
                concepts = concepts.order_by(Lower('unified_title'))
            elif sort == 'plat_earners':
                # Sort by total plat earners across games
                from django.db.models import Sum
                concepts = concepts.annotate(
                    total_plat_earners=Sum('games__trophies__earned_count', filter=Q(
                        games__trophies__trophy_type='platinum'
                    )),
                ).order_by('-total_plat_earners')
            else:  # 'popular' default
                concepts = concepts.order_by('-total_played')

            # Subgenre facet counts from the filtered queryset (before pagination)
            subgenre_counts = {}
            for raw_list in concepts.values_list('subgenres', flat=True):
                if not raw_list:
                    continue
                for sg_key in resolve_subgenres(raw_list):
                    subgenre_counts[sg_key] = subgenre_counts.get(sg_key, 0) + 1

            # Pagination
            concepts_list = list(concepts[offset:offset + limit + 1])
            has_more = len(concepts_list) > limit
            concepts_list = concepts_list[:limit]

            # Batch community ratings
            concept_ids = [c.id for c in concepts_list]
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

            # Get platform info for each concept
            concept_platform_map = {}
            if concept_ids:
                for game_concept_id, platform in Game.objects.filter(
                    concept_id__in=concept_ids,
                ).values_list('concept_id', 'title_platform'):
                    if game_concept_id not in concept_platform_map:
                        concept_platform_map[game_concept_id] = set()
                    if platform:
                        for p in platform:
                            concept_platform_map[game_concept_id].add(p)

            results = []
            for c in concepts_list:
                # Resolve subgenres for this concept
                raw_sgs = c.subgenres or []
                resolved = resolve_subgenres(raw_sgs)
                subgenres_data = [
                    {
                        'key': sg,
                        'display': SUBGENRE_DISPLAY_NAMES.get(sg, sg),
                        'is_new': sg not in already_collected,
                    }
                    for sg in sorted(resolved)
                ]

                platforms_set = concept_platform_map.get(c.id, set())

                results.append({
                    'id': c.id,
                    'concept_id': c.concept_id,
                    'unified_title': c.unified_title,
                    'concept_icon_url': c.concept_icon_url or '',
                    'genres': c.genres or [],
                    'subgenres': subgenres_data,
                    'platforms': sorted(platforms_set),
                    'total_played': getattr(c, 'total_played', 0) or 0,
                    'community_ratings': ratings_map.get(c.id, {}),
                })

            return Response({
                'results': results,
                'has_more': has_more,
                'subgenre_counts': subgenre_counts,
            })

        except Exception:
            logger.exception("Genre concept search error")
            return Response(
                {'error': 'Internal error.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


# ─── Helper: Serialize Bonus Slot ─────────────────────────────────────────────

def _serialize_bonus_slot(slot):
    """Serialize a single GenreBonusSlot to a dict."""
    data = {
        'id': slot.id,
        'is_completed': slot.is_completed,
        'completed_at': slot.completed_at.isoformat() if slot.completed_at else None,
        'assigned_at': slot.assigned_at.isoformat() if slot.assigned_at else None,
        'concept': None,
    }
    if slot.concept:
        data['concept'] = {
            'id': slot.concept.id,
            'concept_id': slot.concept.concept_id,
            'unified_title': slot.concept.unified_title,
            'concept_icon_url': slot.concept.concept_icon_url or '',
            'genres': slot.concept.genres or [],
            'subgenres': slot.concept.subgenres or [],
            'resolved_subgenres': [
                {'key': sg, 'display': SUBGENRE_DISPLAY_NAMES.get(sg, sg)}
                for sg in sorted(resolve_subgenres(slot.concept.subgenres or []))
            ],
        }
    return data


def _build_genre_response_extras(challenge):
    """Build common response fields for genre challenge endpoints."""
    subgenre_status = get_subgenre_status(challenge)
    return {
        'cover_genre': challenge.cover_genre,
        'subgenre_count': challenge.subgenre_count,
        'subgenre_total': len(GENRE_CHALLENGE_SUBGENRES),
        'bonus_count': challenge.bonus_count,
        'collected_subgenres': [
            {
                'key': sg,
                'display': SUBGENRE_DISPLAY_NAMES.get(sg, sg),
                'status': subgenre_status[sg],
            }
            for sg in sorted(subgenre_status.keys())
        ],
    }


# ─── Bonus Slot Endpoints ────────────────────────────────────────────────────

class GenreBonusAddAPIView(APIView):
    """Add a bonus game to a genre challenge for subgenre hunting."""
    authentication_classes = [SessionAuthentication, TokenAuthentication]
    permission_classes = [IsAuthenticated]

    @method_decorator(ratelimit(key='user', rate='60/m', method='POST', block=True))
    def post(self, request, challenge_id):
        try:
            profile, error = _get_profile_or_error(request)
            if error:
                return error

            challenge, error = _get_owned_challenge(challenge_id, profile)
            if error:
                return error

            concept_id = safe_int(request.data.get('concept_id'), None)
            if not concept_id:
                return Response(
                    {'error': 'A game selection is required.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            try:
                concept = Concept.objects.get(id=concept_id)
            except Concept.DoesNotExist:
                return Response(
                    {'error': 'Game not found.'},
                    status=status.HTTP_404_NOT_FOUND,
                )

            # Reject PP_ stubs
            if concept.concept_id.startswith('PP_'):
                return Response(
                    {'error': 'This game is not eligible.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            # Must have PS4/PS5
            has_modern = Game.objects.filter(concept=concept).filter(
                Q(title_platform__contains='PS4') | Q(title_platform__contains='PS5')
            ).exists()
            if not has_modern:
                return Response(
                    {'error': 'This game needs at least one PS4 or PS5 version.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            # Not already platted or >50% progress
            excluded_ids = get_genre_excluded_concept_ids(profile)
            if concept.id in excluded_ids:
                return Response(
                    {'error': "This game is excluded. You've already platted it or have over 50% progress."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            # Not already in a genre slot
            if challenge.genre_slots.filter(concept=concept).exists():
                return Response(
                    {'error': 'This game is already assigned to a genre slot.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            # Not already in bonus
            if challenge.bonus_slots.filter(concept=concept).exists():
                return Response(
                    {'error': 'This game is already in your bonus list.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            # Create bonus slot
            bonus_slot = GenreBonusSlot.objects.create(
                challenge=challenge,
                concept=concept,
            )

            recalculate_challenge_counts(challenge)
            challenge.save(update_fields=[
                'filled_count', 'completed_count', 'subgenre_count',
                'bonus_count', 'updated_at',
            ])

            response_data = _serialize_bonus_slot(bonus_slot)
            response_data.update(_build_genre_response_extras(challenge))
            return Response(response_data, status=status.HTTP_201_CREATED)

        except Exception:
            logger.exception("Genre bonus add error")
            return Response(
                {'error': 'Internal error.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class GenreBonusClearAPIView(APIView):
    """Remove a bonus game from a genre challenge."""
    authentication_classes = [SessionAuthentication, TokenAuthentication]
    permission_classes = [IsAuthenticated]

    @method_decorator(ratelimit(key='user', rate='60/m', method='DELETE', block=True))
    def delete(self, request, challenge_id, bonus_slot_id):
        try:
            profile, error = _get_profile_or_error(request)
            if error:
                return error

            challenge, error = _get_owned_challenge(challenge_id, profile)
            if error:
                return error

            try:
                slot = challenge.bonus_slots.get(id=bonus_slot_id)
            except GenreBonusSlot.DoesNotExist:
                return Response(
                    {'error': 'Bonus slot not found.'},
                    status=status.HTTP_404_NOT_FOUND,
                )

            slot.delete()

            recalculate_challenge_counts(challenge)
            challenge.save(update_fields=[
                'filled_count', 'completed_count', 'subgenre_count',
                'bonus_count', 'updated_at',
            ])

            response_data = _build_genre_response_extras(challenge)
            return Response(response_data)

        except Exception:
            logger.exception("Genre bonus clear error")
            return Response(
                {'error': 'Internal error.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class GenreMoveAPIView(APIView):
    """Move a game between genre slots and bonus slots."""
    authentication_classes = [SessionAuthentication, TokenAuthentication]
    permission_classes = [IsAuthenticated]

    @method_decorator(ratelimit(key='user', rate='60/m', method='POST', block=True))
    def post(self, request, challenge_id):
        try:
            profile, error = _get_profile_or_error(request)
            if error:
                return error

            challenge, error = _get_owned_challenge(challenge_id, profile)
            if error:
                return error

            source_type = (request.data.get('source_type') or '').strip()
            source_id = request.data.get('source_id', '')
            dest_type = (request.data.get('dest_type') or '').strip()
            dest_id = request.data.get('dest_id', '')

            if source_type not in ('genre', 'bonus') or dest_type not in ('genre', 'bonus'):
                return Response(
                    {'error': 'Invalid source_type or dest_type.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            # Can't move to same place
            if source_type == dest_type and str(source_id) == str(dest_id):
                return Response(
                    {'error': 'Source and destination are the same.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            # --- Resolve source concept ---
            concept = None
            if source_type == 'genre':
                source_genre = str(source_id).upper()
                if source_genre not in GENRE_CHALLENGE_GENRES:
                    return Response(
                        {'error': 'Invalid source genre.'},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                try:
                    source_slot = challenge.genre_slots.select_related('concept').get(genre=source_genre)
                except GenreChallengeSlot.DoesNotExist:
                    return Response({'error': 'Source slot not found.'}, status=status.HTTP_404_NOT_FOUND)
                if source_slot.is_completed:
                    return Response({'error': 'Cannot move a completed slot.'}, status=status.HTTP_400_BAD_REQUEST)
                if not source_slot.concept:
                    return Response({'error': 'Source slot is empty.'}, status=status.HTTP_400_BAD_REQUEST)
                concept = source_slot.concept
            else:
                bonus_id = safe_int(source_id, None)
                if not bonus_id:
                    return Response({'error': 'Invalid bonus slot ID.'}, status=status.HTTP_400_BAD_REQUEST)
                try:
                    source_bonus = challenge.bonus_slots.select_related('concept').get(id=bonus_id)
                except GenreBonusSlot.DoesNotExist:
                    return Response({'error': 'Source bonus slot not found.'}, status=status.HTTP_404_NOT_FOUND)
                if not source_bonus.concept:
                    return Response({'error': 'Source bonus slot is empty.'}, status=status.HTTP_400_BAD_REQUEST)
                concept = source_bonus.concept

            # --- Execute move ---
            if dest_type == 'bonus':
                # Move to bonus: no genre restriction
                if source_type == 'genre':
                    was_cover = (source_slot.genre == challenge.cover_genre)
                    source_slot.concept = None
                    source_slot.assigned_at = None
                    source_slot.save(update_fields=['concept', 'assigned_at'])
                    GenreBonusSlot.objects.create(challenge=challenge, concept=concept)
                    if was_cover:
                        auto_set_cover_genre(challenge)
                else:
                    return Response(
                        {'error': 'Game is already in bonus.'},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
            else:
                # Move to genre slot
                dest_genre = str(dest_id).upper()
                if dest_genre not in GENRE_CHALLENGE_GENRES:
                    return Response({'error': 'Invalid destination genre.'}, status=status.HTTP_400_BAD_REQUEST)

                try:
                    dest_slot = challenge.genre_slots.get(genre=dest_genre)
                except GenreChallengeSlot.DoesNotExist:
                    return Response({'error': 'Destination slot not found.'}, status=status.HTTP_404_NOT_FOUND)

                if dest_slot.is_completed:
                    return Response({'error': 'Destination slot is completed.'}, status=status.HTTP_400_BAD_REQUEST)
                if dest_slot.concept_id:
                    return Response({'error': 'Destination slot already has a game. Clear it first.'}, status=status.HTTP_400_BAD_REQUEST)

                # Validate concept has the destination genre
                concept_genres = set(concept.genres or [])
                mapped_genres = set()
                for g in concept_genres:
                    mapped = GENRE_MERGE_MAP.get(g, g)
                    if mapped in set(GENRE_CHALLENGE_GENRES):
                        mapped_genres.add(mapped)

                if dest_genre not in mapped_genres:
                    return Response(
                        {'error': f'This game is not tagged as {GENRE_DISPLAY_NAMES.get(dest_genre, dest_genre)}.'},
                        status=status.HTTP_400_BAD_REQUEST,
                    )

                # Clear source
                if source_type == 'genre':
                    was_cover = (source_slot.genre == challenge.cover_genre)
                    source_slot.concept = None
                    source_slot.assigned_at = None
                    source_slot.save(update_fields=['concept', 'assigned_at'])
                    if was_cover:
                        auto_set_cover_genre(challenge)
                else:
                    source_bonus.delete()

                # Assign to destination
                dest_slot.concept = concept
                dest_slot.assigned_at = timezone.now()
                dest_slot.save(update_fields=['concept', 'assigned_at'])

                # Auto-set cover if needed
                if not challenge.cover_genre:
                    auto_set_cover_genre(challenge)

            recalculate_challenge_counts(challenge)
            challenge.save(update_fields=[
                'filled_count', 'completed_count', 'subgenre_count',
                'bonus_count', 'updated_at',
            ])

            response_data = _build_genre_response_extras(challenge)

            # Include updated slot data so JS can refresh the cards
            slots_data = {}
            for slot in challenge.genre_slots.select_related('concept').all():
                slots_data[slot.genre] = _serialize_slot(slot)
            response_data['slots'] = slots_data
            response_data['bonus_slots'] = [
                _serialize_bonus_slot(bs)
                for bs in challenge.bonus_slots.select_related('concept').all()
            ]

            return Response(response_data)

        except Exception:
            logger.exception("Genre move error")
            return Response(
                {'error': 'Internal error.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class GenreSwapTargetsAPIView(APIView):
    """Get valid move targets for a concept in a genre challenge."""
    authentication_classes = [SessionAuthentication, TokenAuthentication]
    permission_classes = [IsAuthenticated]

    @method_decorator(ratelimit(key='user', rate='120/m', method='GET', block=True))
    def get(self, request, challenge_id):
        try:
            profile, error = _get_profile_or_error(request)
            if error:
                return error

            challenge, error = _get_owned_challenge(challenge_id, profile)
            if error:
                return error

            source_type = (request.query_params.get('source_type') or '').strip()
            source_id = request.query_params.get('source_id', '')

            if source_type == 'genre':
                source_genre = str(source_id).upper()
                try:
                    slot = challenge.genre_slots.select_related('concept').get(genre=source_genre)
                except GenreChallengeSlot.DoesNotExist:
                    return Response({'error': 'Slot not found.'}, status=status.HTTP_404_NOT_FOUND)
                if not slot.concept:
                    return Response({'error': 'Slot is empty.'}, status=status.HTTP_400_BAD_REQUEST)
                concept = slot.concept
                current_source = source_genre
            elif source_type == 'bonus':
                bonus_id = safe_int(source_id, None)
                if not bonus_id:
                    return Response({'error': 'Invalid bonus slot ID.'}, status=status.HTTP_400_BAD_REQUEST)
                try:
                    bonus_slot = challenge.bonus_slots.select_related('concept').get(id=bonus_id)
                except GenreBonusSlot.DoesNotExist:
                    return Response({'error': 'Bonus slot not found.'}, status=status.HTTP_404_NOT_FOUND)
                if not bonus_slot.concept:
                    return Response({'error': 'Bonus slot is empty.'}, status=status.HTTP_400_BAD_REQUEST)
                concept = bonus_slot.concept
                current_source = 'BONUS'
            else:
                return Response({'error': 'Invalid source_type.'}, status=status.HTTP_400_BAD_REQUEST)

            targets = get_genre_swap_targets(concept, challenge, current_source)
            return Response({'targets': targets})

        except Exception:
            logger.exception("Genre swap targets error")
            return Response(
                {'error': 'Internal error.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
