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
from django.urls import path, include
from django.views.generic import TemplateView
from core.views import IndexView
from trophies.views import GamesListView, TrophiesListView, ProfilesListView, SearchView, GameDetailView, ProfileDetailView, TrophyCaseView, ToggleSelectionView, BadgeListView, BadgeDetailView, GuideListView, ProfileSyncStatusView, TriggerSyncView, SearchSyncProfileView, AddSyncStatusView, LinkPSNView, ProfileVerifyView, monitoring_dashboard, token_stats, token_stats_sse
from users.views import CustomConfirmEmailView

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
    path('pptv/', GuideListView.as_view(), name='guides_list'),

    path('profiles/<str:psn_username>/trophy-case/', TrophyCaseView.as_view(), name='trophy_case'),
    path('toggle-selection/', ToggleSelectionView.as_view(), name='toggle-selection'),

    path('search/', SearchView.as_view(), name='search'),
    path('logout/', LogoutView.as_view(next_page='home'), name='logout'),


    path('api/profile-verify/', ProfileVerifyView.as_view(), name='profile_verify'),
    path('api/trigger-sync/', TriggerSyncView.as_view(), name='trigger_sync'),
    path('api/profile-sync-status/', ProfileSyncStatusView.as_view(), name='profile_sync_status'),
    path('api/search-sync-profile/', SearchSyncProfileView.as_view(), name='search_sync_profile'),
    path('api/add-sync-status/', AddSyncStatusView.as_view(), name='add_sync_status'),
    path('api/token-stats/', token_stats, name='token-stats'),
    path('api/token-stats/sse/', token_stats_sse, name='token-stats-sse'),
    path('monitoring/', monitoring_dashboard, name='monitoring'),

    path('accounts/link-psn/', LinkPSNView.as_view(), name='link_psn'),
    path('accounts/confirm-email/<str:key>/', CustomConfirmEmailView.as_view(), name='account_confirm_email'),

    path('users/', include('users.urls')),
    path('accounts/', include('allauth.urls')),
    path('api/v1/', include('api.urls')),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

handler404 = TemplateView.as_view(template_name='404.html')