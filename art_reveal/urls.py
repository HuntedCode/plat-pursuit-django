from django.urls import path

from .views import ArtRevealEventView

urlpatterns = [
    path('events/badge-reveal/', ArtRevealEventView.as_view(), name='art_reveal_event'),
]
