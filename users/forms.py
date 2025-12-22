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
        fields = ['user_timezone', 'default_region']
        widgets = {
            'user_timezone': forms.Select(attrs={'class': 'select w-full'}),
            'default_region': forms.Select(attrs={'class': 'select w-full'}),
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