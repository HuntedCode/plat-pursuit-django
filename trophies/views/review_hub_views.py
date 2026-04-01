"""
Review Hub views.

ReviewHubLandingView: Discovery page at /reviews/ with stats, trending, and recent feed.
ReviewHubDetailView: Per-concept detail page at /reviews/<slug>/ with ratings and reviews.
RateMyGamesView: Wizard for quickly rating/reviewing platinumed games.
"""
import logging

from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import Http404
from django.urls import reverse
from django.views.generic import DetailView, TemplateView

from trophies.mixins import ProfileHotbarMixin, BackgroundContextMixin
from trophies.models import (
    Concept, ConceptTrophyGroup, EarnedTrophy, Review, Trophy, UserConceptRating,
)
from trophies.services.review_service import ReviewService
from trophies.services.review_hub_service import ReviewHubService
from trophies.services.rating_service import RatingService
from trophies.services.concept_trophy_group_service import ConceptTrophyGroupService
from trophies.forms import UserConceptRatingForm

logger = logging.getLogger('psn_api')


class ReviewHubLandingView(ProfileHotbarMixin, TemplateView):
    """Review Hub landing page with discovery content."""

    template_name = 'trophies/review_hub.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        context['breadcrumb'] = [
            {'text': 'Home', 'url': reverse('home')},
            {'text': 'Review Hub'},
        ]

        context['stats'] = ReviewHubService.get_hub_stats()
        context['most_reviewed'] = ReviewHubService.get_most_reviewed_games(limit=10)
        context['trending_reviews'] = ReviewHubService.get_trending_reviews(days=7, limit=5)

        if (
            self.request.user.is_authenticated
            and hasattr(self.request.user, 'profile')
            and self.request.user.profile
        ):
            profile = self.request.user.profile
            context['unrated_count'] = ReviewHubService.get_unrated_platinum_count(profile)
            context['unreviewed_count'] = ReviewHubService.get_unreviewed_platinum_count(profile)

        context['seo_description'] = (
            "Community reviews and ratings for PlayStation games on Platinum Pursuit."
        )

        return context


class RateMyGamesView(LoginRequiredMixin, ProfileHotbarMixin, TemplateView):
    """Wizard for quickly rating and reviewing platinumed games."""

    template_name = 'trophies/rate_my_games.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        context['breadcrumb'] = [
            {'text': 'Home', 'url': reverse('home')},
            {'text': 'Review Hub', 'url': reverse('reviews_landing')},
            {'text': 'Rate My Games'},
        ]

        profile = getattr(self.request.user, 'profile', None)
        if profile:
            context['unrated_count'] = ReviewHubService.get_unrated_platinum_count(profile)
            context['unreviewed_count'] = ReviewHubService.get_unreviewed_platinum_count(profile)
            context['guidelines_agreed'] = profile.guidelines_agreed
        else:
            context['unrated_count'] = 0
            context['unreviewed_count'] = 0
            context['guidelines_agreed'] = False

        return context


