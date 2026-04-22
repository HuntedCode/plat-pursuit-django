import logging
from core.services.tracking import track_page_view
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Q, F, Case, When, Value, IntegerField, OrderBy, FloatField, Subquery, OuterRef, Avg
from django.db.models.functions import Lower, Coalesce
from django.http import JsonResponse, HttpResponseRedirect
from django.shortcuts import get_object_or_404
from django.urls import reverse_lazy, reverse
from django.views.generic import ListView, View
from urllib.parse import urlencode
from trophies.mixins import ProfileHotbarMixin, HtmxListMixin
from .browse_helpers import annotate_community_ratings
from ..models import Trophy, EarnedTrophy, Profile, UserTrophySelection
from ..forms import TrophySearchForm, TrophyCaseForm
from trophies.util_modules.constants import MODERN_PLATFORMS

logger = logging.getLogger("psn_api")


class TrophiesListView(HtmxListMixin, ProfileHotbarMixin, ListView):
    """
    Display paginated list of trophies with filtering and sorting options.

    Provides trophy browsing functionality with filters for:
    - Trophy type (bronze, silver, gold, platinum)
    - Platform
    - Region
    - Alphabetical letter

    Useful for finding specific trophies or browsing rarest/most common achievements.
    """
    model = Trophy
    template_name = 'trophies/trophy_list.html'
    partial_template_name = 'trophies/partials/trophy_list/browse_results.html'
    paginate_by = 30

    def get_filter_form(self):
        if not hasattr(self, '_filter_form'):
            self._filter_form = TrophySearchForm(self.request.GET)
        return self._filter_form

    def dispatch(self, request, *args, **kwargs):
        if not request.GET:
            if request.user.is_authenticated:
                defaults = (request.user.browse_defaults or {}).get('trophies', {})
                if defaults:
                    return HttpResponseRedirect(
                        reverse('trophies_list') + '?' + urlencode(defaults, doseq=True)
                    )
            # Anonymous or no saved defaults: modern platforms only
            return HttpResponseRedirect(
                reverse('trophies_list') + '?' + urlencode({'platform': MODERN_PLATFORMS}, doseq=True)
            )
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        qs = super().get_queryset().select_related('game')
        form = self.get_filter_form()

        # Sort ASCII names before non-ASCII (English-first for majority userbase)
        qs = qs.annotate(
            is_ascii_name=Case(
                When(trophy_name__regex=r'^[A-Za-z0-9]', then=Value(0)),
                default=Value(1),
                output_field=IntegerField(),
            ),
        )
        order = ['is_ascii_name', Lower('trophy_name')]

        if form.is_valid():
            query = form.cleaned_data.get('query')
            platforms = form.cleaned_data.get('platform')
            types = form.cleaned_data.get('type')
            regions = form.cleaned_data.get('region')
            psn_rarity = form.cleaned_data.get('psn_rarity')
            show_only_platinum = form.cleaned_data.get('show_only_platinum')
            filter_shovelware = form.cleaned_data.get('filter_shovelware')
            sort_val = form.cleaned_data.get('sort')

            if query:
                qs = qs.filter(Q(trophy_name__icontains=query))
            if platforms:
                platform_filter = Q()
                for plat in platforms:
                    platform_filter |= Q(game__title_platform__contains=plat)
                qs = qs.filter(platform_filter)
            if types:
                types_filter = Q()
                for type in types:
                    types_filter |= Q(trophy_type=type)
                qs = qs.filter(types_filter)
            if regions:
                region_filter = Q()
                for r in regions:
                    if r == 'global':
                        region_filter |= Q(game__is_regional=False)
                    else:
                        region_filter |= Q(game__is_regional=True, game__region__contains=r)
                qs = qs.filter(region_filter)
            if psn_rarity:
                psn_rarity_filter = Q()
                for rarity in psn_rarity:
                    psn_rarity_filter |= Q(trophy_rarity=rarity)
                qs = qs.filter(psn_rarity_filter)

            if show_only_platinum:
                qs = qs.filter(game__trophies__trophy_type='platinum').distinct()
            if filter_shovelware:
                qs = qs.exclude(game__shovelware_status__in=['auto_flagged', 'manually_flagged'])


            if sort_val == 'earned':
                order = ['-earned_count', 'is_ascii_name', Lower('trophy_name')]
            elif sort_val == 'earned_inv':
                order = ['earned_count', 'is_ascii_name', Lower('trophy_name')]
            elif sort_val == 'rate':
                order = ['-earn_rate', 'is_ascii_name', Lower('trophy_name')]
            elif sort_val == 'rate_inv':
                order = ['earn_rate', 'is_ascii_name', Lower('trophy_name')]
            elif sort_val == 'psn_rate':
                order = ['-trophy_earn_rate', 'is_ascii_name', Lower('trophy_name')]
            elif sort_val == 'psn_rate_inv':
                order = ['trophy_earn_rate', 'is_ascii_name', Lower('trophy_name')]

        return qs.order_by(*order)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # Breadcrumb
        context['breadcrumb'] = [
            {'text': 'Home', 'url': reverse_lazy('home')},
            {'text': 'Trophies'},
        ]

        context['form'] = self.get_filter_form()
        context['selected_platforms'] = self.request.GET.getlist('platform')
        context['selected_types'] = self.request.GET.getlist('type')
        context['selected_regions'] = self.request.GET.getlist('region')
        context['selected_psn_rarity'] = self.request.GET.getlist('psn_rarity')
        context['show_only_platinum'] = self.request.GET.get('show_only_platinum', '')
        context['filter_shovelware'] = self.request.GET.get('filter_shovelware', '')

        context['seo_description'] = (
            "Search PlayStation trophies on Platinum Pursuit. "
            "Filter by type, rarity, and game to find what you're looking for."
        )

        # Post-pagination: user earned data (1 query, authenticated only)
        page_trophies = context['object_list']
        if self.request.user.is_authenticated and hasattr(self.request.user, 'profile'):
            trophy_ids = [t.id for t in page_trophies]
            earned_ids = set(
                EarnedTrophy.objects.filter(
                    profile=self.request.user.profile,
                    trophy_id__in=trophy_ids,
                    earned=True
                ).values_list('trophy_id', flat=True)
            )
            context['user_earned_ids'] = earned_ids

        # Auto-open filter drawer when any filters are active
        context['has_active_filters'] = any(
            v for k, v in self.request.GET.lists()
            if k not in ('page', 'view') and any(v)
        )

        track_page_view('trophies_list', 'list', self.request)
        return context


