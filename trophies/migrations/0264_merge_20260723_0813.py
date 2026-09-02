# Graph join only, no schema operations.
#
# main and rebuild both grew a 0260: main's pg_game_leaderboard_index (PR #52) and rebuild's own chain
# ending at 0263. Merging main back into rebuild left the trophies app with two leaf nodes, which Django
# refuses to apply until they are reconciled. Same situation as 0260-0263.

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("trophies", "0260_profilegame_leaderboard_index"),
        ("trophies", "0263_merge_20260718_2254"),
    ]

    operations = []
