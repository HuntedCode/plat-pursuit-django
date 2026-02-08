from django import forms
from django.contrib.auth.forms import UserChangeForm, PasswordChangeForm
from allauth.account.forms import SignupForm
from .models import CustomUser
import pytz


class CustomUserCreationForm(SignupForm):
    class Meta:
        model = CustomUser
        fields = ("email", "password1", "password2")

    def clean_email(self):
        email = self.cleaned_data['email'].lower()
        if CustomUser.objects.filter(email=email).exists():
            raise forms.ValidationError("This email is already in use.")
        return email

class UserSettingsForm(UserChangeForm):
    class Meta:
        model = CustomUser
        fields = ['user_timezone', 'default_region', 'use_24hr_clock']
        widgets = {
            'user_timezone': forms.Select(attrs={'class': 'select w-full'}),
            'default_region': forms.Select(attrs={'class': 'select w-full'}),
            'use_24hr_clock': forms.CheckboxInput(attrs={'class': 'toggle toggle-primary'}),
        }
        labels = {
            'user_timezone': 'Timezone',
            'default_region': 'Default Region',
            'use_24hr_clock': 'Use 24-Hour Clock Format',
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['user_timezone'].choices = [(tz, tz) for tz in pytz.common_timezones]

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

    milestone_notifications = forms.BooleanField(
        required=False,
        label='Milestone Emails',
        help_text='Get notified about trophy milestones and achievements',
        widget=forms.CheckboxInput(attrs={'class': 'checkbox'})
    )

    admin_announcements = forms.BooleanField(
        required=False,
        label='Site Announcements',
        help_text='Receive important updates and announcements from PlatPursuit',
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
            cleaned_data['milestone_notifications'] = False
            cleaned_data['admin_announcements'] = False

        return cleaned_data