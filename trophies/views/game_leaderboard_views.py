"""Game leaderboard panel (the Ranks tab on game detail).

Served as an HTML partial rather than JSON because the panel is lazy-loaded: it is deliberately NOT
server-rendered with the rest of the page, since it is the only panel whose cost scales with a game's
popularity and most visitors arrive from search wanting trophy info, never opening it.

Response shapes from one endpoint (all honour the BoardOptions in the query string):
  - no cursor/jump  -> the whole panel (controls + header + first page + the viewer's own standing)
  - ?after=&from=   -> the next keyset page of rows, for the infinite scroller to append
  - ?around=me      -> a window centred on the viewer (jump to my rank), rows only
  - ?rank=N         -> a window centred on canonical rank N (typed jump), rows only

Rows are numbered from `start_rank` stepping by +1 (forward) or -1 (inverted); ranks stay canonical (from
the top) either way, so an inverted board simply counts down.
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
    """One page/window of a game's leaderboard, as HTML."""

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

        # Jump to the viewer, or to a typed rank -> a window (rows only) that can scroll BOTH ways.
        if request.GET.get('around') == 'me' or request.GET.get('rank') is not None:
            if request.GET.get('around') == 'me':
                rank = svc.rank_for(game, profile, opts)
                if rank is None:                       # filtered out / not an owner -> nothing to jump to
                    return self._rows(request, game, opts, [], None, None, step, profile)
            else:
                rank = self._int(request.GET.get('rank'), 1)
            window = svc.page_at_rank(game, opts, rank)
            if window is None:
                return self._rows(request, game, opts, [], None, None, step, profile)
            rows, next_cursor, prev_cursor, start_rank, _total = window
            return self._rows(request, game, opts, rows, next_cursor, start_rank, step, profile, prev_cursor)

        # Scroll UP from a jump window -> the page above, prepended. Rows only, a prev marker if more, no
        # next marker (that end is already in the DOM).
        before = request.GET.get('before')
        if before:
            rows, prev_cursor = svc.page_before(game, opts, before)
            fromtop = self._int(request.GET.get('fromtop'), 1)
            # Number so the last prepended row sits immediately above the current top (fromtop - step).
            start_rank = fromtop - len(rows) * step
            return self._rows(request, game, opts, rows, None, start_rank, step, profile, prev_cursor)

        # Keyset continuation DOWN -> rows only, numbered from the caller-supplied rank.
        cursor = request.GET.get('after')
        if cursor:
            rows, next_cursor = svc.page(game, opts, cursor=cursor)
            start_rank = self._int(request.GET.get('from'), 1)
            return self._rows(request, game, opts, rows, next_cursor, start_rank, step, profile)

        # Full panel.
        rows, next_cursor = svc.page(game, opts)
        total = svc.board_size(game, opts)
        start_rank = total if opts.invert else 1
        viewer_rank = svc.rank_for(game, profile, opts)
        context = {
            'game': game,
            'opts': opts,
            'board_size': total,
            'viewer_rank': viewer_rank,
            'viewer_profile': profile,
            # The self-row renders whenever the viewer is ranked; the JS shows it only while their real
            # row is off screen, so it works whether or not they're on the first page.
            **self._rows_ctx(rows, next_cursor, start_rank, step, profile),
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
    def _int(raw, default, lo=1, hi=10_000_000):
        try:
            return max(lo, min(int(raw), hi))
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _rows_ctx(rows, next_cursor, start_rank, step, profile, prev_cursor=None):
        """Number the rows and derive the markers the scroller needs for the next page each way."""
        for i, row in enumerate(rows):
            row.rank = start_rank + i * step
            row.cursor = svc.encode_cursor(row)
        return {
            'rows': rows,
            'next_cursor': next_cursor,
            'next_from': (start_rank + len(rows) * step) if rows else None,
            'prev_cursor': prev_cursor,
            'prev_from': start_rank if rows else None,   # top-most row's rank; the next prepend goes above it
            'viewer_profile': profile,
        }

    def _rows(self, request, game, opts, rows, next_cursor, start_rank, step, profile, prev_cursor=None):
        ctx = {'game': game, 'opts': opts,
               **self._rows_ctx(rows, next_cursor, start_rank, step, profile, prev_cursor)}
        return render(request, ROWS_TEMPLATE, ctx)
