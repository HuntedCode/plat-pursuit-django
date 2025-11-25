from django.urls import path
from .views import RegisterView, TrophiesView

app_name = 'api'

urlpatterns = [
    path('register/', RegisterView.as_view(), name='register'),
    path('trophies/', TrophiesView.as_view(), name='tropihes'),
]