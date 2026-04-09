"""
Trophies views package.

Re-exports all view classes for backward compatibility with existing URL configuration.
"""

from .game_views import GamesListView, GameDetailView, GuideListView, FlaggedGamesView
from .trophy_views import TrophiesListView, TrophyCaseView, ToggleSelectionView
from .profile_views import ProfilesListView, ProfileDetailView, LinkPSNView, ProfileVerifyView
from .badge_views import BadgeListView, BadgeDetailView, BadgeLeaderboardsView, OverallBadgeLeaderboardsView, MilestoneListView
# Checklist views removed during roadmap migration (DB tables retained)
from .sync_views import ProfileSyncStatusView, TriggerSyncView, SearchSyncProfileView, AddSyncStatusView
from .admin_views import (
    TokenMonitoringView, BadgeCreationView, CommentModerationView, ModerationActionView,
    ModerationLogView, ReviewModerationView, ReviewModerationActionView,
    ReviewModerationLogView, GameFamilyManagementView,
)
from .misc_views import SearchView
from .list_views import BrowseListsView, GameListDetailView, GameListEditView, GameListCreateView, MyListsView
from .challenge_views import (
    ChallengeHubView, MyChallengesView, AZChallengeCreateView,
    AZChallengeSetupView, AZChallengeDetailView, AZChallengeEditView,
    CalendarChallengeCreateView, CalendarChallengeDetailView,
    GenreChallengeCreateView, GenreChallengeSetupView,
    GenreChallengeDetailView, GenreChallengeEditView,
)
from .review_hub_views import ReviewHubLandingView, RateMyGamesView, ReviewHubDetailView
from .dashboard_views import DashboardView
from .title_views import MyTitlesView
from .platinum_grid_views import PlatinumGridView
from .roadmap_views import RoadmapEditorView
from .shareables_views import MyShareablesView, MyPlatinumSharesView, MyChallengeSharesView, MyProfileCardView
from .stats_views import MyStatsView
from .company_views import CompanyListView, CompanyDetailView
from .genre_views import GenreThemeListView, GenreDetailView, ThemeDetailView

__all__ = [
    # Game views
    'GamesListView', 'GameDetailView', 'GuideListView', 'FlaggedGamesView',
    # Trophy views
    'TrophiesListView', 'TrophyCaseView', 'ToggleSelectionView',
    # Profile views
    'ProfilesListView', 'ProfileDetailView', 'LinkPSNView', 'ProfileVerifyView',
    # Badge views
    'BadgeListView', 'BadgeDetailView', 'BadgeLeaderboardsView', 'OverallBadgeLeaderboardsView', 'MilestoneListView',
    # Checklist views (removed, DB tables retained)
    # Sync views
    'ProfileSyncStatusView', 'TriggerSyncView', 'SearchSyncProfileView', 'AddSyncStatusView',
    # Admin views
    'TokenMonitoringView', 'BadgeCreationView', 'CommentModerationView', 'ModerationActionView', 'ModerationLogView',
    'ReviewModerationView', 'ReviewModerationActionView', 'ReviewModerationLogView', 'GameFamilyManagementView',
    # Misc views
    'SearchView',
    # List views
    'BrowseListsView', 'GameListDetailView', 'GameListEditView', 'GameListCreateView', 'MyListsView',
    # Challenge views
    'ChallengeHubView', 'MyChallengesView', 'AZChallengeCreateView',
    'AZChallengeSetupView', 'AZChallengeDetailView', 'AZChallengeEditView',
    'CalendarChallengeCreateView', 'CalendarChallengeDetailView',
    'GenreChallengeCreateView', 'GenreChallengeSetupView',
    'GenreChallengeDetailView', 'GenreChallengeEditView',
    # Review Hub views
    'ReviewHubLandingView', 'RateMyGamesView', 'ReviewHubDetailView',
    # Dashboard views
    'DashboardView',
    # Title views
    'MyTitlesView',
    # Platinum Grid views
    'PlatinumGridView',
    # Roadmap views
    'RoadmapEditorView',
    # Shareables views
    'MyShareablesView', 'MyPlatinumSharesView', 'MyChallengeSharesView', 'MyProfileCardView',
    # Stats views
    'MyStatsView',
    # Company views
    'CompanyListView', 'CompanyDetailView',
    # Genre/Theme views
    'GenreThemeListView', 'GenreDetailView', 'ThemeDetailView',
]
