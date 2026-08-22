from django.urls import path
from django.views.generic import RedirectView
from users.views import SettingsView, subscribe_success, paypal_cancel_subscription, stripe_billing_portal

urlpatterns = [
    path('settings/', SettingsView.as_view(), name='settings'),
    # Email preferences: PARKED (2026-08) with the non-vital emails, pending the email-system
    # rebuild. Every remaining email is transactional (auth, billing, fundraiser, membership
    # welcome), so there is nothing to opt out of. The path stays as a redirect because tokened
    # links to it live in every email footer ever delivered; they land on Settings, where email
    # options will return with the rebuild. Views are parked unrouted in users/views.py.
    path('email-preferences/', RedirectView.as_view(
        pattern_name='settings', permanent=False), name='email_preferences'),
    path('email-preferences/redirect/', RedirectView.as_view(
        pattern_name='settings', permanent=False), name='email_preferences_redirect'),
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
    # The membership page moved to /support/membership/ (2026-08 rebuild). This redirect is
    # PERMANENT INFRASTRUCTURE, not cleanup debt: the old path is baked into every notification
    # row already in the DB and every lifecycle email ever sent. 302 on purpose, same reasoning
    # as `subscribe` above -- a 301 on a payment-adjacent URL is cached by the browser and cannot
    # be taken back. Unnamed so nothing can reverse to the old path.
    path('subscription-management/', RedirectView.as_view(
        pattern_name='subscription_management', permanent=False, query_string=True)),
    path('paypal/cancel/', paypal_cancel_subscription, name='paypal_cancel_subscription'),
    path('stripe/portal/', stripe_billing_portal, name='stripe_billing_portal'),
]