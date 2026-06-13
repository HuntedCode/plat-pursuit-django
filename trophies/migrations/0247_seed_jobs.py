"""Seed the 24 job specializations + the Freelancer fallback, grouped into the 5
disciplines (the radar axes). Idempotent (get_or_create); names/descriptions/visuals
can be tuned later via admin. See docs/design/rebuild/job-board-contracts.md."""
from django.db import migrations
from django.utils.text import slugify

# (discipline, [job names in display order])
JOBS = [
    ('combat',      ['Slayer', 'Gunslinger', 'Vanguard', 'Outlaw', 'Warrior']),
    ('exploration', ['Pathfinder', 'Infiltrator', 'Cartographer', 'Mascot', 'Survivalist']),
    ('mind',        ['Mastermind', 'Tactician', 'Architect', 'Tycoon', 'Card Shark']),
    ('heart',       ['Mage', 'Champion', 'Librarian', 'Jester', 'Exorcist']),
    ('finesse',     ['Gamer', 'Driver', 'Athlete', 'Maestro', 'Freelancer']),
]
FALLBACK = 'Freelancer'


def seed_jobs(apps, schema_editor):
    Job = apps.get_model('trophies', 'Job')
    for discipline, names in JOBS:
        for order, name in enumerate(names):
            Job.objects.get_or_create(
                slug=slugify(name),
                defaults={
                    'name': name,
                    'discipline': discipline,
                    'is_fallback': name == FALLBACK,
                    'display_order': order,
                },
            )


def unseed_jobs(apps, schema_editor):
    Job = apps.get_model('trophies', 'Job')
    slugs = [slugify(n) for _, names in JOBS for n in names]
    Job.objects.filter(slug__in=slugs).delete()


class Migration(migrations.Migration):
    dependencies = [
        ('trophies', '0246_contract_job_contractbundle_contractmembership_and_more'),
    ]
    operations = [
        migrations.RunPython(seed_jobs, unseed_jobs),
    ]
