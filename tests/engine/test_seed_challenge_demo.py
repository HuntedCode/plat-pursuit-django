"""`seed_challenge_demo`: the command that makes the reward surfaces LOOKABLE-AT.

WHY A DEV SEEDER GETS TESTS AT ALL. It writes into the append-only job-XP ledger, and its `--reset` deletes
rows from that ledger — so the two things worth pinning are that it cannot destroy real XP and that reset
actually reverses what it did. A seeder that leaves orphaned grants behind inflates a hunter's levels
permanently and there is nothing in the UI that would ever show it.

It also has to produce the states it advertises. A seeder whose "finished run" is not finished wastes the
browser pass it exists to serve, and nothing else in the suite would notice.
"""
import string

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from challenges.management.commands.seed_challenge_demo import (
    CALENDAR_PARTIAL_CLEAN,
    CALENDAR_PARTIAL_DAYS,
    CALENDAR_PARTIAL_MONTH,
    CALENDAR_SHOVELWARE_ONLY,
    CALENDAR_STRUCK,
    DEMO_TAG,
    MIXED_CLAIMED,
    MIXED_FILLED,
)
from challenges.management.commands.seed_challenge_demo import Command
from challenges.models import (
    CALENDAR_MONTH_DAYS,
    CALENDAR_VIEW_ALL,
    CALENDAR_VIEW_CLEAN,
    CHALLENGE_TYPE_AZ,
    CHALLENGE_TYPE_CALENDAR,
    CHALLENGE_TYPE_JOBS,
    CalendarDay,
    Challenge,
)
from challenges.services import calendar_render
from challenges.services import rewards
from tests.factories import ConceptFactory, GameFactory, IGDBMatchFactory, ProfileFactory, UserFactory
from trophies.models import Contract, ContractXPGrant, Job, ProfileJobXP
from trophies.util_modules.constants import CHALLENGE_SLOT_JOB_XP

pytestmark = pytest.mark.django_db

_SEQ = {'n': 0}


@pytest.fixture
def catalogue():
    """A live contract per job, plus one per LETTER, so the seeder has a pool it can finish both types from.

    The dev database has a real catalogue; the test one does not, and the command's whole job is to pick
    REAL contracts out of whatever is there. So the fixture is the catalogue.

    THE WHOLE ALPHABET, not the A-J it started with. The seeder now builds a FINISHED A-Z run so the Hall of
    Fame can be looked at with a hero of each type, and a ten-letter pool cannot complete one -- so that
    scenario would have had no coverage while every assertion around it stayed green. The thin-catalogue case
    keeps its own test (`test_it_survives_a_catalogue_too_thin_to_fill_every_square`), which deliberately
    does NOT take this fixture.
    """
    made = []
    for job in Job.objects.order_by('slug'):
        made.append(_contract('Alpha %s' % job.name, jobs=[job]))
    for letter in string.ascii_uppercase:
        made.append(_contract('%s Game For Letters' % letter))
    return made


def _contract(name, *, jobs=()):
    _SEQ['n'] += 1
    c = Contract.objects.create(name=name, slug='%s-%d' % (name.lower().replace(' ', '-'), _SEQ['n']),
                                is_live=True, igdb_id=930_000 + _SEQ['n'])
    if jobs:
        c.jobs.set(jobs)
    concept = ConceptFactory(anchor_migration_completed_at=timezone.now())
    IGDBMatchFactory(concept=concept, igdb_id=c.igdb_id, status='accepted')
    GameFactory(concept=concept)
    return c


def _hunter():
    return ProfileFactory(user=UserFactory(), user_is_premium=True, is_linked=True)


def _seed(profile, **kwargs):
    call_command('seed_challenge_demo', user=profile.psn_username, verbosity=0, **kwargs)


def _demo_runs(profile):
    return Challenge.objects.filter(profile=profile, name__contains=DEMO_TAG)


# ── it produces the states it advertises ─────────────────────────────────────────────────────────

def test_the_seeded_board_is_not_empty_on_arrival():
    """A SEEDED BOARD EXISTS TO BE LOOKED AT, and for one slice it opened onto nothing.

    Every other seeded month is fixed -- February, May and September struck, July part-filled -- which
    was fine while the board always opened on January. It opens on the CURRENT month now, so a reviewer
    landed on an empty panel with the struck months and every stacked day a click away and no sign they
    existed.

    THE STACK IS PART OF THE POINT: the count badge only renders on a day holding two or more, so a
    month seeded entirely with ones would demonstrate the feature by not showing it.

    ALL TWELVE MONTHS, NOT TODAY'S. This read `timezone.localtime().month`, so it asserted about whichever
    month the suite happened to run in -- and it was WRONG for one of them: September is struck, a struck
    month takes its per-day counts from `CALENDAR_BUSY`, and September had no entry there, so this test
    failed every day of September and passed on either side. A test that holds for eleven twelfths of the
    year is not a weaker test than one that holds always, it is a worse one: it banks a failure for a
    month nobody is looking.
    """
    for month in range(1, 13):
        # Re-seeded per month, because the opening-month pattern is applied to whichever month is passed.
        clean = Command._calendar_days(False, month)[CALENDAR_VIEW_CLEAN]
        filled = [d for (m, d) in clean if m == month]
        assert filled, 'month %d opens empty, so a reviewer landing in it sees nothing' % month

        stacked = [d for (m, d) in clean if m == month and clean[(m, d)].plats > 1]
        assert stacked, 'no day in month %d stacks, so no count badge is visible on arrival' % month


def test_a_seeded_stack_never_hides_on_a_square_that_does_not_draw():
    """THE BADGE READS `filled`, NOT `counts`, so a stack seeded onto a shovelware-only square would be
    invisible -- and a reviewer checking the feature would reasonably conclude it was broken. The one
    deliberately shovelware square carries six platinums precisely to prove that exclusion, and it must
    stay the only stacked square the board does not draw."""
    days = Command._calendar_days(False, 1)
    clean, every = days[CALENDAR_VIEW_CLEAN], days[CALENDAR_VIEW_ALL]

    off_board = [key for key, fill in every.items() if key not in clean and fill.plats > 1]
    assert off_board == [CALENDAR_SHOVELWARE_ONLY], (
        'a stacked square the board does not draw: %s' % off_board)


@override_settings(DEBUG=True)
def test_it_seeds_a_finished_jobs_run_with_nothing_claimed(catalogue):
    """THE SCENARIO THE BROWSER PASS NEEDS MOST: the ledger at its longest, Claim-all live, and the
    layout-jump question. A seeder whose "finished" run is not finished wastes the pass."""
    profile = _hunter()

    _seed(profile)

    finished = _demo_runs(profile).filter(challenge_type=CHALLENGE_TYPE_JOBS, is_complete=True).first()
    assert finished is not None
    assert finished.completed_count == finished.total_slots
    summary = rewards.summary(finished)
    assert summary['claimable_count'] == finished.total_slots, 'every square must still be claimable'
    assert summary['paid_xp'] == 0


@override_settings(DEBUG=True)
def test_it_seeds_a_mixed_ledger_with_both_row_states(catalogue):
    """Paid and claimable rows on one panel, which is the only way to see whether the two read as two
    states rather than two elevations."""
    profile = _hunter()

    _seed(profile)

    mixed = _demo_runs(profile).filter(challenge_type=CHALLENGE_TYPE_JOBS, is_complete=False).first()
    assert mixed is not None
    rows = rewards.summary(mixed)['rows']
    assert len(rows) == MIXED_FILLED
    assert [r['is_paid'] for r in rows].count(True) == MIXED_CLAIMED
    assert [r['claimable'] for r in rows].count(True) == MIXED_FILLED - MIXED_CLAIMED


