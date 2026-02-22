"""
Challenge views.

Handles page-level views for the Challenge Hub, A-Z Platinum Challenges,
Platinum Calendar Challenges, and Genre Challenges: browse hub, my challenges,
create, setup wizard, detail, and edit.
"""
import json
import logging

from core.services.tracking import track_page_view, track_site_event
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Count, F, Q
from django.http import Http404
from django.shortcuts import redirect
from django.urls import reverse_lazy, reverse
from django.views.generic import DetailView, TemplateView

from trophies.mixins import ProfileHotbarMixin
from trophies.models import Challenge, ProfileGame
from trophies.themes import get_available_themes_for_grid
from trophies.services.challenge_service import (
    create_az_challenge, create_calendar_challenge, create_genre_challenge,
    get_calendar_month_data, get_calendar_stats,
    get_collected_subgenres, get_subgenre_status, resolve_subgenres,
)
from trophies.util_modules.constants import (
    GENRE_CHALLENGE_GENRES, GENRE_DISPLAY_NAMES,
    GENRE_CHALLENGE_SUBGENRES, SUBGENRE_DISPLAY_NAMES,
)
from trophies.services.holiday_service import get_holidays_for_js

logger = logging.getLogger("psn_api")


def _attach_cover_images(challenges):
    """Resolve cover_image_url for each challenge from prefetched az_slots."""
    for challenge in challenges:
        challenge.cover_image_url = ''
        if challenge.cover_letter:
            for slot in challenge.az_slots.all():
                if slot.letter == challenge.cover_letter and slot.game:
                    challenge.cover_image_url = (
                        slot.game.title_icon_url or slot.game.title_image or ''
                    )
                    break


class ChallengeHubView(ProfileHotbarMixin, TemplateView):
    """
    Public challenge hub with tab-based navigation between challenge types.

    Top-level tabs switch between challenge types (A-Z, Calendar).
    Each type has In Progress / Hall of Fame sub-tabs, search, and sort.
    Only the active type is queried in full; tab badge counts are lightweight.
    """
    template_name = 'trophies/challenge_hub.html'

    VALID_TYPES = ('az', 'calendar', 'genre')
    PAGINATE_BY = 12

    def _query_challenges(self, challenge_type, tab, search_query, sort, page=1):
        """Query challenges for a given type, tab, search, and sort with pagination."""
        qs = Challenge.objects.filter(
            is_deleted=False, challenge_type=challenge_type,
        ).select_related('profile')

        if challenge_type == 'az':
            qs = qs.prefetch_related('az_slots__game')
        elif challenge_type == 'genre':
            qs = qs.prefetch_related('genre_slots__concept')

        if search_query:
            qs = qs.filter(
                Q(name__icontains=search_query) |
                Q(profile__psn_username__icontains=search_query)
            )

        if tab == 'hall_of_fame':
            qs = qs.filter(is_complete=True)
        else:
            qs = qs.filter(is_complete=False)

        if sort == 'recent':
            qs = qs.order_by('-created_at')
        elif tab == 'hall_of_fame':
            qs = qs.order_by('-completed_at')
        else:
            qs = qs.order_by('-completed_count', '-filled_count', '-created_at')

        offset = (page - 1) * self.PAGINATE_BY
        return list(qs[offset:offset + self.PAGINATE_BY])

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        # Top-level type selection
        current_type = self.request.GET.get('type', 'az')
        if current_type not in self.VALID_TYPES:
            current_type = 'az'

        tab = self.request.GET.get('tab', 'active')
        if tab not in ('active', 'hall_of_fame'):
            tab = 'active'

        search_query = self.request.GET.get('q', '')
        sort = self.request.GET.get('sort', 'progress')

        try:
            page = max(1, int(self.request.GET.get('page', 1)))
        except (ValueError, TypeError):
            page = 1

        context.update({
            'current_type': current_type,
            'current_tab': tab,
            'search_query': search_query,
            'current_sort': sort,
        })

        is_ajax = self.request.headers.get('X-Requested-With') == 'XMLHttpRequest'

        # Skip counts on AJAX pagination requests (only cards needed)
        if not is_ajax:
            # Top-level tab badge counts (lightweight, both types, unfiltered)
            context['az_total_count'] = Challenge.objects.filter(
                is_deleted=False, challenge_type='az',
            ).count()
            context['cal_total_count'] = Challenge.objects.filter(
                is_deleted=False, challenge_type='calendar',
            ).count()
            context['genre_total_count'] = Challenge.objects.filter(
                is_deleted=False, challenge_type='genre',
            ).count()

            # Sub-tab counts for active type (filtered by search)
            base_qs = Challenge.objects.filter(
                is_deleted=False, challenge_type=current_type,
            )
            if search_query:
                base_qs = base_qs.filter(
                    Q(name__icontains=search_query) |
                    Q(profile__psn_username__icontains=search_query)
                )
            context['active_count'] = base_qs.filter(is_complete=False).count()
            context['hof_count'] = base_qs.filter(is_complete=True).count()

        # Challenge list for active type + sub-tab
        challenges = self._query_challenges(current_type, tab, search_query, sort, page=page)

        # Type-specific post-processing
        if current_type == 'az':
            _attach_cover_images(challenges)
        elif current_type == 'calendar':
            for c in challenges:
                c.card_month_data = _get_mini_calendar_data(c)
        elif current_type == 'genre':
            _attach_genre_cover_images(challenges)

        context['challenges'] = challenges

        if not is_ajax:
            context['breadcrumb'] = [
                {'text': 'Home', 'url': reverse_lazy('home')},
                {'text': 'Challenges'},
            ]
            track_page_view('challenges_browse', 'hub', self.request)

        return context

    def get_template_names(self):
        if self.request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return ['partials/challenge_hub_cards.html']
        return [self.template_name]


