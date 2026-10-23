"""Build a whole badge from a franchise, a series (IGDB collection), both, or a developer.

Three steps in one transaction, so a failure leaves nothing behind (not even the slug):
  1. Create the BadgeSeries with the staff create page's defaults (all gating stages, no description, hidden),
     named and slugged after the subject, with its franchise / collection / developer filled in.
  2. `generate_badge_stages <slug>`: one stage per origin game.
  3. `convert_series_to_groups <slug>`: a HIDDEN edition for each platform group the stages reach.

The badge type follows what you pass: a series (alone or with a franchise) is a 'series' badge and takes the
series' name and slug, a franchise alone is a 'franchise' badge, a developer is a 'developer' badge. Find
subjects with `badge_candidates`.

    python manage.py build_badge --franchise god-of-war --dry-run
    python manage.py build_badge --series the-last-of-us --franchise the-last-of-us-franchise
    python manage.py build_badge --developer fromsoftware --slug fromsoftware-games

--dry-run builds everything and rolls it back, so its output is exactly what a real run would write.
"""

from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import IntegrityError, transaction
from django.utils.text import slugify

from trophies.constants import BADGE_TYPE_DEVELOPER, BADGE_TYPE_FRANCHISE, BADGE_TYPE_SERIES
from trophies.models import BadgeSeries, Company, Franchise, Stage
from trophies.services.badge_candidate_service import existing_badge

_SLUG_MAX = BadgeSeries._meta.get_field('series_slug').max_length


# BadgeSeries field -> the flag (and word) a curator knows it by.
_FLAG_FOR_FIELD = {'franchise': 'franchise', 'collection': 'series', 'developer': 'developer'}


def _taken_message(duplicate, fields):
    """Name what `duplicate` already covers, and the way out when only the franchise of a series build is taken
    (the sister case: a series badge beside an existing franchise badge)."""
    taken = [
        field for field in _FLAG_FOR_FIELD
        if fields.get(field) is not None and getattr(duplicate, f'{field}_id') == fields[field].pk
    ]
    message = f'"{duplicate.series_slug}" already covers this {" and ".join(_FLAG_FOR_FIELD[f] for f in taken)}.'
    # Only when the series itself is free: another badge may hold it, and the hint would lead straight to that.
    if taken == ['franchise'] and fields.get('collection') is not None and not existing_badge(
        collection=fields['collection'],
    ):
        message += ' Drop --franchise to build a series badge beside it.'
    return message


class _DryRunRollback(Exception):
    """Raised inside the transaction to undo a dry run's writes."""


def _subject(model, slug, label, **filters):
    found = model.objects.filter(slug=slug, **filters).first()
    if found is not None:
        return found
    hint = ""
    if model is Franchise and Franchise.objects.filter(slug=slug).exists():
        other = 'series' if filters.get('source_type') == 'franchise' else 'franchise'
        hint = f" It is a {other}: pass it as --{other}."
    raise CommandError(f'No {label} with slug "{slug}".{hint}')