@override_settings(DEBUG=True)
def test_it_seeds_an_a_z_run_that_draws_no_panel(catalogue):
    profile = _hunter()

    _seed(profile)

    # THE IN-PROGRESS ONE, named explicitly. There are two A-Z runs now (one finished, one in flight) and
    # `.first()` picked whichever the default ordering happened to put first -- so this test would have
    # silently changed subject. `is_complete=False` is the one under test here: an unfinished A-Z run is the
    # case that must draw no panel while still having a title to play for.
    az = _demo_runs(profile).filter(challenge_type=CHALLENGE_TYPE_AZ, is_complete=False).get()
    assert az.filled_count > 0, 'an empty board shows nothing worth looking at'
    assert rewards.summary(az)['per_square'] == 0, 'A-Z pays no job XP, so it has no panel'

    # `A-Z Legend`, NOT `A-Z Champion`, and the change is the feature rather than a regression. Since the
    # seeder now finishes an A-Z run first, this one is the profile's SECOND -- so the title it is playing
    # for is the second completion's. `rewards.summary` computes that from `completed_run_count + 1` for an
    # unfinished run, which is exactly what a hunter looking at a run in progress is asking. A pleasant
    # side-effect for the seeder: both A-Z titles are now reachable on one demo profile.
    assert rewards.summary(az)['title_name'] == 'A-Z Legend'


@override_settings(DEBUG=True)
def test_it_seeds_a_finished_a_z_run_for_the_hall_of_fame(catalogue):
    """THE REASON THIS SCENARIO EXISTS. Only a finished run reaches the Hall of Fame, and until this was
    added the seeder produced exactly one finished run and it was Job Coverage -- so the A-Z hero, its
    26-square board and its title chip could not be seen at all without finishing 26 contracts by hand.

    Completing it must also grant the real title through the real service, because that `UserTitle` row is
    what the hero's prestige chip reads (`rewards.granted_titles_for`); a run finished without one renders
    with no chip, which is correct behaviour and would make the seeded page misleading.
    """
    from trophies.models import UserTitle

    profile = _hunter()

    _seed(profile)

    az_runs = _demo_runs(profile).filter(challenge_type=CHALLENGE_TYPE_AZ)
    assert az_runs.count() == 2, 'one finished and one in flight'

    finished = az_runs.get(is_complete=True)
    assert finished.completed_count == finished.total_slots, 'every letter must be completed'
    assert not finished.is_deleted, 'a hidden run is off the Hall of Fame'

    assert UserTitle.objects.filter(profile=profile, source_type='challenge',
                                    source_id=finished.pk).exists(), \
        'the finished run granted no title, so the hero would render with no chip'


@override_settings(DEBUG=True)
def test_the_seeded_finished_runs_are_the_ones_the_hall_of_fame_lists(client, catalogue):
    """END TO END, because every assertion above is about rows and the point is a PAGE. One finished run
    of EACH type, all listed; no in-progress run listed."""
    profile = _hunter()

    _seed(profile)

    body = client.get(reverse('challenges_hall_of_fame')).content.decode()

    for run in _demo_runs(profile).filter(is_complete=True):
        assert reverse('challenge_detail', args=[run.pk]) in body, \
            '%s is finished but not on the Hall of Fame' % run.name
    for run in _demo_runs(profile).filter(is_complete=False):
        assert reverse('challenge_detail', args=[run.pk]) not in body, \
            '%s is in flight and must not be on the Hall of Fame' % run.name

    # THE CHAIN PINS TWO DIFFERENT THINGS, which is why both halves are here: that the page draws one
    # hero per finished ROW (the agreement), and that there are two of them (the absolute count): A-Z and
    # Job Coverage. The Calendar is seeded in progress only, since `TYPES_WITH_ONE_RUN` makes a finished
    # plus an in-progress Calendar impossible for one hunter. The literal is deliberate: a new type that
    # does not reach the Hall of Fame is worth being told about.
    assert body.count('<a class="pp-chero') == _demo_runs(profile).filter(is_complete=True).count() == 2


# `test_it_says_so_when_the_catalogue_cannot_finish_the_a_z_run` was here and is DELETED, not moved. It had
# the same fixture and a strict SUBSET of the assertions of `test_the_shortfall_warning_names_the_remedy`
# below -- no mutation could break one without breaking the other, so it added a name to the suite and
# nothing else. Recorded rather than silently dropped, so the next reader does not re-add it.

@override_settings(DEBUG=True)
def test_fill_letter_gaps_makes_the_a_z_run_finishable_on_a_thin_catalogue(client, capsys):
    """THE CASE THE OWNER ACTUALLY HIT. No `catalogue` fixture, so the pool cannot cover the alphabet --
    which is the shape of a real dev database, since prod's thinnest letters (Q, X, Z) sit at six to ten
    contracts and a dev subset easily has none. Without the flag the "finished" A-Z run is not finished, so
    it never reaches the Hall of Fame and there is nothing to look at.
    """
    from challenges.management.commands.seed_challenge_demo import GAP_SLUG_PREFIX

    profile = _hunter()

    _seed(profile, fill_letter_gaps=True)

    finished = _demo_runs(profile).filter(challenge_type=CHALLENGE_TYPE_AZ, is_complete=True)
    assert finished.count() == 1, 'the flag did not get the A-Z run finished'
    run = finished.get()
    assert run.completed_count == run.total_slots

    assert Contract.objects.filter(slug__startswith=GAP_SLUG_PREFIX).exists(), \
        'nothing was stood up, so this test is not exercising the flag'
    assert 'stood up a placeholder contract' in capsys.readouterr().out

    # THE ACTUAL DELIVERABLE, asserted on the PAGE rather than on the row. Every assertion above is about
    # a Challenge's columns, and the thing that was missing was a hero on a screen -- a run one square
    # short of complete satisfies none of this and also looks fine in a `25/26`.
    body = client.get(reverse('challenges_hall_of_fame')).content.decode()
    assert reverse('challenge_detail', args=[run.pk]) in body, 'finished, but still not on the page'
    # The stood-up squares have no resolvable cover, which is the honest `--bare` path rather than a
    # broken image: the board shows holes where the catalogue had none.
    assert 'pp-chero__sq--bare' in body


@override_settings(DEBUG=True)
def test_a_stand_in_never_reaches_a_public_surface():
    """THE WORST THING THIS COMMAND COULD HAVE DONE, and it nearly did.

    A stand-in must be `is_live=True` -- `eligibility._slot_pool` filters on it, so one that is not live
    cannot fill the square it exists for. But `is_live=True` plus a non-null `went_live_at` plus a null
    `announced_at` is EXACTLY `contract_announcer.pending_contracts()`' predicate, so the scheduled
    `announce_contracts` would have posted "Q (placeholder for the demo A-Z run)" to the community Discord
    as a newly published Job Board contract. The same two flags also put it inside
    `contracts_service.new_contract_cutoff()`, so it wore the "New" chip on the public board.
    """
    from core.services import contract_announcer
    from trophies.services import contracts_service

    from challenges.management.commands.seed_challenge_demo import GAP_SLUG_PREFIX

    _seed(_hunter(), fill_letter_gaps=True)
    stand_ins = Contract.objects.filter(slug__startswith=GAP_SLUG_PREFIX)
    assert stand_ins.exists(), 'nothing was stood up, so this test proves nothing'

    queued = set(contract_announcer.pending_contracts().values_list('pk', flat=True))
    assert not (queued & set(stand_ins.values_list('pk', flat=True))), \
        'a placeholder contract is queued for the community announcement'

    # AND NOT "NEW" on the public board either.
    assert not stand_ins.filter(went_live_at__gte=contracts_service.new_contract_cutoff()).exists(), \
        'a placeholder is inside the Latest window and will wear the New chip'