class MyChallengesView(LoginRequiredMixin, ProfileHotbarMixin, TemplateView):
    """
    User's challenge hub: active challenges, create CTAs, and history.

    Shows the user's active A-Z and Calendar challenges (if any),
    create buttons for missing types, and a history of past challenges.
    """
    template_name = 'trophies/my_challenges.html'
    login_url = reverse_lazy('account_login')

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            profile = getattr(request.user, 'profile', None)
            if not profile or not profile.is_linked:
                messages.info(request, "Link your PSN account to use challenges.")
                return redirect('link_psn')
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        profile = self.request.user.profile

        # A-Z: active challenge
        context['active_az'] = Challenge.objects.filter(
            profile=profile, challenge_type='az', is_deleted=False, is_complete=False,
        ).prefetch_related('az_slots__game').first()
        context['can_create_az'] = context['active_az'] is None

        # Calendar: active challenge
        context['active_calendar'] = Challenge.objects.filter(
            profile=profile, challenge_type='calendar', is_deleted=False, is_complete=False,
        ).first()
        context['can_create_calendar'] = context['active_calendar'] is None

        # Attach mini calendar data for the active calendar card
        if context['active_calendar']:
            context['active_calendar_months'] = _get_mini_calendar_data(
                context['active_calendar']
            )

        # Genre: active challenge
        context['active_genre'] = Challenge.objects.filter(
            profile=profile, challenge_type='genre', is_deleted=False, is_complete=False,
        ).prefetch_related('genre_slots__concept').first()
        context['can_create_genre'] = context['active_genre'] is None

        # History: all types, completed and soft-deleted
        context['history'] = list(
            Challenge.objects.filter(
                profile=profile,
            ).filter(
                Q(is_complete=True) | Q(is_deleted=True)
            ).select_related('profile').prefetch_related(
                'az_slots__game', 'genre_slots__concept'
            ).order_by('-created_at')[:20]
        )

        # Resolve cover images for A-Z challenges in the history
        az_history = [c for c in context['history'] if c.challenge_type == 'az']
        all_az = []
        if context['active_az']:
            all_az.append(context['active_az'])
        all_az.extend(az_history)
        _attach_cover_images(all_az)

        # Resolve cover images for genre challenges in the history
        genre_history = [c for c in context['history'] if c.challenge_type == 'genre']
        all_genre = []
        if context['active_genre']:
            all_genre.append(context['active_genre'])
        all_genre.extend(genre_history)
        _attach_genre_cover_images(all_genre)

        # Attach mini calendar data for calendar history items
        for challenge in context['history']:
            if challenge.challenge_type == 'calendar':
                challenge.card_month_data = _get_mini_calendar_data(challenge)

        context['breadcrumb'] = [
            {'text': 'Home', 'url': reverse_lazy('home')},
            {'text': 'My Challenges'},
        ]

        track_page_view('my_challenges', 'hub', self.request)
        return context


class AZChallengeCreateView(LoginRequiredMixin, ProfileHotbarMixin, TemplateView):
    """
    Create a new A-Z Challenge.

    GET: Show create form with name input.
    POST: Create challenge via service, redirect to setup wizard.
    """
    template_name = 'trophies/az_challenge_create.html'
    login_url = reverse_lazy('account_login')

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            profile = getattr(request.user, 'profile', None)
            if not profile or not profile.is_linked:
                messages.info(request, "Link your PSN account to create challenges.")
                return redirect('link_psn')

            # Check for existing active challenge
            active = Challenge.objects.filter(
                profile=profile, challenge_type='az',
                is_deleted=False, is_complete=False,
            ).first()
            if active:
                messages.info(request, "You already have an active A-Z Challenge.")
                return redirect('az_challenge_detail', challenge_id=active.id)

        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['breadcrumb'] = [
            {'text': 'Home', 'url': reverse_lazy('home')},
            {'text': 'My Challenges', 'url': reverse_lazy('my_challenges')},
            {'text': 'New A-Z Challenge'},
        ]
        return context

    def post(self, request):
        profile = request.user.profile
        name = (request.POST.get('name') or 'My A-Z Challenge').strip()[:75]
        if not name:
            name = 'My A-Z Challenge'

        try:
            challenge = create_az_challenge(profile, name=name)
            track_site_event('challenge_create', str(challenge.id), request)
            return redirect('az_challenge_setup', challenge_id=challenge.id)
        except ValueError as e:
            messages.error(request, str(e))
            return redirect('my_challenges')


