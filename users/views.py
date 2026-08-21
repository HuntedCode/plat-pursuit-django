# users/views.py
import json
from datetime import datetime
from datetime import timezone as dt_timezone

from allauth.account.views import ConfirmEmailView
from django.conf import settings
from django.core import signing
from django.core.cache import cache
from django.db.models import Case, Count, IntegerField, Value, When
from django.db.models.functions import Lower
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
from users.constants import (ACTIVE_PREMIUM_TIERS, LADDER_SLUGS,
                             LEGACY_TIER_LEVEL_MAP, PAYPAL_LADDER_PLANS,
                             PREMIUM_PERKS, SUPPORT_TIERS,
                             SUPPORT_TIERS_ARE_PLACEHOLDERS)
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


    def _today(self):
        """The serve band's live figures: hunters, trophies, platinums, hours.

        Read off the hourly site heartbeat rather than queried here -- these are three of the most
        expensive counts on the site and this is a public page anyone can hammer. `get_cached_heartbeat`
        already falls back to the previous hour's bucket.

        Returns None when BOTH buckets are cold, and the template omits the sentence entirely rather
        than printing zeroes at a first-time reader. Same gate `badge_how_it_works` uses on its
        catalogue strip, and it matters more here: "tracking 0 trophies for 0 hunters" on the page
        asking you to fund the thing is worse than saying nothing at all.
        """
        from core.services.site_heartbeat import get_cached_heartbeat

        beat = get_cached_heartbeat() or {}
        # `hours_hunted` and `platinums_total` live in the expanded block, the other two in `always`,
        # so both are merged before the lookup rather than the caller knowing which is where.
        cells = {**(beat.get('always') or {}), **(beat.get('expanded') or {})}
        figures = {
            key: (cells.get(source) or {}).get('value')
            for key, source in (
                ('hunters', 'profiles_total'),
                ('trophies', 'trophies_total'),
                ('platinums', 'platinums_total'),
                ('hours', 'hours_hunted'),
            )
        }
        return figures if all(figures.values()) else None

    def _prices(self):
        """Tier -> djstripe Price, or {} when pricing is unavailable. Memoized per request: a cold
        GET consulted this three times (context, `_support`, and POST validation each fetch three
        `Price.objects.get()`s), which is six redundant queries on a public page.

        `get_prices_from_stripe` does `Price.objects.get()` per tier and lets `DoesNotExist` fly. The
        old view answered that by redirecting the WHOLE page to home, so one missing price took down
        the pitch, the fundraiser and everything else with it. Now the page renders and only the
        pricing block degrades. That also makes this page testable for the first time -- there are no
        djstripe Price rows in the test DB, so previously it always redirected.
        """
        if not hasattr(self, '_prices_memo'):
            try:
                self._prices_memo = SubscriptionService.get_prices_from_stripe(
                    settings.STRIPE_MODE == 'live'
                )
            except Price.DoesNotExist:
                logger.exception("Storefront pricing unavailable in mode %s", settings.STRIPE_MODE)
                self._prices_memo = {}
        return self._prices_memo

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        prices = self._prices()

        # The LADDER is design data, not billing data: it comes from the constant so the page can be
        # built and iterated before the twelve Stripe prices and twelve PayPal plans exist. Each row
        # carries both intervals; the template's switch picks which face to show, so swapping tabs is
        # a CSS state change rather than a round trip.
        is_live = settings.STRIPE_MODE == 'live'

        # PLACEHOLDERS CAN NEVER REACH PRODUCTION. The flag is honoured in test mode only, so the
        # worst a stale `SUPPORT_TIERS_ARE_PLACEHOLDERS = True` can do on a live deploy is show the
        # unavailable state -- never a row of dead buy buttons on a page taking money. This is a
        # runtime guard rather than a checklist item on purpose: checklists get skipped.
        placeholders = SUPPORT_TIERS_ARE_PLACEHOLDERS and not is_live

        ladder = [
            dict(tier,
                 # A real range for the star partial to loop. Django templates cannot count, and the
                 # `|rjust` trick that can is a puzzle to read.
                 star_range=range(tier['stars']))
            for tier in SUPPORT_TIERS
            # Live and un-flagged: only offer a level somebody can actually buy -- BOTH intervals
            # must be configured, or the cycle switch would sell a face with nothing behind it.
            if placeholders or (
                SubscriptionService.resolve_ladder_price_id(tier['slug'], 'monthly', is_live)
                and SubscriptionService.resolve_ladder_price_id(tier['slug'], 'yearly', is_live)
            )
        ]
        context['tiers'] = ladder
        context['tiers_are_placeholders'] = placeholders
        # Preselect the second rung. Defaulting to the top reads as grabby; defaulting to the bottom
        # anchors low and quietly costs the difference. The middle is the honest ask.
        context['default_tier'] = ladder[1]['slug'] if len(ladder) > 1 else (ladder[0]['slug'] if ladder else None)
        context['pricing_available'] = bool(ladder)
        context['is_live'] = is_live

        # The LADDER map, not the legacy PAYPAL_PLANS: this button sells ladder levels, so its
        # availability must track the plans it would actually charge against. (With the legacy map
        # the button showed in live mode on the strength of grandfathered plan ids alone.)
        paypal_mode = 'live' if getattr(settings, 'PAYPAL_MODE', '') == 'live' else 'sandbox'
        context['paypal_available'] = (
            bool(getattr(settings, 'PAYPAL_CLIENT_ID', None))
            and any(v for plans in PAYPAL_LADDER_PLANS.get(paypal_mode, {}).values()
                    for v in plans.values())
        )

        # `has_active_subscription` reads `user.stripe_customer_id`, which AnonymousUser has not got.
        context['viewer_is_member'] = (
            SubscriptionService.has_active_subscription(user)[0] if user.is_authenticated else False
        )

        from users.constants import ROADMAP_FEATURES, ROADMAP_TIERS
        # The band teaser: each certainty tier with its first few feature names -- derived from
        # the SAME constant as the page's sections, so a new tier cannot silently miss the band.
        context['roadmap_teaser'] = [
            {'key': key, 'name': name,
             'feats': [{'key': f['key'], 'name': f['name']}
                       for f in ROADMAP_FEATURES if f['tier'] == key][:3]}
            for key, name, _sub in ROADMAP_TIERS
        ]
        context['premium_perks'] = PREMIUM_PERKS
        context['today'] = self._today()
        context['viewer_name'] = self._viewer_name()
        # The worn title, shown before the supporter line exactly as a leaderboard row shows it.
        # `displayed_title` is a METHOD and was an N+1 on the hunters wall, but this is ONE profile
        # on a page that renders one row, so a single query is the right cost here.
        profile = getattr(self.request.user, 'profile', None) if self.request.user.is_authenticated else None
        context['viewer_title'] = profile.displayed_title() if profile else None
        # Their real avatar, for the same reason as their real name: the preview's promise is "this
        # is how YOU will appear", and a stand-in silhouette beside a real name half-keeps it.
        context['viewer_avatar'] = profile.avatar_url if profile else None
        context.update(self._support())
        # The header's artwork. `badge_subject_art` returns the commissioned SUBJECT drawings -- one
        # per series, avatar submissions skipped, bounded scan -- which is the part an artist actually
        # drew and the one thing on this page nobody else could show.
        #
        # It is here rather than decorative because visual-identity.md calls the badge artwork the
        # moat: "if the chrome ever fights the art, the chrome loses". A Support page with no art on
        # it was the only surface on the site in that state, and it read exactly as flat as that
        # sounds. Empty on a fresh catalogue, and the row is omitted rather than faked.
        from trophies.views.badge_views import badge_subject_art
        context['badge_art'] = badge_subject_art(limit=5)
        return context

    def _viewer_name(self):
        """The name to show in the level preview.

        Their OWN name wearing the mark they are about to pick is far more persuasive than a stand-in,
        and it costs nothing: it is a string already on the request, not a lookup. Falls back through
        the display name, the PSN name, then the username, and finally to a stand-in for anonymous
        visitors -- who still need to see what the preview is showing them.
        """
        user = self.request.user
        if not user.is_authenticated:
            return 'YourName'
        profile = getattr(user, 'profile', None)
        if profile is not None:
            name = profile.display_psn_username or profile.psn_username
            if name:
                return name
        return user.username or 'YourName'

    SUPPORT_CACHE_KEY = 'support:stats'
    SUPPORT_TTL = 300
    LAUNCH = (2026, 1)

    def _support(self):
        """How the site is paid for: supporters, monthly support, months running, ads served.

        COUNTS THE LEGACY TIERS TOO, and has to. Every real supporter today holds `premium_monthly`,
        `premium_yearly` or `supporter`; nobody holds a ladder slug and nobody will until the twelve
        SKUs exist and people move. A band that counted only the new ladder would read zero on a live
        site, which is worse than not having one.

        The money is a MONTHLY EQUIVALENT so one figure means one thing: a yearly pledge is divided
        by twelve rather than counted whole. Legacy prices come from Stripe (the only place they
        live); ladder prices come from the constant.

        Returns `supporter_monthly = None` rather than 0 when Stripe prices are unavailable. Zero
        would be a claim -- "nobody is paying" -- where None is the truth: "we cannot say right now",
        and the template drops that one cell instead of publishing a wrong number.
        """
        data = cache.get(self.SUPPORT_CACHE_KEY)
        if data is not None:
            return self._hydrate(data)

        ladder = {t['slug']: t['monthly'] for t in SUPPORT_TIERS}
        # DB-aggregated group-by, never a Python tally over rows.
        counts = dict(
            CustomUser.objects
            .filter(premium_tier__in=list(ACTIVE_PREMIUM_TIERS) + list(ladder))
            .values_list('premium_tier')
            .annotate(n=Count('id'))
        )

        prices = self._prices()
        monthly, priced_all = 0, True
        for slug, n in counts.items():
            if slug in ladder:
                monthly += ladder[slug] * n
                continue
            price = prices.get(slug)
            if price is None:
                priced_all = False
                continue
            stripe_data = price.stripe_data or {}
            amount = (stripe_data.get('unit_amount') or 0) / 100
            if ((stripe_data.get('recurring') or {}).get('interval')) == 'year':
                amount /= 12
            monthly += amount * n

        now = timezone.now()
        data = {
            'supporter_count': sum(counts.values()),
            'supporter_monthly': round(monthly) if priced_all else None,
            'months_running': (now.year - self.LAUNCH[0]) * 12 + (now.month - self.LAUNCH[1]),
            'wall': self._wall(),
        }
        cache.set(self.SUPPORT_CACHE_KEY, data, self.SUPPORT_TTL)
        return self._hydrate(data)

    @staticmethod
    def _hydrate(data):
        """Rebuild the rich tier dicts on the wall AFTER every cache read.

        The cached payload must hold JSON primitives only. It once carried the full tier dict
        including `star_range=range(...)` -- and production's Redis cache serializes with
        `JSONSerializer`, which cannot encode a `range`. Every request then raised on `cache.set`,
        so the payment landing 500'd permanently the moment the wall had anyone on it. Nothing
        pre-production could see it: the test cache is LocMem (pickle swallows ranges) and dev has
        no supporters, so the payload never contained one. `test_the_cached_support_payload_is_json_
        serializable` now locks the boundary.
        """
        by_slug = {t['slug']: dict(t, star_range=range(t['stars'])) for t in SUPPORT_TIERS}
        return dict(data, wall=[
            dict(person, tier=by_slug.get(person['tier_slug'])) for person in data['wall']
        ])

    @staticmethod
    def _worn_level(premium_tier):
        """The ladder slug a supporter WEARS: their own level, or for a grandfathered legacy tier
        the price-nearest level from LEGACY_TIER_LEVEL_MAP. None is structurally unreachable for
        wall rows today (the map-coverage test forbids an unmapped eligible tier); the 'Supporter'
        fallback it feeds is defence-in-depth, not a live path."""
        if premium_tier in LADDER_SLUGS:
            return premium_tier
        return LEGACY_TIER_LEVEL_MAP.get(premium_tier)

    WALL_CAP = 200

    def _wall(self):
        """The credits: who is keeping the site running, by name. Returns JSON PRIMITIVES ONLY
        (name / avatar / tier_slug) -- this goes straight into the cache, and the serializability
        boundary is documented on `_hydrate`, which rebuilds the rich tier dicts on the way out.

        CONSENT IS THE WHOLE DESIGN HERE. A PSN name is already public everywhere on this site; the
        fact that somebody PAYS is not, and publishing it is new information about a person. So the
        credits only ever show profiles with `show_on_supporter_wall` set, which defaults True to
        auto-opt-in the people who were already supporting when this shipped, and which anyone can
        turn off from subscription management.

        WHO IS ELIGIBLE: everyone supporting, ladder level or legacy tier. See the note on
        SUPPORT_TIERS for why the bottom rungs are included.

        ORDERED AND CAPPED IN THE DATABASE, rank first. The cap used to slice the first 200 by
        alphabet BEFORE the Python rank sort, so past 200 supporters a Cornerstone named "zed" was
        cut while a Backer named "aaa" stayed -- quietly breaking "credits run highest level first".
        The Case/When puts the top level at 0 and everything pre-ladder after the ladder, so the cap
        can never cut a higher level in favour of a lower one.
        """
        from trophies.models import Profile

        ladder_slugs = [t['slug'] for t in SUPPORT_TIERS]
        eligible = ladder_slugs + list(ACTIVE_PREMIUM_TIERS)
        # Rank by the level a supporter WEARS: legacy tiers sort alongside their mapped level
        # (grandfathered presentation, see LEGACY_TIER_LEVEL_MAP) instead of trailing the ladder.
        # Built from a MERGED DICT so a legacy key colliding with a ladder slug is structurally
        # impossible (a duplicate When would be silently dead); the dict makes last-write-wins
        # explicit and keys unique by construction.
        rank_by_slug = {slug: i for i, slug in enumerate(reversed(ladder_slugs))}
        worn_by_tier = {**{slug: slug for slug in ladder_slugs}, **LEGACY_TIER_LEVEL_MAP}
        rank_order = Case(
            *[When(user__premium_tier=tier, then=Value(rank_by_slug[worn]))
              for tier, worn in worn_by_tier.items() if worn in rank_by_slug],
            # Structurally unreachable today -- the map-coverage test forbids an eligible tier
            # missing from worn_by_tier -- kept as the backstop that makes that test's failure
            # mode graceful rather than a query error.
            default=Value(len(ladder_slugs)),
            output_field=IntegerField(),
        )

        rows = (
            Profile.objects
            .filter(show_on_supporter_wall=True, user__premium_tier__in=eligible)
            .select_related('user')
            .only('display_psn_username', 'psn_username', 'avatar_url', 'user__premium_tier')
            # `Lower()` because a raw sort puts every lowercase name after every uppercase one,
            # which reads as two lists stapled together rather than one alphabetical run.
            .order_by(rank_order, Lower('psn_username'))[:self.WALL_CAP]
        )

        return [
            {
                'name': r.display_psn_username or r.psn_username,
                'avatar': r.avatar_url,
                # The WORN slug, so a legacy tier hydrates into its mapped level's colour and
                # stars (grandfathered presentation). The None/'Supporter' fallback is a
                # structural backstop the map-coverage test keeps unreachable.
                'tier_slug': self._worn_level(r.user.premium_tier),
            }
            for r in rows
        ]

    def post(self, request, *args, **kwargs):
        """Start a checkout.

        The payload: `tier` from the amount RADIOS, `provider` from whichever SUBMIT BUTTON was
        pressed (each button carries its own name/value), CSRF from the form -- which exists and
        wraps the whole box. An earlier version of this docstring described that form while the
        template did not have one; `test_the_checkout_is_a_real_form` now keeps the two honest.
        """
        if not request.user.is_authenticated:
            return redirect_to_login(request.get_full_path())

        # Double-subscribe guard across ALL providers. Was a page-level redirect; now it only blocks
        # the purchase, since the page itself is legitimate for a member to be reading.
        if SubscriptionService.has_active_subscription(request.user)[0]:
            messages.info(request, 'You already have an active subscription. Manage it here.')
            return redirect('subscription_management')

        tier = request.POST.get('tier')
        provider = request.POST.get('provider', 'stripe')
        # The cycle radio, finally read. It always rode along in the payload; the server priced
        # everything as monthly regardless, which would have billed a Yearly pick monthly.
        cycle = request.POST.get('sup-cycle', 'monthly')

        # An unknown provider used to FALL THROUGH to Stripe (only `paypal` was branched on), which
        # would quietly charge somebody through a processor they did not pick. Reject instead.
        if provider not in ('stripe', 'paypal'):
            messages.error(request, "Unknown payment provider.")
            return redirect('support_hub')

        if cycle not in ('monthly', 'yearly'):
            messages.error(request, "Unknown billing cycle.")
            return redirect('support_hub')

        # LADDER-ONLY. Grandfathering means the three legacy tiers stay renewable through webhooks
        # but are no longer purchasable: this validation deliberately stopped admitting them.
        if tier not in LADDER_SLUGS:
            messages.error(request, "Invalid tier selected.")
            return redirect('support_hub')

        # Availability. THE SAME live-mode override as get_context_data: the placeholder flag is
        # honoured in test mode only, so a stale True on a live deploy shows the unavailable state
        # on GET -- and must equally refuse the POST, or the guard would render dead buttons while
        # accepting direct posts against unconfigured tiers. And the check is per-PROVIDER: a
        # PayPal purchase admitted because a *Stripe* price existed would send somebody to a
        # processor with nothing configured behind it (or worse, with a different mode's plans --
        # STRIPE_MODE and PAYPAL_MODE are independent settings).
        placeholders = SUPPORT_TIERS_ARE_PLACEHOLDERS and settings.STRIPE_MODE != 'live'
        if not placeholders:
            if provider == 'paypal':
                paypal_mode = 'live' if getattr(settings, 'PAYPAL_MODE', '') == 'live' else 'sandbox'
                configured = (PAYPAL_LADDER_PLANS.get(paypal_mode, {}).get(tier) or {}).get(cycle)
            else:
                configured = SubscriptionService.resolve_ladder_price_id(
                    tier, cycle, settings.STRIPE_MODE == 'live')
            if not configured:
                messages.error(request, "That option is not available right now.")
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
                    interval=cycle,
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
                interval=cycle,
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
        except Exception:
            # ValueError (tier unconfigured) and Price.DoesNotExist (id not yet synced into
            # djstripe) both live on this path; neither deserves a 500 on a page taking money.
            logger.exception("Stripe checkout creation failed")
            messages.error(request, "Could not start checkout. Please try again.")
            return redirect('support_hub')

