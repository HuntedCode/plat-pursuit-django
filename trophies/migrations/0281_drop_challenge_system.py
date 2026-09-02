# Retire the Challenge system (Lane 2 teardown): archive A-Z progress into ArchivedAZChallenge, then drop
# the 5 challenge models. Ordering is load-bearing -- the archive RunPython runs FIRST (while Challenge +
# AZChallengeSlot are still intact), then the schema drops. Calendar/Genre progress is intentionally not
# preserved. Reverse recreates the (empty) archive table + challenge tables; it does NOT restore data.
import django.db.models.deletion
from django.db import migrations, models


def archive_az_challenges(apps, schema_editor):
    """Copy every A-Z challenge (Challenge type='az' + its AZChallengeSlot rows) into ArchivedAZChallenge,
    keyed on stable PSN ids so a future rebuilt system can re-import it. Bounded (one row per A-Z challenge)."""
    Challenge = apps.get_model("trophies", "Challenge")
    ArchivedAZChallenge = apps.get_model("trophies", "ArchivedAZChallenge")

    rows = []
    qs = (
        Challenge.objects.filter(challenge_type="az")
        .select_related("profile")
        .prefetch_related("az_slots__game")
    )
    for ch in qs.iterator(chunk_size=200):
        slots = []
        for slot in ch.az_slots.all():
            game = slot.game
            slots.append({
                "letter": slot.letter,
                "game_np_communication_id": game.np_communication_id if game else None,
                "game_title": game.title_name if game else None,
                "is_completed": slot.is_completed,
                "completed_at": slot.completed_at.isoformat() if slot.completed_at else None,
            })
        slots.sort(key=lambda s: s["letter"])
        rows.append(ArchivedAZChallenge(
            psn_username=(ch.profile.psn_username if ch.profile_id else "") or "",
            profile_id=ch.profile_id,
            name=ch.name or "",
            completed_count=ch.completed_count,
            is_complete=ch.is_complete,
            was_deleted=ch.is_deleted,
            created_at=ch.created_at,
            slots=slots,
        ))
    ArchivedAZChallenge.objects.bulk_create(rows, batch_size=200)


class Migration(migrations.Migration):

    dependencies = [
        ("trophies", "0280_company_representative_game"),
    ]

    operations = [
        # 1) Create the archive table.
        migrations.CreateModel(
            name="ArchivedAZChallenge",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("psn_username", models.CharField(db_index=True, max_length=32)),
                ("name", models.CharField(blank=True, default="", max_length=75)),
                ("completed_count", models.PositiveIntegerField(default=0)),
                ("is_complete", models.BooleanField(default=False)),
                ("was_deleted", models.BooleanField(default=False, help_text="The source challenge was soft-deleted at archive time.")),
                ("created_at", models.DateTimeField(blank=True, help_text="Original challenge creation time.", null=True)),
                ("slots", models.JSONField(default=list, help_text="[{letter, game_np_communication_id, game_title, is_completed, completed_at}]")),
                ("archived_at", models.DateTimeField(auto_now_add=True)),
                ("profile", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="+", to="trophies.profile")),
            ],
            options={
                "ordering": ["psn_username"],
            },
        ),
        # 2) Archive A-Z progress BEFORE any challenge table is touched.
        migrations.RunPython(archive_az_challenges, migrations.RunPython.noop),
        # 3) Drop the challenge schema. The 4 slot tables FK to Challenge (CASCADE), so they're deleted
        #    first; Challenge last, once nothing references it. DeleteModel drops each table with all its
        #    constraints, so no separate AlterUniqueTogether / RemoveField steps are needed.
        migrations.DeleteModel(name="AZChallengeSlot"),
        migrations.DeleteModel(name="CalendarChallengeDay"),
        migrations.DeleteModel(name="GenreBonusSlot"),
        migrations.DeleteModel(name="GenreChallengeSlot"),
        migrations.DeleteModel(name="Challenge"),
    ]