@override_settings(DEBUG=True)
def test_a_stand_in_a_hunter_earned_is_never_deleted():
    """`EarnedContract.contract` is CASCADE and `ContractXPGrant.earned_contract` is CASCADE behind it, so
    deleting a stand-in transitively deletes rows from the ledger whose own docstring calls it "Immutable
    job-XP ledger ... NEVER recomputed" -- and which this feature's XP guard calls append-only, offsettable
    only by a negating row. Reachable because a stand-in must be live to fill its square, so a hunter can
    platinum it like any other contract.

    `--reset`'s headline promise is that it touches no `EarnedContract`. A stray placeholder in a dev
    catalogue is cosmetic; a hole in an append-only ledger is not.
    """
    from trophies.models import EarnedContract

    from challenges.management.commands.seed_challenge_demo import GAP_SLUG_PREFIX

    profile = _hunter()
    _seed(profile, fill_letter_gaps=True)
    stand_in = Contract.objects.filter(slug__startswith=GAP_SLUG_PREFIX).first()
    assert stand_in is not None

    earned = EarnedContract.objects.create(
        profile=profile, contract=stand_in, has_platinum=True, platinum_reached_at=timezone.now())

    call_command('seed_challenge_demo', user=profile.psn_username, reset=True, verbosity=0)

    assert Contract.objects.filter(pk=stand_in.pk).exists(), \
        'an earned placeholder was deleted, cascading into the append-only ledger'
    assert EarnedContract.objects.filter(pk=earned.pk).exists(), 'the EarnedContract row was cascaded away'


@override_settings(DEBUG=True)
def test_the_stand_ins_carry_no_igdb_id_rather_than_an_invented_one():
    """`igdb_id` is `null=True, blank=True, unique=True`, and null is what the field's own help text calls
    the admin/episodic case -- a contract representing no IGDB game. A first draft put a negative number
    there, on the stated grounds that the column was non-nullable, which was simply false.

    It also matters functionally: a null `igdb_id` makes `member_concepts_by_contract` skip its lookup, so
    the square reaches the no-art path by the same route a real episodic contract would, rather than by a
    dangling reference to an id nothing owns.
    """
    from challenges.management.commands.seed_challenge_demo import GAP_SLUG_PREFIX

    _seed(_hunter(), fill_letter_gaps=True)

    stand_ins = Contract.objects.filter(slug__startswith=GAP_SLUG_PREFIX)
    assert stand_ins.exists()
    assert not stand_ins.exclude(igdb_id=None).exists(), 'a stand-in invented an igdb_id'
    assert not stand_ins.exclude(is_live=True).exists(), 'a stand-in that is not live fills no square'


@override_settings(DEBUG=True)
def test_reset_removes_the_titles_its_runs_granted():
    """THE OTHER HALF OF THE OWNER'S MISSING TITLE BAND, and pinning it took two attempts.

    A `UserTitle` is tied to its run by `source_id` alone -- no FK, no cascade -- so deleting the runs left
    the titles behind pointing at dead pks. `granted_titles_for` matches on that column, so the next seeded
    run drew no title band: the hunter held it, the page did not show it.

    NO `catalogue` FIXTURE, WHICH IS THE WHOLE POINT. `--reset` always reseeds in the same call, and
    `grant_completion_title` now REPAIRS an orphan it meets -- so with a catalogue the new run completes,
    the orphan is re-pointed, and the end state is correct whether or not the seeder cleaned up. The two
    fixes mask each other, which a mutation run proved by deleting this cleanup with the test still green.
    Without a catalogue nothing completes, no repair fires, and the cleanup is the only thing that can
    remove the row.

    That window is narrow but real: a profile wiped on a thin catalogue keeps titles for runs that no longer
    exist, and `UserTitle` is what the display-title picker reads -- so the hunter would be offered a title
    with nothing behind it.
    """
    from trophies.models import UserTitle

    profile = _hunter()

    # Seeded WITH a catalogue first, so there is something to orphan.
    for job in Job.objects.order_by('slug'):
        _contract('Alpha %s' % job.name, jobs=[job])
    _seed(profile)
    assert UserTitle.objects.filter(profile=profile, source_type='challenge').exists(), \
        'the seeded runs granted no title, so this test proves nothing'

    # Now take the catalogue away, so the reseed inside `--reset` completes nothing and cannot repair.
    Contract.objects.all().delete()
    call_command('seed_challenge_demo', user=profile.psn_username, reset=True, verbosity=0)

    # NO ORPHANS, which is the invariant -- not "no titles". Emptying the catalogue stops A-Z and Job
    # Coverage completing, so neither re-earns anything; the CALENDAR fills from a hunter's platinums and
    # its own designed day map, so the reseeded run legitimately climbs the ladder again and holds five
    # titles pointing at itself. A blanket "no challenge titles" assertion read that as a leak.
    live = set(Challenge.objects.filter(profile=profile).values_list('pk', flat=True))
    orphans = [(name, sid) for name, sid in
               UserTitle.objects.filter(profile=profile, source_type='challenge')
               .values_list('title__name', 'source_id')
               if sid not in live]
    assert orphans == [], 'titles survived --reset with nothing to re-point them: %s' % orphans


@override_settings(DEBUG=True)
def test_removing_titles_leaves_another_systems_alone():
    """SCOPED BY `source_type`, and without that term this deletes badge and milestone titles that happen to
    share a run's id. `UserTitle.source_id` is a bare integer with no FK, so a collision is a normal state
    rather than a corruption."""
    from trophies.models import Title, UserTitle

    profile = _hunter()
    for job in Job.objects.order_by('slug'):
        _contract('Alpha %s' % job.name, jobs=[job])
    _seed(profile)

    run_id = _demo_runs(profile).filter(is_complete=True).values_list('pk', flat=True).first()
    assert run_id is not None, 'no finished run to collide with'

    # A foreign title whose `source_id` is exactly a seeded run's id.
    foreign_title, _ = Title.objects.get_or_create(name='Some Badge Title')
    foreign = UserTitle.objects.create(profile=profile, title=foreign_title,
                                       source_type='badge_series', source_id=run_id)

    Contract.objects.all().delete()
    call_command('seed_challenge_demo', user=profile.psn_username, wipe=True, verbosity=0)

    assert UserTitle.objects.filter(pk=foreign.pk).exists(), \
        "another system's title was deleted because it shared a run id"
    # NO ORPHANS, for the reason the reset test above gives: a reseeded Calendar run re-earns its ladder
    # from platinums rather than from the catalogue this test empties.
    live = set(Challenge.objects.filter(profile=profile).values_list('pk', flat=True))
    orphans = [(name, sid) for name, sid in
               UserTitle.objects.filter(profile=profile, source_type='challenge')
               .values_list('title__name', 'source_id')
               if sid not in live]
    assert orphans == [], 'the challenge titles were not removed: %s' % orphans


