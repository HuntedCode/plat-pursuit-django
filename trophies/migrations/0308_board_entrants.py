"""Denormalized board entrant counts on BadgeSeries and Job, WITH the backfill.

The backfill is not optional. These columns default to 0, and the directories gate on
`entrants >= BOARD_MIN_ENTRANTS` -- so between this migration and the first nightly run, EVERY badge and
job board would fail its own gate and both directories would render empty. A page that correctly reports
"no board has enough hunters yet" while every board does is the worst kind of wrong: it looks like a
considered empty state.

Same reasoning as `Game.played_count`, which these mirror.
"""

from django.db import migrations, models
from django.db.models import Count


def backfill_entrants(apps, schema_editor):
    """One GROUP BY per board kind, then a bulk write. Identical logic to `recalc_board_entrants`, but
    written against the historical model state -- a migration must not import app code that can change
    under it."""
    BadgeSeries = apps.get_model('trophies', 'BadgeSeries')
    Job = apps.get_model('trophies', 'Job')
    SeriesBadgeStanding = apps.get_model('trophies', 'SeriesBadgeStanding')
    ProfileJobXP = apps.get_model('trophies', 'ProfileJobXP')

    series_counts = dict(
        SeriesBadgeStanding.objects.values('series_slug')
        .annotate(n=Count('id')).values_list('series_slug', 'n')
    )
    changed = []
    for series in BadgeSeries.objects.only('id', 'series_slug', 'entrants'):
        fresh = series_counts.get(series.series_slug, 0)
        if series.entrants != fresh:
            series.entrants = fresh
            changed.append(series)
    if changed:
        BadgeSeries.objects.bulk_update(changed, ['entrants'], batch_size=500)

    job_counts = dict(
        ProfileJobXP.objects.filter(total_xp__gt=0).values('job_id')
        .annotate(n=Count('id')).values_list('job_id', 'n')
    )
    changed = []
    for job in Job.objects.only('slug', 'entrants'):
        fresh = job_counts.get(job.slug, 0)
        if job.entrants != fresh:
            job.entrants = fresh
            changed.append(job)
    if changed:
        Job.objects.bulk_update(changed, ['entrants'], batch_size=500)


def noop_reverse(apps, schema_editor):
    """Reverse: the columns go with the AddFields, so there is nothing to undo separately."""
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("trophies", "0307_partial_board_indexes"),
    ]

    operations = [
        migrations.AddField(
            model_name="badgeseries",
            name="entrants",
            field=models.PositiveIntegerField(
                default=0,
                help_text="Denormalized count of hunters on this board. RECOMPUTED FROM SCRATCH nightly by `recalc_board_entrants` -- never incremented. See the model docstring.",
            ),
        ),
        migrations.AddField(
            model_name="job",
            name="entrants",
            field=models.PositiveIntegerField(
                default=0,
                help_text="Denormalized count of hunters on this board. RECOMPUTED FROM SCRATCH nightly by `recalc_board_entrants` -- never incremented. See the model docstring.",
            ),
        ),
        migrations.AddIndex(
            model_name="badgeseries",
            index=models.Index(fields=["-entrants"], name="badgeseries_entrants_idx"),
        ),
        migrations.AddIndex(
            model_name="job",
            index=models.Index(fields=["-entrants"], name="job_entrants_idx"),
        ),
        # AFTER the indexes, so the bulk_update writes into them rather than forcing a rebuild.
        migrations.RunPython(backfill_entrants, noop_reverse),
    ]
