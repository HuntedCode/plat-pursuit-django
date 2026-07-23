"""Game leaderboard panel (the Leaderboard tab on game detail).

Served as an HTML partial rather than JSON because the panel is lazy-loaded: it is deliberately NOT
server-rendered with the rest of the page, since it is the only panel whose cost scales with a game's
popularity and most visitors arrive from search wanting trophy info, never opening it.

Two response shapes from one endpoint:
  - no ?after=  -> the whole panel (header + first page + the viewer's own standing)
  - ?after=...  -> just the next page of rows, for the infinite scroller to append
"""
import logging

from django.shortcuts import get_object_or_404, render
from django.views import View

from trophies.models import Game
from trophies.services import game_leaderboard_service as svc

logger = logging.getLogger('psn_api')

ROWS_TEMPLATE = 'trophies/partials/game_detail/_leaderboard_rows.html'
PANEL_TEMPLATE = 'trophies/partials/game_detail/_leaderboard_panel.html'


class GameLeaderboardView(View):
    """One keyset page of a game's leaderboard, as HTML."""

    def get(self, request, np_communication_id):
        game = get_object_or_404(Game, np_communication_id=np_communication_id)
        cursor = request.GET.get('after')
        # Resolved for EVERY shape, not just the panel: the rows partial needs it to mark the viewer's
        # own row, and without it appended pages would silently lose the highlight.
        profile = self._viewer_profile(request)

        # "Jump to my rank": a window centred on the viewer, replacing the list. Paging forward until we
        # reach someone ranked 900th would be absurd, so the server opens the window directly.
        if request.GET.get('around') == 'me':
            window = svc.page_around(game, profile)
            if window is None:
                return render(request, ROWS_TEMPLATE,
                              {'game': game, 'rows': [], 'next_cursor': None, 'viewer_profile': profile})
            rows, next_cursor, start_rank = window
            return render(request, ROWS_TEMPLATE, {
                'game': game,
                'rows': self._decorate(rows, start_rank),
                'next_cursor': next_cursor,
                'viewer_profile': profile,
            })

        rows, next_cursor = svc.page(game, cursor=cursor)
        context = {
            'game': game,
            'rows': self._decorate(rows, self._start_rank(request)),
            'next_cursor': next_cursor,
            'viewer_profile': profile,
        }

        # Continuation: rows only. The scroller appends these, so anything else would duplicate chrome.
        if cursor:
            return render(request, ROWS_TEMPLATE, context)

        viewer_rank = svc.rank_for(game, profile)
        context.update({
            'board_size': svc.board_size(game),
            'viewer_rank': viewer_rank,
            # Only pin a self-row when they're deep enough to be off the first page; otherwise their
            # highlighted row is already visible and a duplicate would be noise.
            'viewer_offscreen': bool(viewer_rank and viewer_rank > len(rows)),
        })
        return render(request, PANEL_TEMPLATE, context)

    @staticmethod
    def _viewer_profile(request):
        if not request.user.is_authenticated:
            return None
        profile = getattr(request.user, 'profile', None)
        return profile if profile and profile.is_linked else None

    @staticmethod
    def _start_rank(request):
        """Rank of this page's first row, supplied by the scroller as it appends.

        Display only. Deriving it server-side would mean an O(rank) count on every page fetch, and the
        worst a tampered value can do is show that one viewer wrong numbers on their own screen.
        """
        try:
            return max(1, min(int(request.GET.get('from', 1)), 10_000_000))
        except (TypeError, ValueError):
            return 1

    @staticmethod
    def _decorate(rows, start_rank):
        """Attach rank + the cursor each row would produce, so the template doesn't re-derive either."""
        for offset, row in enumerate(rows):
            row.rank = start_rank + offset
            row.cursor = svc.encode_cursor(row)
        return rows
