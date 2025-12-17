from django import forms
from allauth.account.forms import SignupForm
from .models import CustomUser


class CustomUserCreationForm(SignupForm):
    class Meta:
        model = CustomUser
        fields = ("email", "password1", "password2")

    def clean_email(self):
        email = self.cleaned_data['email'].lower()
        if CustomUser.objects.filter(email=email).exists():
            raise forms.ValidationError("This email is already in use.")
        return email
