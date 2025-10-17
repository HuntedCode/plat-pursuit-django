from django.core.management.base import BaseCommand
from django.utils import timezone
from dotenv import load_dotenv
import os
import requests  # For potential HTTP error handling
from psnawp_api import PSNAWP  # Core import for v3.0.0
from psnawp_api.models.trophies.trophy_constants import PlatformType
import json

load_dotenv()

class Command(BaseCommand):
    help = 'Test PSN API v3.0.0 connection and fetch sample trophy data'

    def add_arguments(self, parser):
        parser.add_argument('psn_username', type=str, nargs='?', default='abu_abu', help='PSN Online ID to test (default: abu_abu)')

    # Endpoint Test Functions
    def user_get_presence(self, user):
        ''' Gets the presence of the user.

            :returns: Dict containing current online status info.

            :raises PSNAWPForbiddenError: When the user's profile does not have proper perms. 
                Perms requirements:
                'Who can see your online status and what you're currently playing' -> Anyone
        '''
        presence = user.get_presence()
        print(json.dumps(presence, indent=4))
        return

    def user_profile(self, user):
        ''' Gets the profile of the user.

            :returns: Dict containing profile information

            :issue: aboutMe seems to return blank, regardless of the About section of the profile.
        '''
        profile = user.profile()
        print(json.dumps(profile, indent=4))
        return
    
    def user_profile_legacy(self, user):
        ''' Gets the legacy profile of the user. Useful for PS3/PS4 presence and About Me.

            :returns: Dict containing legacy profile information
        '''
        profile = user.get_profile_legacy()
        print(json.dumps(profile, indent=4))
        return
    
    def user_title_stats(self, user):
        ''' Gets a list of titles and returns an iterator of TitleStats objects.

            :params: user.title_stats() takes the following optional params:
                - limit = limit the number of objects returned
                - page_size (default: 200) = the number of items to recieve per API request
                - offset = specifies offset for paginator
        
            :returns: Iterator of TitleStats containing an entry for each game the user has played.

            :warning: Only works for PS4 games or higher.
        '''
        title_stats = user.title_stats()
        stat = next(title_stats)
        for attr, value in vars(stat).items():
            print(f"{attr}: {value}")
        return
    
    def user_trophy_summary(self, user):
        ''' Gets the general trophy summary for the user as a TrophySummary obj.

            :returns: TrophySummary object containing basic trophy info (including a TrophySet obj with trophy totals)
        '''
        summary = user.trophy_summary()
        for attr, value in vars(summary).items():
            print(f"{attr}: {value}")
    
    def user_trophy_titles(self, user):
        ''' Get full list of games user has played and TrophyTitle objects containing trophy details for each.

            :returns: An iterator of TrophyTitle objects

            :params: user.trophy_titles() takes the following optional params:
                - limit = limit the number of objects returned
                - page_size (default: 50) = the number of items to recieve per API request
                - offset = specifies offset for paginator

            :raises PSNAWPForbiddenError: When the user's profile does not have proper perms.
        '''
        trophy_titles = user.trophy_titles()
        trophy_title = next(trophy_titles)
        for attr, value in vars(trophy_title).items():
            print(f"{attr}: {value}")

    def user_trophies(self, user, np_comm_id, platform):
        ''' Gets list of all trophies for the specified game (using np_communication_id).
        
            :returns: An iterator of Trophy objects

            :params: user.trophies() takes the following params:
                * np_communication_id - ID for the game to get trophies for
                * platform (PlatformType) - Platform to target
                * include_progress (bool) - Flag to include user progress of trophies (REQUIRES ADDITIONAL API CALLS!)
                * trophy_group_id (default = 'default') - DLC signifier. Use 'default' for base game only, 'all' for all or corresponding number (ie. 001) for specific DLC
                - limit = limit the number of objects returned
                - page_size (default: 200) = the number of items to recieve per API request
                - offset = specifies offset for paginator
            
            :warning: If include_progress = True - additional API call will be made, doubling the API rate limit footprint

            :raises PSNAWPNotFoundError: If user does not have trophies for specified game
            :raises PSNAWPForbiddenError: If user's profile is private
        '''
        trophies = user.trophies(np_comm_id, platform, False, 'all')
        trophy = next(trophies)
        for attr, value in vars(trophy).items():
            print(f"{attr}: {value}")

    def user_trophies_include_progress(self, user, np_comm_id, platform):
        ''' Gets list of all trophies for the specified game (using np_communication_id).
        
            :returns: An iterator of TrophyWithProgress objects

            :params: user.trophies() takes the following params:
                * np_communication_id - ID for the game to get trophies for
                * platform (PlatformType) - Platform to target
                * include_progress (bool) - Flag to include user progress of trophies (REQUIRES ADDITIONAL API CALLS!)
                * trophy_group_id (default = 'default') - DLC signifier. Use 'default' for base game only, 'all' for all or corresponding number (ie. 001) for specific DLC
                - limit = limit the number of objects returned
                - page_size (default: 200) = the number of items to recieve per API request
                - offset = specifies offset for paginator
            
            :warning: If include_progress = True - additional API call will be made, doubling the API rate limit footprint

            :raises PSNAWPNotFoundError: If user does not have trophies for specified game
            :raises PSNAWPForbiddenError: If user's profile is private
        '''
        trophies = user.trophies(np_comm_id, platform, True, 'all')
        trophy = next(trophies)
        for attr, value in vars(trophy).items():
            print(f"{attr}: {value}")


    def handle(self, *args, **options):
        token = os.getenv('NPSSO_TOKEN')
        if not token or len(token) != 64:
            self.stdout.write(self.style.ERROR('Set NPSSO_TOKEN in .env (must be 64-char NPSSO cookie from playstation.com)'))
            return

        try:
            # v3.0.0 Init: PSNAWP handles auth/refresh internally
            psnawp = PSNAWP(token)
            username = options['psn_username']

            # User/Profile (maps to Profile model: psn_username, avatar_url)
            user = psnawp.user(online_id=username)
            self.stdout.write(self.style.SUCCESS(f"Profile: {user.online_id} (Account ID: {user.account_id})"))

            # Displays Key/Value combos for each endpoint. Comment out what you don't want to use.
            # self.user_get_presence(user) # user.get_presence()
            # self.user_profile(user)
            # self.user_profile_legacy(user)
            # self.user_title_stats(user)
            # self.user_trophy_summary(user)
            # self.user_trophy_titles(user)
            np_comm_id = 'NPWR22392_00'
            platform = PlatformType.PS5
            # self.user_trophies(user, np_comm_id, platform)
            # self.user_trophies_include_progress(user, np_comm_id, platform)


            self.stdout.write(self.style.SUCCESS('v3.0.0 API test successful! Data ready for model syncing.'))

        except ValueError as e:
            # Common for auth/token issues in v3.0.0
            self.stdout.write(self.style.ERROR(f'Auth/Token Error: {str(e)} - Regenerate NPSSO token via browser DevTools.'))
        except requests.exceptions.HTTPError as e:
            # Rate limits or 4xx/5xx from API
            if e.response.status_code == 429:
                self.stdout.write(self.style.WARNING('Rate Limit Hit (429) - Wait 15 min; library auto-retries on next call.'))
            else:
                self.stdout.write(self.style.ERROR(f'HTTP Error: {str(e)} - Check token or PSN username.'))
        except Exception as e:
            # Catch-all for other issues (e.g., network, invalid data)
            self.stdout.write(self.style.ERROR(f'Unexpected Error: {str(e)} - Verify library version and token.'))
            self.stdout.write(self.style.WARNING('Run "pip show psnawp" to confirm v3.0.0.'))