class TrophyCaseView(ProfileHotbarMixin, ListView):
    """
    Display user's platinum trophy collection for trophy case selection.

    Shows all platinum trophies earned by a user, allowing them to select
    up to 10 (premium) or 3 (free) trophies to showcase on their profile.
    Provides filtering by game name and pagination.
    """
    model = EarnedTrophy
    template_name = 'trophies/trophy_case.html'
    context_object_name = 'platinums'
    paginate_by = 25

    def get_filter_form(self):
        if not hasattr(self, '_filter_form'):
            self._filter_form = TrophyCaseForm(self.request.GET)
        return self._filter_form

    def get_queryset(self):
        profile = get_object_or_404(Profile, psn_username=self.kwargs['psn_username'].lower())
        self._profile = profile
        qs = EarnedTrophy.objects.filter(
            profile=profile, earned=True, trophy__trophy_type='platinum',
        ).select_related('trophy', 'trophy__game', 'trophy__game__concept', 'trophy__game__concept__igdb_match')

        form = self.get_filter_form()
        if not form.is_valid():
            return qs.order_by(F('earned_date_time').desc(nulls_last=True))

        # --- Filters ---
        query = form.cleaned_data.get('query', '').strip()
        if query:
            qs = qs.filter(trophy__game__title_name__icontains=query)

        filter_val = form.cleaned_data.get('filter', '')
        if filter_val == 'selected':
            selected_ids = list(profile.trophy_selections.values_list('earned_trophy_id', flat=True))
            qs = qs.filter(id__in=selected_ids)

        platforms = form.cleaned_data.get('platform')
        if platforms:
            platform_filter = Q()
            for plat in platforms:
                platform_filter |= Q(trophy__game__title_platform__contains=plat)
            qs = qs.filter(platform_filter)

        genres = form.cleaned_data.get('genres')
        if genres:
            qs = qs.filter(
                trophy__game__concept__concept_genres__genre_id__in=genres,
            ).distinct()
        themes = form.cleaned_data.get('themes')
        if themes:
            qs = qs.filter(
                trophy__game__concept__concept_themes__theme_id__in=themes,
            ).distinct()

        # --- Sort ---
        sort_val = form.cleaned_data.get('sort', 'recent')

        if sort_val == 'oldest':
            qs = qs.order_by(F('earned_date_time').asc(nulls_last=True))
        elif sort_val == 'rarest_psn':
            qs = qs.order_by('trophy__trophy_earn_rate', F('earned_date_time').desc(nulls_last=True))
        elif sort_val == 'rarest_pp':
            qs = qs.order_by('trophy__earn_rate', F('earned_date_time').desc(nulls_last=True))
        elif sort_val == 'alpha':
            qs = qs.order_by(Lower('trophy__game__title_name'))
        elif sort_val == 'rating':
            qs = annotate_community_ratings(qs, 'trophy__game__concept_id')
            qs = qs.filter(_avg_rating__isnull=False)
            qs = qs.order_by('-_avg_rating', Lower('trophy__game__title_name'))
        elif sort_val == 'rating_inv':
            qs = annotate_community_ratings(qs, 'trophy__game__concept_id')
            qs = qs.filter(_avg_rating__isnull=False)
            qs = qs.order_by('_avg_rating', Lower('trophy__game__title_name'))
        elif sort_val == 'played':
            qs = qs.order_by('-trophy__game__played_count', Lower('trophy__game__title_name'))
        elif sort_val == 'played_inv':
            qs = qs.order_by('trophy__game__played_count', Lower('trophy__game__title_name'))
        elif sort_val == 'time_to_beat':
            qs = qs.annotate(
                _time_to_beat=Case(
                    When(
                        trophy__game__concept__igdb_match__status__in=('accepted', 'auto_accepted'),
                        then=F('trophy__game__concept__igdb_match__time_to_beat_completely'),
                    ),
                    default=None,
                    output_field=IntegerField(),
                ),
            )
            qs = qs.order_by(OrderBy(F('_time_to_beat'), nulls_last=True))
        elif sort_val == 'time_to_beat_inv':
            qs = qs.annotate(
                _time_to_beat=Case(
                    When(
                        trophy__game__concept__igdb_match__status__in=('accepted', 'auto_accepted'),
                        then=F('trophy__game__concept__igdb_match__time_to_beat_completely'),
                    ),
                    default=None,
                    output_field=IntegerField(),
                ),
            )
            qs = qs.order_by(OrderBy(F('_time_to_beat'), descending=True, nulls_last=True))
        else:  # 'recent' (default)
            qs = qs.order_by(F('earned_date_time').desc(nulls_last=True))

        return qs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        profile = self._profile
        selected_ids = list(profile.trophy_selections.values_list('earned_trophy_id', flat=True))
        context['form'] = self.get_filter_form()
        context['selected_platforms'] = self.request.GET.getlist('platform')
        context['selected_genres'] = self.request.GET.getlist('genres')
        context['selected_themes'] = self.request.GET.getlist('themes')

        context['breadcrumb'] = [
            {'text': 'Home', 'url': reverse_lazy('home')},
            {'text': 'Profiles', 'url': reverse_lazy('profiles_list')},
            {'text': f"{profile.display_psn_username}", 'url': reverse_lazy('profile_detail', kwargs={'psn_username': profile.psn_username})},
            {'text': 'Trophy Case'}
        ]
        context['profile'] = profile
        context['selected_ids'] = selected_ids
        context['selected_count'] = len(selected_ids)
        context['toggle_selection_url'] = reverse('toggle-selection')

        is_own_profile = self.request.user.is_authenticated and self.request.user.profile == profile
        # Trophy case is now free for all (surfaces via Platinum Trophy Case showcase, 20 slots).
        max_selections = 20 if is_own_profile else 0
        context['max_selections'] = max_selections

        track_page_view('trophy_case', profile.id, self.request)
        return context

