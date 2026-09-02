"""DonationBadgeClaim.badge (legacy tier Badge) -> .series (BadgeSeries).

The claim always meant "a series" -- the old field's help_text said so -- but pointing at a tier row meant
`complete_badge_claim` credited `Badge.funded_by`, while the medallion renders
`GroupBadge.effective_funded_by` (`funded_by_override or series.funded_by`). Donors were credited on a row
nothing displays.

THIS MIGRATION REFUSES TO GUESS. It carries real donation records -- every row is a payment somebody made
-- so an unmappable claim raises with the offending slug named, rather than nulling the FK or dropping the
row. The operator's fix is to run `convert_series_to_groups --all` first, which creates a `BadgeSeries`
for every legacy series; that is a documented hard prerequisite on the deploy checklist.

Mapping is exact rather than heuristic: `BadgeSeries.series_slug` is unique and the claim already carries
a denormalized `series_slug`, so it is a straight lookup with no ambiguity to resolve.
"""

import django.db.models.deletion
from django.db import migrations, models


def plan_mapping(rows, series_by_slug):
    """PURE. rows: [(claim_id, series_slug)]. Returns {claim_id: series_id} or raises.

    Separated from the ORM on purpose: this is the part that decides whether a payment record survives,
    and it is the part worth testing directly. The migration's intermediate schema (a nullable `series`)
    exists only between two operations, so the surrounding `link_series` cannot be exercised from a test
    against the finished models -- but this can, with plain tuples.
    """
    mapping, seen, unmapped, collisions = {}, {}, [], []

    for claim_id, raw_slug in rows:
        slug = (raw_slug or '').strip()
        # Refuse a blank slug BEFORE the lookup. `BadgeSeries.series_slug` is `blank=False`, but that is
        # form-level only -- Postgres accepts '' and `unique=True` permits exactly one such row. If one
        # ever exists, a bare `.get(slug)` maps EVERY null/blank/whitespace claim onto it: the silent
        # mis-attribution of a payment this whole function exists to prevent.
        if not slug:
            unmapped.append(f'claim {claim_id} -> blank slug')
            continue
        series_id = series_by_slug.get(slug)
        if not series_id:
            unmapped.append(f'claim {claim_id} -> {slug!r}')
            continue
        if series_id in seen:
            collisions.append(f'claims {seen[series_id]} and {claim_id} both -> {slug!r}')
            continue
        seen[series_id] = claim_id
        mapping[claim_id] = series_id

    if unmapped or collisions:
        parts = ['Cannot repoint DonationBadgeClaim onto BadgeSeries without losing donation records.']
        if unmapped:
            parts.append(f'No BadgeSeries for: {unmapped}')
        if collisions:
            parts.append(
                f'Two claims map to one series (the OneToOne cannot hold both): {collisions}'
            )
        parts.append(
            'Run `python manage.py convert_series_to_groups --all` first, then re-run migrate. '
            'Do NOT work around this by nulling the field: each of these rows is a payment.'
        )
        raise RuntimeError('\n'.join(parts))
    return mapping


def check_denorm_agrees_with_fk(rows_with_fk):
    """PURE. rows_with_fk: [(claim_id, series_slug, badge_series_slug)]. Returns a list of disagreements.

    The mapping goes through the denormalized `series_slug`, which `claim_badge` copied off the badge at
    creation time. Both sides have been editable in the admin since, so they can have drifted -- and if
    they have, the claim silently repoints to a DIFFERENT series than its old FK named. The old FK is
    dropped in this same transaction, so after that there is nothing left to notice the difference with.
    Cheap to check while the join is still there.
    """
    return [
        f'claim {cid}: series_slug={slug!r} but its badge says {badge_slug!r}'
        for cid, slug, badge_slug in rows_with_fk
        if (slug or '').strip() != (badge_slug or '').strip()
    ]


def link_series(apps, schema_editor):
    DonationBadgeClaim = apps.get_model('fundraiser', 'DonationBadgeClaim')
    BadgeSeries = apps.get_model('trophies', 'BadgeSeries')

    rows_with_fk = list(
        DonationBadgeClaim.objects.values_list('id', 'series_slug', 'badge__series_slug')
    )
    if not rows_with_fk:
        return

    drift = check_denorm_agrees_with_fk(rows_with_fk)
    if drift:
        parts = [
            "A claim's denormalized series_slug disagrees with the badge it points at, so mapping by "
            'slug would silently move it to a different series:'
        ]
        parts.extend(drift)
        parts.append(
            'Resolve by hand (decide which is correct) before migrating. Each row is a payment.'
        )
        raise RuntimeError('\n'.join(parts))

    rows = [(cid, slug) for cid, slug, _ in rows_with_fk]
    mapping = plan_mapping(rows, dict(BadgeSeries.objects.values_list('series_slug', 'id')))
    for claim_id, series_id in mapping.items():
        DonationBadgeClaim.objects.filter(id=claim_id).update(series_id=series_id)


def unlink_series(apps, schema_editor):
    """Reverse: nothing to undo HERE -- `series` is dropped by the AddField's own reversal.

    But be clear about the migration as a whole: it is IRREVERSIBLE on a non-empty table. The forward
    `RemoveField` destroys `badge_id`, and its backward pass re-adds the column NOT NULL with no default,
    so `migrate fundraiser 0005` fails with a NOT NULL violation. That failure is atomic (verified: no
    half-reverted schema, no data loss) -- but it means the rollback plan for this migration is
    restore-from-snapshot, not migrating backwards. The deploy checklist says so too.
    """
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('fundraiser', '0005_alter_donation_badge_picks_earned'),
        # BadgeSeries must exist before we can point at it.
        ('trophies', '0306_user_group_badge_created_at'),
    ]

    operations = [
        migrations.AddField(
            model_name='donationbadgeclaim',
            name='series',
            field=models.OneToOneField(
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name='artwork_claim',
                to='trophies.badgeseries',
            ),
        ),
        migrations.RunPython(link_series, unlink_series),
        migrations.AlterField(
            model_name='donationbadgeclaim',
            name='series',
            field=models.OneToOneField(
                help_text='The claimed badge series. OneToOne enforces one claim per series.',
                on_delete=django.db.models.deletion.CASCADE,
                related_name='artwork_claim',
                to='trophies.badgeseries',
            ),
        ),
        migrations.RemoveField(model_name='donationbadgeclaim', name='badge'),
    ]