@override_settings(DEBUG=True)
def test_a_reseed_still_shows_the_title_band(client, catalogue):
    """END TO END, because the two above are about rows and the symptom was a PAGE: a seeded Job Coverage
    hero with no title band after a reseed, while the newer A-Z one showed fine.

    This one DOES take the catalogue, because what it checks is the outcome the owner reported -- and that
    outcome is correct via either half of the fix, which is exactly why it cannot stand alone.
    """
    profile = _hunter()
    _seed(profile)
    call_command('seed_challenge_demo', user=profile.psn_username, reset=True, verbosity=0)

    body = client.get(reverse('challenges_hall_of_fame')).content.decode()

    # DERIVED, not a literal: one finished run per type, and the Plat Calendar made that three. Reading
    # the row count means a fourth type moves this with the feature.
    assert body.count('<a class="pp-chero') == _demo_runs(profile).filter(is_complete=True).count()
    # TWO BANDS: the finished A-Z run and the finished Job Coverage run (the Calendar is seeded in progress).
    assert body.count('pp-chero__title') == 2, 'a reseeded finished run is missing its title band'
    assert 'Job Challenge Champion' in body
    assert 'A-Z Champion' in body


@override_settings(DEBUG=True)
def test_reset_removes_the_letter_stand_ins():
    """They are catalogue rows in a real table, so leaving them behind means `--reset` no longer returns the
    database to where it was -- which is the one promise that makes the command safe to re-run."""
    from challenges.management.commands.seed_challenge_demo import GAP_SLUG_PREFIX

    profile = _hunter()
    _seed(profile, fill_letter_gaps=True)
    assert Contract.objects.filter(slug__startswith=GAP_SLUG_PREFIX).exists()

    call_command('seed_challenge_demo', user=profile.psn_username, reset=True)

    # SEEDING RUNS AFTER THE RESET in the same invocation, and WITHOUT the flag this time, so nothing
    # stands them back up. A leftover row here means the removal did not happen.
    assert not Contract.objects.filter(slug__startswith=GAP_SLUG_PREFIX).exists(), \
        'the placeholders survived --reset'


@override_settings(DEBUG=True)
def test_the_default_still_creates_no_contracts():
    """The documented default is unchanged: this command makes no demo Contracts, because a demo Concept
    renders the no-art placeholder and most of the questions worth asking in a browser cannot be answered
    against a grey box. The flag is the opt-in exception for the one case where refusing costs more."""
    from challenges.management.commands.seed_challenge_demo import GAP_SLUG_PREFIX

    before = Contract.objects.count()

    _seed(_hunter())

    assert Contract.objects.count() == before, 'the default path invented a contract'
    assert not Contract.objects.filter(slug__startswith=GAP_SLUG_PREFIX).exists()


@override_settings(DEBUG=True)
def test_the_shortfall_warning_names_the_remedy(capsys):
    """A silently-unfinished "finished" run just does not appear on the Hall of Fame, and the owner hit
    exactly that: `--wipe` and `--reset` both ran clean and produced no A-Z hero. The message has to name
    the missing letters AND the flag that fixes it."""
    _seed(_hunter())

    out = capsys.readouterr().out

    assert 'could not be finished' in out
    assert 'OFF the Hall of Fame' in out
    assert '--fill-letter-gaps' in out, 'the warning must say how to get past it'


@override_settings(DEBUG=True)
def test_the_finished_run_is_created_before_the_second_one(catalogue):
    """ORDER IS LOAD-BEARING. Only one run per type can be active, so the finished run has to be built
    first -- otherwise `start` hands the first run back instead of creating a second, and the seeder
    silently produces one jobs run instead of two."""
    profile = _hunter()

    _seed(profile)

    jobs_runs = _demo_runs(profile).filter(challenge_type=CHALLENGE_TYPE_JOBS)
    assert jobs_runs.count() == 2
    assert jobs_runs.filter(is_complete=True).count() == 1
    assert jobs_runs.filter(is_complete=False).count() == 1


# ── what it must not touch ───────────────────────────────────────────────────────────────────────

@override_settings(DEBUG=True)
def test_it_invents_no_completion_history(catalogue):
    """NO `EarnedContract` ROWS. A square is completed by `mark_slot_completed` directly, so the command
    never fabricates trophies or contract credit for a real account -- which is what stops a demo run
    leaking into Career, the boards, or a real claim."""
    from trophies.models import EarnedContract

    profile = _hunter()

    _seed(profile)

    assert not EarnedContract.objects.filter(profile=profile).exists()


@override_settings(DEBUG=True)
def test_reset_removes_its_grants_and_leaves_real_xp_alone(catalogue):
    """THE ONE THAT MATTERS. The ledger is append-only and `recompute_profile_job_xp` sums every row, so a
    reset that deleted the slots but not the grants would leave rows nothing can identify -- inflating the
    hunter's levels permanently, with no surface that would ever show it."""
    profile = _hunter()
    real_job = Job.objects.order_by('slug').first()
    # A REAL grant, of the kind a contract claim writes. It must survive the reset untouched.
    ContractXPGrant.objects.create(profile=profile, job=real_job, amount=1234, source='contract')

    _seed(profile)
    assert ContractXPGrant.objects.filter(profile=profile, source='challenge').count() == MIXED_CLAIMED

    _seed(profile, reset=True)

    assert ContractXPGrant.objects.filter(profile=profile, source='contract', amount=1234).count() == 1
    # The reset removed the FIRST seed's grants; the re-seed paid a fresh set of the same size.
    assert ContractXPGrant.objects.filter(profile=profile, source='challenge').count() == MIXED_CLAIMED
    assert _demo_runs(profile).filter(challenge_type=CHALLENGE_TYPE_JOBS).count() == 2, 'no run pile-up'


@override_settings(DEBUG=True)
def test_reset_rebuilds_the_xp_cache_from_what_survives(catalogue):
    """`ProfileJobXP` is a denormalised sum of the ledger, so a reset that deletes grants without
    rebuilding it leaves the cache claiming XP no row supports."""
    from django.db.models import Sum

    profile = _hunter()
    _seed(profile)

    call_command('seed_challenge_demo', user=profile.psn_username, reset=True, verbosity=0)

    for cache in ProfileJobXP.objects.filter(profile=profile):
        ledger = (ContractXPGrant.objects.filter(profile=profile, job=cache.job)
                  .aggregate(t=Sum('amount'))['t'] or 0)
        assert cache.total_xp == ledger


@override_settings(DEBUG=True)
def test_a_hunters_own_run_is_never_adopted_or_deleted(catalogue):
    """THE WORST BUG THE FIRST DRAFT HAD, and the reason this test exists. `start` RESUMES a hidden run and
    hands back an already-active one, so a bare call adopted the hunter's OWN run -- and `_tag` then wrote
    the demo marker into its name. The marker is the reset scope, so the next `--reset` would have deleted
    real progress while the command reported success.

    The seeder now only tags a run it CREATED, and says out loud which scenario it skipped.
    """
    from challenges.services import challenge_service as svc

    profile = _hunter()
    real = svc.start(profile, CHALLENGE_TYPE_AZ)
    assert DEMO_TAG not in real.name

    _seed(profile)

    real.refresh_from_db()
    assert DEMO_TAG not in real.name, "the hunter's own run was adopted"
    assert not _demo_runs(profile).filter(challenge_type=CHALLENGE_TYPE_AZ).exists()

    _seed(profile, reset=True)

    assert Challenge.objects.filter(pk=real.pk).exists(), "reset deleted the hunter's own run"
    real.refresh_from_db()
    assert DEMO_TAG not in real.name


