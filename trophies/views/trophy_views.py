import logging
from core.services.tracking import track_page_view
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Q, F
from django.db.models.functions import Lower
from django.http import JsonResponse, HttpResponseRedirect
from django.shortcuts import get_object_or_404
from django.urls import reverse_lazy, reverse
from django.views.generic import ListView, View
from urllib.parse import urlencode
from trophies.mixins import ProfileHotbarMixin
from ..models import Trophy, EarnedTrophy, Profile, UserTrophySelection
from ..forms import TrophySearchForm
from trophies.util_modules.constants import MODERN_PLATFORMS

logger = logging.getLogger("psn_api")


class TrophiesListView(ProfileHotbarMixin, ListView):
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
    paginate_by = 25

    def dispatch(self, request, *args, **kwargs):
        if not request.GET:
            default_params = {'platform': MODERN_PLATFORMS}
            if request.user.is_authenticated and request.user.default_region:
                default_params['region'] = ['global', request.user.default_region]

            if default_params:
                query_string = urlencode(default_params, doseq=True)
                url = reverse('trophies_list') + '?' + query_string
                return HttpResponseRedirect(url)
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        qs = super().get_queryset()
        form = TrophySearchForm(self.request.GET)
        order = [Lower('trophy_name')]

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
                qs = qs.filter(game__is_shovelware=False)


            if sort_val == 'earned':
                order = ['-earned_count', Lower('trophy_name')]
            elif sort_val == 'earned_inv':
                order = ['earned_count', Lower('trophy_name')]
            elif sort_val == 'rate':
                order = ['-earn_rate', Lower('trophy_name')]
            elif sort_val == 'rate_inv':
                order = ['earn_rate', Lower('trophy_name')]
            elif sort_val == 'psn_rate':
                order = ['-trophy_earn_rate', Lower('trophy_name')]
            elif sort_val == 'psn_rate_inv':
                order = ['trophy_earn_rate', Lower('trophy_name')]

        return qs.order_by(*order)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # Breadcrumb
        context['breadcrumb'] = [
            {'text': 'Home', 'url': reverse_lazy('home')},
            {'text': 'Trophies'},
        ]

        context['form'] = TrophySearchForm(self.request.GET)
        context['selected_platforms'] = self.request.GET.getlist('platform')
        context['selected_types'] = self.request.GET.getlist('type')
        context['selected_regions'] = self.request.GET.getlist('region')
        context['selected_psn_rarity'] = self.request.GET.getlist('psn_rarity')
        context['show_only_platinum'] = self.request.GET.get('show_only_platinum', '')
        context['filter_shovelware'] = self.request.GET.get('filter_shovelware', '')

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

    def get_queryset(self):
        form = TrophySearchForm(self.request.GET)
        profile = get_object_or_404(Profile, psn_username=self.kwargs['psn_username'].lower())
        if form.is_valid():
            query = form.cleaned_data.get('query')
            qs = EarnedTrophy.objects.filter(profile=profile, earned=True, trophy__trophy_type='platinum').select_related('trophy', 'trophy__game').order_by(F('earned_date_time').desc(nulls_last=True))

            if query:
                qs = qs.filter(trophy__game__title_name__icontains=query)
            return qs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        profile = Profile.objects.get(psn_username=self.kwargs['psn_username'].lower())
        selected_ids = list(profile.trophy_selections.values_list('earned_trophy_id', flat=True))

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
        max_selections = 10 if profile.user_is_premium else 3 if is_own_profile else 0
        context['max_selections'] = max_selections

        track_page_view('trophy_case', profile.id, self.request)
        return context

class ToggleSelectionView(LoginRequiredMixin, ProfileHotbarMixin, View):
    """
    AJAX endpoint to add or remove trophies from user's trophy case selection.

    Handles toggling trophy selections with validation for:
    - Maximum selection limits (3 for free users, 10 for premium)
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

            is_premium = profile.user_is_premium
            max_selections = 10 if is_premium else 3
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
