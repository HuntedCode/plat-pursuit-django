import logging

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.paginator import Paginator
from django.db.models import Q, F, Count, Subquery, OuterRef
from django.db.models.functions import Lower
from django.http import Http404, HttpResponseRedirect, JsonResponse
from django.shortcuts import redirect, render, get_object_or_404
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views.generic import ListView, DetailView, View, TemplateView
from django_ratelimit.decorators import ratelimit
from urllib.parse import urlencode

from trophies.util_modules.cache import redis_client
from ..forms import (
    ProfileSearchForm,
    ProfileGamesForm,
    LinkPSNForm,
)
from ..models import (
    Profile,
    ProfileGame,
    GameList,
    TrophyGroup,
    UserTitle,
)
from trophies.services.activity_service import DAYS_PER_PAGE
from trophies.mixins import HtmxListMixin
from trophies.psn_manager import PSNManager

logger = logging.getLogger("psn_api")

#: A search box on a public page: bounded so one visitor cannot hand the database an enormous ILIKE.
_TROPHY_QUERY_MAX = 80


class ProfilesListView(HtmxListMixin, ListView):
    """Browse hunters at `/hunters/` -- a DISCOVERY surface, not a ranking one.

    Rebuilt 2026-08. The framing is the load-bearing decision: `/leaderboards/` owns ranking (it already
    boards hunters by badges), so this page is a directory of PEOPLE -- who is here, who is active, who
    just arrived -- and everything that made it a second scoreboard was removed rather than restyled.

    What that cost the sort list, and why each one went:

    - `badges_earned` / `badge_xp` -> Leaderboards. Duplicating that hub's job here is what made the two
      surfaces read as the same page twice.
    - `rarest_avg_plat` -> dropped. A connoisseur ranking, and the only sort with no index behind it: a
      correlated AVG over every EarnedTrophy row per profile, sitewide.
    - `games` / `completes` / `avg_progress` -> dropped. Three more "who is biggest" orderings on a page
      that is no longer about biggest.

    The five that remain (alphabetical, recently active, recently joined, trophies, platinums) are each
    served by an index on Profile, so ordering never costs a scan.

    Card data is deliberately thin -- identity plus two proof stats. `recent_platinum` used to be
    prefetched for every row and is gone: the card is meant to scan, not to brief.
    """
    model = Profile
    template_name = 'trophies/profile_list.html'
    partial_template_name = 'trophies/partials/profile_list/browse_results.html'
    paginate_by = 30

    #: Ordering per sort value. Every one is index-backed; `Lower('psn_username')` is the stable
    #: tie-breaker so equal stats never shuffle between pages of the same result set.
    SORTS = {
        'alpha': [Lower('psn_username')],
        'recently_active': [F('last_synced').desc(nulls_last=True), Lower('psn_username')],
        'recently_joined': ['-created_at', Lower('psn_username')],
        'trophies': ['-total_trophies', Lower('psn_username')],
        'plats': ['-total_plats', Lower('psn_username')],
    }
    DEFAULT_SORT = 'alpha'

    def dispatch(self, request, *args, **kwargs):
        if not request.GET and request.user.is_authenticated:
            defaults = (request.user.browse_defaults or {}).get('profiles', {})
            if defaults:
                return HttpResponseRedirect(
                    reverse('profiles_list') + '?' + urlencode(defaults, doseq=True)
                )
        return super().dispatch(request, *args, **kwargs)

    def get_filter_form(self):
        if not hasattr(self, '_filter_form'):
            self._filter_form = ProfileSearchForm(self.request.GET)
        return self._filter_form

    def get_queryset(self):
        qs = super().get_queryset()
        form = self.get_filter_form()

        # The displayed title, folded into the ROW rather than fetched per card. `Profile.displayed_title`
        # is a method that runs `user_titles.filter(...).first()` and then hops the `title` FK -- two
        # queries per profile, so ~60 on a 30-card page purely to print a word under each name. The
        # subquery is served by `usertitle_display_idx` (profile, is_displayed) and costs no extra round
        # trip. Templates read `display_title`; the method stays for callers rendering ONE profile.
        qs = qs.annotate(
            display_title=Subquery(
                UserTitle.objects
                .filter(profile_id=OuterRef('pk'), is_displayed=True)
                .values('title__name')[:1]
            ),
        )
        # Only what a card draws. Profile is a wide row (sync bookkeeping, JSON summaries, PSN payloads)
        # and none of it is on this page; without this the page pays to haul all of it 30 times.
        qs = qs.only(
            # `country_code` is deliberately absent: the card shows `flag` (which holds the code) and the
            # only other reader was a `title` attribute dropped when the code stopped being aria-hidden.
            # Anything added here must be READ by the card, and anything the card reads must be here --
            # a miss is a per-row deferred fetch, i.e. an N+1 wearing a different hat.
            'psn_username', 'display_psn_username', 'avatar_url', 'flag',
            'trophy_level', 'total_trophies', 'total_plats', 'total_games', 'user_is_premium',
            'display_mark',   # the name-mark partial reads it per card; a miss here is the
                              # exact per-row deferred fetch the comment above warns about
            'last_synced', 'created_at',
        )

        # ONE bad field must not take the others down with it. `is_valid()` is all-or-nothing, and both
        # things a stale `browse_defaults` can carry -- a retired sort, or a country that no longer has any
        # hunters in the choice list -- invalidate the whole form. Gating the filters on it meant a hunter
        # whose saved default named a since-removed sort also silently lost their SEARCH, which is a much
        # stranger failure than landing on the default order. So the filters fall back to the raw params;
        # both are parameterised by the ORM, and an unknown country simply matches nothing.
        if form.is_valid():
            query = form.cleaned_data.get('query') or ''
            country = form.cleaned_data.get('country') or ''
        else:
            query = self.request.GET.get('query', '').strip()
            country = self.request.GET.get('country', '').strip()

        if query:
            qs = qs.filter(Q(psn_username__icontains=query))
        if country:
            qs = qs.filter(country_code=country)

        # Read from the raw param against our own map rather than from `cleaned_data`, for the same
        # reason: an unrecognised sort resolves to the default instead of invalidating anything.
        order = self.SORTS.get(self.request.GET.get('sort'), self.SORTS[self.DEFAULT_SORT])
        return qs.order_by(*order)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # Breadcrumb
        context['breadcrumb'] = [
            {'text': 'Home', 'url': reverse_lazy('home')},
            {'text': 'Hunters'},
        ]

        context['form'] = self.get_filter_form()
        context['selected_country'] = self.request.GET.get('country', '')

        # The active sort, resolved the SAME way the queryset resolves it (raw param against the map, with
        # an unknown value falling back rather than invalidating). The card's third stat follows this, so
        # the wall always shows the axis it is ordered by -- the page defaults to Recently Active, and
        # without this it sorts by something no card displays. Set in `get_context_data` so the HTMX
        # partial gets it too; the grid is what re-renders on a sort change.
        sort = self.request.GET.get('sort')
        context['active_sort'] = sort if sort in self.SORTS else self.DEFAULT_SORT

        # No longer mentions leaderboards: ranking moved to /leaderboards/, and describing this page as a
        # board would set the wrong expectation in a result snippet for what is now a directory.
        context['seo_description'] = (
            "Browse PlayStation trophy hunters on Platinum Pursuit. Find hunters by name or country, "
            "and see who is active."
        )

        return context