class AZChallengeSetupView(LoginRequiredMixin, ProfileHotbarMixin, DetailView):
    """
    Guided setup wizard for A-Z Challenge.

    Owner-only. Shows a letter-by-letter wizard where the user searches for
    and assigns games to each letter slot.
    """
    model = Challenge
    template_name = 'trophies/az_challenge_setup.html'
    context_object_name = 'challenge'
    pk_url_kwarg = 'challenge_id'
    login_url = reverse_lazy('account_login')

    def get_queryset(self):
        return Challenge.objects.filter(
            is_deleted=False, challenge_type='az',
        ).select_related('profile')

    def get_object(self, queryset=None):
        challenge = super().get_object(queryset)
        profile = getattr(self.request.user, 'profile', None)
        if not profile or challenge.profile_id != profile.id:
            raise Http404("Challenge not found")
        return challenge

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        challenge = self.object
        slots = list(challenge.az_slots.select_related('game').all())

        # Serialize slots for JS initialization
        slots_data = []
        for slot in slots:
            slot_data = {
                'letter': slot.letter,
                'is_completed': slot.is_completed,
                'game': None,
            }
            if slot.game:
                slot_data['game'] = {
                    'id': slot.game.id,
                    'title_name': slot.game.title_name,
                    'title_image': slot.game.title_image or '',
                    'title_icon_url': slot.game.title_icon_url or '',
                    'title_platform': slot.game.title_platform or [],
                }
            slots_data.append(slot_data)

        context['slots'] = slots
        context['slots_json'] = json.dumps(slots_data)

        context['breadcrumb'] = [
            {'text': 'Home', 'url': reverse_lazy('home')},
            {'text': 'My Challenges', 'url': reverse_lazy('my_challenges')},
            {'text': challenge.name, 'url': reverse('az_challenge_detail', args=[challenge.id])},
            {'text': 'Setup'},
        ]

        track_page_view('az_challenge_setup', str(challenge.id), self.request)
        return context


class AZChallengeDetailView(ProfileHotbarMixin, DetailView):
    """
    Public progress view for an A-Z Challenge.

    Shows the 26-slot grid with game icons, platform badges, and completion status.
    Owner sees edit button. All active challenges are public.
    """
    model = Challenge
    template_name = 'trophies/az_challenge_detail.html'
    context_object_name = 'challenge'
    pk_url_kwarg = 'challenge_id'

    def get_queryset(self):
        return Challenge.objects.filter(
            challenge_type='az',
        ).select_related('profile')

    def get_object(self, queryset=None):
        challenge = super().get_object(queryset)
        profile = getattr(self.request.user, 'profile', None) if self.request.user.is_authenticated else None
        is_owner = profile and challenge.profile_id == profile.id

        # Deleted challenges visible only to owner
        if challenge.is_deleted and not is_owner:
            raise Http404("Challenge not found")

        return challenge

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        challenge = self.object
        profile = getattr(self.request.user, 'profile', None) if self.request.user.is_authenticated else None

        context['is_owner'] = profile and challenge.profile_id == profile.id
        slots = list(challenge.az_slots.select_related('game').all())

        # Batch-fetch the challenge owner's trophy progress for assigned games
        game_ids = [s.game_id for s in slots if s.game_id]
        progress_map = {}
        if game_ids:
            pg_qs = ProfileGame.objects.filter(
                profile_id=challenge.profile_id, game_id__in=game_ids,
            ).values(
                'game_id', 'progress', 'earned_trophies_count',
                'unearned_trophies_count',
            )
            for pg in pg_qs:
                total = pg['earned_trophies_count'] + pg['unearned_trophies_count']
                progress_map[pg['game_id']] = {
                    'percentage': pg['progress'],
                    'earned': pg['earned_trophies_count'],
                    'total': total,
                }

        # Attach progress to each slot for template access
        for slot in slots:
            slot.user_progress = progress_map.get(slot.game_id)
            # Ensure assigned games always have progress data (even if 0)
            if slot.game_id and not slot.user_progress:
                slot.user_progress = {'percentage': 0, 'earned': 0, 'total': None}

        context['slots'] = slots

        context['breadcrumb'] = [
            {'text': 'Home', 'url': reverse_lazy('home')},
            {'text': 'Challenges', 'url': reverse_lazy('challenges_browse')},
            {'text': challenge.name},
        ]

        # Provide theme grid data for share card color picker (owner only)
        if context['is_owner']:
            context['available_themes'] = get_available_themes_for_grid(include_game_art=False)

            # Serialize eligible spinner slots for "Pick My Next Game" picker
            spinner_data = []
            for slot in slots:
                if slot.game and not slot.is_completed:
                    progress = slot.user_progress or {'percentage': 0}
                    spinner_data.append({
                        'letter': slot.letter,
                        'game_name': slot.game.title_name,
                        'game_icon': slot.game.title_icon_url or slot.game.title_image or '',
                        'progress': progress.get('percentage', 0),
                    })
            context['spinner_slots_json'] = json.dumps(spinner_data)

        # Increment view count atomically
        Challenge.objects.filter(pk=challenge.pk).update(view_count=F('view_count') + 1)

        track_page_view('az_challenge', str(challenge.id), self.request)
        return context


