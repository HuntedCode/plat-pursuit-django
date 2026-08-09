"""
Shareables views.

Houses the My Shareables surface.

As of 2026-08 this serves **plat cards only** -- the one place a hunter gets a share card for a
completion. Platinum Grid and Profile Card are retired (their views are parked, their URLs bounce to
the landing); Monthly Recap keeps its own home at `/recap/`. See docs/features/share-images.md.

- Plat Cards (`/shareables/platinums/`) -- browse your completions and generate a card for any of
  them. Eligibility is the DEFAULT trophy group at 100%, so 100%-with-no-platinum games qualify too.

History: this page began as a single browse-all-platinums interface, was split into a landing +
four sub-pages by the Phase 10b restructure, and has now been narrowed back to its one job.
"""
import logging

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models.functions import Lower
from django.shortcuts import redirect
from django.urls import reverse_lazy
from django.utils import timezone
from django.views.generic import ListView, TemplateView

from core.services import completion_card_service as cards
from core.services.tracking import track_page_view
from trophies.mixins import HtmxListMixin
from trophies.themes import get_available_themes_for_grid, get_plat_card_themes

#: Hidden by default on this page -- see PlatCardsView.get_queryset.
SHOVELWARE_STATUSES = ('auto_flagged', 'manually_flagged')

logger = logging.getLogger(__name__)


class _RequireLinkedProfileMixin:
    """Mixin: redirect to the PSN linking flow when the viewer has no linked profile.

    Shared across all shareables sub-pages so each one enforces the same
    "you need a profile to make share images" gating without duplicating
    the dispatch override.
    """

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            profile = getattr(request.user, 'profile', None)
            if not profile or not profile.is_linked:
                messages.info(request, "Link your PSN account to create shareables.")
                return redirect('link_psn')
        return super().dispatch(request, *args, **kwargs)


class PlatCardsView(LoginRequiredMixin, _RequireLinkedProfileMixin, HtmxListMixin, ListView):
    """The Plat Cards page at `/shareables/` -- browse your completions, make a card from any of them.

    Rebuilt from scratch 2026-08. What it replaced and why:

    - A 4-card WAYFINDER landing sat in front of this, distributing to Platinum Cards / Platinum Grid /
      Profile Card / Recap. Three of those are gone, so the wayfinder was a menu with one real item.
    - The browse itself listed platinum `EarnedTrophy` rows, which is why a 100%-with-no-platinum game
      could never produce a card. Eligibility is now the DEFAULT trophy group at 100%
      (`completion_card_service.eligible_completions`), a superset that yields both card variants.
    - It rendered EVERY platinum in one response with client-side search. A hunter with 800 platinums
      shipped 800 rows on load; adding non-platinum completions only grows that. Now paginated with
      server-side filter/sort and infinite scroll, the same shape as Browse Games.

    The variant toggle is a segmented FILTER (radios styled as .pp-switch), not a view island: switching
    preserves the active search and sort, and native `checked` keeps browser Back correct with no JS.
    """
    template_name = 'shareables/plat_cards.html'
    partial_template_name = 'shareables/partials/plat_card_results.html'
    context_object_name = 'completions'
    paginate_by = 24
    login_url = reverse_lazy('account_login')

    VARIANT_CHOICES = [
        ('all', 'All'),
        (cards.PLATINUM, 'Platinum'),
        (cards.FULL, '100%'),
    ]
    SORT_CHOICES = [
        ('recent', 'Most recent'),
        ('oldest', 'Oldest first'),
        ('name', 'Game A-Z'),
        ('name_desc', 'Game Z-A'),
    ]
    _SORTS = {
        # `-last_trophy_at` is the completion moment. `trophy_group_id` is the total-order tiebreak:
        # without it, rows sharing a timestamp can reorder between pages and infinite scroll skips or
        # repeats a card (the same reason the leaderboard indexes carry a unique final key).
        'recent': ('-last_trophy_at', '-trophy_group_id'),
        'oldest': ('last_trophy_at', 'trophy_group_id'),
        'name': (Lower('trophy_group__game__title_name'), 'trophy_group_id'),
        'name_desc': (Lower('trophy_group__game__title_name').desc(), 'trophy_group_id'),
    }

    def _variant(self):
        raw = self.request.GET.get('variant', 'all')
        return raw if raw in dict(self.VARIANT_CHOICES) else 'all'

    def _sort(self):
        raw = self.request.GET.get('sort', 'recent')
        return raw if raw in self._SORTS else 'recent'

    def get_queryset(self):
        profile = self.request.user.profile
        qs = cards.eligible_completions(profile)

        variant = self._variant()
        if variant != 'all':
            qs = cards.variant_filter(qs, variant)

        query = (self.request.GET.get('query') or '').strip()
        if query:
            qs = qs.filter(trophy_group__game__title_name__icontains=query)

        # Shovelware is hidden by default here (unlike Browse Games, where it's catalogue): these are
        # the hunter's OWN completions, and the asset-flip platinums are the ones they least want to
        # scroll past looking for the game they actually care about.
        if not self.request.GET.get('show_shovelware'):
            qs = qs.exclude(trophy_group__game__shovelware_status__in=SHOVELWARE_STATUSES)

        return qs.order_by(*self._SORTS[self._sort()])

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        profile = self.request.user.profile

        # Header stats read the unfiltered ladders, so they describe the hunter's career rather than
        # whatever the toolbar currently shows.
        total_plats, total_full = cards.hunter_totals(profile)
        themes = get_plat_card_themes()
        year_start = timezone.now().replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
        context.update({
            'total_platinums': total_plats,
            'total_completions': total_full,
            'this_year': cards.eligible_completions(profile).filter(last_trophy_at__gte=year_start).count(),
            'variant_choices': self.VARIANT_CHOICES,
            'current_variant': self._variant(),
            'sort_choices': self.SORT_CHOICES,
            'current_sort': self._sort(),
            'show_shovelware': bool(self.request.GET.get('show_shovelware')),
            'card_themes': themes,
            # The same six, shaped for the preview's client-side restyle. The download applies the
            # ground server-side, so this only has to make the PREVIEW agree with it.
            'card_theme_js': {
                key: {
                    'background': t['background_css'],
                    'is_game_art': t.get('is_game_art', False),
                    'source': t.get('game_image_source', ''),
                }
                for key, t in themes
            },
            'breadcrumb': [
                {'text': 'Home', 'url': reverse_lazy('home')},
                {'text': 'Plat Cards'},
            ],
        })
        track_page_view('my_shareables', 'user', self.request)
        return context