class ProfileDetailView(DetailView):
    """
    Display profile detail page with tabbed interface for games, trophies, and badges.

    Shows header stats, trophy case selections, and tab-specific content with
    filtering, sorting, and pagination.
    """
    model = Profile
    template_name = 'trophies/profile_detail.html'
    slug_field = 'psn_username'
    slug_url_kwarg = 'psn_username'
    context_object_name = 'profile'

    def get_object(self, queryset=None):
        psn_username = self.kwargs[self.slug_url_kwarg].lower()
        queryset = queryset or self.get_queryset()
        return get_object_or_404(queryset, **{self.slug_field: psn_username})

    def _build_header_stats(self, profile):
        """The hero's figures. Five denormalized columns off the Profile row -- no queries at all.

        It used to also build four NOTABLE PLATINUMS (recent / rarest / fastest / milestone) for tiles the
        hero rebuild had already retired. Nothing rendered them for weeks while they went on costing three
        queries per profile render -- one of them `[milestone_number - 1]` on an ordered queryset, i.e. an
        OFFSET over the profile's entire earned-platinum set -- plus the select_related chain that fed
        them. Same shape as the timeline that was deleted beside them: a provider outliving its surface,
        invisible precisely because nothing renders it.
        """
        return {
            'total_games': profile.total_games,
            'total_earned_trophies': profile.total_trophies,
            'total_unearned_trophies': profile.total_unearned,
            'total_completions': profile.total_completes,
            'average_completion': profile.avg_progress,
        }


    def _build_games_tab_context(self, profile, per_page, page_number):
        """
        Build context for games tab with filtering and pagination.

        Args:
            profile: Profile instance
            per_page: Items per page
            page_number: Current page number

        Returns:
            dict: Context with profile_games and form
        """
        form = ProfileGamesForm(self.request.GET)
        context = {'trophy_log': []}

        if not form.is_valid():
            context['profile_games'] = []
            context['form'] = form
            return context

        # Get form data
        query = form.cleaned_data.get('query')
        platforms = form.cleaned_data.get('platform')
        status = form.cleaned_data.get('status')
        sort_val = form.cleaned_data.get('sort')

        # Build queryset
        # `game.display_image_url` resolves a trusted IGDB cover FIRST, so rendering a grid of games walks
        # Game -> Concept -> IGDBMatch for every row. With only `game` selected that was two extra queries
        # per card -- measured at 52 Concept + 52 IGDBMatch fetches on a 26-game page, ~104 of the tab's
        # ~115 queries.
        #
        # The `defer` is not optional (project CLAUDE.md): `raw_response` is the ~30 KB IGDB API blob that
        # no cover-art template reads, and hauling it per row is what triggered the May 2026 web-server
        # OOM. Widening the join without deferring it trades a query storm for a memory one.
        games_qs = profile.played_games.all().select_related(
            'game', 'game__concept', 'game__concept__igdb_match',
        ).defer('game__concept__igdb_match__raw_response')

        # Apply profile settings
        if profile.hide_hiddens:
            games_qs = games_qs.exclude(user_hidden=True)
        if profile.hide_zeros:
            games_qs = games_qs.exclude(earned_trophies_count=0)

        # Apply filters
        if query:
            games_qs = games_qs.filter(Q(game__title_name__icontains=query))
        if platforms:
            platform_filter = Q()
            for plat in platforms:
                platform_filter |= Q(game__title_platform__contains=plat)
            games_qs = games_qs.filter(platform_filter)
            context['selected_platforms'] = platforms

        # Status -- ONE control replacing what used to be three plat/100% selects plus a completion
        # range. Those were four fields asking a single question (how far did they get), and the card
        # already answers it with a five-state completion bar; these options ARE those states, so the
        # filter and the card read as one vocabulary.
        if status == 'plat':
            games_qs = games_qs.filter(has_plat=True)
        elif status == 'full':
            games_qs = games_qs.filter(progress=100)
        elif status == 'chase':
            games_qs = games_qs.filter(game__defined_trophies__platinum__gt=0, has_plat=False)
        elif status == 'unfinished':
            games_qs = games_qs.filter(progress__lt=100)

        # The genre/theme pickers, community rating/difficulty/fun ranges, time-to-beat range,
        # shovelware exclude and eight unrendered show_*/hide_* community flags were removed in
        # 2026-08 along with eleven sorts. They were DISCOVERY controls inherited wholesale from
        # Browse Games -- they answer "what should I play next", which is a question about a
        # catalogue, not about somebody's history. Removing them also drops this tab's
        # `annotate_community_ratings()` correlated subqueries and its time-to-beat Case.

        # --- Sort ---
        order = ['-last_updated_datetime']
        if sort_val == 'oldest':
            order = ['last_updated_datetime']
        elif sort_val == 'alpha':
            order = [Lower('game__title_name')]
        elif sort_val == 'completion':
            order = ['-progress', Lower('game__title_name')]
        elif sort_val == 'completion_inv':
            order = ['progress', Lower('game__title_name')]
        elif sort_val == 'earned':
            order = ['-earned_trophies_count', Lower('game__title_name')]

        games_qs = games_qs.order_by(*order)

        # Paginate
        games_paginator = Paginator(games_qs, per_page)
        if int(page_number) > games_paginator.num_pages:
            game_page_obj = []
        else:
            game_page_obj = games_paginator.get_page(page_number)

        context['profile_games'] = game_page_obj
        # DLC pack count per game -- ONE grouped query, bounded to the games actually on this page (not
        # the whole library), mirroring how Browse Games builds the same chip. Trophy groups beyond the
        # base 'default' group are the DLC packs.
        page_game_ids = [pg.game_id for pg in game_page_obj]
        context['dlc_map'] = {
            d['game_id']: d['n']
            for d in (
                TrophyGroup.objects.filter(game_id__in=page_game_ids)
                .exclude(trophy_group_id='default')
                .values('game_id').annotate(n=Count('id'))
            )
        } if page_game_ids else {}
        context['form'] = form
        # Options come from the form's constants rather than a bound field's `.choices`, because both
        # controls are CharFields (see ProfileGamesForm.clean_sort). This also matches how the Badges
        # and Ratings tabs already feed their toolbars.
        context['sort_options'] = ProfileGamesForm.SORT_CHOICES
        context['status_options'] = ProfileGamesForm.STATUS_CHOICES
        context['selected_status'] = status
        context['selected_sort'] = sort_val
        # The scroller gates its first fetch on the grid holding a FULL page. This tab never set the size,
        # so the template's default (30) was measured against a server page of 50 -- the two only agreed by
        # accident, and the resume arithmetic after a history restore did not.
        context['scroll_per_page'] = per_page
        return context

    def _build_trophies_tab_context(self, profile, per_page, page_number):
        """The Trophies tab: ONE surface whose shape follows intent.

        No Activity/Log switcher. A search field sits above the day wall; with nothing in it you get the
        wall, and searching swaps it for the matching trophies. That replaced two views because they were
        never two ways of browsing -- Activity is how you browse a history, and the Log was an INDEX
        dressed as a wall. A single trophy has no natural shape (an icon, a name, a percentage), which is
        why no layout for it read well; as search results it does not need one.

        What went with the Log: its sorts (Activity answers recency far better) and its platform and
        rarity-range filters (a power-user cross-section of someone ELSE's history). What stayed is the
        one thing Activity genuinely cannot do -- answer "did they ever get this".
        """
        query, tiers = self._trophy_search_params()

        context = {'trophy_query': query, 'trophy_selected': tiers, 'trophy_tiers': self._TROPHY_TIERS,
                   'is_searching': bool(query or tiers),
                   # The scroller gates its first fetch on the grid being a FULL page, so it needs the size
                   # of whichever shape is rendered -- days or trophies, which page differently.
                   'scroll_per_page': per_page if (query or tiers) else DAYS_PER_PAGE}
        if context['is_searching']:
            context.update(self._build_trophy_search_context(profile, per_page, page_number, query, tiers))
        else:
            context.update(self._build_activity_context(profile))
        return context

    def _trophy_search_params(self):
        """The validated (query, tiers) pair -- the ONE definition of "is this a search".

        `get_template_names` and the context builder used to decide that separately, and the template
        chooser read `?tier=` RAW: a bogus tier built the day wall and then rendered it with the search
        template, which reads a `trophy_log` that does not exist.

        Tiers are a LIST: "their golds and platinums" is one question, and asking it as two searches is
        the kind of thing a filter exists to avoid. Unknown values are dropped rather than rejected, so a
        stale or hand-edited link still answers with whatever of it made sense.
        """
        query = (self.request.GET.get('q') or '').strip()[:_TROPHY_QUERY_MAX]
        known = dict(self._TROPHY_TIERS)
        tiers = [t for t in self.request.GET.getlist('tier') if t in known]
        return query, tiers

    #: Quick cross-sections worth keeping beside the search box. Tier is the one axis of a trophy that is
    #: both obvious to ask for and free to filter -- it is a column on Trophy, not a computed grade.
    _TROPHY_TIERS = [
        ('platinum', 'Platinums'),
        ('gold', 'Gold'),
        ('silver', 'Silver'),
        ('bronze', 'Bronze'),
    ]

    def _build_activity_context(self, profile):
        """The day wall. See `activity_service` for why each tier is fetched only when it is asked for."""
        from trophies.services.activity_service import DAYS_PER_PAGE, build_activity_page

        try:
            page = max(int(self.request.GET.get('page', 1)), 1)
        except (TypeError, ValueError):
            page = 1        # public, crawled URL: a hand-edited ?page= must not 500

        return build_activity_page(profile, page=page)

    def _build_trophy_search_context(self, profile, per_page, page_number, query, tiers):
        """Matching trophies, newest first.

        Sliced rather than paginated: `Paginator` runs `COUNT(*)` over the whole match set on every page,
        and a whale's history is 250,000 rows. The scroller stops when a page comes back short, so the
        count buys nothing. Ordered by recency because that is the only ordering a search result wants --
        "when did they get it" is the follow-up question to "did they".
        """
        try:
            page = max(int(page_number or 1), 1)
        except (TypeError, ValueError):
            page = 1
        offset = (page - 1) * per_page

        # The cards show COVER ART, and `display_image_url` resolves a trusted IGDB cover first -- so the
        # chain is joined and its ~30 KB `raw_response` blob deferred, which must always travel together.
        qs = (
            # Dated only, so the ordering below can come straight off the index. The partial index is
            # ASC NULLS LAST; a reverse scan gives DESC NULLS FIRST, so `nulls_last` matches neither
            # direction and Postgres sorts the entire match set before the LIMIT. Undated trophies have no
            # place in a recency ordering anyway.
            profile.earned_trophy_entries.filter(earned=True, earned_date_time__isnull=False)
            .select_related('trophy', 'trophy__game', 'trophy__game__concept',
                            'trophy__game__concept__igdb_match')
            .defer('trophy__game__concept__igdb_match__raw_response')
        )
        if query:
            qs = qs.filter(
                Q(trophy__trophy_name__icontains=query) | Q(trophy__game__title_name__icontains=query)
            )
        if tiers:
            qs = qs.filter(trophy__trophy_type__in=tiers)

        return {'trophy_log': list(qs.order_by('-earned_date_time')[offset:offset + per_page])}

    # How a visitor can reorder someone's badges. Deliberately SHORTER than the Collection gallery's six:
    # `edition` is an organisational sort that helps an owner audit their own wall, and this
    # is a stranger's read-only view. These four are the four questions a visitor actually asks.
    _BADGE_SORTS = [
        ('earned', 'Recently earned'),
        ('progress', 'Closest to earning'),
        ('rarity', 'Rarest first'),
        ('series', 'Series (A-Z)'),
    ]

    @staticmethod
    def _sort_badges(frames, sort):
        """Order the badge wall.

        Sorted HERE rather than by passing `sort` down to the service: `build_collection_context` does not
        sort at all. Its `sort` argument only picks the gallery dropdown's initial value, because the gallery
        reorders itself client-side in JS -- so handing it a sort would have reordered nothing, and this tab
        has no such JS (importing the gallery's would drag in its whole toolbar).

        Sorting the materialized list is free: it is already built and bounded by engaged series, not by
        library size, so there is no query and nothing here scales with a big account.
        """
        keys = {
            # Unearned badges carry earned_ts 0, so they land together at the end rather than interleaving.
            'earned': lambda f: (-f.get('earned_ts', 0), f.get('series_name', '').lower()),
            # In-progress first, nearest completion at the top -- an earned badge has nothing left to chase
            # and an untouched one has no progress to rank by, so neither belongs in the middle of this.
            'progress': lambda f: (f.get('state') != 'in_progress', -f.get('progress_pct', 0),
                                   f.get('series_name', '').lower()),
            # Rarity is "% of the community holding it", so low is rare. 0 is NOT the rarest -- it is the
            # no-data value (an edition nobody holds yet), so it sorts last instead of leading the wall.
            'rarity': lambda f: (not f.get('rarity_pct'), f.get('rarity_pct', 0),
                                 f.get('series_name', '').lower()),
            'series': lambda f: f.get('series_name', '').lower(),
        }
        return sorted(frames, key=keys[sort])

    def _build_badges_tab_context(self, profile):
        """Badges this hunter holds and is chasing -- the PUBLIC view of their collection.

        Reads the same service the owner's Collection gallery does. That matters for more than tidiness:
        `build_collection_context` resolves per-edition progress from the standings' materialized
        `group_progress` read-model, so it is a handful of bulk reads with no live evaluation and no
        per-badge queries. Live-evaluating badge state is O(engaged) and times out for a heavy account,
        which is why the Collection was built that way in the first place.

        It replaces a reader of the LEGACY badge tables (UserBadge / Badge / UserBadgeProgress), which
        nothing writes any more -- so this tab had been showing a frozen set to everybody.

        In-progress badges are kept rather than filtered out: what someone is chasing is as interesting to a
        visitor as what they already hold, and the medallion's own state treatment already tells the two
        apart without needing a filter to separate them.
        """
        from trophies.services.collection_service import build_collection_context

        context = build_collection_context(profile)

        sort = self.request.GET.get('sort', '')
        if sort not in dict(self._BADGE_SORTS):
            sort = self._BADGE_SORTS[0][0]
        context.update({
            'list_badges': self._sort_badges(context['list_badges'], sort),
            'sort': sort,
            'sort_options': self._BADGE_SORTS,
        })
        return context

    def _build_ratings_tab_context(self, profile):
        """What this hunter thinks of what they have played.

        The one tab that is about TASTE rather than totals. Games, Trophies and Badges all answer "how
        much"; this answers "and were they any good", which is the only thing on the profile that a second
        hunter with identical numbers would answer differently.

        Two layers, and the top one is why this is not just a list: a summary of everything they have rated
        (their average score, the hours they have signed off on, and the same synthesized sentence the game
        pages use, pointed at a person instead of a game), then the wall. A rating with no aggregate above
        it is a number without a scale -- 6/10 difficulty means one thing from someone whose average is 3
        and another from someone whose average is 8.

        Both halves are built in `rating_service`, next to the community-average code they must agree with.
        """
        from trophies.services.rating_service import (
            PROFILE_RATING_SORTS, RATINGS_PER_PAGE, build_profile_ratings_page, profile_rating_summary,
        )

        sort = self.request.GET.get('sort', '')
        if sort not in dict(PROFILE_RATING_SORTS):
            sort = PROFILE_RATING_SORTS[0][0]

        try:
            page = max(int(self.request.GET.get('page', 1)), 1)
        except (TypeError, ValueError):
            page = 1        # public, crawled URL: a hand-edited ?page= must not 500

        # Base games and DLC are separate walls. A DLC pack is rated separately from the game it belongs
        # to, so a mixed wall shows the same title twice with two different scores and leaves the reader
        # to work out why. Anything but 'dlc' is games -- a hand-edited `?set=` should land somewhere, not
        # 404.
        rating_set = 'dlc' if self.request.GET.get('set') == 'dlc' else 'games'

        return {
            # Not computed for a scroll append: that response is cards only, and the summary describes the
            # whole set, so it would be an extra aggregate per appended page that nothing renders.
            'rating_summary_stats': None if self._is_scroll_append() else profile_rating_summary(profile),
            'profile_ratings': build_profile_ratings_page(
                profile, sort=sort, page=page, dlc=rating_set == 'dlc',
            ),
            'rating_set': rating_set,
            #: The switcher's two chips, in order. A list rather than two hardcoded anchors so the chip
            #: markup is written once -- the same reason the recommendation options are looped.
            'ratings_sets': (('games', 'Games'), ('dlc', 'DLC')),
            'sort': sort,
            'sort_options': PROFILE_RATING_SORTS,
            'scroll_per_page': RATINGS_PER_PAGE,
        }

    def _build_lists_tab_context(self, public_lists_qs):
        """Build context for lists tab — public game lists for this profile."""
        return {'profile_lists': public_lists_qs.order_by('-like_count', '-created_at')}

    def get_context_data(self, **kwargs):
        """Build context for profile detail page with tab-specific content.

        This method delegates to tab-specific helper methods to keep the code
        organized and maintainable. Each tab (games, trophies, badges) has its
        own focused handler method.

        Args:
            **kwargs: Standard Django context keyword arguments

        Returns:
            dict: Context dictionary with profile data, tab content, and metadata
        """
        context = super().get_context_data(**kwargs)
        profile: Profile = self.object
        tab = self.request.GET.get('tab', 'games')
        per_page = 50
        page_number = self.request.GET.get('page', 1)

        # Two providers used to run here and neither does any more (2026-08).
        #
        # SHOWCASES are hidden pending a ground-up rebuild of profile customization -- the doors are
        # closed but every row is intact, so restoring them is putting this call and the include back.
        # They were deliberately ungated (a shared profile link is mostly opened logged-out, which is
        # the audience the customization existed for), so hiding them is a product decision, not a
        # cost one; the page simply got cheaper on the way.
        #
        # The TIMELINE is gone outright. It had rendered nowhere since the header rebuild dropped its
        # include, while still being built and discarded on every authenticated render of every tab.
        # Its content had rotted too: a third of its events read the dead legacy UserBadge tables.
        #
        # The four Platinum Highlight cards below are deliberately NOT gated and stay: they render a
        # "None" empty state when absent, so skipping them would misreport the profile to logged-out
        # visitors rather than hiding a section, and they are cheap (two denormed FKs plus two lookups
        # bounded by the profile's ProfileGame rows).
        context['header_stats'] = self._build_header_stats(profile)

        # Public game lists count (shown in tab header regardless of active tab)
        public_lists_qs = GameList.objects.filter(profile=profile, is_public=True, is_deleted=False)
        context['profile_lists_count'] = public_lists_qs.count()

        # Gaming history is opt-out, and until now the ONLY thing enforcing that was
        # `{% if profile.psn_history_public %}` in profile_detail.html. An HTMX request is answered with
        # the tab template DIRECTLY (get_template_names), which never renders that parent -- so
        # `?tab=games` + `HX-Request: true` returned a private hunter's library to anyone who asked.
        #
        # The hole was dormant for badges, whose tab read legacy tables nothing writes, and live for the
        # rest. Enforced here instead, where both render paths pass: no tab context is built at all, so a
        # private profile costs nothing to render rather than building a wall that gets thrown away.
        self._history_visible = profile.psn_history_public

        # Delegate to tab-specific handler methods
        if not self._history_visible:
            tab_context = {}
        elif tab == 'games':
            tab_context = self._build_games_tab_context(profile, per_page, page_number)
        elif tab == 'trophies':
            tab_context = self._build_trophies_tab_context(profile, per_page, page_number)
        elif tab == 'badges':
            tab_context = self._build_badges_tab_context(profile)
        elif tab == 'ratings':
            tab_context = self._build_ratings_tab_context(profile)
        elif tab == 'lists':
            tab_context = self._build_lists_tab_context(public_lists_qs)
        else:
            # Default to games tab if invalid tab specified
            tab_context = self._build_games_tab_context(profile, per_page, page_number)

        context.update(tab_context)

        # Add shared metadata
        context['breadcrumb'] = [
            {'text': 'Home', 'url': reverse_lazy('home')},
            {'text': 'Hunters', 'url': reverse_lazy('profiles_list')},
            {'text': f"{profile.display_psn_username}"}
        ]
        context['current_tab'] = tab
        # The tabs the switcher renders, in order. Lists is deliberately NOT here: Game Lists is parked
        # and every route into it redirects home, so the tab offered cards whose links bounced the reader
        # to the homepage. Its builder and template stay (hidden, not deleted); only the door is closed.
        context['profile_tabs'] = (
            ('games', 'Games'),
            ('trophies', 'Trophies'),
            ('badges', 'Badges'),
            ('ratings', 'Ratings'),
        )

        context['tab_template'] = self._TAB_TEMPLATES.get(tab, self._TAB_TEMPLATES['games'])

        # Own profile check (for edit controls)
        context['is_own_profile'] = (
            self.request.user.is_authenticated and
            hasattr(self.request.user, 'profile') and
            self.request.user.profile == profile
        )

        context['seo_description'] = (
            f"{profile.display_psn_username}'s PlayStation trophy profile. "
            f"Level {profile.trophy_level}, {profile.total_trophies} trophies, "
            f"{profile.total_games} games."
        )


        return context

    # Template maps for HTMX partial responses. Also the map `get_context_data` picks `tab_template` from,
    # so the full-page render and the HTMX swap cannot answer with different templates for the same tab.
    _TAB_TEMPLATES = {
        'games': 'trophies/partials/profile_detail/tabs/games_tab.html',
        'trophies': 'trophies/partials/profile_detail/tabs/trophies_tab.html',
        'badges': 'trophies/partials/profile_detail/tabs/badges_tab.html',
        'ratings': 'trophies/partials/profile_detail/tabs/ratings_tab.html',
        'lists': 'trophies/partials/profile_detail/tabs/lists_tab.html',
    }
    _RESULTS_TEMPLATES = {
        'games': 'trophies/partials/profile_detail/tabs/games_results.html',
        'trophies': 'trophies/partials/profile_detail/tabs/trophies_results.html',
        'ratings': 'trophies/partials/profile_detail/tabs/ratings_results.html',
    }
    _INFINITE_SCROLL_TEMPLATES = {
        'games': 'trophies/partials/profile_detail/game_list_items.html',
        'trophies': 'trophies/partials/profile_detail/trophy_list_items.html',
        'ratings': 'trophies/partials/profile_detail/rating_list_items.html',
    }
    #: The trophies tab has two views, and only one of them scrolls tiles -- Activity appends day tiles,
    #: while the Log appends trophy rows. Keyed by view so the scroller cannot be handed the wrong half.
    _INFINITE_SCROLL_VIEWS = {
        ('trophies', 'activity'): 'trophies/partials/profile_detail/activity_tiles.html',
        ('trophies', 'search'): 'trophies/partials/profile_detail/trophy_list_items.html',
    }

    def _is_scroll_append(self):
        """True when `InfiniteScroller` is asking for the next page of cards.

        One definition, read by both the context builders (which skip work the append never renders) and
        `get_template_names` (which answers with the items partial). Two copies of a condition that picks
        the TEMPLATE and the condition that picks the CONTEXT is exactly how the trophies tab ended up
        rendering the search template over the day wall's context.
        """
        return self.request.headers.get('X-Requested-With') == 'XMLHttpRequest'

    def get_template_names(self):
        tab = self.request.GET.get('tab', 'games')

        # A private profile never answers with a tab body, on ANY path. Returning the full page instead
        # means the request renders profile_detail.html, whose `{% if profile.psn_history_public %}` drops
        # the tabs -- so the two render paths agree instead of one having its own back door. Defaults to
        # hidden if the flag was never set: this runs after get_context_data on every real request, and an
        # unset attribute should fail closed.
        if not getattr(self, '_history_visible', False):
            return super().get_template_names()

        # HTMX partial swap (tab switch or filter change)
        if getattr(self.request, 'htmx', False):
            target = self.request.htmx.target
            if target == 'tab-results' and tab in self._RESULTS_TEMPLATES:
                return [self._RESULTS_TEMPLATES[tab]]
            if tab in self._TAB_TEMPLATES:
                return [self._TAB_TEMPLATES[tab]]

        # Infinite scroll (XMLHttpRequest from InfiniteScroller)
        if self._is_scroll_append():
            # The trophies tab appends day TILES while browsing and trophy CARDS while searching, so the
            # scroller's template follows the same intent the page does.
            searching = tab == 'trophies' and any(self._trophy_search_params())
            keyed = self._INFINITE_SCROLL_VIEWS.get((tab, 'search' if searching else 'activity'))
            if keyed:
                return [keyed]
            if tab in self._INFINITE_SCROLL_TEMPLATES:
                return [self._INFINITE_SCROLL_TEMPLATES[tab]]

        return super().get_template_names()


