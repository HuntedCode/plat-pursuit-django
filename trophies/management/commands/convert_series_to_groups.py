"""Scaffold the new grouping badges (BadgeSeries + GroupBadge) from an existing badge series.

For a series_slug it: copies the metadata off the legacy tier-1 Badge onto a new BadgeSeries, auto-detects which
PlatformGroups the series' stages' games actually span, and creates a dormant (is_live=False) GroupBadge per
spanned group. Stages are REUSED as-is (read-only) -- not touched. Idempotent: re-running never duplicates and
never clobbers a BadgeSeries you've since hand-edited (it skips an existing one). `--dry-run` reports the plan
(spanned groups, unmapped platforms) and writes nothing.

Also handles a FRESH series: if you've created stages + a BadgeSeries by hand (no legacy Badge), it just
scaffolds the GroupBadges for the detected groups.

    python manage.py convert_series_to_groups god-of-war
    python manage.py convert_series_to_groups --all --dry-run

See docs/design/rebuild/badge-backend-rebuild.md.
"""
from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Q

from trophies.models import Badge, BadgeSeries, GroupBadge, PlatformGroup, Game


def _detect_groups(slug):
    """Return (spanned_groups, unmapped_platforms, game_count) for a series' stages' games (standalone +
    bundle members)."""
    games = list(
        Game.objects.filter(
            Q(concept__stages__series_slug=slug) | Q(concept__bundles__stage__series_slug=slug)
        ).distinct().only('title_platform')
    )
    groups = list(PlatformGroup.objects.filter(is_active=True))
    spanned = [g for g in groups if any(g.matches_platforms(game.title_platform) for game in games)]
    mapped = set()
    for g in groups:
        mapped.update(g.platforms)
    present = set()
    for game in games:
        present.update(game.title_platform or [])
    return spanned, sorted(present - mapped), len(games)


def _metadata_from(base):
    """Map a legacy tier-1 Badge's metadata onto BadgeSeries fields (effective_* honors base_badge inheritance)."""
    if base.badge_type == 'megamix' and not base.requires_all:
        policy, min_required = 'min_count', base.min_required
    else:
        policy, min_required = 'all', 0
    return {
        'name': base.effective_display_series or base.name,
        'description': base.description,
        'badge_type': base.badge_type,
        'completion_policy': policy,
        'min_required': min_required,
        'display_series': base.effective_display_series or '',
        'franchise': base.effective_franchise,
        'collection': base.effective_collection,
        'developer': base.effective_developer,
        # Reuse the legacy Title row (Title.name is unique, so we can't mint a duplicate). The interim is safe
        # (new badges dormant -> no grants); the cutover backfill migrates old UserTitle rows to 'badge_series'.
        'title': base.title,
        'submitted_by': base.effective_submitted_by,
        'funded_by': base.effective_funded_by,
        # Carry the subject illustration over as the series default (group backgrounds/medallion are separate,
        # on PlatformGroup). We copy the file REFERENCE, not the file -- the new series points at the legacy
        # image path, so don't purge old badge image files until final art is uploaded. Holo art stays blank.
        'badge_image': base.badge_image.name if base.badge_image else None,
    }


class Command(BaseCommand):
    help = "Scaffold new grouping badges (BadgeSeries + GroupBadge) from existing badge series."

    def add_arguments(self, parser):
        parser.add_argument('slug', nargs='?', help="A single series_slug to convert.")
        parser.add_argument('--all', action='store_true', help="Convert every live legacy badge series.")
        parser.add_argument('--dry-run', action='store_true', help="Report the plan; write nothing.")

    def handle(self, *args, **opts):
        dry = opts['dry_run']
        if opts['all']:
            slugs = sorted(
                Badge.objects.filter(is_live=True).exclude(series_slug__isnull=True).exclude(series_slug='')
                .values_list('series_slug', flat=True).distinct()
            )
        elif opts['slug']:
            slugs = [opts['slug']]
        else:
            self.stderr.write("Provide a series_slug or --all.")
            return

        totals = {'series_created': 0, 'group_badges_created': 0, 'skipped': 0}
        for slug in slugs:
            self._convert(slug, dry, totals)

        verb = "Would create" if dry else "Created"
        self.stdout.write(self.style.SUCCESS(
            f"\n{verb}: {totals['series_created']} series, {totals['group_badges_created']} group badges "
            f"({totals['skipped']} skipped)."
        ))

    def _convert(self, slug, dry, totals):
        series = BadgeSeries.objects.filter(series_slug=slug).first()
        base = (
            Badge.objects.filter(series_slug=slug, tier=1)
            .select_related('title', 'franchise', 'collection', 'developer', 'funded_by', 'submitted_by', 'base_badge')
            .first()
        )
        if series is None and base is None:
            self.stdout.write(f"  {slug}: skip (no legacy Badge and no BadgeSeries)")
            totals['skipped'] += 1
            return

        spanned, unmapped, game_count = _detect_groups(slug)
        if unmapped:
            self.stdout.write(self.style.WARNING(f"  {slug}: platforms in no group (ignored): {', '.join(unmapped)}"))
        if not spanned:
            self.stdout.write(self.style.WARNING(f"  {slug}: skip (no games map to any platform group; {game_count} games)"))
            totals['skipped'] += 1
            return

        group_names = ', '.join(g.name for g in spanned)
        if dry:
            action = "reuse existing BadgeSeries" if series else "create BadgeSeries"
            self.stdout.write(f"  {slug}: {action} + group badges [{group_names}] ({game_count} games)")
            totals['series_created'] += 0 if series else 1
            totals['group_badges_created'] += len(spanned)   # dry estimate; existing ones would be skipped
            return

        with transaction.atomic():
            if series is None:
                series = BadgeSeries.objects.create(series_slug=slug, **_metadata_from(base))
                totals['series_created'] += 1
                self.stdout.write(self.style.SUCCESS(f"  {slug}: created BadgeSeries '{series.name}'"))
            else:
                self.stdout.write(f"  {slug}: BadgeSeries exists, adding missing group badges")
                # Fill the default art if it's still empty (non-clobbering) -- backfills a series scaffolded
                # before art-copy existed. Never overwrites art you've set.
                if base and base.badge_image and not series.badge_image:
                    series.badge_image = base.badge_image.name
                    series.save(update_fields=['badge_image'])
                    self.stdout.write("    filled missing default art from the legacy badge")

            for group in spanned:
                _, created = GroupBadge.objects.get_or_create(
                    series=series, platform_group=group, defaults={'is_live': False},
                )
                if created:
                    totals['group_badges_created'] += 1
            self.stdout.write(f"    groups: {group_names}")
