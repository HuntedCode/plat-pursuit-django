"""The picker's two panels: what they offer, and what they cost.

WHAT IS WORTH PINNING HERE is not that the panels return data, but the three things that would each ship
silently:

**Flatness.** Both panels are bounded slices over a pool that already runs to the hundreds per letter in
prod and is growing ~150/day. A per-row lookup passes every functional test in this file -- and I shipped
exactly that once already, calling `fitting_keys` per search result, which is 24 queries for a page. The
query-count tests are the only thing that can see it.

**The labels.** An already-completed game can be offered under `import` or under `hatch`, and the two are
not interchangeable: `import` carries a fairness date, `hatch` carries an admission that our supply failed
the hunter. The panel must report what `assign` will actually stamp, because the square records it
permanently and cannot be cleared afterwards.

**What is NOT offered.** A game neither rule lifts is absent, not greyed out. A completed square's key is
absent from a search row's options. Those absences are the product decisions, so they are asserted rather
than left to the UI.
"""
import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from challenges.models import (
    CHALLENGE_TYPE_AZ,
    CHALLENGE_TYPE_JOBS,
    COMPLETED_VIA_HATCH,
    COMPLETED_VIA_IMPORT,
    HATCH_THRESHOLD,
    Challenge,
)
from challenges.services import challenge_service as svc
from challenges.services import picker
from tests.factories import (
    ConceptFactory,
    EarnedTrophyFactory,
    GameFactory,
    IGDBMatchFactory,
    ProfileFactory,
    TrophyFactory,
)
from trophies.models import Contract, EarnedContract, Job

pytestmark = pytest.mark.django_db

_SEQ = {'n': 0}


def _member():
    return ProfileFactory(user_is_premium=True)


def _contract(name, *, jobs=(), live=True, with_game=True):
    _SEQ['n'] += 1
    contract = Contract.objects.create(name=name, slug='c-%d' % _SEQ['n'], is_live=live,
                                       igdb_id=820_000 + _SEQ['n'])
    if jobs:
        contract.jobs.set(jobs)
    if with_game:
        concept = ConceptFactory(anchor_migration_completed_at=timezone.now())
        IGDBMatchFactory(concept=concept, igdb_id=contract.igdb_id, status='accepted')
        GameFactory(concept=concept)
    return contract


def _completed(profile, contract):
    return EarnedContract.objects.create(profile=profile, contract=contract, has_platinum=True,
                                         platinum_reached_at=timezone.now())


def _platted_at(profile, contract, when):
    """Completion as the TROPHY DATA records it, which is what the importer's date reads."""
    concept = ConceptFactory(anchor_migration_completed_at=timezone.now())
    IGDBMatchFactory(concept=concept, igdb_id=contract.igdb_id, status='accepted')
    game = GameFactory(concept=concept)
    plat = TrophyFactory(game=game, trophy_type='platinum')
    EarnedTrophyFactory(profile=profile, trophy=plat, earned_date_time=when)
    _completed(profile, contract)


def _joined(profile, when):
    profile.user.date_joined = when
    profile.user.save(update_fields=['date_joined'])
    return profile


def _spend_the_importer(profile, challenge_type=CHALLENGE_TYPE_AZ):
    """Record a finished run, which closes the first-run importer permanently."""
    Challenge.objects.create(profile=profile, challenge_type=challenge_type, name='Old',
                             total_slots=26, is_complete=True, completed_at=timezone.now())


# ── slot_panel: the slot-first offer ──────────────────────────────────────────────────────────────

def test_the_slot_panel_offers_the_slots_own_pool():
    profile = _member()
    challenge = svc.start(profile, CHALLENGE_TYPE_AZ)
    _contract('Bloodborne')
    _contract('Brothers')
    _contract('Astro Bot')   # a different letter: must not appear

    panel = picker.slot_panel(profile, challenge, 'B')

    assert [r['name'] for r in panel['rows']] == ['Bloodborne', 'Brothers']
    assert panel['total'] == 2


