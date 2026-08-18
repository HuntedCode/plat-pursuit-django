"""
Trophies views package.

Re-exports all view classes for backward compatibility with existing URL configuration.
"""

from .game_views import GamesListView, GameDetailView, GuideListView, RecentlyAddedView, RandomGameView
from .game_leaderboard_views import GameLeaderboardView
from .trophy_views import TrophyCaseView, ToggleSelectionView
from .profile_views import ProfilesListView, ProfileDetailView, ProfileDayView, LinkPSNView, ProfileVerifyView
from .badge_views import BadgeHowItWorksView, BadgeListView, BadgeDetailView, GroupBadgeInspectView, BadgeRanksPanelView, OverallBadgeLeaderboardsView
# Checklist views removed during roadmap migration (DB tables retained)
from .sync_views import ProfileSyncStatusView, TriggerSyncView, SearchSyncProfileView, AddSyncStatusView, ProfileSuggestView, SiteSuggestView
from .admin_views import (
    TokenMonitoringView, BadgeSeriesCreationView, CommentModerationView, ModerationActionView,
    ModerationLogView, ReviewModerationView, ReviewModerationActionView,
    ReviewModerationLogView, GameFamilyManagementView,
    LegacyChecklistListView, LegacyChecklistDetailView,
)
from .misc_views import SearchView
from .list_views import BrowseListsView, GameListDetailView, GameListEditView, GameListCreateView, MyListsView
from .review_hub_views import ReviewHubLandingView, RateMyGamesView, ReviewHubDetailView, ReviewsArchivedView
from .title_views import MyTitlesView
from .platinum_grid_views import PlatinumGridView
from .roadmap_views import RoadmapDetailView, RoadmapEditorView
from .shareables_views import PlatCardsView
from .career_views import CareerView, JobsBrowseView, JobDetailView, ContractsResultsView, ContractModalView, ContractModalPreviewView
from .collection_views import CollectionView, CollectionBadgeModalView
from .company_views import CompanyListView, CompanyDetailView
from .franchise_views import FranchiseListView, FranchiseDetailView
from .genre_views import GenreThemeListView, GenreDetailView, ThemeDetailView

__all__ = [
    # Game views
    'GamesListView', 'GameDetailView', 'GuideListView', 'RecentlyAddedView', 'RandomGameView',
    # Trophy views
    'TrophyCaseView', 'ToggleSelectionView',
    # Profile views
    'ProfilesListView', 'ProfileDetailView', 'ProfileDayView', 'LinkPSNView', 'ProfileVerifyView',
    # Badge views
    'BadgeHowItWorksView', 'BadgeListView', 'BadgeDetailView', 'GroupBadgeInspectView', 'BadgeRanksPanelView', 'OverallBadgeLeaderboardsView',
    # Checklist views (removed, DB tables retained)
    # Sync views
    'ProfileSyncStatusView', 'TriggerSyncView', 'SearchSyncProfileView', 'AddSyncStatusView', 'ProfileSuggestView', 'SiteSuggestView',
    # Admin views
    'TokenMonitoringView', 'BadgeSeriesCreationView', 'CommentModerationView', 'ModerationActionView', 'ModerationLogView',
    'ReviewModerationView', 'ReviewModerationActionView', 'ReviewModerationLogView', 'GameFamilyManagementView',
    'LegacyChecklistListView', 'LegacyChecklistDetailView',
    # Misc views
    'SearchView',
    # List views
    'BrowseListsView', 'GameListDetailView', 'GameListEditView', 'GameListCreateView', 'MyListsView',
    # Review Hub views
    'ReviewHubLandingView', 'RateMyGamesView', 'ReviewHubDetailView', 'ReviewsArchivedView',
    # Dashboard views
    # Title views
    'MyTitlesView',
    # Platinum Grid views
    'PlatinumGridView',
    # Roadmap views
    'RoadmapDetailView', 'RoadmapEditorView',
    # Shareables views
    'PlatCardsView',
    # The Lab view
    'CareerView', 'JobsBrowseView', 'JobDetailView', 'ContractsResultsView', 'ContractModalView', 'ContractModalPreviewView',
    # Collection album view
    'CollectionView',
    'CollectionBadgeModalView',
    # Stats views
    # Company views
    'CompanyListView', 'CompanyDetailView',
    # Franchise views
    'FranchiseListView', 'FranchiseDetailView',
    # Genre/Theme views
    'GenreThemeListView', 'GenreDetailView', 'ThemeDetailView',
    # Engine views
]
