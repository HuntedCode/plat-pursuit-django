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
from dataclasses import dataclass

from django.db.models import Case, IntegerField, Prefetch, Value, When
from django.http import Http404, HttpResponsePermanentRedirect
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.views.generic import TemplateView

from core.services import completion_card_service as cards
from trophies.forms import GameDetailForm
from trophies.models import Concept, ConceptFranchise, EarnedTrophy, Game, ProfileGame
from trophies.util_modules.constants import PLATFORM_PRIORITY_ORDER, ordered_platform_union
from trophies.views.concept_context import ConceptContextMixin
from trophies.views.game_views import (
    build_earned_state,
    build_trophy_groups,
    build_trophy_rows,
    compute_group_pct,
    filter_and_sort_rows,
    group_trophy_rows,
)
from trophies.views.hero_context import (
    defined_total,
    has_platinum,
    list_label,
    list_status,
    plat_rarity,
)

logger = logging.getLogger("psn_api")

_EMPTY_EARNED_STATE = {
    'profile_earned': {}, 'profile_trophy_totals': {}, 'profile_group_totals': {}, 'plat_date': None,
}


def disambiguate_entries(entries):
    """Give each switcher entry a `detail` ONLY when its chip would otherwise read the same as a sibling's
    (DS-09: "0000" has six lists all titled "Zero Zero Zero Zero", three of them PSVITA with no region).
    The first tell is the trophy count; when that matches too, the PSN list id, which always differs.
    Entries that are already distinct get no detail, so the common page is unchanged. Mutates in place."""
    groups = {}
    for e in entries:
        g = e['game']
        regions = tuple(g.region or []) if g.is_regional else ()
        groups.setdefault((e['name'], tuple(g.title_platform or []), regions), []).append(e)
    for same in groups.values():
        if len(same) < 2:
            continue
        totals = [defined_total(e['game']) for e in same]
        for e, total in zip(same, totals):
            detail = f"{total} troph{'y' if total == 1 else 'ies'}"
            if totals.count(total) > 1:
                detail = f"{detail} \u00b7 {e['game'].np_communication_id}"
            e['detail'] = detail
    return entries


def _platform_priority_case():
    """DB-side PLATFORM_PRIORITY_ORDER rank -- this ordering IS the default-list rule, so it must
    be deterministic and identical for every viewer (the host concept is elected from it)."""
    return Case(
        *[When(title_platform__contains=plat, then=Value(idx))
          for idx, plat in enumerate(PLATFORM_PRIORITY_ORDER)],
        default=Value(999), output_field=IntegerField(),
    )


@dataclass(frozen=True)
class SharedListFlags:
    """The game flags that hold for EVERY trophy list on a Game page (owner, 2026-10-10).

    Shaped like a `Game` for exactly the attributes `_game_flags.html` reads, so the Game page reuses the Trophy
    List page's partial rather than copying its chips. A flag one list carries and another does not belongs to
    that list (its own page shows it); naming it for the whole game would tell a hunter on the good list that
    their game is broken.
    """
    is_shovelware: bool
    is_delisted: bool
    is_obtainable: bool
    has_online_trophies: bool
    has_buggy_trophies: bool

    @property
    def has_chips(self):
        """Whether the flags row has a chip to draw. Shovelware is left out because it rides the platform row
        instead (as on the Trophy List page). Gating the partial on this is also what keeps its "No Known Issues"
        chip off the Game page: the row only renders when a real flag is shared, so the clean state can never
        show, and a game whose lists disagree is not a game with no known issues."""
        return (self.is_delisted or not self.is_obtainable
                or self.has_online_trophies or self.has_buggy_trophies)


