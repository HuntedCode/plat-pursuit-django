from django.urls import path
from .views import SummaryView, GenerateCodeView, VerifyView, UnlinkView, CheckLinkedView, RefreshView, TrophyCaseView

app_name = 'api'

urlpatterns = [
    path('generate-code/', GenerateCodeView.as_view(), name='generate-code'),
    path('verify/', VerifyView.as_view(), name='verify'),
    path('check-linked/', CheckLinkedView.as_view(), name='check-linked'),
    path('unlink/', UnlinkView.as_view(), name='unlink'),
    path('refresh/', RefreshView.as_view(), name='refresh'),
    path('summary/', SummaryView.as_view(), name='summary'),
    path('trophy-case/', TrophyCaseView.as_view(), name='trophy-case'),
]