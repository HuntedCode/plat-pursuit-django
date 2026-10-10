"""The Hall of Fame plaque's data: the Pursuer Card spine, batched across a whole page of finished runs.

WHY THIS EXISTS AS ITS OWN SERVICE. The plaque is the **Mini** size of the **Pursuer Card**, one of the
four primitives in `docs/design/visual-identity.md`, whose locked size list includes
"Mini (comments/leaderboards)".

THE WARRANT IS THE PURSUER CARD LANE'S OWN DEFERRAL LIST, which names "a compact leaderboard 'plaque'
variant" among the surfaces awaiting their own redesign. An earlier version of this paragraph cited
`visual-identity.md`'s "'earned by these Pursuers' panels" instead -- that line is scoped **on Badge
detail**, so the citation was both inaccurate and weaker than the one actually available. It also called
this the "compact variant", which collides with a DIFFERENT locked size ("Compact (profile header)").

The primitive's documented spine is "Avatar, Pursuer Name, Pursuer Level, active Title, top Job, recent
badge peek" -- quoted in full, because an earlier version dropped the sixth item while presenting it as
verbatim. The plaque had none of the gamification half of it: an avatar, a name and a subtitle, which is the
FIRST entry on the primitive's own anti-pattern list, "Generic 'user profile card' (avatar circle +
username + bio, like every social app)".

NOT A MOUNT OF `pursuer_card_service`, deliberately. That service builds the FULL card from
`career_service`'s hero -- platinum showcase, DNA ring, rarest/recent slices -- per profile. A page holds
eight finished runs, so mounting it would run a whole Career build eight times on an anonymous, enumerable,
uncached URL. The memory of the Pursuer Card lane records propagation to other surfaces as "DEFERRED, each
riding its own surface's future redesign (do NOT force onto legacy pages)": riding means adapting the
VOCABULARY to the surface's budget, not importing the hero.

THE DISCIPLINES COME FROM THE LIVE RING, NOT FROM A BAND OF MY OWN, and the first cut got this wrong in a
way worth recording. It built a bespoke five-tile band modelled on `pursuer-card.css`'s, justified by the
Pursuer Card's anti-pattern about "inconsistent treatments between hero/compact/mini". The Pursuer Card is
MOUNTED ON NOTHING in production -- `4a730fd9` ("make / the lobby", 2026-08-13) dropped it from the home
hero and nothing re-mounted it -- and that same commit put the real primitive in its place:
`partials/components/_disciplines_ring.html` (`.lab-dna`), live on BOTH the Career hero and the home lobby,
whose own docstring says the second host is "a scale, not a second implementation, which is what keeps the
two surfaces from drifting". A third implementation is precisely what the band was.

So the plaque includes that partial at its `compact` scale, and this service produces the data it reads:
`{slug, label, avg, total}` per discipline, passed through `job_render.discipline_ring` for the arc geometry
so the arithmetic has ONE home rather than one per host. The reuse check that missed it searched for
"Pursuer Card" -- led there by a doc and a memory that were both stale -- and never searched for an existing
discipline component.

FOUR QUERIES FOR THE WHOLE PAGE, whatever the entry count -- itemised below, because quoting a bare total
is how both sibling services got theirs wrong, in two different ways. `enrich`'s figure was UNDERCOUNTED
three times, each revision quietly dropping a read; `boards_for`'s cost is a RANGE and stating it as one
number was the error there. Neither was the per-run/per-page confusion an earlier version of this paragraph
claimed:

1. the career standings -- Pursuer Level and Career XP -- for every profile on the page. NOT the rank:
   that is not a stored column, it is derived from the level in Python by `pursuer_rank_for_level`;
2. the job catalogue's shape -- how many jobs each discipline holds, which the level floor needs;
3. one aggregate over `ProfileJobXP` for every profile on the page, grouped by discipline;
4. the count of redeemed slots per run, for the job XP the run itself paid.

SELF-CONTAINED RATHER THAN RIDING THE CALLER'S `select_related`. Rank, level and Career XP all live on
`ProfileCareerStanding`, a reverse OneToOne off `Profile`, so `select_related('profile__career_standing')`
in the view would make them free. That is NOT what this does, and the extra query is bought deliberately: a
caller who forgets the hint gets a silent per-entry query instead of a visible failure, which is the shape
of every N+1 this project has had to go back and fix. One query for the page cannot regress that way.

PER-USER AGGREGATION IS IN THE DATABASE, per the project rule. `ProfileJobXP` is bounded at ~25 rows per
profile, so iterating it would not be the whale hazard the rule was written for -- but the rule is about the
SHAPE, and a `.values().annotate()` here costs nothing over a loop and cannot rot into one.
"""
from django.db.models import Count, Sum

