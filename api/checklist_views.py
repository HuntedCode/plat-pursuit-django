"""
Checklist API views.

Handles all REST endpoints for checklists, sections, items, voting, progress, and reporting.
"""
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.authentication import SessionAuthentication, TokenAuthentication
from rest_framework.parsers import MultiPartParser, FormParser
from django.utils.decorators import method_decorator
from django.template.loader import render_to_string
from django_ratelimit.decorators import ratelimit
from trophies.models import Checklist, ChecklistSection, ChecklistItem, UserChecklistProgress, ChecklistVote, Concept
from trophies.services.checklist_service import ChecklistService
from .serializers import (
    ChecklistSerializer, ChecklistDetailSerializer, ChecklistCreateSerializer,
    ChecklistUpdateSerializer, ChecklistSectionSerializer, ChecklistSectionCreateSerializer,
    ChecklistSectionUpdateSerializer, ChecklistItemSerializer, ChecklistItemCreateSerializer,
    ChecklistItemUpdateSerializer, ChecklistItemBulkItemSerializer, ChecklistItemBulkCreateSerializer,
    ChecklistReorderSerializer, ChecklistReportSerializer, ChecklistImageUploadSerializer,
    SectionImageUploadSerializer, ItemImageCreateSerializer, TrophySerializer, GameSelectionSerializer
)
import logging

logger = logging.getLogger('psn_api')


