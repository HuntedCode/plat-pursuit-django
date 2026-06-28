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
                title__isnull=False, is_active=True
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

        # Milestone sources (live only, with title)
        milestone_sources = Milestone.objects.filter(
            title__in=discoverable_titles, is_active=True
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

        # 5. Partition discoverable titles by SOURCE into three cohesive groups, each
        #    holding BOTH earned and unearned titles (the template + JS give each its own
        #    Have/Need filter + sort). A title with a trackable (non-manual) milestone
        #    source lives in the milestone group -- progress is the actionable framing;
        #    manual-only titles are Special; everything else is a badge title. Each title
        #    lands in exactly one group, so it never appears twice.
        badge_titles = []
        milestone_titles = []
        special_titles = []
        badge_earned = 0
        milestone_earned = 0

        for title in discoverable_titles:
            ut = earned_map.get(title.id)
            sources = sources_by_title.get(title.id, [])
            earned = ut is not None

            # Manual-only titles: Special, and only shown once earned.
            if title.id in manual_title_ids:
                if ut:
                    special_titles.append({
                        'title': title,
                        'source': sources[0] if sources else None,
                        'earned': True,
                        'state': 'have',
                        'is_displayed': ut.is_displayed,
                        'earned_at': ut.earned_at,
                    })
                continue

            entry = {
                'title': title,
                'earned': earned,
                'state': 'have' if earned else 'need',
                'is_displayed': ut.is_displayed if ut else False,
                'earned_at': ut.earned_at if ut else None,
            }

            ms_sources = [
                s for s in sources
                if s['type'] == 'milestone' and s['object'].criteria_type != 'manual'
            ]
            badge_srcs = [s for s in sources if s['type'] == 'badge']

            if ms_sources:
                # When unearned, surface the CLOSEST milestone (most motivating).
                src = ms_sources[0] if earned else max(
                    ms_sources, key=lambda s: s.get('progress_pct', 0)
                )
                entry['source'] = src
                entry['progress_pct'] = 100 if earned else src.get('progress_pct', 0)
                milestone_titles.append(entry)
                if earned:
                    milestone_earned += 1
            elif badge_srcs:
                # Lowest tier = the easiest path to the title.
                entry['source'] = min(badge_srcs, key=lambda s: s['object'].tier)
                badge_titles.append(entry)
                if earned:
                    badge_earned += 1

        special_titles.sort(key=lambda e: e['earned_at'], reverse=True)

        # Resolve displayed title name directly (works for both regular and special)
        displayed_title_name = None
        if displayed_title_id and displayed_title_id in earned_map:
            displayed_title_name = earned_map[displayed_title_id].title.name

        context.update({
            'badge_titles': badge_titles,
            'badge_total': len(badge_titles),
            'badge_earned': badge_earned,
            'milestone_titles': milestone_titles,
            'milestone_total': len(milestone_titles),
            'milestone_earned': milestone_earned,
            'special_titles': special_titles,
            'displayed_title_id': displayed_title_id,
            'displayed_title_name': displayed_title_name,
            'total_earned': badge_earned + milestone_earned + len(special_titles),
            'profile': profile,
            'breadcrumb': [
                {'text': 'Home', 'url': reverse_lazy('home')},
                {'text': 'My Pursuit', 'url': reverse_lazy('my_pursuit_hub')},
                {'text': 'My Titles'},
            ],
        })
        return context
