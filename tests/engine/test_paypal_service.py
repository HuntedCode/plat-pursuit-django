"""Tests send the TEST PayPal credentials, never a developer's real ones and never None.

`PAYPAL_CLIENT_ID` / `PAYPAL_CLIENT_SECRET` come from `os.getenv` in settings.py, so without a
pin in settings_test a test run on a laptop sent the developer's real sandbox secret to whatever
mock answered, while CI sent `None` (a requests deprecation warning: non-string passwords stop
working in Requests 3.0). The token request is the only place either is used.
"""
import base64

import responses
from django.conf import settings

from users.services.paypal_service import PayPalService

TOKEN_URL = f"{settings.PAYPAL_API_BASE}/v1/oauth2/token"


@responses.activate
def test_the_token_request_authenticates_with_the_test_credentials():
    responses.add(responses.POST, TOKEN_URL, json={'access_token': 'tok', 'expires_in': 3600})

    assert PayPalService._get_access_token() == 'tok'

    [call] = responses.calls
    expected = base64.b64encode(
        b'test-paypal-client-id-not-for-production:test-paypal-client-secret-not-for-production'
    ).decode()
    assert call.request.headers['Authorization'] == f'Basic {expected}'