def test_an_unknown_key_has_no_panel():
    """The view will pass whatever the URL held. A slot that is not part of this run is not an error to
    explain, it is a 404 -- so the panel says None and lets the caller decide."""
    profile = _member()
    challenge = svc.start(profile, CHALLENGE_TYPE_AZ)

    assert picker.slot_panel(profile, challenge, 'card-shark') is None


def test_the_pool_is_ordered_by_name_case_insensitively():
    profile = _member()
    challenge = svc.start(profile, CHALLENGE_TYPE_AZ)
    for name in ('Bzzt', 'brothers', 'Bloodborne'):
        _contract(name)

    panel = picker.slot_panel(profile, challenge, 'B')

    assert [r['name'] for r in panel['rows']] == ['Bloodborne', 'brothers', 'Bzzt']


def test_the_order_is_stable_when_names_differ_only_in_case():
    """THE ASSERTION THE ONE ABOVE CANNOT MAKE, and the reason it cannot is worth recording.

    Dropping `Lower()` from the sort does NOT change the test above: this database collates `en_US.utf8`,
    whose primary comparison already ignores case, so raw and lowered ordering agree on ordinary names. A
    mutation run proved it -- `order_by('name')` passed.

    What `Lower()` alone genuinely breaks is determinism. Under it, names differing only in case compare
    EQUAL, and with no tiebreak Postgres returns them in any order it likes -- so a paginated slice can
    show a row on two pages or on none. The `pk` in `BY_NAME` is what fixes that, and this asserts it by
    demanding two identical calls agree, which a bare `Lower()` cannot promise.
    """
    profile = _member()
    challenge = svc.start(profile, CHALLENGE_TYPE_AZ)
    for name in ('bREAKOUT', 'Breakout', 'BREAKOUT', 'breakout'):
        _contract(name)

    # PINNED ON THE `ORDER BY` ITSELF, because the failure it guards cannot be summoned on demand.
    # Asserting that two identical calls agree does NOT catch a missing tiebreak: with four rows and
    # nothing changing between them, Postgres is free to return the same arbitrary order twice, and a
    # mutation run confirmed it does. Non-determinism is not reproducible, so the test has to assert the
    # property that rules it out -- that the sort is TOTAL -- rather than sampling for a symptom.
    with CaptureQueriesContext(connection) as captured:
        rows = picker.slot_panel(profile, challenge, 'B')['rows']

    # SCOPED TO THE POOL'S QUERY. The first ordered statement in the capture is the SLOT lookup, which
    # `ChallengeSlot.Meta.ordering` sorts by position -- so reading `ordered[0]` asserted against the wrong
    # query entirely and failed for a reason that had nothing to do with the sort under test.
    ordered = [q['sql'] for q in captured.captured_queries
               if 'ORDER BY' in q['sql'] and 'FROM "trophies_contract"' in q['sql']]
    assert ordered, 'the contract pool did not run an ordered query at all'
    clause = ordered[0].split('ORDER BY')[1]
    assert 'UPPER' in clause or 'LOWER' in clause, 'the sort must fold case'
    assert '"id"' in clause or '.id' in clause, (
        'the sort has no unique tiebreak, so rows equal under the case fold can come back in any order '
        'and a paginated slice may repeat or drop one'
    )

    # And the four case-variants really are four distinct rows, so the tiebreak has work to do.
    assert len({r['slug'] for r in rows}) == 4


def test_the_pool_is_a_bounded_slice_with_the_full_count_beside_it():
    """A letter's pool already runs to the hundreds in prod. The panel shows a page and says how many
    there are, rather than rendering the catalogue."""
    profile = _member()
    challenge = svc.start(profile, CHALLENGE_TYPE_AZ)
    for i in range(7):
        _contract('Bloodborne %d' % i)

    panel = picker.slot_panel(profile, challenge, 'B', limit=3)

    assert panel['showing'] == 3
    assert len(panel['rows']) == 3
    assert panel['total'] == 7


