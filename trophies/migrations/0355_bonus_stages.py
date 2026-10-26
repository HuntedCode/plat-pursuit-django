# Bonus stages replace stage 0 (owner, 2026-10-25).
#
# Stage 0 held every optional game of a series (region-locked releases, mostly) in ONE stage, and a stage is
# cleared by any one of its games, so finishing one of them cleared the lot for Mastery. It also never showed on
# the badge page. A bonus stage is an ordinary numbered stage with `is_bonus` set: shown, one per title, never
# counting toward the badge, only toward Mastery.
#
# Each stage 0 moves to the end of its series (its last stage number + 1) as a bonus stage, games and all. The
# owner then splits the multi-title ones into one stage per title by hand, in admin. Stage numbers start at 1
# from here (the check constraint), so the flag is the only way to make a stage optional.
#
# Reverse: each series' one bonus stage goes back to stage 0. It REFUSES once a series has been split into
# several: there is only one stage 0 to put them in, and the rest would quietly become requirements of the
# badge (revoking holds on the next evaluation). Merge them back into one bonus stage first.

import django.core.validators
from django.db import migrations, models
from django.db.models import Count, Max


def stage_zero_to_bonus(apps, schema_editor):
    Stage = apps.get_model('trophies', 'Stage')
    for stage in Stage.objects.filter(stage_number=0):
        last = Stage.objects.filter(series_slug=stage.series_slug).aggregate(n=Max('stage_number'))['n'] or 0
        stage.stage_number = last + 1
        stage.is_bonus = True
        stage.save(update_fields=['stage_number', 'is_bonus'])


def bonus_to_stage_zero(apps, schema_editor):
    Stage = apps.get_model('trophies', 'Stage')
    split = sorted(
        Stage.objects.filter(is_bonus=True).values('series_slug').annotate(n=Count('id')).filter(n__gt=1)
        .values_list('series_slug', flat=True)
    )
    if split:
        raise RuntimeError(
            f"Cannot reverse 0355: these series have several bonus stages, and all but one would become "
            f"required stages: {', '.join(split)}. Merge each into one bonus stage first."
        )
    for stage in Stage.objects.filter(is_bonus=True):
        stage.stage_number = 0
        stage.save(update_fields=['stage_number'])


class Migration(migrations.Migration):

    dependencies = [
        ('trophies', '0354_ugb_mastered_idx'),
    ]

    operations = [
        migrations.AddField(
            model_name='stage',
            name='is_bonus',
            field=models.BooleanField(default=False, help_text="Bonus stage: doesn't count toward the badge, only toward Mastery (e.g. a region-locked game)."),
        ),
        migrations.RunPython(stage_zero_to_bonus, bonus_to_stage_zero),
        migrations.AlterField(
            model_name='stage',
            name='stage_number',
            field=models.IntegerField(help_text='Stage number, from 1.', validators=[django.core.validators.MinValueValidator(1)]),
        ),
        migrations.AddConstraint(
            model_name='stage',
            constraint=models.CheckConstraint(condition=models.Q(('stage_number__gte', 1)), name='stage_number_from_one'),
        ),
    ]
