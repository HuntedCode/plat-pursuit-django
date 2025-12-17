from allauth.account.adapter import DefaultAccountAdapter
import logging

logger = logging.getLogger('psn_api')

class CustomAccountAdapter(DefaultAccountAdapter):
    def __init__(self, *args, **kwargs):
        print("CustomAccountAdapter initialized")
        super().__init__(*args, **kwargs)

    def pre_confirm_email(self, request, email_address):
        print(f"Pre-confirm: Email {email_address.email} for user {email_address.user}")

    def confirm_email(self, request, email_address):
        print(f"Confirming email: {email_address.email} for user {email_address.user}")
        super().confirm_email(request, email_address)
        print(f"Email verified: {email_address.verified}")
    
    def post_confirm_email(self, request, email_address):
        print(f"Post-confirm actions for {email_address.email}")