import logging

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import F
from django.http import Http404
from django.shortcuts import redirect, get_object_or_404
from django.urls import reverse_lazy, reverse
from django.views.generic import View, DetailView, TemplateView

from trophies.mixins import ProfileHotbarMixin
from ..models import Game, Profile, EarnedTrophy, ProfileGame, Concept, Checklist
from trophies.services.checklist_service import ChecklistService

logger = logging.getLogger("psn_api")


class ChecklistDetailView(ProfileHotbarMixin, DetailView):
    """
    Display checklist detail with sections, items, and progress tracking.

    Shows the full checklist structure with checkboxes for tracking progress.
    Anyone can view guides. Authenticated users with linked PSN accounts can interact
    with checkboxes. Premium users and checklist authors can save progress.
    """
    model = Checklist
    template_name = 'trophies/checklist_detail.html'
    context_object_name = 'checklist'
    pk_url_kwarg = 'checklist_id'

    def dispatch(self, request, *args, **kwargs):
        """Allow anyone to view guides (no authentication required)."""
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        """Return checklist with optimized prefetches."""
        return Checklist.objects.active().with_author_data().with_sections()

    def get_object(self, queryset=None):
        """Get checklist and validate access."""
        checklist = super().get_object(queryset)

        # Check if checklist is accessible
        user = self.request.user
        profile = user.profile if user.is_authenticated and hasattr(user, 'profile') else None

        # Draft checklists are only viewable by their author
        if checklist.status == 'draft':
            if not profile or checklist.profile != profile:
                raise Http404("Checklist not found")

        return checklist

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        checklist = self.object
        user = self.request.user
        profile = user.profile if user.is_authenticated and hasattr(user, 'profile') else None

        # Get user's completed items
        completed_items = []
        user_progress = None
        if profile:
            user_progress = ChecklistService.get_user_progress(checklist, profile)
            if user_progress:
                completed_items = user_progress.completed_items

        context['completed_items'] = completed_items
        context['user_progress'] = user_progress

        # Get earned trophy item IDs for this user (auto-check earned trophies)
        earned_trophy_item_ids = set()
        if profile and hasattr(profile, 'is_linked') and profile.is_linked:
            from trophies.models import EarnedTrophy, ChecklistItem

            # Get all trophy items in this checklist
            trophy_items = ChecklistItem.objects.filter(
                section__checklist=checklist,
                item_type='trophy',
                trophy_id__isnull=False
            ).values_list('id', 'trophy_id')

            if trophy_items:
                item_to_trophy = {item_id: trophy_id for item_id, trophy_id in trophy_items}
                trophy_ids = list(item_to_trophy.values())

                # Query which trophies the user has earned
                earned_trophy_pks = set(
                    EarnedTrophy.objects.filter(
                        profile=profile,
                        trophy_id__in=trophy_ids,
                        earned=True
                    ).values_list('trophy_id', flat=True)
                )

                # Convert back to ChecklistItem IDs
                earned_trophy_item_ids = {
                    item_id for item_id, trophy_pk in item_to_trophy.items()
                    if trophy_pk in earned_trophy_pks
                }

        context['earned_trophy_item_ids'] = earned_trophy_item_ids

        # Calculate per-section completion counts and attach to section objects
        # Include both manually completed items AND earned trophies in the count
        completed_item_ids = set(completed_items) | earned_trophy_item_ids
        sections = checklist.sections.all()
        total_items_count = 0
        total_completed_count = 0
        for section in sections:
            section_item_ids = list(section.items.filter(item_type__in=['item', 'trophy']).values_list('id', flat=True))
            completed_count = sum(1 for item_id in section_item_ids if item_id in completed_item_ids)
            # Add completion data as attributes to the section object
            section.completed_count = completed_count
            section.total_count = len(section_item_ids)
            # Track totals for overall progress
            total_items_count += len(section_item_ids)
            total_completed_count += completed_count

        # Calculate adjusted progress that includes earned trophies
        adjusted_progress_percentage = (total_completed_count / total_items_count * 100) if total_items_count > 0 else 0
        context['adjusted_items_completed'] = total_completed_count
        context['adjusted_total_items'] = total_items_count
        context['adjusted_progress_percentage'] = adjusted_progress_percentage

        # Check permissions
        context['can_edit'] = profile and checklist.profile == profile and not checklist.is_deleted
        # can_save_progress returns (bool, str reason), we just need the bool
        can_save, _ = ChecklistService.can_save_progress(checklist, profile) if profile else (False, None)
        context['can_save_progress'] = can_save
        context['is_author'] = profile and checklist.profile == profile

        # Get game info from concept
        context['game'] = checklist.concept.games.first() if checklist.concept else None

        # Check if author has platinum for this game/concept
        context['author_has_platinum'] = False
        if checklist.concept and checklist.profile:
            from trophies.models import ProfileGame
            pg = ProfileGame.objects.filter(
                profile=checklist.profile,
                game__concept=checklist.concept
            ).order_by('-progress').first()
            if pg:
                context['author_has_platinum'] = pg.has_plat

        # Breadcrumbs
        breadcrumb = [
            {'text': 'Home', 'url': reverse_lazy('home')},
            {'text': 'Games', 'url': reverse_lazy('games_list')},
        ]
        if context['game']:
            breadcrumb.append({
                'text': context['game'].title_name,
                'url': reverse_lazy('game_detail', kwargs={'np_communication_id': context['game'].np_communication_id})
            })
        breadcrumb.append({'text': checklist.title})
        context['breadcrumb'] = breadcrumb

        # Set background image from concept if available
        if checklist.concept and checklist.concept.bg_url:
            context['image_urls'] = {'bg_url': checklist.concept.bg_url}

        # Comment section context
        context['guidelines_agreed'] = profile.guidelines_agreed if profile else False

        # Get comment count for this checklist
        from trophies.models import Comment
        comment_count = Comment.objects.filter(
            concept=checklist.concept,
            checklist_id=checklist.id,
            is_deleted=False
        ).count()
        context['comment_count'] = comment_count

        return context


