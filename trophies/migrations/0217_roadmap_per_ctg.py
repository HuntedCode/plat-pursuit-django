"""Collapse RoadmapTab into Roadmap so each ConceptTrophyGroup gets its
own Roadmap.

Behavior change: each CTG (base game + each DLC) becomes its own Roadmap
with independent status, lock, contributors, and notes. Concept-level
navigation still groups them via the `concept` FK.

Existing dev roadmap data is wiped at the start of this migration. Since
no real authored content has been published yet (we only have test data),
splitting the existing N-tab roadmaps into N separate roadmaps in code
isn't worth the complexity vs starting clean.

Affected child relations:
  - RoadmapStep.tab -> RoadmapStep.roadmap
  - TrophyGuide.tab -> TrophyGuide.roadmap
  - RoadmapNote.target_tab removed (kind='tab' folded into kind='guide')
  - RoadmapEditLock.payload_version bumped to 2
  - RoadmapRevision.snapshot shape changes (legacy snapshots wiped)
"""
import django.core.validators
from django.db import migrations, models


def wipe_roadmap_data(apps, schema_editor):
    """Drop every roadmap-shaped row before the schema rewrite.

    Order matters because cascades go top-down. We delete locks + revisions
    + notes first so they don't pull anything unexpected on the way out,
    then clear the content tables, then the parents.
    """
    RoadmapEditLock = apps.get_model('trophies', 'RoadmapEditLock')
    RoadmapRevision = apps.get_model('trophies', 'RoadmapRevision')
    RoadmapNote = apps.get_model('trophies', 'RoadmapNote')
    RoadmapNoteRead = apps.get_model('trophies', 'RoadmapNoteRead')
    RoadmapStepTrophy = apps.get_model('trophies', 'RoadmapStepTrophy')
    RoadmapStep = apps.get_model('trophies', 'RoadmapStep')
    TrophyGuide = apps.get_model('trophies', 'TrophyGuide')
    RoadmapTab = apps.get_model('trophies', 'RoadmapTab')
    Roadmap = apps.get_model('trophies', 'Roadmap')

    RoadmapEditLock.objects.all().delete()
    RoadmapRevision.objects.all().delete()
    RoadmapNote.objects.all().delete()
    RoadmapNoteRead.objects.all().delete()
    RoadmapStepTrophy.objects.all().delete()
    RoadmapStep.objects.all().delete()
    TrophyGuide.objects.all().delete()
    RoadmapTab.objects.all().delete()
    Roadmap.objects.all().delete()


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    # Disable the implicit per-migration transaction. PostgreSQL refuses
    # ALTER TABLE on a table that has pending trigger events from earlier
    # rows-delete in the same transaction; running each operation in its
    # own transaction lets the data wipe commit before the schema swaps.
    atomic = False

    dependencies = [
        ('trophies', '0216_roadmap_galleries'),
    ]

    operations = [
        # 1) Clear all existing roadmap-shaped rows so the schema swaps that
        # follow can run without having to relocate child rows or worry
        # about NOT NULL columns being satisfied.
        migrations.RunPython(wipe_roadmap_data, reverse_code=noop),

        # 2) Drop the old indexes/constraints that reference fields about
        # to be removed.
        migrations.RemoveIndex(
            model_name='roadmapnote',
            name='roadmap_note_tab_idx',
        ),
        migrations.RemoveConstraint(
            model_name='roadmapnote',
            name='roadmap_note_target_consistency',
        ),

        # 3) RoadmapNote: drop the tab-target FK and add the v2 constraint.
        migrations.RemoveField(
            model_name='roadmapnote',
            name='target_tab',
        ),
        migrations.AlterField(
            model_name='roadmapnote',
            name='target_kind',
            field=models.CharField(
                choices=[
                    ('guide', 'Guide'),
                    ('step', 'Step'),
                    ('trophy_guide', 'Trophy guide'),
                ],
                max_length=20,
            ),
        ),
        migrations.AddConstraint(
            model_name='roadmapnote',
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(target_kind='guide', target_step__isnull=True, target_trophy_guide__isnull=True)
                    | models.Q(target_kind='step', target_step__isnull=False, target_trophy_guide__isnull=True)
                    | models.Q(target_kind='trophy_guide', target_step__isnull=True, target_trophy_guide__isnull=False)
                ),
                name='roadmap_note_target_consistency',
            ),
        ),

        # 4) Steps and TrophyGuides: drop their tab FK in favor of a direct
        # roadmap FK. AlterField on `tab` would normally work as a rename,
        # but the FK target also changes (RoadmapTab → Roadmap), so we add
        # the new field and drop the old one in two steps.
        migrations.AlterUniqueTogether(
            name='trophyguide',
            unique_together=set(),
        ),
        migrations.RemoveField(
            model_name='roadmapstep',
            name='tab',
        ),
        migrations.RemoveField(
            model_name='trophyguide',
            name='tab',
        ),

        # 5) Drop RoadmapTab itself.
        migrations.DeleteModel(
            name='RoadmapTab',
        ),

        # 6) Roadmap: switch concept from OneToOne to FK and add the
        # CTG + content fields previously on RoadmapTab.
        migrations.AlterField(
            model_name='roadmap',
            name='concept',
            field=models.ForeignKey(
                on_delete=models.deletion.CASCADE,
                related_name='roadmaps',
                to='trophies.concept',
            ),
        ),
        migrations.AddField(
            model_name='roadmap',
            name='concept_trophy_group',
            field=models.ForeignKey(
                on_delete=models.deletion.CASCADE,
                related_name='roadmaps',
                to='trophies.concepttrophygroup',
            ),
            # No data exists at this point (we wiped above), so NOT NULL
            # without a default is safe.
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='roadmap',
            name='last_edited_by',
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=models.deletion.SET_NULL,
                related_name='last_edited_roadmaps',
                to='trophies.profile',
            ),
        ),
        migrations.AddField(
            model_name='roadmap',
            name='general_tips',
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name='roadmap',
            name='youtube_url',
            field=models.URLField(blank=True),
        ),
        migrations.AddField(
            model_name='roadmap',
            name='difficulty',
            field=models.PositiveSmallIntegerField(
                blank=True, null=True,
                help_text='Author-assessed difficulty (1-10). Distinct from community ratings.',
                validators=[
                    django.core.validators.MinValueValidator(1),
                    django.core.validators.MaxValueValidator(10),
                ],
            ),
        ),
        migrations.AddField(
            model_name='roadmap',
            name='estimated_hours',
            field=models.PositiveSmallIntegerField(
                blank=True, null=True,
                help_text='Author-estimated hours to complete this trophy group.',
            ),
        ),
        migrations.AddField(
            model_name='roadmap',
            name='missable_count',
            field=models.PositiveSmallIntegerField(
                default=0,
                help_text='Number of missable trophies in this group.',
            ),
        ),
        migrations.AddField(
            model_name='roadmap',
            name='online_required',
            field=models.BooleanField(
                default=False,
                help_text='Whether online play is required for trophies in this group.',
            ),
        ),
        migrations.AddField(
            model_name='roadmap',
            name='min_playthroughs',
            field=models.PositiveSmallIntegerField(
                default=1,
                help_text='Minimum playthroughs required for all trophies.',
            ),
        ),

        # 7) Re-attach the RoadmapStep / TrophyGuide content tables to
        # Roadmap directly.
        migrations.AddField(
            model_name='roadmapstep',
            name='roadmap',
            field=models.ForeignKey(
                on_delete=models.deletion.CASCADE,
                related_name='steps',
                to='trophies.roadmap',
            ),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='trophyguide',
            name='roadmap',
            field=models.ForeignKey(
                on_delete=models.deletion.CASCADE,
                related_name='trophy_guides',
                to='trophies.roadmap',
            ),
            preserve_default=False,
        ),
        migrations.AlterUniqueTogether(
            name='trophyguide',
            unique_together={('roadmap', 'trophy_id')},
        ),

        # 8) Roadmap composite index + uniqueness on (concept, ctg).
        migrations.AlterUniqueTogether(
            name='roadmap',
            unique_together={('concept', 'concept_trophy_group')},
        ),
        migrations.AlterModelOptions(
            name='roadmap',
            options={
                'ordering': [
                    'concept_trophy_group__sort_order',
                    'concept_trophy_group__trophy_group_id',
                ],
            },
        ),
        migrations.AddIndex(
            model_name='roadmap',
            index=models.Index(
                fields=['concept', 'concept_trophy_group'],
                name='roadmap_concept_ctg_idx',
            ),
        ),

        # 9) Bump payload_version default to v2 (flat-roadmap shape).
        migrations.AlterField(
            model_name='roadmapeditlock',
            name='payload_version',
            field=models.PositiveSmallIntegerField(default=2),
        ),
    ]
