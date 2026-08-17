"""ArtRevealItem.badge (legacy tier Badge) -> .series (BadgeSeries).

`release()` pushed the revealed artwork onto `Badge.badge_image`, which nothing renders any more. The
series default (`BadgeSeries.badge_image`) is what every edition inherits through
`GroupBadge.art_layers()`, so one write now reaches every medallion of the series.

Same refuse-to-guess discipline as the fundraiser's `0006`: a reveal item represents commissioned
artwork, so an unmappable row raises rather than silently detaching. Unlike the claim table there is no
denormalized slug here, so the mapping goes through the legacy badge's own `series_slug` -- which is
exactly why blank-slug rows have to be reported rather than skipped.
"""

import django.db.models.deletion
from django.db import migrations, models


def plan_mapping(rows, series_by_slug):
    """PURE. rows: [(item_id, event_id, badge_series_slug)]. Returns {item_id: series_id} or raises.

    Plans the WHOLE set before writing anything. An earlier draft updated rows inside the loop and only
    raised at the end, so a refusal still left partial writes behind -- survivable only because RunPython
    happens to be transactional. Deciding first and writing second does not depend on that.

    Uniqueness is per (event, series), matching the constraint the migration installs: the same series
    may legitimately appear in two different reveal events.
    """
    mapping, seen, unmapped, collisions = {}, {}, [], []

    for item_id, event_id, raw_slug in rows:
        slug = (raw_slug or '').strip()
        series_id = series_by_slug.get(slug)
        if not series_id:
            unmapped.append(f'item {item_id} (event {event_id}) -> {slug!r}')
            continue
        key = (event_id, series_id)
        if key in seen:
            collisions.append(f'items {seen[key]} and {item_id} both -> {slug!r} in event {event_id}')
            continue
        seen[key] = item_id
        mapping[item_id] = series_id

    if unmapped or collisions:
        parts = ['Cannot repoint ArtRevealItem onto BadgeSeries without losing reveal items.']
        if unmapped:
            parts.append(f'No BadgeSeries for: {unmapped}')
        if collisions:
            parts.append(
                'Two items map to one series in the same event (the unique constraint cannot hold '
                f'both): {collisions}'
            )
        parts.append(
            'Run `python manage.py convert_series_to_groups --all` first, then re-run migrate.'
        )
        raise RuntimeError('\n'.join(parts))
    return mapping


def link_series(apps, schema_editor):
    ArtRevealItem = apps.get_model('art_reveal', 'ArtRevealItem')
    BadgeSeries = apps.get_model('trophies', 'BadgeSeries')

    rows = list(ArtRevealItem.objects.values_list('id', 'event_id', 'badge__series_slug'))
    if not rows:
        return

    mapping = plan_mapping(rows, dict(BadgeSeries.objects.values_list('series_slug', 'id')))
    for item_id, series_id in mapping.items():
        ArtRevealItem.objects.filter(id=item_id).update(series_id=series_id)


def unlink_series(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('art_reveal', '0003_alter_artrevealitem_badge'),
        ('trophies', '0306_user_group_badge_created_at'),
    ]

    operations = [
        # Drop the old constraint first: it names `badge`, which the RemoveField below deletes.
        migrations.RemoveConstraint(
            model_name='artrevealitem',
            name='uniq_artreveal_event_badge',
        ),
        migrations.AddField(
            model_name='artrevealitem',
            name='series',
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name='art_reveal_items',
                to='trophies.badgeseries',
            ),
        ),
        migrations.RunPython(link_series, unlink_series),
        migrations.AlterField(
            model_name='artrevealitem',
            name='series',
            field=models.ForeignKey(
                help_text='The badge series to reveal. The uploaded artwork becomes the series default '
                          'art on release, which every edition of the series inherits.',
                on_delete=django.db.models.deletion.CASCADE,
                related_name='art_reveal_items',
                to='trophies.badgeseries',
            ),
        ),
        migrations.RemoveField(model_name='artrevealitem', name='badge'),
        migrations.AddConstraint(
            model_name='artrevealitem',
            constraint=models.UniqueConstraint(
                fields=('event', 'series'), name='uniq_artreveal_event_series'
            ),
        ),
    ]
