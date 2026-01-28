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
from trophies.views import GamesListView, TrophiesListView, ProfilesListView, SearchView, GameDetailView, ProfileDetailView, TrophyCaseView, ToggleSelectionView, BadgeListView, BadgeDetailView, GuideListView, ProfileSyncStatusView, TriggerSyncView, SearchSyncProfileView, AddSyncStatusView, LinkPSNView, ProfileVerifyView, TokenMonitoringView, BadgeCreationView, BadgeLeaderboardsView, OverallBadgeLeaderboardsView, MilestoneListView, CommentModerationView, ModerationActionView, ModerationLogView, ChecklistDetailView, ChecklistCreateView, ChecklistEditView, MyChecklistsView
from users.views import CustomConfirmEmailView, stripe_webhook

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

    path('leaderboard/badges/', OverallBadgeLeaderboardsView.as_view(), name='overall_badge_leaderboards'),
    path('leaderboard/badges/<str:series_slug>/', BadgeLeaderboardsView.as_view(), name='badge_leaderboards' ),

    path('pptv/', GuideListView.as_view(), name='guides_list'),

    # Checklist URLs
    path('checklists/<int:checklist_id>/', ChecklistDetailView.as_view(), name='checklist_detail'),
    path('checklists/<int:checklist_id>/edit/', ChecklistEditView.as_view(), name='checklist_edit'),
    path('checklists/create/<int:concept_id>/<str:np_communication_id>/', ChecklistCreateView.as_view(), name='checklist_create'),
    path('my-checklists/', MyChecklistsView.as_view(), name='my_checklists'),

    path('profiles/<str:psn_username>/trophy-case/', TrophyCaseView.as_view(), name='trophy_case'),
    path('toggle-selection/', ToggleSelectionView.as_view(), name='toggle-selection'),

    path('search/', SearchView.as_view(), name='search'),
    path('logout/', LogoutView.as_view(next_page='home'), name='logout'),

    path('staff/badge-create/', BadgeCreationView.as_view(), name='badge_creation'),
    path('staff/moderation/', CommentModerationView.as_view(), name='comment_moderation'),
    path('staff/moderation/action/<int:report_id>/', ModerationActionView.as_view(), name='moderation_action'),
    path('staff/moderation/log/', ModerationLogView.as_view(), name='moderation_log'),

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

    path('users/', include('users.urls')),
    path('accounts/', include('allauth.urls')),
    path('api/v1/', include('api.urls')),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

handler404 = TemplateView.as_view(template_name='404.html')