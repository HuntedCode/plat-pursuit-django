from collections import defaultdict

from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models.functions import Lower
from django.shortcuts import redirect
from django.urls import reverse_lazy
from django.views.generic import TemplateView

from ..models import Badge, Title, UserTitle


class MyTitlesView(LoginRequiredMixin, TemplateView):
    """
    Displays all discoverable titles: earned (with equip controls) and
    locked (with full unlock details).

    Discoverable = assigned to a live badge. Excludes orphan titles and titles
    from non-live badges. Titles the viewer earned from the retired milestone
    engine survive as "Special" awards (source_type='milestone'), shown once earned.
    """
    template_name = 'trophies/my_titles.html'
    login_url = '/login/'

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and not hasattr(request.user, 'profile'):
            return redirect('link_psn')
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        profile = self.request.user.profile

        # 1. Discoverable titles: from live badges
        discoverable_ids = set(
            Badge.objects.filter(
                title__isnull=False, is_live=True
            ).values_list('title_id', flat=True)
        )
        discoverable_titles = Title.objects.filter(
            id__in=discoverable_ids
        ).order_by(Lower('name'))

        # 2. User's earned titles
        user_titles = UserTitle.objects.filter(
            profile=profile
        ).select_related('title')
        earned_map = {}  # title_id -> UserTitle
        for ut in user_titles:
            earned_map[ut.title_id] = ut
        displayed_title_id = next(
            (ut.title_id for ut in user_titles if ut.is_displayed), None
        )

        # 3. Build source mapping
        # Badge sources (live only, with title)
        badge_sources = Badge.objects.filter(
            title__in=discoverable_titles, is_live=True
        ).select_related('title', 'base_badge').order_by('tier')

        sources_by_title = defaultdict(list)
        for badge in badge_sources:
            sources_by_title[badge.title_id].append({
                'type': 'badge',
                'object': badge,
                'name': badge.effective_display_series or badge.name,
                'detail': f'Tier {badge.tier}',
                'description': badge.effective_description or '',
                'url': reverse_lazy('badge_detail', kwargs={'series_slug': badge.series_slug}) if badge.series_slug else None,
                'layers': badge.get_badge_layers(),
            })

        # 4. Badge titles: earned + unearned, each shown via its easiest (lowest-tier) path.
        badge_titles = []
        badge_earned = 0

        for title in discoverable_titles:
            ut = earned_map.get(title.id)
            badge_srcs = sources_by_title.get(title.id, [])
            if not badge_srcs:
                continue
            earned = ut is not None
            badge_titles.append({
                'title': title,
                'earned': earned,
                'state': 'have' if earned else 'need',
                'is_displayed': ut.is_displayed if ut else False,
                'earned_at': ut.earned_at if ut else None,
                'source': min(badge_srcs, key=lambda s: s['object'].tier),
            })
            if earned:
                badge_earned += 1

        # 5. Special titles: one-off awards the viewer earned from the retired milestone
        #    engine (fundraiser patron, easter eggs). Shown only once earned; there's no
        #    live source row to describe, so the card renders the title alone.
        #    Skip any whose Title a live badge also grants -- that one already renders in the
        #    badge section above, and listing it twice would double-count `total_earned`.
        special_titles = [
            {
                'title': ut.title,
                'source': None,
                'earned': True,
                'state': 'have',
                'is_displayed': ut.is_displayed,
                'earned_at': ut.earned_at,
            }
            for ut in user_titles
            if ut.source_type == 'milestone' and ut.title_id not in discoverable_ids
        ]
        special_titles.sort(key=lambda e: e['earned_at'], reverse=True)

        # Resolve displayed title name directly (works for both regular and special)
        displayed_title_name = None
        if displayed_title_id and displayed_title_id in earned_map:
            displayed_title_name = earned_map[displayed_title_id].title.name

        context.update({
            'badge_titles': badge_titles,
            'badge_total': len(badge_titles),
            'badge_earned': badge_earned,
            'special_titles': special_titles,
            'displayed_title_id': displayed_title_id,
            'displayed_title_name': displayed_title_name,
            'total_earned': badge_earned + len(special_titles),
            'profile': profile,
            'breadcrumb': [
                {'text': 'Home', 'url': reverse_lazy('home')},
                {'text': 'My Pursuit', 'url': reverse_lazy('my_pursuit_hub')},
                {'text': 'My Titles'},
            ],
        })
        return context
