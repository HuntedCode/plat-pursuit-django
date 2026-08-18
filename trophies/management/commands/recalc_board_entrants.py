"""Recompute `BadgeSeries.entrants` and `Job.entrants` from scratch.

These denorms exist so the board directories can gate and sort on an indexed column instead of
aggregating the whole standing table on every request (~91 ms x2 per Job Boards view, re-run on every
infinite-scroll page). `Game.played_count` is the same idea and the reason Game Boards was already fast.

RECOMPUTE-FROM-SCRATCH, deliberately: two GROUP BYs and a bulk write, never an increment. Incrementally
maintained counters in this codebase have drifted every time -- `earned_count` came to mean two things
because it tracked create/delete but not lapse, `required_stages` refreshed only out-of-band and silently
desynced megamix award math, and `earned_count` drifts down-only to this day. A column rebuilt from an
aggregate cannot drift; there is no state to get wrong.

ORDERING MATTERS: badge entrants count `SeriesBadgeStanding`, which `evaluate_badges` writes. Run this
AFTER it, or the counts describe a half-rewritten table. The `nightly` command sequences that properly;
running this standalone is for backfills and spot-fixes.
"""
from django.core.management.base import BaseCommand
from django.db.models import Count

from trophies.models import BadgeSeries, Job, ProfileJobXP, SeriesBadgeStanding


def recalc_badge_series_entrants():
    """One GROUP BY over the standings, one bulk write. Returns (updated, total)."""
    counts = dict(
        SeriesBadgeStanding.objects
        .values('series_slug').annotate(n=Count('id'))
        .values_list('series_slug', 'n')
    )
    rows = list(BadgeSeries.objects.only('id', 'series_slug', 'entrants'))
    changed = []
    for series in rows:
        fresh = counts.get(series.series_slug, 0)
        if series.entrants != fresh:
            series.entrants = fresh
            changed.append(series)
    if changed:
        BadgeSeries.objects.bulk_update(changed, ['entrants'], batch_size=500)
    return len(changed), len(rows)


def recalc_job_entrants():
    """Same shape for jobs. Counts only entrants with XP, matching the board's own membership rule
    (`job_rows` filters `total_xp__gt=0`) -- a zero-XP row is a real state the board does not show."""
    counts = dict(
        ProfileJobXP.objects.filter(total_xp__gt=0)
        .values('job_id').annotate(n=Count('id'))
        .values_list('job_id', 'n')
    )
    rows = list(Job.objects.only('slug', 'entrants'))
    changed = []
    for job in rows:
        fresh = counts.get(job.slug, 0)
        if job.entrants != fresh:
            job.entrants = fresh
            changed.append(job)
    if changed:
        Job.objects.bulk_update(changed, ['entrants'], batch_size=500)
    return len(changed), len(rows)


class Command(BaseCommand):
    help = "Recompute the denormalized board entrant counts on BadgeSeries and Job."

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true',
                            help='Report what would change without writing.')

    def handle(self, *args, **opts):
        if opts['dry_run']:
            # Count without writing: compare the stored value against a fresh aggregate.
            series_counts = dict(
                SeriesBadgeStanding.objects.values('series_slug')
                .annotate(n=Count('id')).values_list('series_slug', 'n')
            )
            job_counts = dict(
                ProfileJobXP.objects.filter(total_xp__gt=0).values('job_id')
                .annotate(n=Count('id')).values_list('job_id', 'n')
            )
            series_drift = sum(
                1 for s in BadgeSeries.objects.only('series_slug', 'entrants')
                if s.entrants != series_counts.get(s.series_slug, 0)
            )
            job_drift = sum(
                1 for j in Job.objects.only('slug', 'entrants')
                if j.entrants != job_counts.get(j.slug, 0)
            )
            self.stdout.write(f"DRY RUN: {series_drift} series and {job_drift} jobs would change.")
            return

        series_changed, series_total = recalc_badge_series_entrants()
        job_changed, job_total = recalc_job_entrants()
        self.stdout.write(self.style.SUCCESS(
            f"Entrants recomputed: {series_changed}/{series_total} series, {job_changed}/{job_total} jobs."
        ))