class ToggleSelectionView(LoginRequiredMixin, ProfileHotbarMixin, View):
    """
    AJAX endpoint to add or remove trophies from user's trophy case selection.

    Validates:
    - Maximum selection limit (20 for all users; surfaces via profile showcase)
    - Trophy ownership verification
    - Platinum trophy type requirement

    Returns JSON response with success/error status.
    """
    def post(self, request):
        earned_trophy_id = request.POST.get('earned_trophy_id')
        if not earned_trophy_id:
            return JsonResponse({'success': False, 'error': 'earned_trophy_id required.'}, status=400)
        try:
            earned_trophy_id = int(earned_trophy_id)
            profile = request.user.profile
            earned_trophy = EarnedTrophy.objects.get(id=earned_trophy_id)

            if earned_trophy.profile != profile:
                return JsonResponse({'success': False, 'error': 'Unauthorized: Not your trophy'}, status=403)

            max_selections = 20
            current_count = UserTrophySelection.objects.filter(profile=profile).count()

            selection, created = UserTrophySelection.objects.get_or_create(profile=profile, earned_trophy_id=earned_trophy_id)
            if not created:
                selection.delete()
                action = 'removed'
            else:
                if current_count >= max_selections:
                    selection.delete()
                    return JsonResponse({'success': False, 'error': f'Max selections reached ({max_selections})'}, status=400)
                action = 'added'
            return JsonResponse({'success': True, 'action': action})
        except EarnedTrophy.DoesNotExist:
            return JsonResponse({'success': False, 'error': 'Invalid earned_trophy_id'}, status=400)
        except Profile.DoesNotExist:
            return JsonResponse({'success': False, 'error': 'No profile found'}, status=400)
        except Exception as e:
            logger.error(f"Selection toggle error: {e}")
            return JsonResponse({'success': False, 'error': 'Internal error'}, status=500)
