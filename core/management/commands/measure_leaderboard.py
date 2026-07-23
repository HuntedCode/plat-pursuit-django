"""
Read-only feasibility probe for per-game leaderboards.

Answers three questions before we commit to an architecture:
  1. How much data is there (ProfileGame rows, per-game player counts)?
  2. How fast is the leaderboard query TODAY, without a composite index?
  3. How fast is a DEEP rank lookup (the one operation Redis would accelerate)?

The leaderboard ordering is progress DESC, then earliest most_recent_trophy_date,
then profile_id as a unique final key -- without that third key the ordering is
not total, so pagination can skip/duplicate rows and ranks flicker between calls.

Safe on production: every query is read-only, and the row count uses the planner
estimate rather than a COUNT(*) seq-scan over the biggest table in the schema.

Usage:
    python manage.py measure_leaderboard
    python manage.py measure_leaderboard --games 5 --depth 20000
    python manage.py measure_leaderboard --explain
"""
import time

from django.core.management.base import BaseCommand
from django.db import connection

# The leaderboard page. Kept as raw SQL so EXPLAIN reports exactly what ships.
PAGE_SQL = """
    SELECT profile_id, progress, most_recent_trophy_date
    FROM trophies_profilegame
    WHERE game_id = %s AND hidden_flag = false AND user_hidden = false
    ORDER BY progress DESC, most_recent_trophy_date ASC NULLS LAST, profile_id ASC
    LIMIT 20
"""

# "How many players are ahead of me?" -- the O(rank) count that decides whether
# we need Redis for rank lookups.
RANK_SQL = """
    SELECT COUNT(*) FROM trophies_profilegame
    WHERE game_id = %s AND hidden_flag = false AND user_hidden = false
      AND (progress > %s
           OR (progress = %s AND most_recent_trophy_date IS NOT NULL
               AND most_recent_trophy_date < %s)
           OR (progress = %s AND most_recent_trophy_date = %s AND profile_id < %s))
"""