class SupportRoadmapView(TemplateView):
    """`/support/roadmap/` -- the forward list in the platinum-roadmap frame.

    Upcoming features as compact icon cards in three certainty tiers (ROADMAP_TIERS: in the
    works / up next / the wishlist), nothing backward-looking -- the header's lede sentence
    carries the whole history. Pure constants, zero queries for anonymous visitors.

    Content rules, test-enforced: no dates, months, quarters, counts or percentages anywhere in
    the forward content. TIER is the only promise, and the wishlist's own subline says so.
    """
    template_name = 'support/roadmap.html'

    def get_context_data(self, **kwargs):
        from users.constants import ROADMAP_FEATURES, ROADMAP_TIERS

        context = super().get_context_data(**kwargs)
        # Features grouped per tier HERE rather than filtered in the template: the reveal stagger
        # indexes forloop.counter0, and an outer-loop index made the wishlist's first card wait
        # ~560ms after scrolling into view (the audit's catch) -- per-tier lists restart it at 0.
        context['tiers'] = [
            {'key': key, 'name': name, 'sub': sub,
             'feats': [f for f in ROADMAP_FEATURES if f['tier'] == key]}
            for key, name, sub in ROADMAP_TIERS
        ]
        user = self.request.user
        context['viewer_is_member'] = (
            SubscriptionService.has_active_subscription(user)[0] if user.is_authenticated else False
        )
        return context


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
    
    # Replay guard. Stripe delivers at-least-once; djstripe's Event table is the durable record
    # of what we have already processed. Without this, a redelivered subscription event re-fired
    # the welcome email and notification (send_html_email has no dedupe of its own).
    if DJStripeEvent.objects.filter(id=event.id).exists():
        logger.info(f"Stripe webhook duplicate skipped: {event.id}")
        return HttpResponse(status=200)
    DJStripeEvent.process(event)

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

    # Delegate all subscription-related events to SubscriptionService. Logged, not raised: with
    # the replay guard above, a retry would be skipped anyway (the Event row already exists), so a
    # 500 here buys nothing but noise -- same at-most-once semantics as the PayPal handler below.
    try:
        SubscriptionService.handle_webhook_event(event.type, event.data.object)
    except Exception:
        logger.exception(f"Error processing Stripe webhook event {event.type}")

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
        # cache.add is an atomic set-if-absent, so two concurrent redeliveries cannot both pass
        # the way the old get-then-set pair could. 7 day TTL.
        if not cache.add(cache_key, True, timeout=60 * 60 * 24 * 7):
            logger.info(f"PayPal webhook duplicate skipped: {transmission_id}")
            return HttpResponse(status=200)

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

    def post(self, request, *args, **kwargs):
        """The supporter wall opt-out.

        It lives here rather than in general settings because it is only meaningful while you are
        supporting, and this is the page you are already on when you think about that.

        A plain form post, not an API call: it is one boolean that changes a public listing, and the
        page reload is a useful confirmation that it took. `show_on_supporter_wall` defaults True, so
        this is how somebody who was auto-opted-in takes themselves off.
        """
        profile = getattr(request.user, 'profile', None)
        if profile is not None and 'wall_visibility' in request.POST:
            profile.show_on_supporter_wall = request.POST.get('on_the_wall') == 'yes'
            profile.save(update_fields=['show_on_supporter_wall'])
            # The wall is cached on the Support page; a change nobody can see for five minutes reads
            # as the toggle not working.
            cache.delete(SupportStorefrontView.SUPPORT_CACHE_KEY)
            messages.success(
                request,
                'You are on the supporter wall.' if profile.show_on_supporter_wall
                else 'You have been taken off the supporter wall.'
            )
        return redirect('subscription_management')

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
        profile = getattr(self.request.user, 'profile', None)
        context['on_the_wall'] = profile.show_on_supporter_wall if profile else False
        context['has_profile'] = profile is not None

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
                    # `dt_timezone.utc`, NOT `timezone.utc`: `timezone` here is
                    # django.utils.timezone, whose `utc` alias was REMOVED in Django 5.0 -- this
                    # line raised AttributeError for every active Stripe member since the 5.x
                    # upgrade, and no test covered the active-subscription branch to notice.
                    context['next_billing'] = datetime.fromtimestamp(period_end_ts, tz=dt_timezone.utc)
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