"""
Trophies views package.

Re-exports all view classes for backward compatibility with existing URL configuration.
"""

from .game_views import GamesListView, GameDetailView, GuideListView
from .trophy_views import TrophiesListView, TrophyCaseView, ToggleSelectionView
from .profile_views import ProfilesListView, ProfileDetailView, LinkPSNView, ProfileVerifyView
from .badge_views import BadgeListView, BadgeDetailView, BadgeLeaderboardsView, OverallBadgeLeaderboardsView, MilestoneListView
from .checklist_views import ChecklistDetailView, ChecklistCreateView, ChecklistEditView, MyChecklistsView, MyShareablesView, BrowseGuidesView
from .sync_views import ProfileSyncStatusView, TriggerSyncView, SearchSyncProfileView, AddSyncStatusView
from .admin_views import TokenMonitoringView, BadgeCreationView, CommentModerationView, ModerationActionView, ModerationLogView
from .misc_views import SearchView
from .list_views import BrowseListsView, GameListDetailView, GameListEditView, GameListCreateView, MyListsView

__all__ = [
    # Game views
    'GamesListView', 'GameDetailView', 'GuideListView',
    # Trophy views
    'TrophiesListView', 'TrophyCaseView', 'ToggleSelectionView',
    # Profile views
    'ProfilesListView', 'ProfileDetailView', 'LinkPSNView', 'ProfileVerifyView',
    # Badge views
    'BadgeListView', 'BadgeDetailView', 'BadgeLeaderboardsView', 'OverallBadgeLeaderboardsView', 'MilestoneListView',
    # Checklist views
    'ChecklistDetailView', 'ChecklistCreateView', 'ChecklistEditView', 'MyChecklistsView', 'MyShareablesView', 'BrowseGuidesView',
    # Sync views
    'ProfileSyncStatusView', 'TriggerSyncView', 'SearchSyncProfileView', 'AddSyncStatusView',
    # Admin views
    'TokenMonitoringView', 'BadgeCreationView', 'CommentModerationView', 'ModerationActionView', 'ModerationLogView',
    # Misc views
    'SearchView',
    # List views
    'BrowseListsView', 'GameListDetailView', 'GameListEditView', 'GameListCreateView', 'MyListsView',
]
