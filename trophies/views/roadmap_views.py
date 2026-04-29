"""
Roadmap views: public detail page and role-gated editor.

The detail page provides a full guide experience with sticky TOC, scrollspy,
progress tracking, and per-DLC pages. The editor is gated to authors with at
least the `writer` roadmap_role (independent of Django is_staff). Writers and
editors are additionally blocked from editing published guides; only
publishers may edit a guide that is currently live.
"""
import logging

from django.contrib import messages
from django.http import Http404
from django.shortcuts import redirect
from django.views.generic import DetailView

from trophies.mixins import ProfileHotbarMixin, RoadmapAuthorRequiredMixin
from trophies.models import EarnedTrophy, Game
from trophies.permissions.roadmap_permissions import can_view_editor
from trophies.services.rating_service import RatingService
from trophies.services.roadmap_service import RoadmapService

logger = logging.getLogger('psn_api')


class RoadmapDetailView(ProfileHotbarMixin, DetailView):
    """Public roadmap detail page for a specific trophy group.

    Serves the full guide experience with sticky TOC, progress tracking,
    and guide metadata. Each DLC/trophy group gets its own page.
    Base game is served at /games/<id>/roadmap/, DLC at /games/<id>/roadmap/<group>/.
    """
    model = Game
    template_name = 'trophies/roadmap_detail.html'
    slug_field = 'np_communication_id'
    slug_url_kwarg = 'np_communication_id'

    def get_queryset(self):
        return super().get_queryset().select_related('concept')

    def get_object(self, queryset=None):
        game = super().get_object(queryset)
        if not game.concept:
            raise Http404("Game has no concept.")
        return game

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        game = self.object
        concept = game.concept
        user = self.request.user
        trophy_group_id = self.kwargs.get('trophy_group_id', 'default')

        # Author preview support: any role >= writer can preview drafts.
        preview_mode = (
            self.request.GET.get('preview') == 'true'
            and user.is_authenticated
            and getattr(user, 'profile', None) is not None
            and user.profile.has_roadmap_role('writer')
        )
        context['roadmap_preview_mode'] = preview_mode

        # Fetch the specific tab + roadmap
        if preview_mode:
            tab, roadmap = RoadmapService.get_tab_for_preview(concept, trophy_group_id)
        else:
            tab, roadmap = RoadmapService.get_tab_for_display(concept, trophy_group_id)

        if not tab:
            raise Http404("Roadmap not found.")

        context['tab'] = tab
        context['roadmap'] = roadmap
        context['active_trophy_group_id'] = trophy_group_id

        # DLC navigation strip
        context['available_tabs'] = RoadmapService.get_available_tabs(
            concept, include_drafts=preview_mode
        )

        # Resolve trophy display data for trophies referenced in steps/guides
        roadmap_trophy_ids = set()
        for step in tab.steps.all():
            for st in step.step_trophies.all():
                roadmap_trophy_ids.add(st.trophy_id)
        for tg in tab.trophy_guides.all():
            roadmap_trophy_ids.add(tg.trophy_id)

        if roadmap_trophy_ids:
            context['roadmap_trophies'] = {
                t.trophy_id: t
                for t in game.trophies.filter(
                    trophy_group_id=trophy_group_id,
                    trophy_id__in=roadmap_trophy_ids,
                )
            }
        else:
            context['roadmap_trophies'] = {}

        # Profile earned data + progress computation
        profile_earned = {}
        if (user.is_authenticated and hasattr(user, 'profile')
                and user.profile and user.profile.is_linked):
            earned_qs = EarnedTrophy.objects.filter(
                profile=user.profile, trophy__game=game,
            ).select_related('trophy')
            profile_earned = {
                e.trophy.trophy_id: {
                    'earned': e.earned,
                    'earned_date_time': e.earned_date_time,
                }
                for e in earned_qs
            }
        context['profile_earned'] = profile_earned
        context['progress'] = RoadmapService.compute_progress(tab, profile_earned)

        # Community rating averages for this trophy group
        context['community_averages'] = (
            RatingService.get_cached_community_averages_for_group(
                concept, tab.concept_trophy_group
            )
        )

        # Per-tab counts of online / unobtainable trophy guides, surfaced in the
        # metrics strip alongside the existing Yes/No flags.
        online_count = 0
        unobtainable_count = 0
        for tg in tab.trophy_guides.all():
            if tg.is_online:
                online_count += 1
            if tg.is_unobtainable:
                unobtainable_count += 1
        context['online_trophy_count'] = online_count
        context['unobtainable_trophy_count'] = unobtainable_count

        # Header background (used in header card only, not full-page)
        context['header_bg_url'] = getattr(concept, 'bg_url', None) or ''

        # Breadcrumbs
        group_name = tab.concept_trophy_group.display_name
        context['breadcrumb'] = [
            {'text': 'Games', 'url': '/games/'},
            {'text': game.title_name, 'url': f'/games/{game.np_communication_id}/'},
            {'text': f'Roadmap: {group_name}'},
        ]

        # SEO
        step_count = len(tab.steps.all())
        guide_count = len(tab.trophy_guides.all())
        context['seo_description'] = (
            f"Trophy roadmap for {game.title_name} ({group_name}). "
            f"{step_count} steps, {guide_count} trophy guides."
        )

        context['game'] = game
        return context


