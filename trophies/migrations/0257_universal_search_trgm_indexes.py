# Trigram GIN indexes for the universal nav-search typeahead.
#
# The suggest endpoint matches game/badge/franchise names with substring search
# (ILIKE '%q%'), which a leading-wildcard pattern makes unservable by any btree
# -> a seq scan per keystroke. A pg_trgm GIN index turns each of these into a
# sub-millisecond index scan, scale-independent.
#
# Built with AddIndexConcurrently (+ atomic = False) so CREATE INDEX does not
# write-lock the Concept table (tens of thousands of rows) during the prod
# deploy. TrigramExtension runs first because the gin_trgm_ops opclass the
# indexes use is provided by the pg_trgm extension.

import django.contrib.postgres.indexes
from django.contrib.postgres.operations import AddIndexConcurrently, TrigramExtension
from django.db import migrations


class Migration(migrations.Migration):

    # CREATE INDEX CONCURRENTLY cannot run inside a transaction.
    atomic = False

    dependencies = [
        ("trophies", "0256_game_avg_completion_game_full_completion_count_and_more"),
    ]

    operations = [
        TrigramExtension(),
        AddIndexConcurrently(
            model_name="concept",
            index=django.contrib.postgres.indexes.GinIndex(
                fields=["unified_title"],
                name="concept_title_trgm",
                opclasses=["gin_trgm_ops"],
            ),
        ),
        AddIndexConcurrently(
            model_name="badge",
            index=django.contrib.postgres.indexes.GinIndex(
                fields=["name"], name="badge_name_trgm", opclasses=["gin_trgm_ops"]
            ),
        ),
        AddIndexConcurrently(
            model_name="franchise",
            index=django.contrib.postgres.indexes.GinIndex(
                fields=["name"], name="franchise_name_trgm", opclasses=["gin_trgm_ops"]
            ),
        ),
    ]
