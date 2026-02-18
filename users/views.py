# users/views.py
import json

from allauth.account.views import ConfirmEmailView
from core.services.tracking import track_page_view
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import render, redirect
from django.urls import reverse
from django.views import View
from django.views.generic import TemplateView
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from djstripe.models import Price, Customer, Subscription
from djstripe.models import Event as DJStripeEvent
import stripe
import logging
from users.forms import UserSettingsForm, CustomPasswordChangeForm
from users.services.subscription_service import SubscriptionService
from users.models import CustomUser
from trophies.forms import PremiumSettingsForm, ProfileSettingsForm
from trophies.models import Concept, ProfileGame
from trophies.utils import update_profile_trophy_counts

logger = logging.getLogger('psn_api')

class CustomConfirmEmailView(ConfirmEmailView):
    def get(self, *args, **kwargs):
        logger.debug(f"Confirmation request received: key={kwargs.get('key')}")
        response = super().get(*args, **kwargs)
        logger.debug(f"Confirmation response: {response.status_code}")
        return response

    def post(self, *args, **kwargs):
        logger.debug(f"POST confirmation: key={kwargs.get('key')}")
        response = super().post(*args, **kwargs)
        logger.debug(f"POST response: {response.status_code}")
        return response
    
class SettingsView(LoginRequiredMixin, View):
    template_name = 'users/settings.html'
    login_url = '/login/'

    def get(self, request):
        user_form = UserSettingsForm(instance=request.user)
        password_form = CustomPasswordChangeForm(user=request.user)
        profile = request.user.profile if hasattr(request.user, 'profile') else None
        premium_form = PremiumSettingsForm(instance=profile) if profile else None
        profile_form = ProfileSettingsForm(instance=profile) if profile else None

        # Build available themes for the template
        # Exclude game art themes since settings page has no game context
        from trophies.themes import get_available_themes_for_grid
        available_themes = get_available_themes_for_grid(include_game_art=False)

        # Serialize current background for the JS picker
        initial_bg_data = 'null'
        if profile and profile.selected_background:
            bg = profile.selected_background
            initial_bg_data = json.dumps({
                'concept_id': bg.id,
                'title_name': bg.unified_title or '',
                'icon_url': bg.concept_icon_url or '',
            })

        context = {
            'user_form': user_form,
            'password_form': password_form,
            'premium_form': premium_form,
            'profile_form': profile_form,
            'profile': profile,
            'available_themes': available_themes,
            'initial_background_json': initial_bg_data,
        }
        track_page_view('settings', 'user', request)
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
        
        elif action == 'update_premium':
            if not hasattr(request.user, 'profile') or not request.user.profile.user_is_premium:
                messages.error(request, 'This feature is for premium users only!')
                return redirect('settings')
            profile = request.user.profile
            premium_form = PremiumSettingsForm(request.POST, instance=profile)
            if premium_form.is_valid():
                premium_form.save()

                # Handle selected_background from the JS picker (hidden input)
                bg_id = request.POST.get('selected_background', '').strip()
                if bg_id:
                    try:
                        concept = Concept.objects.get(id=int(bg_id), bg_url__isnull=False)
                        # Validate the user has earned this background
                        has_access = ProfileGame.objects.filter(
                            profile=profile, game__concept=concept,
                        ).filter(Q(has_plat=True) | Q(progress=100)).exists()
                        if has_access:
                            profile.selected_background = concept
                        else:
                            profile.selected_background = None
                    except (ValueError, Concept.DoesNotExist):
                        profile.selected_background = None
                else:
                    profile.selected_background = None
                profile.save(update_fields=['selected_background'])

                messages.success(request, 'Premium settings updated successfully!')
            else:
                messages.error(request, 'Error updating premium settings.')
            return redirect('settings')
        
        elif action == 'update_profile':
            if not hasattr(request.user, 'profile'):
                messages.error(request, 'Link a PSN account to change this setting!')
                return redirect('settings')
            profile_form = ProfileSettingsForm(request.POST, instance=request.user.profile)
            if profile_form.is_valid():
                profile_form.save()
                request.user.profile.refresh_from_db()
                update_profile_trophy_counts(request.user.profile)
                messages.success(request, 'Profile settings updated successfully!')
            else:
                messages.error(request, 'Error updating profile settings.')
            return redirect('settings')

        
        return redirect('settings')
    
