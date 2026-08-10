"""The game's community stats must come from denormed columns, never live aggregates.

These stats used to be computed per request behind an hourly cache. `total_earns`
counted EarnedTrophy across every trophy in the game (players x trophies rows), which on
a cold cache could outlast the gunicorn worker timeout on a popular title -- and the
hourly cache key meant the whole catalogue went cold on the hour, so a crawler walking
distinct games missed by construction.

The rebuilt hero and ratings panel now read the Game columns directly, so the
`_build_game_stats_context` wrapper (and its zero-query test) is gone on this branch; it
survives on `main` only until the pre-rebuild header partial is retired. What remains
worth pinning is that `recalc_earn_rates` actually populates the columns those templates
read, and that a budget-capped run resumes instead of leaving the tail at 0 forever.
"""
import pytest
from django.core.management import call_command
from django.utils import timezone
from datetime import timedelta

from tests.factories import (
    EarnedTrophyFactory, GameFactory, ProfileGameFactory, TrophyFactory,
)
pytestmark = pytest.mark.django_db


def test_recalc_populates_total_earns_across_all_trophies():
    """total_earns_count sums earned rows across every trophy in the game, and counts only
    rows with earned=True."""
    game = GameFactory()
    bronze = TrophyFactory(game=game, trophy_type='bronze')
    gold = TrophyFactory(game=game, trophy_type='gold')

    EarnedTrophyFactory(trophy=bronze, earned=True)
    EarnedTrophyFactory(trophy=bronze, earned=True)
    EarnedTrophyFactory(trophy=gold, earned=True)
    EarnedTrophyFactory(trophy=gold, earned=False)   # unearned must not count

    call_command('recalc_earn_rates')

    game.refresh_from_db()
    assert game.total_earns_count == 3


def test_recalc_populates_monthly_players_on_the_30_day_window():
    """monthly_players_count counts owners whose last_played falls inside 30 days."""
    game = GameFactory()
    now = timezone.now()
    ProfileGameFactory(game=game, last_played_date_time=now - timedelta(days=1))
    ProfileGameFactory(game=game, last_played_date_time=now - timedelta(days=29))
    ProfileGameFactory(game=game, last_played_date_time=now - timedelta(days=31))   # outside
    ProfileGameFactory(game=game, last_played_date_time=None)                       # never played

    call_command('recalc_earn_rates')

    game.refresh_from_db()
    assert game.monthly_players_count == 2
    assert game.played_count == 4      # the window does not change the owner denominator


def test_recalc_zeroes_new_stats_for_untouched_game():
    """A game nobody plays gets 0/0 rather than a crash on the empty aggregate."""
    game = GameFactory()

    call_command('recalc_earn_rates')

    game.refresh_from_db()
    assert game.total_earns_count == 0
    assert game.monthly_players_count == 0


# --- resume cursor -----------------------------------------------------------
#
# These matter because the game-detail header now READS these columns. Before, a game
# the budget never reached just meant stale-but-recomputed-on-demand stats; now it would
# serve 0 forever. A budget-capped run must therefore resume rather than restart.

def _patch_cursor(monkeypatch, cmd, stored):
    monkeypatch.setattr(cmd.Command, '_get_cursor', lambda self: stored.get('v'))
    monkeypatch.setattr(cmd.Command, '_set_cursor', lambda self, gid: stored.update(v=gid))


def _burn_budget_after_n_chunks(monkeypatch, cmd, n):
    """Drive Command._now() off chunk completions rather than real time.

    Patching stdlib time.monotonic would be shared with psycopg/redis, so any incidental
    call shifts a fixed tick sequence and the assertion flips. Advancing only when a chunk
    finishes is deterministic no matter how many times the clock is read.
    """
    clock = {'t': 0.0, 'done': 0}
    monkeypatch.setattr(cmd.Command, '_now', staticmethod(lambda: clock['t']))
    real_process = cmd.Command._process_chunk

    def counting_process(self, ids, dry):
        result = real_process(self, ids, dry)
        clock['done'] += 1
        if clock['done'] >= n:
            clock['t'] += 10_000     # budget exhausted from here on
        return result

    monkeypatch.setattr(cmd.Command, '_process_chunk', counting_process)


def test_budget_capped_run_resumes_from_the_cursor(monkeypatch):
    """A run that stops early records its position, and the next run starts past it."""
    from core.management.commands import recalc_earn_rates as cmd

    games = [GameFactory() for _ in range(6)]
    ids = sorted(g.id for g in games)

    stored = {}
    _patch_cursor(monkeypatch, cmd, stored)
    _burn_budget_after_n_chunks(monkeypatch, cmd, 1)

    call_command('recalc_earn_rates', chunk_size=2, max_minutes=1)

    assert stored['v'] == ids[1], 'cursor should sit on the last game of the processed chunk'

    # Second run: no deadline pressure, so it sweeps the remainder and completes the pass.
    monkeypatch.setattr(cmd.Command, '_now', staticmethod(lambda: 0.0))
    call_command('recalc_earn_rates', chunk_size=2, max_minutes=30)

    assert stored['v'] is None, 'a completed pass clears the cursor'


def test_zero_chunk_run_leaves_the_cursor_intact(monkeypatch):
    """A budget that trips before the FIRST chunk must not clear the stored cursor.

    Writing None here would send the next run back to game id 0, reinstating the
    never-reach-the-tail bug the cursor exists to prevent.
    """
    from core.management.commands import recalc_earn_rates as cmd

    for _ in range(4):
        GameFactory()

    stored = {'v': 4242}
    _patch_cursor(monkeypatch, cmd, stored)
    # The first read establishes `start`; every read after it is past the deadline, so the
    # loop breaks on its very first check. Safe to count calls here precisely because
    # _now() is a seam on the command -- psycopg and redis never reach it.
    reads = {'n': 0}

    def clock():
        reads['n'] += 1
        return 0.0 if reads['n'] == 1 else 10_000.0

    monkeypatch.setattr(cmd.Command, '_now', staticmethod(clock))

    call_command('recalc_earn_rates', chunk_size=2, max_minutes=1)

    assert stored['v'] == 4242, 'a zero-chunk run must leave the cursor untouched'


def test_empty_catalogue_clears_the_cursor(monkeypatch):
    """Zero games is a legitimately completed pass, so the cursor resets."""
    from core.management.commands import recalc_earn_rates as cmd

    stored = {'v': 4242}
    _patch_cursor(monkeypatch, cmd, stored)

    call_command('recalc_earn_rates')

    assert stored['v'] is None


def test_explicit_game_ids_run_does_not_touch_the_cursor(monkeypatch):
    """An ad-hoc --game-ids pass must not perturb the nightly sweep's position."""
    from core.management.commands import recalc_earn_rates as cmd

    game = GameFactory()
    stored = {'v': 12345}
    monkeypatch.setattr(cmd.Command, '_get_cursor', lambda self: stored.get('v'))
    monkeypatch.setattr(cmd.Command, '_set_cursor', lambda self, gid: stored.update(v=gid))

    call_command('recalc_earn_rates', game_ids=[game.id])

    assert stored['v'] == 12345


def test_dry_run_does_not_advance_the_cursor(monkeypatch):
    """--dry-run reports without writing, cursor included."""
    from core.management.commands import recalc_earn_rates as cmd

    GameFactory()
    stored = {'v': 999}
    monkeypatch.setattr(cmd.Command, '_get_cursor', lambda self: stored.get('v'))
    monkeypatch.setattr(cmd.Command, '_set_cursor', lambda self, gid: stored.update(v=gid))

    call_command('recalc_earn_rates', dry_run=True)

    assert stored['v'] == 999