class AZChallengeEditView(LoginRequiredMixin, ProfileHotbarMixin, DetailView):
    """
    Edit page for A-Z Challenge.

    Owner-only. Shows the 26-slot grid with swap/clear actions per slot.
    Completed slots are locked and cannot be changed.
    """
    model = Challenge
    template_name = 'trophies/az_challenge_edit.html'
    context_object_name = 'challenge'
    pk_url_kwarg = 'challenge_id'
    login_url = reverse_lazy('account_login')

    def get_queryset(self):
        return Challenge.objects.filter(
            is_deleted=False, challenge_type='az',
        ).select_related('profile')

    def get_object(self, queryset=None):
        challenge = super().get_object(queryset)
        profile = getattr(self.request.user, 'profile', None)
        if not profile or challenge.profile_id != profile.id:
            raise Http404("Challenge not found")
        return challenge

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        challenge = self.object
        slots = list(challenge.az_slots.select_related('game').all())

        # Serialize for JS
        slots_data = []
        for slot in slots:
            slot_data = {
                'letter': slot.letter,
                'is_completed': slot.is_completed,
                'game': None,
            }
            if slot.game:
                slot_data['game'] = {
                    'id': slot.game.id,
                    'title_name': slot.game.title_name,
                    'title_image': slot.game.title_image or '',
                    'title_icon_url': slot.game.title_icon_url or '',
                    'title_platform': slot.game.title_platform or [],
                }
            slots_data.append(slot_data)

        context['slots'] = slots
        context['slots_json'] = json.dumps(slots_data)

        context['breadcrumb'] = [
            {'text': 'Home', 'url': reverse_lazy('home')},
            {'text': 'My Challenges', 'url': reverse_lazy('my_challenges')},
            {'text': challenge.name, 'url': reverse('az_challenge_detail', args=[challenge.id])},
            {'text': 'Edit'},
        ]

        track_page_view('az_challenge_edit', str(challenge.id), self.request)
        return context


# ─── Platinum Calendar Challenge Views ────────────────────────────────────────


class CalendarChallengeCreateView(LoginRequiredMixin, ProfileHotbarMixin, TemplateView):
    """
    Create a new Platinum Calendar Challenge.

    GET: Show create form with name input and how-it-works explainer.
    POST: Create challenge with auto-backfill, redirect to detail page.
    """
    template_name = 'trophies/calendar_challenge_create.html'
    login_url = reverse_lazy('account_login')

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            profile = getattr(request.user, 'profile', None)
            if not profile or not profile.is_linked:
                messages.info(request, "Link your PSN account to create challenges.")
                return redirect('link_psn')

            # Check for existing active calendar challenge
            active = Challenge.objects.filter(
                profile=profile, challenge_type='calendar',
                is_deleted=False, is_complete=False,
            ).first()
            if active:
                messages.info(request, "You already have an active Platinum Calendar.")
                return redirect('calendar_challenge_detail', challenge_id=active.id)

        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['breadcrumb'] = [
            {'text': 'Home', 'url': reverse_lazy('home')},
            {'text': 'My Challenges', 'url': reverse_lazy('my_challenges')},
            {'text': 'New Platinum Calendar'},
        ]
        return context

    def post(self, request):
        profile = request.user.profile
        name = (request.POST.get('name') or 'My Platinum Calendar').strip()[:75]
        if not name:
            name = 'My Platinum Calendar'

        try:
            challenge = create_calendar_challenge(profile, name=name)
            track_site_event('challenge_create', str(challenge.id), request)
            return redirect('calendar_challenge_detail', challenge_id=challenge.id)
        except ValueError as e:
            messages.error(request, str(e))
            return redirect('my_challenges')


