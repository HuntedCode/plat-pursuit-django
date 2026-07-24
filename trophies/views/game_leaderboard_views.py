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
        profile = self._viewer_profile(request)
        step = -1 if opts.invert else 1

        # Typeahead: board players matching a name -> JSON, for the search dropdown.
        if request.GET.get('suggest') is not None:
            return JsonResponse({'players': [
                {
                    'display': m['profile'].display_psn_username or m['profile'].psn_username,
                    'username': m['profile'].psn_username,
                    'avatar': m['profile'].avatar_url or '',
                    'rank': m['rank'],
                    'progress': m['progress'],
                    'url': reverse('profile_detail', args=[m['profile'].psn_username]),
                }
                for m in svc.suggest(game, opts, request.GET.get('suggest', ''))
            ]})

        # A virtual window: rows at a display range, numbered from the caller-supplied canonical rank.
        if request.GET.get('range') is not None:
            start = self._int(request.GET.get('range'), 1)
            count = self._int(request.GET.get('count'), svc.PAGE_SIZE, hi=200)
            from_rank = self._int(request.GET.get('from'), start)
            rows = svc.page_range(game, opts, start, count)
            return self._rows(request, game, opts, rows, from_rank, step, profile)

        # Full panel: the first window seeds first paint; board_size sizes the virtual spacer.
        total = svc.board_size(game, opts)
        rows = svc.page_range(game, opts, 1, svc.PAGE_SIZE)
        start_rank = total if opts.invert else 1
        viewer_rank = svc.rank_for(game, profile, opts)
        context = {
            'game': game,
            'opts': opts,
            'board_size': total,
            'viewer_rank': viewer_rank,
            'viewer_profile': profile,
            **self._rows_ctx(rows, start_rank, step, profile),
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
    def _rows_ctx(rows, start_rank, step, profile):
        """Number the rows for the template (the client positions them by rank)."""
        for i, row in enumerate(rows):
            row.rank = start_rank + i * step
        return {'rows': rows, 'viewer_profile': profile}

    def _rows(self, request, game, opts, rows, start_rank, step, profile):
        ctx = {'game': game, 'opts': opts, **self._rows_ctx(rows, start_rank, step, profile)}
        return render(request, ROWS_TEMPLATE, ctx)