def test_a_search_term_narrows_the_slots_pool():
    profile = _member()
    challenge = svc.start(profile, CHALLENGE_TYPE_AZ)
    _contract('Bloodborne')
    _contract('Brothers')

    panel = picker.slot_panel(profile, challenge, 'B', query='broth')

    assert [r['name'] for r in panel['rows']] == ['Brothers']
    assert panel['total'] == 1


def test_a_one_character_term_is_ignored_rather_than_run():
    """Below `MIN_QUERY` the term is dropped, so the panel shows the whole pool instead of scanning for
    something that would match most of it."""
    profile = _member()
    challenge = svc.start(profile, CHALLENGE_TYPE_AZ)
    _contract('Bloodborne')
    _contract('Brothers')

    assert picker.slot_panel(profile, challenge, 'B', query='b')['total'] == 2


def test_a_game_you_already_finished_is_not_in_the_ordinary_pool():
    """It would land the square complete instantly, so it is not an ordinary offer -- the hatch and the
    importer are what lift it, under their own labels and their own warning."""
    profile = _member()
    _spend_the_importer(profile)
    challenge = svc.start(profile, CHALLENGE_TYPE_AZ)
    for i in range(HATCH_THRESHOLD + 2):
        _contract('Bloodborne %d' % i)
    done = _contract('Brothers')
    _completed(profile, done)

    panel = picker.slot_panel(profile, challenge, 'B')

    assert 'Brothers' not in [r['name'] for r in panel['rows']]
    assert panel['catchup'] == [], 'a deep pool and a spent importer lift nothing'


def test_the_hatch_offers_completed_games_when_the_pool_is_thin():
    profile = _member()
    _spend_the_importer(profile)
    challenge = svc.start(profile, CHALLENGE_TYPE_AZ)
    done = _contract('Brothers')
    _completed(profile, done)

    panel = picker.slot_panel(profile, challenge, 'B')

    assert [(r['name'], r['via']) for r in panel['catchup']] == [('Brothers', COMPLETED_VIA_HATCH)]
    assert panel['catchup'][0]['completed_at'] is None, 'a hatch row has no date to show'


def test_an_importable_game_carries_the_date_the_work_happened():
    """The date comes from trophy data, never from an `EarnedContract` detection stamp -- those record when
    we noticed, not when the hunter did the work."""
    profile = _member()
    _joined(profile, timezone.now() - timezone.timedelta(days=365))
    challenge = svc.start(profile, CHALLENGE_TYPE_AZ)
    done = _contract('Brothers', with_game=False)
    earned = timezone.now() - timezone.timedelta(days=30)
    _platted_at(profile, done, earned)

    panel = picker.slot_panel(profile, challenge, 'B')

    row = next(r for r in panel['catchup'] if r['name'] == 'Brothers')
    assert row['via'] == COMPLETED_VIA_IMPORT
    assert row['completed_at'] is not None
    assert abs((row['completed_at'] - earned).total_seconds()) < 5


def test_the_panel_reports_the_label_assign_will_actually_stamp():
    """THE ONE THAT MATTERS MOST, because the square records the label permanently and never clears. If
    the panel promised `hatch` and `assign` stamped `import`, a hunter would have been shown one story and
    given another, with no way back."""
    profile = _member()
    _joined(profile, timezone.now() - timezone.timedelta(days=365))
    challenge = svc.start(profile, CHALLENGE_TYPE_AZ)
    done = _contract('Brothers', with_game=False)
    _platted_at(profile, done, timezone.now() - timezone.timedelta(days=30))

    promised = picker.slot_panel(profile, challenge, 'B')['catchup'][0]['via']
    stamped = svc.assign(challenge, profile, 'B', done, acknowledge_lock=True).completed_via

    assert promised == stamped


def test_the_panel_says_what_is_currently_in_a_filled_square():
    """Reassignment needs to name what it would replace, and the name comes from the snapshot."""
    profile = _member()
    challenge = svc.start(profile, CHALLENGE_TYPE_AZ)
    svc.assign(challenge, profile, 'B', _contract('Bloodborne'))
    _contract('Brothers')

    panel = picker.slot_panel(profile, challenge, 'B')

    assert panel['slot_is_filled'] is True
    assert panel['slot_is_completed'] is False
    assert panel['current_name'] == 'Bloodborne'


