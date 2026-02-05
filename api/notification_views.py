"""
REST API views for notification system.
Follows the pattern from api/views.py.
"""
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status as http_status
from rest_framework.permissions import IsAuthenticated, IsAdminUser
from rest_framework.authentication import SessionAuthentication, TokenAuthentication
from django.utils.decorators import method_decorator
from django.template.loader import render_to_string
from django_ratelimit.decorators import ratelimit
from notifications.services.notification_service import NotificationService
from notifications.services.template_service import TemplateService
from notifications.services.scheduled_notification_service import ScheduledNotificationService
from notifications.services.shareable_data_service import ShareableDataService
from notifications.models import Notification, NotificationTemplate, ScheduledNotification
from django.contrib.auth import get_user_model
import json
import logging
import base64
import requests
from urllib.parse import urlparse

logger = logging.getLogger(__name__)
CustomUser = get_user_model()


class NotificationListView(APIView):
    """
    GET /api/v1/notifications/
    Query params: unread_only=true, limit=10, offset=0

    Returns:
    {
        "notifications": [...],
        "unread_count": 5,
        "total_count": 42
    }
    """
    permission_classes = [IsAuthenticated]
    authentication_classes = [SessionAuthentication, TokenAuthentication]

    @method_decorator(ratelimit(key='user', rate='60/m', method='GET'))
    def get(self, request):
        try:
            # Parse query parameters
            unread_only = request.GET.get('unread_only', 'false').lower() == 'true'
            notification_type = request.GET.get('type', '')
            limit = min(int(request.GET.get('limit', 10)), 50)  # Cap at 50
            offset = int(request.GET.get('offset', 0))

            # Get notifications using service
            notifications, total_count = NotificationService.get_user_notifications(
                user=request.user,
                unread_only=unread_only,
                notification_type=notification_type,
                limit=limit,
                offset=offset
            )

            # Get unread count
            unread_count = NotificationService.get_unread_count(request.user)

            # Serialize notifications
            notifications_data = []
            for notification in notifications:
                notifications_data.append({
                    'id': notification.id,
                    'notification_type': notification.notification_type,
                    'title': notification.title,
                    'message': notification.message,
                    'detail': notification.detail,  # Rich text detail with markdown
                    'sections': notification.sections,  # Structured sections for admin announcements
                    'banner_image': notification.banner_image.url if notification.banner_image else None,  # Banner image URL
                    'icon': notification.icon,
                    'action_url': notification.action_url,
                    'action_text': notification.action_text,
                    'priority': notification.priority,
                    'metadata': notification.metadata,  # Include metadata for enhanced rendering
                    'is_read': notification.is_read,
                    'created_at': notification.created_at.isoformat(),
                    'read_at': notification.read_at.isoformat() if notification.read_at else None,
                })

            return Response({
                'notifications': notifications_data,
                'unread_count': unread_count,
                'total_count': total_count
            })

        except ValueError as e:
            return Response(
                {'error': f'Invalid parameter: {str(e)}'},
                status=http_status.HTTP_400_BAD_REQUEST
            )
        except Exception as e:
            logger.error(f"Error fetching notifications: {e}")
            return Response(
                {'error': 'Failed to fetch notifications'},
                status=http_status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class NotificationMarkReadView(APIView):
    """POST /api/v1/notifications/<id>/read/"""
    permission_classes = [IsAuthenticated]
    authentication_classes = [SessionAuthentication, TokenAuthentication]

    @method_decorator(ratelimit(key='user', rate='120/m', method='POST'))
    def post(self, request, pk):
        try:
            success = NotificationService.mark_as_read(pk, request.user)

            if success:
                return Response({'success': True})
            else:
                return Response(
                    {'error': 'Notification not found'},
                    status=http_status.HTTP_404_NOT_FOUND
                )

        except Exception as e:
            logger.error(f"Error marking notification as read: {e}")
            return Response(
                {'error': 'Failed to mark notification as read'},
                status=http_status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class NotificationMarkAllReadView(APIView):
    """POST /api/v1/notifications/mark-all-read/"""
    permission_classes = [IsAuthenticated]
    authentication_classes = [SessionAuthentication, TokenAuthentication]

    @method_decorator(ratelimit(key='user', rate='30/m', method='POST'))
    def post(self, request):
        try:
            count = NotificationService.mark_all_as_read(request.user)

            return Response({
                'success': True,
                'count': count
            })

        except Exception as e:
            logger.error(f"Error marking all notifications as read: {e}")
            return Response(
                {'error': 'Failed to mark all notifications as read'},
                status=http_status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class AdminSendNotificationView(APIView):
    """
    POST /api/v1/admin/notifications/send/
    Admin endpoint for sending notifications

    Body: {
        "target_type": "all" | "premium" | "premium_plus" | "individual",
        "notification_type": "admin_announcement",
        "title": "Title",
        "message": "Message",
        "action_url": "/path/",  # optional
        "action_text": "View",  # optional
        "priority": "normal",  # optional
        "icon": "📢",  # optional
        "user_ids": [1, 2, 3]  # required if target_type is "individual"
    }
    """
    permission_classes = [IsAuthenticated, IsAdminUser]
    authentication_classes = [SessionAuthentication, TokenAuthentication]

    @method_decorator(ratelimit(key='user', rate='10/m', method='POST'))
    def post(self, request):
        try:
            # Validate required fields
            target_type = request.data.get('target_type')
            notification_type = request.data.get('notification_type')
            title = request.data.get('title')
            message = request.data.get('message')

            if not all([target_type, notification_type, title, message]):
                return Response(
                    {'error': 'Missing required fields: target_type, notification_type, title, message'},
                    status=http_status.HTTP_400_BAD_REQUEST
                )

            # Validate title and message length
            if len(title) > 255:
                return Response(
                    {'error': 'Title must be 255 characters or less'},
                    status=http_status.HTTP_400_BAD_REQUEST
                )
            if len(message) > 1000:
                return Response(
                    {'error': 'Message must be 1000 characters or less'},
                    status=http_status.HTTP_400_BAD_REQUEST
                )

            # Get optional fields
            action_url = request.data.get('action_url', '')
            action_text = request.data.get('action_text', '')
            priority = request.data.get('priority', 'normal')
            icon = request.data.get('icon', '📢')

            # Get target users
            if target_type == 'individual':
                user_ids = request.data.get('user_ids', [])
                if not user_ids:
                    return Response(
                        {'error': 'user_ids required for individual target_type'},
                        status=http_status.HTTP_400_BAD_REQUEST
                    )
                recipients = NotificationService.get_target_users(
                    target_type=target_type,
                    user_ids=user_ids
                )
            else:
                recipients = NotificationService.get_target_users(target_type=target_type)

            # Check if any recipients found
            recipient_count = recipients.count()
            if recipient_count == 0:
                return Response(
                    {'error': 'No recipients found for target type'},
                    status=http_status.HTTP_400_BAD_REQUEST
                )

            # Send bulk notification
            created_count = NotificationService.send_bulk_notification(
                recipients_queryset=recipients,
                notification_type=notification_type,
                title=title,
                message=message,
                icon=icon,
                action_url=action_url or None,
                action_text=action_text,
                priority=priority,
            )

            logger.info(
                f"Admin {request.user.email} sent {created_count} notifications "
                f"(type: {notification_type}, target: {target_type})"
            )

            return Response({
                'success': True,
                'count': created_count,
                'message': f'Sent {created_count} notifications successfully'
            })

        except Exception as e:
            logger.error(f"Error sending admin notification: {e}")
            return Response(
                {'error': f'Failed to send notifications: {str(e)}'},
                status=http_status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class NotificationBulkDeleteView(APIView):
    """POST /api/v1/notifications/bulk-delete/"""
    permission_classes = [IsAuthenticated]
    authentication_classes = [SessionAuthentication, TokenAuthentication]

    @method_decorator(ratelimit(key='user', rate='30/m', method='POST'))
    def post(self, request):
        try:
            notification_ids = request.data.get('notification_ids', [])

            if not notification_ids:
                return Response(
                    {'error': 'No notification IDs provided'},
                    status=http_status.HTTP_400_BAD_REQUEST
                )

            # Delete only user's own notifications
            deleted_count, _ = Notification.objects.filter(
                id__in=notification_ids,
                recipient=request.user
            ).delete()

            logger.info(f"User {request.user.email} bulk deleted {deleted_count} notifications")

            return Response({
                'success': True,
                'count': deleted_count
            })

        except Exception as e:
            logger.error(f"Error bulk deleting notifications: {e}")
            return Response(
                {'error': 'Failed to delete notifications'},
                status=http_status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class NotificationDeleteView(APIView):
    """DELETE /api/v1/notifications/<id>/"""
    permission_classes = [IsAuthenticated]
    authentication_classes = [SessionAuthentication, TokenAuthentication]

    @method_decorator(ratelimit(key='user', rate='60/m', method='DELETE'))
    def delete(self, request, pk):
        try:
            deleted_count, _ = Notification.objects.filter(
                id=pk,
                recipient=request.user
            ).delete()

            if deleted_count > 0:
                logger.info(f"User {request.user.email} deleted notification {pk}")
                return Response({'success': True})
            else:
                return Response(
                    {'error': 'Notification not found'},
                    status=http_status.HTTP_404_NOT_FOUND
                )

        except Exception as e:
            logger.error(f"Error deleting notification: {e}")
            return Response(
                {'error': 'Failed to delete notification'},
                status=http_status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class NotificationShareImageGenerateView(APIView):
    """
    POST /api/v1/notifications/<id>/share-image/generate/

    Generate shareable images for platinum notifications.

    Body: {
        "format": "landscape" | "portrait" | "both"
    }

    Returns: {
        "success": true,
        "images": {
            "landscape": { "url": "...", "id": 123 },
            "portrait": { "url": "...", "id": 456 }
        }
    }
    """
    permission_classes = [IsAuthenticated]
    authentication_classes = [SessionAuthentication, TokenAuthentication]

    @method_decorator(ratelimit(key='user', rate='10/m', method='POST'))
    def post(self, request, pk):
        from notifications.services.share_image_service import ShareImageService
        from notifications.models import PlatinumShareImage

        try:
            # Verify notification exists and belongs to user
            notification = Notification.objects.get(
                id=pk,
                recipient=request.user,
                notification_type='platinum_earned'
            )
        except Notification.DoesNotExist:
            return Response(
                {'error': 'Platinum notification not found'},
                status=http_status.HTTP_404_NOT_FOUND
            )

        # Validate format parameter
        format_type = request.data.get('format', 'landscape')
        valid_formats = ['landscape', 'portrait', 'both']
        if format_type not in valid_formats:
            return Response(
                {'error': f'Invalid format. Must be one of: {valid_formats}'},
                status=http_status.HTTP_400_BAD_REQUEST
            )

        formats_to_generate = ['landscape', 'portrait'] if format_type == 'both' else [format_type]
        results = {}

        for fmt in formats_to_generate:
            # Check for existing image
            existing = PlatinumShareImage.objects.filter(
                notification=notification,
                format=fmt
            ).first()

            if existing:
                results[fmt] = {
                    'url': existing.image.url,
                    'id': existing.id,
                    'cached': True
                }
                continue

            # Generate new image
            try:
                image_file = ShareImageService.generate_image(notification, fmt)
                share_image = PlatinumShareImage.objects.create(
                    notification=notification,
                    format=fmt,
                    image=image_file
                )
                results[fmt] = {
                    'url': share_image.image.url,
                    'id': share_image.id,
                    'cached': False
                }
                logger.info(f"Generated {fmt} share image for notification {pk}")
            except Exception as e:
                logger.error(f"Failed to generate {fmt} image for notification {pk}: {e}")
                results[fmt] = {'error': str(e)}

        return Response({
            'success': True,
            'images': results
        })


class NotificationShareImageView(APIView):
    """
    GET /api/v1/notifications/<id>/share-image/<format>/

    Retrieve an existing share image for a platinum notification.

    Returns: {
        "url": "...",
        "id": 123,
        "download_count": 5
    }
    """
    permission_classes = [IsAuthenticated]
    authentication_classes = [SessionAuthentication, TokenAuthentication]

    def get(self, request, pk, format_type):
        from notifications.models import PlatinumShareImage

        # Validate format
        if format_type not in ['landscape', 'portrait']:
            return Response(
                {'error': 'Invalid format. Must be landscape or portrait'},
                status=http_status.HTTP_400_BAD_REQUEST
            )

        try:
            notification = Notification.objects.get(
                id=pk,
                recipient=request.user,
                notification_type='platinum_earned'
            )
        except Notification.DoesNotExist:
            return Response(
                {'error': 'Platinum notification not found'},
                status=http_status.HTTP_404_NOT_FOUND
            )

        share_image = PlatinumShareImage.objects.filter(
            notification=notification,
            format=format_type
        ).first()

        if share_image:
            # Increment download count
            share_image.download_count += 1
            share_image.save(update_fields=['download_count'])

            return Response({
                'url': share_image.image.url,
                'id': share_image.id,
                'download_count': share_image.download_count,
                'created_at': share_image.created_at.isoformat()
            })

        return Response(
            {'error': 'Image not found. Generate it first using POST to /share-image/generate/'},
            status=http_status.HTTP_404_NOT_FOUND
        )


class NotificationShareImageStatusView(APIView):
    """
    GET /api/v1/notifications/<id>/share-image/status/

    Check which share images exist for a platinum notification.

    Returns: {
        "landscape": { "exists": true, "url": "...", "id": 123 },
        "portrait": { "exists": false }
    }
    """
    permission_classes = [IsAuthenticated]
    authentication_classes = [SessionAuthentication, TokenAuthentication]

    def get(self, request, pk):
        from notifications.models import PlatinumShareImage

        try:
            notification = Notification.objects.get(
                id=pk,
                recipient=request.user,
                notification_type='platinum_earned'
            )
        except Notification.DoesNotExist:
            return Response(
                {'error': 'Platinum notification not found'},
                status=http_status.HTTP_404_NOT_FOUND
            )

        # Check for existing images
        existing_images = PlatinumShareImage.objects.filter(notification=notification)
        images_by_format = {img.format: img for img in existing_images}

        result = {}
        for fmt in ['landscape', 'portrait']:
            if fmt in images_by_format:
                img = images_by_format[fmt]
                result[fmt] = {
                    'exists': True,
                    'url': img.image.url,
                    'id': img.id,
                    'download_count': img.download_count
                }
            else:
                result[fmt] = {'exists': False}

        return Response(result)


class NotificationShareImageHTMLView(APIView):
    """
    GET /api/v1/notifications/<id>/share-image/html/

    Returns rendered HTML for share image card.
    Query params: format=landscape|portrait

    Returns: { "html": "<rendered html>" }
    """
    permission_classes = [IsAuthenticated]
    authentication_classes = [SessionAuthentication, TokenAuthentication]

    # Cache for base64 encoded images to avoid repeated fetches
    _image_cache = {}

    def get(self, request, pk):
        # Note: Using 'image_format' instead of 'format' because DRF reserves 'format' for content negotiation
        logger.info(f"[SHARE-HTML] Request received for notification {pk}, image_format={request.query_params.get('image_format')}")
        try:
            notification = Notification.objects.get(
                id=pk,
                recipient=request.user,
                notification_type='platinum_earned'
            )
        except Notification.DoesNotExist:
            return Response(
                {'error': 'Platinum notification not found'},
                status=http_status.HTTP_404_NOT_FOUND
            )

        # Get format from query params (using 'image_format' to avoid DRF's reserved 'format' param)
        format_type = request.query_params.get('image_format', 'landscape')
        if format_type not in ['landscape', 'portrait']:
            return Response(
                {'error': 'Invalid format. Must be landscape or portrait'},
                status=http_status.HTTP_400_BAD_REQUEST
            )

        # Extract metadata
        metadata = notification.metadata or {}

        # Calculate playtime string
        playtime = ''
        play_duration_seconds = metadata.get('play_duration_seconds')
        if play_duration_seconds:
            try:
                seconds = float(play_duration_seconds)
                hours = int(seconds // 3600)
                minutes = int((seconds % 3600) // 60)
                if hours > 0:
                    playtime = f"{hours}h {minutes}m"
                else:
                    playtime = f"{minutes}m"
            except (ValueError, TypeError):
                pass

        # Format earn rate
        earn_rate = metadata.get('trophy_earn_rate')
        if earn_rate:
            try:
                earn_rate = round(float(earn_rate), 2)
            except (ValueError, TypeError):
                earn_rate = None

        # Convert external images to base64 data URLs to avoid CORS issues with html2canvas
        game_image_url = metadata.get('game_image', '')
        trophy_icon_url = metadata.get('trophy_icon_url', '')

        game_image_data = self._fetch_image_as_base64(game_image_url) if game_image_url else ''
        trophy_icon_data = self._fetch_image_as_base64(trophy_icon_url) if trophy_icon_url else ''

        # Extract badge data - fetch LIVE from database instead of stale metadata
        # Badge progress is calculated at end of full sync, so metadata may be outdated
        badge_xp, tier1_badges = self._get_live_badge_data(request.user.profile, metadata)
        badge_xp = self._to_int(badge_xp)

        # Process badge images - convert to base64 or use default
        processed_badges = self._process_badge_images(tier1_badges)

        # Format date strings for display
        first_played_date_time = self._format_date(metadata.get('first_played_date_time'))
        earned_date_time = self._format_date(metadata.get('earned_date_time'))

        # Build context for template
        context = {
            'format': format_type,
            'game_name': metadata.get('game_name', 'Unknown Game'),
            'username': metadata.get('username', 'Player'),
            'total_plats': self._to_int(metadata.get('user_total_platinums', 0)),
            'progress': self._to_int(metadata.get('progress_percentage', 0)),
            'earned_trophies': self._to_int(metadata.get('earned_trophies_count', 0)),
            'total_trophies': self._to_int(metadata.get('total_trophies_count', 0)),
            'game_image': game_image_data or game_image_url,  # Fall back to URL if base64 fails
            'trophy_icon': trophy_icon_data or trophy_icon_url,
            'rarity_label': metadata.get('rarity_label', ''),
            'earn_rate': earn_rate,
            'playtime': playtime,
            # Platform and region information
            'title_platform': metadata.get('title_platform', []),
            'region': metadata.get('region', []),
            'is_regional': metadata.get('is_regional', False),
            # Date information
            'first_played_date_time': first_played_date_time,
            'earned_date_time': earned_date_time,
            # Yearly platinum stats
            'yearly_plats': self._to_int(metadata.get('yearly_plats', 0)),
            'earned_year': self._to_int(metadata.get('earned_year', 0)),
            # Badge system data
            'badge_xp': badge_xp,
            'tier1_badges': processed_badges,
            # User rating data - fetch live from database instead of using stale metadata
            'user_rating': self._get_live_user_rating(request.user.profile, metadata),
        }

        # Render the template
        html = render_to_string('notifications/partials/share_image_card.html', context)

        # Convert background images to base64 for JS to use with game art themes
        # This avoids CORS issues when downloading share cards
        concept_bg_url = metadata.get('concept_bg_url', '')

        response_data = {'html': html}

        # Include base64 versions of background images if available
        # game_image is already converted above, reuse it
        if game_image_url and game_image_data:
            response_data['game_image_base64'] = game_image_data

        if concept_bg_url:
            concept_bg_base64 = self._fetch_image_as_base64(concept_bg_url)
            if concept_bg_base64:
                response_data['concept_bg_base64'] = concept_bg_base64

        return Response(response_data)

    @staticmethod
    def _to_int(value, default=0):
        """Safely convert value to int."""
        if value is None:
            return default
        try:
            return int(value)
        except (ValueError, TypeError):
            return default

    @staticmethod
    def _format_date(iso_string):
        """
        Format an ISO date string to a readable format.
        Example: "2024-01-15T14:30:00" -> "Jan 15, 2024"
        """
        if not iso_string:
            return ''
        try:
            from datetime import datetime
            # Parse ISO format (handles both with and without timezone)
            if 'T' in iso_string:
                dt = datetime.fromisoformat(iso_string.replace('Z', '+00:00'))
            else:
                dt = datetime.fromisoformat(iso_string)
            # Format as "Jan 15, 2024"
            return dt.strftime('%b %d, %Y')
        except (ValueError, TypeError):
            return ''

    @staticmethod
    def _get_live_user_rating(profile, metadata):
        """
        Fetch the user's current rating for the game from this notification.
        Returns the live rating instead of potentially stale metadata.
        """
        concept_id = metadata.get('concept_id')
        if not concept_id:
            return None

        try:
            from trophies.models import Concept
            concept = Concept.objects.filter(id=concept_id).first()
            if not concept:
                return None

            from trophies.models import Game
            game = Game.objects.filter(concept=concept).first()
            if not game:
                return None

            return ShareableDataService.get_user_rating_for_game(profile, game)
        except Exception as e:
            logger.warning(f"[SHARE-HTML] Failed to fetch live user rating: {e}")
            return metadata.get('user_rating')  # Fall back to metadata if lookup fails

    @staticmethod
    def _get_live_badge_data(profile, metadata):
        """
        Fetch the user's current badge progress for the game from this notification.
        Returns live badge_xp and tier1_badges instead of potentially stale metadata.

        Badge progress is calculated at the end of a full sync, so the metadata
        stored when the notification was created may be outdated.
        """
        game_id = metadata.get('game_id')
        if not game_id:
            return metadata.get('badge_xp', 0), metadata.get('tier1_badges', [])

        try:
            from trophies.models import Game
            game = Game.objects.filter(id=game_id).first()
            if not game:
                return metadata.get('badge_xp', 0), metadata.get('tier1_badges', [])

            badge_xp = ShareableDataService.get_badge_xp_for_game(profile, game)
            tier1_badges = ShareableDataService.get_tier1_badges_for_game(profile, game)

            return badge_xp, tier1_badges
        except Exception as e:
            logger.warning(f"[SHARE-HTML] Failed to fetch live badge data: {e}")
            return metadata.get('badge_xp', 0), metadata.get('tier1_badges', [])

    def _process_badge_images(self, badges):
        """
        Process badge images for share image rendering.
        Converts badge image URLs to base64 data URLs, using default image for badges without custom images.

        Handles three types of paths:
        1. Full URLs (http/https) - fetched via HTTP
        2. Media paths (/media/...) - loaded from MEDIA_ROOT
        3. Static paths (images/...) - loaded from staticfiles
        """
        if not badges:
            return []

        processed = []
        default_badge_image = None

        for badge in badges:
            badge_copy = dict(badge)  # Don't mutate the original
            badge_image_url = badge_copy.get('badge_image_url', '')

            if badge_image_url:
                # Case 1: Full URL - fetch via HTTP
                if badge_image_url.startswith(('http://', 'https://')):
                    badge_image_data = self._fetch_image_as_base64(badge_image_url)
                    if badge_image_data:
                        badge_copy['badge_image_url'] = badge_image_data
                    # If fetch fails, leave the original URL

                # Case 2: Media file path (/media/...) - load from MEDIA_ROOT
                elif badge_image_url.startswith('/media/'):
                    try:
                        from django.conf import settings
                        # Convert /media/badges/... to MEDIA_ROOT/badges/...
                        relative_path = badge_image_url[len('/media/'):]  # Remove /media/ prefix
                        file_path = settings.MEDIA_ROOT / relative_path

                        if file_path.exists():
                            with open(file_path, 'rb') as f:
                                image_data = base64.b64encode(f.read()).decode('utf-8')
                                # Determine image type from extension
                                if badge_image_url.endswith('.png'):
                                    mime_type = 'image/png'
                                elif badge_image_url.endswith(('.jpg', '.jpeg')):
                                    mime_type = 'image/jpeg'
                                else:
                                    mime_type = 'image/png'
                                badge_copy['badge_image_url'] = f"data:{mime_type};base64,{image_data}"
                        else:
                            logger.warning(f"[SHARE-HTML] Media file not found: {file_path}")
                            # Leave original URL, will use template fallback
                    except Exception as e:
                        logger.warning(f"[SHARE-HTML] Failed to load media badge image: {e}")
                        # Leave original URL, will use template fallback

                # Case 3: Static file path (images/badges/default.png) - load from staticfiles
                else:
                    # Cache the default badge image to avoid reloading it
                    if default_badge_image is None:
                        try:
                            from django.contrib.staticfiles import finders
                            default_path = finders.find(badge_image_url)
                            if default_path:
                                with open(default_path, 'rb') as f:
                                    image_data = base64.b64encode(f.read()).decode('utf-8')
                                    # Determine image type from extension
                                    if badge_image_url.endswith('.png'):
                                        mime_type = 'image/png'
                                    elif badge_image_url.endswith(('.jpg', '.jpeg')):
                                        mime_type = 'image/jpeg'
                                    else:
                                        mime_type = 'image/png'
                                    default_badge_image = f"data:{mime_type};base64,{image_data}"
                            else:
                                logger.warning(f"[SHARE-HTML] Static file not found: {badge_image_url}")
                                default_badge_image = ''
                        except Exception as e:
                            logger.warning(f"[SHARE-HTML] Failed to load static badge image: {e}")
                            default_badge_image = ''
                    badge_copy['badge_image_url'] = default_badge_image
            else:
                # No image URL provided - use default badge image
                if default_badge_image is None:
                    try:
                        from django.contrib.staticfiles import finders
                        default_path = finders.find('images/badges/default.png')
                        if default_path:
                            with open(default_path, 'rb') as f:
                                image_data = base64.b64encode(f.read()).decode('utf-8')
                                default_badge_image = f"data:image/png;base64,{image_data}"
                        else:
                            default_badge_image = ''
                    except Exception as e:
                        logger.warning(f"[SHARE-HTML] Failed to load default badge image: {e}")
                        default_badge_image = ''

                badge_copy['badge_image_url'] = default_badge_image

            processed.append(badge_copy)

        return processed

    def _fetch_image_as_base64(self, url):
        """
        Fetch an external image and convert it to a base64 data URL.
        This allows html2canvas to render external images without CORS issues.
        """
        if not url:
            return ''

        # Check cache first
        if url in self._image_cache:
            return self._image_cache[url]

        try:
            # Validate URL
            parsed = urlparse(url)
            if not parsed.scheme in ('http', 'https'):
                logger.warning(f"[SHARE-HTML] Invalid URL scheme: {url}")
                return ''

            # Fetch the image with timeout
            response = requests.get(url, timeout=10, headers={
                'User-Agent': 'Mozilla/5.0 (compatible; PlatPursuit/1.0)'
            })
            response.raise_for_status()

            # Get content type
            content_type = response.headers.get('Content-Type', 'image/png')
            if not content_type.startswith('image/'):
                content_type = 'image/png'

            # Encode to base64
            image_data = base64.b64encode(response.content).decode('utf-8')
            data_url = f"data:{content_type};base64,{image_data}"

            # Cache the result (limit cache size)
            if len(self._image_cache) < 100:
                self._image_cache[url] = data_url

            return data_url

        except requests.RequestException as e:
            logger.warning(f"[SHARE-HTML] Failed to fetch image {url}: {e}")
            return ''
        except Exception as e:
            logger.error(f"[SHARE-HTML] Error encoding image {url}: {e}")
            return ''


class NotificationRatingView(APIView):
    """
    GET /api/v1/notifications/<id>/rating/
    Returns rendered rating form HTML with community averages and existing user rating.

    POST /api/v1/notifications/<id>/rating/
    Submit or update a game rating from the notification detail view.
    Body: {
        "difficulty": 1-10,
        "grindiness": 1-10,
        "hours_to_platinum": int,
        "fun_ranking": 1-10,
        "overall_rating": 0.5-5.0
    }
    """
    permission_classes = [IsAuthenticated]
    authentication_classes = [SessionAuthentication, TokenAuthentication]

    def get(self, request, pk):
        """Return rendered rating form HTML with existing data."""
        try:
            notification = Notification.objects.get(
                id=pk,
                recipient=request.user,
                notification_type='platinum_earned'
            )
        except Notification.DoesNotExist:
            return Response(
                {'error': 'Notification not found'},
                status=http_status.HTTP_404_NOT_FOUND
            )

        metadata = notification.metadata or {}
        concept_id = metadata.get('concept_id')

        if not concept_id:
            return Response(
                {'error': 'No concept linked to this game - ratings not available'},
                status=http_status.HTTP_400_BAD_REQUEST
            )

        from trophies.models import Concept, UserConceptRating
        from trophies.services.rating_service import RatingService

        try:
            concept = Concept.objects.get(id=concept_id)
        except Concept.DoesNotExist:
            return Response(
                {'error': 'Game concept not found'},
                status=http_status.HTTP_404_NOT_FOUND
            )

        # Get user's profile
        profile = getattr(request.user, 'profile', None)

        # Check for existing rating
        existing_rating = None
        if profile:
            existing_rating = UserConceptRating.objects.filter(
                profile=profile,
                concept=concept
            ).first()

        # Get community averages
        community_averages = RatingService.get_cached_community_averages(concept)

        # Check if user just submitted their first rating (show thank you message instead)
        just_rated = request.GET.get('just_rated') == '1'

        # Prepare context for template
        context = {
            'concept_id': concept_id,
            'community_averages': community_averages,
            'has_existing_rating': existing_rating is not None,
            'just_rated': just_rated,  # Show thank you message instead of "already rated"
            'existing_rating': {
                'difficulty': existing_rating.difficulty,
                'grindiness': existing_rating.grindiness,
                'hours_to_platinum': existing_rating.hours_to_platinum,
                'fun_ranking': existing_rating.fun_ranking,
                'overall_rating': existing_rating.overall_rating,
            } if existing_rating else None,
            'game_name': metadata.get('game_name', 'this game'),
        }

        html = render_to_string('notifications/partials/rating_section.html', context)

        return Response({
            'html': html,
            'has_existing_rating': existing_rating is not None,
            'existing_rating': context['existing_rating'],
            'community_averages': community_averages
        })

    @method_decorator(ratelimit(key='user', rate='30/m', method='POST'))
    def post(self, request, pk):
        """Submit or update a rating."""
        try:
            notification = Notification.objects.get(
                id=pk,
                recipient=request.user,
                notification_type='platinum_earned'
            )
        except Notification.DoesNotExist:
            return Response(
                {'error': 'Notification not found'},
                status=http_status.HTTP_404_NOT_FOUND
            )

        metadata = notification.metadata or {}
        concept_id = metadata.get('concept_id')

        if not concept_id:
            return Response(
                {'error': 'No concept linked to this game'},
                status=http_status.HTTP_400_BAD_REQUEST
            )

        from trophies.models import Concept, UserConceptRating
        from trophies.forms import UserConceptRatingForm
        from trophies.services.rating_service import RatingService

        try:
            concept = Concept.objects.get(id=concept_id)
        except Concept.DoesNotExist:
            return Response(
                {'error': 'Concept not found'},
                status=http_status.HTTP_404_NOT_FOUND
            )

        profile = getattr(request.user, 'profile', None)
        if not profile:
            return Response(
                {'error': 'Profile not found'},
                status=http_status.HTTP_400_BAD_REQUEST
            )

        # Get or create rating
        existing_rating = UserConceptRating.objects.filter(
            profile=profile,
            concept=concept
        ).first()

        form = UserConceptRatingForm(request.data, instance=existing_rating)

        if form.is_valid():
            rating = form.save(commit=False)
            rating.profile = profile
            rating.concept = concept
            rating.save()

            # Invalidate cache
            RatingService.invalidate_cache(concept)

            # Get updated averages
            updated_averages = RatingService.get_community_averages(concept)

            # Check for rating milestones
            from trophies.services.milestone_service import check_all_milestones_for_user
            check_all_milestones_for_user(profile, criteria_type='rating_count')

            return Response({
                'success': True,
                'message': 'Rating updated!' if existing_rating else 'Rating submitted successfully!',
                'community_averages': updated_averages
            })
        else:
            return Response({
                'success': False,
                'errors': form.errors
            }, status=http_status.HTTP_400_BAD_REQUEST)


class AdminNotificationPreviewView(APIView):
    """
    POST /api/v1/admin/notifications/preview/
    Preview how notification will look.

    Body: {
        "title": "Title",
        "message": "Message",
        "icon": "📢",
        "action_url": "/path/",
        "action_text": "View",
        "priority": "normal"
    }
    """
    permission_classes = [IsAuthenticated, IsAdminUser]
    authentication_classes = [SessionAuthentication]

    def post(self, request):
        from django.utils import timezone

        title = request.data.get('title', '')
        message = request.data.get('message', '')
        icon = request.data.get('icon', '📢')
        action_url = request.data.get('action_url', '')
        action_text = request.data.get('action_text', '')
        priority = request.data.get('priority', 'normal')

        return Response({
            'preview': {
                'title': title,
                'message': message,
                'icon': icon,
                'action_url': action_url,
                'action_text': action_text,
                'priority': priority,
                'created_at': timezone.now().isoformat(),
            }
        })


class AdminTargetCountView(APIView):
    """
    POST /api/v1/admin/notifications/target-count/
    Get estimated recipient count for targeting criteria.

    Body: {
        "target_type": "all",
        "criteria": {}
    }
    """
    permission_classes = [IsAuthenticated, IsAdminUser]
    authentication_classes = [SessionAuthentication]

    def post(self, request):
        target_type = request.data.get('target_type', 'all')
        criteria = request.data.get('criteria', {})

        try:
            count = ScheduledNotificationService.estimate_recipient_count(
                target_type, criteria
            )
            return Response({'count': count})
        except Exception as e:
            logger.error(f"Error getting target count: {e}")
            return Response(
                {'error': str(e)},
                status=http_status.HTTP_400_BAD_REQUEST
            )


class AdminUserSearchView(APIView):
    """
    GET /api/v1/admin/notifications/user-search/?q=username
    Search users for individual targeting.
    """
    permission_classes = [IsAuthenticated, IsAdminUser]
    authentication_classes = [SessionAuthentication]

    def get(self, request):
        from django.db.models import Q
        from trophies.models import Profile

        query = request.GET.get('q', '').strip()
        if len(query) < 2:
            return Response({'users': []})

        profiles = Profile.objects.filter(
            Q(psn_username__icontains=query) |
            Q(user__email__icontains=query)
        ).select_related('user').filter(user__isnull=False)[:10]

        users = []
        for profile in profiles:
            if profile.user:
                users.append({
                    'id': profile.user.id,
                    'psn_username': profile.psn_username,
                    'email': profile.user.email,
                    'avatar_url': profile.avatar_url if hasattr(profile, 'avatar_url') else '',
                })

        return Response({'users': users})