class ChecklistCreateView(LoginRequiredMixin, ProfileHotbarMixin, View):
    """
    Create a new checklist for a concept.

    Redirects to the edit page after creating a draft checklist.
    """
    login_url = reverse_lazy('account_login')

    def get(self, request, concept_id, np_communication_id):
        """Create a new draft checklist and redirect to edit."""
        from trophies.models import Game

        concept = get_object_or_404(Concept, id=concept_id)
        game = get_object_or_404(Game, np_communication_id=np_communication_id, concept=concept)
        profile = request.user.profile if hasattr(request.user, 'profile') else None

        if not profile:
            messages.error(request, "You need to link your PSN account first.")
            return redirect('link_psn')

        # Check if user can create checklists
        can_create, error = ChecklistService.can_create_checklist(profile)
        if not can_create:
            # If the error is about guidelines, redirect with hash to trigger modal
            if error == "You must agree to the community guidelines.":
                return redirect(f"{reverse('game_detail', kwargs={'np_communication_id': game.np_communication_id})}#show-guidelines")
            else:
                messages.error(request, error)
                return redirect('game_detail', np_communication_id=game.np_communication_id)

        # Create the checklist with the selected game
        checklist, error = ChecklistService.create_checklist(
            profile=profile,
            concept=concept,
            title=f"New Guide for {concept.unified_title}"
        )

        if error:
            messages.error(request, error)
            return redirect('game_detail', np_communication_id=game.np_communication_id)

        # Set the selected game (default to the game they came from)
        checklist.selected_game = game
        checklist.save(update_fields=['selected_game', 'updated_at'])

        messages.success(request, "Guide created! Start adding sections and items.")
        return redirect('checklist_edit', checklist_id=checklist.id)


class ChecklistEditView(LoginRequiredMixin, ProfileHotbarMixin, DetailView):
    """
    Edit a checklist (title, description, sections, items).

    Only the checklist author can access this view.
    Requires a linked PSN account.
    """
    model = Checklist
    template_name = 'trophies/checklist_edit.html'
    context_object_name = 'checklist'
    pk_url_kwarg = 'checklist_id'
    login_url = reverse_lazy('account_login')

    def dispatch(self, request, *args, **kwargs):
        """Require linked PSN account."""
        if request.user.is_authenticated:
            profile = getattr(request.user, 'profile', None)
            if not profile or not profile.is_linked:
                messages.info(request, "Link your PSN account to create and edit checklists.")
                return redirect('link_psn')
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        """Return checklist with optimized prefetches."""
        return Checklist.objects.active().with_author_data().with_sections()

    def get_object(self, queryset=None):
        """Get checklist and verify ownership."""
        checklist = super().get_object(queryset)
        user = self.request.user
        profile = user.profile if hasattr(user, 'profile') else None

        # Only author can edit
        if not profile or checklist.profile != profile:
            raise Http404("Checklist not found")

        return checklist

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        checklist = self.object

        # Get game info from concept
        context['game'] = checklist.concept.games.first() if checklist.concept else None

        # Get all games for concept (for trophy selection)
        context['concept_games'] = []
        if checklist.concept:
            context['concept_games'] = checklist.concept.games.all().order_by('title_name')

        # Breadcrumbs
        breadcrumb = [
            {'text': 'Home', 'url': reverse_lazy('home')},
            {'text': 'Games', 'url': reverse_lazy('games_list')},
        ]
        if context['game']:
            breadcrumb.append({
                'text': context['game'].title_name,
                'url': reverse_lazy('game_detail', kwargs={'np_communication_id': context['game'].np_communication_id})
            })
        breadcrumb.append({
            'text': checklist.title,
            'url': reverse_lazy('checklist_detail', kwargs={'checklist_id': checklist.id})
        })
        breadcrumb.append({'text': 'Edit'})
        context['breadcrumb'] = breadcrumb

        # Set background image from concept if available
        if checklist.concept and checklist.concept.bg_url:
            context['image_urls'] = {'bg_url': checklist.concept.bg_url}

        return context


