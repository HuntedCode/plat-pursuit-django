"""Every month becomes unwatched again, because the thing you watch is no longer the thing that was
watched.

The recap was rebuilt from a slide deck into a ceremony: a new entrance, a new stage, twenty re-authored
beats (four of them entirely new), a quiz payoff that never existed, and an ending that hands over to the
share card. A `has_been_viewed` set against the old presentation is answering a question nobody is asking
any more.

This is deliberately BOTH surfaces, not just the archive. The dashboard's recap module gates its
share-card preview on the same flag, so leaving it set there would have the dashboard quietly asserting
"you have seen this" about an experience nobody has seen. One flag, one meaning, reset together.

Non-destructive: `has_been_viewed` is a display signal only. Nothing is deleted, no recap content changes,
and the flag re-sets itself the moment a month is opened. The only cost of being wrong is that a hunter is
shown a "New" flag on a month they had already watched under the old presentation.
"""
from django.db import migrations


def reset_viewed(apps, schema_editor):
    MonthlyRecap = apps.get_model('trophies', 'MonthlyRecap')
    # A single UPDATE. Filtered to the rows that would actually change so the write stays proportional to
    # the number of hunters who had watched something, rather than to the size of the table.
    MonthlyRecap.objects.filter(has_been_viewed=True).update(has_been_viewed=False)


def noop_reverse(apps, schema_editor):
    """Deliberately not reversible in substance.

    Reversing would mean restoring which months each hunter had watched, and that information is gone the
    moment this runs -- it lives nowhere else. A reverse that silently marked everything watched would be
    worse than one that does nothing, so this is a no-op and the migration is safe to unapply.
    """


class Migration(migrations.Migration):

    dependencies = [
        ('trophies', '0288_recap_context_slides'),
    ]

    operations = [
        migrations.RunPython(reset_viewed, noop_reverse),
    ]