@login_required
def subscribe(request):
    is_live = settings.STRIPE_MODE == 'live'

    if Subscription.objects.filter(customer__subscriber=request.user).exists():
        subs = Subscription.objects.filter(customer__subscriber=request.user)
        if any(sub.status == 'active' for sub in subs):
            messages.info(request, 'You already have an active subscription. Manage it here.')
            return redirect('subscription_management')

    try:
        if is_live:
            prices = {
                'ad_free': Price.objects.get(id='price_1SkR4XR5jhcbjB325xchFZm5'),
                'premium_monthly': Price.objects.get(id='price_1SkR3wR5jhcbjB32vEaltpEJ'),
                'premium_yearly': Price.objects.get(id='price_1SkR7jR5jhcbjB32BmKo4iQQ'),
                'supporter': Price.objects.get(id='price_1SkRCuR5jhcbjB32yBFBm1h3'),
            }
        else:
            prices = {
                'ad_free': Price.objects.get(id='price_1SkTknR5jhcbjB32fnM6oP5A'),
                'premium_monthly': Price.objects.get(id='price_1SkSXpR5jhcbjB32BA08Bv0o'),
                'premium_yearly': Price.objects.get(id='price_1SkSY0R5jhcbjB327fYUtaJN'),
                'supporter': Price.objects.get(id='price_1SkTlHR5jhcbjB32zjcM2I4P'),
            }
    except Price.DoesNotExist as e:
        messages.error(request, "Pricing not available in current mode. Contact support.")
        logger.error(f"Price fetch error: {e} in mode {settings.STRIPE_MODE}")
        return redirect('home')

    if request.method == 'POST':
        logger.info('POST request received')
        tier = request.POST.get('tier')
        logger.info(f"Selected tier: {tier}")
        if tier not in prices:
            messages.error(request, "Invalid tier selected.")
            logger.warning(f"Invalid tier: {tier}")
            return redirect('subscribe')
        
        price = prices[tier]

        customer, created = Customer.get_or_create(subscriber=request.user)
        if created:
            customer.email = request.user.email
            customer.save()
        request.user.stripe_customer_id = customer.id
        request.user.save()

        try:
            session = stripe.checkout.Session.create(
                customer=customer.id,
                payment_method_types=['card', 'us_bank_account', 'amazon_pay', 'cashapp', 'link'],
                line_items=[{'price': price.id, 'quantity': 1}],
                mode='subscription',
                success_url=request.build_absolute_uri('/users/subscribe/success/') + "?session_id={CHECKOUT_SESSION_ID}",
                cancel_url=request.build_absolute_uri('/users/subscribe/'),
                metadata={'tier': tier},
            )
            return redirect(session.url, code=303)
        except stripe.error.StripeError as e:
            messages.error(request, f"Error creating checkout: {str(e)}")
            return redirect('subscribe')
        
    context = {'prices': {k: v.unit_amount / 100 for k, v in prices.items()}}
    context['is_live'] = is_live

    # Hand-picked themes for the "Try it!" preview swatches
    import re
    from trophies.themes import GRADIENT_THEMES
    clean = lambda css: re.sub(r'\s+', ' ', css).strip()
    context['preview_themes'] = [
        {'name': 'Machine Hunter', 'css': clean(GRADIENT_THEMES['machineHunter']['background'])},
        {'name': 'Cosmic Nebula', 'css': clean(GRADIENT_THEMES['cosmicNebula']['background'])},
        {'name': 'PlayStation Blue', 'css': clean(GRADIENT_THEMES['playstationBlue']['background'])},
        {'name': 'Game Art', 'image': 'https://image.api.playstation.com/vulcan/ap/rnd/202101/2921/x64hEmgvhgxpXc9z9hpyLAyQ.jpg'},
    ]

    return render(request, 'users/subscribe.html', context)

@login_required
def subscribe_success(request):
    session_id = request.GET.get('session_id')
    if session_id:
        try:
            session = stripe.checkout.Session.retrieve(session_id)
            messages.success(request, "Subscription activated! Enjoy premium features.")
        except stripe.error.StripeError as e:
            messages.error(request, f"Error verifying subscription: {str(e)}")
    return redirect('home')

@csrf_exempt
@require_POST
def stripe_webhook(request):
    payload = request.body
    sig_header = request.META.get('HTTP_STRIPE_SIGNATURE')
    if not sig_header:
        return HttpResponse(status=400)
    event = None

    try:
        event = stripe.Webhook.construct_event(payload, sig_header, settings.DJSTRIPE_WEBHOOK_SECRET)
    except ValueError as e:
        logger.error(f"Webhook payload invalid: {e}")
        return HttpResponse(status=400)
    except stripe.error.SignatureVerificationError as e:
        logger.error(f"Webhook signature verification failed: {e}")
        return HttpResponse(status=400)
    
    dj_event = DJStripeEvent.process(event)

    # Delegate all subscription-related events to SubscriptionService
    SubscriptionService.handle_webhook_event(event.type, event.data.object)

    return HttpResponse(status=200)

