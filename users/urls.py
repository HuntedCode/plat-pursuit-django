from django.urls import path
from users.views import SettingsView, subscribe, subscribe_success, SubscriptionManagementView, EmailPreferencesView

urlpatterns = [
    path('settings/', SettingsView.as_view(), name='settings'),
    path('email-preferences/', EmailPreferencesView.as_view(), name='email_preferences'),
    path('subscribe/', subscribe, name='subscribe'),
    path('subscribe/success/', subscribe_success, name='subscribe_success'),
    path('subscription-management/', SubscriptionManagementView.as_view(), name='subscription_management'),
]