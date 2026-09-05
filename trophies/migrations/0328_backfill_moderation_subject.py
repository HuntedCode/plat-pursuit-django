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

    # OLDEST FIRST, and that ordering is the whole correctness of the reversal branch below.
    # `Meta.ordering` is newest-first, and a reversal is by construction newer than what it
    # reverses -- so under the default order every reversal was visited BEFORE its original, found
    # `subject_user_id` still NULL (the column having been added one migration earlier), and was
    # skipped. The one branch written to handle out-of-order resolution was the one branch that
    # could never fire.
    entries = list(
        ModerationAction.objects
        .select_related('blurb_report__reporter__user__profile',
                        'blurb_report__rating__profile__user__profile',
                        'game_flag__reporter__user__profile', 'reverses')
        .order_by('created_at', 'id')
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
    """A NO-OP, on purpose.

    The first version cleared both columns on every row, justified by "the forward pass is the only
    writer of these for pre-existing rows". True, and irrelevant: the `.update()` was not scoped to
    pre-existing rows, so `migrate trophies 0327` -- an ordinary ops action -- would have wiped
    `subject_user` from every entry the service has written SINCE, which is all of them. That is the
    per-person history this branch exists to build, destroyed by a rollback of the migration that
    populated it.

    Rolling back to 0327 drops the columns anyway, so there is nothing this needs to undo.
    """



class Migration(migrations.Migration):

    dependencies = [
        ('trophies', '0327_moderation_subject'),
    ]

    operations = [
        migrations.RunPython(backfill, unbackfill),
    ]