# ── --wipe, the way out of the deadlock ──────────────────────────────────────────────────────────

@override_settings(DEBUG=True)
def test_wipe_clears_the_profiles_own_runs_so_seeding_can_proceed(catalogue):
    """THE DEADLOCK THIS EXISTS FOR. The seeder refuses to adopt a run it did not create, `--reset` is scoped
    to the demo tag, and `ChallengeAdmin` has `has_delete_permission -> False` -- so a dev profile that had
    ever pressed Start could not be seeded and had no way to clear itself."""
    from challenges.services import challenge_service as svc

    profile = _hunter()
    own = svc.start(profile, CHALLENGE_TYPE_AZ)

    _seed(profile)
    assert not _demo_runs(profile).filter(challenge_type=CHALLENGE_TYPE_AZ).exists(), 'blocked, as designed'

    _seed(profile, wipe=True)

    assert not Challenge.objects.filter(pk=own.pk).exists()
    assert _demo_runs(profile).filter(challenge_type=CHALLENGE_TYPE_AZ).exists()


@override_settings(DEBUG=True)
def test_wipe_clears_a_HIDDEN_run_too(catalogue):
    """THE CASE THE OWNER HIT. Hiding a run feels like clearing it and does not: `start` RESUMES a hidden run
    rather than dealing a fresh one, so the one-active-per-type slot is still occupied as far as seeding is
    concerned -- and a hidden run is invisible on the page, so there is nothing to hide again or finish.

    (`Challenge.objects` deliberately does not filter soft-deleted rows, which is what lets one query reach
    it at all -- a default manager that hid them would have made this impossible to write.)
    """
    from challenges.services import challenge_service as svc

    profile = _hunter()
    own = svc.start(profile, CHALLENGE_TYPE_JOBS)
    svc.hide(own, profile)
    own.refresh_from_db()
    assert own.is_deleted is True

    _seed(profile, wipe=True)

    assert not Challenge.objects.filter(pk=own.pk).exists()
    assert _demo_runs(profile).filter(challenge_type=CHALLENGE_TYPE_JOBS).count() == 2


@override_settings(DEBUG=True)
def test_wipe_takes_the_challenge_grants_with_it(catalogue):
    """Same grants-before-slots order as `--reset`, for the same reason: a challenge grant's `source_id` IS
    the slot id, so once the slots are gone the rows cannot be identified again and would sit in the ledger
    forever, counted by every recompute."""
    from django.db.models import Sum

    profile = _hunter()
    _seed(profile)
    assert ContractXPGrant.objects.filter(profile=profile, source='challenge').exists()

    _seed(profile, wipe=True)

    # The wipe removed the first seed's grants; the re-seed paid a fresh set.
    assert ContractXPGrant.objects.filter(profile=profile, source='challenge').count() == MIXED_CLAIMED
    for cache in ProfileJobXP.objects.filter(profile=profile):
        ledger = (ContractXPGrant.objects.filter(profile=profile, job=cache.job)
                  .aggregate(t=Sum('amount'))['t'] or 0)
        assert cache.total_xp == ledger


@override_settings(DEBUG=True)
def test_wipe_leaves_other_profiles_alone(catalogue):
    """It is scoped to one profile. Pinned because "remove every run" is one missing filter away from
    removing every run on the site."""
    from challenges.services import challenge_service as svc

    mine, theirs = _hunter(), _hunter()
    other_run = svc.start(theirs, CHALLENGE_TYPE_AZ)

    _seed(mine, wipe=True)

    assert Challenge.objects.filter(pk=other_run.pk).exists()


@override_settings(DEBUG=True)
def test_wipe_leaves_non_challenge_xp_alone(catalogue):
    """It deletes grants by `source='challenge'` and slot id, so a contract's XP is untouchable by it."""
    profile = _hunter()
    job = Job.objects.order_by('slug').first()
    ContractXPGrant.objects.create(profile=profile, job=job, amount=4321, source='contract')

    _seed(profile, wipe=True)

    assert ContractXPGrant.objects.filter(profile=profile, source='contract', amount=4321).exists()


@override_settings(DEBUG=True)
def test_reset_does_not_imply_wipe(catalogue):
    """They are different promises. `--reset` re-seeds; `--wipe` destroys a profile's runs, which on a dev
    box is scratch data and anywhere else is somebody's progress."""
    from challenges.services import challenge_service as svc

    profile = _hunter()
    own = svc.start(profile, CHALLENGE_TYPE_AZ)

    _seed(profile, reset=True)

    assert Challenge.objects.filter(pk=own.pk).exists()


@override_settings(DEBUG=False)
def test_wipe_is_behind_the_debug_guard(catalogue):
    """The most destructive flag on the most dev-only command still answers to the same gate."""
    from challenges.services import challenge_service as svc

    profile = _hunter()
    own = svc.start(profile, CHALLENGE_TYPE_AZ)

    with pytest.raises(CommandError, match='dev database'):
        _seed(profile, wipe=True)

    assert Challenge.objects.filter(pk=own.pk).exists()


# ── the notification template, which this command is most likely to surface ──────────────────────

@override_settings(DEBUG=True)
def test_it_says_how_to_install_the_notification_template_when_it_is_missing(catalogue, capsys):
    """Completing a run fires the completion notification, and this command completes runs -- so on a database
    without the fixture loaded it emits "challenge_completed template missing" from three layers down. The
    sender degrades on purpose, which is what makes that log line easy to read as a bug in the feature.

    The test DB has no fixtures loaded, so this is the default state here.
    """
    from notifications.models import NotificationTemplate

    assert not NotificationTemplate.objects.filter(name='challenge_completed').exists()
    profile = _hunter()

    call_command('seed_challenge_demo', user=profile.psn_username)

    out = capsys.readouterr().out
    assert 'loaddata notifications/fixtures/initial_templates.json' in out
    assert 'Nothing is broken' in out


@override_settings(DEBUG=True)
def test_it_stays_quiet_once_the_template_is_there(catalogue, capsys):
    """A hint that fires when there is nothing to fix trains people to ignore it."""
    profile = _hunter()
    call_command('loaddata', 'notifications/fixtures/initial_templates.json', verbosity=0)

    call_command('seed_challenge_demo', user=profile.psn_username)

    assert 'loaddata notifications' not in capsys.readouterr().out


# ── the guards ───────────────────────────────────────────────────────────────────────────────────

@override_settings(DEBUG=False)
def test_it_refuses_to_run_outside_debug_without_force(catalogue):
    """It pays real XP into an append-only ledger. A seeder that runs anywhere is a seeder that runs in
    production once."""
    profile = _hunter()

    with pytest.raises(CommandError, match='dev database'):
        _seed(profile)

    assert not _demo_runs(profile).exists()


@override_settings(DEBUG=False)
def test_force_overrides_the_debug_guard(catalogue):
    profile = _hunter()

    _seed(profile, force=True)

    assert _demo_runs(profile).exists()


@override_settings(DEBUG=True)
def test_an_unlinked_profile_is_refused_before_anything_is_written(catalogue):
    """Every challenge write refuses an unlinked profile, so without this the command would fail one call
    in with the runs half built."""
    profile = ProfileFactory(user=UserFactory(), user_is_premium=True, is_linked=False)

    with pytest.raises(CommandError, match='linked PSN'):
        _seed(profile)

    assert not _demo_runs(profile).exists()


