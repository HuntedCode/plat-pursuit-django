"""
Game Family API views.

Staff-only endpoints for managing GameFamily records and reviewing proposals.
"""
import logging

from collections import defaultdict

from django.db import transaction
from django.db.models import Q
from django.db.models.functions import Lower
from django.utils import timezone
from rest_framework import status
from rest_framework.authentication import SessionAuthentication, TokenAuthentication
from rest_framework.permissions import IsAuthenticated, IsAdminUser
from rest_framework.response import Response
from rest_framework.views import APIView

from trophies.models import Concept, GameFamily, GameFamilyProposal, Trophy

logger = logging.getLogger('psn_api')


def _bulk_serialize_concepts(concepts):
    """Serialize multiple concepts with a single bulk trophy query."""
    concept_ids = [c.id for c in concepts]

    # Single query for all trophy data across all concepts
    trophy_type_counts = defaultdict(lambda: defaultdict(int))
    trophy_icons = {}  # concept_id -> best icon (platinum preferred)
    trophy_all_icons = defaultdict(list)  # concept_id -> [(type, url)]

    for concept_id, trophy_type, icon_url in (
        Trophy.objects.filter(game__concept_id__in=concept_ids)
        .values_list('game__concept_id', 'trophy_type', 'trophy_icon_url')
    ):
        trophy_type_counts[concept_id][trophy_type] += 1
        if icon_url:
            trophy_all_icons[concept_id].append((trophy_type, icon_url))

    # Pick best icon per concept: platinum first, then any
    for concept_id, icons in trophy_all_icons.items():
        plat = next((url for t, url in icons if t == 'platinum'), None)
        trophy_icons[concept_id] = plat or icons[0][1]

    results = []
    for concept in concepts:
        games = concept.games.all()
        platforms = set()
        regions = set()
        for game in games:
            platforms.update(game.title_platform or [])
            for r in (game.region or []):
                regions.add(r)

        results.append({
            'id': concept.id,
            'concept_id': concept.concept_id,
            'unified_title': concept.unified_title,
            'is_stub': concept.concept_id.startswith('PP_'),
            'platforms': sorted(platforms),
            'regions': sorted(regions),
            'trophy_counts': dict(trophy_type_counts.get(concept.id, {})),
            'trophy_icon': trophy_icons.get(concept.id, ''),
            'game_count': len(games),
            'family_id': concept.family_id,
        })
    return results


def _serialize_family(family):
    """Serialize a GameFamily with its member concepts."""
    concepts = list(family.concepts.prefetch_related('games').all())
    return {
        'id': family.id,
        'canonical_name': family.canonical_name,
        'admin_notes': family.admin_notes,
        'is_verified': family.is_verified,
        'created_at': family.created_at.isoformat(),
        'updated_at': family.updated_at.isoformat(),
        'concepts': _bulk_serialize_concepts(concepts),
        'concept_count': len(concepts),
    }


def _serialize_proposal(proposal):
    """Serialize a GameFamilyProposal with its member concepts."""
    concepts = list(proposal.concepts.prefetch_related('games').all())
    return {
        'id': proposal.id,
        'proposed_name': proposal.proposed_name,
        'confidence': proposal.confidence,
        'match_reason': proposal.match_reason,
        'match_signals': proposal.match_signals,
        'status': proposal.status,
        'created_at': proposal.created_at.isoformat(),
        'concepts': _bulk_serialize_concepts(concepts),
    }


