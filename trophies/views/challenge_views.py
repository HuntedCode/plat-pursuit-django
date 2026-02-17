"""
Challenge views.

Handles page-level views for the Challenge Hub and A-Z Platinum Challenges:
browse hub, my challenges, create, setup wizard, detail, and edit.
"""
import json
import logging

from core.services.tracking import track_page_view, track_site_event
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Q
from django.http import Http404
from django.shortcuts import redirect
from django.urls import reverse_lazy, reverse
from django.views.generic import ListView, DetailView, TemplateView

from trophies.mixins import ProfileHotbarMixin
from trophies.models import Challenge, ProfileGame
from trophies.themes import get_available_themes_for_grid
from trophies.services.challenge_service import create_az_challenge

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


class ChallengeHubView(ProfileHotbarMixin, ListView):
    """
    Public challenge hub with tabs: In Progress and Hall of Fame.

    Browse all public A-Z Challenges. Search by challenge name or PSN username.
    Sort by progress or recent. Paginated with Django's built-in pagination.
    """
    model = Challenge
    template_name = 'trophies/challenge_hub.html'
    context_object_name = 'challenges'
    paginate_by = 24

    def get_queryset(self):
        tab = self.request.GET.get('tab', 'active')
        qs = Challenge.objects.filter(
            is_deleted=False, challenge_type='az',
        ).select_related('profile').prefetch_related('az_slots__game')

        # Search
        query = self.request.GET.get('q')
        if query:
            qs = qs.filter(
                Q(name__icontains=query) |
                Q(profile__psn_username__icontains=query)
            )

        # Tab filtering
        if tab == 'hall_of_fame':
            qs = qs.filter(is_complete=True)
        else:
            qs = qs.filter(is_complete=False)

        # Sort
        sort = self.request.GET.get('sort', 'progress')
        if sort == 'recent':
            qs = qs.order_by('-created_at')
        elif tab == 'hall_of_fame':
            qs = qs.order_by('-completed_at')
        else:
            qs = qs.order_by('-completed_count', '-filled_count', '-created_at')

        return qs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['active_tab'] = self.request.GET.get('tab', 'active')
        context['search_query'] = self.request.GET.get('q', '')
        context['current_sort'] = self.request.GET.get('sort', 'progress')

        # Counts for tab badges
        base = Challenge.objects.filter(is_deleted=False, challenge_type='az')
        context['active_count'] = base.filter(is_complete=False).count()
        context['hall_of_fame_count'] = base.filter(is_complete=True).count()

        context['breadcrumb'] = [
            {'text': 'Home', 'url': reverse_lazy('home')},
            {'text': 'Challenges'},
        ]

        # Resolve cover images from prefetched slots (no extra queries)
        _attach_cover_images(context['challenges'])

        track_page_view('challenges_browse', 'hub', self.request)
        return context


class MyChallengesView(LoginRequiredMixin, ProfileHotbarMixin, TemplateView):
    """
    User's challenge hub: active challenge, create CTA, and history.

    Shows the user's active A-Z challenge (if any), a create button if none,
    and a history of completed and deleted challenges.
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

        # Active challenge (non-deleted, non-complete)
        context['active_challenge'] = Challenge.objects.filter(
            profile=profile, challenge_type='az', is_deleted=False, is_complete=False,
        ).prefetch_related('az_slots__game').first()

        # History: completed and soft-deleted
        context['history'] = list(
            Challenge.objects.filter(
                profile=profile, challenge_type='az',
            ).filter(
                Q(is_complete=True) | Q(is_deleted=True)
            ).prefetch_related('az_slots__game').order_by('-created_at')[:20]
        )

        context['can_create'] = context['active_challenge'] is None

        # Resolve cover images
        all_challenges = []
        if context['active_challenge']:
            all_challenges.append(context['active_challenge'])
        all_challenges.extend(context['history'])
        _attach_cover_images(all_challenges)

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