def test_a_jobs_slot_is_named_by_its_job():
    profile = _member()
    challenge = svc.start(profile, CHALLENGE_TYPE_JOBS)
    job = Job.objects.order_by('slug').first()

    panel = picker.slot_panel(profile, challenge, job.slug)

    assert panel['label'] == job.name
    assert panel['job']['disc_slug'] == job.discipline


def test_an_az_slot_is_its_own_label_and_costs_no_catalogue_query():
    profile = _member()
    challenge = svc.start(profile, CHALLENGE_TYPE_AZ)

    panel = picker.slot_panel(profile, challenge, 'B')

    assert panel['label'] == 'B'
    assert panel['job'] is None


# ── search_panel: the contract-first offer ────────────────────────────────────────────────────────

def test_the_search_panel_finds_games_and_says_where_they_fit():
    profile = _member()
    challenge = svc.start(profile, CHALLENGE_TYPE_AZ)
    _contract('Bloodborne')

    panel = picker.search_panel(profile, challenge, 'blood')

    assert [r['name'] for r in panel['rows']] == ['Bloodborne']
    assert panel['rows'][0]['keys'] == ['B']


def test_a_jobs_search_offers_every_job_the_game_fills():
    """THE REASON CONTRACT-FIRST EXISTS. A game carries up to six jobs, so "where does this go?" has
    several answers and a hunter cannot know them in advance -- which slot-first cannot ask."""
    profile = _member()
    challenge = svc.start(profile, CHALLENGE_TYPE_JOBS)
    jobs = list(Job.objects.order_by('slug')[:3])
    _contract('Astro Bot', jobs=jobs)

    panel = picker.search_panel(profile, challenge, 'astro')

    assert set(panel['rows'][0]['keys']) == {j.slug for j in jobs}
    # The names are RUN-LEVEL: every result offers the same squares under the same names, so they arrive
    # once rather than repeated on each of 24 rows.
    assert panel['key_labels'][jobs[0].slug] == jobs[0].name


def test_a_short_term_returns_nothing_and_says_why():
    profile = _member()
    challenge = svc.start(profile, CHALLENGE_TYPE_AZ)
    _contract('Bloodborne')

    panel = picker.search_panel(profile, challenge, 'b')

    assert panel['rows'] == []
    assert panel['too_short'] is True


def test_a_search_result_does_not_offer_a_finished_square():
    """A completed square never reopens, so its key is not among a result's options -- even for a game
    that fits it."""
    profile = _member()
    challenge = svc.start(profile, CHALLENGE_TYPE_AZ)
    slot = svc.assign(challenge, profile, 'B', _contract('Bloodborne'))
    svc.mark_slot_completed(slot)

    panel = picker.search_panel(profile, challenge, 'brothers')
    _contract('Brothers')
    panel = picker.search_panel(profile, challenge, 'brothers')

    assert panel['rows'][0]['keys'] == [], 'B is finished, so it must not be offered'


def test_a_search_result_offers_a_filled_but_unfinished_square():
    """Reassignment is allowed on an unfinished square, so its key stays on offer."""
    profile = _member()
    challenge = svc.start(profile, CHALLENGE_TYPE_AZ)
    svc.assign(challenge, profile, 'B', _contract('Bloodborne'))
    _contract('Brothers')

    panel = picker.search_panel(profile, challenge, 'brothers')

    assert panel['rows'][0]['keys'] == ['B']


def test_a_game_already_in_the_run_says_so():
    profile = _member()
    challenge = svc.start(profile, CHALLENGE_TYPE_AZ)
    already = _contract('Bloodborne')
    svc.assign(challenge, profile, 'B', already)

    panel = picker.search_panel(profile, challenge, 'blood')

    assert panel['rows'][0]['already_in_run'] is True


def test_a_search_result_flags_what_you_have_already_finished():
    """The row needs it to explain itself: placing it would complete the square immediately, which is only
    allowed under the hatch or the importer."""
    profile = _member()
    challenge = svc.start(profile, CHALLENGE_TYPE_AZ)
    done = _contract('Bloodborne')
    _completed(profile, done)

    panel = picker.search_panel(profile, challenge, 'blood')

    assert panel['rows'][0]['is_completed_by_you'] is True