from challenges.models import CHALLENGE_TYPE_JOBS, ChallengeSlot
from trophies.models import Job, ProfileCareerStanding, ProfileJobXP
from trophies.services.contract_service import pursuer_level_from
from trophies.services import job_render
from trophies.services.job_render import DISCIPLINE_LABELS
from trophies.util_modules.constants import CHALLENGE_SLOT_JOB_XP
from trophies.util_modules.leveling import pursuer_rank_for_level

#: An untouched job sits at level 1, not 0. `contract_service.pursuer_level_from` carries the full argument
#: ("PURSUER_RANKS is explicitly calibrated against the floored scale"), and the drift it records -- the
#: Career XP board and the hunter's own Career page disagreeing about one hunter's level -- came from
#: summing the rows that exist instead of flooring across the catalogue. The band below has to floor the
#: same way or a hunter reads one average here and another on their Career page.
UNTOUCHED_JOB_LEVEL = 1

#: How hard the plaque's chrome leans on the hunter's rank colour. FOUR INTENSITIES, NOT ELEVEN: the exact
#: hue comes from `--rank-<key>` (the same token the Career page's rank ladder reads, so the two surfaces
#: agree), and this only says how much of it the material takes.
#:
#: CLOSE TO `pursuer-card.css`'s RUNGS BUT NOT THE SAME ONES, and an earlier version of this comment
#: claimed they matched. That file escalates at FOUR rungs (seeker, ranger, vanquisher, luminary), the
#: vanquisher step being a hue change from violet to cyan, and it assigns `--pc-tier` in only two rules --
#: so it carries TWO hues, not the three another comment claimed. This map has four bands and merges
#: ranger-through-paragon into one `tinted`, so the vanquisher step is absent: deliberately, because the hue
#: here already changes at every rung via `--rank-<key>`, which is what that two-hue split approximates.
#:
#: THE COST OF THAT CHOICE, STATED BOTH WAYS. Reading the ladder's own token keeps the plaque and the Career
#: page agreeing about what a Paragon looks like -- and it makes the plaque DISAGREE with the home Pursuer
#: Card, where the same hunter is cyan-glassed and here is coral. `visual-identity.md` names that as an
#: anti-pattern ("inconsistent treatments between hero/compact/mini that break the family read"), so one of
#: the two surfaces should eventually move; the divergence is recorded in
#: `docs/features/challenge-systems.md` rather than left to be discovered.
#:
#: WHY LAYERED RATHER THAN SWITCHED: the Frame's rule that "Bronze to Platinum should feel like the same
#: family, not different products". Every band keeps the material and the plinth. (It used to say "and the
#: four corner diamonds"; those were cut by the owner, and the resting rank glow with them -- so what is
#: layered now is the border tint, the wash and the notch-free material, nothing that hangs off an edge.)
#:
#: TWO HONEST LIMITS on "the floor is handsome", both glossed by an earlier version: the gold title band is
#: conditional on a title actually being held, and a finished run can legitimately hold none; and every band
#: treatment and the plate live inside `@media (min-width: 768px)`, so below the tablet breakpoint a
#: `matte` and a `radiant` plaque are identical.
RANK_BANDS = {
    'newbie': 'matte', 'recruit': 'matte',
    'seeker': 'lift', 'hunter': 'lift',
    'ranger': 'tinted', 'warden': 'tinted', 'marshal': 'tinted',
    'vanquisher': 'tinted', 'paragon': 'tinted',
    'luminary': 'radiant', 'ascendant': 'radiant',
}