class ProposalApproveView(APIView):
    """Approve a pending proposal, creating a GameFamily."""
    authentication_classes = [SessionAuthentication, TokenAuthentication]
    permission_classes = [IsAuthenticated, IsAdminUser]
    throttle_classes = []

    def post(self, request, proposal_id):
        try:
            proposal = GameFamilyProposal.objects.get(id=proposal_id, status='pending')
        except GameFamilyProposal.DoesNotExist:
            return Response({'error': 'Proposal not found or already reviewed.'},
                            status=status.HTTP_404_NOT_FOUND)

        canonical_name = request.data.get('canonical_name', proposal.proposed_name)

        with transaction.atomic():
            # Lock concepts to prevent concurrent approvals from assigning to different families
            concepts = list(proposal.concepts.select_for_update().all())
            already_in_family = [c for c in concepts if c.family_id is not None]

            if already_in_family:
                # Add remaining concepts to existing family
                existing_family = already_in_family[0].family
                for c in concepts:
                    if c.family_id is None:
                        c.family = existing_family
                        c.save(update_fields=['family'])
                family = existing_family
            else:
                family = GameFamily.objects.create(
                    canonical_name=canonical_name,
                    is_verified=True,
                )
                for c in concepts:
                    c.family = family
                    c.save(update_fields=['family'])

            proposal.status = 'approved'
            proposal.reviewed_by = request.user
            proposal.reviewed_at = timezone.now()
            proposal.resulting_family = family
            proposal.save(update_fields=['status', 'reviewed_by', 'reviewed_at', 'resulting_family'])

        return Response({
            'message': 'Proposal approved.',
            'family': _serialize_family(family),
        })


class ProposalRejectView(APIView):
    """Reject a pending proposal."""
    authentication_classes = [SessionAuthentication, TokenAuthentication]
    permission_classes = [IsAuthenticated, IsAdminUser]
    throttle_classes = []

    def post(self, request, proposal_id):
        try:
            proposal = GameFamilyProposal.objects.get(id=proposal_id, status='pending')
        except GameFamilyProposal.DoesNotExist:
            return Response({'error': 'Proposal not found or already reviewed.'},
                            status=status.HTTP_404_NOT_FOUND)

        proposal.status = 'rejected'
        proposal.reviewed_by = request.user
        proposal.reviewed_at = timezone.now()
        proposal.save(update_fields=['status', 'reviewed_by', 'reviewed_at'])

        return Response({'message': 'Proposal rejected.'})


