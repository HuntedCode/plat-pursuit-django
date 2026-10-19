"""Clean Company slugs before 0344 makes them unique.

IGDB company slugs were stored as sent. Some carry characters the `<slug:>` URL converter rejects, which
raised NoReverseMatch wherever that company page was linked, and the column was never unique, so two
companies could share a slug and 500 the page that looks one up. 0344 adds the unique constraint; this
makes the data fit it first. (Separate migrations, per Django's guidance not to mix RunPython data changes
and schema changes in one migration on PostgreSQL.)

Only slugs that are not slugify-clean, or are duplicated, change. A clean, unique slug is kept, so a working
URL moves only if its slug was routable but not slugify-clean (uppercase, a leading or doubled dash): IGDB
slugs are lowercase in practice, so in effect only the broken ones change. Among duplicates the oldest row
(lowest id) keeps the slug.
"""
from django.db import migrations
from django.utils.text import slugify

SLUG_MAX = 255


def planned_slugs(rows):
    """{company pk: new slug} for the rows that must change. `rows` is `[(pk, igdb_id, name, slug)]`.

    Same rule as `IGDBService._company_slug`, with the same first three candidates: slugify the slug, else
    the name, else `company-<igdb id>`; on a clash try `-<igdb id>` appended, then `company-<igdb id>`. The
    last resort, `company-<igdb id>-<pk>`, is one the service keeps on refresh. Pure, so it is tested without a database that would need to hold the duplicates
    the unique constraint forbids."""
    taken, to_fix = set(), []
    for pk, igdb_id, name, slug in sorted(rows):
        if slug and slugify(slug) == slug and len(slug) <= SLUG_MAX and slug not in taken:
            taken.add(slug)
        else:
            to_fix.append((pk, igdb_id, name, slug))

    def fit(text, suffix=''):
        return text[:SLUG_MAX - len(suffix)].rstrip('-_') + suffix

    plan = {}
    for pk, igdb_id, name, slug in to_fix:
        base = fit(slugify(slug or '') or slugify(name or '') or f'company-{igdb_id}')
        candidates = (base, fit(base, f'-{igdb_id}'), f'company-{igdb_id}', f'company-{igdb_id}-{pk}')
        candidate = next((c for c in candidates if c not in taken), None)
        if candidate is None:
            raise RuntimeError(f'No free slug for company pk={pk}; 0344 would fail on a duplicate.')
        taken.add(candidate)
        if candidate != slug:
            plan[pk] = candidate
    return plan


def clean_company_slugs(apps, schema_editor):
    Company = apps.get_model('trophies', 'Company')
    plan = planned_slugs(Company.objects.values_list('id', 'igdb_id', 'name', 'slug'))
    for pk, slug in plan.items():
        Company.objects.filter(pk=pk).update(slug=slug)


class Migration(migrations.Migration):

    dependencies = [
        ('trophies', '0342_remove_stage_stage_icon'),
    ]

    operations = [
        migrations.RunPython(clean_company_slugs, migrations.RunPython.noop),
    ]
