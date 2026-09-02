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
that title, as a `badge_series` row.

COST, honestly: writes are set-based (bulk_create + two UPDATEs per title, batched), but the DIFF is
computed in Python, so it materializes two dicts keyed by profile for each title -- memory scales with a
title's holder count, not just the catalogue. Fine for a one-off backfill at current scale; if a single
title ever reaches six figures of holders this wants rewriting as `INSERT ... SELECT`. (An earlier
docstring here claimed it "does not care how many hunters there are." It does, and saying otherwise is
how the next reader skips the check.)

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

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import F, Min, OuterRef, Subquery
from django.db.models.functions import Coalesce

from trophies.models import BadgeSeries, UserGroupBadge, UserTitle
from trophies.services.badge_adapters import LEGACY_TITLE_SOURCE, TITLE_SOURCE

#: bulk_create emits ONE statement per batch. Unbounded, a large backfill builds a multi-megabyte INSERT
#: and can trip the 60s statement_timeout partway through. The UPDATEs are chunked on the SAME size --
#: batching only the insert left `profile_id__in=<every id>` on the two updates, which is a bigger
#: statement than the insert the batching was added to split.
BATCH = 2000


def _chunks(seq, size=BATCH):
    for i in range(0, len(seq), size):
        yield seq[i:i + size]


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
                # CommandError, not stderr + exit 0: a deploy script reads the exit code, and "no such
                # slug" must not be indistinguishable from "nothing to do".
                raise CommandError(
                    f"No title-granting series '{opts['series']}' -- unknown slug, or it grants no title."
                )
            series_qs = series_qs.filter(title_id__in=title_ids)

        by_title = defaultdict(list)
        for series in series_qs:
            by_title[series.title_id].append(series)
        if not by_title:
            self.stdout.write("No title-granting series.")
            return

        granted = adopted = pruned = skipped = 0
        for title_id, series_group in sorted(by_title.items(), key=lambda kv: kv[1][0].title.name):
            title = series_group[0].title
            # Earliest badge earn per profile across every series granting this title -- the honest
            # earned_at. auto_now_add would stamp the backfill date on every row, so a hunter's Titles
            # page would claim they earned a five-year-old title this morning, and "Yours" (most recent
            # first) would order by when the backfill ran.
            # READ ORDER IS LOAD-BEARING: existing rows FIRST, badge holders second. The live engine grants
            # a title the instant a badge is earned, so a hunter earning one BETWEEN these two reads would
            # otherwise be absent from `earned` but present in `existing` -- landing in `orphaned`, where
            # --prune deletes the row the engine just wrote. In this order such a hunter is missing from
            # both sets, so the worst case is that the next run grants them.
            existing = dict(
                UserTitle.objects.filter(title_id=title_id).values_list('profile_id', 'source_type')
            )
            earned = dict(
                UserGroupBadge.objects
                .filter(group_badge__series__in=series_group)
                .values('profile_id').annotate(first=Min('earned_at'))
                .values_list('profile_id', 'first')
            )
            ours = {pid for pid, src in existing.items() if src == TITLE_SOURCE}

            missing = sorted(set(earned) - set(existing))
            # Holds the badge AND a LEGACY row -- adopt it. The hunter earned this through the new system;
            # the row just predates it (or was returned untouched by get_or_create). Leaving it means they
            # stay uncountable and see "Be the first" on a title they are wearing.
            #
            # Legacy ONLY, deliberately not "anything that isn't ours": a 'milestone' row is a one-off
            # award with its own label ("Special award") and its own dashboard bucket, so claiming one
            # would reclassify a hunter's award and overwrite the source_id pointing at what granted it.
            adopt = sorted(pid for pid in earned if existing.get(pid) == LEGACY_TITLE_SOURCE)
            orphaned = sorted(ours - set(earned))
            # Holds the badge and a row this system will NOT claim (a one-off award). Skipping them is
            # right; skipping them SILENTLY is not -- they keep the exact symptom this command exists to
            # cure (uncountable holder, "Be the first" on a worn title) with nothing to point at. Counted
            # and reported so the operator can decide, rather than discovered later as a rarity anomaly.
            unclaimable = sorted(
                pid for pid in earned
                if pid in existing and existing[pid] not in (TITLE_SOURCE, LEGACY_TITLE_SOURCE)
            )

            if missing or adopt or orphaned or unclaimable:
                self.stdout.write(
                    f"  {title.name}: {len(earned)} hold a badge, {len(ours)} countable"
                    f" -> +{len(missing)} granted, +{len(adopt)} adopted"
                    + (f", {len(orphaned)} orphaned" if orphaned else "")
                    + (f", {len(unclaimable)} held under another source (left alone)" if unclaimable else "")
                )

            # ONE transaction per title. The insert and the earned_at correction MUST commit together:
            # `earned_at` is auto_now_add, so bulk_create stamps every row with now() and a second pass
            # fixes it -- and if the process died between the two, the surviving rows kept the BACKFILL
            # CLOCK forever. A re-run could not repair them either: they are in `existing` by then, so they
            # appear in neither `missing` nor `adopt`. The command could never converge, which is the one
            # thing a backfill has to be able to do.
            if (adopt or missing) and not dry:
                with transaction.atomic():
                    if adopt:
                        # earned_at left alone: a real date from a real earn. A bookkeeping correction, not
                        # a re-grant -- rewriting it would move the title in the "Yours" ordering.
                        for batch in _chunks(adopt):
                            UserTitle.objects.filter(title_id=title_id, profile_id__in=batch).update(
                                source_type=TITLE_SOURCE, source_id=series_group[0].id,
                            )
                    if missing:
                        # source_id: the first series granting it. Advisory only -- nothing reads it to
                        # decide whether the title is held, and a shared title has no single source.
                        UserTitle.objects.bulk_create([
                            UserTitle(profile_id=pid, title_id=title_id, source_type=TITLE_SOURCE,
                                      source_id=series_group[0].id)
                            for pid in missing
                        ], ignore_conflicts=True, batch_size=BATCH)   # conflicts = concurrent-run guard
                        # Correct the auto_now_add stamp to the badge date in ONE statement. This was a
                        # loop grouped by timestamp, described in its own comment as "a handful of UPDATEs,
                        # not one per profile" -- but earned_at is microsecond-precision, so every profile
                        # WAS its own group. It was one query per row wearing a reassuring comment.
                        first_earn = (
                            UserGroupBadge.objects
                            .filter(profile_id=OuterRef('profile_id'), group_badge__series__in=series_group)
                            .values('profile_id').annotate(m=Min('earned_at')).values('m')
                        )
                        # Coalesce because earned_at is NOT NULL: if a concurrent revoke deletes the
                        # profile's last hold between the read above and this write, the Subquery yields
                        # NULL and the UPDATE would raise, rolling back this title's inserts too. Falling
                        # back to the row's own value leaves the backfill stamp, which the next run's
                        # report still surfaces -- better than an aborted batch.
                        for batch in _chunks(missing):
                            UserTitle.objects.filter(
                                title_id=title_id, profile_id__in=batch, source_type=TITLE_SOURCE,
                            ).update(earned_at=Coalesce(Subquery(first_earn), F('earned_at')))
            granted += len(missing)
            adopted += len(adopt)
            skipped += len(unclaimable)

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
        if skipped:
            # Not an error, and not fixable from here: a one-off award and a series title share one row,
            # and this system does not get to reclassify the award. Named so it can be judged.
            self.stdout.write(self.style.WARNING(
                f"{skipped} hunter(s) hold a badge but their title row belongs to another source "
                f"(a one-off award). Left as-is: they stay uncountable for rarity."
            ))
        if not prune:
            self.stdout.write("(orphaned titles left alone; pass --prune to remove them)")
