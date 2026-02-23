# users/views.py
import json
from datetime import datetime

from allauth.account.views import ConfirmEmailView
from core.services.tracking import track_page_view
from django.conf import settings
from django.core import signing
from django.utils import timezone
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
from users.forms import UserSettingsForm, CustomPasswordChangeForm, EmailPreferencesForm
from users.services.email_preference_service import EmailPreferenceService
from users.services.subscription_service import SubscriptionService
from users.models import CustomUser
from trophies.forms import TitleSettingsForm, PremiumSettingsForm, ProfileSettingsForm
from trophies.models import Concept, ProfileGame
from trophies.utils import update_profile_trophy_counts

logger = logging.getLogger('users.views')

class CustomConfirmEmailView(ConfirmEmailView):
    def get(self, *args, **kwargs):
        logger.info(f"Confirmation request received: key={kwargs.get('key')}")
        response = super().get(*args, **kwargs)
        logger.info(f"Confirmation response: {response.status_code}")
        return response

    def post(self, *args, **kwargs):
        logger.info(f"POST confirmation: key={kwargs.get('key')}")
        response = super().post(*args, **kwargs)
        logger.info(f"POST response: {response.status_code}")
        return response
    
class SettingsView(LoginRequiredMixin, View):
    template_name = 'users/settings.html'
    login_url = '/login/'

    def get(self, request):
        user_form = UserSettingsForm(instance=request.user)
        password_form = CustomPasswordChangeForm(user=request.user)
        profile = request.user.profile if hasattr(request.user, 'profile') else None
        title_form = TitleSettingsForm(profile=profile) if profile else None
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
            'title_form': title_form,
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
        
        elif action == 'update_title':
            if not hasattr(request.user, 'profile'):
                messages.error(request, 'Link a PSN account first!')
                return redirect('settings')
            profile = request.user.profile
            title_form = TitleSettingsForm(request.POST, profile=profile)
            if title_form.is_valid():
                title_form.save()
                messages.success(request, 'Title updated!')
            else:
                messages.error(request, 'Error updating title.')
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

    # Double-subscribe guard: check ALL providers
    has_active, active_provider = SubscriptionService.has_active_subscription(request.user)
    if has_active:
        messages.info(request, 'You already have an active subscription. Manage it here.')
        return redirect('subscription_management')

    try:
        prices = SubscriptionService.get_prices_from_stripe(is_live)
    except Price.DoesNotExist as e:
        messages.error(request, "Pricing not available in current mode. Contact support.")
        logger.error(f"Price fetch error: {e} in mode {settings.STRIPE_MODE}")
        return redirect('home')

    valid_tiers = list(prices.keys())

    if request.method == 'POST':
        tier = request.POST.get('tier')
        provider = request.POST.get('provider', 'stripe')

        if tier not in valid_tiers:
            messages.error(request, "Invalid tier selected.")
            return redirect('subscribe')

        if provider == 'paypal':
            from users.services.paypal_service import PayPalService
            try:
                approval_url = PayPalService.create_subscription(
                    user=request.user,
                    tier=tier,
                    return_url=request.build_absolute_uri('/users/subscribe/success/?provider=paypal'),
                    cancel_url=request.build_absolute_uri('/users/subscribe/'),
                )
                return redirect(approval_url)
            except Exception:
                logger.exception("PayPal subscription creation failed")
                messages.error(request, "Error creating PayPal subscription. Please try again.")
                return redirect('subscribe')
        else:
            # Stripe checkout
            try:
                session_url = SubscriptionService.create_checkout_session(
                    user=request.user,
                    tier=tier,
                    success_url=request.build_absolute_uri('/users/subscribe/success/') + "?session_id={CHECKOUT_SESSION_ID}",
                    cancel_url=request.build_absolute_uri('/users/subscribe/'),
                )
                return redirect(session_url, code=303)
            except stripe.error.StripeError as e:
                messages.error(request, f"Error creating checkout: {str(e)}")
                return redirect('subscribe')

    context = {'prices': {k: (v.stripe_data or {}).get('unit_amount', 0) / 100 for k, v in prices.items()}}
    context['is_live'] = is_live
    paypal_mode = 'live' if getattr(settings, 'PAYPAL_MODE', '') == 'live' else 'sandbox'
    from users.constants import PAYPAL_PLANS
    context['paypal_available'] = (
        bool(getattr(settings, 'PAYPAL_CLIENT_ID', None))
        and any(PAYPAL_PLANS.get(paypal_mode, {}).values())
    )

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
    provider = request.GET.get('provider', 'stripe')

    if provider == 'paypal':
        # PayPal redirects here after user approves. Activation happens via webhook.
        messages.success(request, "PayPal subscription initiated! Your premium features will activate shortly.")
    else:
        # Stripe checkout session verification
        session_id = request.GET.get('session_id')
        if session_id:
            try:
                session = stripe.checkout.Session.retrieve(session_id)
                if session.payment_status == 'paid':
                    messages.success(request, "Subscription activated! Enjoy premium features.")
                else:
                    messages.warning(request, "Your payment is still being processed. Premium features will activate shortly.")
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

    # Route one-time donation payments before subscription handling
    if event.type == 'checkout.session.completed':
        session_data = event.data.object
        metadata = session_data.get('metadata', {}) if isinstance(session_data, dict) else getattr(session_data, 'metadata', {})
        if metadata.get('type') == 'fundraiser_donation':
            from fundraiser.services.donation_service import DonationService
            try:
                DonationService.handle_stripe_payment_completed(
                    session_data if isinstance(session_data, dict) else session_data.to_dict()
                )
            except Exception:
                logger.exception("Error processing fundraiser donation webhook")
            return HttpResponse(status=200)

    # Delegate all subscription-related events to SubscriptionService
    SubscriptionService.handle_webhook_event(event.type, event.data.object)

    return HttpResponse(status=200)

