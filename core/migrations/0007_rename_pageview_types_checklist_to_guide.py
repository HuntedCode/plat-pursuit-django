# Generated migration for renaming checklist → guide in PageView.page_type

from django.db import migrations, models


def rename_page_types_forward(apps, schema_editor):
    """Rename checklist-related page_type values to guide."""
    PageView = apps.get_model('core', 'PageView')

    # Update existing records
    PageView.objects.filter(page_type='checklist').update(page_type='guide')
    PageView.objects.filter(page_type='my_checklists').update(page_type='my_guides')
    PageView.objects.filter(page_type='checklist_edit').update(page_type='guide_edit')


def rename_page_types_backward(apps, schema_editor):
    """Revert guide-related page_type values back to checklist."""
    PageView = apps.get_model('core', 'PageView')

    # Revert records
    PageView.objects.filter(page_type='guide').update(page_type='checklist')
    PageView.objects.filter(page_type='my_guides').update(page_type='my_checklists')
    PageView.objects.filter(page_type='guide_edit').update(page_type='checklist_edit')


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0006_alter_pageview_page_type'),
    ]

    operations = [
        migrations.RunPython(rename_page_types_forward, rename_page_types_backward),
        migrations.AlterField(
            model_name='pageview',
            name='page_type',
            field=models.CharField(
                choices=[
                    ('profile', 'Profile'),
                    ('game', 'Game'),
                    ('guide', 'Guide'),
                    ('badge', 'Badge Series'),
                    ('index', 'Index Page'),
                    ('guides_browse', 'Guides Browse'),
                    ('recap_slide', 'Recap Slide'),
                    ('recap_home', 'Recap Home'),
                    ('search', 'Search'),
                    ('trophy_case', 'Trophy Case'),
                    ('my_guides', 'My Guides'),
                    ('my_shareables', 'My Shareables'),
                    ('guide_edit', 'Guide Edit'),
                    ('settings', 'Settings'),
                    ('subscription', 'Subscription Management'),
                    ('email_prefs', 'Email Preferences'),
                    ('notifications_inbox', 'Notifications Inbox'),
                ],
                db_index=True,
                max_length=20
            ),
        ),
    ]
