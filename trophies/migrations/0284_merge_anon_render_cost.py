"""Rejoin the migration graph after merging `main` into `rebuild`.

`main` shipped the anon-render-cost fix (0274 game stats denorm, 0275 rarest-trophies
showcase removal) off 0273 while `rebuild` was independently building the badge/milestone
work up to 0283 off the same parent. Both branches therefore reused the numbers 0274/0275
for different migrations -- harmless, since Django keys the graph on NAMES -- but the
merge leaves two leaf nodes, which Django refuses to run.

No operations: this only declares "both histories are now one". Same shape as
0274_merge_badge_reroot, which resolved the previous branch divergence.
"""
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("trophies", "0275_remove_rarest_trophies_showcase"),
        ("trophies", "0283_cleanup_orphaned_milestone_titles"),
    ]

    operations = []
