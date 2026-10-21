from django import forms
from django.contrib.auth.forms import AdminUserCreationForm, PasswordChangeForm
from allauth.account.adapter import get_adapter
from allauth.account.forms import SignupForm
from .models import CustomUser
from .services.timezone_service import normalize_zone


class CustomUserCreationForm(SignupForm):
    # Honeypot. Real users never see this input (hidden off-screen in the
    # template); scripted form-fillers populate every field they encounter
    # and trip the validator. Pairs with ACCOUNT_RATE_LIMITS['signup'] to
    # catch attackers below the per-IP throttle (e.g. residential proxies).
    website = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={
            'autocomplete': 'off',
            'tabindex': '-1',
            'aria-hidden': 'true',
        }),
        label='',
    )

    # THE BROWSER'S ZONE, filled by the signup page's script (owner, 2026-10-17): a new account starts in
    # its hunter's own timezone instead of UTC. A guess, not an answer, so it never stamps
    # `timezone_confirmed_at` (`users.services.timezone_service`). Missing or unknown: the UTC default.
    browser_timezone = forms.CharField(required=False, max_length=64, widget=forms.HiddenInput)

    def clean_browser_timezone(self):
        # The field's own choices (`CustomUser.user_timezone`), an old browser-reported name mapped to its current
        # one; anything else is dropped, never an error, because a hunter cannot see or fix this input.
        return normalize_zone(self.cleaned_data.get('browser_timezone'))

    def clean_website(self):
        if self.cleaned_data.get('website'):
            # Surface as a generic non-field-style error so the bot can't
            # tell which input tripped it. Real users will never see this.
            raise forms.ValidationError("Unable to process signup. Please try again later.")
        return ''

    def clean_email(self):
        email = self.cleaned_data['email'].lower()
        if CustomUser.objects.filter(email=email).exists():
            raise forms.ValidationError("This email is already in use.")
        return email


class AdminAddUserForm(AdminUserCreationForm):
    """The Django admin's "Add user" form. Not the signup form above: that one is allauth's, not a ModelForm,
    so the admin could not build its page (`'CustomUserForm' object has no attribute 'instance'`)."""

    class Meta(AdminUserCreationForm.Meta):
        model = CustomUser
        fields = ("email", "user_timezone")

    def clean_email(self):
        # Lowercased like a signup, so the address can still sign in and the duplicate check matches.
        email = self.cleaned_data['email'].lower()
        if CustomUser.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError("This email is already in use.")
        return email

    def save(self, commit=True):
        # The model still carries AbstractUser's unique `username`, which allauth fills on signup. Fill it the
        # same way, or every admin-added account after the first collides on an empty one.
        get_adapter().populate_username(None, self.instance)
        return super().save(commit=commit)

class CustomPasswordChangeForm(PasswordChangeForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['old_password'].widget.attrs.update({'class': 'input w-full', 'placeholder': 'Old Password'})
        self.fields['new_password1'].widget.attrs.update({'class': 'input w-full', 'placeholder': 'New Password'})
        self.fields['new_password2'].widget.attrs.update({'class': 'input w-full', 'placeholder': 'Confirm New Password'})


class EmailPreferencesForm(forms.Form):
    """
    Form for managing user email notification preferences.

    Used in standalone email preference page (no authentication required).
    """
    monthly_recap = forms.BooleanField(
        required=False,
        label='Monthly Recap Emails',
        help_text='Get personalized monthly trophy recaps delivered to your inbox',
        widget=forms.CheckboxInput(attrs={'class': 'checkbox'})
    )

    badge_notifications = forms.BooleanField(
        required=False,
        label='Badge Achievement Emails',
        help_text='Receive notifications when you earn new badges',
        widget=forms.CheckboxInput(attrs={'class': 'checkbox'})
    )

    subscription_notifications = forms.BooleanField(
        required=False,
        label='Subscription & Billing Emails',
        help_text='Payment failure warnings and subscription status updates (recommended)',
        widget=forms.CheckboxInput(attrs={'class': 'checkbox'})
    )

    admin_announcements = forms.BooleanField(
        required=False,
        label='Site Announcements',
        help_text='Receive important updates and announcements from PlatPursuit',
        widget=forms.CheckboxInput(attrs={'class': 'checkbox'})
    )

    weekly_digest = forms.BooleanField(
        required=False,
        label='Weekly Digest',
        help_text='Get a personalized weekly summary of your trophy stats, challenge progress, and community highlights',
        widget=forms.CheckboxInput(attrs={'class': 'checkbox'})
    )

    global_unsubscribe = forms.BooleanField(
        required=False,
        label='Unsubscribe from all emails',
        help_text='Stop receiving all email notifications from PlatPursuit',
        widget=forms.CheckboxInput(attrs={'class': 'checkbox checkbox-error'})
    )

    def clean(self):
        """
        Validate form data and handle global unsubscribe logic.

        If global_unsubscribe is True, automatically set all other preferences to False.
        """
        cleaned_data = super().clean()

        if cleaned_data.get('global_unsubscribe'):
            # If global unsubscribe is checked, uncheck everything else
            cleaned_data['monthly_recap'] = False
            cleaned_data['badge_notifications'] = False
            cleaned_data['subscription_notifications'] = False
            cleaned_data['admin_announcements'] = False
            cleaned_data['weekly_digest'] = False

        return cleaned_data