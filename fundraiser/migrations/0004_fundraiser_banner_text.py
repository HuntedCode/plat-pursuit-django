from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("fundraiser", "0003_fundraiser_end_date_nullable"),
    ]

    operations = [
        migrations.AddField(
            model_name="fundraiser",
            name="banner_text",
            field=models.CharField(
                blank=True,
                default="Help support our community.",
                help_text="Custom message shown in the site-wide banner between the campaign name and the CTA link.",
                max_length=200,
            ),
        ),
    ]