class CalendarChallengeDetailView(ProfileHotbarMixin, DetailView):
    """
    Public progress view for a Platinum Calendar Challenge.

    Shows 12-month calendar grid with filled/empty day states.
    Clicking a filled day opens a modal (JS) with all platinums for that day.
    """
    model = Challenge
    template_name = 'trophies/calendar_challenge_detail.html'
    context_object_name = 'challenge'
    pk_url_kwarg = 'challenge_id'

    def get_queryset(self):
        return Challenge.objects.filter(
            challenge_type='calendar',
        ).select_related('profile')

    def get_object(self, queryset=None):
        challenge = super().get_object(queryset)
        profile = getattr(self.request.user, 'profile', None) if self.request.user.is_authenticated else None
        is_owner = profile and challenge.profile_id == profile.id

        if challenge.is_deleted and not is_owner:
            raise Http404("Challenge not found")

        return challenge

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        challenge = self.object
        profile = getattr(self.request.user, 'profile', None) if self.request.user.is_authenticated else None

        context['is_owner'] = profile and challenge.profile_id == profile.id

        # Build month data for the 12-month calendar grid
        month_data = get_calendar_month_data(challenge)
        context['months'] = month_data

        # Stats
        context['stats'] = get_calendar_stats(challenge, month_data=month_data)

        context['breadcrumb'] = [
            {'text': 'Home', 'url': reverse_lazy('home')},
            {'text': 'Challenges', 'url': reverse_lazy('challenges_browse')},
            {'text': challenge.name},
        ]

        # Holiday data for JS-driven highlights (server-rendered, single source of truth)
        intl_holidays, us_holidays = get_holidays_for_js()
        context['intl_holidays_json'] = json.dumps(intl_holidays)
        context['us_holidays_json'] = json.dumps(us_holidays)

        # Provide theme grid data for share card color picker (owner only)
        if context['is_owner']:
            context['available_themes'] = get_available_themes_for_grid(include_game_art=False)

        # Increment view count atomically to avoid race conditions
        Challenge.objects.filter(pk=challenge.pk).update(view_count=F('view_count') + 1)

        track_page_view('calendar_challenge', str(challenge.id), self.request)
        return context


# ─── Genre Challenge Views ─────────────────────────────────────────────────


class GenreChallengeCreateView(LoginRequiredMixin, ProfileHotbarMixin, TemplateView):
    """
    Create a new Genre Challenge.

    GET: Show create form with name input and genre grid preview.
    POST: Create challenge via service, redirect to setup wizard.
    """
    template_name = 'trophies/genre_challenge_create.html'
    login_url = reverse_lazy('account_login')

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            profile = getattr(request.user, 'profile', None)
            if not profile or not profile.is_linked:
                messages.info(request, "Link your PSN account to create challenges.")
                return redirect('link_psn')

            active = Challenge.objects.filter(
                profile=profile, challenge_type='genre',
                is_deleted=False, is_complete=False,
            ).first()
            if active:
                messages.info(request, "You already have an active Genre Challenge.")
                return redirect('genre_challenge_detail', challenge_id=active.id)

        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # Genre preview grid for the create page
        context['genres'] = [
            {'key': g, 'display': GENRE_DISPLAY_NAMES.get(g, g)}
            for g in GENRE_CHALLENGE_GENRES
        ]
        context['genre_count'] = len(GENRE_CHALLENGE_GENRES)
        context['subgenre_count'] = len(GENRE_CHALLENGE_SUBGENRES)
        context['breadcrumb'] = [
            {'text': 'Home', 'url': reverse_lazy('home')},
            {'text': 'My Challenges', 'url': reverse_lazy('my_challenges')},
            {'text': 'New Genre Challenge'},
        ]
        return context

    def post(self, request):
        profile = request.user.profile
        name = (request.POST.get('name') or 'My Genre Challenge').strip()[:75]
        if not name:
            name = 'My Genre Challenge'

        try:
            challenge = create_genre_challenge(profile, name=name)
            track_site_event('challenge_create', str(challenge.id), request)
            return redirect('genre_challenge_setup', challenge_id=challenge.id)
        except ValueError as e:
            messages.error(request, str(e))
            return redirect('my_challenges')


