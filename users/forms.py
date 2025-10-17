from django import forms
from django.contrib.auth.forms import UserCreationForm
from .models import CustomUser

class CustomUserCreationForm(UserCreationForm):
    email = forms.EmailField(required=True)

    class Meta:
        model = CustomUser
        fields = ('username', 'email', 'password', 'password2')

    def clean_username(self):
        username = self.cleaned_data['username'].lower()
        if CustomUser.objects.filter(username=username).exists():
            raise forms.ValidationError("This username is already in use.")
        return username