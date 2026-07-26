# Adds the partial rating_blurb_idx to UserConceptRating, backing the ratings-card "recent visible blurbs"
# query (present + not staff-hidden, most-recent first, per concept/group).
#
# Built with AddIndexConcurrently (+ atomic = False) so CREATE INDEX does not write-lock UserConceptRating
# while users are submitting/adjusting ratings during the deploy. PARTIAL (WHERE blurb != '' AND NOT
# blurb_hidden) so only rows that actually carry a shown blurb are indexed -- keeps it tiny even on
# heavily-rated games. Same pattern as 0262 / 0260.
#
# NOTE: if a CONCURRENTLY build fails partway, Postgres leaves an INVALID index behind that must be dropped
# manually before re-running:
#     DROP INDEX CONCURRENTLY IF EXISTS rating_blurb_idx;

from django.contrib.postgres.operations import AddIndexConcurrently
from django.db import migrations, models
from django.db.models import Q


class Migration(migrations.Migration):

    # CREATE INDEX CONCURRENTLY cannot run inside a transaction.
    atomic = False

    dependencies = [
        ("trophies", "0264_blurbreport"),
    ]

    operations = [
        AddIndexConcurrently(
            model_name="userconceptrating",
            index=models.Index(
                condition=~Q(blurb="") & Q(blurb_hidden=False),
                fields=["concept", "concept_trophy_group", "-updated_at"],
                name="rating_blurb_idx",
            ),
        ),
    ]
