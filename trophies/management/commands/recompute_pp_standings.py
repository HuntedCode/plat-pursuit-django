"""Rebuild `ProfilePPStanding` -- the PP Score board's store -- from EarnedTrophy.

PP Score sums `100 / earn_rate` over a hunter's rarest 1,000 base-game trophies. The RULE lives in
`services/pp_score.py`; this command only applies it.

THE ONLY WRITER, like every standing store here. There is no incremental path and there should not be
one: a hunter's score depends on WHICH of their trophies are the rarest thousand, so a single new trophy
can displace another out of the scoring set and change the total by more than its own worth. That is not
something a per-row signal can compute without redoing the whole selection anyway.

NO NIGHTLY DEPENDENCY, unlike `recompute_clean_standings`. It reads `Trophy.trophy_earn_rate`, which is
PSN's global figure written during SYNC -- not our `Trophy.earn_rate`, which `recalc_earn_rates` writes.
So nothing earlier in the chain has to run first, and it needs no `DEPENDS_ON` entry.

IT AGGREGATES, IT DOES NOT SORT, and that is the difference between affordable and not. The obvious
implementation orders a hunter's earned trophies by rarity and slices the first thousand: for a
250,000-trophy hunter that is a 250,000-row sort, per hunter, with no index able to serve it (the rate
lives on `Trophy`, so there is no `(profile, rarity)` ordering to walk). This codebase has already
dropped one query of that shape on cost -- Browse Hunters' `rarest_avg_plat`.

Instead it groups by RATE and counts, then walks the buckets rarest-first taking `min(n, remaining)` from
each. PSN reports rates to one decimal, so there are at most ~1,000 distinct values across the entire
catalogue: the result set is bounded by the RATE VOCABULARY rather than by library size, and a hash
aggregate in bounded `work_mem` replaces the sort. The answer is identical, because every figure this
store holds is a function of the rate alone -- so it does not matter WHICH members of a tied bucket the
boundary happens to take. That equivalence is what makes ties safe, and it stops being true the moment
anything row-identified is stored (a rarest-trophy pointer, a per-trophy breakdown, the scoring list).
"""
import time

from django.core.cache import cache
from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Count, Q
from django.utils import timezone

from trophies.models import EarnedTrophy, Profile, ProfilePPStanding
from trophies.services import pp_score

#: Where a budget-capped sweep left off. Not a source of truth: losing it re-runs a full recompute, which
#: is a no-op, and the sweep always advances at least one chunk so it cannot stall.
CURSOR_KEY = 'lb:pp_standings:cursor'
CURSOR_TTL = 60 * 60 * 24 * 7

#: The rate column, reached from EarnedTrophy. Named once because it appears in the values(), the
#: annotate ordering and the bucket read, and a typo in one of the three is a silently different board.
RATE = 'trophy__trophy_earn_rate'


