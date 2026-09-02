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

from trophies.mixins import RoadmapAuthorRequiredMixin
from trophies.models import EarnedTrophy, Game
from trophies.permissions.roadmap_permissions import can_view_editor
from trophies.services.rating_service import RatingService
from trophies.services.roadmap_service import RoadmapService

logger = logging.getLogger('psn_api')


class RoadmapDetailView(DetailView):
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
        if request.GET.get('preview') != 'true':
            return False
        if not user.is_authenticated:
            return False
        profile = getattr(user, 'profile', None)
        if profile is None:
            return False
        # Fast path: a global writer+ doesn't need a roadmap lookup.
        # Covers every existing author (writer / editor / publisher).
        if profile.has_roadmap_role('writer'):
            return True
        # Slow path: trial users need the specific roadmap they're
        # requesting to check their assignment via the per-roadmap
        # escalation in `has_roadmap_role`. This runs BEFORE `get()`
        # populates `_cached_roadmap`, so we resolve from URL kwargs
        # here. `self.object` is already set by `get()` line 59 so
        # we can read its concept.
        roadmap = getattr(self, '_cached_roadmap', None)
        if roadmap is None:
            requested_group_id = self.kwargs.get('trophy_group_id', 'default')
            roadmap = RoadmapService.get_roadmap_for_preview(
                self.object.concept, requested_group_id,
            )
        return profile.has_roadmap_role('writer', roadmap)

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

        # Unified ref dict for `render_roadmap_refs`. Bundles the
        # collectible-type lookup with steps + areas so the renderer
        # can resolve `[[step:N]]`, `[[area:slug]]`, and `[[section:*]]`
        # in addition to the bare `[[slug]]` collectible form.
        # Keys are stringified to match how the regex captures them.
        steps_by_id = {}
        for idx, step in enumerate(roadmap.steps.all()):
            steps_by_id[str(step.id)] = {
                'title': step.title or '',
                'position': idx + 1,
            }
        # Areas are keyed by BOTH slug and stringified id so a token can
        # resolve via either form. Saved content always uses slug (the
        # merge-time translator rewrites `[[area:-N]]` -> slug); the id
        # path is purely a safety net for any pre-translation content
        # that might somehow still be in the database.
        areas_by_key = {}
        # Same dual-keying (slug + stringified id) for sub-areas so
        # `[[subarea:-N]]` resolves during preview and `[[subarea:slug]]`
        # resolves post-merge. Each entry carries area context for the
        # pill's hover title so writers know which parent area a sub-area
        # belongs to.
        subareas_by_key = {}
        for a in roadmap.collectible_areas.all():
            entry = {'name': a.name or a.slug or f'Area {a.id}'}
            if a.slug:
                areas_by_key[a.slug] = entry
            areas_by_key[str(a.id)] = entry
            for sa in a.subareas.all():
                sa_entry = {
                    'name': sa.name or sa.slug or f'Sub-area {sa.id}',
                    'area_slug': a.slug,
                    'area_name': a.name or a.slug or '',
                }
                if sa.slug:
                    subareas_by_key[sa.slug] = sa_entry
                subareas_by_key[str(sa.id)] = sa_entry
        context['roadmap_refs'] = {
            'collectibles': context['collectibles_by_slug'],
            'steps': steps_by_id,
            'areas': areas_by_key,
            'subareas': subareas_by_key,
        }

        # Collectible Tracker context — only built when the roadmap has
        # at least one type with at least one item. The tracker section
        # renders from this dict; absence means "no collectibles, hide
        # the section + the TOC anchor entirely".
        collectible_areas = list(roadmap.collectible_areas.all())
        collectible_types = list(roadmap.collectible_types.all())
        any_items = any(ct.items.all() for ct in collectible_types)
        if collectible_types and any_items:
            # Per-viewer found set (logged-in path only). Anonymous viewers
            # use localStorage on the client; the server doesn't need to
            # know about their state.
            found_ids = set()
            if user.is_authenticated and hasattr(user, 'profile') and user.profile:
                from trophies.models import UserCollectibleProgress
                found_ids = set(
                    UserCollectibleProgress.objects
                    .filter(profile=user.profile, item__collectible_type__roadmap=roadmap)
                    .values_list('item_id', flat=True)
                )

            # Build the rendering shape: each item carries an `is_found` flag
            # plus its owning type (for color/icon/filter). Areas keep their
            # order; within each area items are sorted by `order` regardless
            # of type so the playthrough sequence is preserved (3 journals,
            # health upgrade, 2 journals, mana upgrade — not type-grouped).
            # An "Unsorted" pseudo-area collects area_id=None items.
            #
            # Sub-areas (opt-in second-level grouping) attach to areas; each
            # area's bucket carries a `subareas` dict so items / markers can
            # split between loose (in the area) and sub-area sections.
            # Unsorted bucket has no sub-areas (no parent area to attach to).
            UNSORTED_KEY = '__unsorted__'
            area_buckets = {
                a.id: {
                    'area': a,
                    'items': [],
                    'subareas': {
                        sa.id: {'subarea': sa, 'items': [], 'markers': []}
                        for sa in a.subareas.all()
                    },
                }
                for a in collectible_areas
            }
            unsorted_bucket = {'area': None, 'items': []}

            type_progress = {}
            # Roadmap-wide flag aggregates surfaced in the hero subtitle so
            # the banner answers the actual mid-playthrough question: "what
            # do I still need to grab before missing my chance?"
            missable_total = 0
            missable_found = 0
            for ctype in collectible_types:
                items = list(ctype.items.all())
                # Type-level progress is independent of area; counted here
                # for the per-type chips and progress bars.
                if items:
                    found_for_type = 0
                    for item in items:
                        item.is_found = item.id in found_ids
                        # Annotate with the owning type so the row partial
                        # can render the swatch/icon without the template
                        # having to chase another lookup.
                        item.type_id_ref = ctype.id
                        item.type_slug_ref = ctype.slug
                        item.type_name_ref = ctype.name
                        item.type_color_ref = ctype.color or 'primary'
                        item.type_icon_ref = ctype.icon or '🎯'
                        if item.is_found:
                            found_for_type += 1
                        if item.is_missable:
                            missable_total += 1
                            if item.is_found:
                                missable_found += 1
                        bucket = area_buckets.get(item.area_id) if item.area_id else unsorted_bucket
                        if bucket is None:
                            # area_id refers to a deleted area (shouldn't happen
                            # post-merge; SET_NULL keeps refs clean — but safe).
                            bucket = unsorted_bucket
                        # Sub-area routing: if the item points at a sub-area
                        # that still belongs to this bucket's area, drop it
                        # into the sub-area's bucket; otherwise it's loose
                        # at the area level (the existing behavior).
                        sa_id = getattr(item, 'subarea_id', None)
                        sub_buckets = bucket.get('subareas') or {}
                        if sa_id and sa_id in sub_buckets:
                            sub_buckets[sa_id]['items'].append(item)
                        else:
                            bucket['items'].append(item)
                    type_progress[ctype.id] = {
                        'found': found_for_type,
                        'total': ctype.total_count if ctype.total_count else len(items),
                        'item_total': len(items),
                    }
                else:
                    # Type with no items: still surface in the per-type
                    # progress strip + chip filter, but with zero progress.
                    type_progress[ctype.id] = {
                        'found': 0,
                        'total': ctype.total_count or 0,
                        'item_total': 0,
                    }

            # Sort each bucket's items (loose + per-sub-area) by `order`
            # for playthrough sequence.
            for bucket in list(area_buckets.values()) + [unsorted_bucket]:
                bucket['items'].sort(key=lambda it: it.order)
                for sub in (bucket.get('subareas') or {}).values():
                    sub['items'].sort(key=lambda it: it.order)

            # Trophy nav markers per area — share the area's `order` space
            # with items so they interleave in the playthrough sequence.
            # Markers with subarea_id route into the sub-area's bucket;
            # otherwise they stay loose at the area level. Unsorted bucket
            # has no markers (markers anchor to a real area).
            for area_obj in collectible_areas:
                bucket = area_buckets[area_obj.id]
                bucket['markers'] = []
                sub_buckets = bucket.get('subareas') or {}
                for marker in area_obj.markers.all():
                    if marker.subarea_id and marker.subarea_id in sub_buckets:
                        sub_buckets[marker.subarea_id]['markers'].append(marker)
                    else:
                        bucket['markers'].append(marker)

            # Materialize ordered area list, dropping empty ones. Each
            # bucket gets `is_complete` so the template can render an
            # already-finished chapter pre-collapsed (no payoff in showing
            # a 5/5 list expanded by default on a return visit).
            #
            # `entries` is the unified items+markers sequence the template
            # iterates; each entry is tagged with `entry_kind` so the
            # template can pick the right partial. Completion is computed
            # from items only — markers are pure navigation.
            def _build_entries(items, markers):
                tagged = []
                for it in items:
                    it.entry_kind = 'item'
                    tagged.append((it.order, 0, it))  # 0 = items sort first on tie
                for m in markers:
                    m.entry_kind = 'marker'
                    tagged.append((m.order, 1, m))    # 1 = markers sort after
                tagged.sort(key=lambda x: (x[0], x[1]))
                return [t[2] for t in tagged]

            def _build_subarea_buckets(area_obj, bucket):
                """Materialize the area's sub-area sections in `order`.

                Each entry: {'key', 'subarea', 'entries', 'is_complete'}.
                Empty sub-areas (no items, no markers) are dropped from
                the render — sub-areas without any content add no value
                to the reader and would waste vertical space.
                """
                sub_buckets = bucket.get('subareas') or {}
                if not sub_buckets:
                    return []
                ordered = sorted(
                    sub_buckets.values(),
                    key=lambda b: (b['subarea'].order, b['subarea'].id),
                )
                out = []
                for sb in ordered:
                    items = sb['items']
                    markers = sb['markers']
                    if not items and not markers:
                        continue
                    out.append({
                        'key': sb['subarea'].slug,
                        'subarea': sb['subarea'],
                        'entries': _build_entries(items, markers),
                        'is_complete': bool(items) and all(it.is_found for it in items),
                    })
                return out

            tracker_areas = []
            for a in collectible_areas:
                bucket = area_buckets[a.id]
                subarea_sections = _build_subarea_buckets(a, bucket)
                # Render the area if it has loose items OR any populated
                # sub-area. (An area can be "empty at the loose level"
                # but still meaningful via its sub-areas.)
                if bucket['items'] or subarea_sections:
                    # Completion sums items across loose + every sub-area.
                    all_items_in_area = list(bucket['items'])
                    for sb in (bucket.get('subareas') or {}).values():
                        all_items_in_area.extend(sb['items'])
                    tracker_areas.append({
                        'key': a.slug,
                        'area': a,
                        'entries': _build_entries(bucket['items'], bucket.get('markers') or []),
                        'subareas': subarea_sections,
                        'is_complete': (
                            bool(all_items_in_area)
                            and all(it.is_found for it in all_items_in_area)
                        ),
                    })
            if unsorted_bucket['items']:
                tracker_areas.append({
                    'key': UNSORTED_KEY,
                    'area': None,
                    # Unsorted has no markers (no real area to anchor to);
                    # emit items-only entries to keep the template uniform.
                    'entries': _build_entries(unsorted_bucket['items'], []),
                    'is_complete': all(it.is_found for it in unsorted_bucket['items']),
                })

            # Aggregate progress counters across the whole roadmap. Sum
            # only types with items (empty types don't change the totals).
            total_items = sum(tp['item_total'] for tp in type_progress.values())
            total_found = sum(tp['found'] for tp in type_progress.values())

            context['has_collectibles'] = True
            context['collectible_types_list'] = collectible_types
            context['collectible_tracker_areas'] = tracker_areas
            context['collectible_type_progress'] = type_progress
            context['collectible_total_items'] = total_items
            context['collectible_total_found'] = total_found
            context['collectible_missable_total'] = missable_total
            context['collectible_missable_found'] = missable_found
            context['collectible_missable_remaining'] = missable_total - missable_found
        else:
            context['has_collectibles'] = False

        # DLC navigation strip: enumerate roadmaps under this concept.
        # Public sees only published; authors in preview mode see drafts too.
        context['available_ctgs'] = RoadmapService.get_available_ctgs(
            concept, include_drafts=preview_mode,
        )

        # Resolve trophy display data for trophies referenced in the
        # roadmap's steps + trophy guides + collectible-area trophy markers.
        # Use `trophy_guides_for_display()` so the platinum's id is in
        # the lookup set even if no real TrophyGuide row exists for it
        # (the method injects an unsaved placeholder for the platinum).
        # Without this, the platinum's placeholder card would fall
        # through to the "Trophy #N" fallback in the template.
        roadmap_trophy_ids = set()
        for step in roadmap.steps.all():
            for st in step.step_trophies.all():
                roadmap_trophy_ids.add(st.trophy_id)
        for tg in roadmap.trophy_guides_for_display():
            roadmap_trophy_ids.add(tg.trophy_id)
        for area_obj in roadmap.collectible_areas.all():
            for marker in area_obj.markers.all():
                roadmap_trophy_ids.add(marker.trophy_id)

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

        # Trophy nav marker filter metadata: which trophies are tagged
        # missable on this roadmap (author-set via TrophyGuide.is_missable),
        # and whether the roadmap itself is a DLC CTG (group != 'default'
        # means everything in it is DLC). The marker template uses these
        # to stamp `data-missable` / `data-dlc` so the Missable / DLC
        # filter toggles in the reader can act on markers just like they
        # do on collectible items.
        # Iterate the already-prefetched guide list (which also reflects
        # `apply_branch_overlay`'s in-memory edits during ?preview=true).
        # `.filter()` would bypass the prefetch cache, and when overlay has
        # replaced it with a list the manager call blows up entirely.
        # Uses `trophy_guides_for_display` for consistency with the
        # template — the platinum placeholder it injects has all flags
        # off so it doesn't pollute these sets.
        all_guides = list(roadmap.trophy_guides_for_display())
        context['missable_trophy_ids'] = {
            tg.trophy_id for tg in all_guides if tg.is_missable
        }
        # Parallel sets for the other author-set TrophyGuide flags so the
        # marker template can render attention-grabbing pills inline.
        # Online and unobtainable trophies don't drive the existing filter
        # toggles, but readers care about them mid-playthrough just as
        # much as missables (online may need servers up; unobtainable
        # may need a workaround or version-pinning).
        context['online_trophy_ids'] = {
            tg.trophy_id for tg in all_guides if tg.is_online
        }
        context['unobtainable_trophy_ids'] = {
            tg.trophy_id for tg in all_guides if tg.is_unobtainable
        }
        context['is_dlc_roadmap'] = trophy_group_id != 'default'

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

        # Header background: the assembled landscape image (IGDB screenshots -> artworks -> PSN
        # bg_url fallback), not raw bg_url.
        context['header_bg_url'] = (concept.get_landscape_url() if concept else None) or ''

        # Breadcrumbs.
        group_name = roadmap.concept_trophy_group.display_name
        context['breadcrumb'] = [
            {'text': 'Games', 'url': '/games/'},
            {'text': game.title_name, 'url': f'/games/{game.np_communication_id}/'},
            {'text': f'Roadmap: {group_name}'},
        ]

        # SEO. Title aims for under ~60 chars (Google's typical truncation
        # point) and leads with the game name + "Trophy Guide" — the
        # high-intent keyword pair search readers use. The roadmap's CTG
        # display name is appended only when it's a DLC (the default
        # "Base Game" label adds noise and pushes us over budget).
        is_dlc = group_name and group_name.lower() != 'base game'
        if is_dlc:
            context['seo_title'] = f"{game.title_name}: {group_name} Trophy Guide"
        else:
            context['seo_title'] = f"{game.title_name} Trophy Guide & Roadmap"

        step_count = len(roadmap.steps.all())
        guide_count = len(roadmap.trophy_guides.all())
        difficulty = getattr(roadmap, 'difficulty', None) or ''
        estimated_hours = getattr(roadmap, 'estimated_hours', None)
        playthroughs = getattr(roadmap, 'min_playthroughs', None)

        # Meta description packs the high-intent signals trophy hunters
        # actually search for — difficulty, time-to-platinum, playthrough
        # count — into Google's ~155-char budget. Format is conversational
        # ("Platinum X with our step-by-step guide…") rather than a
        # fact-dump to read better as a search snippet.
        desc_parts = [
            f"Platinum {game.title_name}"
        ]
        if is_dlc:
            desc_parts.append(f" ({group_name})")
        desc_parts.append(f" with our step-by-step trophy roadmap.")
        if step_count:
            desc_parts.append(f" {step_count} steps")
            if guide_count:
                desc_parts.append(f", {guide_count} per-trophy guides")
            desc_parts.append('.')
        meta_extras = []
        if difficulty:
            meta_extras.append(f"Difficulty: {difficulty}")
        if estimated_hours:
            meta_extras.append(f"~{int(estimated_hours)} hours")
        if playthroughs and playthroughs > 1:
            meta_extras.append(f"{playthroughs} playthroughs")
        if meta_extras:
            desc_parts.append(' ' + ' · '.join(meta_extras) + '.')
        context['seo_description'] = ''.join(desc_parts)[:300]  # cap defensively; Google reads ~155

        # Absolute URL for the game cover so OG/Twitter previews show
        # the right artwork instead of the site logo fallback. Falls
        # back to '' so the base.html block keeps its logo default.
        cover_url = getattr(game, 'display_image_url', '') or ''
        if cover_url and not cover_url.startswith('http'):
            cover_url = f"{self.request.scheme}://{self.request.get_host()}{cover_url}"
        context['seo_image'] = cover_url

        # Contributor display-names for the HowTo schema's `author`
        # field. Mirrors the visible attribution above the steps so
        # the structured data matches what the reader sees. NOTE:
        # `contributors` is a *method* (not a property); calling it
        # via `roadmap.contributors` returns the bound method (truthy),
        # which the old broken `for c in roadmap.contributors` was
        # silently swallowing via the try/except. Result was an empty
        # `roadmap_contributors` for every roadmap.
        contributor_names = []
        try:
            for c in (roadmap.contributors() or []):
                name = getattr(c, 'display_psn_username', None) or getattr(c, 'psn_username', None)
                if name:
                    contributor_names.append(name)
        except (AttributeError, TypeError):
            pass
        context['roadmap_contributors'] = contributor_names

        # Concept for the JSON-LD `about` (VideoGame reference).
        context['concept'] = getattr(roadmap.concept_trophy_group, 'concept', None)

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

    def get_roadmap_for_permission(self):
        """Hook for RoadmapAuthorRequiredMixin's trial-writer escalation.

        Resolves the roadmap WITHOUT creating it. If no roadmap exists
        yet for this CTG, returns None and the mixin denies access —
        we don't want a trial user URL-spamming `/edit/` to silently
        create empty Roadmap rows for CTGs they aren't assigned to.

        Regular writers / editors / publishers pass the global-role
        check upstream, so this hook never fires for them; their
        get() path still does the get-or-create as designed (a writer
        opening the editor for a fresh CTG seeds a new draft).

        Caches both the game and the roadmap on the instance so the
        later `get()` doesn't re-query.
        """
        game = self.get_object()
        if not game.concept:
            return None
        roadmap = RoadmapService.get_roadmap_for_preview(
            game.concept, self._trophy_group_id(),
        )
        if roadmap is None:
            return None
        # Cache so get() can skip re-fetching.
        self.object = game
        self._cached_roadmap = roadmap
        return roadmap

    def get(self, request, *args, **kwargs):
        # Defense in depth: even though templates hide the Edit button on
        # published roadmaps for non-publishers, anyone with the URL can
        # hit this view. Block at the top of GET before rendering.
        # `_cached_roadmap` may already be set by the permission hook
        # (trial-writer slow path) — skip the re-fetch when it is.
        if getattr(self, '_cached_roadmap', None) is None:
            self.object = self.get_object()
            roadmap = RoadmapService.get_roadmap_for_editor(
                self.object.concept, self._trophy_group_id(),
            )
            if roadmap is None:
                raise Http404("Trophy group not found.")
        else:
            roadmap = self._cached_roadmap
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

        # Track collectible authors so the editor JS can render owner
        # badges for them. The collectibles payload itself comes from
        # `branch_payload` (via RoadmapEditLock) not roadmap_data, but the
        # badge lookup goes through `profilesById` built from
        # `referenced_profile_ids`. Without these tracks, the set-owner
        # badge falls into applyOwnership's `if (!profile)` branch and
        # stays hidden — writers see the section locked but no
        # attribution. Walk the same nested shape that
        # roadmap_service.build_branch_payload serializes.
        for ct in roadmap.collectible_types.all():
            _track(ct.created_by_id)
            _track(ct.last_edited_by_id)
            for item in ct.items.all():
                _track(item.created_by_id)
                _track(item.last_edited_by_id)
        for area in roadmap.collectible_areas.all():
            _track(area.created_by_id)
            _track(area.last_edited_by_id)
            for marker in area.markers.all():
                _track(marker.created_by_id)
                _track(marker.last_edited_by_id)
            for sa in area.subareas.all():
                _track(sa.created_by_id)
                _track(sa.last_edited_by_id)

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
