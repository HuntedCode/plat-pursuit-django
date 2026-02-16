"""
URL configuration for plat_pursuit project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""

from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.contrib.auth.views import LogoutView
from django.contrib.sitemaps.views import sitemap
from django.urls import path, include
from django.views.generic import TemplateView
from core.views import IndexView, AdsTxtView, RobotsTxtView, PrivacyPolicyView, TermsOfServiceView, AboutView, ContactView
from core.sitemaps import StaticViewSitemap, GameSitemap, ProfileSitemap

sitemaps = {
    'static': StaticViewSitemap,
    'games': GameSitemap,
    'profiles': ProfileSitemap,
}
from trophies.views import GamesListView, TrophiesListView, ProfilesListView, SearchView, GameDetailView, ProfileDetailView, TrophyCaseView, ToggleSelectionView, BadgeListView, BadgeDetailView, GuideListView, ProfileSyncStatusView, TriggerSyncView, SearchSyncProfileView, AddSyncStatusView, LinkPSNView, ProfileVerifyView, TokenMonitoringView, BadgeCreationView, BadgeLeaderboardsView, OverallBadgeLeaderboardsView, MilestoneListView, CommentModerationView, ModerationActionView, ModerationLogView, ChecklistDetailView, ChecklistCreateView, ChecklistEditView, MyChecklistsView, MyShareablesView, BrowseGuidesView, BrowseListsView, GameListDetailView, GameListEditView, GameListCreateView, MyListsView, ChallengeHubView, MyChallengesView, AZChallengeCreateView, AZChallengeSetupView, AZChallengeDetailView, AZChallengeEditView, GameFamilyManagementView
from trophies.recap_views import RecapIndexView, RecapSlideView
from users.views import CustomConfirmEmailView, stripe_webhook
from notifications.views import (
    NotificationInboxView,
    AdminNotificationCenterView,
    AdminNotificationHistoryView,
    AdminScheduledNotificationsView,
    AdminCancelScheduledView,
)

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", IndexView.as_view(), name="home"),
    path('games/', GamesListView.as_view(), name='games_list'),
    path('games/<str:np_communication_id>/', GameDetailView.as_view(), name='game_detail'),
    path('games/<str:np_communication_id>/<str:psn_username>/', GameDetailView.as_view(), name='game_detail_with_profile'),
    path('trophies/', TrophiesListView.as_view(), name='trophies_list'),
    path('profiles/', ProfilesListView.as_view(), name='profiles_list'),
    path('profiles/<str:psn_username>/', ProfileDetailView.as_view(), name='profile_detail'),
    path('badges/', BadgeListView.as_view(), name='badges_list'),
    path('badges/<str:series_slug>/', BadgeDetailView.as_view(), name='badge_detail'),
    path('badges/<str:series_slug>/<str:psn_username>/', BadgeDetailView.as_view(), name='badge_detail_with_profile'),

    path('milestones/', MilestoneListView.as_view(), name='milestones_list'),

    path('notifications/', NotificationInboxView.as_view(), name='notification_inbox'),

    path('leaderboard/badges/', OverallBadgeLeaderboardsView.as_view(), name='overall_badge_leaderboards'),
    path('leaderboard/badges/<str:series_slug>/', BadgeLeaderboardsView.as_view(), name='badge_leaderboards' ),

    path('pptv/', GuideListView.as_view(), name='guides_list'),
    path('guides/', BrowseGuidesView.as_view(), name='guides_browse'),

    # Guide URLs (checklists)
    path('guides/<int:guide_id>/', ChecklistDetailView.as_view(), name='guide_detail'),
    path('guides/<int:guide_id>/edit/', ChecklistEditView.as_view(), name='guide_edit'),
    path('guides/create/<int:concept_id>/<str:np_communication_id>/', ChecklistCreateView.as_view(), name='guide_create'),
    path('my-guides/', MyChecklistsView.as_view(), name='my_guides'),
    path('my-shareables/', MyShareablesView.as_view(), name='my_shareables'),

    # Game List URLs
    path('lists/', BrowseListsView.as_view(), name='lists_browse'),
    path('lists/create/', GameListCreateView.as_view(), name='list_create'),
    path('lists/<int:list_id>/', GameListDetailView.as_view(), name='list_detail'),
    path('lists/<int:list_id>/edit/', GameListEditView.as_view(), name='list_edit'),
    path('my-lists/', MyListsView.as_view(), name='my_lists'),

    # Challenge URLs
    path('challenges/', ChallengeHubView.as_view(), name='challenges_browse'),
    path('challenges/az/create/', AZChallengeCreateView.as_view(), name='az_challenge_create'),
    path('challenges/az/<int:challenge_id>/', AZChallengeDetailView.as_view(), name='az_challenge_detail'),
    path('challenges/az/<int:challenge_id>/setup/', AZChallengeSetupView.as_view(), name='az_challenge_setup'),
    path('challenges/az/<int:challenge_id>/edit/', AZChallengeEditView.as_view(), name='az_challenge_edit'),
    path('my-challenges/', MyChallengesView.as_view(), name='my_challenges'),

    # Monthly Recap URLs
    path('recap/', RecapIndexView.as_view(), name='recap_index'),
    path('recap/<int:year>/<int:month>/', RecapSlideView.as_view(), name='recap_view'),

    path('profiles/<str:psn_username>/trophy-case/', TrophyCaseView.as_view(), name='trophy_case'),
    path('toggle-selection/', ToggleSelectionView.as_view(), name='toggle-selection'),

    path('search/', SearchView.as_view(), name='search'),
    path('logout/', LogoutView.as_view(next_page='home'), name='logout'),

    path('staff/badge-create/', BadgeCreationView.as_view(), name='badge_creation'),
    path('staff/moderation/', CommentModerationView.as_view(), name='comment_moderation'),
    path('staff/moderation/action/<int:report_id>/', ModerationActionView.as_view(), name='moderation_action'),
    path('staff/moderation/log/', ModerationLogView.as_view(), name='moderation_log'),
    path('staff/game-families/', GameFamilyManagementView.as_view(), name='game_family_management'),
    path('staff/notifications/', AdminNotificationCenterView.as_view(), name='admin_notification_center'),
    path('staff/notifications/history/', AdminNotificationHistoryView.as_view(), name='admin_notification_history'),
    path('staff/notifications/scheduled/', AdminScheduledNotificationsView.as_view(), name='admin_scheduled_notifications'),
    path('staff/notifications/scheduled/<int:pk>/cancel/', AdminCancelScheduledView.as_view(), name='admin_cancel_scheduled'),

    path('api/profile-verify/', ProfileVerifyView.as_view(), name='profile_verify'),
    path('api/trigger-sync/', TriggerSyncView.as_view(), name='trigger_sync'),
    path('api/profile-sync-status/', ProfileSyncStatusView.as_view(), name='profile_sync_status'),
    path('api/search-sync-profile/', SearchSyncProfileView.as_view(), name='search_sync_profile'),
    path('api/add-sync-status/', AddSyncStatusView.as_view(), name='add_sync_status'),

    path('accounts/link-psn/', LinkPSNView.as_view(), name='link_psn'),
    path('accounts/confirm-email/<str:key>/', CustomConfirmEmailView.as_view(), name='account_confirm_email'),

    path('monitoring/tokens/', TokenMonitoringView.as_view(), name='token_monitoring'),

    path("stripe/webhook/", stripe_webhook, name="stripe_webhook"),
    path('ads.txt', AdsTxtView.as_view(), name='ads_txt'),
    path('robots.txt', RobotsTxtView.as_view(), name='robots_txt'),
    path('sitemap.xml', sitemap, {'sitemaps': sitemaps}, name='sitemap'),

    path('privacy/', PrivacyPolicyView.as_view(), name='privacy'),
    path('terms/', TermsOfServiceView.as_view(), name='terms'),
    path('about/', AboutView.as_view(), name='about'),
    path('contact/', ContactView.as_view(), name='contact'),
    path('beta-access/', TemplateView.as_view(template_name='pages/beta_access_required.html'), name='beta_access_required'),

    # Arcade (mini-games)
    path('arcade/stellar-circuit/', TemplateView.as_view(template_name='minigames/stellar-circuit.html'), name='stellar_circuit'),

    path('users/', include('users.urls')),
    path('accounts/', include('allauth.urls')),
    path('api/v1/', include('api.urls')),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

handler404 = TemplateView.as_view(template_name='404.html')