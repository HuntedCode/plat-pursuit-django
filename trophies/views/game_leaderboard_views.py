"""Game leaderboard panel (the Ranks tab on game detail).

Served as an HTML partial rather than JSON because the panel is lazy-loaded: it is deliberately NOT
server-rendered with the rest of the page, since it is the only panel whose cost scales with a game's
popularity and most visitors arrive from search wanting trophy info, never opening it.

The client renders the board VIRTUALIZED (a full-height spacer, only the visible rows in the DOM), so it
pulls rows by rank RANGE as it scrolls. Three shapes from one URL:
  - no params  -> the whole panel: controls, board card, the viewer's standing, board_size (for the
    spacer), and the first window of rows for instant first paint
  - ?range=<position>&count=<n>  -> just those rows, for a virtual window
  - ?suggest=<q>  -> JSON of board players matching a name (the search typeahead)

A display position IS a rank. `from` and the +1/-1 step went with `invert`, which was the only reason two
numberings ever existed here.

ONE ROW COMPONENT, shared with the Global Boards, badge detail and job detail: rows are hydrated through
`badge_leaderboards.page()` into the same entry shape those boards use, so `leaderboard_row.html` renders
every leaderboard on the site. The board-specific figures are the only thing that varies, and they go
through the same `extra` hook the other three use -- see FIGURES below.
"""
import logging

from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils.http import urlencode
from django.views import View

from trophies.views.board_helpers import MAX_START, clamped_int, slice_country, window_params
from trophies.models import Game
from trophies.services import badge_leaderboards as lb
from trophies.services import game_leaderboard_service as svc

logger = logging.getLogger('psn_api')

#: The SHARED window partial -- bare `.lb-row`s, no wrapper -- the same one every other board serves.
ROWS_TEMPLATE = 'trophies/partials/leaderboard_rows.html'
PANEL_TEMPLATE = 'trophies/partials/game_detail/_leaderboard_panel.html'