def plaques_for(challenges):
    """`{run id: spine}` for every run handed in, where `spine` is what `_run_hero.html` draws.

    Runs whose hunter has no `ProfileCareerStanding` row still get an entry. That row is written only by
    `contract_service.recompute_career_standing`, which runs on a contract claim AND on a challenge redeem
    (`rewards` calls it once per payout, "so a 25-square payout costs what one costs") -- so a hunter who has
    claimed neither has no row at all. An earlier version of this said the row materialises "once a profile
    has been paid a contract", which missed the second door.

    Returning nothing for them would make the plaque's whole lower half vanish for exactly the hunters most
    likely to have finished an A-Z run off the back of the history importer, and it would do it silently,
    because Django templates swallow missing keys. The level such a hunter gets is the CATALOGUE FLOOR, not
    zero -- see `floor_level` below, which is the defect two audits independently put first.
    """
    challenges = list(challenges)
    run_ids = [c.pk for c in challenges]
    if not run_ids:
        return {}

    profile_ids = {c.profile_id for c in challenges}

    # ── 1. Pursuer Level and Career XP, per profile (the RANK is derived from the level in Python
    #       by `pursuer_rank_for_level` -- it is not a stored column, as the docstring's item 1 says)
    standings = {
        row['profile_id']: row
        for row in ProfileCareerStanding.objects
        .filter(profile_id__in=profile_ids)
        .values('profile_id', 'pursuer_level', 'total_xp')
    }

    # ── 2. the catalogue's shape: how many jobs each discipline holds.
    #
    # READ, NOT ASSUMED. `DISCIPLINE_LABELS` fixes the five disciplines, but how many jobs sit in each is
    # data -- staff add jobs -- and it is the denominator of both the floor and the average. Hard-coding
    # "five per discipline" would be right today and silently wrong after one catalogue edit, reporting a
    # level average that no longer matches the Career page's.
    catalogue = {
        row['discipline']: row['n']
        for row in Job.objects.values('discipline').annotate(n=Count('slug'))
    }

    # ── 3. one aggregate over every page profile's job XP, grouped by discipline
    rows = (
        ProfileJobXP.objects
        .filter(profile_id__in=profile_ids)
        .values('profile_id', 'job__discipline')
        .annotate(level_sum=Sum('level'), row_count=Count('job_id'))
    )
    by_profile = {}
    for row in rows:
        by_profile.setdefault(row['profile_id'], {})[row['job__discipline']] = row

    # ── 4. the job XP this run itself paid.
    #
    # DERIVED FROM THE REDEEM GUARD rather than from the XP ledger. `ContractXPGrant.source_id` is a bare
    # integer with no FK to `ChallengeSlot` (the column is a convention, as `rewards.granted_titles_for`
    # spells out for the title case), so the ledger cannot be joined or grouped per run in the ORM without
    # a second round trip to map slot ids back to runs. `xp_redeemed_at` is the authoritative guard, and it
    # has TWO writers: `rewards.redeem_slot` and `rewards.redeem_all` (via `bulk_update`). On a FINISHED run
    # the Claim-all path is the likelier of the two, so naming only the single-slot door -- as an earlier
    # version did -- understates the writer set a reader would need to check.
    #
    # Every paid slot pays the same flat amount. `rewards` puts it as "a square filled by the history
    # importer or by the scarcity hatch pays exactly what a live one pays", with no branch on
    # `completed_via`. So a count times the constant is exact rather than an estimate. (An earlier version
    # of this comment quoted the words "pays full 6,000"; that phrasing was invented, not quoted.)
    #
    # A-Z RUNS PAY NOTHING HERE and that is correct, not a gap: `rewards.redeemable_slots` gates on
    # `challenge_type == CHALLENGE_TYPE_JOBS`. The 6,000 belongs to the Job Coverage side even when one
    # platinum advanced both boards. So this is empty for A-Z and the plaque omits the line.
    paid = {
        row['challenge_id']: row['n']
        for row in ChallengeSlot.objects
        .filter(challenge_id__in=run_ids, xp_redeemed_at__isnull=False)
        .values('challenge_id')
        .annotate(n=Count('pk'))
    }

    # THE FLOOR FOR A HUNTER WITH NO STANDING ROW, and getting this wrong was the worst defect in the first
    # cut of this file. `or 0` looks like the obvious default and is the one number it cannot be: a hunter
    # has a level in every job from the moment they exist, so `pursuer_level_from(None, None, 25)` is **25**,
    # and their own Career page says Lv 25 while the plaque said Lv 0.
    #
    # IT IS EXACTLY THE DRIFT `UNTOUCHED_JOB_LEVEL`'s comment above exists to prevent -- "summing the rows
    # that exist instead of flooring across the catalogue" -- which `_families` was careful about and this
    # line was not. Worse, the plaque contradicted ITSELF on one row: the band reported all five families
    # averaging level 1.0 across a 25-job catalogue (i.e. 25) directly beside a headline reading Lv 0.
    #
    # COSTS NOTHING: the catalogue is already in hand from query 2, and `pursuer_level_from` is the shared
    # helper both other surfaces go through, so this is the same expression rather than a parallel one.
    # SUMMED OVER THE FIVE REAL DISCIPLINES, which matters because `Job.discipline` has `choices` but no DB
    # constraint: a badly-seeded row outside the five would otherwise put a job in the Pursuer Level that
    # `build_profile_jobs` cannot display and the ring below does not include.
    #
    # THE SAME SCOPE `contract_service.catalogue_job_count()` USES, and an earlier version of this comment
    # claimed the opposite -- that the helper "counts the whole table", which it does not: it filters
    # `discipline__in=Job.DISCIPLINES` and its own docstring says "Scoped to the five real disciplines".
    # The inversion was self-falsifying inside this branch, since
    # `test_a_hunter_with_no_career_standing_still_gets_a_plaque` computes its expectation THROUGH that
    # helper and passes -- which only holds because the two denominators agree.
    #
    # Re-derived from `catalogue` rather than calling it, for the one reason that does hold: the rows are
    # already in hand, so calling it would be a fifth query for a number this dict already contains.
    floor_level = pursuer_level_from(None, None, sum(catalogue.get(slug, 0) for slug in DISCIPLINE_LABELS))

    out = {}
    for challenge in challenges:
        standing = standings.get(challenge.profile_id) or {}
        # `.get(...) is None` RATHER THAN `or`: a stored level of 0 is not the same as no row, and `or` would
        # silently promote a real zero to the floor. A stored 0 should not happen (every writer goes through
        # `pursuer_level_from`), which is precisely why it must not be papered over if it ever does.
        stored = standing.get('pursuer_level')
        level = floor_level if stored is None else stored
        rank = pursuer_rank_for_level(level)
        out[challenge.pk] = {
            'rank': rank,
            # `.get` WITH A DEFAULT rather than a subscript. `PURSUER_RANKS` is the only producer of these
            # keys today, so every one of them is in `RANK_BANDS` -- but a rung added to that ladder without
            # a line here would be a KeyError on a public page, and the honest fallback for an unknown rung
            # is the resting treatment rather than a 500.
            'band': RANK_BANDS.get(rank.get('key'), 'matte'),
            'level': level,
            'career_xp': standing.get('total_xp') or 0,
            'ring': _ring(by_profile.get(challenge.profile_id) or {}, catalogue),
            # Zero for A-Z, and for a Job Coverage run whose owner has not pressed claim yet. The template
            # omits the line on a zero rather than printing "+0 job XP", which would read as a failure.
            'run_xp': paid.get(challenge.pk, 0) * CHALLENGE_SLOT_JOB_XP
            if challenge.challenge_type == CHALLENGE_TYPE_JOBS else 0,
        }
    return out


