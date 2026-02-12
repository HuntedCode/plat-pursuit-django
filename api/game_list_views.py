"""
Game List API views.

Handles all REST endpoints for game lists: CRUD, items, reordering, likes, and game search.
"""
import logging

from django.db import IntegrityError
from django.db.models import F, Q, Exists, OuterRef
from django.db.models.functions import Lower
from django.utils.decorators import method_decorator
from django_ratelimit.decorators import ratelimit
from rest_framework import status
from rest_framework.authentication import SessionAuthentication, TokenAuthentication
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from trophies.models import (
    Game, GameList, GameListItem, GameListLike,
    GAME_LIST_FREE_MAX_LISTS, GAME_LIST_FREE_MAX_ITEMS,
)
from trophies.util_modules.constants import ALL_PLATFORMS, REGIONS

logger = logging.getLogger('psn_api')


def safe_int(value, default=0):
    """Safely convert a query parameter to int, returning default on failure."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _get_profile_or_error(request):
    """Return (profile, None) or (None, Response) for error."""
    profile = getattr(request.user, 'profile', None)
    if not profile:
        return None, Response({'error': 'Linked profile required.'}, status=status.HTTP_403_FORBIDDEN)
    return profile, None


def _get_owned_list(list_id, profile):
    """Return (game_list, None) or (None, Response) for error."""
    try:
        game_list = GameList.objects.get(id=list_id, is_deleted=False)
    except GameList.DoesNotExist:
        return None, Response({'error': 'List not found.'}, status=status.HTTP_404_NOT_FOUND)
    if game_list.profile_id != profile.id:
        return None, Response({'error': 'Not your list.'}, status=status.HTTP_403_FORBIDDEN)
    return game_list, None


def _is_premium(user):
    """Check if user has an active premium subscription."""
    return getattr(getattr(user, 'profile', None), 'user_is_premium', False)


def _check_list_limit(profile, user):
    """Return error Response if free user is at list limit, else None."""
    if _is_premium(user):
        return None
    count = GameList.objects.filter(profile=profile, is_deleted=False).count()
    if count >= GAME_LIST_FREE_MAX_LISTS:
        return Response({
            'error': f'Free accounts are limited to {GAME_LIST_FREE_MAX_LISTS} lists. Upgrade to Premium for unlimited lists!',
            'limit_reached': True,
        }, status=status.HTTP_403_FORBIDDEN)
    return None


def _check_item_limit(game_list, user):
    """Return error Response if free user is at item limit, else None."""
    if _is_premium(user):
        return None
    if game_list.game_count >= GAME_LIST_FREE_MAX_ITEMS:
        return Response({
            'error': f'Free accounts are limited to {GAME_LIST_FREE_MAX_ITEMS} games per list. Upgrade to Premium for unlimited games!',
            'limit_reached': True,
        }, status=status.HTTP_403_FORBIDDEN)
    return None


def _serialize_game_list(game_list, profile=None):
    """Serialize a GameList to dict."""
    data = {
        'id': game_list.id,
        'name': game_list.name,
        'description': game_list.description,
        'is_public': game_list.is_public,
        'game_count': game_list.game_count,
        'like_count': game_list.like_count,
        'view_count': game_list.view_count,
        'created_at': game_list.created_at.isoformat(),
        'updated_at': game_list.updated_at.isoformat(),
        'author': {
            'psn_username': game_list.profile.psn_username,
            'display_psn_username': game_list.profile.display_psn_username,
            'avatar_url': game_list.profile.avatar_url or '',
            'user_is_premium': game_list.profile.user_is_premium,
        },
        'first_game_image': game_list.first_game_image or '',
        'selected_theme': game_list.selected_theme or '',
        # Effective state reflects whether premium features are currently active
        'effective_is_public': game_list.is_public and game_list.profile.user_is_premium,
        'effective_theme': game_list.selected_theme if game_list.profile.user_is_premium else '',
    }
    if profile:
        data['user_has_liked'] = GameListLike.objects.filter(
            game_list=game_list, profile=profile
        ).exists()
    return data


def _serialize_game_list_item(item):
    """Serialize a GameListItem to dict."""
    game = item.game
    return {
        'id': item.id,
        'game_id': game.id,
        'np_communication_id': game.np_communication_id,
        'title_name': game.title_name,
        'title_image': game.title_image or '',
        'title_icon_url': game.title_icon_url or '',
        'display_image': game.get_icon_url() or '',
        'title_platform': game.title_platform or [],
        'is_regional': game.is_regional,
        'region': game.region or [],
        'defined_trophies': game.defined_trophies or {},
        'played_count': game.played_count,
        'note': item.note,
        'position': item.position,
        'added_at': item.added_at.isoformat(),
    }


# --- List CRUD ---

class GameListCreateView(APIView):
    """Create a new game list."""
    authentication_classes = [SessionAuthentication, TokenAuthentication]
    permission_classes = [IsAuthenticated]

    @method_decorator(ratelimit(key='user', rate='30/h', method='POST', block=True))
    def post(self, request):
        """POST /api/v1/lists/"""
        try:
            profile, err = _get_profile_or_error(request)
            if err:
                return err

            limit_err = _check_list_limit(profile, request.user)
            if limit_err:
                return limit_err

            name = (request.data.get('name') or '').strip()
            if not name:
                return Response({'error': 'Name is required.'}, status=status.HTTP_400_BAD_REQUEST)
            if len(name) > 200:
                return Response({'error': 'Name must be 200 characters or less.'}, status=status.HTTP_400_BAD_REQUEST)

            description = (request.data.get('description') or '').strip()
            if len(description) > 1000:
                return Response({'error': 'Description must be 1000 characters or less.'}, status=status.HTTP_400_BAD_REQUEST)

            game_list = GameList.objects.create(
                profile=profile,
                name=name,
                description=description,
            )

            from core.services.tracking import track_site_event
            track_site_event('game_list_create', game_list.id, request)

            return Response({
                'success': True,
                'list': _serialize_game_list(game_list),
            }, status=status.HTTP_201_CREATED)

        except Exception as e:
            logger.exception(f"GameList create error: {e}")
            return Response({'error': 'Internal error.'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class GameListDetailView(APIView):
    """Get a game list with its items."""
    authentication_classes = [SessionAuthentication, TokenAuthentication]
    permission_classes = []

    def get(self, request, list_id):
        """GET /api/v1/lists/<list_id>/"""
        try:
            try:
                game_list = GameList.objects.select_related('profile').get(id=list_id, is_deleted=False)
            except GameList.DoesNotExist:
                return Response({'error': 'List not found.'}, status=status.HTTP_404_NOT_FOUND)

            profile = getattr(request.user, 'profile', None) if request.user.is_authenticated else None

            # Private lists are only visible to owner
            if not game_list.is_public and (not profile or game_list.profile_id != profile.id):
                return Response({'error': 'List not found.'}, status=status.HTTP_404_NOT_FOUND)

            items = game_list.items.select_related('game').order_by('position')
            items_data = [_serialize_game_list_item(item) for item in items]

            data = _serialize_game_list(game_list, profile)
            data['items'] = items_data
            data['is_owner'] = profile and game_list.profile_id == profile.id

            return Response(data)

        except Exception as e:
            logger.exception(f"GameList detail error: {e}")
            return Response({'error': 'Internal error.'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class GameListUpdateView(APIView):
    """Update a game list (name, description, visibility)."""
    authentication_classes = [SessionAuthentication, TokenAuthentication]
    permission_classes = [IsAuthenticated]

    @method_decorator(ratelimit(key='user', rate='60/m', method='PATCH', block=True))
    def patch(self, request, list_id):
        """PATCH /api/v1/lists/<list_id>/"""
        try:
            profile, err = _get_profile_or_error(request)
            if err:
                return err

            game_list, err = _get_owned_list(list_id, profile)
            if err:
                return err

            update_fields = []

            if 'name' in request.data:
                name = (request.data['name'] or '').strip()
                if not name:
                    return Response({'error': 'Name cannot be empty.'}, status=status.HTTP_400_BAD_REQUEST)
                if len(name) > 200:
                    return Response({'error': 'Name must be 200 characters or less.'}, status=status.HTTP_400_BAD_REQUEST)
                game_list.name = name
                update_fields.append('name')

            if 'description' in request.data:
                description = (request.data['description'] or '').strip()
                if len(description) > 1000:
                    return Response({'error': 'Description must be 1000 characters or less.'}, status=status.HTTP_400_BAD_REQUEST)
                game_list.description = description
                update_fields.append('description')

            if 'is_public' in request.data:
                if not _is_premium(request.user):
                    return Response({
                        'error': 'Public lists are a Premium feature. Upgrade to make your lists public!',
                        'premium_required': True,
                    }, status=status.HTTP_403_FORBIDDEN)
                game_list.is_public = bool(request.data['is_public'])
                update_fields.append('is_public')

            if 'selected_theme' in request.data:
                if not _is_premium(request.user):
                    return Response({
                        'error': 'List themes are a Premium feature.',
                        'premium_required': True,
                    }, status=status.HTTP_403_FORBIDDEN)
                theme_key = (request.data['selected_theme'] or '').strip()
                if theme_key:
                    from trophies.themes import GRADIENT_THEMES
                    if theme_key not in GRADIENT_THEMES or GRADIENT_THEMES[theme_key].get('requires_game_image'):
                        return Response({'error': 'Invalid theme.'}, status=status.HTTP_400_BAD_REQUEST)
                game_list.selected_theme = theme_key
                update_fields.append('selected_theme')

            if update_fields:
                game_list.save(update_fields=update_fields + ['updated_at'])

            return Response({
                'success': True,
                'list': _serialize_game_list(game_list),
            })

        except Exception as e:
            logger.exception(f"GameList update error: {e}")
            return Response({'error': 'Internal error.'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class GameListDeleteView(APIView):
    """Soft delete a game list."""
    authentication_classes = [SessionAuthentication, TokenAuthentication]
    permission_classes = [IsAuthenticated]

    @method_decorator(ratelimit(key='user', rate='30/h', method='DELETE', block=True))
    def delete(self, request, list_id):
        """DELETE /api/v1/lists/<list_id>/"""
        try:
            profile, err = _get_profile_or_error(request)
            if err:
                return err

            game_list, err = _get_owned_list(list_id, profile)
            if err:
                return err

            game_list.soft_delete()
            return Response({'success': True})

        except Exception as e:
            logger.exception(f"GameList delete error: {e}")
            return Response({'error': 'Internal error.'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


# --- List Items ---

class GameListAddItemView(APIView):
    """Add a game to a list."""
    authentication_classes = [SessionAuthentication, TokenAuthentication]
    permission_classes = [IsAuthenticated]

    @method_decorator(ratelimit(key='user', rate='120/m', method='POST', block=True))
    def post(self, request, list_id):
        """POST /api/v1/lists/<list_id>/items/"""
        try:
            profile, err = _get_profile_or_error(request)
            if err:
                return err

            game_list, err = _get_owned_list(list_id, profile)
            if err:
                return err

            limit_err = _check_item_limit(game_list, request.user)
            if limit_err:
                return limit_err

            game_id = request.data.get('game_id')
            if not game_id:
                return Response({'error': 'game_id is required.'}, status=status.HTTP_400_BAD_REQUEST)

            try:
                game = Game.objects.get(id=game_id)
            except Game.DoesNotExist:
                return Response({'error': 'Game not found.'}, status=status.HTTP_404_NOT_FOUND)

            # Place at end
            max_position = game_list.items.count()

            try:
                item = GameListItem.objects.create(
                    game_list=game_list,
                    game=game,
                    position=max_position,
                )
            except IntegrityError:
                return Response({'error': 'Game is already in this list.'}, status=status.HTTP_409_CONFLICT)

            # Update denormalized count
            GameList.objects.filter(id=game_list.id).update(
                game_count=F('game_count') + 1,
                updated_at=game_list.updated_at,  # auto_now will handle
            )
            game_list.refresh_from_db(fields=['game_count'])

            return Response({
                'success': True,
                'item': _serialize_game_list_item(item),
                'game_count': game_list.game_count,
            }, status=status.HTTP_201_CREATED)

        except Exception as e:
            logger.exception(f"GameList add item error: {e}")
            return Response({'error': 'Internal error.'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class GameListRemoveItemView(APIView):
    """Remove a game from a list."""
    authentication_classes = [SessionAuthentication, TokenAuthentication]
    permission_classes = [IsAuthenticated]

    @method_decorator(ratelimit(key='user', rate='120/m', method='DELETE', block=True))
    def delete(self, request, list_id, item_id):
        """DELETE /api/v1/lists/<list_id>/items/<item_id>/"""
        try:
            profile, err = _get_profile_or_error(request)
            if err:
                return err

            game_list, err = _get_owned_list(list_id, profile)
            if err:
                return err

            try:
                item = GameListItem.objects.get(id=item_id, game_list=game_list)
            except GameListItem.DoesNotExist:
                return Response({'error': 'Item not found.'}, status=status.HTTP_404_NOT_FOUND)

            removed_position = item.position
            item.delete()

            # Re-compact positions for items after the removed one
            GameListItem.objects.filter(
                game_list=game_list, position__gt=removed_position
            ).update(position=F('position') - 1)

            # Update denormalized count
            GameList.objects.filter(id=game_list.id).update(game_count=F('game_count') - 1)
            game_list.refresh_from_db(fields=['game_count'])

            return Response({
                'success': True,
                'game_count': game_list.game_count,
            })

        except Exception as e:
            logger.exception(f"GameList remove item error: {e}")
            return Response({'error': 'Internal error.'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class GameListUpdateItemView(APIView):
    """Update a game list item (note)."""
    authentication_classes = [SessionAuthentication, TokenAuthentication]
    permission_classes = [IsAuthenticated]

    @method_decorator(ratelimit(key='user', rate='60/m', method='PATCH', block=True))
    def patch(self, request, list_id, item_id):
        """PATCH /api/v1/lists/<list_id>/items/<item_id>/"""
        try:
            profile, err = _get_profile_or_error(request)
            if err:
                return err

            if not _is_premium(request.user):
                return Response({
                    'error': 'Notes are a Premium feature. Upgrade to add personal notes!',
                    'premium_required': True,
                }, status=status.HTTP_403_FORBIDDEN)

            game_list, err = _get_owned_list(list_id, profile)
            if err:
                return err

            try:
                item = GameListItem.objects.get(id=item_id, game_list=game_list)
            except GameListItem.DoesNotExist:
                return Response({'error': 'Item not found.'}, status=status.HTTP_404_NOT_FOUND)

            if 'note' in request.data:
                note = (request.data['note'] or '').strip()
                if len(note) > 500:
                    return Response({'error': 'Note must be 500 characters or less.'}, status=status.HTTP_400_BAD_REQUEST)
                item.note = note
                item.save(update_fields=['note'])

            return Response({
                'success': True,
                'item': _serialize_game_list_item(item),
            })

        except Exception as e:
            logger.exception(f"GameList update item error: {e}")
            return Response({'error': 'Internal error.'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class GameListReorderView(APIView):
    """Reorder items in a game list."""
    authentication_classes = [SessionAuthentication, TokenAuthentication]
    permission_classes = [IsAuthenticated]

    @method_decorator(ratelimit(key='user', rate='60/m', method='POST', block=True))
    def post(self, request, list_id):
        """
        POST /api/v1/lists/<list_id>/items/reorder/

        Body: { "item_id": <int>, "new_position": <int> }
        Moves item_id to new_position, shifting others accordingly.
        """
        try:
            profile, err = _get_profile_or_error(request)
            if err:
                return err

            game_list, err = _get_owned_list(list_id, profile)
            if err:
                return err

            item_id = request.data.get('item_id')
            new_position = request.data.get('new_position')

            if item_id is None or new_position is None:
                return Response({'error': 'item_id and new_position are required.'}, status=status.HTTP_400_BAD_REQUEST)

            try:
                item_id = int(item_id)
                new_position = int(new_position)
            except (TypeError, ValueError):
                return Response({'error': 'item_id and new_position must be integers.'}, status=status.HTTP_400_BAD_REQUEST)

            try:
                item = GameListItem.objects.get(id=item_id, game_list=game_list)
            except GameListItem.DoesNotExist:
                return Response({'error': 'Item not found.'}, status=status.HTTP_404_NOT_FOUND)

            total = game_list.items.count()
            new_position = max(0, min(new_position, total - 1))
            old_position = item.position

            if old_position == new_position:
                return Response({'success': True, 'position': new_position})

            if old_position < new_position:
                # Moving down: shift items in between up
                GameListItem.objects.filter(
                    game_list=game_list,
                    position__gt=old_position,
                    position__lte=new_position,
                ).update(position=F('position') - 1)
            else:
                # Moving up: shift items in between down
                GameListItem.objects.filter(
                    game_list=game_list,
                    position__gte=new_position,
                    position__lt=old_position,
                ).update(position=F('position') + 1)

            item.position = new_position
            item.save(update_fields=['position'])

            return Response({'success': True, 'position': new_position})

        except Exception as e:
            logger.exception(f"GameList reorder error: {e}")
            return Response({'error': 'Internal error.'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


# --- Likes ---

class GameListLikeView(APIView):
    """Toggle like on a public game list."""
    authentication_classes = [SessionAuthentication, TokenAuthentication]
    permission_classes = [IsAuthenticated]

    @method_decorator(ratelimit(key='user', rate='60/m', method='POST', block=True))
    def post(self, request, list_id):
        """POST /api/v1/lists/<list_id>/like/"""
        try:
            profile, err = _get_profile_or_error(request)
            if err:
                return err

            try:
                game_list = GameList.objects.get(id=list_id, is_deleted=False, is_public=True)
            except GameList.DoesNotExist:
                return Response({'error': 'List not found.'}, status=status.HTTP_404_NOT_FOUND)

            # Can't like your own list
            if game_list.profile_id == profile.id:
                return Response({'error': "You can't like your own list."}, status=status.HTTP_400_BAD_REQUEST)

            existing = GameListLike.objects.filter(game_list=game_list, profile=profile)
            if existing.exists():
                existing.delete()
                GameList.objects.filter(id=game_list.id).update(like_count=F('like_count') - 1)
                liked = False
            else:
                GameListLike.objects.create(game_list=game_list, profile=profile)
                GameList.objects.filter(id=game_list.id).update(like_count=F('like_count') + 1)
                liked = True

            game_list.refresh_from_db(fields=['like_count'])

            return Response({
                'success': True,
                'liked': liked,
                'like_count': game_list.like_count,
            })

        except Exception as e:
            logger.exception(f"GameList like error: {e}")
            return Response({'error': 'Internal error.'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


# --- Quick Add ---

class GameListQuickAddView(APIView):
    """Add/remove a game to/from a list (used from game detail/card quick-add dropdown)."""
    authentication_classes = [SessionAuthentication, TokenAuthentication]
    permission_classes = [IsAuthenticated]

    @method_decorator(ratelimit(key='user', rate='120/m', method='POST', block=True))
    def post(self, request):
        """
        POST /api/v1/lists/quick-add/
        Body: { "list_id": <int>, "game_id": <int>, "action": "add"|"remove" }
        """
        try:
            profile, err = _get_profile_or_error(request)
            if err:
                return err

            list_id = request.data.get('list_id')
            game_id = request.data.get('game_id')
            action = request.data.get('action', 'add')

            if not list_id or not game_id:
                return Response({'error': 'list_id and game_id are required.'}, status=status.HTTP_400_BAD_REQUEST)

            game_list, err = _get_owned_list(list_id, profile)
            if err:
                return err

            try:
                game = Game.objects.get(id=game_id)
            except Game.DoesNotExist:
                return Response({'error': 'Game not found.'}, status=status.HTTP_404_NOT_FOUND)

            if action == 'remove':
                deleted_count, _ = GameListItem.objects.filter(
                    game_list=game_list, game=game
                ).delete()
                if deleted_count:
                    # Re-compact positions
                    for idx, item in enumerate(game_list.items.order_by('position')):
                        if item.position != idx:
                            item.position = idx
                            item.save(update_fields=['position'])
                    GameList.objects.filter(id=game_list.id).update(game_count=F('game_count') - 1)
                    game_list.refresh_from_db(fields=['game_count'])
                return Response({
                    'success': True,
                    'action': 'removed',
                    'game_count': game_list.game_count,
                })
            else:
                # Add
                limit_err = _check_item_limit(game_list, request.user)
                if limit_err:
                    return limit_err

                max_position = game_list.items.count()
                try:
                    GameListItem.objects.create(
                        game_list=game_list,
                        game=game,
                        position=max_position,
                    )
                except IntegrityError:
                    return Response({'error': 'Game is already in this list.'}, status=status.HTTP_409_CONFLICT)

                GameList.objects.filter(id=game_list.id).update(game_count=F('game_count') + 1)
                game_list.refresh_from_db(fields=['game_count'])

                return Response({
                    'success': True,
                    'action': 'added',
                    'game_count': game_list.game_count,
                }, status=status.HTTP_201_CREATED)

        except Exception as e:
            logger.exception(f"GameList quick-add error: {e}")
            return Response({'error': 'Internal error.'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


# --- User's Lists ---

class UserGameListsView(APIView):
    """Get the current user's game lists (for quick-add dropdowns)."""
    authentication_classes = [SessionAuthentication, TokenAuthentication]
    permission_classes = [IsAuthenticated]

    def get(self, request):
        """
        GET /api/v1/lists/my/
        Optional query param: game_id — includes `has_game` boolean per list.
        """
        try:
            profile, err = _get_profile_or_error(request)
            if err:
                return err

            lists = GameList.objects.filter(
                profile=profile, is_deleted=False
            ).order_by('-updated_at')

            game_id = safe_int(request.query_params.get('game_id'), None)

            results = []
            for gl in lists:
                data = {
                    'id': gl.id,
                    'name': gl.name,
                    'game_count': gl.game_count,
                    'is_public': gl.is_public,
                }
                if game_id:
                    data['has_game'] = GameListItem.objects.filter(
                        game_list=gl, game_id=game_id
                    ).exists()
                results.append(data)

            is_premium = _is_premium(request.user)
            return Response({
                'lists': results,
                'can_create': is_premium or len(results) < GAME_LIST_FREE_MAX_LISTS,
                'is_premium': is_premium,
                'max_lists': None if is_premium else GAME_LIST_FREE_MAX_LISTS,
                'max_items': None if is_premium else GAME_LIST_FREE_MAX_ITEMS,
            })

        except Exception as e:
            logger.exception(f"User game lists error: {e}")
            return Response({'error': 'Internal error.'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


# --- Copy List ---

class GameListCopyView(APIView):
    """Copy a public game list to the current user's account."""
    authentication_classes = [SessionAuthentication, TokenAuthentication]
    permission_classes = [IsAuthenticated]

    @method_decorator(ratelimit(key='user', rate='10/h', method='POST', block=True))
    def post(self, request, list_id):
        """POST /api/v1/lists/<list_id>/copy/"""
        try:
            profile, err = _get_profile_or_error(request)
            if err:
                return err

            if not _is_premium(request.user):
                return Response({
                    'error': 'Copying lists is a Premium feature.',
                    'premium_required': True,
                }, status=status.HTTP_403_FORBIDDEN)

            try:
                source = GameList.objects.get(id=list_id, is_deleted=False, is_public=True)
            except GameList.DoesNotExist:
                return Response({'error': 'List not found.'}, status=status.HTTP_404_NOT_FOUND)

            # Create copy
            new_list = GameList.objects.create(
                profile=profile,
                name=f"{source.name} (copy)",
                description=source.description,
                is_public=False,
            )

            # Copy items
            source_items = source.items.select_related('game').order_by('position')
            new_items = [
                GameListItem(
                    game_list=new_list,
                    game=item.game,
                    position=item.position,
                )
                for item in source_items
            ]
            GameListItem.objects.bulk_create(new_items)
            new_list.game_count = len(new_items)
            new_list.save(update_fields=['game_count'])

            return Response({
                'success': True,
                'list': _serialize_game_list(new_list),
            }, status=status.HTTP_201_CREATED)

        except Exception as e:
            logger.exception(f"GameList copy error: {e}")
            return Response({'error': 'Internal error.'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


# --- Game Search ---

class GameSearchView(APIView):
    """Search games for adding to a list (typeahead)."""
    authentication_classes = [SessionAuthentication, TokenAuthentication]
    permission_classes = [IsAuthenticated]

    @method_decorator(ratelimit(key='user', rate='120/m', method='GET', block=True))
    def get(self, request):
        """
        GET /api/v1/games/search/?q=<query>&limit=20&exclude_list=<list_id>&platform=PS5,PS4&region=NA,EU
        Returns matching games with basic info for typeahead.
        """
        try:
            query = (request.query_params.get('q') or '').strip()
            if len(query) < 2:
                return Response({'results': []})

            limit = min(safe_int(request.query_params.get('limit', 20), 20), 50)
            exclude_list_id = safe_int(request.query_params.get('exclude_list'), None)

            # Parse optional filter params (comma-separated)
            platforms_raw = (request.query_params.get('platform') or '').strip()
            platforms = [p for p in platforms_raw.split(',') if p in ALL_PLATFORMS] if platforms_raw else []

            regions_raw = (request.query_params.get('region') or '').strip()
            valid_regions = REGIONS + ['global']
            regions = [r for r in regions_raw.split(',') if r in valid_regions] if regions_raw else []

            games = Game.objects.filter(Q(title_name__icontains=query))

            if platforms:
                games = games.for_platform(platforms)
            if regions:
                games = games.for_region(regions)

            games = games.order_by('-played_count')[:limit]

            # If excluding games already in a list
            exclude_game_ids = set()
            if exclude_list_id:
                exclude_game_ids = set(
                    GameListItem.objects.filter(
                        game_list_id=exclude_list_id
                    ).values_list('game_id', flat=True)
                )

            results = []
            for game in games:
                results.append({
                    'id': game.id,
                    'np_communication_id': game.np_communication_id,
                    'title_name': game.title_name,
                    'title_image': game.title_image or '',
                    'title_icon_url': game.title_icon_url or '',
                    'title_platform': game.title_platform or [],
                    'is_regional': game.is_regional,
                    'region': game.region or [],
                    'defined_trophies': game.defined_trophies or {},
                    'played_count': game.played_count,
                    'already_in_list': game.id in exclude_game_ids,
                })

            return Response({'results': results})

        except Exception as e:
            logger.exception(f"Game search error: {e}")
            return Response({'error': 'Internal error.'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