class GameLeaderboardView(View):
    """The panel, a virtual window of rows, or a search suggestion list -- all as one game's leaderboard."""

    def get(self, request, np_communication_id):
        game = get_object_or_404(Game, np_communication_id=np_communication_id)
        # A fresh tab (no ?board=) lands on the base-game standings for a DLC game, the overall board
        # otherwise. Continuation fetches always carry the active board, so this only fires on first load.
        board_param = request.GET.get('board') or svc.default_board_param(game)

        # THE WINDOW PATH TAKES THE SLICE VERBATIM. Validating a country means asking which countries are
        # on this board, which is a DISTINCT over the whole population -- and the virtualizer fires one
        # window per screenful, so doing it here turned every scroll step into a full scan. The client
        # only ever echoes back the slice the panel rendered, so it is validated by construction; a
        # crafted code selects nobody and returns an empty window. See `board_helpers.slice_country`.
        if 'range' in request.GET:
            opts = svc.BoardOptions.from_request(request, unvalidated_country=slice_country(request))
            board = svc.resolve_board(game, board_param, opts)
            start, count = window_params(request, svc.PAGE_SIZE)
            return render(request, ROWS_TEMPLATE,
                          {'entries': self._entries(board.page_range(start, count), start - 1, board.kind)})

        # TWO PASSES over the options on the PANEL path, and the second is not waste. A country is only
        # valid if somebody on this board is from it, and "this board" is not known until the board exists
        # -- which needs options. The first pass builds the board unsliced and asks it which countries it
        # has; the second validates against that. `Board.__init__` issues no query, so only the
        # `countries()` call costs anything, and it is cached.
        unsliced = svc.BoardOptions.from_request(request)
        codes = svc.resolve_board(game, board_param, unsliced).countries()
        opts = svc.BoardOptions.from_request(request, codes)
        board = svc.resolve_board(game, board_param, opts)
        profile = self._viewer_profile(request)

        # Typeahead: board players matching a name -> JSON, for the search dropdown.
        if request.GET.get('suggest') is not None:
            matches = board.suggest(request.GET.get('suggest', ''))
            return JsonResponse({'players': [self._player_json(m) for m in matches]})

        # Number typeahead: preview the hunter at a specific rank -> JSON. The client already holds the total
        # (data-lb-total), so it never asks for a rank past the board -- no COUNT needed here.
        if request.GET.get('at') is not None:
            # Bounded EXPLICITLY. It leaned on `_int`'s default ceiling, which is the kind of bound that
            # disappears the moment somebody passes an argument -- and `at` is a direct offset into the
            # board (`row_at_rank` slices `[rank - 1: rank]` with no cap of its own).
            # Default 1, not 0. `clamped_int` returns the default UNCLAMPED by contract, so a default
            # below `lo` is exactly the bug its docstring warns the clamp would hide -- it only worked
            # because `row_at_rank` re-applies `max(1, rank)`.
            m = board.row_at_rank(clamped_int(request.GET.get('at'), 1, lo=1, hi=MAX_START))
            return JsonResponse({'players': [self._player_json(m)] if m else []})

        # A virtual window: rows at a display range, numbered from the caller-supplied canonical rank.
        if request.GET.get('range') is not None:
            # The SHARED parser, same as the other three boards. This was a fourth hand-rolled copy whose
            # only real difference was a looser start bound (100M, i.e. "walk the whole board"); the
            # count ceiling already matched at 200.
            start, count = window_params(request, svc.PAGE_SIZE)
            return render(request, ROWS_TEMPLATE,
                          {'entries': self._entries(board.page_range(start, count), start - 1, board.kind)})

        # Full panel: the first window seeds first paint; board_size sizes the virtual spacer.
        total = board.size()
        my_rank = board.rank_for(profile)
        nav = svc.board_menu(game, board_param)
        context = {
            'game': game,
            'opts': opts,
            'board_param': board_param,        # carried on continuation fetches so the view stays consistent
            'active_board': board_param,
            'board_nav': nav,
            'total': total,
            'page_size': svc.PAGE_SIZE,        # stamped into the DOM so the JS fetch granularity can't drift
            'my_rank': my_rank,
            # What the board card tells a signed-in viewer about themselves. Badge and job detail both
            # fill this slot; game detail did not, so a linked hunter who owns the game but is off the
            # filtered board saw the tally and nothing about where they stand. A RANKED viewer gets
            # nothing here, because the jump chip beneath already says it.
            'standing': 'Not on this board yet' if (profile and not my_rank) else '',
            'viewer_profile': profile,
            'board_kind': board.kind,
            # The shared board card + jump bar read these, exactly as the other three boards' do.
            'board_label': nav['title'],
            'board_meaning': self.MEANINGS[board.kind],
            'ranked_label': 'hunter',
            # The COUNTRY only. `as_params()` also carries `earners`, which changes the population but is
            # not a slice of it -- "N hunters here" under `?earners=0` claims a narrowing that widened.
            'slice_applied': bool(opts.country),
            'rows_url': reverse('game_leaderboard', args=[game.np_communication_id]),
            'rows_params': urlencode({'board': board_param, **opts.as_params()}),
            'countries': lb.country_options(codes),
            'selected_country': opts.country,
            **self.LABELS[board.kind],
            'rows': self._entries(board.page_range(1, svc.PAGE_SIZE), 0, board.kind),
        }
        return render(request, PANEL_TEMPLATE, context)

    #: What each board RANKS, in one line -- the board card's description slot. Same job as
    #: `OverallBadgeLeaderboardsView.MEANINGS`.
    MEANINGS = {
        'progress': 'Everyone who has started this game, furthest along first.',
        'speed': 'Fastest first trophy to last, for everyone who finished.',
        'playtime': 'The most time on this game, longest first.',
    }

    #: Every LABEL a board kind uses, in one place, merged into each entry by `_entries`. The rows are
    #: the only reader -- there is no column header any more, so a label that names a column the rows do
    #: not carry is not possible by construction.
    LABELS = {
        'progress': {'primary_label': 'complete', 'secondary_label': 'trophies', 'when_label': 'since'},
        'speed': {'primary_label': 'elapsed', 'secondary_label': '', 'when_label': 'finished'},
        'playtime': {'primary_label': 'played', 'secondary_label': 'complete',
                     'when_label': 'last played'},
    }

    #: How each board kind fills the shared row's three figure slots. The row carries ONE headline figure,
    #: one supporting figure and one date -- so the completion BAR, the per-tier trophy dots and the speed
    #: board's second date are gone, deliberately: this board had a richer row than every other board on
    #: the site, and being the same board mattered more than the extra columns.
    #:
    #: Pre-formatted strings are safe in `primary`. The row runs it through `intcomma`, which falls back to
    #: a digit-prefix regex for anything it cannot `int()` -- so "4d 12h" survives untouched and "1234h"
    #: even picks up its comma.
    FIGURES = {
        'progress': lambda r: {
            'primary': f'{r.progress}%',
            'secondary': sum((r.earned_trophies or {}).values()),
            'when': _when(r),
        },
        'speed': lambda r: {
            # The started -> finished window is gone with the second date slot; the finish is the one that
            # doubles as this board's tiebreak, so it is the one that stays.
            'primary': _fmt_seconds(getattr(r, 'completion_seconds', None)),
            'secondary': None,
            'when': _when(r),
        },
        'playtime': lambda r: {
            'primary': _fmt_playtime(getattr(r, 'play_duration', None)),
            'secondary': f'{r.progress}%',
            'when': getattr(r, 'last_played_date_time', None),
        },
    }

    @classmethod
    def _entries(cls, rows, offset, kind):
        """Board rows -> the shared entry shape, through the SAME `page()` every other board uses.

        `page()` wants tuples whose first element is a profile id; the rest is opaque to it, so the model
        instance rides along as the second element and the figure mapper reads it. That buys the identity
        half for free and identically: the flag, the supporter star and the worn title all render here now
        because they render there, and this board simply did not have them before.

        It also makes the rows VIEWER-INDEPENDENT. This board rendered its own `--you` modifier server-side,
        so every row was personal to the reader and could not be shared between them; the shared row is
        marked in the browser from `data-lb-viewer-rank` instead.
        """
        figures = cls.FIGURES[kind]
        labels = cls.LABELS[kind]
        return lb.page([(r.profile_id, r) for r in rows], offset,
                       extra=lambda t: {**labels, **figures(t[1])})

    # -- helpers ---------------------------------------------------------

    @staticmethod
    def _viewer_profile(request):
        if not request.user.is_authenticated:
            return None
        profile = getattr(request.user, 'profile', None)
        return profile if profile and profile.is_linked else None

    @staticmethod
    def _player_json(m):
        """Serialize one board match (from suggest / row_at_rank) for the search dropdown."""
        p = m['profile']
        return {
            'display': p.display_psn_username or p.psn_username,
            'username': p.psn_username,
            'avatar': p.avatar_url or '',
            'rank': m['rank'],
            'progress': m['progress'],
            'url': reverse('profile_detail', args=[p.psn_username]),
        }

def _when(row):
    """The tiebreak timestamp, normalized across the two models a board can read.

    `ProfileGame` carries `most_recent_trophy_date`, `ProfileTrophyGroup` carries `last_trophy_at`, and
    the row template must not know which board it is rendering.
    """
    return getattr(row, 'most_recent_trophy_date', None) or getattr(row, 'last_trophy_at', None)


def _fmt_seconds(s):
    """A completion elapsed, compact: the two most significant units (5d 6h, 3h 20m, 45m, <1m)."""
    if s is None:
        return None
    s = int(s)
    d, r = divmod(s, 86400)
    h, r = divmod(r, 3600)
    m, _ = divmod(r, 60)
    if d:
        return f'{d}d {h}h' if h else f'{d}d'
    if h:
        return f'{h}h {m}m' if m else f'{h}h'
    return f'{m}m' if m else '<1m'


def _fmt_playtime(td):
    """PSN play time, compact -- hours are the natural unit gamers read (452h); minutes only under an hour."""
    if td is None:
        return None
    total = int(td.total_seconds())
    h, m = total // 3600, (total % 3600) // 60
    if h:
        return f'{h}h'
    return f'{m}m' if m else '<1m'