def _ring(disc_rows, catalogue):
    """The five discipline arcs for the shared ring: `[{slug, label, avg, total, share_pct, dash, offset}]`.

    FED TO `partials/components/_disciplines_ring.html`, the live component the Career hero and the home
    lobby both render -- not to a band of this surface's own. The arc geometry is added by
    `job_render.discipline_ring`, which is where it lives for every producer.

    NO `bar_pct`, AND DELIBERATELY NOT `pursuer_card_service`'s FORMULA. An earlier version of this docstring
    claimed "the same `bar_pct` formula ... which is the point", which was left over from the five-tile band
    this replaced and is the opposite of what the function does. That formula scales each family against the
    hunter's STRONGEST family; a hunter at the level-1 floor in all five would get `bar_pct: 100` five times
    from it, and gets `share_pct: 20` five times from here. Avoiding that is the point -- see the closing
    comment below, which the old sentence contradicted.

    ORDERED BY `DISCIPLINE_LABELS`, which `job_render` treats as the canonical sequence. Iterating the
    aggregate's own key order would arrange the band by whatever Postgres returned, so one hunter's Combat
    tile would sit where another's Heart tile does -- and a band whose positions move between rows cannot be
    compared at a glance, which is the only thing a band is for.
    """
    averages = {}
    totals = {}
    for slug in DISCIPLINE_LABELS:
        n_jobs = catalogue.get(slug, 0)
        if not n_jobs:
            # A discipline with no jobs in the catalogue: reachable only by staff deleting a discipline's
            # last job, but a zero denominator is a ZeroDivisionError on a public page, so it is handled
            # rather than trusted not to happen.
            #
            # ZERO, NOT THE FLOOR. The first cut used `UNTOUCHED_JOB_LEVEL` here, which is the one value that
            # disagrees with the surface this branch exists to agree with: `job_render` computes
            # `avg = round(...) if tiles else 0`, so an empty discipline reads 0 on the Career page. A floor
            # of 1 for a discipline containing no jobs also claims a level in jobs that do not exist.
            averages[slug] = 0.0
            totals[slug] = 0
            continue
        row = disc_rows.get(slug) or {}
        level_sum = row.get('level_sum') or 0
        row_count = row.get('row_count') or 0
        # The floor, applied exactly as `pursuer_level_from` does: every job the hunter has no row for still
        # counts at level 1. `max(..., 0)` for the same reason it carries one -- a catalogue edit in flight
        # must not produce a negative term.
        floored = level_sum + max(n_jobs - row_count, 0) * UNTOUCHED_JOB_LEVEL
        averages[slug] = round(floored / n_jobs, 1)
        totals[slug] = floored

    # THE FIVE-FULL-BARS PROBLEM DOES NOT ARISE HERE, and it is worth saying why rather than leaving the
    # guard's absence to look like an omission. The bespoke band scaled each family against the hunter's
    # STRONGEST family, which is undefined when every family sits at the level floor and resolves to
    # 1.0 / 1.0 = 100% -- five full bars for a hunter who has never been paid a contract, reading as mastery
    # of everything on the one page built to display mastery. (Reachable: an A-Z run completed through the
    # history importer pays no job XP.)
    #
    # The ring scales each arc against the SUM instead, so five equal floors are five equal arcs -- which is
    # the truth, and what the Career hero already shows those hunters. `discipline_ring` handles the
    # all-zero case explicitly as an even split rather than as five zero-length arcs.
    #
    # THE ABANDONED PURSUER CARD STILL CARRIES THE BUG, dormant. `pursuer_card_service` computes its
    # `bar_pct` unconditionally and `_pursuer_card.html` gates only on a truthy five-element list, so a
    # hunter with no `ProfileJobXP` rows gets five full bars from it. It is not user-facing today, because
    # that component is mounted on nothing but `/design/pursuer-card-ranks/` -- noted so whoever re-mounts
    # it fixes it on the way in. (An earlier comment here claimed that service already guarded this, citing
    # a gate that lives in `career_service` and governs a different element. Two audits found that false.)
    return job_render.discipline_ring([
        {
            'slug': slug,
            'label': label,
            'avg': averages[slug],
            # `total` IS WHAT THE ARCS ARE PROPORTIONED BY -- this discipline's floored share of the Pursuer
            # Level, the same figure `career_service` hands the helper. The ring reads `slug`, `dash` and
            # `offset` for the arcs and `total`, `short` and `label` for the hover peek; `avg` is the one key
            # no host renders, kept only because `career_service` supplies it and the two producers' key
            # sets are deliberately identical. (An earlier comment said the ring reads only the first
            # three, which the peek falsified.)
            'total': totals[slug],
        }
        for slug, label in DISCIPLINE_LABELS.items()
    ])