def test_an_unpublished_game_is_not_findable():
    profile = _member()
    challenge = svc.start(profile, CHALLENGE_TYPE_AZ)
    _contract('Bloodborne', live=False)

    assert picker.search_panel(profile, challenge, 'blood')['rows'] == []


# ── cost ──────────────────────────────────────────────────────────────────────────────────────────

def _cost(fn):
    with CaptureQueriesContext(connection) as captured:
        fn()
    return len(captured.captured_queries)


@pytest.mark.parametrize('challenge_type', [CHALLENGE_TYPE_AZ, CHALLENGE_TYPE_JOBS])
def test_the_search_panel_does_not_scale_with_its_results(challenge_type):
    """THE TEST THAT CAUGHT A REAL N+1 IN THIS MODULE. `search_panel` called `fitting_keys` once per row,
    which is one query each -- 24 for a page, the same N+1 `fitting_keys` was built to remove, one level
    up. Nothing else in this file noticed: every functional assertion above passed."""
    profile = _member()
    challenge = svc.start(profile, challenge_type)
    jobs = list(Job.objects.order_by('slug')[:2])
    for i in range(3):
        _contract('Bloodborne %d' % i, jobs=jobs)

    three = _cost(lambda: picker.search_panel(profile, challenge, 'bloodborne'))

    for i in range(3, 9):
        _contract('Bloodborne %d' % i, jobs=jobs)

    nine = _cost(lambda: picker.search_panel(profile, challenge, 'bloodborne'))

    assert nine == three, 'tripling the results changed the query count'


@pytest.mark.parametrize('challenge_type', [CHALLENGE_TYPE_AZ, CHALLENGE_TYPE_JOBS])
def test_the_slot_panel_does_not_scale_with_its_pool(challenge_type):
    profile = _member()
    _spend_the_importer(profile, challenge_type)
    challenge = svc.start(profile, challenge_type)
    key = 'B' if challenge_type == CHALLENGE_TYPE_AZ else Job.objects.order_by('slug').first().slug
    jobs = list(Job.objects.order_by('slug')[:1])
    for i in range(4):
        _contract('Bloodborne %d' % i, jobs=jobs)

    four = _cost(lambda: picker.slot_panel(profile, challenge, key))

    for i in range(4, 12):
        _contract('Bloodborne %d' % i, jobs=jobs)

    twelve = _cost(lambda: picker.slot_panel(profile, challenge, key))

    assert twelve == four, 'tripling the pool changed the query count'


def test_the_importers_date_read_is_skipped_when_nothing_is_importable():
    """`completion_dates` reads trophy data in three to five queries, and a hatch row displays no date -- so
    paying for one would be queries spent on nothing, on every thin slot a hunter opens after their first run.

    WHAT IT PINS NOW is the `importer_is_available` gate inside `catchup_offers`. The `_with_dates` skip its
    first docstring described has been DELETED: the dates come back with the labels from one call, which is
    what removed the double read. The oracle below is unchanged and still correct; only the thing it guards
    moved. (It also said "five queries", which overstated a 3-5 range.)"""
    profile = _member()
    _spend_the_importer(profile)
    challenge = svc.start(profile, CHALLENGE_TYPE_AZ)
    done = _contract('Brothers')
    _completed(profile, done)

    with CaptureQueriesContext(connection) as captured:
        panel = picker.slot_panel(profile, challenge, 'B')

    assert panel['catchup'][0]['via'] == COMPLETED_VIA_HATCH

    # `trophies_profilegame` IS THE ORACLE, and picking it took three attempts worth recording.
    #
    # First I asserted `'trophies_earnedtrophy' not in sql`. That passed with the skip REMOVED, because
    # `completion_dates` only reads `EarnedTrophy` when the member concepts have a platinum trophy, and
    # this fixture's game has none -- the assertion was measuring the fixture, not the code.
    #
    # Then I compared this panel's query count against an importable panel's and asserted the second was
    # larger. That passed too: removing the skip raises BOTH counts, so the inequality survived.
    #
    # `ProfileGame` is read UNCONDITIONALLY by `completion_dates` (its second date source) and by nothing
    # else on this code path -- not by `eligible_contracts`, not by `covers_by_contract`. So its presence
    # in the captured SQL is exactly equivalent to "the date read ran", which is the thing being pinned.
    sql = ' '.join(q['sql'] for q in captured.captured_queries)
    assert 'trophies_profilegame' not in sql, (
        'the trophy-date read ran for a hatch-only panel, where no row displays a date'
    )


