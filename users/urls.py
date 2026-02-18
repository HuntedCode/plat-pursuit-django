from django.urls import path
from users.views import SettingsView, subscribe, subscribe_success, SubscriptionManagementView, EmailPreferencesView, paypal_cancel_subscription

urlpatterns = [
    path('settings/', SettingsView.as_view(), name='settings'),
    path('email-preferences/', EmailPreferencesView.as_view(), name='email_preferences'),
    path('subscribe/', subscribe, name='subscribe'),
    path('subscribe/success/', subscribe_success, name='subscribe_success'),
    path('subscription-management/', SubscriptionManagementView.as_view(), name='subscription_management'),
    path('paypal/cancel/', paypal_cancel_subscription, name='paypal_cancel_subscription'),
]