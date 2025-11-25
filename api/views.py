from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from .serializers import RegisterSerializer, ProfileSerializer
from trophies.models import Profile
from django.utils import timezone
from datetime import timedelta
from trophies.psn_manager import PSNManager
import logging

logger = logging.getLogger('psn_api')

class RegisterView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = RegisterSerializer(data=request.data)
        if serializer.is_valid():
            discord_id = serializer.validated_data['discord_id']
            psn_username = serializer.validated_data['psn_username'].lower()

            profile, created = Profile.objects.get_or_create(psn_username=psn_username)
            if profile.discord_id and profile.discord_id != discord_id:
                return Response({'success': False, 'message': 'PSN already linked to a different Discord account.'})
            
            profile.link_discord(discord_id)
            if created:
                PSNManager.initial_sync(profile)
            else:
                if timezone.now() - timedelta(hours=1) > profile.last_synced:
                    PSNManager.profile_refresh(profile)
            return Response({'success': True, 'message': 'Linked successfully.'})
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

class TrophiesView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        discord_id = request.query_params.get('discord_id')
        print(f"Profile request for {discord_id}")
        if not discord_id:
            return Response({'error': 'discord_id required.'}, status=status.HTTP_400_BAD_REQUEST)
        
        try:
            profile = Profile.objects.get(discord_id=int(discord_id.strip()))
            serializer = ProfileSerializer(profile)
            return Response({'linked': True, 'profile': serializer.data})
        except Profile.DoesNotExist:
            return Response({'linked': False, 'message': 'No linked PSN profile found.'})
        except Exception as e:
            logger.error(f"Trophies fetch error: {e}")
            return Response({'error': 'Internal error.'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)