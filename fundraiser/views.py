"""
Fundraiser page views: public fundraiser page, donation success, and staff admin.
"""
import json
import logging
from collections import defaultdict
from decimal import Decimal

import stripe

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Count, Max, Q, Sum
from django.db.models.functions import Coalesce, Lower
from django.shortcuts import get_object_or_404, redirect
from django.utils import timezone
from django.views.generic import TemplateView

from trophies.mixins import StaffRequiredMixin

from fundraiser.models import Fundraiser, Donation, DonationBadgeClaim
from trophies.models import Badge, Profile

logger = logging.getLogger(__name__)


class FundraiserView(TemplateView):
    """
    Public fundraiser page with donation form, donor wall, badge tracker,
    and badge claim gallery.

    Access control:
    - Live: visible to all
    - Upcoming: staff-only preview, non-staff redirected with message
    - Ended: read-only archive for all (donation form hidden)
    """
    template_name = 'fundraiser/fundraiser.html'

    def get(self, request, *args, **kwargs):
        self.fundraiser = get_object_or_404(Fundraiser, slug=kwargs['slug'])

        # Upcoming: redirect non-staff with a friendly message
        if self.fundraiser.is_upcoming() and not (request.user.is_authenticated and request.user.is_staff):
            start_str = timezone.localtime(self.fundraiser.start_date).strftime('%B %d, %Y')
            messages.info(
                request,
                f"This fundraiser hasn't started yet. Check back on {start_str}!"
            )
            return redirect('home')

        return super().get(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        fundraiser = self.fundraiser
        context['fundraiser'] = fundraiser

        # Lifecycle state
        context['is_live'] = fundraiser.is_live()
        context['is_upcoming'] = fundraiser.is_upcoming()
        context['is_ended'] = fundraiser.is_ended()
        context['is_staff_preview'] = (
            not fundraiser.is_live()
            and self.request.user.is_authenticated
            and self.request.user.is_staff
        )

        # Fundraiser stats
        completed_donations = Donation.objects.filter(
            fundraiser=fundraiser, status='completed',
        )
        context['total_raised'] = (
            completed_donations.aggregate(total=Sum('amount'))['total'] or 0
        )
        context['donor_count'] = (
            completed_donations.values('user').distinct().count()
        )

        # Donor wall (non-anonymous, completed, grouped by user)
        donor_wall_donations = list(
            completed_donations
            .filter(is_anonymous=False, profile__isnull=False)
            .values('profile_id')
            .annotate(
                total_amount=Sum('amount'),
                donation_count=Count('id'),
                latest_donation=Max('completed_at'),
            )
            .order_by('-total_amount', '-latest_donation')
        )

        # Build donor dicts with profile objects, messages, and badge claims
        profile_ids = [d['profile_id'] for d in donor_wall_donations]
        profiles_by_id = {
            p.id: p
            for p in Profile.objects.filter(id__in=profile_ids)
        }

        # Collect most recent non-empty message per profile
        profile_messages = {}
        for d in (completed_donations
                  .filter(is_anonymous=False, profile_id__in=profile_ids, message__gt='')
                  .order_by('-completed_at')
                  .values('profile_id', 'message')):
            if d['profile_id'] not in profile_messages:
                profile_messages[d['profile_id']] = d['message']

        # Collect badge claims per profile
        profile_claims = defaultdict(list)
        donation_ids_for_wall = list(
            completed_donations
            .filter(is_anonymous=False, profile_id__in=profile_ids)
            .values_list('id', flat=True)
        )
        for claim in (DonationBadgeClaim.objects
                      .filter(donation_id__in=donation_ids_for_wall)
                      .select_related('badge')
                      .order_by('-claimed_at')):
            profile_claims[claim.profile_id].append(claim)

        context['donors'] = [
            {
                'profile': profiles_by_id.get(d['profile_id']),
                'total_amount': d['total_amount'],
                'donation_count': d['donation_count'],
                'latest_donation': d['latest_donation'],
                'message': profile_messages.get(d['profile_id']),
                'badge_claims': profile_claims.get(d['profile_id'], []),
            }
            for d in donor_wall_donations
            if d['profile_id'] in profiles_by_id
        ]

        # Badge artwork campaign specifics
        if fundraiser.campaign_type == 'badge_artwork':
            # All badge claims for this fundraiser, split into completed/pending
            fundraiser_donation_ids = completed_donations.values_list('id', flat=True)
            all_claims = list(
                DonationBadgeClaim.objects
                .filter(donation_id__in=fundraiser_donation_ids)
                .select_related('badge', 'badge__base_badge', 'profile')
                .order_by('-claimed_at')
            )

            completed_claims = []
            pending_claims = []
            for claim in all_claims:
                claim.badge_layers = claim.badge.get_badge_layers() if claim.badge else None
                if claim.status == 'completed':
                    completed_claims.append(claim)
                else:
                    pending_claims.append(claim)

            context['completed_claims'] = completed_claims
            context['pending_claims'] = pending_claims

            # Badge tracker stats
            total_needing_art = Badge.objects.live().filter(
                tier=1,
            ).filter(
                Q(badge_image__isnull=True) | Q(badge_image=''),
            ).exclude(
                series_slug__isnull=True,
            ).exclude(series_slug='').count()

            claimed_count = len(all_claims)
            completed_count = len(completed_claims)
            pending_count = len(pending_claims)

            # total_needing_art includes claimed-but-pending badges (still no image).
            # Completed claims have artwork uploaded, so they're no longer in that query.
            # True total = still needing art + already completed artwork.
            context['badge_tracker'] = {
                'total': total_needing_art + completed_count,
                'claimed': claimed_count,
                'completed': completed_count,
                'pending': pending_count,
            }

            # Available badges for claiming (logged-in users only)
            claimed_badge_ids = DonationBadgeClaim.objects.values_list('badge_id', flat=True)
            context['available_badges'] = (
                Badge.objects.live().filter(tier=1)
                .filter(Q(badge_image__isnull=True) | Q(badge_image=''))
                .exclude(series_slug__isnull=True)
                .exclude(series_slug='')
                .exclude(id__in=claimed_badge_ids)
                .select_related('base_badge')
                .order_by(Lower('name'))
            )

        # User-specific context
        if self.request.user.is_authenticated:
            profile = getattr(self.request.user, 'profile', None)
            if profile:
                user_donations = list(Donation.objects.filter(
                    fundraiser=fundraiser,
                    user=self.request.user,
                    status='completed',
                ).order_by('-completed_at'))

                context['user_donations'] = user_donations
                context['total_donated'] = sum(
                    (d.amount for d in user_donations), Decimal('0')
                )
                context['total_picks_remaining'] = sum(
                    d.badge_picks_remaining for d in user_donations
                )
                context['donation_ids_with_picks'] = json.dumps([
                    d.id for d in user_donations if d.badge_picks_remaining > 0
                ])
                context['user_profile'] = profile
                context['has_contributions'] = len(user_donations) > 0

                # User's badge claims for this fundraiser
                donation_ids = [d.id for d in user_donations]
                if donation_ids:
                    context['user_claims'] = (
                        DonationBadgeClaim.objects
                        .filter(donation_id__in=donation_ids)
                        .select_related('badge')
                        .order_by('-claimed_at')
                    )
                else:
                    context['user_claims'] = []

        # Badge pick divisor for template/JS display
        context['badge_pick_divisor'] = int(Fundraiser.BADGE_PICK_DIVISOR)

        # PayPal availability (just needs client ID for one-time payments)
        context['paypal_available'] = bool(
            getattr(settings, 'PAYPAL_CLIENT_ID', None)
        )

        return context


class DonationSuccessView(LoginRequiredMixin, TemplateView):
    """
    Donation success redirect handler.

    For Stripe: verifies session payment status.
    For PayPal: attempts order capture (hybrid approach).
    Redirects to the fundraiser page with a success message.
    """
    template_name = 'fundraiser/fundraiser.html'  # Fallback, usually redirects

    def get(self, request, *args, **kwargs):
        fundraiser = get_object_or_404(Fundraiser, slug=kwargs['slug'])
        session_id = request.GET.get('session_id')
        paypal_token = request.GET.get('token')

        if session_id:
            # Stripe: verify payment status
            try:
                session = stripe.checkout.Session.retrieve(session_id)
                if session.payment_status == 'paid':
                    # In DEBUG mode, complete the donation on redirect since
                    # webhooks can't reach the local dev server.
                    if settings.DEBUG:
                        from fundraiser.services.donation_service import DonationService
                        donation_id = session.metadata.get('donation_id')
                        if donation_id:
                            donation = Donation.objects.filter(
                                id=int(donation_id), status='pending',
                            ).first()
                            if donation:
                                DonationService.complete_donation(donation)
                                logger.info(f"DEBUG: Completed donation {donation.id} on redirect")

                    messages.success(
                        request,
                        "Thank you for your donation! Your support means the world to us."
                    )
                else:
                    messages.info(
                        request,
                        "Your payment is being processed. We'll confirm shortly."
                    )
            except stripe.error.StripeError:
                messages.info(
                    request,
                    "Your donation is being processed. Thank you!"
                )

        elif paypal_token:
            # PayPal: attempt capture on redirect (webhook as backup)
            from fundraiser.services.donation_service import DonationService
            try:
                capture_data = DonationService.capture_paypal_order(paypal_token)
                if capture_data.get('status') == 'COMPLETED':
                    # Find and complete the donation
                    custom_id = None
                    for pu in capture_data.get('purchase_units', []):
                        for cap in pu.get('payments', {}).get('captures', []):
                            custom_id = cap.get('custom_id')
                            break
                        if custom_id:
                            break

                    if custom_id:
                        donation = Donation.objects.filter(
                            id=int(custom_id), status='pending',
                        ).first()
                        if donation:
                            DonationService.complete_donation(donation)

                    messages.success(
                        request,
                        "Thank you for your donation! Your support is incredible."
                    )
                else:
                    messages.info(
                        request,
                        "Your PayPal payment is being processed."
                    )
            except Exception:
                logger.exception("PayPal capture failed on redirect")
                messages.info(
                    request,
                    "Your donation is being processed via PayPal. We'll confirm shortly."
                )
        else:
            messages.info(request, "Thank you for visiting the fundraiser!")

        return redirect('fundraiser', slug=fundraiser.slug)


class FundraiserAdminView(StaffRequiredMixin, TemplateView):
    """
    Staff admin page for monitoring fundraiser donations and managing claims.

    Fundraiser campaign CRUD is handled via Django admin.
    """
    template_name = 'fundraiser/fundraiser_admin.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        # All fundraisers with stats (annotated to avoid N+1)
        fundraisers = Fundraiser.objects.annotate(
            total_raised=Coalesce(
                Sum('donations__amount', filter=Q(donations__status='completed')),
                Decimal('0'),
            ),
            donor_count=Count(
                'donations__user', filter=Q(donations__status='completed'), distinct=True,
            ),
        )
        fundraiser_stats = []
        for fr in fundraisers:
            fundraiser_stats.append({
                'fundraiser': fr,
                'total_raised': fr.total_raised,
                'donor_count': fr.donor_count,
                'is_live': fr.is_live(),
                'is_upcoming': fr.is_upcoming(),
                'is_ended': fr.is_ended(),
            })
        context['fundraiser_stats'] = fundraiser_stats

        # Selected fundraiser (default to first, or specified by query param)
        selected_slug = self.request.GET.get('campaign')
        if selected_slug:
            selected = Fundraiser.objects.filter(slug=selected_slug).first()
        else:
            selected = fundraisers.first()

        context['selected_fundraiser'] = selected

        if selected:
            # Donations for selected fundraiser
            context['donations'] = (
                selected.donations.all()
                .select_related('profile', 'user')
                .prefetch_related('badge_claims__badge')
                .order_by('-created_at')
            )

            # Stats for selected fundraiser
            completed = selected.donations.filter(status='completed')
            context['total_raised'] = (
                completed.aggregate(total=Sum('amount'))['total'] or 0
            )
            context['total_donors'] = (
                completed.values('user').distinct().count()
            )
            context['pending_count'] = (
                selected.donations.filter(status='pending').count()
            )

            # Badge claims for selected fundraiser
            donation_ids = selected.donations.values_list('id', flat=True)
            context['claims'] = (
                DonationBadgeClaim.objects
                .filter(donation_id__in=donation_ids)
                .select_related('badge', 'profile', 'donation')
                .order_by('status', '-claimed_at')
            )

            context['claims_pending'] = context['claims'].filter(status='claimed').count()
            context['claims_in_progress'] = context['claims'].filter(status='in_progress').count()
            context['claims_completed'] = context['claims'].filter(status='completed').count()

        return context