class ChecklistListView(APIView):
    """Get published checklists for a Concept."""
    authentication_classes = [SessionAuthentication, TokenAuthentication]
    permission_classes = []  # Allow unauthenticated to view

    def get(self, request, concept_id):
        """
        GET /api/v1/checklists/concept/<concept_id>/
        Query params:
            - sort (top/new/popular)
            - limit (default: 10)
            - offset (default: 0)
            - format (json/html) - default: json. If html, returns rendered card HTML
        """
        try:
            try:
                concept = Concept.objects.get(id=concept_id)
            except Concept.DoesNotExist:
                return Response({'error': 'Concept not found.'}, status=status.HTTP_404_NOT_FOUND)

            profile = None
            if request.user.is_authenticated:
                profile = getattr(request.user, 'profile', None)

            sort = request.query_params.get('sort', 'top')
            if sort not in ['top', 'new', 'popular']:
                sort = 'top'

            limit = int(request.query_params.get('limit', 10))
            offset = int(request.query_params.get('offset', 0))
            # Use 'output' instead of 'format' to avoid conflict with DRF's format negotiation
            output_format = request.query_params.get('output', 'json')

            checklists = ChecklistService.get_checklists_for_concept(concept, profile, sort)
            total_count = checklists.count()
            paginated = checklists[offset:offset + limit]

            # If HTML format requested, render cards server-side
            if output_format == 'html':
                cards_html = []
                for checklist in paginated:
                    # Build context for template
                    user_has_voted = False
                    user_progress = None
                    can_edit = False

                    if profile:
                        user_has_voted = ChecklistVote.objects.filter(checklist=checklist, profile=profile).exists()
                        progress = UserChecklistProgress.objects.filter(checklist=checklist, profile=profile).first()
                        if progress:
                            user_progress = {
                                'percentage': progress.progress_percentage,
                                'items_completed': progress.items_completed,
                                'total_items': progress.total_items
                            }
                        can_edit = checklist.profile == profile

                    # Check if author has platinum for this game/concept
                    author_has_platinum = False
                    if checklist.concept and checklist.profile:
                        from trophies.models import ProfileGame
                        pg = ProfileGame.objects.filter(
                            profile=checklist.profile,
                            game__concept=checklist.concept
                        ).order_by('-progress').first()
                        if pg:
                            author_has_platinum = pg.has_plat

                    card_context = {
                        'checklist': {
                            'id': checklist.id,
                            'title': checklist.title,
                            'description': checklist.description,
                            'thumbnail': checklist.thumbnail,
                            'author': {
                                'username': checklist.profile.display_psn_username,
                                'avatar_url': checklist.profile.avatar_url,
                                'user_is_premium': checklist.profile.user_is_premium,
                                'flag': checklist.profile.flag,
                                'author_has_platinum': author_has_platinum
                            },
                            'upvote_count': checklist.upvote_count,
                            'progress_save_count': checklist.progress_save_count,
                            'total_items': checklist.total_items,
                            'section_count': checklist.sections.count(),
                            'user_has_voted': user_has_voted,
                            'user_progress': user_progress,
                            'can_edit': can_edit
                        }
                    }
                    try:
                        rendered = render_to_string('partials/checklist_card.html', card_context, request=request)
                        cards_html.append(rendered)
                    except Exception as template_error:
                        logger.error(f"Template render error: {template_error}")
                        raise

                return Response({
                    'cards_html': cards_html,
                    'total_count': total_count,
                    'has_more': (offset + limit) < total_count,
                    'next_offset': offset + limit,
                    'sort': sort
                })

            # Default JSON response
            serializer = ChecklistSerializer(paginated, many=True, context={'request': request})

            return Response({
                'checklists': serializer.data,
                'total_count': total_count,
                'has_more': (offset + limit) < total_count,
                'next_offset': offset + limit,
                'sort': sort
            })

        except Exception as e:
            import traceback
            logger.error(f"Checklist list error: {e}\n{traceback.format_exc()}")
            return Response({'error': 'Internal error.'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class ChecklistCreateView(APIView):
    """Create a new checklist (as draft)."""
    authentication_classes = [SessionAuthentication, TokenAuthentication]
    permission_classes = [IsAuthenticated]

    @method_decorator(ratelimit(key='user', rate='10/m', method='POST', block=True))
    def post(self, request, concept_id):
        """
        POST /api/v1/checklists/concept/<concept_id>/create/
        Body: { title, description (optional) }
        """
        try:
            try:
                concept = Concept.objects.get(id=concept_id)
            except Concept.DoesNotExist:
                return Response({'error': 'Concept not found.'}, status=status.HTTP_404_NOT_FOUND)

            profile = getattr(request.user, 'profile', None)
            serializer = ChecklistCreateSerializer(data=request.data)

            if not serializer.is_valid():
                return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

            checklist, error = ChecklistService.create_checklist(
                profile=profile,
                concept=concept,
                title=serializer.validated_data['title'],
                description=serializer.validated_data.get('description', '')
            )

            if error:
                return Response({'error': error}, status=status.HTTP_400_BAD_REQUEST)

            return Response({
                'success': True,
                'checklist': ChecklistDetailSerializer(checklist, context={'request': request}).data
            }, status=status.HTTP_201_CREATED)

        except Exception as e:
            logger.error(f"Checklist create error: {e}")
            return Response({'error': 'Internal error.'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class ChecklistDetailView(APIView):
    """Get, update, or delete a checklist."""
    authentication_classes = [SessionAuthentication, TokenAuthentication]
    permission_classes = []

    def get(self, request, checklist_id):
        """GET /api/v1/checklists/<checklist_id>/"""
        try:
            try:
                checklist = Checklist.objects.select_related(
                    'profile', 'profile__user', 'concept'
                ).prefetch_related(
                    'sections__items'
                ).get(id=checklist_id, is_deleted=False)
            except Checklist.DoesNotExist:
                return Response({'error': 'Checklist not found.'}, status=status.HTTP_404_NOT_FOUND)

            profile = None
            if request.user.is_authenticated:
                profile = getattr(request.user, 'profile', None)

            # Only author can view drafts
            if checklist.status == 'draft' and checklist.profile != profile:
                return Response({'error': 'Checklist not found.'}, status=status.HTTP_404_NOT_FOUND)

            serializer = ChecklistDetailSerializer(checklist, context={'request': request})
            return Response(serializer.data)

        except Exception as e:
            logger.error(f"Checklist detail error: {e}")
            return Response({'error': 'Internal error.'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    def patch(self, request, checklist_id):
        """PATCH /api/v1/checklists/<checklist_id>/"""
        try:
            try:
                checklist = Checklist.objects.get(id=checklist_id, is_deleted=False)
            except Checklist.DoesNotExist:
                return Response({'error': 'Checklist not found.'}, status=status.HTTP_404_NOT_FOUND)

            profile = getattr(request.user, 'profile', None)
            serializer = ChecklistUpdateSerializer(data=request.data)

            if not serializer.is_valid():
                return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

            success, error = ChecklistService.update_checklist(
                checklist=checklist,
                profile=profile,
                title=serializer.validated_data.get('title'),
                description=serializer.validated_data.get('description')
            )

            if not success:
                return Response({'error': error}, status=status.HTTP_400_BAD_REQUEST)

            checklist.refresh_from_db()
            return Response({
                'success': True,
                'checklist': ChecklistDetailSerializer(checklist, context={'request': request}).data
            })

        except Exception as e:
            logger.error(f"Checklist update error: {e}")
            return Response({'error': 'Internal error.'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    def delete(self, request, checklist_id):
        """DELETE /api/v1/checklists/<checklist_id>/"""
        try:
            try:
                checklist = Checklist.objects.get(id=checklist_id)
            except Checklist.DoesNotExist:
                return Response({'error': 'Checklist not found.'}, status=status.HTTP_404_NOT_FOUND)

            profile = getattr(request.user, 'profile', None)
            is_admin = request.user.is_staff if request.user.is_authenticated else False

            success, error = ChecklistService.delete_checklist(checklist, profile, is_admin)

            if not success:
                return Response({'error': error}, status=status.HTTP_400_BAD_REQUEST)

            return Response({'success': True})

        except Exception as e:
            logger.error(f"Checklist delete error: {e}")
            return Response({'error': 'Internal error.'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class ChecklistPublishView(APIView):
    """Publish or unpublish a checklist."""
    authentication_classes = [SessionAuthentication, TokenAuthentication]
    permission_classes = [IsAuthenticated]

    def get(self, request, checklist_id):
        """GET /api/v1/checklists/<checklist_id>/publish/ - Get publish status and tracker count."""
        try:
            try:
                checklist = Checklist.objects.get(id=checklist_id, is_deleted=False)
            except Checklist.DoesNotExist:
                return Response({'error': 'Checklist not found.'}, status=status.HTTP_404_NOT_FOUND)

            profile = getattr(request.user, 'profile', None)
            if checklist.profile != profile:
                return Response({'error': 'Not authorized.'}, status=status.HTTP_403_FORBIDDEN)

            tracker_count = ChecklistService.get_tracker_count(checklist)

            return Response({
                'status': checklist.status,
                'is_published': checklist.is_published,
                'tracker_count': tracker_count
            })

        except Exception as e:
            logger.error(f"Checklist publish status error: {e}")
            return Response({'error': 'Internal error.'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    def post(self, request, checklist_id):
        """POST /api/v1/checklists/<checklist_id>/publish/"""
        try:
            try:
                checklist = Checklist.objects.get(id=checklist_id, is_deleted=False)
            except Checklist.DoesNotExist:
                return Response({'error': 'Checklist not found.'}, status=status.HTTP_404_NOT_FOUND)

            profile = getattr(request.user, 'profile', None)
            success, error = ChecklistService.publish_checklist(checklist, profile)

            if not success:
                return Response({'error': error}, status=status.HTTP_400_BAD_REQUEST)

            return Response({'success': True, 'status': 'published'})

        except Exception as e:
            logger.error(f"Checklist publish error: {e}")
            return Response({'error': 'Internal error.'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    def delete(self, request, checklist_id):
        """DELETE /api/v1/checklists/<checklist_id>/publish/ (unpublish)"""
        try:
            try:
                checklist = Checklist.objects.get(id=checklist_id, is_deleted=False)
            except Checklist.DoesNotExist:
                return Response({'error': 'Checklist not found.'}, status=status.HTTP_404_NOT_FOUND)

            profile = getattr(request.user, 'profile', None)
            success, error = ChecklistService.unpublish_checklist(checklist, profile)

            if not success:
                return Response({'error': error}, status=status.HTTP_400_BAD_REQUEST)

            return Response({'success': True, 'status': 'draft'})

        except Exception as e:
            logger.error(f"Checklist unpublish error: {e}")
            return Response({'error': 'Internal error.'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class ChecklistVoteView(APIView):
    """Toggle upvote on a checklist."""
    authentication_classes = [SessionAuthentication, TokenAuthentication]
    permission_classes = [IsAuthenticated]

    @method_decorator(ratelimit(key='user', rate='60/m', method='POST', block=True))
    def post(self, request, checklist_id):
        """POST /api/v1/checklists/<checklist_id>/vote/"""
        try:
            try:
                checklist = Checklist.objects.get(id=checklist_id, is_deleted=False)
            except Checklist.DoesNotExist:
                return Response({'error': 'Checklist not found.'}, status=status.HTTP_404_NOT_FOUND)

            profile = getattr(request.user, 'profile', None)
            voted, error = ChecklistService.toggle_vote(checklist, profile)

            if error:
                return Response({'error': error}, status=status.HTTP_400_BAD_REQUEST)

            return Response({
                'success': True,
                'voted': voted,
                'upvote_count': checklist.upvote_count
            })

        except Exception as e:
            logger.error(f"Checklist vote error: {e}")
            return Response({'error': 'Internal error.'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class ChecklistReportView(APIView):
    """Report a checklist for moderation."""
    authentication_classes = [SessionAuthentication, TokenAuthentication]
    permission_classes = [IsAuthenticated]

    @method_decorator(ratelimit(key='user', rate='5/h', method='POST', block=True))
    def post(self, request, checklist_id):
        """POST /api/v1/checklists/<checklist_id>/report/"""
        try:
            try:
                checklist = Checklist.objects.get(id=checklist_id, is_deleted=False)
            except Checklist.DoesNotExist:
                return Response({'error': 'Checklist not found.'}, status=status.HTTP_404_NOT_FOUND)

            profile = getattr(request.user, 'profile', None)
            if not profile:
                return Response({'error': 'Profile required to report.'}, status=status.HTTP_403_FORBIDDEN)

            serializer = ChecklistReportSerializer(data=request.data)

            if not serializer.is_valid():
                return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

            report, error = ChecklistService.report_checklist(
                checklist=checklist,
                reporter=profile,
                reason=serializer.validated_data['reason'],
                details=serializer.validated_data.get('details', '')
            )

            if error:
                return Response({'error': error}, status=status.HTTP_400_BAD_REQUEST)

            return Response({'success': True, 'report_id': report.id})

        except Exception as e:
            logger.error(f"Checklist report error: {e}")
            return Response({'error': 'Internal error.'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class ChecklistProgressToggleView(APIView):
    """Toggle item completion for a user."""
    authentication_classes = [SessionAuthentication, TokenAuthentication]
    permission_classes = [IsAuthenticated]

    def post(self, request, checklist_id, item_id):
        """POST /api/v1/checklists/<checklist_id>/progress/toggle/<item_id>/"""
        try:
            try:
                checklist = Checklist.objects.get(id=checklist_id, is_deleted=False)
            except Checklist.DoesNotExist:
                return Response({'error': 'Checklist not found.'}, status=status.HTTP_404_NOT_FOUND)

            profile = getattr(request.user, 'profile', None)
            completed, error = ChecklistService.toggle_item_progress(checklist, profile, item_id)

            if error:
                return Response({'error': error}, status=status.HTTP_400_BAD_REQUEST)

            # Get updated progress
            progress = ChecklistService.get_user_progress(checklist, profile)

            return Response({
                'success': True,
                'item_completed': completed,
                'progress_percentage': progress.progress_percentage if progress else 0,
                'items_completed': progress.items_completed if progress else 0,
                'total_items': progress.total_items if progress else checklist.total_items
            })

        except Exception as e:
            logger.error(f"Checklist progress toggle error: {e}")
            return Response({'error': 'Internal error.'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class ChecklistProgressView(APIView):
    """Get user's progress on a checklist."""
    authentication_classes = [SessionAuthentication, TokenAuthentication]
    permission_classes = [IsAuthenticated]

    def get(self, request, checklist_id):
        """GET /api/v1/checklists/<checklist_id>/progress/"""
        try:
            try:
                checklist = Checklist.objects.get(id=checklist_id, is_deleted=False)
            except Checklist.DoesNotExist:
                return Response({'error': 'Checklist not found.'}, status=status.HTTP_404_NOT_FOUND)

            profile = getattr(request.user, 'profile', None)
            progress = ChecklistService.get_user_progress(checklist, profile)

            if not progress:
                return Response({
                    'has_progress': False,
                    'completed_items': [],
                    'items_completed': 0,
                    'total_items': checklist.total_items,
                    'progress_percentage': 0
                })

            return Response({
                'has_progress': True,
                'completed_items': progress.completed_items,
                'items_completed': progress.items_completed,
                'total_items': progress.total_items,
                'progress_percentage': progress.progress_percentage
            })

        except Exception as e:
            logger.error(f"Checklist progress error: {e}")
            return Response({'error': 'Internal error.'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


# ---------- Section Endpoints ----------

class ChecklistSectionListView(APIView):
    """List or add sections to a checklist."""
    authentication_classes = [SessionAuthentication, TokenAuthentication]
    permission_classes = [IsAuthenticated]

    def get(self, request, checklist_id):
        """GET /api/v1/checklists/<checklist_id>/sections/"""
        try:
            try:
                checklist = Checklist.objects.get(id=checklist_id, is_deleted=False)
            except Checklist.DoesNotExist:
                return Response({'error': 'Checklist not found.'}, status=status.HTTP_404_NOT_FOUND)

            profile = getattr(request.user, 'profile', None)

            # Only author can view draft sections
            if checklist.status == 'draft' and checklist.profile != profile:
                return Response({'error': 'Checklist not found.'}, status=status.HTTP_404_NOT_FOUND)

            sections = checklist.sections.prefetch_related('items').order_by('order')
            serializer = ChecklistSectionSerializer(sections, many=True)

            return Response({'sections': serializer.data})

        except Exception as e:
            logger.error(f"Section list error: {e}")
            return Response({'error': 'Internal error.'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    def post(self, request, checklist_id):
        """POST /api/v1/checklists/<checklist_id>/sections/"""
        try:
            try:
                checklist = Checklist.objects.get(id=checklist_id, is_deleted=False)
            except Checklist.DoesNotExist:
                return Response({'error': 'Checklist not found.'}, status=status.HTTP_404_NOT_FOUND)

            profile = getattr(request.user, 'profile', None)
            serializer = ChecklistSectionCreateSerializer(data=request.data)

            if not serializer.is_valid():
                return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

            section, error = ChecklistService.add_section(
                checklist=checklist,
                profile=profile,
                subtitle=serializer.validated_data['subtitle'],
                description=serializer.validated_data.get('description', ''),
                order=serializer.validated_data.get('order')
            )

            if error:
                return Response({'error': error}, status=status.HTTP_400_BAD_REQUEST)

            return Response({
                'success': True,
                'section': ChecklistSectionSerializer(section).data
            }, status=status.HTTP_201_CREATED)

        except Exception as e:
            logger.error(f"Section create error: {e}")
            return Response({'error': 'Internal error.'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class ChecklistSectionDetailView(APIView):
    """Update or delete a section."""
    authentication_classes = [SessionAuthentication, TokenAuthentication]
    permission_classes = [IsAuthenticated]

    def patch(self, request, checklist_id, section_id):
        """PATCH /api/v1/checklists/<checklist_id>/sections/<section_id>/"""
        try:
            try:
                section = ChecklistSection.objects.get(
                    id=section_id,
                    checklist_id=checklist_id,
                    checklist__is_deleted=False
                )
            except ChecklistSection.DoesNotExist:
                return Response({'error': 'Section not found.'}, status=status.HTTP_404_NOT_FOUND)

            profile = getattr(request.user, 'profile', None)
            serializer = ChecklistSectionUpdateSerializer(data=request.data)

            if not serializer.is_valid():
                return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

            success, error = ChecklistService.update_section(
                section=section,
                profile=profile,
                subtitle=serializer.validated_data.get('subtitle'),
                description=serializer.validated_data.get('description'),
                order=serializer.validated_data.get('order')
            )

            if not success:
                return Response({'error': error}, status=status.HTTP_400_BAD_REQUEST)

            section.refresh_from_db()
            return Response({
                'success': True,
                'section': ChecklistSectionSerializer(section).data
            })

        except Exception as e:
            logger.error(f"Section update error: {e}")
            return Response({'error': 'Internal error.'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    def delete(self, request, checklist_id, section_id):
        """DELETE /api/v1/checklists/<checklist_id>/sections/<section_id>/"""
        try:
            try:
                section = ChecklistSection.objects.get(
                    id=section_id,
                    checklist_id=checklist_id,
                    checklist__is_deleted=False
                )
            except ChecklistSection.DoesNotExist:
                return Response({'error': 'Section not found.'}, status=status.HTTP_404_NOT_FOUND)

            profile = getattr(request.user, 'profile', None)
            success, error = ChecklistService.delete_section(section, profile)

            if not success:
                return Response({'error': error}, status=status.HTTP_400_BAD_REQUEST)

            return Response({'success': True})

        except Exception as e:
            logger.error(f"Section delete error: {e}")
            return Response({'error': 'Internal error.'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class ChecklistSectionReorderView(APIView):
    """Reorder sections within a checklist."""
    authentication_classes = [SessionAuthentication, TokenAuthentication]
    permission_classes = [IsAuthenticated]

    def post(self, request, checklist_id):
        """POST /api/v1/checklists/<checklist_id>/sections/reorder/"""
        try:
            try:
                checklist = Checklist.objects.get(id=checklist_id, is_deleted=False)
            except Checklist.DoesNotExist:
                return Response({'error': 'Checklist not found.'}, status=status.HTTP_404_NOT_FOUND)

            profile = getattr(request.user, 'profile', None)
            serializer = ChecklistReorderSerializer(data=request.data)

            if not serializer.is_valid():
                return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

            success, error = ChecklistService.reorder_sections(
                checklist=checklist,
                profile=profile,
                section_ids=serializer.validated_data['ids']
            )

            if not success:
                return Response({'error': error}, status=status.HTTP_400_BAD_REQUEST)

            return Response({'success': True})

        except Exception as e:
            logger.error(f"Section reorder error: {e}")
            return Response({'error': 'Internal error.'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


# ---------- Item Endpoints ----------

class ChecklistItemListView(APIView):
    """List or add items to a section."""
    authentication_classes = [SessionAuthentication, TokenAuthentication]
    permission_classes = [IsAuthenticated]

    def get(self, request, section_id):
        """GET /api/v1/checklists/sections/<section_id>/items/"""
        try:
            try:
                section = ChecklistSection.objects.get(
                    id=section_id,
                    checklist__is_deleted=False
                )
            except ChecklistSection.DoesNotExist:
                return Response({'error': 'Section not found.'}, status=status.HTTP_404_NOT_FOUND)

            profile = getattr(request.user, 'profile', None)

            # Only author can view draft items
            if section.checklist.status == 'draft' and section.checklist.profile != profile:
                return Response({'error': 'Section not found.'}, status=status.HTTP_404_NOT_FOUND)

            items = section.items.order_by('order')
            serializer = ChecklistItemSerializer(items, many=True)

            return Response({'items': serializer.data})

        except Exception as e:
            logger.error(f"Item list error: {e}")
            return Response({'error': 'Internal error.'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    def post(self, request, section_id):
        """POST /api/v1/checklists/sections/<section_id>/items/"""
        try:
            try:
                section = ChecklistSection.objects.get(
                    id=section_id,
                    checklist__is_deleted=False
                )
            except ChecklistSection.DoesNotExist:
                return Response({'error': 'Section not found.'}, status=status.HTTP_404_NOT_FOUND)

            profile = getattr(request.user, 'profile', None)
            serializer = ChecklistItemCreateSerializer(data=request.data)

            if not serializer.is_valid():
                return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

            item_type = serializer.validated_data.get('item_type', 'item')

            # Handle trophy items separately
            if item_type == 'trophy':
                item, error = ChecklistService.add_trophy_item(
                    section=section,
                    profile=profile,
                    trophy_id=serializer.validated_data['trophy_id'],
                    order=serializer.validated_data.get('order')
                )
            else:
                # Existing item creation logic
                item, error = ChecklistService.add_item(
                    section=section,
                    profile=profile,
                    text=serializer.validated_data.get('text', ''),
                    item_type=item_type,
                    trophy_id=serializer.validated_data.get('trophy_id'),
                    order=serializer.validated_data.get('order')
                )

            if error:
                return Response({'error': error}, status=status.HTTP_400_BAD_REQUEST)

            return Response({
                'success': True,
                'item': ChecklistItemSerializer(item).data
            }, status=status.HTTP_201_CREATED)

        except Exception as e:
            logger.error(f"Item create error: {e}")
            return Response({'error': 'Internal error.'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class ChecklistItemBulkCreateView(APIView):
    """Bulk create items in a section."""
    authentication_classes = [SessionAuthentication, TokenAuthentication]
    permission_classes = [IsAuthenticated]

    @method_decorator(ratelimit(key='user', rate='10/m', method='POST', block=True))
    def post(self, request, section_id):
        """
        POST /api/v1/checklists/sections/<section_id>/items/bulk/

        Request Body:
        {
            "items": [
                {"text": "Item 1", "item_type": "item"},
                {"text": "Sub-header", "item_type": "sub_header"}
            ]
        }

        Success Response (201):
        {
            "success": true,
            "items_created": 3,
            "items": [...],
            "summary": {"total_submitted": 3, "created": 3, "failed": 0}
        }

        Validation Error (400):
        {
            "error": "Validation failed for 2 items",
            "failed_items": [{"index": 0, "text": "...", "error": "..."}],
            "summary": {"total_submitted": 10, "valid": 8, "failed": 2}
        }
        """
        try:
            # Get section
            try:
                section = ChecklistSection.objects.select_related('checklist').get(
                    id=section_id,
                    checklist__is_deleted=False
                )
            except ChecklistSection.DoesNotExist:
                return Response({'error': 'Section not found.'}, status=status.HTTP_404_NOT_FOUND)

            profile = getattr(request.user, 'profile', None)

            # Validate request data
            serializer = ChecklistItemBulkCreateSerializer(data=request.data)
            if not serializer.is_valid():
                return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

            # Bulk create items
            items_data = serializer.validated_data['items']
            created_items, error = ChecklistService.bulk_add_items(
                section=section,
                profile=profile,
                items_data=items_data
            )

            if error:
                # Validation errors
                return Response(error, status=status.HTTP_400_BAD_REQUEST)

            # Success - serialize created items
            return Response({
                'success': True,
                'items_created': len(created_items),
                'items': ChecklistItemSerializer(created_items, many=True).data,
                'summary': {
                    'total_submitted': len(items_data),
                    'created': len(created_items),
                    'failed': 0
                }
            }, status=status.HTTP_201_CREATED)

        except Exception as e:
            logger.error(f"Bulk item create error: {e}")
            return Response({'error': 'Internal error.'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class ChecklistItemDetailView(APIView):
    """Update or delete an item."""
    authentication_classes = [SessionAuthentication, TokenAuthentication]
    permission_classes = [IsAuthenticated]

    def patch(self, request, item_id):
        """PATCH /api/v1/checklists/items/<item_id>/"""
        try:
            try:
                item = ChecklistItem.objects.select_related(
                    'section__checklist'
                ).get(id=item_id, section__checklist__is_deleted=False)
            except ChecklistItem.DoesNotExist:
                return Response({'error': 'Item not found.'}, status=status.HTTP_404_NOT_FOUND)

            profile = getattr(request.user, 'profile', None)
            serializer = ChecklistItemUpdateSerializer(data=request.data)

            if not serializer.is_valid():
                return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

            success, error = ChecklistService.update_item(
                item=item,
                profile=profile,
                text=serializer.validated_data.get('text'),
                item_type=serializer.validated_data.get('item_type'),
                trophy_id=serializer.validated_data.get('trophy_id'),
                order=serializer.validated_data.get('order')
            )

            if not success:
                return Response({'error': error}, status=status.HTTP_400_BAD_REQUEST)

            item.refresh_from_db()
            return Response({
                'success': True,
                'item': ChecklistItemSerializer(item).data
            })

        except Exception as e:
            logger.error(f"Item update error: {e}")
            return Response({'error': 'Internal error.'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    def delete(self, request, item_id):
        """DELETE /api/v1/checklists/items/<item_id>/"""
        try:
            try:
                item = ChecklistItem.objects.select_related(
                    'section__checklist'
                ).get(id=item_id, section__checklist__is_deleted=False)
            except ChecklistItem.DoesNotExist:
                return Response({'error': 'Item not found.'}, status=status.HTTP_404_NOT_FOUND)

            profile = getattr(request.user, 'profile', None)
            success, error = ChecklistService.delete_item(item, profile)

            if not success:
                return Response({'error': error}, status=status.HTTP_400_BAD_REQUEST)

            return Response({'success': True})

        except Exception as e:
            logger.error(f"Item delete error: {e}")
            return Response({'error': 'Internal error.'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class ChecklistItemReorderView(APIView):
    """Reorder items within a section."""
    authentication_classes = [SessionAuthentication, TokenAuthentication]
    permission_classes = [IsAuthenticated]

    def post(self, request, section_id):
        """POST /api/v1/checklists/sections/<section_id>/items/reorder/"""
        try:
            try:
                section = ChecklistSection.objects.get(
                    id=section_id,
                    checklist__is_deleted=False
                )
            except ChecklistSection.DoesNotExist:
                return Response({'error': 'Section not found.'}, status=status.HTTP_404_NOT_FOUND)

            profile = getattr(request.user, 'profile', None)
            serializer = ChecklistReorderSerializer(data=request.data)

            if not serializer.is_valid():
                return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

            success, error = ChecklistService.reorder_items(
                section=section,
                profile=profile,
                item_ids=serializer.validated_data['ids']
            )

            if not success:
                return Response({'error': error}, status=status.HTTP_400_BAD_REQUEST)

            return Response({'success': True})

        except Exception as e:
            logger.error(f"Item reorder error: {e}")
            return Response({'error': 'Internal error.'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


# ---------- User-specific Endpoints ----------

class UserDraftChecklistsView(APIView):
    """Get user's draft checklists."""
    authentication_classes = [SessionAuthentication, TokenAuthentication]
    permission_classes = [IsAuthenticated]

    def get(self, request):
        """GET /api/v1/checklists/my-drafts/"""
        try:
            profile = getattr(request.user, 'profile', None)
            if not profile:
                return Response({'error': 'Profile not found.'}, status=status.HTTP_404_NOT_FOUND)

            drafts = ChecklistService.get_user_drafts(profile)
            serializer = ChecklistSerializer(drafts, many=True, context={'request': request})

            return Response({'checklists': serializer.data})

        except Exception as e:
            logger.error(f"User drafts error: {e}")
            return Response({'error': 'Internal error.'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class UserPublishedChecklistsView(APIView):
    """Get user's published checklists."""
    authentication_classes = [SessionAuthentication, TokenAuthentication]
    permission_classes = [IsAuthenticated]

    def get(self, request):
        """GET /api/v1/checklists/my-published/"""
        try:
            profile = getattr(request.user, 'profile', None)
            if not profile:
                return Response({'error': 'Profile not found.'}, status=status.HTTP_404_NOT_FOUND)

            published = ChecklistService.get_user_published(profile)
            serializer = ChecklistSerializer(published, many=True, context={'request': request})

            return Response({'checklists': serializer.data})

        except Exception as e:
            logger.error(f"User published error: {e}")
            return Response({'error': 'Internal error.'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class UserChecklistProgressView(APIView):
    """Get checklists user has started."""
    authentication_classes = [SessionAuthentication, TokenAuthentication]
    permission_classes = [IsAuthenticated]

    def get(self, request):
        """GET /api/v1/checklists/my-progress/"""
        try:
            profile = getattr(request.user, 'profile', None)
            if not profile:
                return Response({'error': 'Profile not found.'}, status=status.HTTP_404_NOT_FOUND)

            limit = int(request.query_params.get('limit', 10))
            progress_list = ChecklistService.get_user_checklists_in_progress(profile, limit)

            result = []
            for progress in progress_list:
                result.append({
                    'checklist': ChecklistSerializer(progress.checklist, context={'request': request}).data,
                    'items_completed': progress.items_completed,
                    'total_items': progress.total_items,
                    'progress_percentage': progress.progress_percentage,
                    'last_activity': progress.last_activity.isoformat()
                })

            return Response({'in_progress': result})

        except Exception as e:
            logger.error(f"User progress error: {e}")
            return Response({'error': 'Internal error.'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class ChecklistImageUploadView(APIView):
    """Upload/remove checklist thumbnail."""
    authentication_classes = [SessionAuthentication, TokenAuthentication]
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]

    @method_decorator(ratelimit(key='user', rate='10/h', method='POST', block=True))
    def post(self, request, checklist_id):
        """POST /api/v1/checklists/<id>/image/"""
        try:
            checklist = Checklist.objects.get(id=checklist_id, is_deleted=False)
            profile = getattr(request.user, 'profile', None)

            serializer = ChecklistImageUploadSerializer(data=request.data)
            if not serializer.is_valid():
                return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

            success, error = ChecklistService.update_checklist_thumbnail(
                checklist, profile, serializer.validated_data['thumbnail']
            )

            if not success:
                return Response({'error': error}, status=status.HTTP_400_BAD_REQUEST)

            checklist.refresh_from_db()
            return Response({
                'success': True,
                'thumbnail_url': request.build_absolute_uri(checklist.thumbnail.url) if checklist.thumbnail else None
            })

        except Checklist.DoesNotExist:
            return Response({'error': 'Checklist not found.'}, status=status.HTTP_404_NOT_FOUND)
        except Exception as e:
            logger.error(f"Image upload error: {e}")
            return Response({'error': 'Internal error.'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    def delete(self, request, checklist_id):
        """DELETE /api/v1/checklists/<id>/image/ - Remove thumbnail"""
        try:
            checklist = Checklist.objects.get(id=checklist_id, is_deleted=False)
            profile = getattr(request.user, 'profile', None)

            success, error = ChecklistService.remove_checklist_thumbnail(checklist, profile)
            if not success:
                return Response({'error': error}, status=status.HTTP_403_FORBIDDEN)

            return Response({'success': True})

        except Checklist.DoesNotExist:
            return Response({'error': 'Checklist not found.'}, status=status.HTTP_404_NOT_FOUND)


class SectionImageUploadView(APIView):
    """Upload/remove section thumbnail."""
    authentication_classes = [SessionAuthentication, TokenAuthentication]
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]

    @method_decorator(ratelimit(key='user', rate='10/h', method='POST', block=True))
    def post(self, request, section_id):
        """POST /api/v1/checklists/sections/<id>/image/"""
        try:
            section = ChecklistSection.objects.select_related('checklist').get(id=section_id)
            profile = getattr(request.user, 'profile', None)

            serializer = SectionImageUploadSerializer(data=request.data)
            if not serializer.is_valid():
                return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

            success, error = ChecklistService.update_section_thumbnail(
                section, profile, serializer.validated_data['thumbnail']
            )

            if not success:
                return Response({'error': error}, status=status.HTTP_400_BAD_REQUEST)

            section.refresh_from_db()
            return Response({
                'success': True,
                'thumbnail_url': request.build_absolute_uri(section.thumbnail.url) if section.thumbnail else None
            })

        except ChecklistSection.DoesNotExist:
            return Response({'error': 'Section not found.'}, status=status.HTTP_404_NOT_FOUND)
        except Exception as e:
            logger.error(f"Section image upload error: {e}")
            return Response({'error': 'Internal error.'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    def delete(self, request, section_id):
        """DELETE /api/v1/checklists/sections/<id>/image/ - Remove section thumbnail"""
        try:
            section = ChecklistSection.objects.select_related('checklist').get(id=section_id)
            profile = getattr(request.user, 'profile', None)

            success, error = ChecklistService.remove_section_thumbnail(section, profile)
            if not success:
                return Response({'error': error}, status=status.HTTP_403_FORBIDDEN)

            return Response({'success': True})

        except ChecklistSection.DoesNotExist:
            return Response({'error': 'Section not found.'}, status=status.HTTP_404_NOT_FOUND)


class ItemImageCreateView(APIView):
    """Create inline image item (premium only)."""
    authentication_classes = [SessionAuthentication, TokenAuthentication]
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]

    @method_decorator(ratelimit(key='user', rate='20/h', method='POST', block=True))
    def post(self, request, section_id):
        """POST /api/v1/checklists/sections/<id>/items/image/"""
        try:
            section = ChecklistSection.objects.select_related('checklist').get(id=section_id)
            profile = getattr(request.user, 'profile', None)

            serializer = ItemImageCreateSerializer(data=request.data)
            if not serializer.is_valid():
                return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

            item, error = ChecklistService.add_item(
                section=section,
                profile=profile,
                text=serializer.validated_data.get('text', ''),
                item_type='image',
                image=serializer.validated_data['image'],
                order=serializer.validated_data.get('order')
            )

            if error:
                return Response({'error': error}, status=status.HTTP_400_BAD_REQUEST)

            item_serializer = ChecklistItemSerializer(item, context={'request': request})
            return Response(item_serializer.data, status=status.HTTP_201_CREATED)

        except ChecklistSection.DoesNotExist:
            return Response({'error': 'Section not found.'}, status=status.HTTP_404_NOT_FOUND)
        except Exception as e:
            logger.error(f"Inline image create error: {e}")
            return Response({'error': 'Internal error.'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class MarkdownPreviewView(APIView):
    """
    Preview markdown rendering.
    POST /api/v1/markdown/preview/
    """
    authentication_classes = [SessionAuthentication, TokenAuthentication]
    permission_classes = [IsAuthenticated]

    def post(self, request):
        """Render markdown to HTML for preview."""
        text = request.data.get('text', '').strip()

        if not text:
            return Response({'error': 'No text provided.'}, status=status.HTTP_400_BAD_REQUEST)

        if len(text) > 2000:
            return Response({'error': 'Text too long (max 2000 characters).'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            html = ChecklistService.process_markdown(text)
            return Response({'html': html})
        except Exception as e:
            logger.error(f"Markdown preview error: {e}")
            return Response({'error': 'Preview failed.'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class ChecklistGameSelectView(APIView):
    """Set the selected game for a checklist."""
    authentication_classes = [SessionAuthentication, TokenAuthentication]
    permission_classes = [IsAuthenticated]

    def post(self, request, checklist_id):
        """POST /api/v1/checklists/<checklist_id>/select-game/"""
        try:
            try:
                checklist = Checklist.objects.get(id=checklist_id, is_deleted=False)
            except Checklist.DoesNotExist:
                return Response({'error': 'Checklist not found.'}, status=status.HTTP_404_NOT_FOUND)

            profile = getattr(request.user, 'profile', None)

            serializer = GameSelectionSerializer(data=request.data)
            if not serializer.is_valid():
                return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

            success, error = ChecklistService.set_checklist_game(
                checklist=checklist,
                game_id=serializer.validated_data['game_id'],
                profile=profile
            )

            if not success:
                return Response({'error': error}, status=status.HTTP_400_BAD_REQUEST)

            return Response({'success': True, 'game_id': serializer.validated_data['game_id']})

        except Exception as e:
            logger.error(f"Game selection error: {e}")
            return Response({'error': 'Internal error.'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class ChecklistAvailableTrophiesView(APIView):
    """Get available trophies for a checklist."""
    authentication_classes = [SessionAuthentication, TokenAuthentication]
    permission_classes = [IsAuthenticated]

    def get(self, request, checklist_id):
        """GET /api/v1/checklists/<checklist_id>/available-trophies/"""
        try:
            try:
                checklist = Checklist.objects.get(id=checklist_id, is_deleted=False)
            except Checklist.DoesNotExist:
                return Response({'error': 'Checklist not found.'}, status=status.HTTP_404_NOT_FOUND)

            profile = getattr(request.user, 'profile', None)

            # Only author can view available trophies
            if checklist.profile != profile:
                return Response({'error': 'Permission denied.'}, status=status.HTTP_403_FORBIDDEN)

            trophies = ChecklistService.get_available_trophies_for_checklist(checklist)

            # Get trophy group names for the selected game
            from trophies.models import TrophyGroup
            trophy_groups = {}
            if checklist.selected_game:
                groups = TrophyGroup.objects.filter(game=checklist.selected_game)
                trophy_groups = {g.trophy_group_id: g.trophy_group_name for g in groups}

            # Build trophy data with group names
            trophy_data = []
            for trophy in trophies:
                data = {
                    'id': trophy.id,
                    'trophy_name': trophy.trophy_name,
                    'trophy_detail': trophy.trophy_detail,
                    'trophy_icon_url': trophy.trophy_icon_url,
                    'trophy_type': trophy.trophy_type,
                    'trophy_rarity': trophy.trophy_rarity,
                    'trophy_earn_rate': trophy.trophy_earn_rate,
                    'trophy_group_id': trophy.trophy_group_id,
                    'trophy_group_name': trophy_groups.get(trophy.trophy_group_id, ''),
                    'is_base_game': trophy.is_base_game,
                    'is_used': trophy.is_used,
                }
                trophy_data.append(data)

            # Get unique trophy groups for filter dropdown
            unique_groups = []
            seen_group_ids = set()
            for trophy in trophies:
                if trophy.trophy_group_id not in seen_group_ids:
                    seen_group_ids.add(trophy.trophy_group_id)
                    unique_groups.append({
                        'trophy_group_id': trophy.trophy_group_id,
                        'trophy_group_name': trophy_groups.get(trophy.trophy_group_id, ''),
                        'is_base_game': trophy.is_base_game
                    })

            return Response({
                'trophies': trophy_data,
                'trophy_groups': unique_groups,
                'selected_game_id': checklist.selected_game.id if checklist.selected_game else None
            })

        except Exception as e:
            logger.error(f"Available trophies error: {e}")
            return Response({'error': 'Internal error.'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