@csrf_exempt
@require_POST
def paypal_webhook(request):
    """Handle incoming PayPal webhook events."""
    from django.core.cache import cache
    from users.services.paypal_service import PayPalService

    raw_body = request.body
    try:
        event_data = json.loads(raw_body)
    except json.JSONDecodeError:
        logger.error("PayPal webhook: invalid JSON payload")
        return HttpResponse(status=400)

    if not PayPalService.verify_webhook_signature(request.META, raw_body):
        logger.error("PayPal webhook: signature verification failed")
        return HttpResponse(status=400)

    # Idempotency: skip duplicate webhook deliveries (PayPal guarantees at-least-once)
    transmission_id = request.META.get('HTTP_PAYPAL_TRANSMISSION_ID', '')
    if transmission_id:
        cache_key = f'paypal_webhook:{transmission_id}'
        if cache.get(cache_key):
            logger.info(f"PayPal webhook duplicate skipped: {transmission_id}")
            return HttpResponse(status=200)
        cache.set(cache_key, True, timeout=60 * 60 * 24 * 7)  # 7 day TTL

    event_type = event_data.get('event_type', '')
    resource = event_data.get('resource', {})

    logger.info(f"PayPal webhook received: {event_type}")

    # Route one-time donation order events before subscription handling
    if event_type == 'CHECKOUT.ORDER.APPROVED':
        logger.info(f"PayPal order approved (capture pending): {resource.get('id')}")
        return HttpResponse(status=200)

    if event_type == 'PAYMENT.CAPTURE.COMPLETED':
        from fundraiser.services.donation_service import DonationService
        try:
            if DonationService.handle_paypal_capture_completed(resource):
                return HttpResponse(status=200)
        except Exception:
            logger.exception("Error processing fundraiser PayPal capture event")
        # Fall through to subscription handler if not a donation capture

    try:
        PayPalService.handle_webhook_event(event_type, resource)
    except Exception:
        logger.exception(f"Error processing PayPal webhook event {event_type}")

    return HttpResponse(status=200)


