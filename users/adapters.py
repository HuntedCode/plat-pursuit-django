from allauth.account.adapter import DefaultAccountAdapter
import logging

logger = logging.getLogger('psn_api')

class CustomAccountAdapter(DefaultAccountAdapter):
    def confirm_email(self, request, email_address):
        print(f"Confirming email: {email_address.email} for user {email_address.user}")
        super().confirm_email(request, email_address)
        print(f"Email verified: {email_address.verified}")