def test_the_trophy_date_read_happens_exactly_once_per_panel():
    """THE PIN THE FLATNESS TESTS CANNOT BE. Both of those assert only that the count does not change as the
    pool grows -- and restoring the double `completion_dates` raises both sides equally, so both stay green.
    That is the same trap the comment further up this file records falling into.

    `trophies_profilegame` is the oracle because `completion_dates` reads it unconditionally as its second
    date source, and nothing else on this path touches it. COUNTED rather than tested for presence: presence
    proves the read happened, and the defect was that it happened twice.
    """
    profile = _member()
    _joined(profile, timezone.now() - timezone.timedelta(days=365))
    challenge = svc.start(profile, CHALLENGE_TYPE_AZ)
    done = _contract('Brothers', with_game=False)
    _platted_at(profile, done, timezone.now() - timezone.timedelta(days=30))

    with CaptureQueriesContext(connection) as captured:
        panel = picker.slot_panel(profile, challenge, 'B')

    assert panel['catchup'][0]['via'] == COMPLETED_VIA_IMPORT, 'the fixture must exercise the import path'
    # QUERIES, not MENTIONS. One statement names its table several times (SELECT list, FROM, WHERE), so
    # counting occurrences in the joined SQL reported six for a single read.
    reads = [q for q in captured.captured_queries if 'trophies_profilegame' in q['sql']]
    assert len(reads) == 1, (
        'the trophy-date read ran %d times; it used to run twice because `importable_ids` computed the '
        'dates and threw them away' % len(reads))


def test_the_search_panel_also_keys_the_duplicate_check_on_the_live_fk():
    """THE THIRD PLACE the FK fix had to reach, and the only one that had no test. `assign` and the pool were
    both pinned; the picker's `already_in_run` was not -- so the panel could still have offered a renamed
    game that `assign` would then refuse."""
    profile = _member()
    challenge = svc.start(profile, CHALLENGE_TYPE_JOBS)
    two = list(Job.objects.order_by('slug')[:2])
    game = _contract('Astro Bot', jobs=two)
    svc.assign(challenge, profile, two[0].slug, game)

    Contract.objects.filter(pk=game.pk).update(slug='renamed-by-staff')

    panel = picker.search_panel(profile, challenge, 'astro')

    # `already_in_run` IS the answer; `keys` deliberately still lists what the game fits. The row explains
    # itself with the flag rather than rendering as though it fits nowhere -- "already placed" and "fits
    # nothing" are different facts and a hunter needs to be told which. An earlier version of this test
    # asserted `keys == []` and was wrong about the design, not about the fix.
    assert panel['rows'][0]['already_in_run'] is True


# ── the square buttons wear their job ─────────────────────────────────────────────────────────────

def test_a_jobs_search_button_carries_its_jobs_glyph_and_discipline():
    """A button offering the Slayer square should look like the Slayer square. Both facts come off the same
    `job_atom` the grid already draws from, so the panel cannot invent a second answer to "what colour is a
    Finesse job?"."""
    profile = _member()
    challenge = svc.start(profile, CHALLENGE_TYPE_JOBS)
    job = Job.objects.order_by('slug').first()
    _contract('Astro Bot', jobs=[job])

    panel = picker.search_panel(profile, challenge, 'astro')

    assert panel['rows'][0]['keys'] == [job.slug]
    # RUN-LEVEL: the map describes the run's squares, so it covers the whole catalogue rather than only
    # the keys this one game reaches.
    assert panel['key_atoms'][job.slug] == {'icon': job.icon, 'disc_slug': job.discipline}
    assert len(panel['key_atoms']) == Job.objects.count()