class ProfileDayView(View):
    """One day of a hunter's activity: the sessions inside it.

    A REAL URL, not just a modal payload. The profile is public and crawled, and content reachable only by
    clicking is invisible to a crawler and impossible to link to -- so a day is addressable, the browser's
    back button works on it, and someone without JS gets a page instead of a dead tile. HTMX asks for the
    same URL and drops the partial into the modal; the only difference is which template answers.

    Gated on `psn_history_public` in its own right. The tab's guard lives in the profile view, and an
    endpoint on its own URL would otherwise be a side door around it -- the exact shape of the HTMX bypass
    that made the tabs leak.
    """

    def get(self, request, psn_username, day):
        from datetime import date
        from trophies.services.activity_service import day_sessions

        # `.lower()` + exact, as ProfileDetailView does: `iexact` compiles to UPPER(col) = UPPER(%s),
        # which cannot use the unique index and seq-scans every Profile on each modal open.
        profile = get_object_or_404(Profile, psn_username=psn_username.lower())
        if not profile.psn_history_public:
            raise Http404

        try:
            when = date.fromisoformat(day)
        except ValueError:
            raise Http404       # client-supplied; a malformed date is not a 500

        sessions = day_sessions(profile, when)
        if not sessions:
            raise Http404       # a day with nothing in it is not a page

        # Both templates render every game OPEN, so both need the trophies in the HTML. One grouped query
        # for the whole day rather than one per session -- a day has few games, but "few" is not a reason
        # to write an N+1 into the page crawlers read.
        from trophies.services.activity_service import attach_day_trophies
        attach_day_trophies(profile, when, sessions)
        htmx = getattr(request, 'htmx', False)

        context = {
            'profile': profile,
            'day': when,
            'sessions': sessions,
            'day_trophies': sum(s['trophies'] for s in sessions),
            'day_platinums': sum(1 for s in sessions if s['has_platinum']),
            'breadcrumb': [
                {'text': 'Home', 'url': reverse_lazy('home')},
                {'text': 'Hunters', 'url': reverse_lazy('profiles_list')},
                {'text': profile.display_psn_username or profile.psn_username,
                 'url': reverse('profile_detail', args=[profile.psn_username])},
                {'text': when.strftime('%b %d, %Y')},
            ],
        }
        template = ('trophies/partials/profile_detail/activity_day_modal.html'
                    if htmx else 'trophies/activity_day.html')
        return render(request, template, context)