class GameFamilyCreateView(APIView):
    """Manually create a GameFamily with selected concepts."""
    authentication_classes = [SessionAuthentication, TokenAuthentication]
    permission_classes = [IsAuthenticated, IsAdminUser]
    throttle_classes = []

    def post(self, request):
        canonical_name = request.data.get('canonical_name', '').strip()
        concept_ids = request.data.get('concept_ids', [])

        if not canonical_name:
            return Response({'error': 'canonical_name is required.'},
                            status=status.HTTP_400_BAD_REQUEST)
        if len(concept_ids) < 2:
            return Response({'error': 'At least 2 concepts required.'},
                            status=status.HTTP_400_BAD_REQUEST)

        concepts = list(Concept.objects.filter(id__in=concept_ids))
        if len(concepts) != len(concept_ids):
            return Response({'error': 'One or more concept IDs not found.'},
                            status=status.HTTP_400_BAD_REQUEST)

        # Check if any concept is already in a family
        in_family = [c for c in concepts if c.family_id is not None]
        if in_family:
            names = ', '.join(c.unified_title for c in in_family)
            return Response(
                {'error': f'Concepts already in a family: {names}'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        family = GameFamily.objects.create(
            canonical_name=canonical_name,
            admin_notes=request.data.get('admin_notes', ''),
            is_verified=True,
        )
        for c in concepts:
            c.family = family
            c.save(update_fields=['family'])

        return Response({
            'message': 'GameFamily created.',
            'family': _serialize_family(family),
        }, status=status.HTTP_201_CREATED)


class GameFamilyUpdateView(APIView):
    """Update a GameFamily's canonical_name or admin_notes."""
    authentication_classes = [SessionAuthentication, TokenAuthentication]
    permission_classes = [IsAuthenticated, IsAdminUser]
    throttle_classes = []

    def patch(self, request, family_id):
        try:
            family = GameFamily.objects.get(id=family_id)
        except GameFamily.DoesNotExist:
            return Response({'error': 'Family not found.'}, status=status.HTTP_404_NOT_FOUND)

        update_fields = []
        if 'canonical_name' in request.data:
            family.canonical_name = request.data['canonical_name'].strip()
            update_fields.append('canonical_name')
        if 'admin_notes' in request.data:
            family.admin_notes = request.data['admin_notes']
            update_fields.append('admin_notes')
        if 'is_verified' in request.data:
            family.is_verified = bool(request.data['is_verified'])
            update_fields.append('is_verified')

        if update_fields:
            family.save(update_fields=update_fields)

        return Response({
            'message': 'Family updated.',
            'family': _serialize_family(family),
        })


class GameFamilyDeleteView(APIView):
    """Delete a GameFamily (unlinks concepts, does not delete them)."""
    authentication_classes = [SessionAuthentication, TokenAuthentication]
    permission_classes = [IsAuthenticated, IsAdminUser]
    throttle_classes = []

    def delete(self, request, family_id):
        try:
            family = GameFamily.objects.get(id=family_id)
        except GameFamily.DoesNotExist:
            return Response({'error': 'Family not found.'}, status=status.HTTP_404_NOT_FOUND)

        # Unlink all concepts
        family.concepts.update(family=None)
        family.delete()

        return Response({'message': 'Family deleted.'})


class GameFamilyAddConceptView(APIView):
    """Add a concept to an existing GameFamily."""
    authentication_classes = [SessionAuthentication, TokenAuthentication]
    permission_classes = [IsAuthenticated, IsAdminUser]
    throttle_classes = []

    def post(self, request, family_id):
        try:
            family = GameFamily.objects.get(id=family_id)
        except GameFamily.DoesNotExist:
            return Response({'error': 'Family not found.'}, status=status.HTTP_404_NOT_FOUND)

        concept_id = request.data.get('concept_id')
        try:
            concept = Concept.objects.get(id=concept_id)
        except Concept.DoesNotExist:
            return Response({'error': 'Concept not found.'}, status=status.HTTP_404_NOT_FOUND)

        if concept.family_id is not None:
            return Response(
                {'error': f'Concept already in family: {concept.family.canonical_name}'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        concept.family = family
        concept.save(update_fields=['family'])

        return Response({
            'message': f'Added "{concept.unified_title}" to family.',
            'family': _serialize_family(family),
        })


class GameFamilyRemoveConceptView(APIView):
    """Remove a concept from a GameFamily."""
    authentication_classes = [SessionAuthentication, TokenAuthentication]
    permission_classes = [IsAuthenticated, IsAdminUser]
    throttle_classes = []

    def post(self, request, family_id):
        try:
            family = GameFamily.objects.get(id=family_id)
        except GameFamily.DoesNotExist:
            return Response({'error': 'Family not found.'}, status=status.HTTP_404_NOT_FOUND)

        concept_id = request.data.get('concept_id')
        try:
            concept = Concept.objects.get(id=concept_id, family=family)
        except Concept.DoesNotExist:
            return Response({'error': 'Concept not found in this family.'},
                            status=status.HTTP_404_NOT_FOUND)

        concept.family = None
        concept.save(update_fields=['family'])

        # Clean up families that are empty or have only 1 member (not a valid family)
        remaining = family.concepts.count()
        if remaining < 2:
            family.delete()
            return Response({'message': 'Concept removed. Family deleted (insufficient members).'})

        return Response({
            'message': f'Removed "{concept.unified_title}" from family.',
            'family': _serialize_family(family),
        })


class ConceptSearchView(APIView):
    """Search concepts by name for manual family linking."""
    authentication_classes = [SessionAuthentication, TokenAuthentication]
    permission_classes = [IsAuthenticated, IsAdminUser]
    throttle_classes = []

    def get(self, request):
        query = request.GET.get('q', '').strip()
        if len(query) < 2:
            return Response({'results': []})

        concepts = list(
            Concept.objects.filter(
                Q(unified_title__icontains=query) |
                Q(games__title_name__icontains=query)
            ).prefetch_related('games').distinct().order_by(Lower('unified_title'))[:20]
        )

        return Response({
            'results': _bulk_serialize_concepts(concepts),
        })
