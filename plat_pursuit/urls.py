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
from django.views.generic import RedirectView, TemplateView
from core.views import AdsTxtView, RobotsTxtView, PrivacyPolicyView, TermsOfServiceView, AboutView, ContactView, HomeView, CommunityHubView
from core.sitemaps import (
    StaticViewSitemap, GameSitemap, ProfileSitemap,
    BadgeSitemap, GameListSitemap, ChallengeSitemap,
)

sitemaps = {
    'static': StaticViewSitemap,
    'games': GameSitemap,
    'profiles': ProfileSitemap,
    'badges': BadgeSitemap,
    'lists': GameListSitemap,
    'challenges': ChallengeSitemap,
}
from trophies.views import GamesListView, GameDetailView, RandomGameView, TrophiesListView, ProfilesListView, SearchView, ProfileDetailView, TrophyCaseView, ToggleSelectionView, BadgeListView, BadgeDetailView, ProfileSyncStatusView, TriggerSyncView, SearchSyncProfileView, AddSyncStatusView, LinkPSNView, ProfileVerifyView, TokenMonitoringView, BadgeCreationView, BadgeLeaderboardsView, OverallBadgeLeaderboardsView, MilestoneListView, CommentModerationView, ModerationActionView, ModerationLogView, BrowseListsView, GameListDetailView, GameListEditView, GameListCreateView, MyListsView, ChallengeHubView, MyChallengesView, AZChallengeCreateView, AZChallengeSetupView, AZChallengeDetailView, AZChallengeEditView, CalendarChallengeCreateView, CalendarChallengeDetailView, GenreChallengeCreateView, GenreChallengeSetupView, GenreChallengeDetailView, GenreChallengeEditView, GameFamilyManagementView, ReviewModerationView, ReviewModerationActionView, ReviewModerationLogView, MyTitlesView, ReviewHubLandingView, RateMyGamesView, ReviewHubDetailView, PlatinumGridView, RoadmapDetailView, RoadmapEditorView, MyShareablesView, MyPlatinumSharesView, MyChallengeSharesView, MyProfileCardView, MyStatsView, FlaggedGamesView, RecentlyAddedView, CompanyListView, CompanyDetailView, FranchiseListView, FranchiseDetailView, GenreThemeListView, GenreDetailView, ThemeDetailView, EngineListView, EngineDetailView
from trophies.recap_views import RecapIndexView, RecapSlideView
from users.views import CustomConfirmEmailView, stripe_webhook, paypal_webhook
from users.subscription_admin_views import SubscriptionAdminView
from fundraiser.views import FundraiserView, DonationSuccessView, FundraiserAdminView, BadgeRevealView
from api.profile_card_views import serve_profile_sig
from notifications.views import (
    NotificationInboxView,
    AdminNotificationCenterView,
    AdminNotificationHistoryView,
    AdminScheduledNotificationsView,
    AdminCancelScheduledView,
)

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", HomeView.as_view(), name="home"),
    # Legacy alias - keep old /dashboard/ links working
    path('dashboard/', RedirectView.as_view(pattern_name='home', permanent=True), name='dashboard'),

    # ─────────────────────────────────────────────────────────────────────
    # Community Hub initiative (Phase 10 IA + URL audit)
    # ─────────────────────────────────────────────────────────────────────
    # The Community Hub destination at /community/ and the standalone
    # /community/feed/ page (Phases 7-8). Below this, the URL audit moves
    # community-shaped content under /community/ and personal-progression
    # content under /achievements/. Public-facing tools moved to /tools/.
    #
    # Backward compatibility: every renamed URL keeps a 301 RedirectView
    # at the old path, so external links and bookmarks survive. The
    # `name=` parameter stays bound to the NEW canonical path so existing
    # `{% url %}` and `reverse()` calls in templates and Python code
    # continue to resolve to the right place without changes.
    path('community/', CommunityHubView.as_view(), name='community_hub'),

    path('games/', GamesListView.as_view(), name='games_list'),
    path('games/lucky/', RandomGameView.as_view(), name='random_game'),
    path('games/flagged/', FlaggedGamesView.as_view(), name='flagged_games'),
    path('games/recently-added/', RecentlyAddedView.as_view(), name='recently_added'),
    path('games/<str:np_communication_id>/roadmap/edit/', RoadmapEditorView.as_view(), name='roadmap_edit'),
    path('games/<str:np_communication_id>/roadmap/', RoadmapDetailView.as_view(), name='roadmap_detail'),
    path('games/<str:np_communication_id>/roadmap/<str:trophy_group_id>/', RoadmapDetailView.as_view(), name='roadmap_detail_dlc'),

    # Company pages
    path('companies/', CompanyListView.as_view(), name='companies_list'),
    path('companies/<slug:slug>/', CompanyDetailView.as_view(), name='company_detail'),

    # Franchise pages
    path('franchises/', FranchiseListView.as_view(), name='franchises_list'),
    path('franchises/<slug:slug>/', FranchiseDetailView.as_view(), name='franchise_detail'),

    # Genre/Theme pages
    path('genres/', GenreThemeListView.as_view(), name='genres_list'),
    path('genres/<slug:slug>/', GenreDetailView.as_view(), name='genre_detail'),
    path('themes/<slug:slug>/', ThemeDetailView.as_view(), name='theme_detail'),

    # Game engine pages
    path('engines/', EngineListView.as_view(), name='engines_list'),
    path('engines/<slug:slug>/', EngineDetailView.as_view(), name='engine_detail'),
    path('games/<str:np_communication_id>/', GameDetailView.as_view(), name='game_detail'),
    path('games/<str:np_communication_id>/<str:psn_username>/', GameDetailView.as_view(), name='game_detail_with_profile'),
    path('trophies/', TrophiesListView.as_view(), name='trophies_list'),

    # Profiles (canonical paths under /community/, legacy redirects below)
    path('community/profiles/', ProfilesListView.as_view(), name='profiles_list'),
    path('community/profiles/<str:psn_username>/', ProfileDetailView.as_view(), name='profile_detail'),
    path('community/profiles/<str:psn_username>/trophy-case/', TrophyCaseView.as_view(), name='trophy_case'),

    # My Pursuit hub: badges, milestones, titles (canonical paths under /my-pursuit/)
    # The original Phase 10 commit had these under /achievements/. The Phase 10a
    # rework relocates them to /my-pursuit/ to align with the renamed hub
    # (see docs/features/my-pursuit-hub.md for the rationale). Both the legacy
    # /badges/, /milestones/, /my-titles/ paths AND the previous /achievements/*
    # paths now redirect here via the legacy redirect block below.
    #
    # The bare /my-pursuit/ path is a 301 redirect to the headline sub-page
    # (Badges). My Pursuit deliberately does NOT have its own landing page;
    # the Badges page IS the landing, and the sub-nav strip handles wayfinding
    # to Milestones and Titles. This mirrors how /games/ is the Browse hub
    # landing rather than building a separate Browse landing page. Once the
    # gamification initiative ships and the section grows to 8+ sub-items,
    # a dedicated /my-pursuit/ landing page may make sense.
    path('my-pursuit/', RedirectView.as_view(pattern_name='badges_list', permanent=True), name='my_pursuit_hub'),
    path('my-pursuit/badges/', BadgeListView.as_view(), name='badges_list'),
    path('my-pursuit/badges/<str:series_slug>/', BadgeDetailView.as_view(), name='badge_detail'),
    path('my-pursuit/badges/<str:series_slug>/<str:psn_username>/', BadgeDetailView.as_view(), name='badge_detail_with_profile'),
    path('my-pursuit/milestones/', MilestoneListView.as_view(), name='milestones_list'),
    path('my-pursuit/titles/', MyTitlesView.as_view(), name='my_titles'),

    # Dashboard hub: personal-utility pages live under /dashboard/.
    # The original Phase 10 commit put these under /tools/. The Phase 10a
    # rework relocates them to /dashboard/ because they're personal-cockpit
    # features that belong in the Dashboard hub (see ia-and-subnav.md). The
    # Platinum Grid wizard lives nested inside Shareables since it generates
    # one of the shareable image types.
    path('dashboard/stats/', MyStatsView.as_view(), name='my_stats'),
    # Shareables hub: landing page + dedicated sub-pages for each share type.
    # See trophies/views/shareables_views.py for the per-view docstrings.
    path('dashboard/shareables/', MyShareablesView.as_view(), name='my_shareables'),
    path('dashboard/shareables/platinums/', MyPlatinumSharesView.as_view(), name='my_shareables_platinums'),
    path('dashboard/shareables/profile-card/', MyProfileCardView.as_view(), name='my_shareables_profile_card'),
    path('dashboard/shareables/challenges/', MyChallengeSharesView.as_view(), name='my_shareables_challenges'),
    path('dashboard/shareables/platinum-grid/', PlatinumGridView.as_view(), name='platinum_grid'),

    path('notifications/', NotificationInboxView.as_view(), name='notification_inbox'),

    # Leaderboards (canonical paths under /community/leaderboards/)
    path('community/leaderboards/badges/', OverallBadgeLeaderboardsView.as_view(), name='overall_badge_leaderboards'),
    path('community/leaderboards/badges/<str:series_slug>/', BadgeLeaderboardsView.as_view(), name='badge_leaderboards'),

    # Guide/checklist URLs - all redirected to home (system removed, replaced by roadmaps)
    path('guides/', RedirectView.as_view(pattern_name='home', permanent=False), name='guides_browse'),
    path('guides/<int:guide_id>/', RedirectView.as_view(pattern_name='home', permanent=False), name='guide_detail'),
    path('guides/<int:guide_id>/edit/', RedirectView.as_view(pattern_name='home', permanent=False), name='guide_edit'),
    path('guides/create/<int:concept_id>/<str:np_communication_id>/', RedirectView.as_view(pattern_name='home', permanent=False), name='guide_create'),
    path('my-guides/', RedirectView.as_view(pattern_name='home', permanent=False), name='my_guides'),

    # Game Lists (canonical paths under /community/lists/)
    path('community/lists/', BrowseListsView.as_view(), name='lists_browse'),
    path('community/lists/create/', GameListCreateView.as_view(), name='list_create'),
    path('community/lists/<int:list_id>/', GameListDetailView.as_view(), name='list_detail'),
    path('community/lists/<int:list_id>/edit/', GameListEditView.as_view(), name='list_edit'),
    path('my-lists/', MyListsView.as_view(), name='my_lists'),

    # Challenges (canonical paths under /community/challenges/)
    path('community/challenges/', ChallengeHubView.as_view(), name='challenges_browse'),
    path('community/challenges/az/create/', AZChallengeCreateView.as_view(), name='az_challenge_create'),
    path('community/challenges/az/<int:challenge_id>/', AZChallengeDetailView.as_view(), name='az_challenge_detail'),
    path('community/challenges/az/<int:challenge_id>/setup/', AZChallengeSetupView.as_view(), name='az_challenge_setup'),
    path('community/challenges/az/<int:challenge_id>/edit/', AZChallengeEditView.as_view(), name='az_challenge_edit'),
    path('community/challenges/calendar/create/', CalendarChallengeCreateView.as_view(), name='calendar_challenge_create'),
    path('community/challenges/calendar/<int:challenge_id>/', CalendarChallengeDetailView.as_view(), name='calendar_challenge_detail'),
    path('community/challenges/genre/create/', GenreChallengeCreateView.as_view(), name='genre_challenge_create'),
    path('community/challenges/genre/<int:challenge_id>/', GenreChallengeDetailView.as_view(), name='genre_challenge_detail'),
    path('community/challenges/genre/<int:challenge_id>/setup/', GenreChallengeSetupView.as_view(), name='genre_challenge_setup'),
    path('community/challenges/genre/<int:challenge_id>/edit/', GenreChallengeEditView.as_view(), name='genre_challenge_edit'),
    path('my-challenges/', MyChallengesView.as_view(), name='my_challenges'),

    # Review Hub (canonical paths under /community/reviews/)
    path('community/reviews/', ReviewHubLandingView.as_view(), name='reviews_landing'),
    path('community/reviews/rate-my-games/', RateMyGamesView.as_view(), name='rate_my_games'),
    path('community/reviews/<slug:slug>/', ReviewHubDetailView.as_view(), name='review_hub'),

    # Monthly Recap URLs (canonical paths under /dashboard/recap/)
    # Recap is a Dashboard hub citizen — see docs/architecture/ia-and-subnav.md.
    # Legacy /recap/ paths redirect here via the redirect block below.
    path('dashboard/recap/', RecapIndexView.as_view(), name='recap_index'),
    path('dashboard/recap/<int:year>/<int:month>/', RecapSlideView.as_view(), name='recap_view'),

    path('toggle-selection/', ToggleSelectionView.as_view(), name='toggle-selection'),

    # ─────────────────────────────────────────────────────────────────────
    # Phase 10 legacy redirects: 301 from old paths to new canonical names.
    # ─────────────────────────────────────────────────────────────────────
    # These keep external links, bookmarks, and search engine indices alive
    # as the URL audit reshuffles paths into the cleaner /community/,
    # /my-pursuit/, and /dashboard/ namespaces. RedirectView with
    # `pattern_name=` resolves the redirect target via the new canonical
    # `name=`, so any future rename requires updating only the canonical
    # path above (this section keeps working unchanged).
    #
    # `query_string=True` propagates query strings (?tab=, ?page=, etc.)
    # through the redirect so deep links survive intact.
    #
    # The Phase 10a rework added a SECOND wave of redirects: the original
    # Phase 10 had moved badges/milestones/titles to /achievements/* and
    # Stats/Grid to /tools/*. Phase 10a re-renamed those to /my-pursuit/*
    # and /dashboard/*, so the previously-canonical paths now also need
    # redirect entries here alongside the original legacy paths.

    # Profiles → /community/profiles/
    path('profiles/', RedirectView.as_view(pattern_name='profiles_list', permanent=True, query_string=True)),
    path('profiles/<str:psn_username>/', RedirectView.as_view(pattern_name='profile_detail', permanent=True, query_string=True)),
    path('profiles/<str:psn_username>/trophy-case/', RedirectView.as_view(pattern_name='trophy_case', permanent=True, query_string=True)),

    # My Pursuit hub: badges, milestones, titles
    # Two waves: pre-Phase-10 legacy paths AND the intermediate /achievements/* paths
    path('badges/', RedirectView.as_view(pattern_name='badges_list', permanent=True, query_string=True)),
    path('badges/<str:series_slug>/', RedirectView.as_view(pattern_name='badge_detail', permanent=True, query_string=True)),
    path('badges/<str:series_slug>/<str:psn_username>/', RedirectView.as_view(pattern_name='badge_detail_with_profile', permanent=True, query_string=True)),
    path('milestones/', RedirectView.as_view(pattern_name='milestones_list', permanent=True, query_string=True)),
    path('my-titles/', RedirectView.as_view(pattern_name='my_titles', permanent=True, query_string=True)),
    path('achievements/badges/', RedirectView.as_view(pattern_name='badges_list', permanent=True, query_string=True)),
    path('achievements/badges/<str:series_slug>/', RedirectView.as_view(pattern_name='badge_detail', permanent=True, query_string=True)),
    path('achievements/badges/<str:series_slug>/<str:psn_username>/', RedirectView.as_view(pattern_name='badge_detail_with_profile', permanent=True, query_string=True)),
    path('achievements/milestones/', RedirectView.as_view(pattern_name='milestones_list', permanent=True, query_string=True)),
    path('achievements/titles/', RedirectView.as_view(pattern_name='my_titles', permanent=True, query_string=True)),

    # Dashboard hub: My Stats, My Shareables, Platinum Grid, Recap
    # Two waves: pre-Phase-10 legacy paths AND the intermediate /tools/* paths
    path('my-stats/', RedirectView.as_view(pattern_name='my_stats', permanent=True, query_string=True)),
    path('my-shareables/', RedirectView.as_view(pattern_name='my_shareables', permanent=True, query_string=True)),
    path('staff/platinum-grid/', RedirectView.as_view(pattern_name='platinum_grid', permanent=True, query_string=True)),
    path('tools/stats/', RedirectView.as_view(pattern_name='my_stats', permanent=True, query_string=True)),
    path('tools/platinum-grid/', RedirectView.as_view(pattern_name='platinum_grid', permanent=True, query_string=True)),
    path('recap/', RedirectView.as_view(pattern_name='recap_index', permanent=True, query_string=True)),
    path('recap/<int:year>/<int:month>/', RedirectView.as_view(pattern_name='recap_view', permanent=True, query_string=True)),

    # Leaderboards
    path('leaderboard/badges/', RedirectView.as_view(pattern_name='overall_badge_leaderboards', permanent=True, query_string=True)),
    path('leaderboard/badges/<str:series_slug>/', RedirectView.as_view(pattern_name='badge_leaderboards', permanent=True, query_string=True)),

    # Game Lists
    path('lists/', RedirectView.as_view(pattern_name='lists_browse', permanent=True, query_string=True)),
    path('lists/create/', RedirectView.as_view(pattern_name='list_create', permanent=True, query_string=True)),
    path('lists/<int:list_id>/', RedirectView.as_view(pattern_name='list_detail', permanent=True, query_string=True)),
    path('lists/<int:list_id>/edit/', RedirectView.as_view(pattern_name='list_edit', permanent=True, query_string=True)),

    # Challenges
    path('challenges/', RedirectView.as_view(pattern_name='challenges_browse', permanent=True, query_string=True)),
    path('challenges/az/create/', RedirectView.as_view(pattern_name='az_challenge_create', permanent=True, query_string=True)),
    path('challenges/az/<int:challenge_id>/', RedirectView.as_view(pattern_name='az_challenge_detail', permanent=True, query_string=True)),
    path('challenges/az/<int:challenge_id>/setup/', RedirectView.as_view(pattern_name='az_challenge_setup', permanent=True, query_string=True)),
    path('challenges/az/<int:challenge_id>/edit/', RedirectView.as_view(pattern_name='az_challenge_edit', permanent=True, query_string=True)),
    path('challenges/calendar/create/', RedirectView.as_view(pattern_name='calendar_challenge_create', permanent=True, query_string=True)),
    path('challenges/calendar/<int:challenge_id>/', RedirectView.as_view(pattern_name='calendar_challenge_detail', permanent=True, query_string=True)),
    path('challenges/genre/create/', RedirectView.as_view(pattern_name='genre_challenge_create', permanent=True, query_string=True)),
    path('challenges/genre/<int:challenge_id>/', RedirectView.as_view(pattern_name='genre_challenge_detail', permanent=True, query_string=True)),
    path('challenges/genre/<int:challenge_id>/setup/', RedirectView.as_view(pattern_name='genre_challenge_setup', permanent=True, query_string=True)),
    path('challenges/genre/<int:challenge_id>/edit/', RedirectView.as_view(pattern_name='genre_challenge_edit', permanent=True, query_string=True)),

    # Reviews
    path('reviews/', RedirectView.as_view(pattern_name='reviews_landing', permanent=True, query_string=True)),
    path('reviews/rate-my-games/', RedirectView.as_view(pattern_name='rate_my_games', permanent=True, query_string=True)),
    path('reviews/<slug:slug>/', RedirectView.as_view(pattern_name='review_hub', permanent=True, query_string=True)),

    path('search/', SearchView.as_view(), name='search'),
    path('logout/', LogoutView.as_view(template_name='account/logout.html'), name='logout'),

    path('staff/badge-create/', BadgeCreationView.as_view(), name='badge_creation'),
    path('staff/moderation/', CommentModerationView.as_view(), name='comment_moderation'),
    path('staff/moderation/action/<int:report_id>/', ModerationActionView.as_view(), name='moderation_action'),
    path('staff/moderation/log/', ModerationLogView.as_view(), name='moderation_log'),
    path('staff/review-moderation/', ReviewModerationView.as_view(), name='review_moderation'),
    path('staff/review-moderation/action/<int:report_id>/', ReviewModerationActionView.as_view(), name='review_moderation_action'),
    path('staff/review-moderation/log/', ReviewModerationLogView.as_view(), name='review_moderation_log'),
    path('staff/game-families/', GameFamilyManagementView.as_view(), name='game_family_management'),
    path('staff/notifications/', AdminNotificationCenterView.as_view(), name='admin_notification_center'),
    path('staff/notifications/history/', AdminNotificationHistoryView.as_view(), name='admin_notification_history'),
    path('staff/notifications/scheduled/', AdminScheduledNotificationsView.as_view(), name='admin_scheduled_notifications'),
    path('staff/notifications/scheduled/<int:pk>/cancel/', AdminCancelScheduledView.as_view(), name='admin_cancel_scheduled'),
    path('staff/subscriptions/', SubscriptionAdminView.as_view(), name='subscription_admin'),
    path('staff/fundraiser/', FundraiserAdminView.as_view(), name='fundraiser_admin'),
    path('staff/badge-reveal/', BadgeRevealView.as_view(), name='badge_reveal'),
    # NOTE: PlatinumGridView's canonical path is now /tools/platinum-grid/
    # (registered above in the Tools section). The legacy /staff/platinum-grid/
    # path is a 301 redirect, registered in the Phase 10 legacy redirects block.

    # Fundraiser
    path('fundraiser/<slug:slug>/', FundraiserView.as_view(), name='fundraiser'),
    path('fundraiser/<slug:slug>/success/', DonationSuccessView.as_view(), name='fundraiser_success'),

    path('api/profile-verify/', ProfileVerifyView.as_view(), name='profile_verify'),
    path('api/trigger-sync/', TriggerSyncView.as_view(), name='trigger_sync'),
    path('api/profile-sync-status/', ProfileSyncStatusView.as_view(), name='profile_sync_status'),
    path('api/search-sync-profile/', SearchSyncProfileView.as_view(), name='search_sync_profile'),
    path('api/add-sync-status/', AddSyncStatusView.as_view(), name='add_sync_status'),

    path('accounts/link-psn/', LinkPSNView.as_view(), name='link_psn'),
    path('accounts/confirm-email/<str:key>/', CustomConfirmEmailView.as_view(), name='account_confirm_email'),

    path('monitoring/tokens/', TokenMonitoringView.as_view(), name='token_monitoring'),

    path("stripe/webhook/", stripe_webhook, name="stripe_webhook"),
    path("paypal/webhook/", paypal_webhook, name="paypal_webhook"),
    path('ads.txt', AdsTxtView.as_view(), name='ads_txt'),
    path('robots.txt', RobotsTxtView.as_view(), name='robots_txt'),
    path('sitemap.xml', sitemap, {'sitemaps': sitemaps}, name='sitemap'),

    path('privacy/', PrivacyPolicyView.as_view(), name='privacy'),
    path('terms/', TermsOfServiceView.as_view(), name='terms'),
    path('about/', AboutView.as_view(), name='about'),
    path('contact/', ContactView.as_view(), name='contact'),
    path('beta-access/', TemplateView.as_view(template_name='pages/beta_access_required.html'), name='beta_access_required'),

    # Public forum signature images (no auth required)
    path('sig/<uuid:token>.<str:ext>', serve_profile_sig, name='profile_sig'),

    # Arcade (mini-games)
    path('arcade/stellar-circuit/', TemplateView.as_view(template_name='minigames/stellar-circuit.html'), name='stellar_circuit'),

    path('users/', include('users.urls')),
    path('accounts/', include('allauth.urls')),
    path('api/v1/', include('api.urls')),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

handler404 = TemplateView.as_view(template_name='404.html')