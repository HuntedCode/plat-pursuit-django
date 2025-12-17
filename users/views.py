# users/views.py
from allauth.account.views import ConfirmEmailView
import logging

logger = logging.getLogger(__name__)

class CustomConfirmEmailView(ConfirmEmailView):
    def get(self, *args, **kwargs):
        print(f"Confirmation request received: key={kwargs.get('key')}")
        response = super().get(*args, **kwargs)
        print(f"Confirmation response: {response.status_code}")
        return response

    def post(self, *args, **kwargs):
        print(f"POST confirmation: key={kwargs.get('key')}")
        response = super().post(*args, **kwargs)
        print(f"POST response: {response.status_code}")
        return response