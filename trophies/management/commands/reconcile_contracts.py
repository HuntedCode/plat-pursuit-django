"""Revoke Contract credit that current membership no longer supports.

Contract membership is DERIVED, not stored: `Contract.member_concept_ids` resolves it live from
the anchored + trusted IGDB id. So it changes under hunters' feet -- a Concept SPLIT (two games
wrongly grouped under one concept, pulled apart), a re-anchor onto a different IGDB id, an
IGDBMatch dropping out of TRUSTED_STATUSES, or a staff edit to `Contract.igdb_id`. Detection and
acceptance are both forward-only, so credit for the games that left survives, and where it was
already accepted so does the banked XP. Nothing in the engine subtracts. This command does.

    python manage.py reconcile_contracts --contract <slug>            # preview (default)
    python manage.py reconcile_contracts --contract <slug> --apply    # write
    python manage.py reconcile_contracts --contract <slug> --user <psn> --apply   # one hunter

PREVIEW IS THE DEFAULT and writing takes an explicit `--apply`, the inverse of
`process_contracts --dry-run`. That command only ever adds, so an accidental run is harmless;
this one deletes banked XP, so the accident has to be the one you opt into.

DELIBERATELY SINGLE-CONTRACT, and deliberately NOT on the nightly chain. A sweep of the whole
catalogue would strip real XP the moment a match went `pending_review` mid-rematch or PSN flux
dropped a title out of a hunter's library -- the transient states the "no destructive prune
during PSN flux" rule exists for. Staff name the Contract they have just re-keyed, read the
preview, and apply.

THE ZERO-QUALIFIER REFUSAL IS THE LOAD-BEARING GUARD. When nobody can currently qualify, EVERY
earner reads as orphaned and `--apply` would revoke the entire population -- and that state is
reached by several things that are mistakes rather than splits: `igdb_id` cleared or mistyped,
every member's match sitting at `pending_review` mid-rematch, an anchor stamp cleared. Preview
and apply are separate invocations that each re-resolve membership, so a clean preview does NOT
bind the apply; the guard has to live in the write path. `--force-empty` exists for the genuine
case (a Contract that really has lost every member) and says so out loud.

It asks whether qualification is POSSIBLE, not whether a bundle row exists: an empty bundle --
one `_detect_tiers` can never satisfy -- used to switch the guard off entirely. And it does not
apply to a `--user`-scoped run, where the blast radius is the one hunter named.

What a revoke does per hunter: deletes the EarnedContract (its ContractXPGrant rows cascade) and
rebuilds ProfileJobXP + ProfileCareerStanding from the surviving ledger. ProgressionMilestones
are left intact, and every revoke is logged once its writes have landed -- see
`contract_service.revoke_contract`. Hunters who ALSO completed a concept that is still a member
keep everything: the same detector the sync uses decides, so a genuine qualification is never
touched, and `revoke_contract` re-checks under its lock in case one lands mid-sweep.
"""
import logging

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Sum

from trophies.models import Contract, ContractXPGrant, EarnedContract
from trophies.services.contract_service import (
    _detect_tiers, credit_is_orphaned, revoke_contract,
)

logger = logging.getLogger(__name__)

#: How many orphan lines to print before collapsing to a count. A preview is only useful if it can
#: be read; tens of thousands of lines is the shape you get when something has gone wrong, which is
#: exactly when the summary matters more than the enumeration.
REPORT_LIMIT = 50


