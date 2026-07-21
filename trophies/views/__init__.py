"""
Trophies views package.

Re-exports all view classes for backward compatibility with existing URL configuration.
"""

from .game_views import GamesListView, GameDetailView, GuideListView, FlaggedGamesView, RecentlyAddedView, RandomGameView
from .trophy_views import TrophiesListView, TrophyCaseView, ToggleSelectionView
from .profile_views import ProfilesListView, ProfileDetailView, LinkPSNView, ProfileVerifyView, ProfileEditorView
from .badge_views import BadgeListView, BadgeDetailView, BadgeQuickPeekView, BadgeProgressPeekView, BadgeLeaderboardsView, OverallBadgeLeaderboardsView, MilestoneListView
# Checklist views removed during roadmap migration (DB tables retained)
from .sync_views import ProfileSyncStatusView, TriggerSyncView, SearchSyncProfileView, AddSyncStatusView, ProfileSuggestView, SiteSuggestView
from .admin_views import (
    TokenMonitoringView, BadgeCreationView, CommentModerationView, ModerationActionView,
    ModerationLogView, ReviewModerationView, ReviewModerationActionView,
    ReviewModerationLogView, GameFamilyManagementView,
    LegacyChecklistListView, LegacyChecklistDetailView,
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
from .review_hub_views import ReviewHubLandingView, RateMyGamesView, ReviewHubDetailView, ReviewsArchivedView
from .dashboard_views import DashboardView
from .title_views import MyTitlesView
from .platinum_grid_views import PlatinumGridView
from .roadmap_views import RoadmapDetailView, RoadmapEditorView
from .shareables_views import MyShareablesView, MyPlatinumSharesView, MyChallengeSharesView, MyProfileCardView
from .career_views import CareerView, ContractsResultsView, ContractModalView, ContractModalPreviewView
from .collection_views import CollectionView, CollectionBadgeModalView
from .stats_views import MyStatsView
from .company_views import CompanyListView, CompanyDetailView
from .franchise_views import FranchiseListView, FranchiseDetailView
from .genre_views import GenreThemeListView, GenreDetailView, ThemeDetailView
from .engine_views import EngineListView, EngineDetailView

__all__ = [
    # Game views
    'GamesListView', 'GameDetailView', 'GuideListView', 'FlaggedGamesView', 'RecentlyAddedView', 'RandomGameView',
    # Trophy views
    'TrophiesListView', 'TrophyCaseView', 'ToggleSelectionView',
    # Profile views
    'ProfilesListView', 'ProfileDetailView', 'LinkPSNView', 'ProfileVerifyView', 'ProfileEditorView',
    # Badge views
    'BadgeListView', 'BadgeDetailView', 'BadgeQuickPeekView', 'BadgeProgressPeekView', 'BadgeLeaderboardsView', 'OverallBadgeLeaderboardsView', 'MilestoneListView',
    # Checklist views (removed, DB tables retained)
    # Sync views
    'ProfileSyncStatusView', 'TriggerSyncView', 'SearchSyncProfileView', 'AddSyncStatusView', 'ProfileSuggestView', 'SiteSuggestView',
    # Admin views
    'TokenMonitoringView', 'BadgeCreationView', 'CommentModerationView', 'ModerationActionView', 'ModerationLogView',
    'ReviewModerationView', 'ReviewModerationActionView', 'ReviewModerationLogView', 'GameFamilyManagementView',
    'LegacyChecklistListView', 'LegacyChecklistDetailView',
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
    'ReviewHubLandingView', 'RateMyGamesView', 'ReviewHubDetailView', 'ReviewsArchivedView',
    # Dashboard views
    'DashboardView',
    # Title views
    'MyTitlesView',
    # Platinum Grid views
    'PlatinumGridView',
    # Roadmap views
    'RoadmapDetailView', 'RoadmapEditorView',
    # Shareables views
    'MyShareablesView', 'MyPlatinumSharesView', 'MyChallengeSharesView', 'MyProfileCardView',
    # The Lab view
    'CareerView', 'ContractsResultsView', 'ContractModalView', 'ContractModalPreviewView',
    # Collection album view
    'CollectionView',
    'CollectionBadgeModalView',
    # Stats views
    'MyStatsView',
    # Company views
    'CompanyListView', 'CompanyDetailView',
    # Franchise views
    'FranchiseListView', 'FranchiseDetailView',
    # Genre/Theme views
    'GenreThemeListView', 'GenreDetailView', 'ThemeDetailView',
    # Engine views
    'EngineListView', 'EngineDetailView',
]
