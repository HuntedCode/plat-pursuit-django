"""The concept-level Game page: /games/<igdb_id>/ (Games/Trophy Lists IA slice 1).

The page renders the WORK -- one page per IGDB id, wrapping every trophy list that resolves to it
-- with concept-oriented tabs around a switchable list viewport. Decision record:
docs/design/games-and-trophy-lists-ia.md; approved plan: cheerful-snacking-wozniak.

Resolution mirrors the tested `_build_other_versions` semantics deliberately: the list set is
"every Game whose concept's IGDB match carries this id", with a same-concept fallback for the
unmatched tail (PP_* stubs, PSN-only concepts) at /games/c/<concept_id>/ -- which 301s to the igdb
URL the moment the concept graduates to a trusted match. Deliberately-split concepts (lists split
out when trophy counts diverge) therefore share one page, per the owner's call. NOTE the matching
carries no trusted-status gate, same as _build_other_versions: trusted-ness gates which URL is the
page (Concept.game_page_url), not membership.
"""
import logging

from django.db.models import Case, IntegerField, Prefetch, Value, When
from django.http import Http404, HttpResponsePermanentRedirect
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.views.generic import TemplateView

from trophies.models import Concept, ConceptFranchise, EarnedTrophy, Game, ProfileGame
from trophies.util_modules.constants import PLATFORM_PRIORITY_ORDER
from trophies.views.concept_context import ConceptContextMixin
from trophies.views.game_views import (
    build_earned_state,
    build_trophy_groups,
    build_trophy_rows,
    compute_group_pct,
    group_trophy_rows,
)

logger = logging.getLogger("psn_api")

_EMPTY_EARNED_STATE = {
    'profile_earned': {}, 'profile_trophy_totals': {}, 'profile_group_totals': {},
}


def _platform_priority_case():
    """DB-side PLATFORM_PRIORITY_ORDER rank -- this ordering IS the default-list rule, so it must
    be deterministic and identical for every viewer (the host concept is elected from it)."""
    return Case(
        *[When(title_platform__contains=plat, then=Value(idx))
          for idx, plat in enumerate(PLATFORM_PRIORITY_ORDER)],
        default=Value(999), output_field=IntegerField(),
    )


