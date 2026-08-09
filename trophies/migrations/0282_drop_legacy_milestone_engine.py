# Retire the legacy milestone engine (Lane 2 Step 3): delete the titles its metric LADDERS granted,
# keep the one-off MANUAL award titles, then drop the 3 tables.
#
# Ordering is load-bearing: the title cleanup runs FIRST, while Milestone still exists, because
# criteria_type is the only thing that distinguishes a ladder title (plat/trophy/rating/badge-stat
# counts -- superseded by the milestones app) from a manual award (fundraiser patron, easter eggs).
# UserTitle.source_id is a plain integer, not an FK, so the surviving manual rows are unaffected by
# the drop; they simply become historical awards with no live source.
#
# Reverse recreates the (empty) tables; it does NOT restore deleted titles or earn records.
from django.db import migrations


def delete_ladder_titles(apps, schema_editor):
    """Delete UserTitles granted by metric-ladder milestones; keep the manual one-off awards."""
    Milestone = apps.get_model("trophies", "Milestone")
    UserTitle = apps.get_model("trophies", "UserTitle")

    ladder_ids = list(
        Milestone.objects.exclude(criteria_type="manual").values_list("id", flat=True)
    )
    if ladder_ids:
        UserTitle.objects.filter(source_type="milestone", source_id__in=ladder_ids).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("trophies", "0281_drop_challenge_system"),
    ]

    operations = [
        # 1) Clean up granted titles BEFORE the source rows disappear.
        migrations.RunPython(delete_ladder_titles, migrations.RunPython.noop),
        # 2) Drop the engine's schema. The two child tables FK to Milestone (CASCADE), so they're
        #    deleted first; Milestone last. DeleteModel drops each table with all its constraints,
        #    so no separate AlterUniqueTogether / RemoveField steps are needed.
        migrations.DeleteModel(name="UserMilestone"),
        migrations.DeleteModel(name="UserMilestoneProgress"),
        migrations.DeleteModel(name="Milestone"),
    ]