class Command(BaseCommand):
    help = 'Rebuild ProfilePPStanding (the PP Score board) from EarnedTrophy.'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true',
                            help='Compute and report deltas without writing.')
        parser.add_argument('--chunk-size', type=int, default=200,
                            help='Profiles aggregated per query.')
        parser.add_argument('--max-minutes', type=int, default=30,
                            help='Wall-clock budget. Always completes at least one chunk.')
        parser.add_argument('--profile-ids', nargs='*', type=int, default=None,
                            help='Optional subset, for ad-hoc repair runs.')

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        # Zero is a ZeroDivisionError below and a negative makes `range` empty -- which would write
        # nothing and still report success, so a typo'd flag would look like a clean run.
        chunk_size = max(1, options['chunk_size'])
        deadline = time.monotonic() + options['max_minutes'] * 60
        start = time.monotonic()

        explicit = options['profile_ids']
        after_id = 0 if explicit else (cache.get(CURSOR_KEY) or 0)
        all_ids = sorted(set(explicit)) if explicit else list(self._population(after_id))

        total = len(all_ids)
        chunks_total = (total + chunk_size - 1) // chunk_size
        self.stdout.write(self.style.NOTICE(
            f'recompute_pp_standings starting: {total} profiles, chunk={chunk_size}, '
            f'budget={options["max_minutes"]}min, dry_run={dry_run}'
            + (f', resuming after profile {after_id}' if after_id else '')
        ))

        written = 0
        chunks_done = 0
        capped = False
        for chunk_start in range(0, total, chunk_size):
            # ALWAYS ONE CHUNK. Checking the deadline before any work means a budget too small for a
            # single chunk does nothing, advances no cursor, and does the same nothing every night --
            # the starvation the cursor exists to end, reintroduced by the guard meant to bound the run.
            if chunks_done and time.monotonic() >= deadline:
                capped = True
                break
            chunk = all_ids[chunk_start:chunk_start + chunk_size]
            written += self._process_chunk(chunk, dry_run)
            chunks_done += 1
            if not explicit and not dry_run:
                cache.set(CURSOR_KEY, chunk[-1], CURSOR_TTL)

        if not explicit and not dry_run and not capped:
            cache.delete(CURSOR_KEY)          # a full pass finished; the next starts from the top

        if capped:
            self.stdout.write(self.style.WARNING(
                f'Hit max-minutes budget after {chunks_done}/{chunks_total} chunks. '
                f'{chunks_total - chunks_done} deferred; the next run resumes after profile '
                f'{all_ids[chunk_start - 1] if chunks_done else after_id}.'
            ))
        verb = 'Would write' if dry_run else 'Wrote'
        self.stdout.write(self.style.SUCCESS(
            f'recompute_pp_standings complete in {time.monotonic() - start:.1f}s. '
            f'{verb} {written} standing(s) across {chunks_done} chunk(s).'
        ))

    @staticmethod
    def _population(after_id=0):
        """LINKED hunters, plus anyone who already has a row, ascending by id.

        Same rule and the same reasons as `recompute_clean_standings._population`: the second half catches
        a hunter who UNLINKED or whose trophies were removed, whose stale figures a linked-only sweep
        would never revisit; and linked-only on the first half because this store serves one board and
        nothing else, so a row for an unlinked profile would be computed, stored and never read.

        ORDERED, because the cursor resumes by id. An unordered population makes "where we left off"
        meaningless and decides arbitrarily which hunters get starved by a budget cap.
        """
        return (
            Profile.objects
            .filter(Q(is_linked=True) | Q(pp_standing__isnull=False))
            .filter(id__gt=after_id)
            .order_by('id')
            .values_list('id', flat=True)
        )

    def _process_chunk(self, profile_ids, dry_run):
        """One GROUP BY for the chunk's rate buckets, one read of the profiles, one create + one update."""
        now = timezone.now()

        # (profile, rate) -> how many of that hunter's earned, scorable trophies sit at that rate.
        # `scorable_earned` is what applies the base-game / known-rate / earned filters TOGETHER -- see its
        # docstring for why they must not be written apart here.
        buckets = {}
        rows = (
            pp_score.scorable_earned(EarnedTrophy.objects.filter(profile_id__in=profile_ids))
            .values('profile_id', RATE)
            .annotate(n=Count('id'))
            .order_by('profile_id', RATE)          # rarest first within each hunter
        )
        for r in rows:
            buckets.setdefault(r['profile_id'], []).append((r[RATE], r['n']))

        owners = dict(Profile.objects.filter(id__in=profile_ids).values_list('id', 'country_code'))
        linked = set(
            Profile.objects.filter(id__in=profile_ids, is_linked=True).values_list('id', flat=True)
        )
        existing = {
            s.profile_id: s for s in ProfilePPStanding.objects.filter(profile_id__in=profile_ids)
        }

        to_create, to_update = [], []
        for pid in profile_ids:
            if pid not in owners:
                continue                       # deleted between the population read and now
            score, avg_rate, scored = self._score(buckets.get(pid, ()))
            fields = {
                'pp_score': score, 'avg_earn_rate': avg_rate, 'scored_count': scored,
                'country_code': owners[pid] or '', 'is_linked': pid in linked,
            }

            row = existing.get(pid)
            if row is None:
                to_create.append(ProfilePPStanding(profile_id=pid, **fields))
                continue
            if any(getattr(row, k) != v for k, v in fields.items()):
                for k, v in fields.items():
                    setattr(row, k, v)
                # `updated_at` is auto_now, which Django applies in Model.save(); bulk_update does not
                # call it, so without this the stamp freezes at row creation while the figures move.
                row.updated_at = now
                to_update.append(row)

        if not dry_run:
            with transaction.atomic():
                if to_create:
                    ProfilePPStanding.objects.bulk_create(to_create, batch_size=500)
                if to_update:
                    ProfilePPStanding.objects.bulk_update(
                        to_update,
                        ['pp_score', 'avg_earn_rate', 'scored_count', 'country_code', 'is_linked',
                         'updated_at'],
                        batch_size=500,
                    )
        return len(to_create) + len(to_update)

    @staticmethod
    def _score(rate_buckets):
        """(pp_score, avg_earn_rate, scored_count) from `[(rate, count), ...]` sorted rarest-first.

        Walks buckets taking `min(n, remaining)` from each, so the boundary bucket contributes only its
        share. Every figure here is a function of the RATE, which is what makes it irrelevant that a tied
        bucket's members are interchangeable.

        ROUNDED, not truncated. `pp_score` is a PositiveIntegerField and Django's `get_prep_value` is
        `int(value)`, so storing 41210.999 yields 41210 -- a consistent downward bias of about half a
        point per hunter. Invisible in magnitude, but it makes the stored figure disagree with the one the
        formula produces, which is the kind of gap that costs an afternoon later.
        """
        remaining = pp_score.TOP_N
        total_points = 0.0
        total_rate = 0.0
        scored = 0

        for rate, n in rate_buckets:
            # An OPTIMISATION, not the cap. Correctness is `min(n, remaining)` below: once `remaining`
            # reaches 0 every later bucket takes 0 and contributes nothing, so removing this break gives
            # an identical answer more slowly. Worth knowing before anyone "simplifies" the cap by
            # deleting the wrong one of the two -- a whale can carry several hundred buckets.
            if remaining <= 0:
                break
            take = min(n, remaining)
            total_points += take * pp_score.points_for(rate)
            total_rate += take * rate
            scored += take
            remaining -= take

        avg_rate = (total_rate / scored) if scored else 0.0
        return round(total_points), avg_rate, scored
