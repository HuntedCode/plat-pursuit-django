# Follow-up to 0282: remove milestone-sourced UserTitles that 0282's predicate missed.
#
# 0282 deleted `source_type='milestone'` rows via `source_id__in=<ladder milestone ids>`. That misses two
# shapes, both of which then render as bogus "Special" awards on the Titles page:
#   1. `source_id IS NULL` (the column is nullable; some legacy grant paths never set it), and
#   2. `source_id` pointing at a Milestone that had ALREADY been deleted by an earlier cleanup, so the id
#      was absent from the ladder list.
#
# The Milestone table is gone by now, so criteria_type is no longer available to classify the survivors.
# Instead we key on the only thing left that identifies a genuine one-off award: its Title name. The legacy
# engine had exactly three `criteria_type='manual'` milestones, and each granted a fixed title:
#   Badge Artwork Patron -> "Patron of the Arts"
#   Platinum Race Winner -> "Fastest Plat in the West"
#   Unboxed!             -> "Case Hardened"
# Everything else with source_type='milestone' came from a retired metric ladder and should be gone.
#
# Idempotent: re-running finds nothing. On a DB where 0282 worked cleanly this is a no-op.
from django.db import migrations

# The only titles the legacy engine's MANUAL awards ever granted. Historical constant -- the milestones
# that produced them no longer exist, so this list can't be derived at runtime.
KEPT_MANUAL_TITLES = [
    "Patron of the Arts",
    "Fastest Plat in the West",
    "Case Hardened",
]


def cleanup_orphaned_ladder_titles(apps, schema_editor):
    UserTitle = apps.get_model("trophies", "UserTitle")
    (
        UserTitle.objects
        .filter(source_type="milestone")
        .exclude(title__name__in=KEPT_MANUAL_TITLES)
        .delete()
    )


class Migration(migrations.Migration):

    dependencies = [
        ("trophies", "0282_drop_legacy_milestone_engine"),
    ]

    operations = [
        migrations.RunPython(cleanup_orphaned_ladder_titles, migrations.RunPython.noop),
    ]
