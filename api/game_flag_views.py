import logging

from django.utils.decorators import method_decorator
from django_ratelimit.decorators import ratelimit
from rest_framework import status
from rest_framework.authentication import SessionAuthentication, TokenAuthentication
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from trophies.models import Game
from trophies.services.game_flag_service import GameFlagService, GameFlagSubmissionError
from trophies.services.game_grouping_service import version_peer_qs

logger = logging.getLogger(__name__)

#: ONE rate-limit bucket shared by both flag endpoints.
#:
#: django_ratelimit derives the bucket from the decorated function when `group` is omitted, so
#: `GameFlagView.post` and `GameFlagVersionsView.post` were separate budgets -- a reporter got 5
#: single-game writes AND 5 bulk writes per minute, and each bulk write carries up to
#: MAX_BULK_VERSIONS rows. That is 10 accepted submissions where the design budgeted 5, and up to
#: 125 GameFlag rows a minute into the moderation queue from one account. Naming the group makes
#: "one deliberate act, one unit of budget" true across BOTH shapes rather than within each.
FLAG_RATELIMIT_GROUP = 'game-flag'


class GameFlagView(APIView):
    """Submit a community flag for a game."""
    authentication_classes = [SessionAuthentication, TokenAuthentication]
    permission_classes = [IsAuthenticated]

    @method_decorator(ratelimit(group=FLAG_RATELIMIT_GROUP, key='user', rate='5/m', method='POST', block=True))
    def post(self, request, game_id):
        """
        POST /api/v1/games/<game_id>/flag/
        Body: { "flag_type": str, "details": str (optional) }
        """
        try:
            profile = getattr(request.user, 'profile', None)
            if not profile:
                return Response(
                    {'error': 'Profile not found.'},
                    status=status.HTTP_400_BAD_REQUEST
                )
            # The SHARED gate, not an inline `is_linked`. This endpoint had its own copy, which is
            # why it was the one UGC write a restriction would not have covered -- and why its
            # refusal message could never mention flagging.
            from trophies.services.comment_service import CommentService

            can_flag, refusal = CommentService.can_interact(profile, action='flag a game')
            if not can_flag:
                return Response({'error': refusal}, status=status.HTTP_403_FORBIDDEN)

            try:
                game = Game.objects.get(pk=game_id)
            except Game.DoesNotExist:
                return Response({'error': 'Game not found.'}, status=status.HTTP_404_NOT_FOUND)

            flag_type = request.data.get('flag_type')
            if not flag_type:
                return Response(
                    {'error': 'flag_type is required.'},
                    status=status.HTTP_400_BAD_REQUEST
                )

            details = request.data.get('details', '')

            flag, error = GameFlagService.submit_flag(game, profile, flag_type, details)
            if error:
                return Response({'error': error}, status=status.HTTP_400_BAD_REQUEST)

            return Response({'success': True, 'message': 'Flag submitted successfully. Thank you for helping improve our data!'})

        except Exception as e:
            logger.exception('Game flag submission error: %s', e)
            return Response(
                {'error': 'An unexpected error occurred.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class GameFlagVersionsView(APIView):
    """Submit ONE flag against several versions of the same game, in one request.

    The Game page is concept-level: it wraps every trophy list that resolves to one IGDB id, and a
    data problem ("delisted", "trophies unobtainable") is usually true of several of them at once.
    Filing those one at a time from the client is not an option -- the per-user limit is 5/min, so a
    six-list concept would be REFUSED PART WAY THROUGH, having already filed some, with nothing in
    the UI able to say which. (The refusal is a 403, not a 429: `Ratelimited` subclasses
    `PermissionDenied`, which DRF renders as 403 with a `detail` key -- not the `error` key this
    module's own responses use, which is why game-flag.js reads both.)

    So the fan-out is server-side and the rate limit stays on the REQUEST, sharing ONE bucket with
    the single-game endpoint (`FLAG_RATELIMIT_GROUP`): one deliberate act by a person, one unit of
    budget. `GameFlagService.MAX_BULK_VERSIONS` is what stops that from meaning "unlimited rows per
    unit".

    POST /api/v1/games/<anchor_game_id>/flag/versions/
    Body: { "game_ids": [int, ...], "flag_type": str, "details": str (optional) }
    """
    authentication_classes = [SessionAuthentication, TokenAuthentication]
    permission_classes = [IsAuthenticated]

    @method_decorator(ratelimit(group=FLAG_RATELIMIT_GROUP, key='user', rate='5/m', method='POST', block=True))
    def post(self, request, game_id):
        try:
            profile = getattr(request.user, 'profile', None)
            if not profile:
                return Response({'error': 'Profile not found.'}, status=status.HTTP_400_BAD_REQUEST)

            from trophies.services.comment_service import CommentService

            can_flag, refusal = CommentService.can_interact(profile, action='flag a game')
            if not can_flag:
                return Response({'error': refusal}, status=status.HTTP_403_FORBIDDEN)

            # Every cheap validation FIRST, so a junk payload never costs a query. flag_type is
            # validated for membership here and again in the service -- the service is the one that
            # matters (it is the single writer), this one just stops the peer query running for a
            # request already known to be refused.
            flag_type = request.data.get('flag_type')
            if not flag_type:
                return Response({'error': 'flag_type is required.'}, status=status.HTTP_400_BAD_REQUEST)
            if flag_type not in GameFlagService.VALID_FLAG_TYPES:
                return Response({'error': 'Invalid flag type.'}, status=status.HTTP_400_BAD_REQUEST)

            raw_ids = request.data.get('game_ids') or []
            if not isinstance(raw_ids, (list, tuple)):
                return Response({'error': 'game_ids must be a list.'},
                                status=status.HTTP_400_BAD_REQUEST)
            # REJECT, don't coerce. `int()` would turn 3.9 into 3 and `true` into 1, so a client bug
            # would file against a game nobody named. bool is excluded explicitly because it is an
            # int subclass in Python. Note `Infinity`/`NaN` never reach here -- DRF's JSONParser is
            # strict by default and 400s them at parse time.
            if any(not isinstance(i, int) or isinstance(i, bool) for i in raw_ids):
                return Response({'error': 'game_ids must be a list of integers.'},
                                status=status.HTTP_400_BAD_REQUEST)
            wanted = set(raw_ids)
            if not wanted:
                return Response({'error': GameFlagService.ERR_NO_VERSIONS},
                                status=status.HTTP_400_BAD_REQUEST)
            # Cap BEFORE the peer query, so an oversized payload is refused without doing the work.
            # The service caps again on the FILTERED set; this one caps what was submitted.
            if len(wanted) > GameFlagService.MAX_BULK_VERSIONS:
                return Response({'error': GameFlagService.ERR_TOO_MANY},
                                status=status.HTTP_400_BAD_REQUEST)

            try:
                # `.defer(raw_response)` is the house rule for every queryset joining igdb_match:
                # it is a ~30 KB blob and this view reads exactly one field off the match (igdb_id,
                # via version_peer_qs).
                anchor = (Game.objects.select_related('concept__igdb_match')
                          .defer('concept__igdb_match__raw_response').get(pk=game_id))
            except Game.DoesNotExist:
                return Response({'error': 'Game not found.'}, status=status.HTTP_404_NOT_FOUND)

            # THE security boundary: a submitted id must be a VERSION OF THE SAME WORK as the
            # anchor. Without this the endpoint takes arbitrary game ids, and 5 requests a minute
            # times MAX_BULK_VERSIONS becomes a mass-flagging tool aimed anywhere in the catalog.
            # Intersected server-side, never trusted from the client, because the client's list is
            # exactly what an attacker controls.
            #
            # The same-work relation, NOT "whatever the switcher rendered". Two known gaps, both
            # deliberate, listed so the next reader does not assume this set equals the page's:
            #   1. On the /games/c/<id>/ route the page's list set is one concept's lists while this
            #      admits every list sharing the igdb id.
            #   2. GamePageView._resolve floors out lists with a null/blank np_communication_id;
            #      this does not, so such a list is flaggable though no page ever rendered it.
            # Both admit only genuine versions of the same work, which is what a flag is about, and
            # the About tab's versions card already surfaces the first group to the same reader.
            # Narrowing to the switcher would need the endpoint to know which route rendered the
            # page -- a worse thing to take from the client than the ids themselves.
            games = list(version_peer_qs(anchor).filter(pk__in=wanted))
            if not games:
                return Response({'error': 'No valid versions selected.'},
                                status=status.HTTP_400_BAD_REQUEST)

            details = request.data.get('details', '')
            try:
                created, duplicates, error = GameFlagService.submit_flags(
                    games, profile, flag_type, details)
            except GameFlagSubmissionError as exc:
                return Response({'error': str(exc)}, status=status.HTTP_403_FORBIDDEN)
            if error:
                return Response({'error': error}, status=status.HTTP_400_BAD_REQUEST)

            return Response({
                'success': True,
                'created': created,
                'duplicates': duplicates,
                'message': _bulk_message(created, duplicates),
            })

        except Exception as e:
            logger.exception('Bulk game flag submission error: %s', e)
            return Response({'error': 'An unexpected error occurred.'},
                            status=status.HTTP_500_INTERNAL_SERVER_ERROR)


def _bulk_message(created, duplicates):
    """The toast. Says what actually happened, including the all-duplicates case -- a plain success
    line there would tell a reporter their submission filed rows when it filed none."""
    if created and duplicates:
        return (f'Reported {created} version{"s" if created != 1 else ""}. '
                f'{duplicates} {"were" if duplicates != 1 else "was"} already reported.')
    if created:
        return (f'Reported {created} version{"s" if created != 1 else ""}. '
                f'Thanks for helping keep our data accurate!')
    return (f'You had already reported {duplicates} '
            f'version{"s" if duplicates != 1 else ""} for this issue.')
