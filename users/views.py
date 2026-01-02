# users/views.py
import time
from allauth.account.views import ConfirmEmailView
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import HttpResponse
from django.shortcuts import render, redirect
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from djstripe.models import Price, Customer
from djstripe.models import Event as DJStripeEvent
import stripe
import logging
from users.forms import UserSettingsForm, CustomPasswordChangeForm
from users.models import CustomUser
from trophies.forms import PremiumSettingsForm

logger = logging.getLogger('psn_api')

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
        premium_form = PremiumSettingsForm(instance=profile) if profile else None
        context = {
            'user_form': user_form,
            'password_form': password_form,
            'premium_form': premium_form,
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
        
        elif action == 'update_premium':
            if not hasattr(request.user, 'profile') or not request.user.profile.user_is_premium:
                messages.error(request, 'This feature is for premium users only!')
                return redirect('settings')
            premium_form = PremiumSettingsForm(request.POST, instance=request.user.profile)
            if premium_form.is_valid():
                premium_form.save()
                messages.success(request, 'Premium settings updated successfully!')
            else:
                messages.error(request, 'Error updating background.')
            return redirect('settings')
        
        return redirect('settings')
    
@login_required
def subscribe(request):
    is_live = settings.STRIPE_MODE == 'live'

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
    sig_header = request.META['HTTP_STRIPE_SIGNATURE']
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

    if event.type in ['checkout.session.completed', 'customer.subscription.created', 'invoice.paid']:
        customer_id = event.data.object.get('customer')
        if customer_id:
            user = CustomUser.objects.filter(stripe_customer_id=customer_id).first()
            if user:
                user.update_subscription_status(event.type)
                logger.info(f"Updated tier for user {user.id}")
    
    elif event.type == 'customer.subscription.deleted':
        customer_id = event.data.object.get('customer')
        if customer_id:
            user = CustomUser.objects.filter(stripe_customer_id=customer_id).first()
            if user:
                subscription = event.data.object
                if subscription:
                    user.premium_tier = None
                    user.save()
                    if hasattr(user, 'profile'):
                        user.profile.update_profile_premium(False)
                    logger.info(f"Revoked tier for user {user.id}")

    return HttpResponse(status=200)