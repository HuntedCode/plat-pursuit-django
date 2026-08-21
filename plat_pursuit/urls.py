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
from django.contrib.sitemaps.views import sitemap, index as sitemap_index
from django.urls import path, include
from django.views.generic import RedirectView, TemplateView
from core.views import AdsTxtView, RobotsTxtView, PrivacyPolicyView, TermsOfServiceView, AboutView, ContactView, HomeView, FrameComponentTestView, BinderPreviewView, BadgeCollectionListView, BadgePresentationView, RequirementsChecklistWorkshopView, StageCardsWorkshopView, GameCardWorkshopView, BadgeJourneyWorkshopView, ChromeWorkshopView, RecapStageWorkshopView, PursuerCardPreviewView, PursuerCardRanksPreviewView, PursuerCardCustomizationPreviewView, JobsWorkshopView, LabWorkshopView, ResearchPanelView as DesignResearchPanelView, csp_report_ingest, CspViolationsView, CspViolationsClearView
from core.sitemaps import (
    StaticViewSitemap, GameSitemap, ProfileSitemap,
    BadgeSitemap, RoadmapSitemap,
)

sitemaps = {
    'static': StaticViewSitemap,
    'games': GameSitemap,
    'profiles': ProfileSitemap,
    'badges': BadgeSitemap,
    'roadmaps': RoadmapSitemap,
    # 'lists': GameListSitemap — dropped while Game Lists is hidden; the class stays in core/sitemaps.py
    # for the revamp, since nothing else about the system was deleted.
}
from trophies.views import GamesListView, GameDetailView, GameLeaderboardView, RandomGameView, ProfilesListView, SearchView, ProfileDetailView, ProfileDayView, TrophyCaseView, ToggleSelectionView, BadgeHowItWorksView, BadgeListView, BadgeDetailView, GroupBadgeInspectView, ProfileSyncStatusView, TriggerSyncView, SearchSyncProfileView, AddSyncStatusView, ProfileSuggestView, SiteSuggestView, LinkPSNView, ProfileVerifyView, TokenMonitoringView, BadgeSeriesCreationView, BadgeRanksPanelView, OverallBadgeLeaderboardsView, LeaderboardRowsView, CommentModerationView, ModerationActionView, ModerationLogView, GameFamilyManagementView, ReviewModerationView, ReviewModerationActionView, ReviewModerationLogView, MyTitlesView, RateMyGamesView, ReviewsArchivedView, RoadmapDetailView, RoadmapEditorView, PlatCardsView, RecentlyAddedView, CompanyListView, CompanyDetailView, FranchiseListView, FranchiseDetailView, GenreThemeListView, GenreDetailView, ThemeDetailView, LegacyChecklistListView, LegacyChecklistDetailView, CareerView, JobsBrowseView, JobDetailView, JobRanksPanelView, JobContractsView, ContractsResultsView, ContractModalView, ContractModalPreviewView, CollectionView, CollectionBadgeModalView
from milestones.views import MilestoneListView   # new milestones app (replaces the legacy trophies view)
from trophies.recap_views import RecapIndexView, RecapSlideView
from users.views import CustomConfirmEmailView, stripe_webhook, paypal_webhook, SupportStorefrontView, GiftRedeemView
from users.subscription_admin_views import SubscriptionAdminView
from fundraiser.views import FundraiserView, DonationSuccessView, FundraiserAdminView, BadgeRevealView
# Notifications are HIDDEN pending their rebuild (2026-08); every view in `notifications/views.py` is
# parked unrouted. See the redirect block further down for why the URL names survive.

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
    # The Community hub was retired 2026-08: Challenges retired, Reviews archived, Lists hidden,
    # Profiles moved to Browse, Rate My Games to My Pursuit, and Leaderboards promoted to their own
    # hub -- which left a landing page with nothing of its own to land on. Deliberately UNNAMED, so
    # a `{% url 'community_hub' %}` cannot quietly reappear; this exists for inbound links only.
    path('community/', RedirectView.as_view(
        pattern_name='overall_badge_leaderboards', permanent=True, query_string=True)),
    # The Support landing IS the membership storefront (not a table of contents pointing at one), so
    # it serves the checkout form and answers its POST. Lives in `users.views` because that is where
    # the subscription services and the Stripe/PayPal knowledge already are.
    path('support/', SupportStorefrontView.as_view(), name='support_hub'),
    path('support/redeem/', GiftRedeemView.as_view(), name='gift_redeem'),

    path('games/', GamesListView.as_view(), name='games_list'),
    path('games/lucky/', RandomGameView.as_view(), name='random_game'),
    path('games/recently-added/', RecentlyAddedView.as_view(), name='recently_added'),
    # Editor: per-CTG. The bare /edit/ form opens the base-game roadmap;
    # the /<group_id>/edit/ form opens a specific DLC's roadmap. Each is
    # its own session/lock so DLC writers don't block base-game writers.
    # Path order matters: /edit/ before /<group_id>/ so 'edit' isn't
    # captured as a trophy_group_id.
    path('games/<str:np_communication_id>/roadmap/edit/', RoadmapEditorView.as_view(), name='roadmap_edit'),
    path('games/<str:np_communication_id>/roadmap/<str:trophy_group_id>/edit/', RoadmapEditorView.as_view(), name='roadmap_edit_ctg'),
    path('games/<str:np_communication_id>/roadmap/', RoadmapDetailView.as_view(), name='roadmap_detail'),
    path('games/<str:np_communication_id>/roadmap/<str:trophy_group_id>/', RoadmapDetailView.as_view(), name='roadmap_detail_dlc'),

    # Company pages
    path('companies/', CompanyListView.as_view(), name='companies_list'),
    path('companies/<slug:slug>/', CompanyDetailView.as_view(), name='company_detail'),

    # Franchise pages
    path('franchises/', FranchiseListView.as_view(), name='franchises_list'),
    path('franchises/<slug:slug>/', FranchiseDetailView.as_view(), name='franchise_detail'),

    # Badge pages: the public discovery CATALOG (find/search badges). The personal badge
    # album (Collection) is a separate, login-gated surface at /my-pursuit/collection/.
    path('badges/', BadgeListView.as_view(), name='badges_list'),
    # The badge teaching, at a real address. It lived only in a first-run modal on the page above, so the
    # vocabulary the whole system speaks ("Ultra HD", "Legacy HD") could not be linked, indexed, or reached
    # from the three surfaces that render those names off PlatformGroup without explaining them: the badge
    # detail group tabs, the gallery's platform filter chips, and the Collection's edition stats.
    path('badges/how-it-works/', BadgeHowItWorksView.as_view(), name='badge_how_it_works'),
    # Public quick-peek modal (fetched on tap from the Series/Gallery medallions). Deliberately NOT under
    # /badges/<x>/<y>/ -- that shape is the profile-scoped detail pattern the Cloudflare-bypass guard
    # redirects, and it collides with badge_detail_with_profile. A distinct top-level path sidesteps both.
    # Profile-aware badge peek (badge detail): the DISPLAYED profile's real state. Username first so the URL
    # ends in the badge id (0) that the peek JS substitutes.
    # NEW grouping-badge inspect (Legacy HD / Ultra HD): same top-level shape as the tier peeks, keyed on the
    # GroupBadge id. Public showcase + the profile-aware variant; the badge id (0) is substituted by the peek JS.
    path('group-badge-peek/<int:group_badge_id>/', GroupBadgeInspectView.as_view(), name='group_badge_quick_peek'),
    path('group-badge-progress-peek/<str:psn_username>/<int:group_badge_id>/', GroupBadgeInspectView.as_view(), name='group_badge_progress_peek'),
    # The per-series board, fetched into badge detail's Ranks section. TOP-LEVEL on purpose, for the same
    # reason as the peek routes above: `/badges/<x>/<y>/` is the profile-scoped shape that the
    # Cloudflare-bypass guard redirects (middleware.py) and that `badge_detail_with_profile` claims, so an
    # endpoint there would 302 before it ever reached the view.
    path('badge-ranks/<str:series_slug>/', BadgeRanksPanelView.as_view(), name='badge_ranks_panel'),
    path('badges/<str:series_slug>/', BadgeDetailView.as_view(), name='badge_detail'),
    path('badges/<str:series_slug>/<str:psn_username>/', BadgeDetailView.as_view(), name='badge_detail_with_profile'),

    # Jobs -- the PUBLIC catalogue, in the BROWSE hub (not Leaderboards). Career:jobs is Collection:Browse
    # Badges -- scope, not pagination: Career shows YOUR standing across them, this shows what they are.
    path('jobs/', JobsBrowseView.as_view(), name='jobs_browse'),
    path('jobs/<slug:slug>/', JobDetailView.as_view(), name='job_detail'),
    # The board, fetched into job detail's Ranks tab on activation. Its own endpoint for the same reason
    # `badge_ranks_panel` and game detail's leaderboard panel have one: the cost scales with the job's
    # popularity and most visitors come for the contracts, so the page ships the cheap panel and fetches
    # the expensive one. MUST precede nothing in particular -- `<slug:slug>` above cannot swallow it,
    # since that pattern has no trailing segment.
    path('jobs/<slug:slug>/ranks/', JobRanksPanelView.as_view(), name='job_ranks_panel'),
    # Cards-only page N for job detail's Contracts tab. PUBLIC, unlike Career's `contracts_results`:
    # this is the same catalogue an anonymous visitor already sees on the page, so gating the second
    # screenful would stop the tab halfway down for the readers it exists to persuade.
    path('jobs/<slug:slug>/contracts/', JobContractsView.as_view(), name='job_contracts'),

    # Genre/Theme pages
    path('genres/', GenreThemeListView.as_view(), name='genres_list'),
    path('genres/<slug:slug>/', GenreDetailView.as_view(), name='genre_detail'),
    path('themes/<slug:slug>/', ThemeDetailView.as_view(), name='theme_detail'),

    # Retired engine pages -> Browse games (301; keeps bookmarks / inbound links alive). Names kept so any
    # lingering reverse('engines_list'/'engine_detail') degrades to a redirect, not a 500.
    path('engines/', RedirectView.as_view(pattern_name='games_list', permanent=True), name='engines_list'),
    path('engines/<slug:slug>/', RedirectView.as_view(url='/games/', permanent=True), name='engine_detail'),
    # MUST precede game_detail_with_profile below, which would otherwise match 'leaderboard' as a
    # psn_username -- the same reason the roadmap sub-paths are declared above.
    path('games/<str:np_communication_id>/leaderboard/', GameLeaderboardView.as_view(), name='game_leaderboard'),
    path('games/<str:np_communication_id>/', GameDetailView.as_view(), name='game_detail'),
    path('games/<str:np_communication_id>/<str:psn_username>/', GameDetailView.as_view(), name='game_detail_with_profile'),
    # Retired trophies-list page -> Browse games (301).
    path('trophies/', RedirectView.as_view(pattern_name='games_list', permanent=True), name='trophies_list'),

    # Hunters -- the people section (browse + detail + trophy case).
    #
    # Two moves in 2026-08. First out from under /community/ when that hub was retired (they belong with
    # the other things you BROWSE); then /profiles/ -> /hunters/, when the section was renamed to match
    # what the site actually calls these people everywhere else. These are indexed pages with their own
    # sitemap -- the largest indexed set on the site -- so every old path redirects permanently.
    #
    # The URL NAMES deliberately keep their `profile*` spelling. They are internal handles reversed from
    # templates, services, the sitemap, the sub-nav map and the tests; renaming them would touch all of
    # that to change a string nobody outside the codebase sees, and every one of those call sites is a
    # chance to typo a `{% url %}` into a 500. The PATH is the thing users and Google see.
    #
    # Every redirect below points at a `pattern_name`, which resolves at REQUEST time -- so the
    # /community/ wave re-aimed itself at /hunters/ the moment the canonical moved. That is what keeps
    # this a single hop from any old URL instead of a 301 chain through /profiles/.
    path('hunters/', ProfilesListView.as_view(), name='profiles_list'),
    # A day of activity. Addressable so it can be crawled, linked and reached without JS; the modal
    # fetches this same URL.
    path('hunters/<str:psn_username>/day/<str:day>/', ProfileDayView.as_view(), name='profile_day'),
    path('hunters/<str:psn_username>/', ProfileDetailView.as_view(), name='profile_detail'),
    path('hunters/<str:psn_username>/trophy-case/', TrophyCaseView.as_view(), name='trophy_case'),
    path('profiles/', RedirectView.as_view(
        pattern_name='profiles_list', permanent=True, query_string=True)),
    path('community/profiles/', RedirectView.as_view(
        pattern_name='profiles_list', permanent=True, query_string=True)),
    path('community/profiles/<str:psn_username>/', RedirectView.as_view(
        pattern_name='profile_detail', permanent=True, query_string=True)),
    path('community/profiles/<str:psn_username>/trophy-case/', RedirectView.as_view(
        pattern_name='trophy_case', permanent=True, query_string=True)),

    # My Pursuit hub: the personal Pursuer surfaces (canonical paths under /my-pursuit/).
    # The badge CATALOG (list + detail) was re-homed to the Browse hub at /badges/ -- it is a
    # public discovery surface, not personal. The personal badge album (Collection) stays here.
    #
    # The bare /my-pursuit/ path 301-redirects to its landing sub-page, the Collection (the
    # sub-nav strip handles wayfinding to The Lab, Milestones, Titles, etc.). My Pursuit
    # deliberately has no separate landing page; the Collection IS the landing, mirroring how
    # /games/ is the Browse hub landing rather than a dedicated Browse landing page.
    # Personal-hub unify: the personal Pursuer surfaces now live at ROOT paths. The logged-in
    # Home (/) is the hub Overview; the sub-nav strip does wayfinding to these. Old /my-pursuit/*
    # paths 301 to them (below), and the bare /my-pursuit/ now points at Home.
    path('collection/', CollectionView.as_view(), name='badge_collection'),
    path('collection/badge/<int:badge_id>/', CollectionBadgeModalView.as_view(), name='collection_badge_modal'),
    path('career/', CareerView.as_view(), name='career'),
    # Contracts board: cards partial (filter-swap + infinite scroll) and lazy per-contract modal.
    path('career/contracts/results/', ContractsResultsView.as_view(), name='contracts_results'),
    path('career/contracts/<slug:slug>/modal/', ContractModalView.as_view(), name='contract_modal'),
    path('career/contracts/<slug:slug>/preview/', ContractModalPreviewView.as_view(), name='contract_modal_preview'),
    # Merged into Career: /research-panel/ 301s to Career's Contracts tab (one surface, one URL).
    path('research-panel/', RedirectView.as_view(url='/career/?view=contracts', permanent=True), name='research_panel'),
    path('milestones/', MilestoneListView.as_view(), name='milestones_list'),
    path('titles/', MyTitlesView.as_view(), name='my_titles'),
    # ── Profile customization: HIDDEN pending a ground-up rebuild ────────────────────────────────
    # The showcase band is gone from the profile (see profile_detail.html) and this is the surface that
    # edited it. TEMPORARY on purpose, so `permanent=False` (302): a 301 is cached by browsers
    # indefinitely and would keep bouncing exactly the people who used customization most, since they
    # are the ones holding the bookmark.
    #
    # The NAME stays resolvable -- the parked showcase section reverses it twice -- and the view, its
    # template, its JS controller, the service and every row of user data are all intact. This is a
    # curtain, not a demolition.
    path('profile-editor/', RedirectView.as_view(url='/', permanent=False), name='profile_editor'),
    # Old /my-pursuit/* → new root canonicals (301). Bare hub path + logbook alias kept by name.
    path('my-pursuit/', RedirectView.as_view(pattern_name='home', permanent=True), name='my_pursuit_hub'),
    path('my-pursuit/collection/', RedirectView.as_view(pattern_name='badge_collection', permanent=True, query_string=True)),
    path('my-pursuit/lab/', RedirectView.as_view(pattern_name='career', permanent=True, query_string=True)),
    path('my-pursuit/logbook/', RedirectView.as_view(pattern_name='career', permanent=True), name='logbook'),
    path('my-pursuit/research-panel/', RedirectView.as_view(pattern_name='research_panel', permanent=True, query_string=True)),
    path('my-pursuit/milestones/', RedirectView.as_view(pattern_name='milestones_list', permanent=True, query_string=True)),
    path('my-pursuit/titles/', RedirectView.as_view(pattern_name='my_titles', permanent=True, query_string=True)),
    # Straight to the homepage rather than through `profile_editor`, which now 302s there itself -- no
    # double hop. Still 301, because the /my-pursuit/ -> root move IS permanent whatever happens to the
    # editor. `query_string` dropped with it: there is no longer a target that reads one.
    path('my-pursuit/profile-editor/', RedirectView.as_view(url='/', permanent=True)),

    # Dashboard hub: personal-utility pages live under /dashboard/.
    # The original Phase 10 commit put these under /tools/. The Phase 10a
    # rework relocates them to /dashboard/ because they're personal-cockpit
    # features that belong in the Dashboard hub (see ia-and-subnav.md). The
    # Platinum Grid wizard lives nested inside Shareables since it generates
    # one of the shareable image types.
    # My Stats is HIDDEN for the 1.0 launch (see docs/design/stats-page.md). Bookmarks and the older
    # /my-stats/ + /tools/stats/ + /dashboard/stats/ redirects all land here, so this bounces to Home
    # rather than 404ing or dumping the visitor on a login screen. TEMPORARY (302, not 301) on purpose:
    # a permanent redirect gets cached by the browser, and this page is coming back rebuilt --
    # `MyStatsView` is parked (unrouted) in trophies/views/stats_views.py until then.
    path('stats/', RedirectView.as_view(pattern_name='home', permanent=False), name='my_stats'),
    # Shareables: landing + dedicated sub-pages for each share type (moved from /dashboard/).
    # See trophies/views/shareables_views.py for the per-view docstrings.
    # /shareables/ IS the Plat Cards page now. The 4-card wayfinder landing that used to sit here
    # distributed to three surfaces that no longer exist, and /shareables/platinums/ was the browse it
    # pointed at -- so the two collapsed into one. The old sub-path keeps its name and redirects, since
    # platinum-earned notifications deep-link it with ?et=<id>; TEMPORARY, because if the page ever
    # regains siblings this path is where the browse would live again.
    path('shareables/', PlatCardsView.as_view(), name='my_shareables'),
    path('shareables/platinums/', RedirectView.as_view(pattern_name='my_shareables', permanent=False, query_string=True), name='my_shareables_platinums'),
    # Profile Card + Platinum Grid are RETIRED (2026-08): this surface serves plat cards only.
    # Both bounce to the shareables landing rather than 404ing, and stay TEMPORARY (302) because the
    # views/templates/JS are parked for a possible revival under the new card design -- a cached 301
    # would strand returning users. See docs/features/share-images.md.
    path('shareables/profile-card/', RedirectView.as_view(pattern_name='my_shareables', permanent=False), name='my_shareables_profile_card'),
    path('shareables/platinum-grid/', RedirectView.as_view(pattern_name='my_shareables', permanent=False), name='platinum_grid'),
    # Old /dashboard/stats|shareables/* → new root canonicals (301).
    path('dashboard/stats/', RedirectView.as_view(pattern_name='my_stats', permanent=True, query_string=True)),
    path('dashboard/shareables/', RedirectView.as_view(pattern_name='my_shareables', permanent=True, query_string=True)),
    path('dashboard/shareables/platinums/', RedirectView.as_view(pattern_name='my_shareables_platinums', permanent=True, query_string=True)),
    path('dashboard/shareables/profile-card/', RedirectView.as_view(pattern_name='my_shareables_profile_card', permanent=True, query_string=True)),
    path('dashboard/shareables/platinum-grid/', RedirectView.as_view(pattern_name='platinum_grid', permanent=True, query_string=True)),

    # ── Notifications: HIDDEN pending their rebuild (2026-08) ────────────────────────────────────────
    # The system comes back rebuilt to the current standard rather than being restyled, so every door is
    # closed while the models, the data and every PRODUCER (sync, badges, reviews, roadmap notes,
    # donations, subscriptions) stay exactly as they are. Rows keep accruing behind the closed door,
    # which is what makes this reversible: restoring it is putting back these five patterns, the API
    # block in api/urls.py, and the navbar bell.
    #
    # 302, never 301: a permanent redirect is cached indefinitely and would keep bouncing people to the
    # homepage long after the rebuild ships -- specifically the people who used notifications most,
    # because they are the ones holding the bookmarks.
    #
    # The NAMES stay resolvable. The parked views in notifications/views.py still `redirect(
    # 'admin_notification_center')` in eleven places, and the parked templates still `{% url %}` these
    # names -- none of it is reachable today, but keeping the names resolvable is what makes putting the
    # curtain back a matter of restoring routes rather than editing the parked code.
    #
    # Nothing REACHABLE links in any more. Three staff pages (the subscription dashboard, the fundraiser
    # admin, the badge reveal) carried a "Notifications" button that would have dead-ended on the
    # homepage; those were removed with the rest of the chrome.
    path('notifications/', RedirectView.as_view(pattern_name='home', permanent=False), name='notification_inbox'),

    # Leaderboards -- their own hub as of 2026-08 (they were the substance left in Community).
    # `/leaderboards/` is the landing; the type segment stays on the per-series route so a second kind of
    # leaderboard can land beside `badges/` without colliding with a series slug.
    path('leaderboards/', OverallBadgeLeaderboardsView.as_view(), name='overall_badge_leaderboards'),
    # A WINDOW of the active board, for the virtualized wall. Bare `.lb-row` elements -- the engine
    # splices them into its spacer, so any wrapper would be parsed and thrown away. MUST precede nothing
    # in particular; `leaderboards/` above is an exact match and cannot swallow it.
    path('leaderboards/rows/', LeaderboardRowsView.as_view(), name='leaderboard_rows'),
    # The three board DIRECTORIES (`leaderboards/badges|games|jobs/`) were removed in 2026-08 without
    # redirects, having never left a dev machine. Each was a catalogue of the entities `/games/`,
    # `/badges/` and `/jobs/` already catalogue, distinguished only by a sort those browse pages already
    # had -- `played_count` on Browse Games, "Most earned" on Browse Badges, a hunter count on every job
    # card -- plus a min-entrants gate that only ever HID entities. Nothing linked to them but the hub
    # rail, which existed because they did. Boards live on the thing they rank; see the per-entity Ranks
    # panels on game, badge and job detail.
    #
    # `leaderboards/badges/` keeps a redirect to the landing, which is what it did BEFORE the directory
    # took the path: the per-series redirect below is still live, so somebody chopping that URL back to
    # its parent needs somewhere to land rather than a 404. It must stay ABOVE that pattern, which would
    # otherwise capture nothing here but reads confusingly out of order.
    path('leaderboards/badges/', RedirectView.as_view(
        pattern_name='overall_badge_leaderboards', permanent=True, query_string=True)),
    # Retired 2026-08: the per-series board moved onto badge detail. Permanent, and it keeps the slug, so
    # every existing link lands on the badge whose board it wanted rather than on a generic index.
    path('leaderboards/badges/<str:series_slug>/', RedirectView.as_view(
        pattern_name='badge_detail', permanent=True, query_string=True)),

    # So the per-series route has a resolvable parent rather than a 404 above it.
    # The paths they lived at under Community. RedirectView resolves `pattern_name` at REQUEST time, so
    # the older /leaderboard/badges/* redirects further down already point straight at the new canonicals
    # -- no chain to collapse.
    path('community/leaderboards/badges/', RedirectView.as_view(
        pattern_name='overall_badge_leaderboards', permanent=True, query_string=True)),
    path('community/leaderboards/badges/<str:series_slug>/', RedirectView.as_view(
        pattern_name='badge_detail', permanent=True, query_string=True)),

    # Guide/checklist URLs - all redirected to home (system removed, replaced by roadmaps)
    path('guides/', RedirectView.as_view(pattern_name='home', permanent=False), name='guides_browse'),
    # The three that capture arguments use `url='/'` -- see the note on `admin_cancel_scheduled` below.
    # `pattern_name='home'` 500s here, because the captured id is forwarded into a reverse() that takes none.
    path('guides/<int:guide_id>/', RedirectView.as_view(url='/', permanent=False), name='guide_detail'),
    path('guides/<int:guide_id>/edit/', RedirectView.as_view(url='/', permanent=False), name='guide_edit'),
    path('guides/create/<int:concept_id>/<str:np_communication_id>/', RedirectView.as_view(url='/', permanent=False), name='guide_create'),
    path('my-guides/', RedirectView.as_view(pattern_name='home', permanent=False), name='my_guides'),

    # Game Lists (canonical paths under /community/lists/)
    # ── Game Lists: HIDDEN pending a revamp ──────────────────────────────────────────────────────
    # Every entry point is gone (sub-nav, footer, community hub, sitemap, and the add-to-list button on
    # game cards), and these send anyone arriving on an old link or bookmark to the homepage instead.
    #
    # TEMPORARY on purpose, so `permanent=False` (302). A 301 is cached by browsers indefinitely and
    # would keep redirecting to the homepage long after the rebuilt system ships -- for exactly the
    # people who used lists most, since they are the ones holding the bookmarks.
    #
    # The NAMES stay resolvable. Templates that are no longer reachable still contain
    # `{% url 'list_detail' %}`, and the views, models, data and the rebuilt browse page are all intact:
    # this is a curtain, not a demolition. Restoring it is putting these four lines back.
    path('community/lists/', RedirectView.as_view(url='/', permanent=False), name='lists_browse'),
    path('community/lists/create/', RedirectView.as_view(url='/', permanent=False), name='list_create'),
    path('community/lists/<int:list_id>/', RedirectView.as_view(url='/', permanent=False), name='list_detail'),
    path('community/lists/<int:list_id>/edit/', RedirectView.as_view(url='/', permanent=False), name='list_edit'),
    path('my-lists/', RedirectView.as_view(url='/', permanent=False), name='my_lists'),

    # Rate My Games wizard (ratings-only). Rehoused 2026-08 from /community/ to a root path under the
    # My Pursuit hub: it is login-required, noindex, and works only on YOUR library -- a personal tool
    # that happens to produce community data, the same shape as Plat Cards and Recap beside it. Root path
    # matches the rest of that hub (/collection/, /career/, /shareables/, /recap/).
    path('rate-my-games/', RateMyGamesView.as_view(), name='rate_my_games'),
    # The path it lived at for its whole life. Permanent: this move is not coming back.
    path('community/rate-my-games/', RedirectView.as_view(
        pattern_name='rate_my_games', permanent=True, query_string=True)),

    # Reviews ARCHIVED (2026-05). The former Review Hub URLs now serve a
    # notice page; the detail route redirects to it. URL names are kept so
    # the many lingering `{% url 'reviews_landing'/'review_hub' %}` refs
    # resolve to the notice instead of 500ing. The old hub views remain in
    # the tree, dormant, for a possible future rebuild.
    path('community/reviews/', ReviewsArchivedView.as_view(), name='reviews_landing'),
    path('community/reviews/rate-my-games/', RedirectView.as_view(pattern_name='rate_my_games', permanent=True, query_string=True)),
    # Use a literal `url` (not `pattern_name`) so the captured <slug> is
    # dropped. RedirectView forwards URL kwargs into reverse(), and
    # `reviews_landing` takes no slug → NoReverseMatch → 500 (the archival bug).
    path('community/reviews/<slug:slug>/', RedirectView.as_view(url='/community/reviews/', permanent=False, query_string=True), name='review_hub'),

    # Monthly Recap (canonical ROOT paths, moved from /dashboard/recap/).
    path('recap/', RecapIndexView.as_view(), name='recap_index'),
    path('recap/<int:year>/<int:month>/', RecapSlideView.as_view(), name='recap_view'),
    path('dashboard/recap/', RedirectView.as_view(pattern_name='recap_index', permanent=True, query_string=True)),
    path('dashboard/recap/<int:year>/<int:month>/', RedirectView.as_view(pattern_name='recap_view', permanent=True, query_string=True)),

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

    # These two were DEAD until the /profiles/ -> /hunters/ rename (2026-08): the canonical
    # `profiles/<psn_username>/` pattern sat higher in this list and matched first, so they could never be
    # reached -- and had they been reached they would have redirected /profiles/<u>/ to itself. Moving the
    # canonical to /hunters/ is what made them both reachable AND correct. Do not "tidy" them away; they
    # are now the only thing catching the old profile-detail URLs, which are the site's largest indexed set.
    path('profiles/<str:psn_username>/', RedirectView.as_view(pattern_name='profile_detail', permanent=True, query_string=True)),
    path('profiles/<str:psn_username>/trophy-case/', RedirectView.as_view(pattern_name='trophy_case', permanent=True, query_string=True)),

    # My Pursuit hub legacy paths. The badge CATALOG re-homed to Browse /badges/, so the
    # Phase-10a /my-pursuit/badges/* paths now 301 to it. (The pre-Phase-10 /badges/* paths
    # ARE the canonical now -- no redirect. The /achievements/badges/* wave still 301s below.)
    path('my-pursuit/badges/', RedirectView.as_view(pattern_name='badges_list', permanent=True, query_string=True)),
    path('my-pursuit/badges/<str:series_slug>/', RedirectView.as_view(pattern_name='badge_detail', permanent=True, query_string=True)),
    path('my-pursuit/badges/<str:series_slug>/<str:psn_username>/', RedirectView.as_view(pattern_name='badge_detail_with_profile', permanent=True, query_string=True)),
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

    # Leaderboards
    path('leaderboard/badges/', RedirectView.as_view(pattern_name='overall_badge_leaderboards', permanent=True, query_string=True)),
    path('leaderboard/badges/<str:series_slug>/', RedirectView.as_view(pattern_name='badge_detail', permanent=True, query_string=True)),

    # Game Lists (hidden -- see the note above). These already 301'd to the /community/lists/ paths, which
    # now 302 to the homepage; sending them straight there saves the double hop. Kept as 301 because the
    # /lists/ -> /community/lists/ move IS permanent, whatever happens to the system itself.
    path('lists/', RedirectView.as_view(url='/', permanent=True)),
    path('lists/create/', RedirectView.as_view(url='/', permanent=True)),
    path('lists/<int:list_id>/', RedirectView.as_view(url='/', permanent=True)),
    path('lists/<int:list_id>/edit/', RedirectView.as_view(url='/', permanent=True)),


    # Reviews
    path('reviews/', RedirectView.as_view(pattern_name='reviews_landing', permanent=True, query_string=True)),
    path('reviews/rate-my-games/', RedirectView.as_view(pattern_name='rate_my_games', permanent=True, query_string=True)),
    path('reviews/<slug:slug>/', RedirectView.as_view(pattern_name='review_hub', permanent=True, query_string=True)),

    path('search/', SearchView.as_view(), name='search'),
    path('logout/', LogoutView.as_view(template_name='account/logout.html'), name='logout'),

    path('staff/moderation/', CommentModerationView.as_view(), name='comment_moderation'),
    path('staff/moderation/action/<int:report_id>/', ModerationActionView.as_view(), name='moderation_action'),
    path('staff/moderation/log/', ModerationLogView.as_view(), name='moderation_log'),
    path('staff/review-moderation/', ReviewModerationView.as_view(), name='review_moderation'),
    path('staff/review-moderation/action/<int:report_id>/', ReviewModerationActionView.as_view(), name='review_moderation_action'),
    path('staff/review-moderation/log/', ReviewModerationLogView.as_view(), name='review_moderation_log'),
    path('staff/game-families/', GameFamilyManagementView.as_view(), name='game_family_management'),
    # Notification staff pages: HIDDEN with the rest of the system (see the note on `notification_inbox`
    # above). Names kept so the parked views' own redirects still reverse.
    path('staff/notifications/', RedirectView.as_view(pattern_name='home', permanent=False), name='admin_notification_center'),
    path('staff/notifications/history/', RedirectView.as_view(pattern_name='home', permanent=False), name='admin_notification_history'),
    path('staff/notifications/scheduled/', RedirectView.as_view(pattern_name='home', permanent=False), name='admin_scheduled_notifications'),
    # `url='/'`, NOT `pattern_name='home'`: RedirectView forwards captured kwargs into reverse(), and
    # `home` takes none -- so the pattern_name form raises NoReverseMatch (a hard 500, ungated) on every
    # hit. Any redirect on a path that captures something has to use this form.
    path('staff/notifications/scheduled/<int:pk>/cancel/', RedirectView.as_view(url='/', permanent=False), name='admin_cancel_scheduled'),
    path('staff/subscriptions/', SubscriptionAdminView.as_view(), name='subscription_admin'),
    # Bookmark-only staff analytics dashboard. Not linked from nav.
    # CSP violation reporting. Ingest endpoint MUST live at the project root
    # (not under /staff/ or /api/) since browsers POST reports without auth.
    path('csp-report/', csp_report_ingest, name='csp_report'),
    path('staff/csp-violations/', CspViolationsView.as_view(), name='staff_csp_violations'),
    path('staff/csp-violations/clear/', CspViolationsClearView.as_view(), name='staff_csp_violations_clear'),
    path('staff/fundraiser/', FundraiserAdminView.as_view(), name='fundraiser_admin'),
    # Keeps the pre-cutover route name: any staff bookmark still resolves.
    path('staff/badge-create/', BadgeSeriesCreationView.as_view(), name='badge_creation'),
    path('staff/badge-reveal/', BadgeRevealView.as_view(), name='badge_reveal'),
    # Read-only browser for the deprecated Checklist system (tables retained
    # after the Roadmap migration). Not linked from nav.
    path('staff/legacy-checklists/', LegacyChecklistListView.as_view(), name='legacy_checklist_list'),
    path('staff/legacy-checklists/<int:pk>/', LegacyChecklistDetailView.as_view(), name='legacy_checklist_detail'),
    # NOTE: the Platinum Grid wizard is RETIRED (2026-08). /shareables/platinum-grid/ bounces to
    # the shareables landing; the legacy /staff/platinum-grid/ + /tools/platinum-grid/ 301s funnel
    # into that bounce. PlatinumGridView is parked unrouted in trophies/views/platinum_grid_views.py.

    # Fundraiser
    path('fundraiser/<slug:slug>/', FundraiserView.as_view(), name='fundraiser'),
    path('fundraiser/<slug:slug>/success/', DonationSuccessView.as_view(), name='fundraiser_success'),

    path('api/profile-verify/', ProfileVerifyView.as_view(), name='profile_verify'),
    path('api/trigger-sync/', TriggerSyncView.as_view(), name='trigger_sync'),
    path('api/profile-sync-status/', ProfileSyncStatusView.as_view(), name='profile_sync_status'),
    path('api/search-sync-profile/', SearchSyncProfileView.as_view(), name='search_sync_profile'),
    path('api/add-sync-status/', AddSyncStatusView.as_view(), name='add_sync_status'),
    path('api/profile-suggest/', ProfileSuggestView.as_view(), name='profile_suggest'),
    path('api/site-suggest/', SiteSuggestView.as_view(), name='site_suggest'),

    path('accounts/link-psn/', LinkPSNView.as_view(), name='link_psn'),
    path('accounts/confirm-email/<str:key>/', CustomConfirmEmailView.as_view(), name='account_confirm_email'),

    path('monitoring/tokens/', TokenMonitoringView.as_view(), name='token_monitoring'),

    path("stripe/webhook/", stripe_webhook, name="stripe_webhook"),
    path("paypal/webhook/", paypal_webhook, name="paypal_webhook"),
    path('ads.txt', AdsTxtView.as_view(), name='ads_txt'),
    path('robots.txt', RobotsTxtView.as_view(), name='robots_txt'),
    # Sitemap index: /sitemap.xml returns the index of section sitemaps,
    # crawlers fetch each /sitemap-<section>.xml on demand. This bounds the
    # per-request memory cost and matches sitemap-protocol best practices
    # for sites with tens of thousands of URLs. (Bots that hit /sitemap.xml
    # directly used to materialize every Game/Profile row at once for an
    # ~160 MB allocation per fetch — the May 2026 OOM contributor.)
    path(
        'sitemap.xml',
        sitemap_index,
        {'sitemaps': sitemaps, 'sitemap_url_name': 'sitemap_section'},
        name='sitemap',
    ),
    path(
        'sitemap-<section>.xml',
        sitemap,
        {'sitemaps': sitemaps},
        name='sitemap_section',
    ),

    path('privacy/', PrivacyPolicyView.as_view(), name='privacy'),
    path('terms/', TermsOfServiceView.as_view(), name='terms'),
    path('about/', AboutView.as_view(), name='about'),
    path('contact/', ContactView.as_view(), name='contact'),
    path('beta-access/', TemplateView.as_view(template_name='pages/beta_access_required.html'), name='beta_access_required'),

    # Public forum signature images (no auth required)

    # Arcade (mini-games)
    path('arcade/stellar-circuit/', TemplateView.as_view(template_name='minigames/stellar-circuit.html'), name='stellar_circuit'),

    # Design previews (team-facing, publicly accessible by direct link).
    # Used to gather feedback on proposed visual primitives before they're
    # committed to the canonical design system. Not linked from nav.
    path('design/frame/', TemplateView.as_view(template_name='design/frame_preview.html'), name='design_frame_preview'),
    path('design/frame-component/', FrameComponentTestView.as_view(), name='design_frame_component_test'),
    path('design/binder/', BinderPreviewView.as_view(), name='design_binder_preview'),
    path('design/badge-collection/', BadgeCollectionListView.as_view(), name='design_badge_collection_list'),
    path('design/badge-presentation/', BadgePresentationView.as_view(), name='design_badge_presentation'),
    path('design/requirements-checklist/', RequirementsChecklistWorkshopView.as_view(), name='design_requirements_checklist'),
    path('design/stage-cards/', StageCardsWorkshopView.as_view(), name='design_stage_cards'),
    path('design/game-card/', GameCardWorkshopView.as_view(), name='design_game_card'),
    path('design/badge-journey/', BadgeJourneyWorkshopView.as_view(), name='design_badge_journey'),
    path('design/chrome/', ChromeWorkshopView.as_view(), name='design_chrome'),
    path('design/recap-stage/', RecapStageWorkshopView.as_view(), name='design_recap_stage'),
    path('design/tally/', TemplateView.as_view(template_name='design/tally_preview.html'), name='design_tally_preview'),
    path('design/horizon/', TemplateView.as_view(template_name='design/horizon_preview.html'), name='design_horizon_preview'),
    path('design/pursuer-card/', PursuerCardPreviewView.as_view(), name='design_pursuer_card_preview'),
    path('design/pursuer-card-ranks/', PursuerCardRanksPreviewView.as_view(), name='design_pursuer_card_ranks_preview'),
    path('design/pursuer-card-customization/', PursuerCardCustomizationPreviewView.as_view(), name='design_pursuer_card_customization_preview'),
    path('design/pursuer-card-v2/', TemplateView.as_view(template_name='design/pursuer_card_workshop.html'), name='design_pursuer_card_workshop'),
    path('design/pursuer-card-spectral/', TemplateView.as_view(template_name='design/pursuer_card_spectral.html'), name='design_pursuer_card_spectral'),
    path('design/pursuer-card-collection/', TemplateView.as_view(template_name='design/pursuer_card_collection.html'), name='design_pursuer_card_collection'),
    path('design/style-guide/', TemplateView.as_view(template_name='design/style_guide_preview.html'), name='design_style_guide_preview'),
    path('design/jobs/', JobsWorkshopView.as_view(), name='design_jobs_preview'),
    path('design/lab/', LabWorkshopView.as_view(), name='design_lab_preview'),
    path('design/research-panel/', DesignResearchPanelView.as_view(), name='design_research_panel_preview'),
    path('design/mobile-subnav/', TemplateView.as_view(template_name='design/mobile_subnav.html'), name='design_mobile_subnav'),
    path('design/rank-colours/', TemplateView.as_view(template_name='design/rank_colours_preview.html'), name='design_rank_colours_preview'),

    path('users/', include('users.urls')),
    path('accounts/', include('allauth.urls')),
    path('api/v1/', include('api.urls')),
    path('', include('art_reveal.urls')),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

class NotFoundView(TemplateView):
    """404 page that actually returns a 404 status (a plain TemplateView renders it at 200)."""
    template_name = '404.html'

    def render_to_response(self, context, **kwargs):
        kwargs.setdefault('status', 404)
        return super().render_to_response(context, **kwargs)


handler404 = NotFoundView.as_view()