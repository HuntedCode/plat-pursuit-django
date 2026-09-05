"""Backfill `ModerationAction.subject_user` for entries written before the column existed.

Runs now rather than later on purpose. The FKs it reads from -- `blurb_report` and `game_flag` --
are both SET_NULL, so every purged report takes another entry's traceability with it. The table was
created 2026-09 and holds very few rows; in a year this migration would recover less and cost more.

The subject rule, which the field's help_text also carries:
  - blurb_hidden        -> the take's AUTHOR (hiding somebody's words is evidence about them)
  - blurb_report_*      -> the REPORTER (a dismissal is evidence about who filed it)
  - game_flag_*         -> the reporter (a game has no hunter behind it)
  - blurb_restored      -> whatever the entry it reverses had

Entries whose report or flag is already gone keep a null subject. That is the honest outcome: there
is nothing left to derive one from, and guessing would put a name on an entry that never had one.
"""
from django.db import migrations


def _label(user):
    """Frozen name, matching `core.services.audit.frozen_label`.

    Spelled out rather than imported: a migration must keep running against the code as it was, and
    importing today's service into a historical migration is how a rerun breaks two refactors from
    now. `display_name` is read defensively for the same reason.
    """
    if user is None:
        return ''
    profile = getattr(user, 'profile', None)
    name = (getattr(profile, 'display_psn_username', '') or getattr(profile, 'psn_username', '')
            or user.email or '')
    return name[:150]


def backfill(apps, schema_editor):
    ModerationAction = apps.get_model('trophies', 'ModerationAction')

    entries = list(
        ModerationAction.objects
        .select_related('blurb_report__reporter__user', 'blurb_report__rating__profile__user',
                        'game_flag__reporter__user', 'reverses')
        .all()
    )
    by_pk = {entry.pk: entry for entry in entries}
    updated = []

    for entry in entries:
        profile = None
        if entry.action == 'blurb_hidden' and entry.blurb_report_id:
            profile = getattr(entry.blurb_report.rating, 'profile', None)
        elif entry.action == 'blurb_report_dismissed' and entry.blurb_report_id:
            profile = entry.blurb_report.reporter
        elif entry.action in ('game_flag_approved', 'game_flag_dismissed') and entry.game_flag_id:
            profile = entry.game_flag.reporter
        elif entry.action == 'blurb_restored' and entry.reverses_id:
            # A reversal inherits its original's subject. Resolved from the in-memory map because the
            # original may not have been visited yet.
            original = by_pk.get(entry.reverses_id)
            if original is not None and original.subject_user_id:
                entry.subject_user_id = original.subject_user_id
                entry.subject_label = original.subject_label
                updated.append(entry)
            continue

        user = getattr(profile, 'user', None)
        if user is None:
            continue
        entry.subject_user = user
        entry.subject_label = _label(user)
        updated.append(entry)

    if updated:
        ModerationAction.objects.bulk_update(updated, ['subject_user', 'subject_label'],
                                             batch_size=500)


def unbackfill(apps, schema_editor):
    """Reversible, and deliberately blunt: it clears the columns the forward pass filled.

    The forward pass is the only writer of these two columns for pre-existing rows, and the field was
    added in the migration immediately before this one -- so anything set here came from here.
    """
    ModerationAction = apps.get_model('trophies', 'ModerationAction')
    ModerationAction.objects.update(subject_user=None, subject_label='')


class Migration(migrations.Migration):

    dependencies = [
        ('trophies', '0327_moderation_subject'),
    ]

    operations = [
        migrations.RunPython(backfill, unbackfill),
    ]