class SubscriptionManagementView(LoginRequiredMixin, TemplateView):
    template_name = 'users/subscription_management.html'

    def get_context_data(self, **kwargs):
        is_live = settings.STRIPE_MODE == 'live'

        context = super().get_context_data(**kwargs)
        user = self.request.user
        sub = Subscription.objects.filter(customer__subscriber=user).first()
        if sub and sub.status == 'active':
            context['tier'] = user.get_premium_tier()
            context['premium_tier_slug'] = user.premium_tier
            context['status'] = sub.status.capitalize()
            context['next_billing'] = sub.current_period_end if sub.current_period_end else 'N/A'

            portal_session = stripe.billing_portal.Session.create(
                customer=user.stripe_customer_id,
                return_url=self.request.build_absolute_uri(reverse('profile_detail', kwargs={'psn_username': user.profile.psn_username if hasattr(user, 'profile') else ''}))
            )
            context['portal_url'] = portal_session.url
        else:
            context['tier'] = 'None'
            context['status'] = 'Inactive' if sub else 'No Subscription'
        context['is_live'] = is_live

        track_page_view('subscription', 'user', self.request)
        return context


class EmailPreferencesView(View):
    """
    Standalone view for managing email preferences via token-based authentication.

    Users can access this page from email links without logging in.
    Token validation ensures security while providing a frictionless experience.
    """
    template_name = 'users/email_preferences.html'

    def get(self, request):
        """
        Display email preferences form.

        Validates token from URL parameter and pre-fills form with user's current preferences.
        """
        from django.core import signing
        from users.services.email_preference_service import EmailPreferenceService

        token = request.GET.get('token')
        context = {
            'site_url': settings.SITE_URL,
            'error_message': None,
            'form': None,
            'saved': False,
        }

        # Validate token
        if not token:
            context['error_message'] = 'No preference token provided. Please use the link from your email.'
            return render(request, self.template_name, context)

        try:
            user_id = EmailPreferenceService.validate_preference_token(token)
            user = CustomUser.objects.get(id=user_id)
        except signing.SignatureExpired:
            context['error_message'] = 'This link has expired. Links are valid for 90 days. Please use a newer email or log in to update your preferences.'
            return render(request, self.template_name, context)
        except (signing.BadSignature, ValueError):
            context['error_message'] = 'This link is invalid or has been tampered with. Please use the link from your email.'
            return render(request, self.template_name, context)
        except CustomUser.DoesNotExist:
            context['error_message'] = 'User not found. This link may be invalid.'
            return render(request, self.template_name, context)

        # Get user's current preferences
        preferences = EmailPreferenceService.get_user_preferences(user)

        # Pre-fill form with current preferences
        from users.forms import EmailPreferencesForm
        form = EmailPreferencesForm(initial=preferences)

        context['form'] = form
        context['user_email'] = user.email
        track_page_view('email_prefs', 'user', request)
        return render(request, self.template_name, context)

    def post(self, request):
        """
        Save updated email preferences.

        Validates token, processes form data, and updates user preferences.
        """
        from django.core import signing
        from users.services.email_preference_service import EmailPreferenceService
        from users.forms import EmailPreferencesForm

        token = request.GET.get('token')
        context = {
            'site_url': settings.SITE_URL,
            'error_message': None,
            'form': None,
            'saved': False,
        }

        # Validate token (same as GET)
        if not token:
            context['error_message'] = 'No preference token provided.'
            return render(request, self.template_name, context)

        try:
            user_id = EmailPreferenceService.validate_preference_token(token)
            user = CustomUser.objects.get(id=user_id)
        except signing.SignatureExpired:
            context['error_message'] = 'This link has expired. Please use a newer email.'
            return render(request, self.template_name, context)
        except (signing.BadSignature, ValueError):
            context['error_message'] = 'This link is invalid.'
            return render(request, self.template_name, context)
        except CustomUser.DoesNotExist:
            context['error_message'] = 'User not found.'
            return render(request, self.template_name, context)

        # Process form
        form = EmailPreferencesForm(request.POST)
        if form.is_valid():
            # Update preferences
            preferences = {
                'monthly_recap': form.cleaned_data.get('monthly_recap', False),
                'badge_notifications': form.cleaned_data.get('badge_notifications', False),
                'milestone_notifications': form.cleaned_data.get('milestone_notifications', False),
                'admin_announcements': form.cleaned_data.get('admin_announcements', False),
                'global_unsubscribe': form.cleaned_data.get('global_unsubscribe', False),
            }

            EmailPreferenceService.update_user_preferences(user, preferences)

            # Show success message and re-render form with updated values
            context['saved'] = True
            context['form'] = EmailPreferencesForm(initial=preferences)
            context['user_email'] = user.email
            return render(request, self.template_name, context)
        else:
            # Form validation failed, re-display with errors
            context['form'] = form
            context['user_email'] = user.email
            return render(request, self.template_name, context)