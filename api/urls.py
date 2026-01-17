from django.urls import path
from .views import (
    SummaryView, GenerateCodeView, VerifyView, UnlinkView, CheckLinkedView,
    RefreshView, TrophyCaseView, CommentListView, CommentCreateView,
    CommentDetailView, CommentVoteView, CommentReportView
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
    path('comments/concept/<int:concept_id>/create/', CommentCreateView.as_view(), name='comment-create'),
    path('comments/concept/<int:concept_id>/trophy/<int:trophy_id>/create/', CommentCreateView.as_view(), name='comment-create-trophy'),
    path('comments/<int:comment_id>/', CommentDetailView.as_view(), name='comment-detail'),
    path('comments/<int:comment_id>/vote/', CommentVoteView.as_view(), name='comment-vote'),
    path('comments/<int:comment_id>/report/', CommentReportView.as_view(), name='comment-report'),
]