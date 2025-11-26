from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from .serializers import GenerateCodeSerializer, VerifySerializer, ProfileSerializer
from trophies.models import Profile
from django.utils import timezone
from datetime import timedelta
from trophies.psn_manager import PSNManager
import time
import math
import logging

logger = logging.getLogger('psn_api')

class GenerateCodeView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = GenerateCodeSerializer(data=request.data)
        if serializer.is_valid():
            psn_username = serializer.validated_data['psn_username'].lower()
            if not psn_username:
                return Response({'error': 'psn_username required.'}, status=status.HTTP_400_BAD_REQUEST)
            
            profile, created = Profile.objects.get_or_create(psn_username=psn_username)
            profile.generate_verification_code()
            if created:
                PSNManager.initial_sync(profile)
            else:
                if profile.get_time_since_last_sync() > timedelta(hours=1):
                    PSNManager.profile_refresh(profile)

            return Response({
                "success": True,
                "code": profile.verification_code,
                "message": f"Add '{profile.verification_code}' to your PSN 'About Me' section and run the /verify command!"
            })
        else:
            logger.error(f"Serializer errors: {serializer.errors}")
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
    
class VerifyView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = VerifySerializer(data=request.data)
        if serializer.is_valid():
            discord_id = serializer.validated_data['discord_id']
            psn_username = serializer.validated_data['psn_username'].lower()
            if not all([discord_id, psn_username]):
                return Response({'error': 'discord_id and psn_username required.'}, status=status.HTTP_400_BAD_REQUEST)
            
            try:
                profile = Profile.objects.get(psn_username=psn_username)
                
                start_time = timezone.now()
                timeout_seconds = 30
                poll_interval_seconds = 1

                if profile.get_time_since_last_sync() > timedelta(hours=1):
                    PSNManager.profile_refresh(profile)
                else:
                    PSNManager.sync_profile_data(profile)

                while (timezone.now() - start_time).total_seconds() < timeout_seconds:
                    profile.refresh_from_db()
                    if profile.last_synced > start_time:
                        logger.info(f"Sync completed for profile {profile.id} after polling.")
                        break
                    time.sleep(poll_interval_seconds)
                
                if profile.last_synced <= start_time:
                    logger.warning(f"Sync timeout for profile {profile.id} after {timeout_seconds}s.")
                    return Response({'success': False, 'message': 'Sync timed out. Try again later.'}, status=status.HTTP_408_REQUEST_TIMEOUT)

                if profile.verify_code(profile.about_me):
                    profile.link_discord(discord_id)
                    return Response({'success': True, 'message': 'Verified and linked successfully!'})
                else:
                    return Response({'success': False, 'message': 'Verification failed. Check code and try again.'})
            except Profile.DoesNotExist:
                return Response({'error': 'Profile not found.'}, status=status.HTTP_404_NOT_FOUND)
        else:
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

class CheckLinkedView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        discord_id = request.query_params.get('discord_id')
        if not discord_id:
            return Response({'error': 'discord_id required.'}, status=status.HTTP_400_BAD_REQUEST)
        
        try:
            profile = Profile.objects.get(discord_id=discord_id)
            return Response({'linked': True, 'psn_username': profile.display_psn_username})
        except Profile.DoesNotExist:
            return Response({'linked': False, 'message': 'No linked profile found.'})
        except Exception as e:
            logger.error(f"Check linked error: {e}")
            return Response({'error': 'Internal error.'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

class UnlinkView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        discord_id = request.data.get('discord_id')
        if not discord_id:
            return Response({'error': 'discord_id required.'}, status=status.HTTP_400_BAD_REQUEST)
        
        try:
            profile = Profile.objects.get(discord_id=discord_id)
            profile.unlink_discord()
            return Response({'success': True, 'message': 'Unlinked successfully.'})
        except Profile.DoesNotExist:
            return Response({'success': False, 'message': 'No linked profile found.'})
        except Exception as e:
            logger.error(f"Unlink error: {e}")
            return Response({'error': 'Internal error.'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

class RefreshView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        discord_id = request.data.get('discord_id')
        if not discord_id:
            return Response({'error': 'discord_id required.'}, status=status.HTTP_400_BAD_REQUEST)
        
        try:
            profile = Profile.objects.get(discord_id=discord_id)
            time_since_last_sync = profile.get_time_since_last_sync()
            if time_since_last_sync > timedelta(hours=1):
                PSNManager.profile_refresh(profile)
                return Response({'linked': True, 'success': True, 'psn_username': profile.display_psn_username})
            else:
                total_seconds = (timedelta(hours=1) - time_since_last_sync).total_seconds()
                minutes = math.ceil(total_seconds / 60)
                return Response({'linked': True, 'succes': False, 'message': f"Too many profile refresh requests! Please try again in: {int(minutes)} minutes"})
        except Profile.DoesNotExist:
            return Response({'linked': False, 'message': 'No linked profile found.'})
        except Exception as e:
            logger.error(f"Refresh error: {e}")
            return Response({'error': 'Internal error.'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

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