@override_settings(DEBUG=True)
def test_an_unknown_user_is_a_clean_refusal():
    with pytest.raises(CommandError, match='No profile'):
        call_command('seed_challenge_demo', user='nobody-by-that-name', verbosity=0)


@override_settings(DEBUG=True)
def test_list_writes_nothing(catalogue):
    """`--list` is the "where were those URLs again" path, and it must not re-seed."""
    profile = _hunter()
    _seed(profile)
    before = set(_demo_runs(profile).values_list('pk', flat=True))

    call_command('seed_challenge_demo', user=profile.psn_username, list=True, verbosity=0)

    assert set(_demo_runs(profile).values_list('pk', flat=True)) == before


@override_settings(DEBUG=True)
def test_it_survives_a_catalogue_too_thin_to_fill_every_square():
    """NO FIXTURE HERE, deliberately: with no live contracts at all the seeder must still produce runs and
    say so, rather than raising. A dev database part way through a catalogue import is the normal case."""
    profile = _hunter()

    _seed(profile)

    # ONE PER CONTRACT-BACKED TYPE, not two: with nothing to fill, the first jobs run cannot COMPLETE, so
    # nothing frees the one-active-per-type slot and the mixed scenario is skipped with a warning rather
    # than silently tagging the same run twice (which is what the first draft did). Same for A-Z.
    assert _demo_runs(profile).filter(challenge_type=CHALLENGE_TYPE_JOBS).count() == 1
    assert _demo_runs(profile).filter(challenge_type=CHALLENGE_TYPE_AZ).count() == 1
    assert _demo_runs(profile).filter(filled_count=0).exists()

    # THE CALENDAR RUN SURVIVES A THIN CATALOGUE, which is a real property rather than an accident: a day
    # is filled by a date, not by a contract, so it needs no catalogue at all. It is the one type whose
    # surfaces can be looked at on a database with no contracts imported yet.
    assert _demo_runs(profile).filter(challenge_type=CHALLENGE_TYPE_CALENDAR).count() == 1
    assert _demo_runs(profile).count() == 3


@override_settings(DEBUG=True)
def test_the_seeded_claim_pays_the_real_figure(catalogue):
    """It goes through `rewards.redeem_slot`, not a hand-written grant, so a paid row in the demo is paid
    the way a real one is -- which is the only reason looking at it proves anything."""
    profile = _hunter()

    _seed(profile)

    grants = ContractXPGrant.objects.filter(profile=profile, source='challenge')
    assert grants.count() == MIXED_CLAIMED
    assert {g.amount for g in grants} == {CHALLENGE_SLOT_JOB_XP}
    assert all(g.source_id is not None for g in grants), 'the slot id is half the idempotency guard'


# ── the "why is my pill lit" diagnostic ───────────────────────────────────────────────────────────

@override_settings(DEBUG=True)
def test_the_listing_says_why_the_nav_pill_is_lit(catalogue, capsys):
    """THE OWNER'S REPORT IS WHY THIS EXISTS: the XP pill looked stuck after claiming everything he could find,
    and nothing in this command could say whether the pill was wrong or whether some run still owed. Two
    properties make that invisible from the page alone -- the pill is PROFILE-WIDE, and hidden runs count while
    My Challenges deliberately does not list them.
    """
    profile = _hunter()
    _seed(profile)
    capsys.readouterr()   # discard the seeding output

    call_command('seed_challenge_demo', user=profile.psn_username, list=True)
    out = capsys.readouterr().out

    assert 'LIT' in out, 'the seeded runs leave squares owed, so it must say so'
    assert 'across' in out and 'run(s)' in out, 'and how many runs are responsible'
    # THE RUN URLS, because "somewhere you have unclaimed XP" is not actionable -- counted in the PILL SECTION
    # only. `_report` prints every seeded run's URL further down, so counting them over the whole output passed
    # whether or not the diagnostic emitted a link.
    pill = out.split('The My Pursuit', 1)[1]
    assert pill.count('/challenges/') >= 1


@override_settings(DEBUG=True)
def test_the_listing_says_the_pill_is_dark_once_nothing_is_owed(catalogue, capsys,
                                                               django_capture_on_commit_callbacks):
    """The other half, and the one that answers the bug report: with every square paid the diagnostic must agree
    with the pill.

    THE CAPTURE FIXTURE IS LOAD-BEARING, and leaving it out is what first made this test fail -- informatively.
    The claim clears the cached marker from `transaction.on_commit`, which pytest's never-committing transaction
    does not run, so the stale cached `True` survived and the diagnostic correctly reported that the browser and
    the truth disagree. That is the tool working; the test simply was not modelling what production does.
    """
    profile = _hunter()
    _seed(profile)
    with django_capture_on_commit_callbacks(execute=True):
        for run in Challenge.objects.filter(profile=profile, challenge_type=CHALLENGE_TYPE_JOBS):
            rewards.redeem_all(run, profile)
    capsys.readouterr()

    call_command('seed_challenge_demo', user=profile.psn_username, list=True)
    out = capsys.readouterr().out

    assert 'dark -- nothing owed anywhere' in out
    assert 'THE CACHE DISAGREES' not in out, 'the cached answer must have been cleared by the claim'
    assert rewards.has_unclaimed_xp(profile) is False, 'and the live query agrees'


@override_settings(DEBUG=True)
def test_a_hidden_run_that_owes_xp_is_flagged_as_hidden(catalogue, capsys):
    """THE CASE THAT CAN ONLY BE DIAGNOSED HERE. A hidden run's squares still owe XP and the payout door still
    pays them, so the pill counts them -- but My Challenges does not list hidden runs, so a hunter can be
    looking at a lit pill with no page that shows the run causing it."""
    from challenges.services import challenge_service as svc

    profile = _hunter()
    _seed(profile)
    owing = [r for r in Challenge.objects.filter(profile=profile, challenge_type=CHALLENGE_TYPE_JOBS)
             if rewards.redeemable_slots(r).count()]
    svc.hide(owing[0], profile)
    capsys.readouterr()

    call_command('seed_challenge_demo', user=profile.psn_username, list=True)
    out = capsys.readouterr().out

    assert 'HIDDEN -- not listed on My Challenges' in out


@override_settings(DEBUG=True)
def test_the_listing_says_why_the_nav_pill_is_lit_without_any_demo_runs(catalogue, capsys):
    """THE CASE THE DIAGNOSTIC WAS BUILT FOR AND COULD NOT REACH. `_report` returns early when the profile has
    no `[demo]` runs, and the pill section sat after that return -- so on a real account with real runs and no
    seeded ones, `--list` printed nothing about the pill. That is the shape of the account that produced the bug
    report. Every other test here seeds first, which is exactly why none of them saw it.
    """
    from challenges.services import challenge_service as svc

    profile = _hunter()
    challenge = svc.start(profile, CHALLENGE_TYPE_JOBS)
    job = Job.objects.order_by('slug').first()
    svc.assign(challenge, profile, job.slug, _contract('Real Run Game', jobs=[job]))
    svc.mark_slot_completed(challenge.slots.get(key=job.slug))
    assert not Challenge.objects.filter(profile=profile, name__contains=DEMO_TAG).exists()
    capsys.readouterr()

    call_command('seed_challenge_demo', user=profile.psn_username, list=True)
    out = capsys.readouterr().out

    assert 'The My Pursuit' in out, 'the pill section must print with no demo runs at all'
    assert 'LIT' in out and '1 owed' in out
    assert reverse('challenge_detail', args=[challenge.pk]) in out, 'and link the run that owes'


