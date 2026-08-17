from django.urls import path
from .views import (
    SummaryView, GenerateCodeView, VerifyView, UnlinkView, CheckLinkedView,
    RefreshView, SyncRolesView, RecheckBadgesView, TrophyCaseView,
    CommentDetailView, CommentVoteView, CommentReportView,
    AgreeToGuidelinesView
)
# Checklist API views removed during roadmap migration (DB tables retained)
# Nine of the ten notification views are no longer imported: their paths are withdrawn while the system
# is hidden, and an import with no route is just a name to trip over later. They are untouched in
# api/notification_views.py -- restoring the system restores this import alongside the paths.
#
# as its user picker, so withdrawing it would silently break an unrelated staff tool. It belongs
# somewhere neutral; rehoming it is a follow-up, not a reason to leave a door open here.
from .shareable_views import (
    PlatCardHTMLView, PlatCardPNGView, LegacyPlatinumCardHTMLView, LegacyPlatinumCardPNGView,
)
# Platinum Grid is RETIRED (2026-08); api/platinum_grid_views.py is parked unrouted.
from .recap_views import (
    RecapAvailableView, RecapDetailView, RecapRegenerateView, RecapShareImageHTMLView,
    RecapShareImagePNGView, RecapSlidePartialView, RecapDeckView
)
from .tracking_views import TrackSiteEventView
from .easter_egg_views import RollEasterEggView
from .share_temp_views import serve_share_temp_image
# The twelve list views are no longer imported: their paths are withdrawn while the lists system is
# hidden, and an import with no route is just a name to trip over later. They are untouched in
# api/game_list_views.py -- restoring the system restores this import alongside the paths.
# GameSearchView stays: it is a general game-search endpoint that happens to live in this module.
from .game_list_views import GameSearchView
from .game_picker_views import GameBackgroundSearchView, ConceptBannerImagesView
from .game_family_views import (
    GameFamilyCreateView, GameFamilyUpdateView, GameFamilyDeleteView,
    GameFamilyAddConceptView, GameFamilyRemoveConceptView,
    ConceptSearchView as GameFamilyConceptSearchView,
)
from .subscription_admin_views import SubscriptionAdminActionView, SubscriptionAdminUserDetailView
from .fundraiser_views import CreateDonationView, ClaimBadgeView, UpdateClaimStatusView
from .title_views import EquipTitleAPIView
from .user_settings_views import UpdateTimezoneAPIView, UpdateQuickSettingsAPIView
from .game_player_views import GamePlayersAPIView
from .game_flag_views import GameFlagView
from .rating_views import GroupRatingView, WizardQueueView, TrophyListView, BlurbReportView
from .roadmap_views import (
    RoadmapPublishView, RoadmapImageUploadView, RoadmapPreviewView,
    RoadmapHiddenAuthorsView, RoadmapTrialWritersView,
)
from .roadmap_lock_views import (
    RoadmapLockAcquireView, RoadmapLockHeartbeatView, RoadmapLockBranchView,
    RoadmapLockReleaseView, RoadmapLockBreakView, RoadmapLockMergeView,
)
from .collectible_progress_views import CollectibleProgressView
from .roadmap_note_views import (
    RoadmapNoteListCreateView, RoadmapNoteDetailView,
    RoadmapNoteResolveView, RoadmapNoteMarkReadView,
)
from .community_stats_views import (
    CommunityStatsDayView, CommunityStatsTodayView, CommunityStatsRecordsView,
)
from .youtube_views import YouTubeAttributionLookupView
from .contract_views import AcceptContractView
from .pursuer_card_views import PursuerCardRefreshView

app_name = 'api'