class ReviewHubDetailView(ProfileHotbarMixin, BackgroundContextMixin, DetailView):
    """Review Hub detail page for a game concept."""

    model = Concept
    template_name = 'trophies/review_hub_detail.html'
    slug_field = 'slug'
    slug_url_kwarg = 'slug'
    context_object_name = 'concept'

    def get_object(self, queryset=None):
        concept = super().get_object(queryset)

        # Evaluate once and cache for reuse in get_context_data
        self._concept_games = list(concept.games.all())

        # Shovelware gate: 404 if all games in concept are shovelware
        if self._concept_games and all(
            g.shovelware_status in ('auto_flagged', 'manually_flagged')
            for g in self._concept_games
        ):
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
            {'text': 'Review Hub', 'url': reverse('reviews_landing')},
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
            context['concept_games'] = self._concept_games[:5]
            context['concept_icon'] = self._get_concept_icon(concept)
            context['unique_platforms'] = self._get_unique_platforms(context['concept_games'])
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
            # True only when guidelines are the sole remaining barrier
            context['needs_guidelines'] = (
                not can_review and not profile.guidelines_agreed
                and can_review_reason == "You must agree to the community guidelines before writing a review."
            )

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

            # User's gameplay stats for this concept
            context['user_game_stats'] = self._get_user_game_stats(
                profile, concept
            )

            # Community guidelines agreement status
            context['guidelines_agreed'] = profile.guidelines_agreed
        else:
            context['user_review'] = None
            context['can_review'] = False
            context['can_review_reason'] = None
            context['can_rate'] = False
            context['can_rate_reason'] = None
            context['user_rating'] = None
            context['rating_form'] = None
            context['guidelines_agreed'] = False

        # Condensed trophy list for sidebar
        context['trophy_list'] = self._get_trophy_list(
            concept, active_group, profile
        )

        # Concept games for header
        context['concept_games'] = self._concept_games[:5]
        context['concept_icon'] = self._get_concept_icon(concept)

        context['unique_platforms'] = self._get_unique_platforms(context['concept_games'])

        context['seo_description'] = (
            f"Community reviews and ratings for {concept.unified_title}. "
            f"See what trophy hunters think on Platinum Pursuit."
        )

        return context

    @staticmethod
    def _get_trophy_list(concept, active_group, profile):
        """Build a condensed, deduplicated trophy list for the active group."""
        trophies_qs = Trophy.objects.filter(
            game__concept=concept,
            trophy_group_id=active_group.trophy_group_id,
        ).order_by('trophy_id').values(
            'trophy_id', 'trophy_type', 'trophy_name',
            'trophy_detail', 'trophy_icon_url',
        )

        # Deduplicate by trophy_id (same trophy across multi-region stacks)
        seen_ids = set()
        trophy_list = []
        for t in trophies_qs:
            if t['trophy_id'] not in seen_ids:
                seen_ids.add(t['trophy_id'])
                trophy_list.append(t)

        # Add earned status for authenticated user
        if profile:
            earned_ids = set(
                EarnedTrophy.objects.filter(
                    profile=profile,
                    earned=True,
                    trophy__game__concept=concept,
                    trophy__trophy_group_id=active_group.trophy_group_id,
                ).values_list('trophy__trophy_id', flat=True).distinct()
            )
            for t in trophy_list:
                t['earned'] = t['trophy_id'] in earned_ids
        else:
            for t in trophy_list:
                t['earned'] = False

        return trophy_list

    @staticmethod
    def _get_unique_platforms(games):
        """Deduplicate platforms across multiple Game records (avoids 'PS4 PS4 PS4')."""
        seen = set()
        platforms = []
        for game in games:
            for platform in (game.title_platform or []):
                if platform not in seen:
                    seen.add(platform)
                    platforms.append(platform)
        return platforms

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
    def _get_user_game_stats(profile, concept):
        """Get the authenticated user's gameplay stats for this concept."""
        from trophies.models import ProfileGame, EarnedTrophy
        from django.db.models import Sum, Max

        row = ProfileGame.objects.filter(
            profile=profile,
            game__concept=concept,
        ).aggregate(
            max_progress=Max('progress'),
            total_earned=Sum('earned_trophies_count'),
            total_unearned=Sum('unearned_trophies_count'),
            total_play=Sum('play_duration'),
        )

        earned = row['total_earned'] or 0
        unearned = row['total_unearned'] or 0
        total = earned + unearned
        progress = row['max_progress'] or 0
        play_hours = None
        if row['total_play']:
            play_hours = int(row['total_play'].total_seconds()) // 3600

        # Platinum date (most recent platinum across multi-region stacks)
        plat_trophy = EarnedTrophy.objects.filter(
            profile=profile,
            earned=True,
            trophy__trophy_type='platinum',
            trophy__game__concept=concept,
        ).order_by('-earned_date_time').values_list(
            'earned_date_time', flat=True
        ).first()

        if not total and not plat_trophy:
            return None

        return {
            'earned_trophies': earned,
            'total_trophies': total,
            'progress': progress,
            'play_hours': play_hours,
            'platinum_date': plat_trophy,
        }

    def _get_concept_icon(self, concept):
        """Get the best icon URL for a concept."""
        if concept.concept_icon_url:
            return concept.concept_icon_url
        if self._concept_games:
            return self._concept_games[0].get_icon_url()
        return None