@override_settings(DEBUG=True)
def test_the_listing_reports_a_stale_cached_pill(catalogue, capsys):
    """THE MOST LIKELY CAUSE OF A PILL THAT LOOKS STUCK, and the one the first version of this diagnostic could
    not see: the browser reads a 300-second cached answer while everything else here reads the live query. The
    docstring claimed the two "cannot disagree", which was false -- the cache is a reason of the pill's own.

    A diagnostic that printed only the live answer would say "dark" while the pill stayed lit, denying a real
    problem. That is worse than having no diagnostic.
    """
    from challenges.services import rewards as rewards_mod
    from challenges.services import challenge_service as svc
    from trophies.services import career_attention

    profile = _hunter()
    challenge = svc.start(profile, CHALLENGE_TYPE_JOBS)
    job = Job.objects.order_by('slug').first()
    svc.assign(challenge, profile, job.slug, _contract('Cache Game', jobs=[job]))
    svc.mark_slot_completed(challenge.slots.get(key=job.slug))
    # The browser's answer, now cached as LIT.
    assert career_attention.has_unclaimed_challenge_xp(profile) is True
    # Paid WITHOUT running the on-commit callbacks, which is what a stale key looks like from outside.
    rewards_mod.redeem_all(challenge, profile)
    assert rewards_mod.has_unclaimed_xp(profile) is False, 'the truth is dark'
    capsys.readouterr()

    call_command('seed_challenge_demo', user=profile.psn_username, list=True)
    out = capsys.readouterr().out

    assert 'THE CACHE DISAGREES' in out
    assert 'showing LIT and the truth is dark' in out



def _seed_and_return(profile):
    """Seed, then hand the profile back, so a test can read a run in one expression."""
    _seed(profile)
    return profile


def _in_progress_calendar(profile):
    """The unfinished Calendar run, NAMED BY ITS STATE rather than taken by index.

    The A-Z tests were deliberately rewritten this way after one of them silently changed subject when
    the run order moved; the Calendar tests were written with positional indexing anyway. The order is
    pinned elsewhere, so a flip would be caught -- but a test that says what it means cannot be read
    wrongly in the first place.
    """
    return _demo_runs(profile).get(challenge_type=CHALLENGE_TYPE_CALENDAR, is_complete=False)

# ── the Plat Calendar run ────────────────────────────────────────────────────────────────────────

def _calendar_runs(profile):
    return list(_demo_runs(profile).filter(challenge_type=CHALLENGE_TYPE_CALENDAR).order_by('id'))


@override_settings(DEBUG=True)
def test_it_seeds_one_calendar_run_in_progress(catalogue):
    """ONE, because a hunter with a finished Calendar cannot start another (`TYPES_WITH_ONE_RUN`), so the
    finished/in-progress pair the other types get is not a state a real hunter can be in."""
    profile = _hunter()
    _seed(profile)

    runs = _calendar_runs(profile)
    assert len(runs) == 1
    assert runs[0].is_complete is False


@override_settings(DEBUG=True)
def test_the_in_progress_run_shows_struck_and_unstruck_crests_together(catalogue):
    """THE POINT OF A DESIGNED FILL rather than the hunter's real platinums: a struck crest beside an
    unstruck one, comparable at a glance. A dev library decides how much of a 365-day board fills, so a
    real backfill is sparse and unpredictable.

    IT USED TO CHECK THREE METALS. The crest was bronze/silver/gold for whichever of three lenses
    completed the month, and the seeder spread one of each across the row so they could be compared. One
    lens made the crest a boolean.
    """
    profile = _hunter()
    _seed(profile)
    months = calendar_render.calendar_groups(_in_progress_calendar(profile))

    struck = {i + 1 for i, m in enumerate(months) if m['is_struck']}
    assert struck == set(CALENDAR_STRUCK), 'the constants and this assertion describe one thing'
    assert months[CALENDAR_PARTIAL_MONTH - 1]['is_struck'] is False, (
        'an unstruck crest has to sit beside the struck ones')


@override_settings(DEBUG=True)
def test_the_part_filled_month_shows_the_gap_the_comparison_figure_is_about(catalogue):
    """A month whose filled count is LOWER than its platinum count, which is the only thing on the board
    that explains what "shovelware-free" is excluding.

    IT USED TO ASK FOR THREE DIFFERENT FIGURES, one per lens, and the test was named for three while
    asserting two until that was fixed. One lens leaves a pair: what you filled, and what you would have
    filled counting shovelware.
    """
    profile = _hunter()
    _seed(profile)
    months = calendar_render.calendar_groups(_in_progress_calendar(profile))
    month = months[CALENDAR_PARTIAL_MONTH - 1]

    assert month['done'] == CALENDAR_PARTIAL_CLEAN
    assert month['all_done'] == CALENDAR_PARTIAL_DAYS
    assert month['done'] < month['all_done'], 'the gap is the whole point of seeding this month'
    assert month['total'] == CALENDAR_MONTH_DAYS[CALENDAR_PARTIAL_MONTH - 1]


@override_settings(DEBUG=True)
def test_it_seeds_a_day_that_counts_but_does_not_draw(catalogue):
    """THE ONE PLACE A READER CAN SEE WHAT THE COMPARISON FIGURE COUNTS: a shovelware platinum. It is in
    `in_all` and not on the board, so the two numbers differ by exactly this square.

    THIS SLOT USED TO HOLD THE CONTRACTS-ONLY DAY -- a contract finished at its 100% tier with no
    platinum anywhere, which filled a contracts day and no platinum day, and was the feature's one
    genuinely confusing state. The collapse removed the lens and the confusion with it.
    """
    profile = _hunter()
    _seed(profile)
    run = _in_progress_calendar(profile)

    month, day = CALENDAR_SHOVELWARE_ONLY
    row = run.calendar_days.get(month=month, day=day)
    assert row.in_all is True
    assert row.in_clean is False, 'a shovelware platinum must not draw a square'


@override_settings(DEBUG=True)
def test_every_seeded_day_carries_the_date_a_cell_reads(catalogue):
    """`earned_on` is what a day cell and the coming day modal show, and a null one makes a filled square
    look like a row the backfill half-wrote."""
    profile = _hunter()
    _seed(profile)
    filled = _calendar_runs(profile)[0].calendar_days.filter(in_all=True)

    assert filled.exists()
    assert not filled.filter(earned_on=None).exists()


@override_settings(DEBUG=True)
def test_reset_takes_the_calendar_runs_and_their_days_with_it(catalogue):
    """365 rows per run, so a reset that misses them leaves thousands of orphans behind. `CalendarDay`
    cascades off the run, which is what makes the tag-scoped delete sufficient -- pinned because it is a
    property of the model rather than of this command."""
    from challenges.models import CalendarDay

    profile = _hunter()
    _seed(profile)
    assert CalendarDay.objects.filter(challenge__profile=profile).exists()

    _seed(profile, reset=True)
    assert len(_calendar_runs(profile)) == 1, 'reseeded, not accumulated'
    assert CalendarDay.objects.filter(challenge__profile=profile).count() == 365