class LinkPSNView(LoginRequiredMixin, View):
    """
    Multi-step view for linking PSN account to web account.

    Steps:
    1. User enters PSN username
    2. System generates verification code and syncs profile
    3. User adds code to PSN "About Me" section
    4. System verifies code presence via PSN API
    5. Profile is linked to authenticated user account

    Handles profile creation, sync, verification code generation, and final verification.
    """
    template_name = 'account/link_psn.html'
    login_url = reverse_lazy('login')
    form_class = LinkPSNForm

    def get(self, request):
        if hasattr(request.user, 'profile') and request.user.profile.is_linked:
            messages.info(request, 'This PSN account is already linked to a web account.')
            return redirect('link_psn')

        form = self.form_class()
        context = {'form': form, 'step': 1}
        return render(request, self.template_name, context)

    def post(self, request):
        action = request.POST.get('action')
        psn_outage = bool(redis_client.get('site:psn_outage'))

        if action == 'submit_username':
            form = self.form_class(request.POST)
            if form.is_valid():
                psn_username = form.cleaned_data['psn_username'].lower().strip()
                try:
                    profile, created = Profile.objects.get_or_create(psn_username=psn_username)
                    if profile.user and profile.user != request.user:
                        raise ValueError('This PSN account is already linked to another user.')

                    time_since_last_sync = profile.get_time_since_last_sync()
                    if created:
                        PSNManager.initial_sync(profile)
                    else:
                        profile.attempt_sync()

                    if not profile.verification_code or profile.verification_expires_at < timezone.now():
                        profile.generate_verification_code()

                    if psn_outage:
                        messages.warning(
                            request,
                            'PlayStation Network is currently unavailable. '
                            'Verification will not work until PSN recovers. '
                            'Your code has been generated and will be ready when service returns.'
                        )

                    context = {
                        'form': form,
                        'step': 2,
                        'verification_code': profile.verification_code,
                        'profile': profile,
                    }
                    return render(request, self.template_name, context)
                except ValueError as e:
                    messages.error(request, str(e))
                except Exception as e:
                    messages.error(request, 'An error occured during sync. Please try again later.')
            return render(request, self.template_name, {'form': form, 'step': 1})
        elif action == 'verify':
            if psn_outage:
                form = self.form_class(request.POST)
                psn_username = form.data.get('psn_username', '')
                messages.error(
                    request,
                    'PlayStation Network is currently unavailable. '
                    'Please try verifying again once service recovers.'
                )
                try:
                    profile = Profile.objects.get(psn_username=psn_username.lower())
                    return render(request, self.template_name, {
                        'form': self.form_class(initial={'psn_username': psn_username}),
                        'step': 2,
                        'verification_code': profile.verification_code,
                        'profile': profile,
                    })
                except Profile.DoesNotExist:
                    return redirect('link_psn')

            form = self.form_class(request.POST)
            if form.is_valid():
                psn_username = form.cleaned_data['psn_username'].lower()
                try:
                    start_time = timezone.now().timestamp()
                    profile = Profile.objects.get(psn_username=psn_username.lower())
                    is_syncing = profile.attempt_sync()
                    if not is_syncing:
                        PSNManager.sync_profile_data(profile)

                    messages.info(request, "Verification in progress...")
                    context = {
                        'form': self.form_class(initial={'psn_username': psn_username}),
                        'step': 3,
                        'verification_code': profile.verification_code,
                        'profile': profile,
                        'start_time': str(start_time),
                    }
                    return render(request, self.template_name, context)
                except Profile.DoesNotExist:
                    messages.error(request, "Profile not found. Please start over.")
                    return redirect('link_psn')
                except Exception as e:
                    messages.error(request, f"An error occurred during verification. Please try again.")
                    return render(request, self.template_name, {'form': self.form_class(initial={'psn_username': psn_username}), 'step': 2})

        return redirect('link_psn')


