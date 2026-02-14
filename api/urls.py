from django.urls import path
from .views import (
    SummaryView, GenerateCodeView, VerifyView, UnlinkView, CheckLinkedView,
    RefreshView, TrophyCaseView, CommentListView, CommentCreateView,
    CommentDetailView, CommentVoteView, CommentReportView, AgreeToGuidelinesView
)
from .checklist_views import (
    ChecklistListView, ChecklistCreateView, ChecklistDetailView,
    ChecklistPublishView, ChecklistVoteView, ChecklistReportView,
    ChecklistProgressToggleView, ChecklistProgressView, ChecklistSectionBulkProgressView,
    ChecklistSectionListView, ChecklistSectionDetailView, ChecklistSectionReorderView,
    ChecklistItemListView, ChecklistItemBulkCreateView, ChecklistItemBulkUpdateView,
    ChecklistItemDetailView, ChecklistItemReorderView,
    UserDraftChecklistsView, UserPublishedChecklistsView, UserChecklistProgressView,
    ChecklistImageUploadView, SectionImageUploadView, ItemImageCreateView,
    MarkdownPreviewView, ChecklistGameSelectView, ChecklistAvailableTrophiesView
)
from .notification_views import (
    NotificationListView, NotificationMarkReadView, NotificationMarkAllReadView,
    AdminSendNotificationView, NotificationBulkDeleteView,
    NotificationDeleteView, NotificationShareImageGenerateView, NotificationShareImageView,
    NotificationShareImageStatusView, NotificationShareImageHTMLView, NotificationShareImagePNGView,
    NotificationRatingView,
    AdminNotificationPreviewView, AdminTargetCountView, AdminUserSearchView
)
from .shareable_views import ShareableImageHTMLView, ShareableImagePNGView
from .recap_views import (
    RecapAvailableView, RecapDetailView, RecapRegenerateView, RecapShareImageHTMLView,
    RecapShareImagePNGView, RecapSlidePartialView
)
from .tracking_views import TrackSiteEventView
from .share_temp_views import serve_share_temp_image
from .game_list_views import (
    GameListCreateView, GameListDetailView, GameListUpdateView, GameListDeleteView,
    GameListAddItemView, GameListRemoveItemView, GameListUpdateItemView, GameListReorderView,
    GameListLikeView, GameListQuickAddView, UserGameListsView, GameListCopyView,
    GameSearchView,
)
from .az_challenge_views import (
    AZChallengeCreateAPIView, AZChallengeDetailAPIView, AZChallengeUpdateAPIView,
    AZChallengeDeleteAPIView, AZSlotAssignAPIView, AZSlotClearAPIView,
    AZGameSearchAPIView,
)

app_name = 'api'