class Command(BaseCommand):
    help = (
        "Build a badge from a franchise, series, both, or developer: the BadgeSeries, its stages, and a hidden "
        "edition per platform group the stages reach."
    )

    def add_arguments(self, parser):
        parser.add_argument('--franchise', help="IGDB franchise slug.")
        parser.add_argument('--series', help="IGDB series (collection) slug.")
        parser.add_argument('--developer', help="Company slug. Not combined with --franchise/--series.")
        parser.add_argument('--slug', help="Badge slug, when the subject's own slug is taken or unwanted.")
        parser.add_argument('--dry-run', action='store_true', help="Build it all, print it, roll it back.")

    def handle(self, *args, **options):
        fields = self._resolve(options)
        subject_slug = fields.pop('slug')
        slug = slugify(options['slug'] or subject_slug)[:_SLUG_MAX]
        if not slug:
            raise CommandError("Could not derive a slug; pass --slug.")
        # The subject check first: when its badge is also on this slug, "pass --slug" would only lead here.
        duplicate = existing_badge(**{key: fields.get(key) for key in ('franchise', 'collection', 'developer')})
        if duplicate is not None:
            raise CommandError(_taken_message(duplicate, fields))
        if BadgeSeries.objects.filter(series_slug=slug).exists():
            raise CommandError(f'A badge already uses the slug "{slug}". Pass --slug to choose another.')
        if Stage.objects.filter(series_slug=slug).exists():
            raise CommandError(f'Stages already use the slug "{slug}" with no badge. Pass --slug to choose another, '
                               f'or delete those stages first.')

        try:
            with transaction.atomic():
                series = self._build(slug, fields)
                if options['dry_run']:
                    raise _DryRunRollback
        except _DryRunRollback:
            self.stdout.write(self.style.WARNING(f'\nDry run: "{slug}" was built and rolled back. Nothing saved.'))
            return

        editions = ", ".join(series.group_badges.order_by('platform_group__sort_order')
                             .values_list('platform_group__name', flat=True))
        self.stdout.write(self.style.SUCCESS(
            f'\nBuilt "{series.name}" ({slug}, {series.get_badge_type_display()}): '
            f'{Stage.objects.filter(series_slug=slug).count()} stage(s), hidden editions: {editions}.'
        ))
        self.stdout.write(
            "Next:\n"
            "  Review the stages in the admin (episodic games need a bundle by hand)\n"
            "  Mark an edition live in the GroupBadge admin\n"
            f"  python manage.py evaluate_badges --series {slug}"
        )

    def _resolve(self, options):
        """The BadgeSeries fields the subject flags decide: badge_type, name, slug, and the subject FKs."""
        franchise_slug, series_slug, developer_slug = options['franchise'], options['series'], options['developer']
        if developer_slug and (franchise_slug or series_slug):
            raise CommandError("--developer builds a developer badge on its own; drop --franchise/--series.")
        if developer_slug:
            developer = _subject(Company, developer_slug, "company")
            return {'badge_type': BADGE_TYPE_DEVELOPER, 'name': developer.name, 'slug': developer.slug,
                    'developer': developer}
        if not (franchise_slug or series_slug):
            raise CommandError("Pass --franchise, --series (or both), or --developer.")

        franchise = collection = None
        if franchise_slug:
            franchise = _subject(Franchise, franchise_slug, "franchise", source_type='franchise')
        if series_slug:
            collection = _subject(Franchise, series_slug, "series", source_type='collection')
        named = collection or franchise
        return {'badge_type': BADGE_TYPE_SERIES if collection else BADGE_TYPE_FRANCHISE, 'name': named.name,
                'slug': named.slug, 'franchise': franchise, 'collection': collection}

    def _build(self, slug, fields):
        try:
            # Its own savepoint, so only a clash on THIS insert (the slug taken since the pre-check) reads as one.
            with transaction.atomic():
                series = BadgeSeries.objects.create(series_slug=slug, **fields)
        except IntegrityError as exc:
            raise CommandError(f'The slug "{slug}" was taken while this ran. Nothing saved; run it again.') from exc
        self.stdout.write(f'Created badge "{series.name}" ({slug}, {series.get_badge_type_display()}).')
        self.stdout.write("")

        call_command('generate_badge_stages', slug, skip_next_steps=True, stdout=self.stdout, stderr=self.stderr)
        if not Stage.objects.filter(series_slug=slug).exists():
            raise CommandError(f'"{series.name}" has no games to build stages from. Nothing saved.')

        self.stdout.write("")
        call_command('convert_series_to_groups', slug, stdout=self.stdout, stderr=self.stderr)
        if not series.group_badges.exists():
            raise CommandError(f'No stage game is on a platform group\'s platforms, so "{slug}" has no '
                               f'edition. Nothing saved.')
        return series
