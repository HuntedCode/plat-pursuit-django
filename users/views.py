# users/views.py
import json
from datetime import datetime

from allauth.account.views import ConfirmEmailView
from django.conf import settings
from django.core import signing
from django.core.cache import cache
from django.db.models import Sum
from django.utils import timezone
from django.contrib import messages
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.views import redirect_to_login
from django.http import HttpResponse, HttpResponseRedirect
from django.shortcuts import render, redirect
from django.urls import reverse
from django.views import View
from django.views.generic import TemplateView
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from djstripe.models import Price, Subscription
from djstripe.models import Event as DJStripeEvent
import stripe
import logging
from fundraiser.models import get_live_fundraiser
from users.constants import CURRENT_BETA, PAYPAL_PLANS, PREMIUM_PERKS, PREMIUM_TIER_DISPLAY
from users.forms import UserSettingsForm, CustomPasswordChangeForm, EmailPreferencesForm
from users.services.email_preference_service import EmailPreferenceService
from users.services.subscription_service import SubscriptionService
from users.models import CustomUser
from trophies.forms import ProfileSettingsForm
from trophies.services.profile_stats_service import update_profile_trophy_counts

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
        profile_form = ProfileSettingsForm(instance=profile) if profile else None

        context = {
            'user_form': user_form,
            'password_form': password_form,
            'profile_form': profile_form,
            'profile': profile,
            'breadcrumb': [
                {'text': 'Home', 'url': '/'},
                {'text': 'Settings'},
            ],
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
    
class SupportStorefrontView(TemplateView):
    """`/support/` -- the Support hub landing AND the membership storefront, deliberately one page.

    It lives here rather than in `core.views` because the checkout POST lives here: the form carries
    no `action`, so it self-POSTs to whatever URL rendered it. Serving the form from `/support/`
    while the handler stayed at `/users/subscribe/` would mean a redirect on a POST -- which browsers
    turn into a GET with the body dropped, silently breaking checkout. The handler goes where the
    form is rendered. `/users/subscribe/` is now a 302 in (302, not 301: nothing should POST there
    any more, but a cached permanent redirect on a payment URL is unrecoverable if that assumption
    ever breaks).

    PUBLIC, and it stays rendered for members. Both are changes from the old `subscribe` view:

    - Anonymous visitors see the entire pitch. A support page you must log in to read cannot do the
      one job it has. The buy controls become a sign-in link that returns here.
    - Active members are no longer bounced to `subscription_management`. This page is also the hub
      landing (and soon holds the roadmap and fundraiser), so redirecting members off it makes those
      unreachable for exactly the people who paid for them. They get a thank-you state instead.
    """
    template_name = 'support/support_hub.html'

    # Tier order on the page. Monthly first (the default expectation), then yearly, then supporter --
    # ascending commitment. `supporter` shipped purchasable but BUTTONLESS: it has Stripe products, a
    # PayPal plan, a Discord role and bespoke styling on the management page, and `POST tier=supporter`
    # has always worked. It simply had no UI, so nobody could choose it.
    TIER_ORDER = ('premium_monthly', 'premium_yearly', 'supporter')

    def _today(self):
        """The live catalogue figures beat 2 opens on: trophies, games, hunters.

        Read off the hourly site heartbeat rather than queried here -- these are three of the most
        expensive counts on the site and this is a public page anyone can hammer. `get_cached_heartbeat`
        already falls back to the previous hour's bucket.

        Returns None when BOTH buckets are cold, and the template omits the sentence entirely rather
        than printing zeroes at a first-time reader. Same gate `badge_how_it_works` uses on its
        catalogue strip, and it matters more here: "tracking 0 trophies for 0 hunters" on the page
        asking you to fund the thing is worse than saying nothing at all.
        """
        from core.services.site_heartbeat import get_cached_heartbeat

        beat = (get_cached_heartbeat() or {}).get('always') or {}
        figures = {
            key: (beat.get(source) or {}).get('value')
            for key, source in (
                ('trophies', 'trophies_total'),
                ('games', 'games_total'),
                ('hunters', 'profiles_total'),
            )
        }
        return figures if all(figures.values()) else None

    def _prices(self):
        """Tier -> djstripe Price, or {} when pricing is unavailable.

        `get_prices_from_stripe` does `Price.objects.get()` per tier and lets `DoesNotExist` fly. The
        old view answered that by redirecting the WHOLE page to home, so one missing price took down
        the pitch, the fundraiser and everything else with it. Now the page renders and only the
        pricing block degrades. That also makes this page testable for the first time -- there are no
        djstripe Price rows in the test DB, so previously it always redirected.
        """
        try:
            return SubscriptionService.get_prices_from_stripe(settings.STRIPE_MODE == 'live')
        except Price.DoesNotExist:
            logger.exception("Storefront pricing unavailable in mode %s", settings.STRIPE_MODE)
            return {}

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        prices = self._prices()

        # `cta` is derived here rather than branched in the template: Stripe gives us 'month'/'year'
        # and the English for those is irregular enough ("monthly"/"yearly") that a template
        # conditional per tier is how the third option ends up mislabelled as the first.
        cta = {'month': 'Support monthly', 'year': 'Support yearly'}
        context['tiers'] = [
            {
                'slug': slug,
                'name': PREMIUM_TIER_DISPLAY[slug],
                'price': (prices[slug].stripe_data or {}).get('unit_amount', 0) / 100,
                'interval': interval,
                'cta': cta.get(interval, 'Support PlatPursuit'),
            }
            for slug, interval in (
                (s, ((prices[s].stripe_data or {}).get('recurring') or {}).get('interval'))
                for s in self.TIER_ORDER if s in prices
            )
        ]
        context['pricing_available'] = bool(context['tiers'])
        context['is_live'] = settings.STRIPE_MODE == 'live'

        paypal_mode = 'live' if getattr(settings, 'PAYPAL_MODE', '') == 'live' else 'sandbox'
        context['paypal_available'] = (
            bool(getattr(settings, 'PAYPAL_CLIENT_ID', None))
            and any(PAYPAL_PLANS.get(paypal_mode, {}).values())
        )

        # `has_active_subscription` reads `user.stripe_customer_id`, which AnonymousUser has not got.
        context['viewer_is_member'] = (
            SubscriptionService.has_active_subscription(user)[0] if user.is_authenticated else False
        )

        context['premium_perks'] = PREMIUM_PERKS
        context['today'] = self._today()
        # Show the work rather than list it. `badge_subject_art` returns the commissioned SUBJECT
        # drawings (one per series, avatar submissions skipped, bounded scan) -- the part an artist
        # actually drew, which is the only genuinely beautiful object this page can put in front of
        # somebody. Empty on a fresh catalogue, and the band is omitted rather than faked.
        from trophies.views.badge_views import badge_subject_art
        context['badge_art'] = badge_subject_art(limit=5)
        context['current_beta'] = CURRENT_BETA
        context['support_fundraiser'] = get_live_fundraiser()
        return context

    def post(self, request, *args, **kwargs):
        """Start a checkout. The payload contract is unchanged from the old view: `tier` from the
        submit button's name/value, `provider` from a hidden input, CSRF from the form."""
        if not request.user.is_authenticated:
            return redirect_to_login(request.get_full_path())

        # Double-subscribe guard across ALL providers. Was a page-level redirect; now it only blocks
        # the purchase, since the page itself is legitimate for a member to be reading.
        if SubscriptionService.has_active_subscription(request.user)[0]:
            messages.info(request, 'You already have an active subscription. Manage it here.')
            return redirect('subscription_management')

        tier = request.POST.get('tier')
        provider = request.POST.get('provider', 'stripe')

        if tier not in self._prices():
            messages.error(request, "Invalid tier selected.")
            return redirect('support_hub')

        # Built with reverse() rather than the string literals these used to be, so the pair cannot
        # drift apart from the URL conf. `{CHECKOUT_SESSION_ID}` is a Stripe-side placeholder Stripe
        # substitutes on redirect -- it must reach them un-interpolated.
        success_url = request.build_absolute_uri(reverse('subscribe_success'))
        cancel_url = request.build_absolute_uri(reverse('support_hub'))

        if provider == 'paypal':
            from users.services.paypal_service import PayPalService
            try:
                approval_url = PayPalService.create_subscription(
                    user=request.user,
                    tier=tier,
                    return_url=f'{success_url}?provider=paypal',
                    cancel_url=cancel_url,
                )
                return redirect(approval_url)
            except Exception:
                logger.exception("PayPal subscription creation failed")
                messages.error(request, "Error creating PayPal subscription. Please try again.")
                return redirect('support_hub')

        try:
            session_url = SubscriptionService.create_checkout_session(
                user=request.user,
                tier=tier,
                success_url=success_url + "?session_id={CHECKOUT_SESSION_ID}",
                cancel_url=cancel_url,
            )
            # 303, not 302: this answers a POST, and 303 is the status that unambiguously tells
            # the client to GET the new location. It was WRITTEN as `redirect(session_url, code=303)`
            # -- but `django.shortcuts.redirect` has no `code` parameter, so that kwarg was swallowed
            # into `resolve_url(**kwargs)`, ignored there because the target is already an absolute
            # URL, and every checkout has quietly been a 302. Harmless in practice (browsers downgrade
            # a 302 POST to GET anyway) but it never did what it said.
            return HttpResponseRedirect(session_url, status=303)
        except stripe.error.StripeError as e:
            messages.error(request, f"Error creating checkout: {str(e)}")
            return redirect('support_hub')

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
        # Same source of truth as the storefront. These two pages had hand-written perk lists that
        # had already drifted apart from each other AND from what the site actually does.
        context['premium_perks'] = PREMIUM_PERKS

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

        context['breadcrumb'] = [
            {'text': 'Home', 'url': '/'},
            {'text': 'Settings', 'url': reverse('settings')},
            {'text': 'My Premium'},
        ]

        return context


class EmailPreferencesRedirectView(LoginRequiredMixin, View):
    """
    Redirect logged-in users to the token-based email preferences page.
    Generates a fresh preference token and redirects to EmailPreferencesView.
    """

    def get(self, request):
        from users.services.email_preference_service import EmailPreferenceService
        token = EmailPreferenceService.generate_preference_token(request.user.id)
        return redirect(f"{reverse('email_preferences')}?token={token}")


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
                'subscription_notifications': form.cleaned_data.get('subscription_notifications', False),
                'admin_announcements': form.cleaned_data.get('admin_announcements', False),
                'weekly_digest': form.cleaned_data.get('weekly_digest', False),
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