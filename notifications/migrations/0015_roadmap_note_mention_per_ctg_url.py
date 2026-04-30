"""Update the roadmap_note_mention template for the per-CTG roadmap split.

Two changes:
  1. action_url_template gains a `{trophy_group_id}` segment so DLC
     mentions deep-link to the correct editor (not the base game).
     'default' as the group_id still opens the base-game editor.
  2. title_template gains a `{ctg_label}` suffix so the recipient can
     see at a glance which roadmap they were mentioned on. The label is
     blank for base-game mentions and "(DLC name)" for DLC mentions.
"""
from django.db import migrations


def update_template(apps, schema_editor):
    NotificationTemplate = apps.get_model('notifications', 'NotificationTemplate')
    NotificationTemplate.objects.filter(name='roadmap_note_mention').update(
        action_url_template='/games/{game_slug}/roadmap/{trophy_group_id}/edit/?note={note_id}',
        title_template='💬 {author_username} mentioned you on {game_title}{ctg_label}',
    )


def revert_template(apps, schema_editor):
    NotificationTemplate = apps.get_model('notifications', 'NotificationTemplate')
    NotificationTemplate.objects.filter(name='roadmap_note_mention').update(
        action_url_template='/games/{game_slug}/roadmap/edit/?note={note_id}',
        title_template='💬 {author_username} mentioned you on {game_title}',
    )


class Migration(migrations.Migration):

    dependencies = [
        ('notifications', '0014_add_roadmap_note_mention_type'),
    ]

    operations = [
        migrations.RunPython(update_template, revert_template),
    ]
