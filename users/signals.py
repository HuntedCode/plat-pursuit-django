from django.contrib.auth.signals import user_logged_in
from django.dispatch import receiver


@receiver(user_logged_in, dispatch_uid='users.forget_pending_signup')
def forget_pending_signup(sender, request, user, **kwargs):
    """A completed sign-in ends "Check your inbox": the confirmation link's own auto-login is the usual one.
    Without this the session record survives the login (Django keeps session data across `cycle_key`), so a
    later visit to the page showed the old address, even to a different account signed in on this browser.

    Only our record goes. allauth manages its own pending login through a completed sign-in."""
    if request is not None and hasattr(request, 'session'):
        from users.services.pending_signup import SESSION_KEY
        request.session.pop(SESSION_KEY, None)