class MyChecklistsView(LoginRequiredMixin, ProfileHotbarMixin, TemplateView):
    """
    Display user's checklists: drafts, published, and in-progress.

    Shows three tabs:
    1. My Drafts - Checklists user is working on
    2. My Published - Checklists user has published
    3. In Progress - Other users' checklists the user is tracking

    Requires a linked PSN account.
    """
    template_name = 'trophies/my_checklists.html'
    login_url = reverse_lazy('account_login')

    def dispatch(self, request, *args, **kwargs):
        """Require linked PSN account."""
        if request.user.is_authenticated:
            profile = getattr(request.user, 'profile', None)
            if not profile or not profile.is_linked:
                messages.info(request, "Link your PSN account to use checklists.")
                return redirect('link_psn')
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        profile = user.profile if hasattr(user, 'profile') else None

        if not profile:
            context['drafts'] = []
            context['published'] = []
            context['in_progress'] = []
            return context

        # Get user's drafts
        context['drafts'] = ChecklistService.get_user_drafts(profile)

        # Get user's published checklists
        context['published'] = ChecklistService.get_user_published(profile)

        # Get checklists user is tracking (in progress)
        context['in_progress'] = ChecklistService.get_user_checklists_in_progress(profile)

        # Breadcrumbs
        context['breadcrumb'] = [
            {'text': 'Home', 'url': reverse_lazy('home')},
            {'text': 'My Checklists'},
        ]

        # Active tab
        context['active_tab'] = self.request.GET.get('tab', 'drafts')

        return context


class MyShareablesView(LoginRequiredMixin, ProfileHotbarMixin, TemplateView):
    """
    My Shareables hub - centralized page for all shareable content.

    Allows users to generate share images for any platinum trophy they've earned,
    not just those that triggered a notification. Designed for extensibility to
    support future shareable types (trophy cabinet, calendar, etc.).

    Shows platinum trophies grouped by year with "Share" buttons.
    Requires a linked PSN account.
    """
    template_name = 'shareables/my_shareables.html'
    login_url = reverse_lazy('account_login')

    def dispatch(self, request, *args, **kwargs):
        """Require linked PSN account."""
        if request.user.is_authenticated:
            profile = getattr(request.user, 'profile', None)
            if not profile or not profile.is_linked:
                messages.info(request, "Link your PSN account to create shareables.")
                return redirect('link_psn')
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        profile = user.profile if hasattr(user, 'profile') else None

        if not profile:
            context['platinums_by_year'] = {}
            context['total_platinums'] = 0
            return context

        # Get user's platinum trophies (including shovelware - filtered client-side)
        earned_platinums = EarnedTrophy.objects.filter(
            profile=profile,
            earned=True,
            trophy__trophy_type='platinum',
        ).select_related(
            'trophy__game',
            'trophy__game__concept'
        ).order_by('-earned_date_time')

        # Calculate platinum number for each trophy (for milestone display)
        # We need to count platinums earned up to each one's date
        platinum_list = list(earned_platinums)
        total_count = len(platinum_list)

        # Since list is ordered by -earned_date_time (newest first),
        # the newest plat is #total_count, oldest is #1
        for idx, et in enumerate(platinum_list):
            # Platinum number = total - index (since newest is first)
            et.platinum_number = total_count - idx
            et.is_milestone = et.platinum_number % 10 == 0 and et.platinum_number > 0
            et.is_shovelware = et.trophy.game.is_shovelware

        # Count shovelware for filter toggle
        shovelware_count = sum(1 for et in platinum_list if et.trophy.game.is_shovelware)

        # Group by year for organization
        platinums_by_year = {}
        for et in platinum_list:
            year = et.earned_date_time.year if et.earned_date_time else 'Unknown'
            if year not in platinums_by_year:
                platinums_by_year[year] = []
            platinums_by_year[year].append(et)

        # Sort years descending (most recent first), with 'Unknown' at the end
        sorted_years = sorted(
            [y for y in platinums_by_year.keys() if y != 'Unknown'],
            reverse=True
        )
        if 'Unknown' in platinums_by_year:
            sorted_years.append('Unknown')

        context['platinums_by_year'] = {year: platinums_by_year[year] for year in sorted_years}
        context['total_platinums'] = earned_platinums.count()
        context['shovelware_count'] = shovelware_count

        # Active tab (for future extensibility)
        context['active_tab'] = self.request.GET.get('tab', 'platinum_images')

        # Add available themes for color grid modal
        # Include game art themes since we have game context in share cards
        from trophies.themes import get_available_themes_for_grid
        context['available_themes'] = get_available_themes_for_grid(include_game_art=True)

        # Breadcrumbs
        context['breadcrumb'] = [
            {'text': 'Home', 'url': reverse_lazy('home')},
            {'text': 'My Shareables'},
        ]

        return context
