"""Game leaderboard panel (the Ranks tab on game detail).

Served as an HTML partial rather than JSON because the panel is lazy-loaded: it is deliberately NOT
server-rendered with the rest of the page, since it is the only panel whose cost scales with a game's
popularity and most visitors arrive from search wanting trophy info, never opening it.

The client renders the board VIRTUALIZED (a full-height spacer, only the visible rows in the DOM), so it
pulls rows by rank RANGE as it scrolls. Three shapes from one URL:
  - no params  -> the whole panel: controls, header, the viewer's standing, board_size (for the spacer),
    and the first window of rows for instant first paint
  - ?range=<display-position>&from=<canonical-rank>&count=<n>  -> just those rows, for a virtual window
  - ?suggest=<q>  -> JSON of board players matching a name (the search typeahead)

Rows are numbered from `from` stepping +1 (forward) or -1 (inverted); ranks stay canonical (from the top),
so an inverted board simply counts down. `from` is display-only: the client derives it from the window's
position + the total it already holds, so a range fetch costs no COUNT.
"""
import logging

from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.views import View

from trophies.models import Game
from trophies.services import game_leaderboard_service as svc

logger = logging.getLogger('psn_api')

ROWS_TEMPLATE = 'trophies/partials/game_detail/_leaderboard_rows.html'
PANEL_TEMPLATE = 'trophies/partials/game_detail/_leaderboard_panel.html'


class GameLeaderboardView(View):
    """The panel, a virtual window of rows, or a search suggestion list -- all as one game's leaderboard."""

    def get(self, request, np_communication_id):
        game = get_object_or_404(Game, np_communication_id=np_communication_id)
        opts = svc.BoardOptions.from_request(request)
        board_param = request.GET.get('board', '')
        board = svc.resolve_board(game, board_param, opts)   # which board (?board=); defaults to Everything
        profile = self._viewer_profile(request)
        step = -1 if opts.invert else 1

        # Typeahead: board players matching a name -> JSON, for the search dropdown.
        if request.GET.get('suggest') is not None:
            matches = board.suggest(request.GET.get('suggest', ''))
            return JsonResponse({'players': [self._player_json(m) for m in matches]})

        # Number typeahead: preview the hunter at a specific rank -> JSON. The client already holds the total
        # (data-lb-total), so it never asks for a rank past the board -- no COUNT needed here.
        if request.GET.get('at') is not None:
            m = board.row_at_rank(self._int(request.GET.get('at'), 0))
            return JsonResponse({'players': [self._player_json(m)] if m else []})

        # A virtual window: rows at a display range, numbered from the caller-supplied canonical rank.
        if request.GET.get('range') is not None:
            start = self._int(request.GET.get('range'), 1)
            count = self._int(request.GET.get('count'), svc.PAGE_SIZE, hi=200)
            from_rank = self._int(request.GET.get('from'), start)
            rows = board.page_range(start, count)
            return self._rows(request, game, opts, rows, from_rank, step, profile, board.kind)

        # Full panel: the first window seeds first paint; board_size sizes the virtual spacer.
        total = board.size()
        rows = board.page_range(1, svc.PAGE_SIZE)
        start_rank = total if opts.invert else 1
        viewer_rank = board.rank_for(profile)
        context = {
            'game': game,
            'opts': opts,
            'board_param': board_param,        # carried on continuation fetches so the view stays consistent
            'active_board': board_param or 'progress:all',
            'board_nav': svc.board_menu(game, board_param),
            'board_size': total,
            'page_size': svc.PAGE_SIZE,        # stamped into the DOM so the JS fetch granularity can't drift
            'viewer_rank': viewer_rank,
            'viewer_profile': profile,
            **self._rows_ctx(rows, start_rank, step, profile, board.kind),
        }
        return render(request, PANEL_TEMPLATE, context)

    # -- helpers ---------------------------------------------------------

    @staticmethod
    def _viewer_profile(request):
        if not request.user.is_authenticated:
            return None
        profile = getattr(request.user, 'profile', None)
        return profile if profile and profile.is_linked else None

    @staticmethod
    def _int(raw, default, lo=1, hi=100_000_000):
        try:
            return max(lo, min(int(raw), hi))
        except (TypeError, ValueError):
            return default

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

    @staticmethod
    def _rows_ctx(rows, start_rank, step, profile, board_kind):
        """Number the rows and stamp the display fields the row template reads uniformly across board types.
        `when` normalizes the tiebreak timestamp (ProfileGame.most_recent_trophy_date vs
        ProfileTrophyGroup.last_trophy_at); speed/playtime rows also get a compact metric label."""
        for i, row in enumerate(rows):
            row.rank = start_rank + i * step
            row.when = getattr(row, 'most_recent_trophy_date', None) or getattr(row, 'last_trophy_at', None)
            if board_kind == 'speed':
                row.elapsed_label = _fmt_seconds(getattr(row, 'completion_seconds', None))
            elif board_kind == 'playtime':
                row.playtime_label = _fmt_playtime(getattr(row, 'play_duration', None))
        return {'rows': rows, 'viewer_profile': profile, 'board_kind': board_kind}

    def _rows(self, request, game, opts, rows, start_rank, step, profile, board_kind):
        ctx = {'game': game, 'opts': opts, **self._rows_ctx(rows, start_rank, step, profile, board_kind)}
        return render(request, ROWS_TEMPLATE, ctx)


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