def test_an_az_search_button_has_no_glyph_because_a_letter_has_none():
    """A-Z keys are letters. `key_atoms` returns `{}` for that type, so the map is empty rather than carrying
    an entry with blank fields -- the client tests for the entry, not for its contents."""
    profile = _member()
    challenge = svc.start(profile, CHALLENGE_TYPE_AZ)
    _contract('Bloodborne')

    panel = picker.search_panel(profile, challenge, 'blood')

    assert panel['rows'][0]['keys'] == ['B']
    assert panel['key_atoms'] == {}
    assert panel['key_labels'] == {}


def test_a_glyph_the_sprite_does_not_carry_is_not_sent():
    """THE TWO PATHS MUST AGREE. `job_icon_use` renders nothing for an unknown name, so a server-drawn icon
    is simply absent; a `<use href="#jobicon-typo">` built in JavaScript instead resolves to an empty box
    that still takes its width. The validation happens on this side so the browser never needs the registry.
    """
    profile = _member()
    challenge = svc.start(profile, CHALLENGE_TYPE_JOBS)
    job = Job.objects.order_by('slug').first()
    Job.objects.filter(pk=job.pk).update(icon='not-a-lucide-glyph')
    _contract('Astro Bot', jobs=[job])

    panel = picker.search_panel(profile, challenge, 'astro')

    assert panel['key_atoms'][job.slug]['icon'] == ''
    # The discipline still arrives: an unknown glyph costs the icon, not the colour.
    assert panel['key_atoms'][job.slug]['disc_slug'] == job.discipline


def test_sending_the_atoms_costs_no_extra_query():
    """The atoms REPLACED a slug-to-name read of the same 25-row catalogue rather than joining it. A second
    catalogue query per panel would be the kind of cost that looks free in review."""
    profile = _member()
    challenge = svc.start(profile, CHALLENGE_TYPE_JOBS)
    jobs = list(Job.objects.order_by('slug')[:3])
    _contract('Astro Bot', jobs=jobs)

    with CaptureQueriesContext(connection) as jobs_run:
        picker.search_panel(profile, challenge, 'astro')

    az = svc.start(_member(), CHALLENGE_TYPE_AZ)
    with CaptureQueriesContext(connection) as az_run:
        picker.search_panel(az.profile, az, 'astro')

    # A jobs run pays exactly ONE query more than A-Z: its catalogue. Asserting the difference rather than an
    # absolute keeps this about the atoms instead of re-pinning the whole panel's cost.
    assert len(jobs_run) - len(az_run) == 1


def test_a_deleted_job_takes_its_square_out_of_the_search_offers_entirely():
    """WRITTEN TO PROVE A DEGRADATION, AND IT FOUND THERE IS NONE. The intent was that a `Job` deleted under a
    live run reads "Card Shark" rather than `card-shark` in the panel, matching what the grid does. It cannot:
    the search panel derives its keys from the `Contract.jobs` M2M, and deleting the `Job` cascades those rows,
    so the key is not renamed -- it is gone.

    Which is the more important fact, and is what this pins. `total_slots` is frozen at creation, so the
    square remains on the grid with its stored `key` (degraded by `label_for_key` there) while no search result
    can ever be offered for it again -- the run becomes unwinnable. Staff deleting a `Job` with live runs
    against it is the problem; this test is the record of what it costs.
    """
    profile = _member()
    challenge = svc.start(profile, CHALLENGE_TYPE_JOBS)
    job, other = list(Job.objects.order_by('slug')[:2])
    slug = job.slug
    _contract('Astro Bot', jobs=[job, other])

    Job.objects.filter(pk=job.pk).delete()

    panel = picker.search_panel(profile, challenge, 'astro')
    row = panel['rows'][0]

    assert slug not in row['keys']
    # The run-level map is the catalogue, and the `Job` is gone from it too.
    assert slug not in panel['key_atoms']
    # Its co-tenant is untouched, so this is the one key going missing rather than the whole row failing.
    assert other.slug in row['keys']
    # And the square is still there on the grid, which is why the run is now unwinnable rather than shortened.
    assert challenge.slots.filter(key=slug).exists()


