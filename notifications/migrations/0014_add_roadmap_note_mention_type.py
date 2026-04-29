from django.db import migrations, models


_CHOICES = [
    ("platinum_earned", "Platinum Trophy Earned"),
    ("badge_awarded", "Badge Awarded"),
    ("milestone_achieved", "Milestone Achieved"),
    ("monthly_recap", "Monthly Recap Available"),
    ("subscription_created", "Subscription Created"),
    ("subscription_updated", "Subscription Updated"),
    ("discord_verified", "Discord Verified"),
    ("challenge_completed", "Challenge Completed"),
    ("review_reply", "Review Reply"),
    ("review_milestone", "Review Milestone"),
    ("admin_announcement", "Admin Announcement"),
    ("system_alert", "System Alert"),
    ("payment_failed", "Payment Failed"),
    ("payment_action_required", "Payment Action Required"),
    ("roadmap_note_mention", "Roadmap Note Mention"),
]


class Migration(migrations.Migration):

    dependencies = [
        ("notifications", "0013_email_system_enhancements"),
    ]

    operations = [
        migrations.AlterField(
            model_name="notification",
            name="notification_type",
            field=models.CharField(choices=_CHOICES, db_index=True, max_length=50),
        ),
        migrations.AlterField(
            model_name="notificationtemplate",
            name="notification_type",
            field=models.CharField(choices=_CHOICES, max_length=50),
        ),
        migrations.AlterField(
            model_name="schedulednotification",
            name="notification_type",
            field=models.CharField(
                choices=_CHOICES, default="admin_announcement", max_length=50
            ),
        ),
    ]