urlpatterns = [
    path('generate-code/', GenerateCodeView.as_view(), name='generate-code'),
    path('verify/', VerifyView.as_view(), name='verify'),
    path('check-linked/', CheckLinkedView.as_view(), name='check-linked'),
    path('unlink/', UnlinkView.as_view(), name='unlink'),
    path('sync-roles/', SyncRolesView.as_view(), name='sync-roles'),
    path('recheck-badges/', RecheckBadgesView.as_view(), name='recheck-badges'),
    path('refresh/', RefreshView.as_view(), name='refresh'),
    path('summary/', SummaryView.as_view(), name='summary'),
    path('trophy-case/', TrophyCaseView.as_view(), name='trophy-case'),

    # Comment endpoints (generic, not checklist-scoped)
    path('comments/<int:comment_id>/', CommentDetailView.as_view(), name='comment-detail'),
    path('comments/<int:comment_id>/vote/', CommentVoteView.as_view(), name='comment-vote'),
    path('comments/<int:comment_id>/report/', CommentReportView.as_view(), name='comment-report'),

    # Community guidelines
    path('guidelines/agree/', AgreeToGuidelinesView.as_view(), name='guidelines-agree'),

    # ── Notifications: WITHDRAWN while the system is hidden (2026-08) ───────────────────────────────
    # Nine routes gone -- list, mark-read, mark-all-read, bulk-delete, delete, the rating endpoint, and
    # the three admin compose endpoints. Nothing can read or write into a system with no door, which is
    # what the rebuild would otherwise have to reconcile. The views are parked in
    # api/notification_views.py; the models and every producer are untouched.
    #
    # `notification-rating` went with them, which also removes the SECOND server-side writer of
    # UserConceptRating -- `GroupRatingView` is now the only one.
    #
    # The user-search endpoint is the deliberate exception (see the import note above).

    # Plat cards. Keyed on the game's default TrophyGroup -- a card is earned by completing that group,
    # platinum or not (see core/services/completion_card_service.py).
    path('shareables/completion/<int:trophy_group_id>/html/', PlatCardHTMLView.as_view(), name='plat-card-html'),
    path('shareables/completion/<int:trophy_group_id>/png/', PlatCardPNGView.as_view(), name='plat-card-png'),
    # Pre-2026-08 EarnedTrophy-keyed alias. Platinum notifications already sent deep-link this way, and
    # these carry TokenAuthentication, so assume external consumers too. Same card.
    path('shareables/platinum/<int:earned_trophy_id>/html/', LegacyPlatinumCardHTMLView.as_view(), name='shareable-platinum-html'),
    path('shareables/platinum/<int:earned_trophy_id>/png/', LegacyPlatinumCardPNGView.as_view(), name='shareable-platinum-png'),

    # Monthly recap endpoints
    path('recap/available/', RecapAvailableView.as_view(), name='recap-available'),
    path('recap/<int:year>/<int:month>/', RecapDetailView.as_view(), name='recap-detail'),
    path('recap/<int:year>/<int:month>/regenerate/', RecapRegenerateView.as_view(), name='recap-regenerate'),
    path('recap/<int:year>/<int:month>/html/', RecapShareImageHTMLView.as_view(), name='recap-share-html'),
    path('recap/<int:year>/<int:month>/png/', RecapShareImagePNGView.as_view(), name='recap-share-png'),
    path('recap/<int:year>/<int:month>/deck/', RecapDeckView.as_view(), name='recap-deck'),
    path('recap/<int:year>/<int:month>/slide/<str:slide_type>/', RecapSlidePartialView.as_view(), name='recap-slide-partial'),

    # Tracking endpoints
    path('tracking/site-event/', TrackSiteEventView.as_view(), name='tracking-site-event'),

    # Project (Contract) acceptance gate -- banks XP for claimable Projects
    path('projects/accept/', AcceptContractView.as_view(), name='project-accept'),

    # Easter egg endpoints
    path('easter-eggs/roll/', RollEasterEggView.as_view(), name='easter-egg-roll'),

    # Temp share image serving
    path('share-temp/<str:filename>', serve_share_temp_image, name='share-temp-image'),

    # Game list endpoints -- WITHDRAWN while the lists system is hidden.
    #
    # Unrouted rather than left answering: the only caller was the add-to-list button on game cards,
    # which is gone with the rest of the entry points, and an endpoint that still accepts writes into a
    # system nobody can open collects data the revamp then has to reconcile. Checked before pulling
    # them: PlatBot does not call `/api/v1/lists/` (its only "lists" matches are in vendored packages).
    #
    # The views are untouched in api/game_list_views.py; restoring the system is restoring these paths.

    # Game search (for list typeahead)
    path('games/search/', GameSearchView.as_view(), name='game-search'),

    # Game players
    path('games/<str:np_communication_id>/players/', GamePlayersAPIView.as_view(), name='game-players'),

    # Game flags (community data quality reports)
    path('games/<int:game_id>/flag/', GameFlagView.as_view(), name='game-flag'),

    # Game background search (shared by share card + banner picker)
    path('game-backgrounds/', GameBackgroundSearchView.as_view(), name='game-background-search'),
    path('game-backgrounds/<int:concept_id>/images/', ConceptBannerImagesView.as_view(), name='concept-banner-images'),

    # Game Family endpoints (staff-only)
    path('game-families/', GameFamilyCreateView.as_view(), name='game-family-create'),
    path('game-families/<int:family_id>/', GameFamilyUpdateView.as_view(), name='game-family-update'),
    path('game-families/<int:family_id>/delete/', GameFamilyDeleteView.as_view(), name='game-family-delete'),
    path('game-families/<int:family_id>/add-concept/', GameFamilyAddConceptView.as_view(), name='game-family-add-concept'),
    path('game-families/<int:family_id>/remove-concept/', GameFamilyRemoveConceptView.as_view(), name='game-family-remove-concept'),
    path('game-families/search-concepts/', GameFamilyConceptSearchView.as_view(), name='game-family-search-concepts'),

    # Subscription admin endpoints (staff-only)
    path('admin/subscriptions/action/', SubscriptionAdminActionView.as_view(), name='subscription-admin-action'),
    path('admin/subscriptions/user/<int:user_id>/', SubscriptionAdminUserDetailView.as_view(), name='subscription-admin-user-detail'),

    # Fundraiser endpoints
    path('fundraiser/<slug:slug>/donate/', CreateDonationView.as_view(), name='fundraiser-donate'),
    path('fundraiser/claim/', ClaimBadgeView.as_view(), name='fundraiser-claim'),
    path('admin/fundraiser/claim-status/', UpdateClaimStatusView.as_view(), name='fundraiser-claim-status'),

    # Dashboard endpoints

    # Stats page endpoints

    # Title endpoints
    path('equip-title/', EquipTitleAPIView.as_view(), name='equip-title'),

    # Pursuer Card (fresh re-fetch for the post-sync forge)
    path('pursuer-card/', PursuerCardRefreshView.as_view(), name='pursuer-card'),

    # User settings endpoints
    path('user/timezone/', UpdateTimezoneAPIView.as_view(), name='user-timezone-update'),
    path('user/quick-settings/', UpdateQuickSettingsAPIView.as_view(), name='user-quick-settings'),

    # Profile Card endpoints

    # Badge display selection

    # Profile Showcase endpoints: WITHDRAWN 2026-08 with the rest of the customization surface. The views
    # are parked in api/profile_showcase_views.py -- restoring them is putting these four lines and the
    # import back. Withdrawn rather than redirected because these are WRITES: an endpoint left answering
    # would let anything still holding a reference file rows into a system with no door, which the rebuild
    # would then have to reconcile.
    #
    # /badges/showcase/ and /badges/showcase/reorder/ above are NOT part of this. Despite the name they
    # belong to the dashboard's badge-showcase module (dashboard.js), and withdrawing them is the
    # dashboard sunset's job, not this one.

    # Rating endpoints (standalone — independent of the archived review system)
    path('ratings/wizard/queue/', WizardQueueView.as_view(), name='rating-wizard-queue'),
    path('ratings/<int:concept_id>/group/<str:group_id>/rate/', GroupRatingView.as_view(), name='rating-group-rate'),
    path('ratings/<int:concept_id>/group/<str:group_id>/trophies/', TrophyListView.as_view(), name='rating-group-trophies'),
    path('ratings/blurb/<int:rating_id>/report/', BlurbReportView.as_view(), name='rating-blurb-report'),

    # Review API endpoints ARCHIVED (2026-05) — unregistered so they 404.
    # The view classes remain dormant in api/review_views.py for a
    # possible future rebuild.

    # Roadmap endpoints (staff-only). Per-tab/step/guide CRUD lives entirely
    # in the editor's BranchProxy now — every edit flows through the
    # lock/branch/merge cycle below, so we no longer expose direct
    # mutations as separate URLs.
    path('roadmap/<int:roadmap_id>/publish/', RoadmapPublishView.as_view(), name='roadmap-publish'),
    path('roadmap/<int:roadmap_id>/preview/', RoadmapPreviewView.as_view(), name='roadmap-preview'),
    path('roadmap/<int:roadmap_id>/hidden-authors/', RoadmapHiddenAuthorsView.as_view(), name='roadmap-hidden-authors'),
    path('roadmap/<int:roadmap_id>/trial-writers/', RoadmapTrialWritersView.as_view(), name='roadmap-trial-writers'),
    path('roadmap/<int:roadmap_id>/upload-image/', RoadmapImageUploadView.as_view(), name='roadmap-image-upload'),
    # Lock + branch-and-merge endpoints
    path('roadmap/<int:roadmap_id>/lock/acquire/', RoadmapLockAcquireView.as_view(), name='roadmap-lock-acquire'),
    path('roadmap/<int:roadmap_id>/lock/heartbeat/', RoadmapLockHeartbeatView.as_view(), name='roadmap-lock-heartbeat'),
    path('roadmap/<int:roadmap_id>/lock/branch/', RoadmapLockBranchView.as_view(), name='roadmap-lock-branch'),
    path('roadmap/<int:roadmap_id>/lock/release/', RoadmapLockReleaseView.as_view(), name='roadmap-lock-release'),
    path('roadmap/<int:roadmap_id>/lock/break/', RoadmapLockBreakView.as_view(), name='roadmap-lock-break'),
    path('roadmap/<int:roadmap_id>/lock/merge/', RoadmapLockMergeView.as_view(), name='roadmap-lock-merge'),
    # Author notes (writer+, no lock requirement)
    path('roadmap/<int:roadmap_id>/notes/', RoadmapNoteListCreateView.as_view(), name='roadmap-notes-list'),
    path('roadmap/<int:roadmap_id>/notes/<int:note_id>/', RoadmapNoteDetailView.as_view(), name='roadmap-notes-detail'),
    path('roadmap/<int:roadmap_id>/notes/<int:note_id>/resolve/', RoadmapNoteResolveView.as_view(), name='roadmap-notes-resolve'),
    path('roadmap/<int:roadmap_id>/notes/mark-read/', RoadmapNoteMarkReadView.as_view(), name='roadmap-notes-mark-read'),

    # Collectible progress: per-viewer "found" state for individual items.
    # Anonymous viewers track this in localStorage; the server only stores
    # logged-in progress.
    path(
        'collectibles/items/<int:item_id>/progress/',
        CollectibleProgressView.as_view(),
        name='collectible-progress',
    ),

    # YouTube oEmbed proxy for the roadmap editor's live attribution preview.
    path('youtube/attribution-lookup/', YouTubeAttributionLookupView.as_view(), name='youtube-attribution-lookup'),

    # Community Trophy Tracker (public, read-only aggregates)
    path('community-stats/today/', CommunityStatsTodayView.as_view(), name='community-stats-today'),
    path('community-stats/records/', CommunityStatsRecordsView.as_view(), name='community-stats-records'),
    path('community-stats/<str:date_str>/', CommunityStatsDayView.as_view(), name='community-stats-day'),
]