"""Lower the rarity RATCHET floors: the rarest each gradeable thing has ever been.

Rarity is derived from a denominator that only grows, so a grade drifts DOWN over time -- log in one
day and your Mythic is Rare. That is a grade behaving like a weather report rather than a property of
the thing, and it can quietly take something away. Grading from the floor instead means a grade can
rise but never fall.

The floor stays COMMUNITY-LEVEL: it is a property of the badge or title, identical for every hunter.
(Freezing per hunter at earn time would be worse -- two people would see different grades on the same
item, which is not how anyone reads rarity.)

Runs nightly and is a pure recompute over a bounded set. Safe to re-run; it only ever moves a floor
DOWN, so an extra run can never inflate a grade. Sampling daily means a brief intra-day dip can be
missed -- acceptable, because rarity moves slowly and the alternative is writing on every read.
"""
from django.core.management.base import BaseCommand
from django.db.models import Count

from trophies.models import GroupBadge, SeriesBadgeStanding, Title, UserTitle


class Command(BaseCommand):
    help = "Lower rarity ratchet floors for group badges and titles."

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true',
                            help='Report what would move without writing.')

    def handle(self, *args, **options):
        dry = options['dry_run']

        # The shared denominator: how many profiles are making real progress on each series.
        pursuers = dict(
            SeriesBadgeStanding.objects.values('series_slug')
            .annotate(n=Count('id')).values_list('series_slug', 'n')
        )

        badges = self._badge_floors(pursuers)
        titles = self._title_floors(pursuers)

        if not dry:
            if badges:
                GroupBadge.objects.bulk_update(badges, ['rarity_floor_pct'], batch_size=500)
            if titles:
                Title.objects.bulk_update(titles, ['rarity_floor_pct'], batch_size=500)

        verb = 'would lower' if dry else 'lowered'
        self.stdout.write(self.style.SUCCESS(
            f"{verb} {len(badges)} badge floors and {len(titles)} title floors."
        ))

    @staticmethod
    def _pct(earned, eligible):
        """The live percentage, matching `rarity.rarity_for` exactly -- including its clamp, because
        `earned_count` is a manual denorm a cascade delete can leave above the pursuer base."""
        if not eligible or not earned:
            return None
        return round(min(100.0, 100.0 * earned / eligible), 1)

    def _badge_floors(self, pursuers):
        moved = []
        for gb in GroupBadge.objects.filter(is_live=True).select_related('series').only(
            'id', 'earned_count', 'rarity_floor_pct', 'series__series_slug',
        ):
            pct = self._pct(gb.earned_count, pursuers.get(gb.series.series_slug, 0))
            if pct is None:
                continue
            if gb.rarity_floor_pct is None or pct < gb.rarity_floor_pct:
                gb.rarity_floor_pct = pct
                moved.append(gb)
        return moved

    def _title_floors(self, pursuers):
        """A title's numerator is its HOLDERS, not any badge's earned_count -- a title is granted by
        earning ANY live edition, so it is strictly easier than any single edition, and it is the
        holder count that surfaces print beside the grade."""
        holders = dict(
            UserTitle.objects.values('title_id').annotate(n=Count('id')).values_list('title_id', 'n')
        )
        # A title's eligible base is the pursuer count of the series that grants it. Two series can
        # point at one Title, so take the LARGEST base: the widest population that could have earned it.
        base = {}
        for slug, title_id in GroupBadge.objects.filter(
            is_live=True, series__title__isnull=False,
        ).values_list('series__series_slug', 'series__title_id').distinct():
            n = pursuers.get(slug, 0)
            if n > base.get(title_id, 0):
                base[title_id] = n

        moved = []
        for title in Title.objects.filter(id__in=base).only('id', 'rarity_floor_pct'):
            pct = self._pct(holders.get(title.id, 0), base.get(title.id, 0))
            if pct is None:
                continue
            if title.rarity_floor_pct is None or pct < title.rarity_floor_pct:
                title.rarity_floor_pct = pct
                moved.append(title)
        return moved
