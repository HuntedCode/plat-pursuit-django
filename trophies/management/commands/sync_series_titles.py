"""Reconcile UserTitle against the badges actually held, for the NEW badge system.

Two ways a held badge ends up with no COUNTABLE title, both invisible until rarity is graded off the
holder count:

1. **The grant never ran.** `grant_series_title` fires from exactly one place: the `award` branch of
   `badge_apply.apply_changes`. And `diff` only emits `award` when the profile does NOT already hold the
   badge. So the grant is a one-shot at the moment of earning -- a hunter who earned a badge BEFORE its
   series had a title can never be fixed by re-running `evaluate_badges`, because the diff is empty and
   the grant never runs.

2. **The grant ran and recorded nothing.** `grant_series_title` warns about this in its own docstring:
   UserTitle is unique on (profile, title) WITHOUT source_type, so when a series reuses a legacy Badge's
   Title, `get_or_create` returns the pre-existing legacy row and leaves `source_type='badge'`. The
   hunter holds the title -- the page shows it, they can equip it -- but every count filtered to
   `badge_series` (which is all of them, by design) cannot see them.

Both corrupt rarity the same way. A title is granted by ANY live edition, so its holders are a UNION and
it can never be rarer than the single easiest edition -- yet a title whose Ultra HD edition most of the
community holds can read 0.7% and grade Mythic. Case 2 goes further and shows "Be the first" on a title
the viewer is wearing.

This walks the other direction: every profile holding a badge in a title-granting series SHOULD hold
that title, as a `badge_series` row. Set-based (a few id reads and bulk writes per title), so it does not
care how many hunters there are.

    python manage.py sync_series_titles --dry-run           # report the gap, write nothing
    python manage.py sync_series_titles                     # grant what is missing
    python manage.py sync_series_titles --series god-of-war # scope to one series
    python manage.py sync_series_titles --prune             # ALSO delete titles no badge backs

Grouped by TITLE, not by series: `BadgeSeries.title` has no unique constraint, so two series can grant
one title and a profile holding either has earned it. Reconciling per series would have the second
series' pass delete what the first just granted.

`--prune` is opt-in on purpose. Deleting a title a hunter can see they earned is not something to do as
a side effect of a backfill, and the orphan set includes anything whose GroupBadge rows were deleted
during re-authoring -- a re-author would silently strip titles from everyone who held them.
"""
from collections import defaultdict

from django.core.management.base import BaseCommand
from django.db.models import Min

from trophies.models import BadgeSeries, UserGroupBadge, UserTitle
from trophies.services.badge_adapters import TITLE_SOURCE


class Command(BaseCommand):
    help = "Grant missing UserTitle rows for badges already held (new badge system)."

    def add_arguments(self, parser):
        parser.add_argument('--series', help="Scope to one series_slug (its title is still reconciled in full).")
        parser.add_argument('--dry-run', action='store_true', help="Report the gap; write nothing.")
        parser.add_argument('--prune', action='store_true',
                            help="Also DELETE badge_series titles no held badge backs. Opt-in: see the module docstring.")

    def handle(self, *args, **opts):
        dry, prune = opts['dry_run'], opts['prune']

        # Group series by the title they grant. A title is earned by holding a badge in ANY of them.
        series_qs = BadgeSeries.objects.filter(title__isnull=False).select_related('title')
        if opts['series']:
            title_ids = set(series_qs.filter(series_slug=opts['series']).values_list('title_id', flat=True))
            if not title_ids:
                self.stderr.write(f"No title-granting series '{opts['series']}'.")
                return
            series_qs = series_qs.filter(title_id__in=title_ids)

        by_title = defaultdict(list)
        for series in series_qs:
            by_title[series.title_id].append(series)
        if not by_title:
            self.stdout.write("No title-granting series.")
            return

        granted = adopted = pruned = 0
        for title_id, series_group in sorted(by_title.items(), key=lambda kv: kv[1][0].title.name):
            title = series_group[0].title
            # Earliest badge earn per profile across every series granting this title -- the honest
            # earned_at. auto_now_add would stamp the backfill date on every row, so a hunter's Titles
            # page would claim they earned a five-year-old title this morning, and "Yours" (most recent
            # first) would order by when the backfill ran.
            earned = dict(
                UserGroupBadge.objects
                .filter(group_badge__series__in=series_group)
                .values('profile_id').annotate(first=Min('earned_at'))
                .values_list('profile_id', 'first')
            )
            # ALL rows on this Title, whatever created them -- the legacy ones are the second bug.
            existing = dict(
                UserTitle.objects.filter(title_id=title_id).values_list('profile_id', 'source_type')
            )
            ours = {pid for pid, src in existing.items() if src == TITLE_SOURCE}

            missing = sorted(set(earned) - set(existing))
            # Holds the badge AND a row, but the row belongs to another system -- adopt it. The hunter
            # earned this through the new system; the row just predates it (or was returned untouched by
            # get_or_create). Leaving it means they stay uncountable and see "Be the first" on a title
            # they are wearing.
            adopt = sorted(pid for pid in earned if existing.get(pid, TITLE_SOURCE) != TITLE_SOURCE)
            orphaned = sorted(ours - set(earned))

            if missing or adopt or orphaned:
                self.stdout.write(
                    f"  {title.name}: {len(earned)} hold a badge, {len(ours)} countable"
                    f" -> +{len(missing)} granted, +{len(adopt)} adopted"
                    + (f", {len(orphaned)} orphaned" if orphaned else "")
                )

            if adopt and not dry:
                # earned_at is left alone: it is a real date from a real earn, and this is a bookkeeping
                # correction, not a re-grant. Rewriting it would move the title in the "Yours" ordering.
                UserTitle.objects.filter(title_id=title_id, profile_id__in=adopt).update(
                    source_type=TITLE_SOURCE, source_id=series_group[0].id,
                )
            adopted += len(adopt)

            if missing and not dry:
                # source_id: the first series granting it. Advisory only -- nothing reads it to decide
                # whether the title is held, and a shared title has no single source by construction.
                UserTitle.objects.bulk_create([
                    UserTitle(profile_id=pid, title_id=title_id, source_type=TITLE_SOURCE,
                              source_id=series_group[0].id)
                    for pid in missing
                ], ignore_conflicts=True)   # `missing` is rows that do not exist, so this is a race guard
                                            # only -- a concurrent sync granting the same title mid-run
                                            # must not abort every other grant in the batch.
                # earned_at is auto_now_add, so bulk_create stamped it with now(); correct it to the badge
                # date. Grouped by timestamp so this is a handful of UPDATEs, not one per profile.
                by_date = defaultdict(list)
                for pid in missing:
                    by_date[earned[pid]].append(pid)
                for when, pids in by_date.items():
                    UserTitle.objects.filter(title_id=title_id, profile_id__in=pids,
                                             source_type=TITLE_SOURCE).update(earned_at=when)
            granted += len(missing)

            if orphaned and prune:
                if not dry:
                    UserTitle.objects.filter(title_id=title_id, profile_id__in=orphaned,
                                             source_type=TITLE_SOURCE).delete()
                pruned += len(orphaned)

        verb = "Would grant" if dry else "Granted"
        line = f"\n{verb} {granted} title(s), {'would adopt' if dry else 'adopted'} {adopted}"
        if prune:
            line += f", {'would prune' if dry else 'pruned'} {pruned}"
        self.stdout.write(self.style.SUCCESS(line + "."))
        if not prune:
            self.stdout.write("(orphaned titles left alone; pass --prune to remove them)")
