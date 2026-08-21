from django.urls import path
from django.views.generic import RedirectView
from users.views import SettingsView, subscribe_success, SubscriptionManagementView, EmailPreferencesView, EmailPreferencesRedirectView, paypal_cancel_subscription

urlpatterns = [
    path('settings/', SettingsView.as_view(), name='settings'),
    path('email-preferences/', EmailPreferencesView.as_view(), name='email_preferences'),
    path('email-preferences/redirect/', EmailPreferencesRedirectView.as_view(), name='email_preferences_redirect'),
    # The storefront moved to /support/ (SupportStorefrontView) so the checkout form and its POST
    # handler share a URL. TEMPORARY, not permanent: a 301 on a payment URL is cached by the browser
    # and cannot be taken back, and a 301 on a POST is downgraded to a GET with the body dropped.
    # Nothing should POST here any more, but the cost of being wrong is a silently broken checkout.
    # The `subscribe` NAME is kept for inbound bookmarks and any stale external link; every internal
    # reverser was repointed to `support_hub` and the notification/email copy uses /support/ direct,
    # so the name's only remaining consumers are the redirect test and history.
    path('subscribe/', RedirectView.as_view(pattern_name='support_hub', permanent=False, query_string=True), name='subscribe'),
    # Does NOT move. This exact path is baked into every Stripe `success_url` and PayPal `return_url`
    # we have ever sent, including on subscriptions bought months ago.
    path('subscribe/success/', subscribe_success, name='subscribe_success'),
    path('subscription-management/', SubscriptionManagementView.as_view(), name='subscription_management'),
    path('paypal/cancel/', paypal_cancel_subscription, name='paypal_cancel_subscription'),
]