class Command(BaseCommand):
    help = "Revoke Contract credit no longer supported by current membership (--contract <slug>)."

    def add_arguments(self, parser):
        parser.add_argument('--contract', required=True,
                            help='Slug of the Contract to reconcile.')
        parser.add_argument('--user',
                            help='Limit to one psn_username. Use this to sanity-check the fix on '
                                 'a single hunter before committing to the whole population.')
        parser.add_argument('--apply', action='store_true',
                            help='Actually revoke. Without it the command only reports.')
        parser.add_argument('--force-empty', action='store_true',
                            help='Permit --apply when the Contract resolves to ZERO members. Only '
                                 'for a Contract that has genuinely lost every member; the '
                                 'refusal exists because that state is usually a mid-rematch or a '
                                 'mistyped igdb_id, where applying would revoke every earner.')

    def handle(self, *args, **opts):
        contract = Contract.objects.filter(slug=opts['contract']).prefetch_related(
            'bundles__concepts').first()
        if contract is None:
            raise CommandError(f"No Contract with slug '{opts['contract']}'.")

        apply_changes = opts['apply']
        self.stdout.write(self.style.MIGRATE_HEADING(
            f"Reconciling '{contract.name}' ({contract.slug}, igdb_id={contract.igdb_id})"
        ))
        if not apply_changes:
            self.stdout.write(self.style.WARNING("PREVIEW -- nothing will be written. Re-run with --apply.\n"))

        # Resolved ONCE: membership is per-contract, not per-hunter (the same shape
        # process_contracts uses). Every detection below reuses this set.
        member_ids = contract.member_concept_ids()
        # `len(... .all())`, not `.count()`: bundles are prefetched above, and an aggregate on a
        # related manager issues a fresh query that silently bypasses the prefetch. Same trap
        # `_detect_tiers` and `process_contracts._candidate_profiles` both carry warnings about.
        bundles = list(contract.bundles.all())
        # A bundle only makes qualification possible if it HAS concepts. Testing `bundles` alone let
        # an EMPTY bundle -- one `_detect_tiers` can never satisfy -- switch the guard off on a
        # contract whose igdb_id had been cleared or mistyped, which is precisely the state the
        # guard exists for. The question is "can anyone qualify at all", not "does a row exist".
        qualifiable = bool(member_ids) or any(b.concepts.all() for b in bundles)
        self.stdout.write(f"  current members: {len(member_ids)} concept(s), {len(bundles)} bundle(s)")

        # Scoped to ONE hunter, the blast radius is that hunter, so the mass-revoke guard has
        # nothing to protect and refusing would only block the deploy checklist's own
        # "spot-check one hunter first" step.
        user = opts.get('user')
        if not qualifiable and not user:
            self._handle_empty_membership(contract, apply_changes, opts['force_empty'])

        orphans = self._find_orphans(contract, member_ids, user)
        if not orphans:
            self.stdout.write(self.style.SUCCESS(
                "\nNo orphaned credit: every stamped row still qualifies under current membership."
            ))
            return

        total_xp = self._report(orphans)
        if not apply_changes:
            self.stdout.write(self.style.WARNING(
                f"\nWould revoke {len(orphans)} row(s) and {total_xp} banked XP. Re-run with --apply."
            ))
            return

        self._apply(contract, orphans)

    def _handle_empty_membership(self, contract, apply_changes, force_empty):
        """Refuse to apply when NOBODY can currently qualify (unless forced).

        Not called for a `--user`-scoped run: the guard exists to stop a mass revoke, and one
        named hunter is not one."""
        warning = (
            f"'{contract.slug}' resolves to ZERO qualifiable members (no members, and no bundle "
            f"with concepts in it), so EVERY earner reads as orphaned."
        )
        if not apply_changes:
            self.stdout.write(self.style.WARNING(
                f"\n  !! {warning}\n"
                f"     If this Contract has not genuinely lost every member, this is a mid-rematch\n"
                f"     (`pending_review`), a cleared anchor stamp, or a wrong igdb_id -- fix that\n"
                f"     first. --apply will refuse without --force-empty."
            ))
            return
        if not force_empty:
            raise CommandError(
                f"{warning} Refusing to apply. This is usually a match in flight or a mistyped "
                f"igdb_id rather than a split -- check `Contract.igdb_id` and the members' "
                f"IGDBMatch status first. Pass --force-empty if the Contract really has lost "
                f"every member and you intend to revoke every earner."
            )
        self.stdout.write(self.style.ERROR(f"\n  !! {warning} Proceeding on --force-empty."))

    # -- the sweep --------------------------------------------------------------------------

    def _find_orphans(self, contract, member_ids, username=None):
        """EarnedContract rows whose stamped credit current membership no longer supports.

        Bounded by this Contract's EARNERS, not the userbase, and each row costs the same two
        `.exists()` queries the sync's detection pays -- flat in profile size, so a
        250,000-trophy hunter is no more expensive here than a new one. Collected in one read
        pass and revoked in a second: the orphan set is small whenever the guard holds (`--user` bounds it to one),
        and it keeps the writes out of the cursor being iterated. (`revoke_contract` re-checks
        under its lock, so a hunter who re-qualifies between the two passes is not revoked.)

        `.only(...)` for the same reason `process_contracts._candidate_profiles` uses it: nothing
        here or in `_report` reads another Profile field, so there is no deferred-field reload.
        """
        rows = (EarnedContract.objects.filter(contract=contract)
                .select_related('profile').order_by('pk')
                .only('platinum_reached_at', 'full_reached_at', 'platinum_accepted_at',
                      'full_accepted_at', 'contract_id', 'profile__psn_username'))
        if username:
            rows = rows.filter(profile__psn_username=username)
        orphans = []
        for ec in rows.iterator(chunk_size=500):
            platinum_reached, full_reached = _detect_tiers(ec.profile, contract, member_ids)
            if credit_is_orphaned(ec, platinum_reached, full_reached):
                orphans.append(ec)
        return orphans

    def _apply(self, contract, orphans):
        """Revoke each orphan in its own transaction, then report what actually happened.

        Per-hunter atomicity rather than one transaction around the sweep: each revoke is
        self-contained, and the command is idempotent, so a run that dies halfway leaves every
        hunter it reached in a consistent state and is simply re-run. One giant transaction would
        instead hold locks across the whole population and roll back completed, correct work.

        One hunter's failure must not abort the rest for the same reason -- a sweep that stops on
        row 400 of 900 leaves a job half-done with no record of where it stopped, so failures are
        logged and counted and the sweep continues.
        """
        revoked = removed = failed = declined = 0
        for ec in orphans:
            try:
                did_revoke, xp = revoke_contract(ec.profile, contract)
            except Exception:
                failed += 1
                logger.exception(
                    "reconcile_contracts: revoke failed for profile=%s contract=%s",
                    ec.profile_id, contract.slug,
                )
                self.stderr.write(self.style.ERROR(
                    f"    FAILED {ec.profile.psn_username} -- logged, continuing."
                ))
                continue
            if did_revoke:
                revoked += 1
                removed += xp
            else:
                # Re-qualified between the find pass and the write pass, and `revoke_contract`
                # declined under its lock. Correct, and worth surfacing rather than hiding.
                declined += 1
        self.stdout.write(self.style.SUCCESS(
            f"\nRevoked {revoked} row(s); removed {removed} banked XP. "
            f"Job XP and career standings rebuilt for each hunter."
        ))
        if declined:
            self.stdout.write(
                f"  {declined} skipped: re-qualified after the scan (a sync landed mid-sweep)."
            )
        if failed:
            self.stdout.write(self.style.ERROR(
                f"  {failed} FAILED -- see the log, then re-run (the command is idempotent)."
            ))

    def _report(self, orphans):
        """Print the orphans (capped) and return the total banked XP at stake."""
        # ONE grouped aggregate for the whole set rather than a query per row (whale-OOM rule
        # applies to staff commands too). Keyed on ids, not model instances: an `__in` of
        # instances is one SQL parameter per row, the multi-megabyte-statement shape
        # `process_contracts._candidate_profiles` carries a warning about.
        xp_by_ec = dict(
            ContractXPGrant.objects.filter(earned_contract_id__in=[ec.id for ec in orphans])
            .values('earned_contract').annotate(t=Sum('amount'))
            .values_list('earned_contract', 't')
        )
        total_xp = sum(xp_by_ec.values())
        banked = sum(1 for ec in orphans
                     if ec.platinum_accepted_at or ec.full_accepted_at)
        self.stdout.write(f"\n  {len(orphans)} orphaned row(s):")
        for ec in orphans[:REPORT_LIMIT]:
            xp = xp_by_ec.get(ec.id, 0)
            tiers = [name for name, stamp in (('platinum', ec.platinum_reached_at),
                                              ('100%', ec.full_reached_at)) if stamp]
            accepted = [name for name, stamp in (('platinum', ec.platinum_accepted_at),
                                                 ('100%', ec.full_accepted_at)) if stamp]
            state = (f"ACCEPTED {'+'.join(accepted)} -> -{xp} XP" if accepted
                     else "unaccepted (clears a stale claimable, no XP)")
            self.stdout.write(f"    {ec.profile.psn_username}: reached {'+'.join(tiers)}; {state}")
        if len(orphans) > REPORT_LIMIT:
            self.stdout.write(f"    ... and {len(orphans) - REPORT_LIMIT} more (not listed).")
        self.stdout.write(
            f"\n  {banked} of {len(orphans)} had banked XP; {total_xp} XP total at stake."
        )
        return total_xp