urlpatterns = [
    path('generate-code/', GenerateCodeView.as_view(), name='generate-code'),
    path('verify/', VerifyView.as_view(), name='verify'),
    path('check-linked/', CheckLinkedView.as_view(), name='check-linked'),
    path('unlink/', UnlinkView.as_view(), name='unlink'),
    path('refresh/', RefreshView.as_view(), name='refresh'),
    path('summary/', SummaryView.as_view(), name='summary'),
    path('trophy-case/', TrophyCaseView.as_view(), name='trophy-case'),

    # Comment endpoints - Concept-based
    path('comments/concept/<int:concept_id>/', CommentListView.as_view(), name='comment-list'),
    path('comments/concept/<int:concept_id>/trophy/<int:trophy_id>/', CommentListView.as_view(), name='comment-list-trophy'),
    path('comments/concept/<int:concept_id>/checklist/<int:checklist_id>/', CommentListView.as_view(), name='comment-list-checklist'),
    path('comments/concept/<int:concept_id>/create/', CommentCreateView.as_view(), name='comment-create'),
    path('comments/concept/<int:concept_id>/trophy/<int:trophy_id>/create/', CommentCreateView.as_view(), name='comment-create-trophy'),
    path('comments/concept/<int:concept_id>/checklist/<int:checklist_id>/create/', CommentCreateView.as_view(), name='comment-create-checklist'),
    path('comments/<int:comment_id>/', CommentDetailView.as_view(), name='comment-detail'),
    path('comments/<int:comment_id>/vote/', CommentVoteView.as_view(), name='comment-vote'),
    path('comments/<int:comment_id>/report/', CommentReportView.as_view(), name='comment-report'),

    # Community guidelines
    path('guidelines/agree/', AgreeToGuidelinesView.as_view(), name='guidelines-agree'),

    # Checklist endpoints - Concept-based
    path('checklists/concept/<int:concept_id>/', ChecklistListView.as_view(), name='checklist-list'),
    path('checklists/concept/<int:concept_id>/create/', ChecklistCreateView.as_view(), name='checklist-create'),
    path('checklists/<int:checklist_id>/', ChecklistDetailView.as_view(), name='checklist-detail'),
    path('checklists/<int:checklist_id>/publish/', ChecklistPublishView.as_view(), name='checklist-publish'),
    path('checklists/<int:checklist_id>/vote/', ChecklistVoteView.as_view(), name='checklist-vote'),
    path('checklists/<int:checklist_id>/report/', ChecklistReportView.as_view(), name='checklist-report'),
    path('checklists/<int:checklist_id>/progress/', ChecklistProgressView.as_view(), name='checklist-progress'),
    path('checklists/<int:checklist_id>/progress/toggle/<int:item_id>/', ChecklistProgressToggleView.as_view(), name='checklist-progress-toggle'),
    path('checklists/<int:checklist_id>/sections/<int:section_id>/bulk-progress/', ChecklistSectionBulkProgressView.as_view(), name='checklist-section-bulk-progress'),

    # Checklist sections
    path('checklists/<int:checklist_id>/sections/', ChecklistSectionListView.as_view(), name='checklist-section-list'),
    path('checklists/<int:checklist_id>/sections/reorder/', ChecklistSectionReorderView.as_view(), name='checklist-section-reorder'),
    path('checklists/<int:checklist_id>/sections/<int:section_id>/', ChecklistSectionDetailView.as_view(), name='checklist-section-detail'),

    # Checklist items
    path('checklists/sections/<int:section_id>/items/', ChecklistItemListView.as_view(), name='checklist-item-list'),
    path('checklists/sections/<int:section_id>/items/bulk/', ChecklistItemBulkCreateView.as_view(), name='checklist-item-bulk-create'),
    path('checklists/sections/<int:section_id>/items/image/', ItemImageCreateView.as_view(), name='checklist-item-image-create'),
    path('checklists/sections/<int:section_id>/items/reorder/', ChecklistItemReorderView.as_view(), name='checklist-item-reorder'),
    path('checklists/<int:checklist_id>/items/bulk-update/', ChecklistItemBulkUpdateView.as_view(), name='checklist-item-bulk-update'),
    path('checklists/items/<int:item_id>/', ChecklistItemDetailView.as_view(), name='checklist-item-detail'),

    # Checklist image endpoints
    path('checklists/<int:checklist_id>/image/', ChecklistImageUploadView.as_view(), name='checklist-image-upload'),
    path('checklists/sections/<int:section_id>/image/', SectionImageUploadView.as_view(), name='section-image-upload'),

    # Checklist trophy endpoints
    path('checklists/<int:checklist_id>/select-game/', ChecklistGameSelectView.as_view(), name='checklist-select-game'),
    path('checklists/<int:checklist_id>/available-trophies/', ChecklistAvailableTrophiesView.as_view(), name='checklist-available-trophies'),

    # User checklist endpoints
    path('checklists/my-drafts/', UserDraftChecklistsView.as_view(), name='checklist-my-drafts'),
    path('checklists/my-published/', UserPublishedChecklistsView.as_view(), name='checklist-my-published'),
    path('checklists/my-progress/', UserChecklistProgressView.as_view(), name='checklist-my-progress'),

    # Markdown preview
    path('markdown/preview/', MarkdownPreviewView.as_view(), name='markdown-preview'),

    # Notification endpoints
    path('notifications/', NotificationListView.as_view(), name='notification-list'),
    path('notifications/mark-all-read/', NotificationMarkAllReadView.as_view(), name='notification-mark-all-read'),
    path('notifications/bulk-delete/', NotificationBulkDeleteView.as_view(), name='notification-bulk-delete'),
    path('admin/notifications/send/', AdminSendNotificationView.as_view(), name='admin-send-notification'),
    path('admin/notifications/preview/', AdminNotificationPreviewView.as_view(), name='admin-notification-preview'),
    path('admin/notifications/target-count/', AdminTargetCountView.as_view(), name='admin-notification-target-count'),
    path('admin/notifications/user-search/', AdminUserSearchView.as_view(), name='admin-notification-user-search'),

    # Platinum share image endpoints (must be before generic <int:pk>/ route)
    path('notifications/<int:pk>/share-image/generate/', NotificationShareImageGenerateView.as_view(), name='notification-share-image-generate'),
    path('notifications/<int:pk>/share-image/status/', NotificationShareImageStatusView.as_view(), name='notification-share-image-status'),
    path('notifications/<int:pk>/share-image/html/', NotificationShareImageHTMLView.as_view(), name='notification-share-image-html'),
    path('notifications/<int:pk>/share-image/png/', NotificationShareImagePNGView.as_view(), name='notification-share-image-png'),
    path('notifications/<int:pk>/share-image/<str:format_type>/', NotificationShareImageView.as_view(), name='notification-share-image'),

    # Notification rating endpoint (for platinum notifications)
    path('notifications/<int:pk>/rating/', NotificationRatingView.as_view(), name='notification-rating'),

    # Generic notification detail routes (must be after more specific routes)
    path('notifications/<int:pk>/read/', NotificationMarkReadView.as_view(), name='notification-mark-read'),
    path('notifications/<int:pk>/', NotificationDeleteView.as_view(), name='notification-delete'),

    # Shareable image endpoints (EarnedTrophy-based, for My Shareables page)
    path('shareables/platinum/<int:earned_trophy_id>/html/', ShareableImageHTMLView.as_view(), name='shareable-platinum-html'),
    path('shareables/platinum/<int:earned_trophy_id>/png/', ShareableImagePNGView.as_view(), name='shareable-platinum-png'),

    # Monthly recap endpoints
    path('recap/available/', RecapAvailableView.as_view(), name='recap-available'),
    path('recap/<int:year>/<int:month>/', RecapDetailView.as_view(), name='recap-detail'),
    path('recap/<int:year>/<int:month>/regenerate/', RecapRegenerateView.as_view(), name='recap-regenerate'),
    path('recap/<int:year>/<int:month>/html/', RecapShareImageHTMLView.as_view(), name='recap-share-html'),
    path('recap/<int:year>/<int:month>/png/', RecapShareImagePNGView.as_view(), name='recap-share-png'),
    path('recap/<int:year>/<int:month>/slide/<str:slide_type>/', RecapSlidePartialView.as_view(), name='recap-slide-partial'),

    # Tracking endpoints
    path('tracking/site-event/', TrackSiteEventView.as_view(), name='tracking-site-event'),

    # Temp share image serving
    path('share-temp/<str:filename>', serve_share_temp_image, name='share-temp-image'),

    # Game list endpoints
    path('lists/', GameListCreateView.as_view(), name='game-list-create'),
    path('lists/my/', UserGameListsView.as_view(), name='game-list-my'),
    path('lists/quick-add/', GameListQuickAddView.as_view(), name='game-list-quick-add'),
    path('lists/<int:list_id>/', GameListDetailView.as_view(), name='game-list-detail'),
    path('lists/<int:list_id>/update/', GameListUpdateView.as_view(), name='game-list-update'),
    path('lists/<int:list_id>/delete/', GameListDeleteView.as_view(), name='game-list-delete'),
    path('lists/<int:list_id>/items/', GameListAddItemView.as_view(), name='game-list-add-item'),
    path('lists/<int:list_id>/items/<int:item_id>/', GameListRemoveItemView.as_view(), name='game-list-remove-item'),
    path('lists/<int:list_id>/items/<int:item_id>/update/', GameListUpdateItemView.as_view(), name='game-list-update-item'),
    path('lists/<int:list_id>/items/reorder/', GameListReorderView.as_view(), name='game-list-reorder'),
    path('lists/<int:list_id>/like/', GameListLikeView.as_view(), name='game-list-like'),
    path('lists/<int:list_id>/copy/', GameListCopyView.as_view(), name='game-list-copy'),

    # Game search (for list typeahead)
    path('games/search/', GameSearchView.as_view(), name='game-search'),

    # A-Z Challenge endpoints (static paths before <int:> to avoid URL conflicts)
    path('challenges/az/', AZChallengeCreateAPIView.as_view(), name='az-challenge-create'),
    path('challenges/az/game-search/', AZGameSearchAPIView.as_view(), name='az-game-search'),
    path('challenges/az/<int:challenge_id>/', AZChallengeDetailAPIView.as_view(), name='az-challenge-detail'),
    path('challenges/az/<int:challenge_id>/update/', AZChallengeUpdateAPIView.as_view(), name='az-challenge-update'),
    path('challenges/az/<int:challenge_id>/delete/', AZChallengeDeleteAPIView.as_view(), name='az-challenge-delete'),
    path('challenges/az/<int:challenge_id>/slots/<str:letter>/assign/', AZSlotAssignAPIView.as_view(), name='az-slot-assign'),
    path('challenges/az/<int:challenge_id>/slots/<str:letter>/clear/', AZSlotClearAPIView.as_view(), name='az-slot-clear'),
]