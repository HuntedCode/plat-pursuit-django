"""
Trophies views package.

Re-exports all view classes for backward compatibility with existing URL configuration.
"""

from .game_views import GamesListView, GameDetailView, GuideListView, RecentlyAddedView, RandomGameView, TrophyListsBrowseView
from .game_page_views import GamePageView
from .game_leaderboard_views import GameLeaderboardView
from .profile_views import ProfilesListView, ProfileDetailView, ProfileDayView, LinkPSNView, ProfileVerifyView
from .badge_views import BadgeHowItWorksView, BadgeListView, BadgeDetailView, GroupBadgeInspectView, BadgeRanksPanelView, OverallBadgeLeaderboardsView, LeaderboardRowsView
# Checklist views removed during roadmap migration (DB tables retained)
from .sync_views import ProfileSyncStatusView, TriggerSyncView, SearchSyncProfileView, AddSyncStatusView, RequestProfileRefreshView, ProfileSuggestView, SiteSuggestView
from .admin_views import TokenMonitoringView, BadgeSeriesCreationView
from .misc_views import SearchView
from .review_hub_views import RateMyGamesView
from .title_views import MyTitlesView
from .roadmap_views import RoadmapDetailView, RoadmapEditorView
from .shareables_views import PlatCardsView
from .career_views import CareerView, JobsBrowseView, JobDetailView, JobRanksPanelView, JobContractsView, ContractsResultsView, ContractModalView, ContractModalPreviewView
from .collection_views import CollectionView, CollectionBadgeModalView
from .company_views import CompanyListView, CompanyDetailView
from .franchise_views import FranchiseListView, FranchiseDetailView
from .genre_views import GenreThemeListView, GenreDetailView, ThemeDetailView
from .moderation_views import (  # noqa: F401
    ModCenterView, QuickTakeQueueView, GameFlagQueueView, ListReportQueueView,
    HideBlurbView, DismissBlurbReportView, ApproveGameFlagView, DismissGameFlagView,
    HideListTextView, DismissListReportView,
)

__all__ = [
    # Game views
    'GamesListView', 'GameDetailView', 'GamePageView', 'GuideListView', 'RecentlyAddedView', 'RandomGameView',
    'TrophyListsBrowseView',
    # Profile views
    'ProfilesListView', 'ProfileDetailView', 'ProfileDayView', 'LinkPSNView', 'ProfileVerifyView',
    # Badge views
    'BadgeHowItWorksView', 'BadgeListView', 'BadgeDetailView', 'GroupBadgeInspectView', 'BadgeRanksPanelView', 'OverallBadgeLeaderboardsView', 'LeaderboardRowsView',
    # Checklist views (removed, DB tables retained)
    # Sync views
    'ProfileSyncStatusView', 'TriggerSyncView', 'SearchSyncProfileView', 'AddSyncStatusView', 'RequestProfileRefreshView', 'ProfileSuggestView', 'SiteSuggestView',
    # Admin views
    'TokenMonitoringView', 'BadgeSeriesCreationView',
    # Misc views
    'SearchView',
    # Review Hub views
    'RateMyGamesView',
    # Dashboard views
    # Title views
    'MyTitlesView',
    # Roadmap views
    'RoadmapDetailView', 'RoadmapEditorView',
    # Shareables views
    'PlatCardsView',
    # The Lab view
    'CareerView', 'JobsBrowseView', 'JobDetailView', 'JobRanksPanelView', 'JobContractsView', 'ContractsResultsView', 'ContractModalView', 'ContractModalPreviewView',
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
    # Mod Center
    'ModCenterView', 'QuickTakeQueueView', 'GameFlagQueueView', 'ListReportQueueView',
    'HideBlurbView', 'DismissBlurbReportView', 'ApproveGameFlagView', 'DismissGameFlagView',
    'HideListTextView', 'DismissListReportView',
]