def shared_list_flags(games):
    """`SharedListFlags` for `games` (a page's trophy lists, already in memory), or None for none.

    ALL for every flag, with `is_obtainable` read the other way round: the page says Unobtainable only when NO
    list is obtainable, which is "every list unobtainable". Pure Python over rows the page has already loaded,
    so it costs no query.
    """
    games = list(games)
    if not games:
        return None
    return SharedListFlags(
        is_shovelware=all(g.is_shovelware for g in games),
        is_delisted=all(g.is_delisted for g in games),
        is_obtainable=any(g.is_obtainable for g in games),
        has_online_trophies=all(g.has_online_trophies for g in games),
        has_buggy_trophies=all(g.has_buggy_trophies for g in games),
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
        """(progress by game pk, {plat'd game pk: when it popped}, play duration, earned trophies by
        game pk) over the list set -- two bounded queries. The viewer's playtime rides the SAME ProfileGame query (one extra column,
        zero extra queries): the MAX play_duration across the set's rows, because a PS4+PS5
        auto-pop would double-count a sum. Returned RAW (a timedelta) -- the caller derives the
        quick-rate modal's whole-hours hint (>= 1h rounds, else None, List detail's old rule)
        AND feeds the About tab's time-to-beat "You" row, which wants the duration itself.
        NOT the whole per-user cost of the page: an authed viewer also pays build_earned_state
        (3 queries) and, via the inherited ratings builder, several queries PER ConceptTrophyGroup
        (can_rate + own-rating lookups) -- tens of queries on a DLC-heavy game, inherited unchanged
        from GameDetailView. Anonymous pays none of it (pinned)."""
        if viewer is None:
            return {}, {}, None, {}
        game_ids = [g.pk for g in self.list_set]
        rows = list(
            ProfileGame.objects
            .filter(profile=viewer, game_id__in=game_ids)
            .values_list('game_id', 'progress', 'play_duration', 'earned_trophies_count')
        )
        progress = {game_id: prog for game_id, prog, _dur, _n in rows}
        earned_counts = {game_id: n for game_id, _prog, _dur, n in rows}
        durations = [dur for _gid, _prog, dur, _n in rows if dur is not None]
        play_duration = max(durations, default=None)
        # {game pk: when the platinum popped} -- the date the status card names, on the same query.
        plats = dict(
            EarnedTrophy.objects
            .filter(profile=viewer, earned=True, trophy__trophy_type='platinum',
                    trophy__game_id__in=game_ids)
            .values_list('trophy__game_id', 'earned_date_time')
        )
        return progress, plats, play_duration, earned_counts

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

    def _hunter_status(self, viewer, progress, plats, earned_counts):
        """Where the signed-in hunter stands on this game (DS-07), from their BEST list (owner,
        2026-10-22): a platinum on any list wins, else their highest percentage; ties go to the page's
        platform order. It describes the game, so it does not follow the list switcher.

        None signed out. Otherwise a dict the hero's status card reads:
          state      'plat' | 'complete' (100% on a list with no platinum, or a platinum not synced yet)
                     | 'started' | 'owned' (in the library, nothing earned) | 'none' (not owned)
          game       the list it describes (absent for 'none')
          list_label its platforms, only when the page has more than one list
          progress, earned, total, to_go, plat_date
          share      the share-card link once that list is finished (completion_card_service.share_link)
        The per-list part is hero_context.list_status, which the Trophy List page's card reads too.
        Zero queries beyond `_viewer_maps`, except the one share-link seek on a finished list."""
        if viewer is None:
            return None
        owned = [g for g in self.list_set if g.pk in progress]
        if not owned:
            return {'state': 'none'}
        platted = [g for g in owned if g.pk in plats]
        pool = platted or owned
        best = max(pool, key=lambda g: progress.get(g.pk) or 0)   # max() keeps the first of a tie
        pct = progress.get(best.pk) or 0
        return list_status(
            best, pct, earned_counts.get(best.pk),
            platted=bool(platted), plat_date=plats.get(best.pk),
            share=cards.share_link(viewer, best, pct) if (platted or pct == 100) else None,
            label=list_label(best) if len(self.list_set) > 1 else '',
        )

    def _plat_rarity(self, status):
        """The hero's platinum stat (hero_context.plat_rarity) for the list it should describe: the one the
        status card describes, else the one most hunters own. Lists with a platinum come first; with none on
        any list, the choice is among the lists whose trophies are known, and None when none are (an unsynced
        list is not a list without a platinum). At most one indexed query."""
        mine = status.get('game') if status else None
        pool = ([g for g in self.list_set if has_platinum(g)]
                or [g for g in self.list_set if defined_total(g)])
        if not pool:
            return None
        chosen = mine if mine in pool else max(pool, key=lambda g: g.played_count or 0)
        return plat_rarity(chosen, list_label(chosen) if len(self.list_set) > 1 else '')

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

    def _build_viewport_context(self, selected, viewer):
        """Everything the viewport partial renders for ONE list: the shared grid's contract params, filtered
        and sorted by the control row (GL-10, the Trophy List page's own filter). This is the ENTIRE htmx
        branch -- a list switch or a filter change prices at exactly this, none of the concept furniture."""
        rows, has_trophies = build_trophy_rows(selected)
        groups = build_trophy_groups(selected)
        state = build_earned_state(selected, viewer)[0] if viewer else _EMPTY_EARNED_STATE
        form = GameDetailForm(self.request.GET)
        rows = filter_and_sort_rows(rows, form, state['profile_earned'])
        return {
            'selected_game': selected,
            'vp_form': form,
            'selected_trophy_types': self.request.GET.getlist('trophy_type'),
            'selected_rarity_brackets': self.request.GET.getlist('rarity_bracket'),
            'vp_shown_count': len(rows),
            'vp_list_url': reverse('game_detail', kwargs={'np_communication_id': selected.np_communication_id}),
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
        progress, plats, play_duration, earned_counts = self._viewer_maps(viewer)
        selected = self._selected_list(progress)

        if self._is_viewport_swap():
            # The swap branch renders the viewport partial alone.
            context.update(self._build_viewport_context(selected, viewer))
            return context

        host = self._host_game()
        default = self._default_list(progress)
        names = Game.display_list_names(self.list_set)

        # Hero chips: the platform UNION across the set, in display order -- per-list platforms
        # live on the switcher chips; the hero describes the WORK. The shared util is also what
        # the condensed Browse card uses, so the two surfaces cannot disagree.
        context['all_platforms'] = ordered_platform_union(g.title_platform for g in self.list_set)
        context['list_flags'] = shared_list_flags(self.list_set)
        context['host_game'] = host
        # The concept partials (ratings/about/versions) read `game` -- the HOST game, elected
        # anonymously, so the concept furniture never varies by viewer.
        context['game'] = host
        context['concept'] = host.concept
        context['list_set'] = self.list_set
        context['selected_game'] = selected
        context['default_list_np'] = default.np_communication_id
        # ?view= is honored SERVER-side too, so the hero's jump anchors work without JS (the
        # audit found the no-JS href landed on a page whose target panel was rendered hidden).
        # game-page.js re-applies the same view idempotently at init; unknown values fall back.
        view_param = self.request.GET.get('view')
        context['initial_view'] = view_param if view_param in ('lists', 'ratings', 'about') else 'lists'
        context['viewer_profile'] = viewer
        # The bulk flag endpoint's per-request cap, handed to the template so the report modal can
        # stop a reader BEFORE a doomed round trip. A concept with more lists than this is rare, but
        # without it "All" then "Submit" is an opaque 400 with nothing having said there was a limit.
        from trophies.services.game_flag_service import GameFlagService

        context['flag_max_versions'] = GameFlagService.MAX_BULK_VERSIONS
        context['switcher_entries'] = disambiguate_entries([
            {
                'game': g,
                'name': names[g.np_communication_id],
                'progress': progress.get(g.pk),
                'has_plat': g.pk in plats,
                'is_selected': g.pk == selected.pk,
            }
            for g in self.list_set
        ])

        # Concept furniture off the host (the lifted mixin builders).
        context['image_urls'] = self._build_images_context(host)
        context.update(self._build_concept_context(host))
        if host.concept_id:
            # The viewer's playtime joins the TTB scale (the "You" row) -- parity with what List
            # detail's About tab showed before the slim-down moved the tab here.
            context['about_ttb'] = self._build_about_ttb(
                getattr(host.concept, 'igdb_match', None), play_duration,
            )
        context.update(self._build_pursuit_context(host, viewer))
        context['hunter_status'] = self._hunter_status(viewer, progress, plats, earned_counts)
        # The facts line names ONE developer (the first credited); the full credits are in About. Off the
        # prefetched concept_companies, so no query.
        context['hero_developer'] = next(
            (cc.company for cc in host.concept.concept_companies.all() if cc.is_developer), None,
        ) if host.concept_id else None
        context['plat_rarity'] = self._plat_rarity(context['hunter_status'])
        # The signed-out invitation's wording (GL-09): "chasing this platinum" only when a list has one.
        context['invite_platinum'] = any(has_platinum(g) for g in self.list_set)
        # With one list the invitation names what there is to earn, as that list's own page does; with several,
        # it says the site tracks every one.
        context['invite_defined'] = self.list_set[0].defined_trophies if len(self.list_set) == 1 else None

        # The Community band must describe the GAME, not the host list, on a page whose header
        # just said "N trophy lists": per-list denorms summed (platinums and 100%s ARE separate
        # earnings), played-weighted for the average. PLAYERS are people, not earnings: a hunter
        # with the PS4 and PS5 versions is one player, so it is the page's distinct count
        # (Concept.page_player_count, nightly), floored by the biggest live list count (DS-08).
        played = [g.played_count or 0 for g in self.list_set]
        total_played = sum(played)
        context['community_stats'] = {
            'played_count': max(host.page_players, *played),
            'plats_earned_count': sum(g.plats_earned_count or 0 for g in self.list_set),
            'full_completion_count': sum(g.full_completion_count or 0 for g in self.list_set),
            'avg_completion': (
                round(sum((g.avg_completion or 0) * w for g, w in zip(self.list_set, played)) / total_played)
                if total_played else (self.list_set[0].avg_completion or 0)
            ),
        }
        # Phase 2 (slim-down): this page is the ONE active ratings host -- quick-rate, blurb
        # report, guidelines and the flag/report modal all ship here (List detail's copies left
        # with its Ratings/About tabs). The slice-1 concept_tabs_readonly gate retired with them.
        # PSN-tracked playtime as a whole-hours hint in the quick-rate modal; rides _viewer_maps.
        secs = play_duration.total_seconds() if play_duration else 0
        context['user_play_hours'] = round(secs / 3600) if secs >= 3600 else None
        # The About tab leaves out "Other platforms" on IGDB pages only: there it is the switcher's own
        # list set. The family stays (it moved here from the hero, DS-07). On a c/ page both stay -- the
        # audit found other_versions is the sole surface reaching untrusted same-igdb siblings (they are
        # neither switcher lists there nor family, by the exclusion rule).
        context['about_hide_other_platforms'] = self.kwargs.get('igdb_id') is not None

        context.update(self._build_viewport_context(selected, viewer))

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
