from django.urls import path
from users.views import SettingsView, subscribe, subscribe_success

urlpatterns = [
    path('settings/', SettingsView.as_view(), name='settings'),
    path('subscribe/', subscribe, name='subscribe'),
    path('subscribe/success/', subscribe_success, name='subscribe_success'),
]