class Command(BaseCommand):
    help = 'Read-only probe: is a per-game leaderboard feasible on the DB alone?'

    def add_arguments(self, parser):
        parser.add_argument('--games', type=int, default=3,
                            help='How many of the biggest games to probe (default 3)')
        parser.add_argument('--depth', type=int, default=5000,
                            help='Rank depth to simulate for the worst-case lookup (default 5000)')
        parser.add_argument('--explain', action='store_true',
                            help='Print EXPLAIN (ANALYZE, BUFFERS) for the biggest game')

    # -- helpers ---------------------------------------------------------

    def _q(self, sql, params=None):
        with connection.cursor() as cur:
            cur.execute(sql, params or [])
            return cur.fetchall()

    def _timed(self, sql, params=None):
        """Run once to warm caches, then time the second run."""
        with connection.cursor() as cur:
            cur.execute(sql, params or [])
            cur.fetchall()
            start = time.perf_counter()
            cur.execute(sql, params or [])
            rows = cur.fetchall()
            return rows, (time.perf_counter() - start) * 1000

    def _head(self, text):
        self.stdout.write('')
        self.stdout.write(self.style.MIGRATE_HEADING(text))

    # -- main ------------------------------------------------------------

    def handle(self, *args, **options):
        self._head('1. SCALE')

        est = self._q("SELECT reltuples::bigint FROM pg_class WHERE relname = %s",
                      ['trophies_profilegame'])[0][0]
        size = self._q("SELECT pg_size_pretty(pg_total_relation_size('trophies_profilegame'))")[0][0]
        self.stdout.write(f"  ProfileGame rows (estimate)  : {int(est or 0):,}")
        self.stdout.write(f"  ProfileGame size (+ indexes) : {size}")

        row = self._q("""
            SELECT COUNT(*),
                   COALESCE(MAX(played_count), 0),
                   COALESCE(ROUND(AVG(played_count)), 0),
                   COUNT(*) FILTER (WHERE played_count >= 1000),
                   COUNT(*) FILTER (WHERE played_count >= 10000),
                   COUNT(*) FILTER (WHERE played_count >= 50000)
            FROM trophies_game
        """)[0]
        self.stdout.write(f"  Games total                  : {int(row[0]):,}")
        self.stdout.write(f"  Biggest game (played_count)  : {int(row[1]):,}")
        self.stdout.write(f"  Average players per game     : {int(row[2]):,}")
        self.stdout.write(f"  Games >=1k / >=10k / >=50k   : "
                          f"{int(row[3]):,} / {int(row[4]):,} / {int(row[5]):,}")

        pct = self._q("""
            SELECT percentile_disc(0.50) WITHIN GROUP (ORDER BY played_count),
                   percentile_disc(0.90) WITHIN GROUP (ORDER BY played_count),
                   percentile_disc(0.99) WITHIN GROUP (ORDER BY played_count)
            FROM trophies_game
        """)[0]
        self.stdout.write("  played_count p50/p90/p99     : "
                          f"{int(pct[0] or 0):,} / {int(pct[1] or 0):,} / {int(pct[2] or 0):,}")

        # Redis sizing, if we ever went that way: ~100 bytes per member, ids only.
        est_rows = int(est or 0)
        self.stdout.write(f"\n  If EVERY game were held in Redis (ids only, ~100B/member):")
        self.stdout.write(f"    ~{est_rows * 100 / 1_000_000_000:.2f} GB")
        self.stdout.write(f"  Capped at top 1,000 per game for the 'hot' games only:")
        self.stdout.write(f"    500 games  ~{500 * 1000 * 100 / 1_000_000:.0f} MB"
                          f"   |   2,000 games  ~{2000 * 1000 * 100 / 1_000_000:.0f} MB")

        self._head('2. LEADERBOARD QUERY TODAY (no composite index yet)')

        tops = self._q("""
            SELECT id, np_communication_id, played_count
            FROM trophies_game
            WHERE played_count > 0
            ORDER BY played_count DESC
            LIMIT %s
        """, [options['games']])

        if not tops:
            self.stdout.write(self.style.WARNING('  No games with players. Nothing to measure.'))
            return

        worst_page = 0.0
        worst_rank = 0.0
        for game_id, npid, players in tops:
            players = int(players or 0)

            # played_count counts EVERY ProfileGame row; the board filters hidden ones out. Size the rank
            # probe off the eligible rows or the offset overshoots and silently measures nothing -- and
            # report both, because the gap is what the leaderboard header has to reconcile.
            eligible = int(self._q("""
                SELECT COUNT(*) FROM trophies_profilegame
                WHERE game_id = %s AND hidden_flag = false AND user_hidden = false
            """, [game_id])[0][0])
            hidden = players - eligible
            self.stdout.write(f"\n  {npid}  (id={game_id})")
            self.stdout.write(f"    played_count     : {players:,}")
            self.stdout.write(f"    on the board     : {eligible:,}"
                              + (f"   ({hidden:,} hidden/excluded)" if hidden else ""))

            _, ms = self._timed(PAGE_SQL, [game_id])
            worst_page = max(worst_page, ms)
            self.stdout.write(f"    {'top-20 page':<16}: {ms:8.1f} ms")

            depth = min(options['depth'], max(eligible - 1, 0))
            if depth <= 0:
                self.stdout.write("    rank lookup      : skipped (too few players)")
                continue
            sample = self._q(PAGE_SQL.replace('LIMIT 20', 'OFFSET %s LIMIT 1'), [game_id, depth])
            if not sample:
                self.stdout.write("    rank lookup      : skipped (no row at that depth)")
                continue
            pid, prog, dt = sample[0]
            rows, ms = self._timed(RANK_SQL, [game_id, prog, prog, dt, prog, dt, pid])
            worst_rank = max(worst_rank, ms)
            label = f"rank @ ~{depth:,}"
            self.stdout.write(f"    {label:<16}: {ms:8.1f} ms   "
                              f"({int(rows[0][0]):,} players ahead)")

        if options['explain']:
            self._head('3. EXPLAIN (biggest game, top-20 page)')
            with connection.cursor() as cur:
                cur.execute('EXPLAIN (ANALYZE, BUFFERS) ' + PAGE_SQL, [tops[0][0]])
                for line in cur.fetchall():
                    self.stdout.write(f"  {line[0]}")

        self._head('VERDICT')
        if worst_rank == 0.0:
            # Don't let an unmeasured rank read as a fast one.
            self.stdout.write(self.style.WARNING(
                "  Rank lookup was never measured (every probed game had too few eligible players).\n"
                "  Re-run with a smaller --depth to exercise it."))
        if worst_page < 25 and worst_rank < 100:
            self.stdout.write(self.style.SUCCESS(
                f"  DB-only is fine. Worst page {worst_page:.0f}ms, worst rank {worst_rank:.0f}ms.\n"
                "  Add the composite index and skip Redis entirely for now."))
        elif worst_page < 25:
            self.stdout.write(self.style.WARNING(
                f"  Pages are fast ({worst_page:.0f}ms) but deep rank is slow ({worst_rank:.0f}ms).\n"
                "  Add the composite index, then re-run: it targets exactly this."))
        else:
            self.stdout.write(self.style.WARNING(
                f"  Worst page {worst_page:.0f}ms, worst rank {worst_rank:.0f}ms.\n"
                "  Add the composite index and re-run before considering Redis."))
        self.stdout.write(
            "  Index to add: (game_id, progress DESC, most_recent_trophy_date ASC, profile_id)\n"
            "  In the EXPLAIN, a Sort node with a large rows= is what that index removes.")