@login_required
@require_POST
def paypal_cancel_subscription(request):
    """Cancel the user's active PayPal subscription."""
    from users.services.paypal_service import PayPalService

    user = request.user
    if not user.paypal_subscription_id or user.subscription_provider != 'paypal':
        messages.error(request, "No active PayPal subscription found.")
        return redirect('subscription_management')

    success = PayPalService.cancel_subscription(user.paypal_subscription_id)
    if success:
        messages.success(request, "Your subscription has been cancelled. You will retain access until the end of your current billing period.")
    else:
        messages.error(request, "Error cancelling subscription. Please try through PayPal directly.")

    return redirect('subscription_management')


class SubscriptionManagementView(LoginRequiredMixin, TemplateView):
    template_name = 'users/subscription_management.html'

    def get_context_data(self, **kwargs):
        is_live = settings.STRIPE_MODE == 'live'

        context = super().get_context_data(**kwargs)
        user = self.request.user

        has_active, provider = SubscriptionService.has_active_subscription(user)
        context['subscription_provider'] = provider
        context['is_live'] = is_live

        if provider == 'stripe':
            sub = Subscription.objects.filter(
                customer__id=user.stripe_customer_id, stripe_data__status='active'
            ).first()

            # Fallback: check for past_due subscription so users can still
            # access billing portal to fix their payment method
            if not sub:
                sub = Subscription.objects.filter(
                    customer__id=user.stripe_customer_id, stripe_data__status='past_due'
                ).first()
                if sub:
                    context['payment_past_due'] = True

            if sub:
                stripe_data = sub.stripe_data or {}
                context['tier'] = user.get_premium_tier()
                context['premium_tier_slug'] = user.premium_tier
                context['status'] = str(stripe_data.get('status', 'unknown')).capitalize()
                period_end_ts = stripe_data.get('current_period_end')
                if period_end_ts:
                    context['next_billing'] = datetime.fromtimestamp(period_end_ts, tz=timezone.utc)
                else:
                    context['next_billing'] = 'N/A'

                try:
                    return_url = self.request.build_absolute_uri(
                        reverse('subscription_management')
                    )
                    portal_session = stripe.billing_portal.Session.create(
                        customer=user.stripe_customer_id,
                        return_url=return_url,
                    )
                    context['portal_url'] = portal_session.url
                except stripe.error.StripeError:
                    logger.exception("Failed to create Stripe billing portal session")
                    context['portal_url'] = None
            else:
                context['tier'] = 'None'
                context['status'] = 'No Subscription'

        elif provider == 'paypal':
            from users.services.paypal_service import PayPalService
            context['tier'] = user.get_premium_tier()
            context['premium_tier_slug'] = user.premium_tier

            try:
                sub_details = PayPalService.get_subscription_details(user.paypal_subscription_id)
                paypal_status = sub_details.get('status', 'UNKNOWN')
                context['status'] = paypal_status.capitalize()

                billing_info = sub_details.get('billing_info', {})
                next_billing = billing_info.get('next_billing_time')
                if next_billing:
                    try:
                        context['next_billing'] = datetime.fromisoformat(
                            next_billing.replace('Z', '+00:00')
                        )
                    except (ValueError, AttributeError):
                        context['next_billing'] = next_billing
                else:
                    context['next_billing'] = 'N/A'
            except Exception:
                logger.exception("Error fetching PayPal subscription details")
                context['status'] = 'Active'
                context['next_billing'] = 'N/A'

            context['paypal_cancel_at'] = user.paypal_cancel_at
            context['paypal_manage_url'] = (
                'https://www.paypal.com/myaccount/autopay/'
                if settings.PAYPAL_MODE == 'live'
                else 'https://www.sandbox.paypal.com/myaccount/autopay/'
            )
        else:
            context['tier'] = 'None'
            context['status'] = 'No Subscription'

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
                'subscription_notifications': form.cleaned_data.get('subscription_notifications', False),
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