"""Rename MUSIC/RHYTHM genre key to MUSIC_RHYTHM (remove slash from URL paths)."""

from django.db import migrations


def rename_music_rhythm_forward(apps, schema_editor):
    GenreChallengeSlot = apps.get_model('trophies', 'GenreChallengeSlot')
    Challenge = apps.get_model('trophies', 'Challenge')

    GenreChallengeSlot.objects.filter(genre='MUSIC/RHYTHM').update(genre='MUSIC_RHYTHM')
    Challenge.objects.filter(cover_genre='MUSIC/RHYTHM').update(cover_genre='MUSIC_RHYTHM')


def rename_music_rhythm_reverse(apps, schema_editor):
    GenreChallengeSlot = apps.get_model('trophies', 'GenreChallengeSlot')
    Challenge = apps.get_model('trophies', 'Challenge')

    GenreChallengeSlot.objects.filter(genre='MUSIC_RHYTHM').update(genre='MUSIC/RHYTHM')
    Challenge.objects.filter(cover_genre='MUSIC_RHYTHM').update(cover_genre='MUSIC/RHYTHM')


class Migration(migrations.Migration):

    dependencies = [
        ('trophies', '0152_genre_bonus_slots'),
    ]

    operations = [
        migrations.RunPython(
            rename_music_rhythm_forward,
            rename_music_rhythm_reverse,
        ),
    ]
