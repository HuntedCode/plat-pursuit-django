# Generated manually for Review Hub expansion

from django.db import migrations, models


def backfill_word_counts(apps, schema_editor):
    """Compute word_count for all existing non-deleted reviews."""
    Review = apps.get_model('trophies', 'Review')
    reviews = Review.objects.filter(is_deleted=False).only('id', 'body')
    for review in reviews.iterator(chunk_size=500):
        wc = len(review.body.split()) if review.body else 0
        Review.objects.filter(id=review.id).update(word_count=wc)


class Migration(migrations.Migration):

    dependencies = [
        ("trophies", "0161_add_developer_badge_type"),
    ]

    operations = [
        # Add word_count to Review
        migrations.AddField(
            model_name="review",
            name="word_count",
            field=models.PositiveIntegerField(default=0),
        ),
        # Add new milestone criteria types
        migrations.AlterField(
            model_name="milestone",
            name="criteria_type",
            field=models.CharField(
                choices=[
                    ("manual", "Manual Award"),
                    ("plat_count", "Earned Plats"),
                    ("psn_linked", "PSN Profile Linked"),
                    ("discord_linked", "Discord Connected"),
                    ("rating_count", "Games Rated"),
                    ("playtime_hours", "Total Playtime (Hours)"),
                    ("trophy_count", "Total Trophies Earned"),
                    ("comment_upvotes", "Comment Upvotes Received"),
                    ("checklist_upvotes", "Checklist Upvotes Received"),
                    ("badge_count", "Badge Tiers Earned"),
                    ("unique_badge_count", "Unique Badges Earned"),
                    ("completion_count", "Games 100% Completed"),
                    ("stage_count", "Badge Stages Completed"),
                    ("az_progress", "A-Z Challenge Letters"),
                    ("genre_progress", "Genre Challenge Genres"),
                    ("subgenre_progress", "Subgenre Collection"),
                    ("calendar_month_jan", "Calendar: January Complete"),
                    ("calendar_month_feb", "Calendar: February Complete"),
                    ("calendar_month_mar", "Calendar: March Complete"),
                    ("calendar_month_apr", "Calendar: April Complete"),
                    ("calendar_month_may", "Calendar: May Complete"),
                    ("calendar_month_jun", "Calendar: June Complete"),
                    ("calendar_month_jul", "Calendar: July Complete"),
                    ("calendar_month_aug", "Calendar: August Complete"),
                    ("calendar_month_sep", "Calendar: September Complete"),
                    ("calendar_month_oct", "Calendar: October Complete"),
                    ("calendar_month_nov", "Calendar: November Complete"),
                    ("calendar_month_dec", "Calendar: December Complete"),
                    ("calendar_months_total", "Calendar Months Completed"),
                    ("calendar_complete", "Calendar Challenge Complete"),
                    ("is_premium", "Premium Subscriber"),
                    ("subscription_months", "Subscription Months"),
                    ("review_count", "Quality Reviews Written"),
                    ("review_helpful_count", "Review Helpful Votes Received"),
                ],
                default="manual",
                max_length=30,
            ),
        ),
        # Backfill word counts for existing reviews
        migrations.RunPython(
            backfill_word_counts,
            migrations.RunPython.noop,
        ),
    ]
