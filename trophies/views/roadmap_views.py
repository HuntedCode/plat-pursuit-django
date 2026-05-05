"""
Roadmap views: public detail page and role-gated editor.

Each ConceptTrophyGroup gets its own Roadmap. Public viewers can only see
published roadmaps; writers+ also see drafts via `?preview=true`. The
detail page provides the full guide experience with sticky TOC, scrollspy,
progress, and metadata. The editor is gated to authors with at least the
`writer` roadmap_role on UNPUBLISHED roadmaps; published roadmaps are
publisher-only at the editor entry point.
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

    Each CTG (base game + each DLC) is its own Roadmap and renders at its
    own URL: `/games/<id>/roadmap/` (base, default) or
    `/games/<id>/roadmap/<group>/` (DLC). When the requested CTG isn't
    published, we fall back to the first published roadmap on the same
    concept (writers shipping DLC-first or base-only stay reachable from
    the canonical URL); if nothing is published, 404.
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

    def get(self, request, *args, **kwargs):
        """Override to handle the b -> a routing fallback before render.

        Standard DetailView.get() resolves the object then calls
        get_context_data. We need the redirect decision (when the
        requested CTG isn't published but another one is) to happen
        BEFORE we commit to rendering.
        """
        self.object = self.get_object()
        user = request.user
        requested_group_id = kwargs.get('trophy_group_id', 'default')

        preview_mode = self._is_preview_mode(request)
        if not preview_mode:
            # Public path: if the requested CTG isn't published, redirect
            # to the first published roadmap on this concept.
            roadmap, resolved_group_id, redirected = (
                RoadmapService.resolve_public_target(
                    self.object.concept, requested_group_id,
                )
            )
            if roadmap is None:
                raise Http404("No published roadmap available.")
            if redirected:
                # Redirect to the canonical URL for the resolved CTG.
                if resolved_group_id == 'default':
                    return redirect('roadmap_detail', self.object.np_communication_id)
                return redirect(
                    'roadmap_detail_dlc',
                    self.object.np_communication_id,
                    resolved_group_id,
                )
            self._cached_roadmap = roadmap
            self._resolved_group_id = resolved_group_id
        else:
            # Preview path: writer+ sees any-status roadmap. No fallback —
            # the writer is asking for THIS specific CTG.
            roadmap = RoadmapService.get_roadmap_for_preview(
                self.object.concept, requested_group_id,
            )
            if not roadmap:
                raise Http404("Roadmap not found.")
            self._cached_roadmap = roadmap
            self._resolved_group_id = requested_group_id

        return super().get(request, *args, **kwargs)

    def _is_preview_mode(self, request):
        user = request.user
        return (
            request.GET.get('preview') == 'true'
            and user.is_authenticated
            and getattr(user, 'profile', None) is not None
            and user.profile.has_roadmap_role('writer')
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        game = self.object
        concept = game.concept
        user = self.request.user
        roadmap = self._cached_roadmap
        trophy_group_id = self._resolved_group_id
        preview_mode = self._is_preview_mode(self.request)

        context['roadmap_preview_mode'] = preview_mode

        # When the previewing author holds an active edit lock with a draft
        # branch, overlay it onto the in-memory roadmap so the preview
        # reflects uncommitted edits (saves a round-trip of merge -> preview
        # -> revert).
        branch_applied = False
        if preview_mode:
            from trophies.models import RoadmapEditLock
            lock = (
                RoadmapEditLock.objects
                .filter(roadmap=roadmap, holder=user.profile)
                .first()
            )
            if lock is not None and not lock.is_expired():
                payload = lock.branch_payload
                if isinstance(payload, dict):
                    RoadmapService.apply_branch_overlay(roadmap, payload)
                    branch_applied = True
        context['roadmap_branch_preview'] = branch_applied

        context['roadmap'] = roadmap
        context['active_trophy_group_id'] = trophy_group_id

        # Slug -> CollectibleType lookup consumed by the
        # `render_collectible_pills` template filter to swap [[slug]]
        # tokens in markdown for color-coded pills. apply_branch_overlay
        # rebuilds this prefetch in preview mode, so unsaved types from
        # the current edit session show up too.
        context['collectibles_by_slug'] = {
            ct.slug: ct for ct in roadmap.collectible_types.all()
        }

        # DLC navigation strip: enumerate roadmaps under this concept.
        # Public sees only published; authors in preview mode see drafts too.
        context['available_ctgs'] = RoadmapService.get_available_ctgs(
            concept, include_drafts=preview_mode,
        )

        # Resolve trophy display data for trophies referenced in the
        # roadmap's steps + trophy guides.
        roadmap_trophy_ids = set()
        for step in roadmap.steps.all():
            for st in step.step_trophies.all():
                roadmap_trophy_ids.add(st.trophy_id)
        for tg in roadmap.trophy_guides.all():
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

        # Profile earned data + progress computation.
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
        context['progress'] = RoadmapService.compute_progress(roadmap, profile_earned)

        # Community rating averages for this trophy group.
        context['community_averages'] = (
            RatingService.get_cached_community_averages_for_group(
                concept, roadmap.concept_trophy_group,
            )
        )

        # Per-roadmap counts of online / unobtainable trophy guides,
        # surfaced in the metrics strip alongside the existing flags.
        online_count = 0
        unobtainable_count = 0
        for tg in roadmap.trophy_guides.all():
            if tg.is_online:
                online_count += 1
            if tg.is_unobtainable:
                unobtainable_count += 1
        context['online_trophy_count'] = online_count
        context['unobtainable_trophy_count'] = unobtainable_count

        # Header background.
        context['header_bg_url'] = getattr(concept, 'bg_url', None) or ''

        # Breadcrumbs.
        group_name = roadmap.concept_trophy_group.display_name
        context['breadcrumb'] = [
            {'text': 'Games', 'url': '/games/'},
            {'text': game.title_name, 'url': f'/games/{game.np_communication_id}/'},
            {'text': f'Roadmap: {group_name}'},
        ]

        # SEO.
        step_count = len(roadmap.steps.all())
        guide_count = len(roadmap.trophy_guides.all())
        context['seo_description'] = (
            f"Trophy roadmap for {game.title_name} ({group_name}). "
            f"{step_count} steps, {guide_count} trophy guides."
        )

        context['game'] = game
        # Phase metadata for the always-visible phase pill on each card and
        # the "By recommended phase" sort option in the toolbar.
        from trophies.util_modules.trophy_phases import phases_for_template, phases_by_key
        context['trophy_phases'] = phases_for_template()
        context['trophy_phases_by_key'] = phases_by_key()
        return context


class RoadmapEditorView(RoadmapAuthorRequiredMixin, DetailView):
    """Role-gated roadmap editor page (per-CTG).

    URL: `/games/<np>/roadmap/edit/` (base) or
         `/games/<np>/roadmap/<group_id>/edit/` (DLC).

    Open to any user with at least the `writer` roadmap role on
    UNPUBLISHED roadmaps. Published roadmaps are publisher-only at the
    editor entry point: writers and editors who try to open the editor on
    a live roadmap are redirected back to the detail page with a flash
    explaining why. Per-action permission scoping (writer-only-edits-own-
    sections, editor-only deletes, publisher-only status toggle) is
    enforced server-side in the merge / publish endpoints, with the
    editor UI hiding affordances the current role lacks.

    Note: ProfileHotbarMixin is intentionally NOT mixed in. The hotbar
    competes with the sticky page header for vertical space and adds
    noise that's irrelevant to authoring.
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

    def _trophy_group_id(self):
        return self.kwargs.get('trophy_group_id', 'default')

    def get(self, request, *args, **kwargs):
        # Defense in depth: even though templates hide the Edit button on
        # published roadmaps for non-publishers, anyone with the URL can
        # hit this view. Block at the top of GET before rendering.
        self.object = self.get_object()
        roadmap = RoadmapService.get_roadmap_for_editor(
            self.object.concept, self._trophy_group_id(),
        )
        if roadmap is None:
            raise Http404("Trophy group not found.")
        if not can_view_editor(request.user.profile, roadmap):
            messages.warning(
                request,
                'This roadmap is published. Only publishers can edit it directly. '
                'Ask a publisher to unpublish it first if you need to make changes.',
            )
            if self._trophy_group_id() == 'default':
                return redirect('roadmap_detail', self.object.np_communication_id)
            return redirect(
                'roadmap_detail_dlc',
                self.object.np_communication_id,
                self._trophy_group_id(),
            )
        self._cached_roadmap = roadmap
        return super().get(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        from trophies.models import Profile

        context = super().get_context_data(**kwargs)
        game = self.object
        concept = game.concept
        roadmap = self._cached_roadmap
        ctg = roadmap.concept_trophy_group
        context['roadmap'] = roadmap

        # Trophy data for the picker, scoped to the roadmap's CTG (only
        # trophies in this group are pickable since the roadmap is
        # per-CTG now).
        trophies_in_group_serialized = [
            {
                'trophy_id': t.trophy_id,
                'name': t.trophy_name,
                'detail': t.trophy_detail or '',
                'type': t.trophy_type,
                'icon_url': t.trophy_icon_url or '',
            }
            for t in (
                game.trophies
                .filter(trophy_group_id=ctg.trophy_group_id)
                .order_by('trophy_id')
            )
        ]
        context['trophies_in_group'] = trophies_in_group_serialized
        # Legacy compatibility for the existing editor JS, which reads a
        # `{trophy_group_id: [...]}` dict. One key here since the editor
        # now operates on a single CTG per session.
        context['trophies_by_group'] = {
            ctg.trophy_group_id: trophies_in_group_serialized,
        }

        # Build the flat roadmap_data block for JS init.
        referenced_profile_ids = set()

        def _track(profile_id):
            if profile_id:
                referenced_profile_ids.add(profile_id)

        _track(roadmap.created_by_id)
        _track(roadmap.last_edited_by_id)

        steps_data = []
        for step in roadmap.steps.all():
            _track(step.created_by_id)
            _track(step.last_edited_by_id)
            steps_data.append({
                'id': step.id,
                'title': step.title,
                'description': step.description,
                'youtube_url': step.youtube_url,
                'order': step.order,
                'gallery_images': list(step.gallery_images or []),
                'created_by_id': step.created_by_id,
                'last_edited_by_id': step.last_edited_by_id,
                'trophy_ids': list(
                    step.step_trophies.order_by('order').values_list('trophy_id', flat=True)
                ),
            })

        trophy_guides_data = {}
        for tg in roadmap.trophy_guides.all():
            _track(tg.created_by_id)
            _track(tg.last_edited_by_id)
            trophy_guides_data[tg.trophy_id] = {
                'id': tg.id,
                'body': tg.body,
                'is_missable': tg.is_missable,
                'is_online': tg.is_online,
                'is_unobtainable': tg.is_unobtainable,
                'phase': tg.phase or '',
                'gallery_images': list(tg.gallery_images or []),
                'created_by_id': tg.created_by_id,
                'last_edited_by_id': tg.last_edited_by_id,
            }

        roadmap_data = {
            'id': roadmap.id,
            'concept_trophy_group_id': ctg.id,
            'trophy_group_id': ctg.trophy_group_id,
            'display_name': ctg.display_name,
            'status': roadmap.status,
            'general_tips': roadmap.general_tips,
            'youtube_url': roadmap.youtube_url,
            'difficulty': roadmap.difficulty,
            'estimated_hours': roadmap.estimated_hours,
            'min_playthroughs': roadmap.min_playthroughs,
            'created_by_id': roadmap.created_by_id,
            'last_edited_by_id': roadmap.last_edited_by_id,
            'steps': steps_data,
            'trophy_guides': trophy_guides_data,
        }
        context['roadmap_data'] = roadmap_data
        # Legacy compatibility shim: existing editor JS still reads
        # `tabsData` as a 1-element list of tab-shaped dicts. Wrap the
        # roadmap so the JS can keep operating until it's rewritten for
        # the flat shape. Once the JS migrates this can be deleted.
        context['tabs_data_legacy'] = [roadmap_data]

        # CTG nav: every CTG on the concept in stable sort order. The
        # active one renders as a primary pill in place, the rest as
        # quick links — same positions regardless of which CTG is
        # currently active so the bar layout doesn't jump when writers
        # switch between base / DLC. Clicking a "Not started" link
        # silently get_or_creates the empty Roadmap on the editor view.
        from trophies.models import Roadmap as RoadmapModel, RoadmapEditLock

        roadmaps_by_ctg_id = {
            r.concept_trophy_group_id: r
            for r in (
                RoadmapModel.objects
                .filter(concept=concept)
                .select_related('concept_trophy_group')
            )
        }
        # One query for every lock on this concept's roadmaps so we can
        # mark tabs as "Resuming" when the viewer holds the lock.
        viewer_lock_roadmap_ids = set(
            RoadmapEditLock.objects
            .filter(
                roadmap__concept=concept,
                holder=self.request.user.profile,
            )
            .values_list('roadmap_id', flat=True)
        )

        ctg_nav = []
        for ctg_obj in concept.concept_trophy_groups.all().order_by(
            'sort_order', 'trophy_group_id',
        ):
            sib = roadmaps_by_ctg_id.get(ctg_obj.id)
            ctg_nav.append({
                'trophy_group_id': ctg_obj.trophy_group_id,
                'display_name': ctg_obj.display_name,
                'roadmap_id': sib.id if sib else None,
                'status': sib.status if sib else None,
                'has_roadmap': sib is not None,
                'held_by_viewer': bool(sib and sib.id in viewer_lock_roadmap_ids),
                'is_active': ctg_obj.id == ctg.id,
            })
        context['ctg_nav'] = ctg_nav

        # Profile lookup for ownership badges. One query, regardless of
        # how many steps/guides reference profiles.
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

        # Breadcrumb.
        context['breadcrumb'] = [
            {'text': 'Games', 'url': '/games/'},
            {'text': game.title_name, 'url': f'/games/{game.np_communication_id}/'},
            {'text': f'Edit Roadmap: {ctg.display_name}'},
        ]

        context['game'] = game
        context['concept'] = concept

        # Role flags for the editor UI.
        profile = self.request.user.profile
        context['author_role'] = profile.roadmap_role
        context['author_can_delete'] = profile.has_roadmap_role('editor')
        context['author_can_publish'] = profile.has_roadmap_role('publisher')
        context['viewer_profile_id'] = profile.id

        # Notes unread count for the heads-up banner.
        from trophies.services import roadmap_note_service
        context['notes_unread_count'] = roadmap_note_service.unread_count(
            profile=profile, roadmap=roadmap,
        )

        # Mention autocomplete: pre-load ALL profiles with writer-or-higher
        # role so the JS can filter purely client-side.
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

        # Phase metadata for the editor's per-trophy-guide phase dropdown.
        from trophies.util_modules.trophy_phases import phases_for_template
        context['trophy_phases'] = phases_for_template()

        return context
