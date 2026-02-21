"""
Fundraiser API views: donation checkout, badge claiming, and admin claim management.
"""
import json
import logging
from decimal import Decimal, InvalidOperation

from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views import View

from fundraiser.models import Fundraiser, Donation, DonationBadgeClaim
from fundraiser.services.donation_service import DonationService
from trophies.models import Badge

logger = logging.getLogger(__name__)


class CreateDonationView(LoginRequiredMixin, View):
    """POST: Create a Stripe/PayPal checkout session for a one-time donation."""

    def post(self, request, slug):
        fundraiser = get_object_or_404(Fundraiser, slug=slug)

        if not fundraiser.is_live():
            return JsonResponse(
                {'error': 'This fundraiser is not currently accepting donations.'},
                status=400,
            )

        try:
            data = json.loads(request.body)
        except json.JSONDecodeError:
            return JsonResponse({'error': 'Invalid request.'}, status=400)

        # Parse and validate amount
        try:
            amount = Decimal(str(data.get('amount', 0)))
        except (InvalidOperation, TypeError):
            return JsonResponse({'error': 'Invalid donation amount.'}, status=400)

        if amount < fundraiser.minimum_donation:
            return JsonResponse(
                {'error': f'Minimum donation is ${fundraiser.minimum_donation}.'},
                status=400,
            )

        max_donation = Decimal('500')
        if amount > max_donation:
            return JsonResponse(
                {'error': f'Maximum donation is ${max_donation}.'},
                status=400,
            )

        if amount.as_tuple().exponent < -2:
            return JsonResponse({'error': 'Invalid amount precision.'}, status=400)

        provider = data.get('provider', 'stripe')
        if provider not in ('stripe', 'paypal'):
            return JsonResponse({'error': 'Invalid payment provider.'}, status=400)
        is_anonymous = bool(data.get('is_anonymous', False))
        message = str(data.get('message', ''))[:200]

        success_url = request.build_absolute_uri(f'/fundraiser/{slug}/success/')
        cancel_url = request.build_absolute_uri(f'/fundraiser/{slug}/')

        try:
            if provider == 'paypal':
                redirect_url = DonationService.create_paypal_order(
                    fundraiser=fundraiser,
                    user=request.user,
                    amount=amount,
                    is_anonymous=is_anonymous,
                    message=message,
                    return_url=success_url,
                    cancel_url=cancel_url,
                )
            else:
                redirect_url = DonationService.create_stripe_checkout(
                    fundraiser=fundraiser,
                    user=request.user,
                    amount=amount,
                    is_anonymous=is_anonymous,
                    message=message,
                    success_url=success_url,
                    cancel_url=cancel_url,
                )

            return JsonResponse({'redirect_url': redirect_url})

        except Exception:
            logger.exception(f"Failed to create {provider} checkout for fundraiser {slug}")
            return JsonResponse(
                {'error': f'Error creating {provider} checkout. Please try again.'},
                status=500,
            )


class ClaimBadgeView(LoginRequiredMixin, View):
    """POST: Claim a badge series for artwork from available picks."""

    def post(self, request):
        try:
            data = json.loads(request.body)
        except json.JSONDecodeError:
            return JsonResponse({'error': 'Invalid request.'}, status=400)

        badge_id = data.get('badge_id')
        donation_id = data.get('donation_id')

        if not badge_id or not donation_id:
            return JsonResponse(
                {'error': 'Badge and donation are required.'},
                status=400,
            )

        profile = getattr(request.user, 'profile', None)
        if not profile:
            return JsonResponse(
                {'error': 'You need a linked PSN profile to claim badges.'},
                status=400,
            )

        donation = get_object_or_404(
            Donation,
            id=donation_id,
            user=request.user,
            status='completed',
        )

        if not donation.fundraiser.is_live():
            return JsonResponse(
                {'error': 'This fundraiser is not currently accepting badge claims.'},
                status=400,
            )

        try:
            claim = DonationService.claim_badge(donation, profile, badge_id)
            return JsonResponse({
                'success': True,
                'claim': {
                    'id': claim.id,
                    'series_name': claim.series_name,
                    'series_slug': claim.series_slug,
                    'picks_remaining': donation.badge_picks_remaining,
                },
            })
        except ValueError as e:
            return JsonResponse({'error': str(e)}, status=400)


@method_decorator(staff_member_required, name='dispatch')
class UpdateClaimStatusView(View):
    """POST: Update the artwork status of a badge claim (staff only)."""

    def post(self, request):
        try:
            data = json.loads(request.body)
        except json.JSONDecodeError:
            return JsonResponse({'error': 'Invalid request.'}, status=400)

        claim_id = data.get('claim_id')
        new_status = data.get('status')

        if new_status not in ('in_progress', 'completed'):
            return JsonResponse({'error': 'Invalid status.'}, status=400)

        claim = get_object_or_404(DonationBadgeClaim, id=claim_id)
        old_status = claim.status
        claim.status = new_status

        with transaction.atomic():
            update_fields = ['status']
            if new_status == 'completed':
                claim.completed_at = timezone.now()
                update_fields.append('completed_at')

                # Credit the donor on all tiers of this badge series
                Badge.objects.filter(series_slug=claim.series_slug).update(
                    funded_by=claim.profile
                )

            claim.save(update_fields=update_fields)

        logger.info(
            f"Claim {claim_id} ({claim.series_name}) status updated: "
            f"{old_status} -> {new_status} by {request.user.email}"
        )

        # Send notifications when artwork is completed
        if new_status == 'completed':
            DonationService.send_artwork_complete_email(claim)
            DonationService.send_artwork_complete_notification(claim)

        return JsonResponse({
            'success': True,
            'claim_id': claim.id,
            'new_status': new_status,
        })