class GenreChallengeSetupView(LoginRequiredMixin, ProfileHotbarMixin, DetailView):
    """
    Guided setup wizard for Genre Challenge.

    Owner-only. Shows a genre-by-genre wizard where the user searches for
    and assigns concepts to each genre slot.
    """
    model = Challenge
    template_name = 'trophies/genre_challenge_setup.html'
    context_object_name = 'challenge'
    pk_url_kwarg = 'challenge_id'
    login_url = reverse_lazy('account_login')

    def get_queryset(self):
        return Challenge.objects.filter(
            is_deleted=False, challenge_type='genre',
        ).select_related('profile')

    def get_object(self, queryset=None):
        challenge = super().get_object(queryset)
        profile = getattr(self.request.user, 'profile', None)
        if not profile or challenge.profile_id != profile.id:
            raise Http404("Challenge not found")
        return challenge

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        challenge = self.object
        slots = list(challenge.genre_slots.select_related('concept').all())

        # Serialize slots for JS initialization
        slots_data = []
        for slot in slots:
            slot_data = {
                'genre': slot.genre,
                'genre_display': slot.genre_display,
                'is_completed': slot.is_completed,
                'concept': None,
            }
            if slot.concept:
                slot_data['concept'] = {
                    'id': slot.concept.id,
                    'concept_id': slot.concept.concept_id,
                    'unified_title': slot.concept.unified_title,
                    'concept_icon_url': slot.concept.concept_icon_url or '',
                    'genres': slot.concept.genres or [],
                    'subgenres': slot.concept.subgenres or [],
                }
            slots_data.append(slot_data)

        context['slots'] = slots
        context['slots_json'] = json.dumps(slots_data)

        # Genre display map for JS
        context['genre_display_json'] = json.dumps(GENRE_DISPLAY_NAMES)

        # Subgenre data for the tracker (three-state: platted/assigned/uncollected)
        subgenre_status = get_subgenre_status(challenge)
        all_subgenres = [
            {
                'key': sg,
                'display': SUBGENRE_DISPLAY_NAMES.get(sg, sg),
                'status': subgenre_status.get(sg, 'uncollected'),
            }
            for sg in GENRE_CHALLENGE_SUBGENRES
        ]
        context['subgenres_json'] = json.dumps(all_subgenres)
        context['subgenre_count'] = challenge.subgenre_count
        context['subgenre_total'] = len(GENRE_CHALLENGE_SUBGENRES)

        context['breadcrumb'] = [
            {'text': 'Home', 'url': reverse_lazy('home')},
            {'text': 'My Challenges', 'url': reverse_lazy('my_challenges')},
            {'text': challenge.name, 'url': reverse('genre_challenge_detail', args=[challenge.id])},
            {'text': 'Setup'},
        ]

        track_page_view('genre_challenge_setup', str(challenge.id), self.request)
        return context