class RoadmapEditorView(RoadmapAuthorRequiredMixin, DetailView):
    """Role-gated roadmap editor page.

    Open to any user with at least the `writer` roadmap role on UNPUBLISHED
    guides. Published guides are publisher-only — writers and editors who try
    to open the editor on a live guide are redirected back to the detail page
    with a flash explaining why. Per-action permission scoping (writer-only-
    edits-own-sections, editor-only deletes, publisher-only status toggle) is
    enforced server-side in the merge / publish endpoints, with the editor UI
    hiding affordances the current role lacks.

    Note: ProfileHotbarMixin is intentionally NOT mixed in. The hotbar
    competes with the sticky page header for vertical space and adds noise
    that's irrelevant to authoring (sync status, queue position, etc.).
    """
    model = Game
    template_name = 'trophies/roadmap_edit.html'
    slug_field = 'np_communication_id'
    slug_url_kwarg = 'np_communication_id'

    def get_object(self, queryset=None):
        game = super().get_object(queryset)
        if not game.concept:
            raise Http404("Game has no concept.")
        return game

    def get(self, request, *args, **kwargs):
        # Defense in depth: even though templates hide the Edit button on
        # published guides for non-publishers, anyone with the URL can hit
        # this view. Block at the top of GET before rendering.
        self.object = self.get_object()
        roadmap = RoadmapService.get_roadmap_for_editor(self.object.concept)
        if not can_view_editor(request.user.profile, roadmap):
            messages.warning(
                request,
                'This guide is published — only publishers can edit it directly. '
                'Ask a publisher to unpublish first if you need to make changes.',
            )
            return redirect('roadmap_detail', self.object.np_communication_id)
        return super().get(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        from trophies.models import Profile

        context = super().get_context_data(**kwargs)
        game = self.object
        concept = game.concept

        # Get or create roadmap with all nested data
        roadmap = RoadmapService.get_roadmap_for_editor(concept)
        context['roadmap'] = roadmap

        # Build trophy data for the picker, organized by trophy_group_id
        trophies_by_group = {}
        for trophy in game.trophies.order_by('trophy_group_id', 'trophy_id'):
            group_id = trophy.trophy_group_id
            if group_id not in trophies_by_group:
                trophies_by_group[group_id] = []
            trophies_by_group[group_id].append({
                'trophy_id': trophy.trophy_id,
                'name': trophy.trophy_name,
                'detail': trophy.trophy_detail or '',
                'type': trophy.trophy_type,
                'icon_url': trophy.trophy_icon_url or '',
            })
        context['trophies_by_group'] = trophies_by_group

        # Build tab data for JS initialization. Each step/guide carries its
        # `created_by_id` so the JS can render an "Owned by X" badge and lock
        # the inputs for writers who don't own the row. Profile display info
        # lives in a separate lookup map (`profiles_by_id`) so duplicate
        # profiles aren't spammed across steps.
        referenced_profile_ids = set()

        def _track(profile_id):
            if profile_id:
                referenced_profile_ids.add(profile_id)

        tabs_data = []
        for tab in roadmap.tabs.all():
            ctg = tab.concept_trophy_group
            _track(tab.created_by_id)
            _track(tab.last_edited_by_id)
            steps_data = []
            for step in tab.steps.all():
                _track(step.created_by_id)
                _track(step.last_edited_by_id)
                steps_data.append({
                    'id': step.id,
                    'title': step.title,
                    'description': step.description,
                    'youtube_url': step.youtube_url,
                    'order': step.order,
                    'created_by_id': step.created_by_id,
                    'last_edited_by_id': step.last_edited_by_id,
                    'trophy_ids': list(
                        step.step_trophies.order_by('order').values_list('trophy_id', flat=True)
                    ),
                })
            trophy_guides_data = {
                tg.trophy_id: {
                    'body': tg.body,
                    'is_missable': tg.is_missable,
                    'is_online': tg.is_online,
                    'is_unobtainable': tg.is_unobtainable,
                    'created_by_id': tg.created_by_id,
                    'last_edited_by_id': tg.last_edited_by_id,
                }
                for tg in tab.trophy_guides.all()
            }
            for tg in tab.trophy_guides.all():
                _track(tg.created_by_id)
                _track(tg.last_edited_by_id)
            tabs_data.append({
                'id': tab.id,
                'trophy_group_id': ctg.trophy_group_id,
                'display_name': ctg.display_name,
                'general_tips': tab.general_tips,
                'youtube_url': tab.youtube_url,
                'difficulty': tab.difficulty,
                'estimated_hours': tab.estimated_hours,
                'missable_count': tab.missable_count,
                'online_required': tab.online_required,
                'min_playthroughs': tab.min_playthroughs,
                'created_by_id': tab.created_by_id,
                'last_edited_by_id': tab.last_edited_by_id,
                'steps': steps_data,
                'trophy_guides': trophy_guides_data,
            })
        context['tabs_data'] = tabs_data

        # Profile lookup for ownership badges. One query, regardless of how
        # many steps/guides reference profiles.
        profiles_by_id = {}
        if referenced_profile_ids:
            for p in Profile.objects.filter(id__in=referenced_profile_ids).only(
                'id', 'psn_username', 'display_psn_username', 'avatar_url'
            ):
                profiles_by_id[p.id] = {
                    'username': p.psn_username,
                    'display_name': p.display_psn_username or p.psn_username,
                    'avatar_url': p.avatar_url or '',
                }
        context['profiles_by_id'] = profiles_by_id

        # Breadcrumb
        context['breadcrumb'] = [
            {'text': 'Games', 'url': '/games/'},
            {'text': game.title_name, 'url': f'/games/{game.np_communication_id}/'},
            {'text': 'Edit Roadmap'},
        ]

        context['game'] = game
        context['concept'] = concept

        # Role flags for the editor UI: hide delete affordances for writers,
        # hide publish toggle for non-publishers. Server-side merge/publish
        # endpoints enforce these as well.
        profile = self.request.user.profile
        context['author_role'] = profile.roadmap_role
        context['author_can_delete'] = profile.has_roadmap_role('editor')
        context['author_can_publish'] = profile.has_roadmap_role('publisher')
        # JS needs the viewer's profile id to apply writer-scoping (lock
        # inputs on sections owned by other writers). Editor+ bypasses the
        # lock; writers only see their own sections as editable.
        context['viewer_profile_id'] = profile.id

        # Notes — surface unread count for the heads-up banner. The actual
        # notes are fetched client-side via /api/v1/roadmap/<id>/notes/ on
        # editor init; we don't inline them in tabs_data because they're
        # decoupled from the lock + branch flow.
        from trophies.services import roadmap_note_service
        context['notes_unread_count'] = roadmap_note_service.unread_count(
            profile=profile, roadmap=roadmap,
        )

        # Mention autocomplete: pre-load ALL profiles with writer-or-higher
        # role so the JS can filter purely client-side. The team is small
        # enough that fetching the whole list once per page is cheaper and
        # snappier than a debounced search endpoint. Order by role tier
        # (publishers first, then editors, then writers) and within each by
        # username — gives a sensible default ranking when prefixes tie.
        ROLE_ORDER = {'publisher': 0, 'editor': 1, 'writer': 2}
        mention_qs = Profile.objects.filter(
            roadmap_role__in=['writer', 'editor', 'publisher']
        ).only(
            'id', 'psn_username', 'display_psn_username', 'avatar_url', 'roadmap_role',
        )
        context['mentionable_authors'] = sorted(
            (
                {
                    'username': p.psn_username,
                    'display_name': p.display_psn_username or p.psn_username,
                    'avatar_url': p.avatar_url or '',
                    'role': p.roadmap_role,
                }
                for p in mention_qs
            ),
            key=lambda a: (ROLE_ORDER.get(a['role'], 99), a['username']),
        )

        return context