class GamePageView(ConceptContextMixin, TemplateView):
    """Anon renders cheap (zero per-user queries -- pinned by test); the concept tabs are stable
    across viewers because the HOST concept comes from the platform-priority election, never from
    the viewer-personalized default list."""

    template_name = 'trophies/game_page.html'

    def get(self, request, *args, **kwargs):
        resolved = self._resolve(kwargs)
        if isinstance(resolved, HttpResponsePermanentRedirect):
            return resolved
        self.list_set = resolved
        if not self.list_set:
            raise Http404('No trophy lists resolve to this game')
        return super().get(request, *args, **kwargs)

    def _resolve(self, kwargs):
        base = (
            Game.objects
            # Mirror the sitemap's floor: np_communication_id is nullable/blankable, a blank row
            # NoReverseMatch-500s the identity chip and a null one mints an unactivatable
            # ?list=None chip.
            .filter(np_communication_id__isnull=False)
            .exclude(np_communication_id='')
            .select_related('concept', 'concept__igdb_match')
            .defer(
                # The ~30KB IGDB blob; the house rule for every queryset joining igdb_match.
                'concept__igdb_match__raw_response',
            )
            .prefetch_related(
                # The About/Ratings tabs walk these off the HOST game's concept; the set is a
                # handful of rows, so prefetching for all of them is cheaper than special-casing.
                'concept__concept_companies__company',
                'concept__concept_genres__genre',
                'concept__concept_themes__theme',
                'concept__concept_engines__engine',
                Prefetch(
                    'concept__concept_franchises',
                    queryset=ConceptFranchise.objects.select_related('franchise').order_by(
                        'franchise__name',
                    ),
                ),
            )
            .annotate(_platform_order=_platform_priority_case())
            .order_by('_platform_order', '-played_count', 'np_communication_id')
        )
        igdb_id = kwargs.get('igdb_id')
        if igdb_id is not None:
            return list(base.filter(concept__igdb_match__igdb_id=igdb_id))

        concept = get_object_or_404(Concept, concept_id=kwargs['concept_id'])
        match = getattr(concept, 'igdb_match', None)
        if match is not None and match.is_trusted and match.igdb_id is not None:
            # Graduation: the concept now has a trusted match, so its page IS the igdb page.
            url = reverse('game_page', kwargs={'igdb_id': match.igdb_id})
            # Forward ONLY ?list= -- the one param the target reads. Reflecting the raw query
            # string mints an unbounded family of 301s onto one page for crawlers to chew.
            wanted = self.request.GET.get('list', '')
            return HttpResponsePermanentRedirect(f'{url}?list={wanted}' if wanted else url)
        return list(base.filter(concept=concept))

    # ── viewer ───────────────────────────────────────────────────────────────────────────────────

    def _viewer_profile(self):
        user = getattr(self.request, 'user', None)
        profile = getattr(user, 'profile', None) if (user is not None and user.is_authenticated) else None
        return profile if (profile is not None and profile.is_linked) else None

    def _viewer_maps(self, viewer):
        """(progress by game pk, plat'd game pks) over the list set -- two bounded queries.
        NOT the whole per-user cost of the page: an authed viewer also pays build_earned_state
        (3 queries) and, via the inherited ratings builder, several queries PER ConceptTrophyGroup
        (can_rate + own-rating lookups) -- tens of queries on a DLC-heavy game, inherited unchanged
        from GameDetailView. Anonymous pays none of it (pinned)."""
        if viewer is None:
            return {}, set()
        game_ids = [g.pk for g in self.list_set]
        progress = dict(
            ProfileGame.objects
            .filter(profile=viewer, game_id__in=game_ids)
            .values_list('game_id', 'progress')
        )
        plats = set(
            EarnedTrophy.objects
            .filter(profile=viewer, earned=True, trophy__trophy_type='platinum',
                    trophy__game_id__in=game_ids)
            .values_list('trophy__game_id', flat=True)
        )
        return progress, plats

    def _host_game(self):
        """The list whose concept supplies the page's identity (title, canonical, About, ratings).

        MEMBERSHIP is deliberately trust-ungated (the decision record); the HOST is not: an
        untrusted -- or admin-REJECTED -- match whose list wins platform priority would otherwise
        title the page, and its game_page_url would point the canonical at a subset c/ page while
        the sitemap advertises the igdb URL (the audit's H2). So: first list, in the deterministic
        platform order, whose concept holds a trusted match; else first list. On the c/ route every
        list shares the requested concept, so this degrades to list_set[0].
        """
        for g in self.list_set:
            match = getattr(g.concept, 'igdb_match', None) if g.concept_id else None
            if match is not None and match.is_trusted:
                return g
        return self.list_set[0]

    def _default_list(self, progress):
        """The decided rule: the viewer's list when they have progress on EXACTLY one stack
        (progress > 0 -- a finished list still counts as theirs); otherwise platform-priority
        first, which is also the anonymous answer."""
        started = [g for g in self.list_set if (progress.get(g.pk) or 0) > 0]
        if len(started) == 1:
            return started[0]
        return self.list_set[0]

    def _selected_list(self, progress):
        """?list=<np_communication_id>, validated against the set; unknown or missing falls back
        to the default with no redirect."""
        wanted = self.request.GET.get('list')
        if wanted:
            for g in self.list_set:
                if g.np_communication_id == wanted:
                    return g
        return self._default_list(progress)

    # ── rendering ────────────────────────────────────────────────────────────────────────────────

    def _is_viewport_swap(self):
        return getattr(self.request, 'htmx', False) and self.request.htmx.target == 'gp-viewport'

    def get_template_names(self):
        if self._is_viewport_swap():
            return ['trophies/partials/game_page/list_viewport.html']
        return [self.template_name]

    def _build_viewport_context(self, selected, viewer, names):
        """Everything the viewport partial renders for ONE list: the shared grid's contract params
        plus the identity chip. This is the ENTIRE htmx branch -- a list switch prices at exactly
        this, none of the concept furniture."""
        rows, has_trophies = build_trophy_rows(selected)
        groups = build_trophy_groups(selected)
        state = build_earned_state(selected, viewer)[0] if viewer else _EMPTY_EARNED_STATE
        return {
            'selected_game': selected,
            'selected_list_name': names[selected.np_communication_id],
            'vp_trophies': group_trophy_rows(rows),
            'vp_groups': groups,
            'vp_earned': state['profile_earned'],
            'vp_group_pct': compute_group_pct(groups, state['profile_group_totals']) if viewer else {},
            'vp_group_totals': state['profile_group_totals'],
            'vp_profile': viewer,
            'vp_show_group_nav': selected.has_trophy_groups,
            'trophies_syncing': not has_trophies,
        }

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        viewer = self._viewer_profile()
        progress, plats = self._viewer_maps(viewer)
        selected = self._selected_list(progress)

        if self._is_viewport_swap():
            # The swap branch renders the viewport partial alone; names for one list only.
            names = Game.display_list_names([selected])
            context.update(self._build_viewport_context(selected, viewer, names))
            return context

        host = self._host_game()
        default = self._default_list(progress)
        names = Game.display_list_names(self.list_set)

        # Hero chips: the platform UNION across the set, in priority order -- per-list platforms
        # live on the switcher chips; the hero describes the WORK.
        seen = {p for g in self.list_set for p in (g.title_platform or [])}
        context['all_platforms'] = [p for p in PLATFORM_PRIORITY_ORDER if p in seen] + sorted(
            p for p in seen if p not in PLATFORM_PRIORITY_ORDER
        )
        context['host_game'] = host
        # The concept partials (ratings/about/versions) read `game` -- the HOST game, elected
        # anonymously, so the concept furniture never varies by viewer.
        context['game'] = host
        context['concept'] = host.concept
        context['list_set'] = self.list_set
        context['selected_game'] = selected
        context['default_list_np'] = default.np_communication_id
        context['viewer_profile'] = viewer
        context['switcher_entries'] = [
            {
                'game': g,
                'name': names[g.np_communication_id],
                'progress': progress.get(g.pk),
                'has_plat': g.pk in plats,
                'is_selected': g.pk == selected.pk,
            }
            for g in self.list_set
        ]

        # Concept furniture off the host (the lifted mixin builders).
        context['image_urls'] = self._build_images_context(host)
        context.update(self._build_concept_context(host))
        if host.concept_id:
            context['about_ttb'] = self._build_about_ttb(
                getattr(host.concept, 'igdb_match', None), None,
            )
        context.update(self._build_pursuit_context(host, viewer))

        # The Community band must describe the GAME, not the host list, on a page whose header
        # just said "N trophy lists": sum the per-list denorms, played-weighted for the average.
        played = [g.played_count or 0 for g in self.list_set]
        total_played = sum(played)
        context['community_stats'] = {
            'played_count': total_played,
            'plats_earned_count': sum(g.plats_earned_count or 0 for g in self.list_set),
            'full_completion_count': sum(g.full_completion_count or 0 for g in self.list_set),
            'avg_completion': (
                round(sum((g.avg_completion or 0) * w for g, w in zip(self.list_set, played)) / total_played)
                if total_played else (self.list_set[0].avg_completion or 0)
            ),
        }
        # Slice 1: the reused Ratings/About tabs are READ-ONLY here -- the quick-rate modal, its
        # JS and the flag/report modal live on List detail. The flag gates their CTAs so the page
        # never renders an invitation with no button (audit A3/M4).
        context['concept_tabs_readonly'] = True
        # The About tab's versions sections are redundant on THIS page: "Other platforms" is the
        # switcher's own list set, "In the same family" is the hero's family band (Jeffrey's call).
        context['about_hide_versions'] = True

        context.update(self._build_viewport_context(selected, viewer, names))

        # One absolute canonical, computed once: the template's rel=canonical, og:url and the
        # jsonld VideoGame node all read this single value, so they cannot disagree.
        context['page_canonical_url'] = (
            f"{self.request.scheme}://{self.request.get_host()}{host.concept.game_page_url()}"
        )

        title = host.concept.unified_title if host.concept_id and host.concept.unified_title else host.title_name
        context['page_title'] = title
        context['breadcrumb'] = [
            {'text': 'Home', 'url': reverse('home')},
            {'text': 'Games', 'url': reverse('games_list')},
            {'text': title},
        ]
        context['seo_description'] = (
            f"{title} trophies on Platinum Pursuit. "
            f"{len(self.list_set)} trophy list{'s' if len(self.list_set) != 1 else ''} tracked "
            f"across {', '.join(sorted({p for g in self.list_set for p in (g.title_platform or [])})) or 'PlayStation'}."
        )
        return context
