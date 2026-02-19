"""
Update the milestone_achieved notification template with enhanced fields.

Adds tier progression info and deep-link support to the milestone notification.
"""
from django.db import migrations


def update_milestone_template(apps, schema_editor):
    NotificationTemplate = apps.get_model('notifications', 'NotificationTemplate')
    try:
        template = NotificationTemplate.objects.get(name='milestone_achieved')
        template.message_template = (
            "You've achieved {milestone_name}!{tier_text}{next_milestone_text}"
        )
        template.action_url_template = '/milestones/?cat={milestone_category}#{criteria_type}'
        template.save(update_fields=['message_template', 'action_url_template'])
    except NotificationTemplate.DoesNotExist:
        pass


def revert_milestone_template(apps, schema_editor):
    NotificationTemplate = apps.get_model('notifications', 'NotificationTemplate')
    try:
        template = NotificationTemplate.objects.get(name='milestone_achieved')
        template.message_template = (
            "Amazing progress, {username}! You've achieved the {milestone_name} milestone!"
        )
        template.action_url_template = '/milestones/'
        template.save(update_fields=['message_template', 'action_url_template'])
    except NotificationTemplate.DoesNotExist:
        pass


class Migration(migrations.Migration):

    dependencies = [
        ('notifications', '0007_alter_notification_notification_type_and_more'),
    ]

    operations = [
        migrations.RunPython(update_milestone_template, revert_milestone_template),
    ]
