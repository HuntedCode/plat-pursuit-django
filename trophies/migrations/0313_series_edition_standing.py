"""SeriesEditionStanding: the per-edition badge board gets a table of its own.

WHAT IT REPLACES. The board on badge detail's Ranks tab, sliced by `?edition=`, read `SeriesBadgeStanding`
and pulled both its quantity and its membership rule out of JSON -- ordering on `Cast(group_xp -> key)`
and filtering on `Cast(group_progress -> key -> 0) > 0`. `sbs_series_board_idx` narrowed that to one
series' rows and then every one of them was extracted, filtered and sorted from the heap. Nothing could
stop early, the count and the rank paid it too, and the virtualizer re-runs the query per window, so
scrolling a popular badge re-sorted the whole series per screenful.

AND THE TIEBREAK, which is the reason this is a table rather than another index. `SeriesBadgeStanding
.advanced_at` is SERIES-wide (`badge_xp.compute_series_standings` takes it from the furthest-along
edition), so an edition board that tiebroke on it separated two hunters tied on Legacy HD points by their
Ultra HD progress: ADVANCING IN ONE EDITION COULD DROP A RANK IN ANOTHER. The per-edition date already
existed in the engine and was discarded; this stores it.

NO BACKFILL, and no backfill COMMAND either. The table is populated by the same seam that writes every
other standing, so the `evaluate_badges --all` that already runs at deploy fills it -- exactly how
migration 0300 handled `ProfileEditionStanding`, which was created empty and hit the identical "every
edition board says nobody is here" window. A bespoke seeder was written first and deleted: it could only
derive `advanced_at` from `SeriesBadgeStanding`'s series-wide value, i.e. reproduce the very tiebreak this
table exists to fix, on a board that has never been in production and therefore has no ranks to preserve.
See the deploy checklist.

CREATE, so the indexes go up inline: the table is empty and nothing reads it until the backfill, which is
the one case where a partial index does not need CONCURRENTLY (unlike 0307 / 0309 / 0310 / 0311, which
rebuilt indexes on live tables).
"""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("trophies", "0312_series_board_ranks_on_points"),
    ]

    operations = [
        migrations.CreateModel(
            name="SeriesEditionStanding",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("series_slug", models.SlugField(max_length=100)),
                ("platform_group_key", models.SlugField(max_length=40)),
                ("xp", models.PositiveIntegerField(default=0)),
                ("stages_cleared", models.PositiveIntegerField(default=0)),
                ("gating_count", models.PositiveIntegerField(default=0)),
                ("advanced_at", models.DateField(blank=True, null=True)),
                (
                    "country_code",
                    models.CharField(
                        blank=True, db_index=True, default="", max_length=5
                    ),
                ),
                ("is_linked", models.BooleanField(default=False)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "profile",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="series_edition_standings",
                        to="trophies.profile",
                    ),
                ),
            ],
            options={
                "indexes": [
                    models.Index(
                        condition=models.Q(("is_linked", True)),
                        fields=[
                            "series_slug",
                            "platform_group_key",
                            "-xp",
                            "advanced_at",
                            "profile",
                        ],
                        name="ses_board_idx",
                    ),
                    models.Index(
                        condition=models.Q(("is_linked", True)),
                        fields=[
                            "series_slug",
                            "platform_group_key",
                            "country_code",
                            "-xp",
                            "advanced_at",
                            "profile",
                        ],
                        name="ses_board_cc_idx",
                    ),
                ],
                "constraints": [
                    models.UniqueConstraint(
                        fields=("profile", "series_slug", "platform_group_key"),
                        name="uniq_profile_series_edition",
                    )
                ],
            },
        ),
    ]