class MyProfileCardView(LoginRequiredMixin, _RequireLinkedProfileMixin, TemplateView):
    """Profile card builder page.

    RETIRED (2026-08): PARKED, not routed. My Shareables now serves plat cards only -- see
    docs/features/share-images.md. /shareables/profile-card/ bounces to the Plat Cards page.

    Kept for its DATA CONTRACT, not as a working page: its template hand-invokes a dashboard module
    init that no longer exists in the registry, and `static/js/profile-card-share.js` requires
    `ShareImageManager` from `share-image.js`, which was deleted with the page it served. Reviving
    this means rebuilding the front end against the new card design -- which was always the plan --
    so what's useful here is the ProfileCardSettings wiring below, not the page.

    Was at `/dashboard/shareables/profile-card/`.

    Dedicated page for generating share images of the user's trophy
    profile (landscape, portrait, and tab variants). Loads card HTML
    via the existing `/api/v1/profile-card/html/` endpoint and the
    existing `static/js/profile-card-share.js` controller — this
    page is the long-form home for what the dashboard `profile_card_preview`
    module already shows in compact form.

    Pulls the user's current ProfileCardSettings (theme + public sig
    toggle) so the page can render the correct theme on first load
    without an extra round-trip.
    """
    template_name = 'shareables/profile_card.html'
    login_url = reverse_lazy('account_login')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        profile = user.profile if hasattr(user, 'profile') else None

        if not profile:
            return context

        from trophies.models import ProfileCardSettings

        card_settings, _ = ProfileCardSettings.objects.get_or_create(profile=profile)
        is_premium = profile.user_is_premium

        context['card_theme'] = card_settings.card_theme or 'default'
        context['is_premium'] = is_premium
        context['available_themes'] = get_available_themes_for_grid(
            include_game_art=False,
            grouped=True,
        )

        context['breadcrumb'] = [
            {'text': 'Home', 'url': reverse_lazy('home')},
            {'text': 'My Shareables', 'url': reverse_lazy('my_shareables')},
            {'text': 'Profile Card'},
        ]

        track_page_view('my_shareables', 'user', self.request)
        return context