class GenreChallengeDetailView(ProfileHotbarMixin, DetailView):
    """
    Public progress view for a Genre Challenge.

    Shows the genre slot grid with concept icons, progress, and subgenre tracker.
    Owner sees edit button.
    """
    model = Challenge
    template_name = 'trophies/genre_challenge_detail.html'
    context_object_name = 'challenge'
    pk_url_kwarg = 'challenge_id'

    def get_queryset(self):
        return Challenge.objects.filter(
            challenge_type='genre',
        ).select_related('profile')

    def get_object(self, queryset=None):
        challenge = super().get_object(queryset)
        profile = getattr(self.request.user, 'profile', None) if self.request.user.is_authenticated else None
        is_owner = profile and challenge.profile_id == profile.id

        if challenge.is_deleted and not is_owner:
            raise Http404("Challenge not found")

        return challenge

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        challenge = self.object
        profile = getattr(self.request.user, 'profile', None) if self.request.user.is_authenticated else None

        context['is_owner'] = profile and challenge.profile_id == profile.id
        slots = list(challenge.genre_slots.select_related('concept').all())

        # Batch-fetch trophy progress for assigned concepts (via their games)
        concept_ids = [s.concept_id for s in slots if s.concept_id]
        progress_map = {}
        if concept_ids:
            from trophies.models import Game
            # Get all game IDs for these concepts
            concept_game_ids = {}
            for game_id, c_id in Game.objects.filter(
                concept_id__in=concept_ids
            ).values_list('id', 'concept_id'):
                concept_game_ids.setdefault(c_id, []).append(game_id)

            # Get best progress per concept from ProfileGame
            all_game_ids = []
            for gids in concept_game_ids.values():
                all_game_ids.extend(gids)

            if all_game_ids:
                pg_data = {}
                for pg in ProfileGame.objects.filter(
                    profile_id=challenge.profile_id, game_id__in=all_game_ids,
                ).values('game_id', 'progress', 'has_plat'):
                    pg_data[pg['game_id']] = pg

                # For each concept, find best progress game
                for c_id, gids in concept_game_ids.items():
                    best = None
                    for gid in gids:
                        pg = pg_data.get(gid)
                        if pg:
                            if best is None or pg['progress'] > best['progress']:
                                best = pg
                    if best:
                        progress_map[c_id] = {
                            'percentage': best['progress'],
                            'has_plat': best['has_plat'],
                        }

        for slot in slots:
            slot.user_progress = progress_map.get(slot.concept_id)
            if slot.concept_id and not slot.user_progress:
                slot.user_progress = {'percentage': 0, 'has_plat': False}
            # Resolve subgenres for display
            if slot.concept:
                raw_sgs = slot.concept.subgenres or []
                slot.resolved_subgenres = [
                    {'key': sg, 'display': SUBGENRE_DISPLAY_NAMES.get(sg, sg)}
                    for sg in sorted(resolve_subgenres(raw_sgs))
                ]
            else:
                slot.resolved_subgenres = []

        context['slots'] = slots

        # Bonus slots with progress + subgenres
        bonus_slots = list(challenge.bonus_slots.select_related('concept').all())
        bonus_concept_ids = [s.concept_id for s in bonus_slots if s.concept_id]
        if bonus_concept_ids:
            # Reuse progress_map data for bonus concepts too
            from trophies.models import Game as GameModel
            bonus_game_ids_map = {}
            for game_id, c_id in GameModel.objects.filter(
                concept_id__in=bonus_concept_ids
            ).values_list('id', 'concept_id'):
                bonus_game_ids_map.setdefault(c_id, []).append(game_id)

            all_bonus_game_ids = []
            for gids in bonus_game_ids_map.values():
                all_bonus_game_ids.extend(gids)

            bonus_pg_data = {}
            if all_bonus_game_ids:
                for pg in ProfileGame.objects.filter(
                    profile_id=challenge.profile_id, game_id__in=all_bonus_game_ids,
                ).values('game_id', 'progress', 'has_plat'):
                    bonus_pg_data[pg['game_id']] = pg

            for slot in bonus_slots:
                if slot.concept_id:
                    gids = bonus_game_ids_map.get(slot.concept_id, [])
                    best = None
                    for gid in gids:
                        pg = bonus_pg_data.get(gid)
                        if pg and (best is None or pg['progress'] > best['progress']):
                            best = pg
                    slot.user_progress = best or {'percentage': 0, 'has_plat': False}
                else:
                    slot.user_progress = None

                if slot.concept:
                    raw_sgs = slot.concept.subgenres or []
                    slot.resolved_subgenres = [
                        {'key': sg, 'display': SUBGENRE_DISPLAY_NAMES.get(sg, sg)}
                        for sg in sorted(resolve_subgenres(raw_sgs))
                    ]
                else:
                    slot.resolved_subgenres = []

        context['bonus_slots'] = bonus_slots
        context['bonus_count'] = len(bonus_slots)

        # Subgenre tracker (three-state: platted/assigned/uncollected)
        subgenre_status = get_subgenre_status(challenge)
        all_subgenres = [
            {
                'key': sg,
                'display': SUBGENRE_DISPLAY_NAMES.get(sg, sg),
                'status': subgenre_status.get(sg, 'uncollected'),
            }
            for sg in GENRE_CHALLENGE_SUBGENRES
        ]
        context['all_subgenres'] = all_subgenres
        context['subgenre_count'] = challenge.subgenre_count
        context['subgenre_total'] = len(GENRE_CHALLENGE_SUBGENRES)

        context['breadcrumb'] = [
            {'text': 'Home', 'url': reverse_lazy('home')},
            {'text': 'Challenges', 'url': reverse_lazy('challenges_browse')},
            {'text': challenge.name},
        ]

        # Provide theme grid data and spinner slots (owner only)
        if context['is_owner']:
            context['available_themes'] = get_available_themes_for_grid(include_game_art=False)

            # Serialize eligible spinner slots for "Pick My Next Game" picker
            spinner_data = []
            for slot in slots:
                if slot.concept and not slot.is_completed:
                    progress = slot.user_progress or {'percentage': 0}
                    spinner_data.append({
                        'genre': slot.genre,
                        'genre_display': slot.genre_display,
                        'game_name': slot.concept.unified_title,
                        'game_icon': slot.concept.concept_icon_url or '',
                        'progress': progress.get('percentage', 0),
                    })
            context['spinner_slots_json'] = json.dumps(spinner_data)

        # Increment view count atomically
        Challenge.objects.filter(pk=challenge.pk).update(view_count=F('view_count') + 1)

        track_page_view('genre_challenge', str(challenge.id), self.request)
        return context