class ProfileVerifyView(LoginRequiredMixin, View):
    """
    AJAX endpoint for polling PSN verification status during link flow.

    Checks if profile has been synced since verification started and
    if verification code appears in PSN "About Me" section.
    Links profile to user account upon successful verification.

    Rate limited to 60 requests per minute per user.
    """
    @method_decorator(ratelimit(key='user', rate='60/m', method='GET'))
    def get(self, request):
        if redis_client.get('site:psn_outage'):
            return JsonResponse({
                'psn_outage': True,
                'error': 'PlayStation Network is currently unavailable. '
                         'Verification will resume when PSN recovers.',
            }, status=503)

        user = request.user
        profile_id = request.GET.get('profile_id')
        start_time = request.GET.get('start_time')
        if not profile_id:
            return JsonResponse({'error': 'Profile id required'}, status=400)
        if not start_time:
            return JsonResponse({'error': 'start_time required'}, status=400)

        try:
            start_time_float = float(start_time)
        except ValueError:
            return JsonResponse({'error': 'Invalid start_time format'}, status=400)

        try:
            profile = Profile.objects.get(id=profile_id)
        except Profile.DoesNotExist:
            return JsonResponse({'error': 'Profile not found'}, status=404)

        if profile.sync_status == 'error':
            return JsonResponse({'error': 'Sync error. Make sure your "Gaming History" permission is set to "Anybody"'}, status=400)

        verified = False
        synced = False
        if profile.last_synced.timestamp() > start_time_float:
            synced = True
            verified = profile.verify_code(profile.about_me)
            if verified:
                profile.link_to_user(user)

        return JsonResponse({
            'synced': synced,
            'verified': verified,
        })

