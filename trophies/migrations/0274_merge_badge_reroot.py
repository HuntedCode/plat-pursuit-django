# rebuild-only: rejoins the 0266_merge leaf (left dangling when the badge chain was re-rooted onto
# 0265_rating_blurb_idx so 0267-0273 stay portable to main) with the badge chain tip. No operations.
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("trophies", "0266_merge_0265_merge_20260724_2106_0265_rating_blurb_idx"),
        ("trophies", "0273_remove_profilebadgestanding_series_xp_and_more"),
    ]

    operations = []