class GenreChallengeEditView(LoginRequiredMixin, ProfileHotbarMixin, DetailView):
    """
    Edit page for Genre Challenge.

    Owner-only. Shows the genre slot grid with swap/clear actions per slot.
    Completed slots are locked and cannot be changed.
    """
    model = Challenge
    template_name = 'trophies/genre_challenge_edit.html'
    context_object_name = 'challenge'
    pk_url_kwarg = 'challenge_id'
    login_url = reverse_lazy('account_login')

    def get_queryset(self):
        return Challenge.objects.filter(
            is_deleted=False, challenge_type='genre',
        ).select_related('profile')

    def get_object(self, queryset=None):
        challenge = super().get_object(queryset)
        profile = getattr(self.request.user, 'profile', None)
        if not profile or challenge.profile_id != profile.id:
            raise Http404("Challenge not found")
        return challenge

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        challenge = self.object
        slots = list(challenge.genre_slots.select_related('concept').all())

        # Attach resolved subgenres to slots for template rendering
        for slot in slots:
            if slot.concept:
                raw_sgs = slot.concept.subgenres or []
                slot.resolved_subgenres = [
                    {'key': sg, 'display': SUBGENRE_DISPLAY_NAMES.get(sg, sg)}
                    for sg in sorted(resolve_subgenres(raw_sgs))
                ]
            else:
                slot.resolved_subgenres = []

        # Serialize for JS (includes resolved subgenres)
        slots_data = []
        for slot in slots:
            slot_data = {
                'genre': slot.genre,
                'genre_display': slot.genre_display,
                'is_completed': slot.is_completed,
                'concept': None,
            }
            if slot.concept:
                slot_data['concept'] = {
                    'id': slot.concept.id,
                    'concept_id': slot.concept.concept_id,
                    'unified_title': slot.concept.unified_title,
                    'concept_icon_url': slot.concept.concept_icon_url or '',
                    'genres': slot.concept.genres or [],
                    'subgenres': slot.concept.subgenres or [],
                    'resolved_subgenres': slot.resolved_subgenres,
                }
            slots_data.append(slot_data)

        context['slots'] = slots
        context['slots_json'] = json.dumps(slots_data)

        # Bonus slots for JS
        bonus_slots = list(challenge.bonus_slots.select_related('concept').all())

        # Attach resolved subgenres to bonus slots
        for bs in bonus_slots:
            if bs.concept:
                raw_sgs = bs.concept.subgenres or []
                bs.resolved_subgenres = [
                    {'key': sg, 'display': SUBGENRE_DISPLAY_NAMES.get(sg, sg)}
                    for sg in sorted(resolve_subgenres(raw_sgs))
                ]
            else:
                bs.resolved_subgenres = []

        bonus_data = []
        for bs in bonus_slots:
            bs_data = {
                'id': bs.id,
                'is_completed': bs.is_completed,
                'concept': None,
            }
            if bs.concept:
                bs_data['concept'] = {
                    'id': bs.concept.id,
                    'concept_id': bs.concept.concept_id,
                    'unified_title': bs.concept.unified_title,
                    'concept_icon_url': bs.concept.concept_icon_url or '',
                    'genres': bs.concept.genres or [],
                    'subgenres': bs.concept.subgenres or [],
                    'resolved_subgenres': bs.resolved_subgenres,
                }
            bonus_data.append(bs_data)
        context['bonus_slots'] = bonus_slots
        context['bonus_slots_json'] = json.dumps(bonus_data)

        # Genre display map for JS
        context['genre_display_json'] = json.dumps(GENRE_DISPLAY_NAMES)

        # Subgenre data for the tracker (three-state: platted/assigned/uncollected)
        subgenre_status = get_subgenre_status(challenge)
        all_subgenres = [
            {
                'key': sg,
                'display': SUBGENRE_DISPLAY_NAMES.get(sg, sg),
                'status': subgenre_status.get(sg, 'uncollected'),
            }
            for sg in GENRE_CHALLENGE_SUBGENRES
        ]
        context['subgenres_json'] = json.dumps(all_subgenres)
        context['subgenre_count'] = challenge.subgenre_count
        context['subgenre_total'] = len(GENRE_CHALLENGE_SUBGENRES)

        context['breadcrumb'] = [
            {'text': 'Home', 'url': reverse_lazy('home')},
            {'text': 'My Challenges', 'url': reverse_lazy('my_challenges')},
            {'text': challenge.name, 'url': reverse('genre_challenge_detail', args=[challenge.id])},
            {'text': 'Edit'},
        ]

        track_page_view('genre_challenge_edit', str(challenge.id), self.request)
        return context


# ─── Helpers ──────────────────────────────────────────────────────────────────


def _attach_genre_cover_images(challenges):
    """Resolve cover_image_url for genre challenges from prefetched genre_slots."""
    for challenge in challenges:
        if hasattr(challenge, 'cover_image_url'):
            continue
        challenge.cover_image_url = ''
        if challenge.cover_genre:
            for slot in challenge.genre_slots.all():
                if slot.genre == challenge.cover_genre and slot.concept:
                    challenge.cover_image_url = slot.concept.concept_icon_url or ''
                    break


def _get_mini_calendar_data(challenge):
    """
    Build lightweight per-month fill counts for calendar challenge cards.
    Returns a list of 12 dicts: {month_num, filled_count, total_days}.
    Uses a single aggregate query rather than fetching all 365 day objects.
    """
    from trophies.models import CALENDAR_DAYS_PER_MONTH

    filled_by_month = dict(
        challenge.calendar_days.filter(is_filled=True)
        .values_list('month')
        .annotate(count=Count('id'))
        .values_list('month', 'count')
    )

    month_abbrs = [
        '', 'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
        'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec',
    ]

    months = []
    for month_num in range(1, 13):
        months.append({
            'month_num': month_num,
            'month_abbr': month_abbrs[month_num],
            'filled_count': filled_by_month.get(month_num, 0),
            'total_days': CALENDAR_DAYS_PER_MONTH[month_num],
        })
    return months