def test_a_search_that_found_nothing_does_not_read_the_catalogue():
    """`covers_by_contract` early-returns on an empty list and this did not, so typing one character past the
    last match still spent a query naming 25 squares no row would offer -- on every keystroke."""
    profile = _member()
    challenge = svc.start(profile, CHALLENGE_TYPE_JOBS)

    with CaptureQueriesContext(connection) as captured:
        panel = picker.search_panel(profile, challenge, 'nothingmatchesthis')

    assert panel['rows'] == []
    assert panel['key_atoms'] == {}
    tables = [q['sql'] for q in captured.captured_queries if 'trophies_job' in q['sql']]
    assert tables == [], 'an empty result must not read the job catalogue'


def test_every_search_panel_has_the_same_keys_whatever_the_branch():
    """ONE FUNCTION, ONE DICT SHAPE -- the same rule `slot_groups` broke on the A-Z branch. The too-short
    early return went stale the moment the run-level maps were added to the full return, and the caller got a
    `KeyError` on exactly one input: a one-character search."""
    profile = _member()
    challenge = svc.start(profile, CHALLENGE_TYPE_JOBS)
    _contract('Astro Bot', jobs=list(Job.objects.order_by('slug')[:1]))

    full = picker.search_panel(profile, challenge, 'astro')
    short = picker.search_panel(profile, challenge, 'a')
    empty = picker.search_panel(profile, challenge, 'nothingmatchesthis')

    assert set(full) == set(short) == set(empty)
    assert short['too_short'] is True


def test_a_job_slot_panel_offers_no_history_import():
    """The panel and the write door have to agree, and they do because both reach the rule through
    `catchup_offers`. With supply thick there is no catch-up block at all on a jobs run."""
    profile = _member()
    _joined(profile, timezone.now() - timezone.timedelta(days=365))
    challenge = svc.start(profile, CHALLENGE_TYPE_JOBS)
    job = Job.objects.order_by('slug').first()
    done = _contract('Astro Bot', jobs=[job])
    _platted_at(profile, done, timezone.now() - timezone.timedelta(days=30))
    for i in range(HATCH_THRESHOLD + 2):
        _contract(f'Filler {i}', jobs=[job])

    panel = picker.slot_panel(profile, challenge, job.slug)

    assert panel['catchup'] == []


def test_a_job_panel_does_not_even_ask_whether_the_importer_is_open():
    """`challenge_type == CHALLENGE_TYPE_AZ and completed_run_count(...) == 0` short-circuits, so the run
    count is never queried for a jobs run. A comparison rather than a round trip, which is what lets the
    cost paragraph say a Job Coverage panel pays none of the importer's price."""
    profile = _member()
    _joined(profile, timezone.now() - timezone.timedelta(days=365))
    challenge = svc.start(profile, CHALLENGE_TYPE_JOBS)
    job = Job.objects.order_by('slug').first()
    done = _contract('Astro Bot', jobs=[job])
    _platted_at(profile, done, timezone.now() - timezone.timedelta(days=30))
    for i in range(HATCH_THRESHOLD + 2):
        _contract(f'Filler {i}', jobs=[job])

    with CaptureQueriesContext(connection) as captured:
        picker.slot_panel(profile, challenge, job.slug)

    # `FROM "challenges_challenge"` WITH ITS CLOSING QUOTE. A bare `challenges_challenge` is a PREFIX of
    # `challenges_challengeslot`, so it matched the pool's own "not already in this run" subquery and the
    # test failed against queries that had nothing to do with the importer.
    counts = [q['sql'] for q in captured.captured_queries
              if 'FROM "challenges_challenge"' in q['sql'] and 'COUNT' in q['sql'].upper()]
    assert counts == [], 'a jobs panel must not pay the importer run count'
