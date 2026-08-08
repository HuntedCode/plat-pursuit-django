"""Game-background picker API (profile banner + share-card image selection).

The two-step image picker: search a hunter's platted/100% library for a game, then pick one of that game's
landscape images. Premium-only. Used by the profile banner picker and share-card backdrops. (Extracted from
the calendar-challenge share views during the Lane 2 challenge teardown; this infra is not challenge-specific.)
"""
from django.db.models import Q
from django.db.models.functions import Lower
from django.utils.decorators import method_decorator
from django_ratelimit.decorators import ratelimit
from rest_framework import status as http_status
from rest_framework.authentication import SessionAuthentication, TokenAuthentication
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from trophies.models import Concept, ProfileGame


def _concept_landscape_images(concept):
    """Ordered, de-duplicated landscape image URLs for a concept's picker.

    PSN GAMEHUB art first (real key art when present), then IGDB artworks,
    then IGDB screenshots, with the portrait cover as a last resort so every
    game offers at least one option even when no landscape art exists.
    """
    urls = []
    seen = set()

    def _add(url):
        if url and url not in seen:
            seen.add(url)
            urls.append(url)

    _add(concept.bg_url)

    # Request IGDB's largest size (t_1080p, 1920x1080) for both artworks and
    # screenshots so banners/share cards aren't pixelated. screenshot_big
    # (889x500) was the source of the blur when stretched across a wide banner.
    match = getattr(concept, 'igdb_match', None)
    if match and match.is_trusted:
        for url in match.artwork_urls('1080p'):
            _add(url)
        for url in match.screenshot_urls('1080p'):
            _add(url)

    # Cover is a last resort (portrait, crops oddly); request a larger render.
    _add(concept.get_cover_url('1080p'))

    return urls


class GameBackgroundSearchView(APIView):
    """
    GET /api/v1/game-backgrounds/?q=<search_term>&require_bg=<0|1>

    Search the current user's platted/completed games. Used by the game
    picker widget shared by the share card and the profile banner picker.

    `require_bg` (default true) keeps only games that already have a PSN
    landscape image (`concept.bg_url`). Callers using the two-step image
    picker pass `require_bg=0` to surface every platted/100% game, since
    images are then sourced per-concept from IGDB as well.
    """
    permission_classes = [IsAuthenticated]
    authentication_classes = [SessionAuthentication, TokenAuthentication]

    @method_decorator(ratelimit(key='user', rate='60/m', method='GET', block=True))
    def get(self, request):
        if not hasattr(request.user, 'profile'):
            return Response(
                {'error': 'No linked PSN profile'},
                status=http_status.HTTP_400_BAD_REQUEST,
            )
        profile = request.user.profile
        if not profile.user_is_premium:
            return Response(
                {'error': 'Premium required'},
                status=http_status.HTTP_403_FORBIDDEN,
            )

        query = request.query_params.get('q', '').strip()
        require_bg = request.query_params.get('require_bg', '1').lower() not in ('0', 'false', 'no')

        qs = ProfileGame.objects.filter(
            profile=profile,
        ).filter(
            Q(has_plat=True) | Q(progress=100)
        ).select_related('game__concept')

        if require_bg:
            qs = qs.filter(
                game__concept__bg_url__isnull=False,
            ).exclude(
                game__concept__bg_url=''
            )

        if query:
            qs = qs.filter(
                game__concept__unified_title__icontains=query
            ).order_by(Lower('game__concept__unified_title'))
            limit = 20
        else:
            # No query: show most recently played games first for browsing
            qs = qs.order_by('-last_played_date_time')
            limit = 24

        # Deduplicate by concept at the DB level
        concept_ids = list(
            qs.values_list('game__concept_id', flat=True)
            .distinct()[:limit]
        )

        # select_related igdb_match (deferring the heavy raw_response blob) so
        # the IGDB-first c.cover_url below doesn't N+1; anchored concepts have
        # an empty concept_icon_url and rely on the trusted IGDB cover.
        base = Concept.objects.select_related('igdb_match').defer('igdb_match__raw_response')
        if query:
            concepts = base.filter(id__in=concept_ids).order_by(Lower('unified_title'))
        else:
            # Preserve the recency order from ProfileGame
            concepts_map = {c.id: c for c in base.filter(id__in=concept_ids)}
            concepts = [concepts_map[cid] for cid in concept_ids if cid in concepts_map]

        results = [{
            'concept_id': c.id,
            'title_name': c.unified_title,
            'bg_url': c.bg_url,
            'icon_url': c.cover_url or '',
        } for c in concepts]

        return Response({'results': results})


class ConceptBannerImagesView(APIView):
    """
    GET /api/v1/game-backgrounds/<concept_id>/images/

    Return the landscape image options for a single concept the user has
    platted/100% completed. Powers the second step of the image picker
    (game -> pick exact image) for both the profile banner and share cards.
    """
    permission_classes = [IsAuthenticated]
    authentication_classes = [SessionAuthentication, TokenAuthentication]

    @method_decorator(ratelimit(key='user', rate='60/m', method='GET', block=True))
    def get(self, request, concept_id):
        if not hasattr(request.user, 'profile'):
            return Response(
                {'error': 'No linked PSN profile'},
                status=http_status.HTTP_400_BAD_REQUEST,
            )
        profile = request.user.profile
        if not profile.user_is_premium:
            return Response(
                {'error': 'Premium required'},
                status=http_status.HTTP_403_FORBIDDEN,
            )

        owns_concept = ProfileGame.objects.filter(
            profile=profile,
            game__concept_id=concept_id,
        ).filter(
            Q(has_plat=True) | Q(progress=100)
        ).exists()
        if not owns_concept:
            return Response(
                {'error': 'Game not found in your platinum/completed library'},
                status=http_status.HTTP_404_NOT_FOUND,
            )

        concept = (
            Concept.objects.select_related('igdb_match')
            .filter(id=concept_id)
            .first()
        )
        if concept is None:
            return Response(
                {'error': 'Concept not found'},
                status=http_status.HTTP_404_NOT_FOUND,
            )

        images = _concept_landscape_images(concept)
        return Response({
            'concept_id': concept.id,
            'title_name': concept.unified_title,
            'images': images,
        })
