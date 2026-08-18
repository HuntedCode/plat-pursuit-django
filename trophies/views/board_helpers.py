"""Shared helpers for the LEADERBOARD family: Global Boards, badge series, job.

All three are virtualized the same way -- a full-height spacer with only the visible rows in the DOM --
so all three answer the same kind of request: "give me display positions [start, start+count)". This
module holds the parsing of that request, because the clamping is the part with teeth and three
hand-rolled copies of it is three chances to leave one unbounded.

GAME DETAIL parses its own (`game_leaderboard_views._int`) and is deliberately left alone. Its `range`
and `count` are exactly equivalent to these, but it also takes an `at` param -- a direct offset into the
board for its search -- which leans on `_int`'s DEFAULT upper bound. `clamped_int` defaults `hi` to None,
i.e. unbounded, so a mechanical swap would hand `?at=<huge>` an uncapped slice. Folding it in means
giving `at` an explicit bound first.
"""


def clamped_int(raw, default, lo=1, hi=None):
    """Parse `raw` to an int, clamped to [lo, hi]. Unparseable -> `default`, unclamped.

    `default` is trusted (it comes from the view, not the request), which is why it is returned as-is
    rather than clamped -- a view whose default is out of its own range has a bug the clamp would hide.
    """
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    value = max(lo, value)
    return min(value, hi) if hi is not None else value


#: Deepest reachable row. `range` becomes a SQL OFFSET, and Postgres walks every skipped row to honour it
#: -- so on a public URL this is the ceiling on how much of a board an anonymous request can make the
#: database walk.
#:
#: The real cost of `OFFSET n` is `min(n, board_size)` rows, because the scan stops when the rows run out.
#: So a cap ONLY does anything when it sits below the board it is applied to, and a cap "past any real
#: board" -- which is what 100M was, briefly -- is by construction a cap that never binds. It replaced
#: two caps that DID bind (badge's 10,000 and job's 400 pages x 25 = 9,975), which made those two boards
#: measurably cheaper to attack than before.
#:
#: 1M is the compromise, and the trade is explicit: every board is bounded by the LINKED PROFILE count
#: (`badge_leaderboards._linked` gates them all), so 1M is roughly twenty times the current population --
#: high enough that no real reader ever meets it, low enough to be a real bound on a board that grows.
#: The old 10,000 cannot come back: a hunter ranked #40,000 on the Trophies board has to be able to reach
#: their own row, which is the entire dead end this rebuild removed.
MAX_START = 1_000_000

#: Hard ceiling on `count`. The client only ever asks for the board's own page size; this bounds what a
#: crafted URL can ask for, so nobody hydrates a whole board in one read.
MAX_COUNT = 200

#: Rows per window, for every board. It was declared three times -- `OverallBadgeLeaderboardsView
#: .paginate_by`, `BadgeRanksPanelView.PAGE_SIZE`, `JobRanksPanelView.PAGE_SIZE` -- which is three
#: numbers that have to stay equal for no reason anybody would remember. They are stamped into the DOM
#: and read back by the client, so a board whose page size disagrees with its own fetch granularity does
#: not error: it shows GAPS in the rows a reader scrolls past.
PAGE_SIZE = 50


def window_params(request, default_count, max_count=MAX_COUNT, max_start=MAX_START):
    """`?range=` / `?count=` for one window of a virtualized board -> `(start, count)`, both clamped.

    `start` is a 1-indexed DISPLAY POSITION, not a page number: the client asks for the rows it is about
    to show, and the server has no idea which screenful that is.
    """
    start = clamped_int(request.GET.get('range'), 1, lo=1, hi=max_start)
    count = clamped_int(request.GET.get('count'), default_count, lo=1, hi=max_count)
    return start, count
