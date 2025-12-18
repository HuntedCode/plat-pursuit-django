# users/views.py
from allauth.account.views import ConfirmEmailView
from django.contrib import messages
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import render, redirect
from django.views import View
import logging
from users.forms import UserSettingsForm, CustomPasswordChangeForm
from trophies.models import Profile

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
    
class SettingsView(LoginRequiredMixin, View):
    template_name = 'users/settings.html'
    login_url = '/login/'

    def get(self, request):
        user_form = UserSettingsForm(instance=request.user)
        password_form = CustomPasswordChangeForm(user=request.user)
        profile = request.user.profile if hasattr(request.user, 'profile') else None
        context = {
            'user_form': user_form,
            'password_form': password_form,
            'profile': profile,
        }
        return render(request, self.template_name, context)
    
    def post(self, request):
        action = request.POST.get('action')

        if action == 'update_user':
            user_form = UserSettingsForm(request.POST, instance=request.user)
            if user_form.is_valid():
                user_form.save()
                messages.success(request, 'User settings updated successfully!')
            else:
                messages.error(request, 'Error updating user settings.')
            return redirect('settings')
        
        elif action == 'change_password':
            password_form = CustomPasswordChangeForm(user=request.user, data=request.POST)
            if password_form.is_valid():
                user = password_form.save()
                update_session_auth_hash(request, user)
                messages.success(request, 'Password changed successfully.')
            else:
                messages.error(request, 'Error changing password. Check fields.')
            return redirect('settings')

        elif action == 'unlink_profile':
            profile = request.user.profile if hasattr(request.user, 'profile') else None
            if profile:
                profile.unlink_user()
                messages.success(request, 'PSN profile unlinked successfully!')
            else:
                messages.error(request, 'No profile to unlink.')
            return redirect('settings')
        return redirect('settings')