@override_settings(DEBUG=True)
def test_the_seeded_calendar_invents_no_platinums(catalogue):
    """The same rule the rest of the command follows: it writes challenge rows, never a hunter's trophy
    history, so nothing it does can leak into Career or the boards. A Calendar run is filled by handing
    designed days to the writer, not by inventing the platinums that would have filled them."""
    from trophies.models import EarnedTrophy, ProfileGame

    profile = _hunter()
    _seed(profile)
    assert not EarnedTrophy.objects.filter(profile=profile).exists()
    assert not ProfileGame.objects.filter(profile=profile, has_plat=True).exists()


@override_settings(DEBUG=True)
def test_every_seeded_square_carries_its_own_date(catalogue):
    """THE MUTANT THAT SURVIVED. The first version clamped the day (`min(day, 28)`), so 29 of the 365
    squares held a date that was not their own -- 31 March reading "28 March 2019" -- and removing the
    clamp broke nothing, because the whole suite was blind to whether a date matched its square.

    IT IS ALSO STATE A REAL BACKFILL CANNOT PRODUCE, which is the sharper objection: the real fill remaps
    a 29 February KEY onto the 28th and never touches a date in the other eleven months. So the only
    legal key/date disagreement in real data is the single 28 February square holding a Feb-29 date, and
    a seeded board that disagrees on 29 squares would mislead the day modal it exists to serve.
    """
    profile = _hunter()
    _seed(profile)

    rows = [r for r in CalendarDay.objects.filter(challenge__profile=profile)
            if r.earned_on is not None]
    assert rows, 'nothing was filled, so this would pass vacuously'

    wrong = [(r.month, r.day, r.earned_on) for r in rows
             if (r.earned_on.month, r.earned_on.day) != (r.month, r.day)]
    assert wrong == [], 'squares whose date is not their own date: %r' % wrong[:5]


@override_settings(DEBUG=True)
def test_a_hunters_own_calendar_run_is_never_adopted(catalogue):
    """THE ADOPTION GUARD, ON THE ONE PATH THAT LIFTS THE CREATION GATE. `_start_fresh` refuses to take
    over a run it did not create, because `--reset`'s scope is the demo tag and tagging somebody's real
    run would put their progress inside it. The existing version of this test covers A-Z only, and the
    Calendar is the single call that reaches `start_reporting` with the gate open."""
    from challenges.services import challenge_service as svc

    profile = _hunter()
    original = svc.TYPES_NOT_YET_CREATABLE
    svc.TYPES_NOT_YET_CREATABLE = frozenset()
    try:
        mine = svc.start(profile, CHALLENGE_TYPE_CALENDAR)
    finally:
        svc.TYPES_NOT_YET_CREATABLE = original

    _seed(profile)

    mine.refresh_from_db()
    assert DEMO_TAG not in mine.name, 'the seeder tagged a run it did not create'
    assert _demo_runs(profile).filter(challenge_type=CHALLENGE_TYPE_CALENDAR).count() == 0, (
        'the active slot was taken, so neither Calendar run should have been dealt')

    _seed(profile, reset=True)
    mine.refresh_from_db()
    assert mine.pk, 'a reset deleted the hunter\'s own run'


@override_settings(DEBUG=True)
def test_the_seeded_board_is_not_frozen_and_the_command_says_so(catalogue):
    """FILLS ARE MONOTONE, so the next real backfill ADDS the hunter's days on top of the designed ones.
    That is not a defect -- it is what keeps a hunter's earned square from being retracted by catalogue
    bookkeeping -- but it means a browser pass can watch the board change under it, and the only
    protection is the command saying so where somebody will read it.

    PINNED ON THE DOCSTRING because that is the actual mitigation. There is no behaviour to assert: the
    merge is correct, and the thing that was wrong was a docstring recommending one of the three doors
    that trigger it as though it were harmless.
    """
    from challenges.management.commands import seed_challenge_demo as cmd

    doc = cmd.__doc__
    assert 'SEED LAST' in doc
    for door in ('--only calendar --user', '--all-calendars', 'a sync of that profile'):
        assert door in doc, 'the docstring must name every door that rewrites the designed board: %s' % door


@override_settings(DEBUG=True)
def test_the_report_does_not_invent_a_reason_for_the_missing_title_band(catalogue, capsys):
    """A diagnostic that states a fabricated reason is worse than silence, and this one did: the Plat
    Calendar's FIRST completion was reported as "no title (third or later completion)". The type grants
    no ordinal titles at all, which `rewards.TYPES_WITHOUT_ORDINAL_TITLES` says outright."""
    profile = _hunter()
    # `call_command` DIRECTLY, because `_seed` pins `verbosity=0` and this test is about what the command
    # PRINTS -- the one assertion in this file that needs the report on stdout.
    call_command('seed_challenge_demo', user=profile.psn_username, verbosity=1)

    out = capsys.readouterr().out
    # THE CALENDAR IS SEEDED IN PROGRESS now, so it reports no band at all. What is pinned is the original
    # point -- the report never INVENTS a reason -- so the fabricated one stays forbidden.
    assert 'third or later completion' not in out
    assert 'no title band' not in out, 'the report claims a band is missing while printing one'


@override_settings(DEBUG=True)
def test_the_seeded_opening_month_comes_from_the_boards_own_clock(catalogue, monkeypatch):
    """`_calendar_days` TAKES THE MONTH AS AN ARGUMENT, and the test for it calls the helper directly for
    all twelve -- which is right for the helper and leaves the WIRING uncovered. Hardcoding the call site
    to `1` passed the whole file.

    THE WIRING IS THE BEHAVIOUR CHANGE, though: the seeder read `timezone.localtime().month`, the SERVER's
    month, while the board opens on the month in the OWNER's zone. The two differ for up to a day at each
    month boundary, which is exactly when somebody seeding a board would be confused by it.

    PATCHED RATHER THAN CLOCK-DEPENDENT. Asserting against the real `today_key` would pass vacuously
    whenever the server and the owner agree, which is almost always -- and in January it would also pass
    against the hardcoded `1` this test exists to catch."""
    from challenges.services import calendar_render

    monkeypatch.setattr(calendar_render, 'today_key', lambda profile, **kw: (7, 4))
    seen = []
    real = Command.__dict__['_calendar_days'].__func__

    def spy(finished, now_month):
        seen.append(now_month)
        return real(finished, now_month)

    monkeypatch.setattr(Command, '_calendar_days', staticmethod(spy))
    _seed(_hunter())

    assert seen, 'the calendar runs were never seeded, so this proves nothing'
    assert set(seen) == {7}, 'the opening month does not come from `today_key`: %s' % seen


@override_settings(DEBUG=True)
def test_the_seeded_calendar_never_runs_the_real_backfill(catalogue, monkeypatch):
    """The demo draws a DESIGNED board, so it opts out of the history fill at creation: a dev profile's real
    platinums would otherwise fill squares the design leaves empty. Every fill it makes carries `found=`."""
    from challenges.services import calendar_fill

    real = calendar_fill.apply_to_run
    unscoped = []

    def spy(challenge, *, found=None):
        if found is None:
            unscoped.append(challenge.pk)
        return real(challenge, found=found)

    monkeypatch.setattr(calendar_fill, 'apply_to_run', spy)
    profile = _hunter()
    _seed(profile)
    assert profile.challenges.filter(challenge_type='calendar').exists(), 'no Calendar run was seeded'
    assert not unscoped, 'the seeder filled a Calendar run from the real history'
