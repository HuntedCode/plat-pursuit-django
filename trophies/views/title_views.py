from collections import defaultdict

from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models.functions import Lower
from django.shortcuts import redirect
from django.urls import reverse_lazy
from django.views.generic import TemplateView

from ..models import (
    Badge, CALENDAR_DAYS_PER_MONTH, Milestone, Title, UserTitle,
    UserMilestoneProgress,
)
from trophies.milestone_constants import MILESTONE_CATEGORIES, CRITERIA_TYPE_DISPLAY_NAMES, MONTH_MAP


class MyTitlesView(LoginRequiredMixin, TemplateView):
    """
    Displays all discoverable titles: earned (with equip controls) and
    locked (with full unlock details).

    Discoverable = assigned to a live badge OR any milestone.
    Excludes orphan titles and titles from non-live badges.
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

        # 1. Discoverable titles: from live badges OR any milestone
        badge_title_ids = set(
            Badge.objects.filter(
                title__isnull=False, is_live=True
            ).values_list('title_id', flat=True)
        )
        milestone_title_ids = set(
            Milestone.objects.filter(
                title__isnull=False
            ).values_list('title_id', flat=True)
        )
        discoverable_ids = badge_title_ids | milestone_title_ids
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

        # Milestone sources (with title)
        milestone_sources = Milestone.objects.filter(
            title__in=discoverable_titles
        ).select_related('title')

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

        for milestone in milestone_sources:
            # Find category slug for deep-linking
            category_slug = None
            for slug, cat_config in MILESTONE_CATEGORIES.items():
                if milestone.criteria_type in cat_config['criteria_types']:
                    category_slug = slug
                    break

            criteria_display = CRITERIA_TYPE_DISPLAY_NAMES.get(
                milestone.criteria_type, 'Milestone'
            )

            base_url = str(reverse_lazy('milestones_list'))
            if category_slug:
                milestone_url = f"{base_url}?cat={category_slug}#{milestone.criteria_type}"
            else:
                milestone_url = base_url

            sources_by_title[milestone.title_id].append({
                'type': 'milestone',
                'object': milestone,
                'name': milestone.name,
                'category_name': criteria_display,
                'detail': milestone.description,
                'url': milestone_url,
                'image': milestone.image.url if milestone.image else None,
            })

        # 3b. Identify manual-only titles (special recognition, not gameplay goals)
        manual_title_ids = set()
        for title_id, sources in sources_by_title.items():
            if all(
                s['type'] == 'milestone' and s['object'].criteria_type == 'manual'
                for s in sources
            ):
                manual_title_ids.add(title_id)

        # 4. Milestone progress for locked titles
        locked_milestone_ids = [
            ms.id for ms in milestone_sources
            if ms.title_id not in earned_map
            and ms.title_id not in manual_title_ids
        ]
        progress_qs = UserMilestoneProgress.objects.filter(
            profile=profile,
            milestone_id__in=locked_milestone_ids
        )
        progress_map = {p.milestone_id: p.progress_value for p in progress_qs}

        # Attach progress to locked milestone sources
        for title_id, sources in sources_by_title.items():
            if title_id not in earned_map:
                for source in sources:
                    if source['type'] == 'milestone':
                        ms = source['object']
                        current = progress_map.get(ms.id, 0)
                        # Calendar month milestones store day counts as
                        # progress but have required_value=1 (boolean).
                        # Use the actual days-in-month as the denominator.
                        month_num = MONTH_MAP.get(ms.criteria_type)
                        if month_num:
                            required = CALENDAR_DAYS_PER_MONTH[month_num]
                        else:
                            required = ms.required_value
                        source['progress_value'] = current
                        source['required_value'] = required
                        if required > 0:
                            source['progress_pct'] = min(
                                round((current / required) * 100, 1), 100
                            )
                        else:
                            source['progress_pct'] = 0

        # 5. Split into earned, locked, and special titles
        earned_titles = []
        locked_titles = []
        special_titles = []
        badge_title_earned = 0
        milestone_title_earned = 0

        for title in discoverable_titles:
            ut = earned_map.get(title.id)
            sources = sources_by_title.get(title.id, [])
            is_manual = title.id in manual_title_ids

            # Manual-only titles: show only if earned, never show locked
            if is_manual:
                if ut:
                    special_titles.append({
                        'title': title,
                        'sources': sources,
                        'is_displayed': ut.is_displayed,
                        'earned_at': ut.earned_at,
                    })
                continue

            # Determine primary source type for filter tabs
            source_types = {s['type'] for s in sources}
            if source_types == {'badge'}:
                primary_source = 'badge'
            elif source_types == {'milestone'}:
                primary_source = 'milestone'
            else:
                primary_source = 'both'

            entry = {
                'title': title,
                'sources': sources,
                'source_type': primary_source,
            }
            if ut:
                entry['is_displayed'] = ut.is_displayed
                entry['earned_at'] = ut.earned_at
                earned_titles.append(entry)
                if 'badge' in source_types:
                    badge_title_earned += 1
                if 'milestone' in source_types:
                    milestone_title_earned += 1
            else:
                # Determine encouraging flavor text for locked footer
                max_pct = max(
                    (s.get('progress_pct', 0) for s in sources
                     if s['type'] == 'milestone'),
                    default=0,
                )
                entry['lock_flavor'] = (
                    'Almost there...' if max_pct > 50
                    else 'The hunt continues...'
                )
                locked_titles.append(entry)

        # Sort earned titles by newest first (default)
        earned_titles.sort(key=lambda e: e['earned_at'], reverse=True)
        special_titles.sort(key=lambda e: e['earned_at'], reverse=True)

        # Resolve displayed title name directly (works for both regular and special)
        displayed_title_name = None
        if displayed_title_id and displayed_title_id in earned_map:
            displayed_title_name = earned_map[displayed_title_id].title.name

        context.update({
            'earned_titles': earned_titles,
            'locked_titles': locked_titles,
            'special_titles': special_titles,
            'displayed_title_id': displayed_title_id,
            'displayed_title_name': displayed_title_name,
            'total_earned': len(earned_titles),
            'total_available': len(earned_titles) + len(locked_titles),
            'badge_title_earned': badge_title_earned,
            'milestone_title_earned': milestone_title_earned,
            'profile': profile,
            'breadcrumb': [
                {'text': 'Home', 'url': reverse_lazy('home')},
                {'text': 'My Titles'},
            ],
        })
        return context
