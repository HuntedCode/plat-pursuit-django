"""
Review Hub views.

Displays the Review Hub for a Concept, with DLC-aware trophy group tabs,
recommendation stats, community ratings, and a review feed.
"""
import logging

from django.http import Http404
from django.urls import reverse
from django.views.generic import DetailView

from trophies.mixins import ProfileHotbarMixin, BackgroundContextMixin
from trophies.models import Concept, Review, UserConceptRating, ConceptTrophyGroup
from trophies.services.review_service import ReviewService
from trophies.services.rating_service import RatingService
from trophies.services.concept_trophy_group_service import ConceptTrophyGroupService
from trophies.forms import UserConceptRatingForm

logger = logging.getLogger('psn_api')


class ReviewHubDetailView(ProfileHotbarMixin, BackgroundContextMixin, DetailView):
    """Review Hub detail page for a game concept."""

    model = Concept
    template_name = 'trophies/review_hub_detail.html'
    slug_field = 'slug'
    slug_url_kwarg = 'slug'
    context_object_name = 'concept'

    def get_object(self, queryset=None):
        concept = super().get_object(queryset)

        # Shovelware gate: 404 if all games in concept are shovelware
        games = concept.games.all()
        if games.exists() and not games.exclude(
            shovelware_status__in=['auto_flagged', 'manually_flagged']
        ).exists():
            raise Http404("Review Hub is not available for this game.")

        return concept

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        concept = self.object
        request = self.request

        # Background image
        context['image_urls'] = self.get_background_context(concept=concept)

        # Breadcrumb
        context['breadcrumb'] = [
            {'text': 'Home', 'url': reverse('home')},
            {'text': 'Review Hub'},
            {'text': concept.unified_title},
        ]

        # Trophy groups for tabs
        trophy_groups = list(
            concept.concept_trophy_groups.all().order_by('trophy_group_id')
        )
        context['trophy_groups'] = trophy_groups

        # Active group from query param (default: 'default')
        active_group_id = request.GET.get('group', 'default')
        active_group = None
        for tg in trophy_groups:
            if tg.trophy_group_id == active_group_id:
                active_group = tg
                break

        # Fall back to 'default' if requested group not found
        if active_group is None:
            for tg in trophy_groups:
                if tg.trophy_group_id == 'default':
                    active_group = tg
                    break
            active_group_id = 'default'

        context['active_group'] = active_group
        context['active_group_id'] = active_group_id

        # If no trophy groups exist at all, provide minimal context
        if active_group is None:
            context['recommendation_stats'] = None
            context['community_averages'] = None
            context['user_review'] = None
            context['can_review'] = False
            context['can_review_reason'] = 'No trophy data available yet.'
            context['can_rate'] = False
            context['can_rate_reason'] = 'No trophy data available yet.'
            context['user_rating'] = None
            context['rating_form'] = None
            context['review_count'] = 0
            context['concept_games'] = concept.games.all()[:5]
            context['concept_icon'] = self._get_concept_icon(concept)
            return context

        # Recommendation stats
        context['recommendation_stats'] = ReviewService.get_recommendation_stats(
            concept, active_group
        )

        # Community rating averages
        context['community_averages'] = (
            RatingService.get_cached_community_averages_for_group(concept, active_group)
        )

        # Review count
        context['review_count'] = Review.objects.filter(
            concept=concept,
            concept_trophy_group=active_group,
            is_deleted=False,
        ).count()

        # User-specific context
        profile = self._get_profile(request)
        if profile:
            # User's own review
            context['user_review'] = Review.objects.filter(
                concept=concept,
                concept_trophy_group=active_group,
                profile=profile,
                is_deleted=False,
            ).first()

            # Can review?
            can_review, can_review_reason = (
                ConceptTrophyGroupService.can_review_group(profile, concept, active_group)
            )
            context['can_review'] = can_review
            context['can_review_reason'] = can_review_reason

            # Can rate?
            can_rate, can_rate_reason = (
                ConceptTrophyGroupService.can_rate_group(profile, concept, active_group)
            )
            context['can_rate'] = can_rate
            context['can_rate_reason'] = can_rate_reason

            # User's existing rating (backward compat: base game uses NULL)
            if active_group.trophy_group_id == 'default':
                user_rating = UserConceptRating.objects.filter(
                    profile=profile,
                    concept=concept,
                    concept_trophy_group__isnull=True,
                ).first()
            else:
                user_rating = UserConceptRating.objects.filter(
                    profile=profile,
                    concept=concept,
                    concept_trophy_group=active_group,
                ).first()
            context['user_rating'] = user_rating

            # Rating form (prefilled if editing)
            if can_rate:
                context['rating_form'] = UserConceptRatingForm(instance=user_rating)
            else:
                context['rating_form'] = None
        else:
            context['user_review'] = None
            context['can_review'] = False
            context['can_review_reason'] = None
            context['can_rate'] = False
            context['can_rate_reason'] = None
            context['user_rating'] = None
            context['rating_form'] = None

        # Concept games for header
        context['concept_games'] = concept.games.all()[:5]
        context['concept_icon'] = self._get_concept_icon(concept)

        return context

    @staticmethod
    def _get_profile(request):
        """Get the authenticated user's profile, or None."""
        if (
            request.user.is_authenticated
            and hasattr(request.user, 'profile')
            and request.user.profile
        ):
            return request.user.profile
        return None

    @staticmethod
    def _get_concept_icon(concept):
        """Get the best icon URL for a concept."""
        if concept.concept_icon_url:
            return concept.concept_icon_url
        first_game = concept.games.first()
        if first_game:
            return first_game.get_icon_url()
        return None
