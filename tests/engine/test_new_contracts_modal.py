"""The "new contracts since you last looked" modal on Career.

Per-user marker (`ui_flags['contracts_seen']`), not the global 14-day Latest window: that window
answers "is this contract new?" and this answers "is it new TO YOU?". A hunter away for three weeks
is told nothing by the window and everything by the marker, and they are who the modal exists for.
"""
import pytest
from django.urls import reverse
from django.utils import timezone

from tests.factories import ProfileFactory
from trophies.models import Contract, Job
from trophies.services import new_contracts_modal

pytestmark = pytest.mark.django_db

CF = {'HTTP_CF_RAY': '8f0000000000abcd-LHR'}

#: Contract.igdb_id is UNIQUE; see _live().
_NEXT_IGDB = 5_000_000
QUICK = 'api:user-quick-settings'


@pytest.fixture
def hunter(client):
    """Signed in, synced, and PAST the first-visit explainer -- which otherwise wins precedence and
    suppresses this modal in every test."""
    profile = ProfileFactory(is_linked=True, sync_status='synced', total_trophies=10)
    user = profile.user
    user.ui_flags = {'career_explainer': True}
    user.save(update_fields=['ui_flags'])
    client.force_login(user)
    client.profile = profile
    return client


def _live(name, *, days_ago=0, jobs=None, announced=True):
    """A published contract, POSTED by default, because that is what the modal reads -- publishing
    alone puts a contract on the board and nowhere near a reader. `announced=False` is the one-off:
    live, visible on the board, waiting for the next wave to carry it."""
    # A COUNTER, not `hash(name)`. `igdb_id` is UNIQUE and str hashing is salted per interpreter, so
    # the old expression drew a fresh random id every run: a collision was an IntegrityError that
    # could not be reproduced by re-running, in a file that creates 200+ contracts in one test.
    global _NEXT_IGDB
    _NEXT_IGDB += 1
    c = Contract.objects.create(name=name, slug=name.lower().replace(' ', '-'),
                                igdb_id=_NEXT_IGDB, is_live=True)
    c.jobs.set(jobs or list(Job.objects.exclude(is_fallback=True)[:1]))
    when = timezone.now() - timezone.timedelta(days=days_ago)
    # ANNOUNCED SIX HOURS AFTER PUBLISHING, because production never stamps them equal -- the
    # announcer runs on a daily schedule. Equal stamps made every test that reads one of the two
    # columns pass while reading the other, which is precisely the confusion this feature turns on.
    Contract.objects.filter(pk=c.pk).update(
        went_live_at=when - timezone.timedelta(hours=6),
        announced_at=when if announced else None,
        announcement_posted=bool(announced))
    c.refresh_from_db()
    return c


def _member_game(contract, url='https://cdn.example.test/cover.png', played=1):
    """A game that MEMBERS `contract`: an anchored concept whose trusted IGDB match carries the
    contract's raw igdb_id. That pair is the whole membership rule -- there is no join table."""
    from tests.factories import GameFactory, IGDBMatchFactory

    match = IGDBMatchFactory(igdb_id=contract.igdb_id, status='auto_accepted')
    concept = match.concept
    concept.anchor_migration_completed_at = timezone.now()
    concept.save(update_fields=['anchor_migration_completed_at'])
    return GameFactory(concept=concept, title_icon_url=url, played_count=played)


def _progress(contract, profile, pct):
    """Give `profile` `pct` progress on a member game of `contract` -- what `max_progress` reads."""
    from tests.factories import ProfileGameFactory

    ProfileGameFactory(profile=profile, game=_member_game(contract), progress=pct)


def _mark(user, when):
    user.ui_flags = dict(user.ui_flags or {}, contracts_seen=when.isoformat())
    user.save(update_fields=['ui_flags'])


# ── who is due ───────────────────────────────────────────────────────────────────────────────────

def test_a_hunter_with_no_marker_sees_everything_live(hunter):
    _live('Brand New')

    body = hunter.get('/career/', **CF).content.decode()

    assert 'id="new-contracts"' in body
    assert 'Brand New' in body


def test_only_contracts_newer_than_the_marker_count(hunter):
    _live('Old News', days_ago=30)
    _live('Fresh', days_ago=1)
    _mark(hunter.profile.user, timezone.now() - timezone.timedelta(days=5))

    body = hunter.get('/career/', **CF).content.decode()

    modal = body.split('id="new-contracts"', 1)[1].split('</script>', 1)[0]
    assert 'Fresh' in modal
    assert 'Old News' not in modal, 'a contract older than the marker was announced as new'


def test_nothing_newer_means_no_modal(hunter):
    _live('Seen This', days_ago=10)
    _mark(hunter.profile.user, timezone.now())

    body = hunter.get('/career/', **CF).content.decode()

    assert 'id="new-contracts"' not in body


def test_a_hunter_away_for_a_month_is_still_told(hunter):
    """The reason this uses a per-user marker rather than the board's 14-day Latest window. Under
    that window a contract published 20 days ago is not new to ANYBODY, so the person who has been
    away longest -- the one with the most to catch up on -- would be told nothing at all."""
    from trophies.util_modules.constants import NEW_CONTRACT_WINDOW_DAYS

    _live('Published While You Were Out', days_ago=NEW_CONTRACT_WINDOW_DAYS + 10)
    _mark(hunter.profile.user, timezone.now() - timezone.timedelta(days=NEW_CONTRACT_WINDOW_DAYS + 30))

    body = hunter.get('/career/', **CF).content.decode()

    assert 'id="new-contracts"' in body
    assert 'Published While You Were Out' in body


def test_a_first_visit_sees_the_last_fortnight_not_the_archive(hunter):
    """A hunter with no marker has no personal answer to "what is new to you", and the literal one --
    everything ever posted -- is useless: someone signing up in a year would be met with every wave
    since launch. A first visit falls back to the board's own 14-day window, so it shows exactly what
    the board is calling new."""
    from trophies.util_modules.constants import NEW_CONTRACT_WINDOW_DAYS

    _live('Ancient History', days_ago=NEW_CONTRACT_WINDOW_DAYS + 10)
    _live('This Week')
    assert hunter.profile.user.ui_flags.get('contracts_seen') is None, 'fixture wrong: has a marker'

    body = hunter.get('/career/', **CF).content.decode()

    assert 'This Week' in body
    assert 'Ancient History' not in body, 'a first visit was handed the whole archive'


def test_the_first_visit_floor_never_applies_to_someone_who_has_a_marker(hunter):
    """The floor is for the reader with no history, not the one with a long absence. Applying it to
    both would silently undo the reason the marker exists -- and it is the same 14-day number, so a
    floor left switched on for everyone looks exactly like it is working."""
    from trophies.util_modules.constants import NEW_CONTRACT_WINDOW_DAYS

    _live('Missed While Away', days_ago=NEW_CONTRACT_WINDOW_DAYS + 10)
    _mark(hunter.profile.user, timezone.now() - timezone.timedelta(days=NEW_CONTRACT_WINDOW_DAYS + 30))

    assert 'Missed While Away' in hunter.get('/career/', **CF).content.decode()


def test_nothing_recent_means_no_first_visit_modal(hunter):
    """The floor has to be able to empty the wave, not merely trim it. If everything posted is older
    than the window, a first visit gets no modal at all rather than an empty one."""
    from trophies.util_modules.constants import NEW_CONTRACT_WINDOW_DAYS

    _live('Long Ago', days_ago=NEW_CONTRACT_WINDOW_DAYS + 1)

    assert 'id="new-contracts"' not in hunter.get('/career/', **CF).content.decode()


def test_a_contract_that_is_not_live_is_never_announced(hunter):
    """Un-publishing has to pull a contract back even after it was announced.
    `announced_at` is stamped once and never cleared (that is what stops a re-publish from
    re-announcing), so with the stamp left in place `is_live` is the only thing between a
    withdrawn contract and every reader."""
    c = _live('Staged')
    Contract.objects.filter(pk=c.pk).update(is_live=False, went_live_at=None)
    assert Contract.objects.get(pk=c.pk).announced_at is not None, 'fixture wrong: no stamp'
    assert 'id="new-contracts"' not in hunter.get('/career/', **CF).content.decode()


# -- the staff preview ------------------------------------------------------------------------------

def test_the_preview_never_stacks_two_modals(hunter):
    """`?preview=new-contracts` was ORed onto the precedence rule rather than replacing it, so a
    staff account that had not dismissed the explainer got BOTH -- two scrims, two focus traps, and
    the gate released by whichever closed first while the other still covered the page. The view's
    own comment and the gate partial's both declare that impossible."""
    user = hunter.profile.user
    user.is_staff = True
    user.ui_flags = {}           # explainer NOT dismissed
    user.save(update_fields=['is_staff', 'ui_flags'])
    _live('Preview Me')

    body = hunter.get('/career/?preview=new-contracts', **CF).content.decode()

    assert 'id="new-contracts"' in body, 'the preview showed nothing'
    tag = body.split('id="career-howto"')[1].split('>')[0]
    assert 'data-auto' not in tag, 'the explainer auto-opens on top of the preview'


def test_the_preview_works_for_staff_who_have_already_dismissed(hunter):
    """The reader who wants to look at this modal is usually the one who has already seen it. While
    the preview honoured their marker it showed them nothing, which is the one thing a preview must
    not do."""
    user = hunter.profile.user
    user.is_staff = True
    user.save(update_fields=['is_staff'])
    _live('Old News', days_ago=40)
    _mark(user, timezone.now())

    assert 'id="new-contracts"' not in hunter.get('/career/', **CF).content.decode()
    assert 'id="new-contracts"' in hunter.get('/career/?preview=new-contracts', **CF).content.decode()


def test_the_preview_is_staff_only(hunter):
    """It bypasses the marker AND the 14-day floor, so it must not be a querystring anybody can add."""
    _live('Old News', days_ago=40)
    _mark(hunter.profile.user, timezone.now())

    assert 'id="new-contracts"' not in hunter.get(
        '/career/?preview=new-contracts', **CF).content.decode()


# -- announced, not merely published ---------------------------------------------------------------

def test_a_published_contract_is_not_shown_until_it_has_been_announced(hunter):
    """THE ONE-OFF. Publishing is a staff action that happens whenever staff happen to do it -- a
    fix, a single game re-added, a correction. Gating on it popped a modal at every hunter to
    announce one game. It is on the board immediately; the modal waits for the wave."""
    _live('Quietly Fixed', announced=False)

    assert 'id="new-contracts"' not in hunter.get('/career/', **CF).content.decode()


def test_the_announcer_is_what_releases_it(hunter):
    """And the real writer, not a fixture: `mark_announced` is called only after a confirmed 2xx, so
    a wave that never reached Discord shows nobody a modal claiming it was announced."""
    from core.services.contract_announcer import mark_announced

    contract = _live('Waiting For The Wave', announced=False)
    assert 'id="new-contracts"' not in hunter.get('/career/', **CF).content.decode()

    mark_announced([contract])

    body = hunter.get('/career/', **CF).content.decode()
    assert 'id="new-contracts"' in body and 'Waiting For The Wave' in body


def test_a_baselined_backlog_is_never_announced_to_a_reader(hunter):
    """THE LAUNCH SET. Those ~1,000 contracts DO carry `went_live_at` -- the deploy notes said
    otherwise and prod says they do -- so the first run meets a wave far past MAX_WAVE and the
    operator answers with `--baseline`, which records them as known without posting.

    `--baseline` stamps `announced_at` too, because it also settles the row for idempotency. If the
    modal read only that stamp, the operator's way of NOT announcing a backlog would announce the
    whole backlog to every hunter on their next Career load."""
    from django.core.management import call_command

    for i in range(5):
        _live('Seeded %02d' % i, announced=False)

    call_command('announce_contracts', '--baseline')

    assert Contract.objects.filter(announced_at__isnull=True).count() == 0, 'nothing was baselined'
    assert 'id="new-contracts"' not in hunter.get('/career/', **CF).content.decode()


def test_an_oversized_wave_is_refused_before_anything_is_stamped(hunter):
    """The guard that sends the operator to --baseline in the first place. It must refuse WITHOUT
    stamping, or a wave too big to post becomes a wave silently marked as posted."""
    from django.core.management import call_command
    from django.core.management.base import CommandError

    from core.management.commands.announce_contracts import MAX_WAVE

    for i in range(MAX_WAVE + 1):
        _live('Bulk %03d' % i, announced=False)

    with pytest.raises(CommandError) as err:
        call_command('announce_contracts')

    # The MESSAGE, not merely the type. Without a webhook configured this command raises
    # CommandError on the posting path too, so `raises(CommandError)` alone passed with the size
    # guard deleted -- the right exception for entirely the wrong reason.
    assert 'safety limit' in str(err.value) and '--baseline' in str(err.value)
    assert Contract.objects.filter(announced_at__isnull=False).count() == 0


def test_only_a_confirmed_post_sets_the_posted_flag():
    """`mark_announced` runs after a 2xx and nowhere else, so it is the only thing that may say a
    contract was told to anybody."""
    from core.services.contract_announcer import mark_announced

    contract = _live('Told', announced=False)
    assert contract.announcement_posted is False

    mark_announced([contract])

    contract.refresh_from_db()
    assert contract.announcement_posted is True and contract.announced_at is not None


def test_a_one_off_held_back_for_days_is_still_shown_when_its_wave_lands(hunter):
    """The skip this gate would otherwise create, and the reason the MARKER had to move to
    `announced_at` with the filter. A game fixed last week and carried by today's wave went live
    BEFORE the stamp this hunter is holding -- so a `went_live_at` filter drops it silently, and the
    contract the batching exists to deliver is the exact one nobody ever sees."""
    from core.services.contract_announcer import mark_announced

    held = _live('Fixed Last Week', days_ago=5, announced=False)
    _mark(hunter.profile.user, timezone.now() - timezone.timedelta(days=2))

    mark_announced([held])

    body = hunter.get('/career/', **CF).content.decode()
    assert 'Fixed Last Week' in body, 'the batched one-off was filtered out by its publish date'


def test_the_stamp_the_modal_offers_is_the_announcement_not_the_publish(hunter):
    """Both halves have to read the same column. A marker holding a publish time against a filter on
    the announcement time re-shows waves the reader has already dismissed."""
    contract = _live('Old But Newly Announced', days_ago=9)
    Contract.objects.filter(pk=contract.pk).update(announced_at=timezone.now())
    contract.refresh_from_db()

    modal = hunter.get('/career/', **CF).content.decode().split('id="new-contracts"', 1)[1]

    assert contract.announced_at.isoformat() in modal.replace(chr(92) + 'u002D', '-')
    assert contract.went_live_at.isoformat() not in modal.replace(chr(92) + 'u002D', '-')


# ── precedence ───────────────────────────────────────────────────────────────────────────────────

def test_the_first_visit_explainer_wins(client):
    """Never both on one visit. Showing somebody new contracts before they know what a contract IS
    is backwards -- and the explainer fires once in an account's life where this waits harmlessly
    for the next visit."""
    profile = ProfileFactory(is_linked=True, sync_status='synced', total_trophies=10)
    client.force_login(profile.user)          # no career_explainer flag: the explainer is due
    _live('Would Be Shown')

    body = client.get('/career/', **CF).content.decode()

    assert 'id="career-howto"' in body
    assert 'id="new-contracts"' not in body, 'both modals rendered on one visit'


def test_the_contracts_wait_rather_than_being_spent(client):
    """Deferred, not cancelled: dismissing the explainer must leave the wave still due."""
    profile = ProfileFactory(is_linked=True, sync_status='synced', total_trophies=10)
    client.force_login(profile.user)
    _live('Still Waiting')

    client.get('/career/', **CF)                                  # explainer visit
    profile.user.ui_flags = {'career_explainer': True}
    profile.user.save(update_fields=['ui_flags'])

    body = client.get('/career/', **CF).content.decode()

    assert 'id="new-contracts"' in body and 'Still Waiting' in body


# ── what it leads with ───────────────────────────────────────────────────────────────────────────

def test_actionable_contracts_come_first(hunter):
    """The whole reason this is a modal rather than a link to the board. `status_order` is the
    board's own SQL ranking -- claimable 0, in progress 1, everything else 2."""
    from trophies.models import EarnedContract, Game, ProfileGame
    from tests.factories import ConceptFactory, GameFactory, IGDBMatchFactory

    plain = _live('Nothing Done Here', days_ago=1)
    claimable = _live('Already Finished', days_ago=2)

    # Make `claimable` genuinely claimable for this hunter: a member game at 100%.
    concept = ConceptFactory(anchor_migration_completed_at=timezone.now())
    IGDBMatchFactory(concept=concept, igdb_id=claimable.igdb_id, status='accepted')
    game = GameFactory(concept=concept)
    ProfileGame.objects.create(profile=hunter.profile, game=game, progress=100)

    nc = new_contracts_modal.new_for(hunter.profile, hunter.profile.user)

    assert nc['total'] == 2
    assert nc['rows'][0].name == 'Already Finished', (
        'the contract they can act on is not first: %s' % [c.name for c in nc['rows']]
    )
    assert nc['rows'][0].status in ('claimable', 'pursuing')
    assert plain.name in [c.name for c in nc['rows']]
    # The heroes are the head of that same ordering, so the actionable one leads the art too.
    assert nc['heroes'][0].name == 'Already Finished'


def test_the_list_leads_with_what_this_hunter_is_furthest_along_on(hunter):
    """Within a status band the order is progress, descending. A wave is a catalogue notice until it
    is sorted by the reader's own work, at which point it is "you are 80% of the way through two of
    these" -- and the heroes, being the top of the same order, carry that on their covers."""
    # RECENCY AND ALPHABET BOTH POINT THE OTHER WAY. With the wave created in progress order, the
    # tie-breakers below `-sort_progress` produced the same list and dropping it changed nothing --
    # the fixture, not the code, was making the assertion true.
    ahead = _live('Nearly There', days_ago=5)
    behind = _live('Barely Started', days_ago=0)
    _progress(behind, hunter.profile, 12)
    _progress(ahead, hunter.profile, 88)
    _live('Untouched')

    nc = new_contracts_modal.new_for(hunter.profile, hunter.profile.user)

    assert [c.name for c in nc['rows'][:2]] == ['Nearly There', 'Barely Started']
    assert nc['heroes'][0].name == 'Nearly There', 'the heroes do not follow the list order'


def test_the_progress_order_is_applied_in_sql_not_to_the_slice(hunter):
    """Sorting the SLICE cannot promote a row from outside it. The most-progressed contract in the
    wave has to reach the top of the list even when it is the last row the cap would have kept."""
    for i in range(6):
        _live('Filler %02d' % i)
    late = _live('Zzz Last By Every Other Measure')
    _progress(late, hunter.profile, 95)

    nc = new_contracts_modal.new_for(hunter.profile, hunter.profile.user, limit=3)

    assert nc['rows'][0].name == late.name


def test_the_list_is_capped_and_counts_the_overflow(hunter):
    """The cap is a real ceiling, not a tidy number: this renders on EVERY Career load, so a bulk
    publish must not become hundreds of rows plus their job icons in a modal nobody asked for."""
    for i in range(new_contracts_modal.MAX_LIST + 3):
        _live('Contract %02d' % i)

    body = hunter.get('/career/', **CF).content.decode()
    modal = body.split('id="new-contracts"', 1)[1].split('</script>', 1)[0]

    # The `<li` prefix matters: `nc__row` alone appears TWICE per row (base class plus status
    # modifier), so counting the bare token counted every row twice.
    assert modal.count('<li class="nc__row') == new_contracts_modal.MAX_LIST
    assert 'and 3 more on the board' in modal


def test_only_the_heroes_carry_cover_art(hunter):
    """Art is fetched per hero, so the count is the query count. Every row getting one would be a
    per-row lookup on every Career render."""
    for i in range(new_contracts_modal.MAX_HEROES + 4):
        _live('Contract %02d' % i)

    nc = new_contracts_modal.new_for(hunter.profile, hunter.profile.user)

    assert len(nc['heroes']) == new_contracts_modal.MAX_HEROES
    assert all(hasattr(h, 'cover_url') for h in nc['heroes'])
    assert not any(hasattr(r, 'cover_url') for r in nc['rows'][new_contracts_modal.MAX_HEROES:])


def test_the_modals_cost_does_not_grow_with_the_number_of_heroes(hunter):
    """The guard that matters, because it is on the path that runs: `new_for` must cost the same for
    six heroes as for two. Resolving art per hero (the announcer's shape, right for a post naming
    three games) would make this modal four queries more expensive on every Career load."""
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    def cost():
        with CaptureQueriesContext(connection) as ctx:
            new_contracts_modal.new_for(hunter.profile, hunter.profile.user)
        return len(ctx.captured_queries)

    for i in range(2):
        _member_game(_live('Pair %02d' % i))
    two = cost()

    for i in range(new_contracts_modal.MAX_HEROES):
        _member_game(_live('Wave %02d' % i))

    assert cost() == two, 'the modal got more expensive purely by having more heroes'
    # AND AN ABSOLUTE CEILING. The comparison above is relative, so a mutation adding one CONSTANT
    # query per render -- dropping `with_ranking=False`, reading Job.DISCIPLINES from the DB --
    # moves both sides equally and is invisible to it. Four: the count, the rows, the jobs
    # prefetch, the hero covers. `newest` comes from the rows in memory and costs nothing.
    assert two == 4, 'the modal costs %d queries per render, not 4' % two


def test_hero_covers_are_one_query_however_many_heroes_there_are(hunter, django_assert_num_queries):
    """The announcer resolves a cover PER contract, which is right for a post naming three games and
    wrong here: this renders on every Career load, so six heroes must not be six round trips."""
    heroes = [_live('Covered %02d' % i) for i in range(new_contracts_modal.MAX_HEROES)]
    for c in heroes:
        _member_game(c)

    with django_assert_num_queries(1):
        covers = new_contracts_modal._hero_covers(heroes)

    assert len(covers) == new_contracts_modal.MAX_HEROES
    assert all(u.startswith('https://') for u in covers.values())


def test_a_hero_gets_the_cover_of_its_most_played_member_game(hunter):
    """`display_image_url` is the single source of truth for the fallback chain, and the tie-break
    between several member games is play count -- the announcer's rule, not a second one."""
    contract = _live('Two Members')
    _member_game(contract, url='https://cdn.example.test/quiet.png', played=2)
    _member_game(contract, url='https://cdn.example.test/popular.png', played=99)

    nc = new_contracts_modal.new_for(hunter.profile, hunter.profile.user)

    assert nc['heroes'][0].cover_url == 'https://cdn.example.test/popular.png'


def test_a_hero_with_no_member_game_carries_no_art_rather_than_a_broken_one(hunter):
    _live('Nothing Owns This')

    nc = new_contracts_modal.new_for(hunter.profile, hunter.profile.user)

    assert nc['heroes'][0].cover_url == ''


def test_the_nothing_new_shape_matches_the_populated_one(hunter):
    """The early return builds its dict by hand, so a renamed key survives there untouched -- as
    `jobs` did when the facets became discipline-grouped. Django templates resolve a missing key to
    empty rather than raising, so the mismatch is invisible until something iterates it."""
    _live('Anything')
    populated = new_contracts_modal.new_for(hunter.profile, hunter.profile.user)
    assert populated['rows'], 'fixture wrong: nothing is new'

    hunter.profile.user.ui_flags = {}
    empty = new_contracts_modal.new_for(hunter.profile, None)

    assert set(empty) == set(populated)


def test_the_facet_counts_can_never_promise_more_than_the_list_shows(hunter):
    """Facets are built from the rendered rows, not a separate aggregate. Counted independently they
    would include contracts past the cap -- a filter leading to an emptier list than it advertised."""
    job = Job.objects.exclude(is_fallback=True).first()
    for i in range(4):
        _live('For One Job %02d' % i, jobs=[job])

    # A LIMIT SMALL ENOUGH TO BITE. With the default cap of 200 and four contracts nothing overflows,
    # so a facet counted from the whole queryset and one counted from the shown rows agree -- and the
    # assertion below holds no matter which the code does. The cap has to be exercised to be pinned.
    nc = new_contracts_modal.new_for(hunter.profile, hunter.profile.user, limit=2)
    assert len(nc['rows']) == 2, 'fixture wrong: the cap did not bite'

    disc = next(d for d in nc['disciplines'] if d['slug'] == job.discipline)
    entry = next(j for j in disc['jobs'] if j['slug'] == job.slug)
    listed = sum(1 for r in nc['rows'] if job in r.jobs.all())
    assert entry['count'] == listed


def test_the_filter_is_grouped_by_discipline_in_canonical_order(hunter):
    """The site's own dropdown idiom rather than a flat row of up to 25 chips -- and in the same order
    the Career bands use, so a job is where the board already taught you to look.

    EVERY discipline gets a contract, and the assertion is exact equality with the whole canonical
    list. With a three-discipline fixture and a subset comparison, a reversed dict order happened to
    read as canonical and the mutation went uncaught."""
    from core.services.contract_announcer import DISCIPLINE_ORDER

    by_disc = {}
    for job in Job.objects.exclude(is_fallback=True):
        by_disc.setdefault(job.discipline, job)
    assert set(by_disc) == set(DISCIPLINE_ORDER), 'fixture wrong: not every discipline has a job'
    for disc, job in by_disc.items():
        _live('Work For ' + disc, jobs=[job])

    nc = new_contracts_modal.new_for(hunter.profile, hunter.profile.user)

    assert [d['slug'] for d in nc['disciplines']] == list(DISCIPLINE_ORDER)
    for d in nc['disciplines']:
        assert d['jobs'], '%s has no jobs under it' % d['slug']


def test_a_disciplines_count_is_distinct_contracts_not_a_sum_of_its_jobs(hunter):
    """One contract feeding two jobs in the SAME discipline is one contract to that discipline.
    Summing the job counts would say two, and the filter would promise more than it shows."""
    jobs = list(Job.objects.exclude(is_fallback=True).filter(
        discipline=Job.objects.exclude(is_fallback=True).first().discipline)[:2])
    assert len(jobs) == 2
    _live('Feeds Both', jobs=jobs)

    nc = new_contracts_modal.new_for(hunter.profile, hunter.profile.user)

    disc = next(d for d in nc['disciplines'] if d['slug'] == jobs[0].discipline)
    assert disc['count'] == 1, 'the discipline counted one contract twice'
    assert sum(j['count'] for j in disc['jobs']) == 2, 'each job should still count it'


def test_rows_carry_both_job_and_discipline_slugs(hunter):
    """The filter is client-side off these attributes: without them every selection empties the
    list, and the modal looks broken rather than filtered."""
    job = Job.objects.exclude(is_fallback=True).first()
    _live('Filterable', jobs=[job])

    body = hunter.get('/career/', **CF).content.decode()
    row = body.split('<li class="nc__row', 1)[1].split('>', 1)[0]

    assert job.slug in row.split('data-nc-jobs="', 1)[1].split('"', 1)[0]
    assert job.discipline in row.split('data-nc-discs="', 1)[1].split('"', 1)[0]


def test_every_job_icon_in_the_modal_uses_the_sprite(hunter):
    """A full inline glyph is 674 bytes; a sprite reference is 186. At up to MAX_LIST rows with
    several jobs each, that 3.6x is the whole reason the list can be this long -- and it was the
    reason the cap sat at 60 before the icons were fixed.

    EVERY occurrence, because the modal draws job icons in three places (heroes, rows, the job
    popovers) and asserting that the sprite appears somewhere passes while two of the three are
    inline."""
    job = Job.objects.exclude(is_fallback=True).first()
    _live('Iconed', jobs=[job])

    body = hunter.get('/career/', **CF).content.decode()
    modal = body.split('id="new-contracts"', 1)[1].split('</script>', 1)[0]

    seen = 0
    for css in ('nc__job-ic', 'rp-pop__ico'):
        chunks = modal.split('class="%s"' % css)[1:]
        assert chunks, 'no %s icon rendered at all' % css
        for chunk in chunks:
            svg = chunk.split('</svg>', 1)[0]
            assert '<use href="#jobicon-' in svg, '%s renders an inline glyph' % css
            seen += 1
    assert seen >= 2





# ── the marker ───────────────────────────────────────────────────────────────────────────────────

def test_the_stamp_offered_is_the_waves_newest_not_now(hunter):
    """A contract published between this render and the dismissal went live BEFORE the click but
    AFTER the query. A now-stamp would mark it seen without ever showing it; storing what was shown
    cannot skip anything."""
    # A WAVE, not one contract. With a single row newest == oldest, so `order_by('-announced_at')`
    # losing its minus sign was invisible -- and that offers the OLDEST stamp of the wave, leaving
    # the marker below the rest of it and re-showing the identical modal on every later visit.
    _live('Oldest', days_ago=9)
    _live('Middle', days_ago=5)
    newest = _live('Newest', days_ago=1)

    body = hunter.get('/career/', **CF).content.decode()
    modal = body.split('id="new-contracts"', 1)[1]

    newest.refresh_from_db()
    assert newest.announced_at.isoformat() in modal.replace('\\u002D', '-'), (
        'the modal offers a stamp that is not the newest contract it showed'
    )


def test_the_endpoint_stores_the_marker(hunter):
    stamp = timezone.now() - timezone.timedelta(days=1)

    resp = hunter.post(reverse(QUICK),
                       data={'setting': 'contracts_seen', 'value': stamp.isoformat()},
                       content_type='application/json')

    assert resp.status_code == 200
    hunter.profile.user.refresh_from_db()
    assert new_contracts_modal.seen_marker(hunter.profile.user) is not None


def test_the_stamp_offered_covers_only_what_was_shown(hunter):
    """Above `MAX_LIST` the newest announcement in the wave and the newest one RENDERED are different
    contracts, because `_ORDER` leads with actionability rather than time. Offering the global max
    marks the un-rendered remainder seen, and `announced_at__gt=marker` then hides it forever.

    Below the cap the two are identical, which is why every other stamp test passes either way."""
    old_but_actionable = _live('Started It', days_ago=9)
    _progress(old_but_actionable, hunter.profile, 40)
    newest_overall = _live('Announced Today', days_ago=0)

    nc = new_contracts_modal.new_for(hunter.profile, hunter.profile.user, limit=1)

    assert [c.name for c in nc['rows']] == ['Started It'], 'fixture wrong: the cap cut the wrong row'
    newest_overall.refresh_from_db()
    old_but_actionable.refresh_from_db()
    assert nc['newest'] == old_but_actionable.announced_at
    assert nc['newest'] != newest_overall.announced_at, (
        'dismissing would mark a contract seen that was never rendered'
    )


def test_the_endpoint_stores_THE_STAMP_IT_WAS_SENT(hunter):
    """`is not None` was the whole assertion, so writing `timezone.now()` instead of the posted value
    passed -- the exact now-stamp this design exists to prevent, pinned on the client and unpinned on
    the server."""
    stamp = timezone.now() - timezone.timedelta(days=3)

    hunter.post(reverse('api:user-quick-settings'),
                data={'setting': 'contracts_seen', 'value': stamp.isoformat()},
                content_type='application/json')

    hunter.profile.user.refresh_from_db()
    stored = new_contracts_modal.seen_marker(hunter.profile.user)
    assert abs((stored - stamp).total_seconds()) < 1


def test_dismissing_the_modal_actually_silences_it(hunter):
    """The whole loop, end to end: render, post the stamp the page offered, render again. Every part
    was covered in isolation and the seam between them was not -- `_mark` writes the flag directly,
    so nothing proved the offered stamp and the stored marker were the same thing."""
    _live('Read It')

    body = hunter.get('/career/', **CF).content.decode()
    assert 'id="new-contracts"' in body
    offered = body.split('PAGE_STAMP = ' + chr(39), 1)[1].split(chr(39), 1)[0]
    offered = offered.replace(chr(92) + 'u002D', '-')

    resp = hunter.post(reverse('api:user-quick-settings'),
                       data={'setting': 'contracts_seen', 'value': offered},
                       content_type='application/json')
    assert resp.status_code == 200

    assert 'id="new-contracts"' not in hunter.get('/career/', **CF).content.decode()


def test_a_wellformed_but_impossible_stamp_is_a_400_not_a_500(hunter):
    """`parse_datetime` returns None when the REGEX misses but RAISES on February 31st, hour 25 or a
    +99:00 offset. Only the None half was handled, so those went out as a 500."""
    for value in ('2026-02-31T00:00:00', '2026-09-10T25:00:00', '2026-09-10T12:00:00+99:00'):
        resp = hunter.post(reverse('api:user-quick-settings'),
                           data={'setting': 'contracts_seen', 'value': value},
                           content_type='application/json')
        assert resp.status_code == 400, '%r came back %s' % (value, resp.status_code)


def test_an_impossible_stored_marker_shows_the_modal_rather_than_500ing_career(hunter):
    """The same crash on the read side, where it is worse: an unparseable marker in ui_flags took
    /career/ down for that account on every render, permanently, with nothing in the UI able to clear
    it -- the exact failure `seen_marker` is written to avoid."""
    user = hunter.profile.user
    user.ui_flags = dict(user.ui_flags, contracts_seen='2026-02-31T00:00:00')
    user.save(update_fields=['ui_flags'])
    _live('Still Visible')

    assert new_contracts_modal.seen_marker(user) is None
    assert 'id="new-contracts"' in hunter.get('/career/', **CF).content.decode()


def test_the_marker_never_moves_backwards(hunter):
    """A stale tab dismissed after a newer visit would rewind the marker and re-show a wave already
    read. The stored value only ever moves forward."""
    recent = timezone.now() - timezone.timedelta(days=1)
    _mark(hunter.profile.user, recent)
    hunter.profile.user.refresh_from_db()

    hunter.post(reverse('api:user-quick-settings'),
                data={'setting': 'contracts_seen',
                      'value': (timezone.now() - timezone.timedelta(days=30)).isoformat()},
                content_type='application/json')

    hunter.profile.user.refresh_from_db()
    stored = new_contracts_modal.seen_marker(hunter.profile.user)
    assert abs((stored - recent).total_seconds()) < 1


def test_an_ancient_marker_cannot_turn_every_render_into_a_full_catalogue_scan(hunter):
    """One POST of `0001-01-01` defeated the no-marker 14-day floor and made every later /career/
    render sort and materialise every contract ever announced, for that account, forever."""
    hunter.post(reverse('api:user-quick-settings'),
                data={'setting': 'contracts_seen', 'value': '0001-01-01T00:00:00+00:00'},
                content_type='application/json')

    hunter.profile.user.refresh_from_db()
    stored = new_contracts_modal.seen_marker(hunter.profile.user)
    assert stored > timezone.now() - timezone.timedelta(days=366)


def test_the_endpoint_refuses_junk(hunter):
    for bad in ('not a date', '', 12345, None, ['2026-01-01']):
        resp = hunter.post(reverse(QUICK),
                           data={'setting': 'contracts_seen', 'value': bad},
                           content_type='application/json')
        assert resp.status_code == 400, '%r was accepted' % (bad,)

    hunter.profile.user.refresh_from_db()
    assert 'contracts_seen' not in (hunter.profile.user.ui_flags or {})


def test_a_future_stamp_is_clamped_to_now(hunter):
    """Unclamped, a stamp years ahead suppresses the modal for that account permanently, with
    nothing in the UI able to undo it."""
    far = timezone.now() + timezone.timedelta(days=3650)

    hunter.post(reverse(QUICK), data={'setting': 'contracts_seen', 'value': far.isoformat()},
                content_type='application/json')

    hunter.profile.user.refresh_from_db()
    stored = new_contracts_modal.seen_marker(hunter.profile.user)
    assert stored <= timezone.now() + timezone.timedelta(seconds=5)


def test_an_unparseable_marker_shows_the_modal_rather_than_hiding_it(hunter):
    """The safe direction for a value we do not fully control: a junk marker means "we do not know
    what you have seen", and the answer to that is to show you, not to go silent forever."""
    _live('Should Still Appear')
    hunter.profile.user.ui_flags = {'career_explainer': True, 'contracts_seen': 'garbage'}
    hunter.profile.user.save(update_fields=['ui_flags'])

    assert 'id="new-contracts"' in hunter.get('/career/', **CF).content.decode()


# ── the page's motion ────────────────────────────────────────────────────────────────────────────

def test_the_modal_settles_the_career_gate(hunter):
    """Career's numbers count up as they scroll in, and the ones above the fold are in view at load.
    Behind a scrim they would finish before the reader closed the modal."""
    _live('Anything')

    body = hunter.get('/career/', **CF).content.decode()
    modal = body.split('id="new-contracts"', 1)[1]

    # SCOPED TO THE OPTIONS. A bare `'ppSettleCareerModal' in modal` is satisfied by the local
    # `settle` helper's own definition, so deleting `onSettled: settle` from the DetailModal options
    # left this green -- and with `onOpened` cancelling the backstop, nothing would ever release the
    # gate: Career's count-ups frozen at 0 for the life of the page, worse than the original bug.
    opts = modal.split('PlatPursuit.DetailModal(el, {', 1)[1].split('});', 1)[0]
    assert 'onSettled: settle' in opts, 'nothing releases the page motion when this modal closes'
    assert 'ppHoldCareerModal' in opts, 'the backstop is not cancelled when this modal opens'


def test_the_gate_arms_when_this_modal_is_due(hunter):
    """The half that is easy to miss: every other gate test checks the UNARMED case, and dropping
    this flag from the gate leaves the suite green while every visit ships an unarmed gate."""
    _live('Anything')

    body = hunter.get('/career/', **CF).content.decode()

    assert 'id="new-contracts"' in body, 'fixture wrong: no modal is due'
    gate = body.split('ppAfterCareerModal', 1)[0]
    assert 'pending.contracts = true' in gate, 'a modal is on the page but the gate is not holding'


def test_the_gate_is_armed_PER_MODAL_so_an_unarmed_one_cannot_release_it(hunter):
    """THE BUG THIS REPLACED. The explainer's markup renders on every visit -- the edhint has to be
    able to reopen it -- and a DetailModal with no autoOpenDelay settles immediately. With one shared
    boolean, the explainer settled the gate at DOMContentLoaded, about 450ms before THIS modal opened,
    so Career's count-ups ran behind the scrim on every visit the gate exists for.

    Named arms make an unarmed modal's settle a no-op: it can only remove itself, and it is not in
    the set."""
    _live('Anything')

    body = hunter.get('/career/', **CF).content.decode()
    gate = body.split('ppAfterCareerModal', 1)[0]

    assert 'pending.explainer = true' not in gate, 'the dismissed explainer is still arming the gate'
    # And the name has to be CHECKED. A settle that ignores it releases the page for whichever modal
    # reports first, which is the bug with an extra argument.
    # The whole settle function, because each half of it is separately defeatable: a name that is
    # never checked releases for whichever modal reports first (the original bug with an extra
    # argument), and a counter that never decrements never releases at all.
    settle = body.split('window.ppSettleCareerModal = function (name) {', 1)[1].split('};', 1)[0]
    assert 'if (name) {' in settle, 'the name is ignored, so any modal releases the gate'
    assert 'if (!pending[name]) { return; }' in settle, 'the gate settles for a modal it never armed'
    assert 'waiting -= 1' in settle, 'nothing counts down, so the gate never releases'
    assert "ppSettleCareerModal('explainer')" in body, 'the explainer settles anonymously again'
    assert "ppSettleCareerModal('contracts')" in body, 'this modal settles anonymously again'


def test_a_visit_due_neither_modal_ships_an_unarmed_gate(hunter):
    """The half no test covered, despite a docstring claiming otherwise. An always-armed gate holds
    every Career visit's motion until the 4-second backstop -- a site-wide regression that no
    assertion in this file could see."""
    body = hunter.get('/career/', **CF).content.decode()

    assert 'id="new-contracts"' not in body, 'fixture wrong: something is due'
    gate = body.split('ppAfterCareerModal', 1)[0]
    assert 'pending.contracts = true' not in gate and 'pending.explainer = true' not in gate, (
        'nothing is due but the gate is holding the page'
    )


# -- the filter reuses the site's dropdown, whole --------------------------------------------------

def _partial():
    from pathlib import Path

    from django.conf import settings

    return (Path(settings.BASE_DIR) / 'templates' / 'trophies' / 'partials' / 'career' /
            '_new_contracts.html').read_text(encoding='utf-8')


def test_the_filter_is_the_shared_dropdown_and_not_a_private_copy(hunter):
    """The first cut was a bespoke row of chips. This is the shared `.rp-discs` group the contracts
    board and Browse Games use -- markup AND the controller that owns opening it. Hand-rolling the
    open/close here is how the three surfaces come to behave differently from each other."""
    job = Job.objects.exclude(is_fallback=True).first()
    _live('Grouped', jobs=[job])

    body = hunter.get('/career/', **CF).content.decode()
    modal = body.split('id="new-contracts"', 1)[1].split('</script>', 1)[0]
    # THE MARKUP ONLY. The inline script mentions both class names as selectors, so renaming them in
    # the HTML -- the private copy this test forbids -- left both assertions true.
    markup = modal.split('<script>', 1)[0]

    assert 'rp-disc__trigger' in markup and 'rp-pop__item' in markup
    assert 'var(--disc-%s)' % job.discipline in markup, 'the trigger is not tinted in its discipline'
    assert 'PlatPursuit.discPopovers' in modal, 'the modal opens its own popovers instead'


def test_escape_closes_an_open_popover_without_closing_the_modal():
    """Both discPopovers and DetailModal close on Escape from document in the bubble phase, so one
    press did both: dismissing a dropdown took the whole modal with it. The fix has to be CAPTURE
    phase (it runs before either) and has to be conditional on a popover actually being open, or
    Escape stops closing the modal at all."""
    partial = _partial()

    # `}, true);` was the SEPARATOR of this split and never asserted -- and `split` returns the whole
    # string when the separator is missing, so dropping capture phase silently widened the window to
    # the rest of the file, where all three tokens still appear. The one property the test named was
    # the one it could not see.
    assert partial.count('}, true);') == 2, 'a capture-phase listener stopped capturing'
    guard = partial.split("if (e.key !== 'Escape'", 1)[1].split('}, true);', 1)[0]
    assert 'popoverOpen()' in guard, 'the guard swallows Escape when no popover is open'
    assert 'stopPropagation' in guard, 'the modal still closes underneath the popover'
    assert 'pops.closeAll' in guard, 'nothing closes the popover the press was meant for'


def test_a_scrim_click_with_a_popover_open_closes_only_the_popover(hunter):
    """The click twin of the same collision, and the more costly one: discPopovers treats a scrim
    click as click-outside while DetailModal treats it as dismiss, so closing a dropdown that way
    closed the whole modal AND recorded the wave as read."""
    partial = _partial()

    click = partial.split("document.addEventListener('click'", 1)[1].split('}, true);', 1)[0]
    assert 'popoverOpen()' in click, 'the guard fires when no popover is open'
    assert 'stopPropagation' in click, 'the modal still closes underneath the popover'
    # SCOPED TO THE SCRIM. The first cut guarded on "not inside `.rp-disc`" -- which is everything
    # else in the dialog -- and stopPropagation from a document CAPTURE listener kills the event
    # before any handler below it. So with a dropdown open, the first click on the close button, the
    # sort chips and the All chip all did nothing, and the board LINK navigated anyway (stopping
    # propagation is not preventing the default) with the dismissal never recorded.
    assert "closest('.pp-detail-modal__scrim')" in click, (
        'the guard swallows the first click on every other control in the dialog'
    )


def test_the_other_controls_still_work_on_the_first_click(hunter):
    """The regression the scoped guard exists to avoid, pinned at the markup level: the controls the
    capture listener must not stand in front of are all OUTSIDE `.pp-detail-modal__scrim`."""
    _live('Anything')

    body = hunter.get('/career/', **CF).content.decode()
    modal = body.split('id="new-contracts"', 1)[1].split('</script>', 1)[0]

    # The scrim is EMPTY, and has to stay that way: the capture guard stops the event dead for
    # anything inside it, so a control nested there would need two clicks -- or, if it were a link,
    # would navigate with the dismissal unrecorded.
    scrim = modal.split('class="pp-detail-modal__scrim"', 1)[1].split('</div>', 1)[0]
    assert scrim.strip() in ('data-nc-close>', 'data-nc-close >'), (
        'the scrim gained a child, which the click guard will swallow'
    )


def test_a_selected_job_stays_visible_after_its_popover_closes():
    """The popover shuts on selection, so the only lasting sign of what is filtering the list is the
    discipline trigger. Marking the pressed item alone leaves the reader with a filtered list and
    nothing on screen saying why."""
    partial = _partial()

    end = chr(10) + '    }'
    mark = partial.split('function markActive', 1)[1].split(end, 1)[0]
    # `button.closest`, not a bare `closest('.rp-disc')`: the comparison one line below mentions the
    # same selector, so the loose form stayed true with the lookup itself replaced by null.
    assert "button.closest('.rp-disc')" in mark, 'the trigger above a pressed job item is never lit'
    # And CONSUMED. Pinning only the lookup left `toggle('is-active', c === button)` -- owner
    # computed, never read, and the trigger dark again.
    # `|| ownerTrigger`, the USE. The declaration one line above is inside the same forEach, so
    # asserting the bare name passed with the toggle reduced to `c === button`.
    assert '|| ownerTrigger' in mark, 'owner is computed but never applied'
    # The CODE, not the comment above it that names the class: reading the prose passed while the
    # toggle itself was deleted.
    assert "toggle('is-selected'" in mark, "the shared popover's own selected state is not applied"


# -- it has to fit on one screen -------------------------------------------------------------------

def _elements_css():
    from pathlib import Path

    from django.conf import settings

    return (Path(settings.BASE_DIR) / 'static' / 'css' / 'components' / 'elements.css').read_text(
        encoding='utf-8')


def _rule(css, selector):
    """The declarations of one rule, by exact selector."""
    assert selector + ' {' in css, 'no rule for %s' % selector
    return css.split(selector + ' {', 1)[1].split('}', 1)[0]


def test_the_dialog_holds_still_and_only_the_list_scrolls():
    """`.pp-howto__dialog` scrolls its whole self, which took the close button off screen and let the
    reader scroll the modal instead of its list. The dialog is a flex column pinned to a viewport
    fraction; the list is the only scroller."""
    css = _elements_css()

    dialog = _rule(css, '.pp-howto .nc__dialog')
    assert 'max-height: 88vh' in dialog and 'flex-direction: column' in dialog

    body = _rule(css, '.pp-howto .nc__dialog > .pp-detail-modal__body')
    assert 'min-height: 0' in body, "a flex item's min-height:auto refuses to shrink, so the list cannot scroll"

    lst = _rule(css, '.nc__list')
    assert 'overflow-y: auto' in lst and 'flex: 1 1 auto' in lst

    # AND THE CHROME AROUND IT HAS TO BE ABLE TO GET OUT OF THE WAY. Every other child is a default
    # flex item, so none of them can shrink below their content -- the dialog's minimum is the sum of
    # its chrome, and where that exceeds 88vh the footer buttons render below the viewport with no
    # scrollbar to reach them (overflow is visible, deliberately, for the popovers). At 375x667 the
    # heroes alone were ~240px of that sum. Height queries are what give it back.
    assert '@media (max-height: 620px)' in css, 'nothing responds to a SHORT viewport'
    short = css.split('@media (max-height: 620px)', 1)[1].split('}\n', 1)[0]
    assert '.nc__heroes' in short, 'the covers still hold their height on a landscape phone'


def test_the_dialog_never_clips_because_the_popovers_live_inside_it():
    """The discipline popovers are absolutely positioned inside the dialog, so ANY clipping on it
    cuts them off at its edge -- which is what made them look like they were not opening at all.
    This is the one rule that has to stay `visible` even though the box is height-capped."""
    dialog = _rule(_elements_css(), '.pp-howto .nc__dialog')

    assert 'overflow: visible' in dialog


def test_a_popover_with_no_room_below_it_opens_upward_and_is_clamped():
    """discPopovers flipped at the horizontal viewport edge but always opened downward -- right on a
    page you can scroll, wrong in a dialog, where a popover taller than the room beneath it runs off
    the screen with no way to reach it.

    It lives in the SHARED control, not this modal: the Career board and Browse Games have the same
    edge, and a fix kept in one consumer is a fix the other two never get. And a flip alone is not
    enough -- on a landscape phone neither direction fits a 300px popover, so the height is clamped
    to whatever room the chosen direction actually has."""
    from pathlib import Path

    from django.conf import settings

    js = (Path(settings.BASE_DIR) / 'static' / 'js' / 'utils.js').read_text(encoding='utf-8')
    body = js.split('function discPopovers', 1)[1].split('window.PlatPursuit.discPopovers', 1)[0]

    assert "classList.add('rp-pop--up')" in body, 'the shared control still only opens downward'
    assert 'window.innerHeight' in body, 'the flip is not measured against the viewport'
    assert 'Math.min(300, Math.max(64, room))' in body, (
        'a flipped popover can still be taller than the room it flipped into'
    )
    assert "p.style.maxHeight = ''" in body, 'the clamp is never cleared, so it leaks to the next open'

    up = _rule(_elements_css(), '.rp-pop--up')
    assert 'bottom: calc(100% + 6px)' in up and 'top: auto' in up

    # And it must NOT be scoped to this modal, or the two other consumers keep the bug.
    assert '.nc__toolbar .rp-pop--up' not in _elements_css()


def test_the_heroes_are_two_four_six_by_breakpoint(hunter):
    """The server renders all six and CSS hides what does not fit, so the count follows the screen
    without a second render path.

    ASSERTED PER MEDIA BLOCK, because the counts are made of FOUR rules whose nesting is the whole
    behaviour. Searching one flat chunk for substrings could not tell a re-show rule inside its
    media query from one hoisted to the top -- which is 4 heroes at every size, including desktop's
    six-wide row -- and did not check the re-show rules existed at all."""
    for i in range(new_contracts_modal.MAX_HEROES + 2):
        _live('Hero %02d' % i)

    body = hunter.get('/career/', **CF).content.decode()
    modal = body.split('id="new-contracts"', 1)[1].split('</script>', 1)[0]
    assert modal.count('class="nc__hero"') == 6, 'the server is not rendering six heroes'

    # Scoped to the modal's own region first: both breakpoints appear elsewhere in this file,
    # and a bare split lands in whichever component happens to come first.
    css = _elements_css()
    region = css.split('.nc__heroes {', 1)[1].split('.nc__hero-art {', 1)[0]
    close = '}' + chr(10) + '}'
    base = region.split('@media', 1)[0]
    tablet = region.split('@media (min-width: 640px) {', 1)[1].split(close, 1)[0]
    desktop = region.split('@media (min-width: 1024px) {', 1)[1].split(close, 1)[0]

    assert 'calc((100% - 12px) / 2)' in base and 'nth-child(n + 3) { display: none' in base
    assert 'calc((100% - 36px) / 4)' in tablet
    assert 'nth-child(n + 3) { display: block' in tablet, 'a tablet shows 2 heroes in a 4-wide row'
    assert 'nth-child(n + 5) { display: none' in tablet
    assert 'calc((100% - 60px) / 6)' in desktop
    assert 'nth-child(n + 5) { display: block' in desktop, 'a desktop shows 4 heroes in a 6-wide row'

    # Centred, so a two-contract wave is not three covers hard-left against half a row of nothing.
    assert 'justify-content: center' in css.split('.nc__heroes {', 1)[1].split('}', 1)[0]


def test_every_hero_caption_reserves_two_lines(hunter):
    """The caption clamps at two lines, so a long title takes two and a short one takes one -- which
    left every hero's job icons at a different height and the row looking ragged. Reserving the
    second line is what lines the icon strips up."""
    css = _elements_css()
    name = _rule(css, '.nc__hero-name')

    assert 'min-height: 2.5em' in name, 'a one-line title collapses its caption and drops the icons'
    assert '-webkit-line-clamp: 2' in name, 'the reserved height no longer matches the clamp'


def test_the_hero_status_badge_stays_on_one_line(hunter):
    """It read "Ready to claim" and wrapped across the bottom of a 110px cover, competing with the
    art. Fixed by shortening the WORDS rather than the type -- going under the readable floor is the
    trade the design system says not to make. The row below still says it in full."""
    from trophies.models import EarnedContract

    contract = _live('Claimable Hero')
    # CLAIMABLE, not merely finished: the board's status is reached-but-not-accepted on the
    # EarnedContract, so progress alone renders "pursuing" and this would test the wrong badge.
    EarnedContract.objects.create(profile=hunter.profile, contract=contract,
                                  full_reached_at=timezone.now())

    body = hunter.get('/career/', **CF).content.decode()
    modal = body.split('id="new-contracts"', 1)[1].split('</script>', 1)[0]
    hero = modal.split('nc__heroes', 1)[1].split('nc__toolbar', 1)[0]

    assert '>Ready<' in hero, 'the hero badge is back to the phrase that wrapped'
    chip = _rule(_elements_css(), '.nc__hero-chip')
    assert 'white-space: nowrap' in chip, 'the badge can wrap again'
    # ...and the LIST keeps the full phrase, which is where the width is.
    assert 'Ready to claim' in modal.split('nc__list', 1)[1]


def test_a_cover_can_never_grow_taller_than_the_screen_allows():
    """The grid column sets the width and `aspect-ratio` sets the height from it, so on a short
    viewport six covers were still tall enough to push the list off the bottom."""
    css = _elements_css()
    art = _rule(css, '.nc__hero-art')

    assert 'max-height: 24vh' in art
    # And tighter again where the viewport is short, which is where it actually mattered: a
    # 1366x768 laptop and a 667px phone both overflowed the dialog at 24vh.
    # Qualified to phones and tablets: unqualified it fired on every laptop and cost the desktop
    # 30% of its cover height for no fit benefit -- on the design system's primary target.
    assert ('@media (max-height: 800px) and (max-width: 1023px) { .nc__hero-art { max-height: 18vh; } }') in css
    assert 'object-position: top' in _rule(css, '.nc__hero-art img'), (
        'cropping without object-top eats the logo at the top of the cover'
    )


# -- sorting ---------------------------------------------------------------------------------------

def test_every_row_carries_both_sort_keys_in_the_servers_order(hunter):
    """The sort is a client-side re-append, so the keys have to be on the rows. `data-nc-i` is the
    SERVER's order -- the one the heroes were chosen by -- not a number the client recomputes."""
    ahead = _live('Zzz Furthest Along')
    _progress(ahead, hunter.profile, 90)
    _live('Aaa Untouched')

    body = hunter.get('/career/', **CF).content.decode()
    rows = body.split('id="new-contracts"', 1)[1].split('<li class="nc__row')[1:]

    assert [r.split('data-nc-i="', 1)[1].split('"', 1)[0] for r in rows][:2] == ['0', '1']
    # Row 0 is the most-progressed contract, and its name key is lowercased for comparison.
    assert rows[0].split('data-nc-name="', 1)[1].split('"', 1)[0] == 'zzz furthest along'


def test_the_sort_switcher_offers_progress_and_a_to_z(hunter):
    """Progress first because it is the answer to "which of these am I nearly done with"; A-Z is for
    the reader hunting one title. The shared `.pp-switch`, which is the site's ONE toggle treatment
    -- a bespoke pair of buttons here is how a third switcher look gets born."""
    _live('Anything')

    body = hunter.get('/career/', **CF).content.decode()
    modal = body.split('id="new-contracts"', 1)[1].split('</script>', 1)[0]

    # The whole control: its opening tag back through the class list, and its two chips.
    head, switch = modal.split('data-nc-sort', 1)
    switch = switch.split('</div>', 1)[0]
    assert 'pp-switch' in head.rsplit('<div', 1)[-1], 'the sort control is not the shared switcher'
    assert switch.count('pp-switch__chip') == 2, 'the chips are not shared-switcher chips'
    assert 'data-nc-order="progress"' in switch and 'data-nc-order="name"' in switch
    assert 'is-active' in switch.split('data-nc-order="progress"', 1)[0], 'progress is not the default'


def test_the_progress_sort_restores_the_servers_order_rather_than_recomputing_it():
    """Two definitions of "most progressed" drift. The client only has to put the rows back in the
    order they arrived in, which is the order the heroes were picked from."""
    sort = _partial().split('function applySort', 1)[1].split('function markActive', 1)[0]

    assert "data-nc-i" in sort, 'the progress branch recomputes an order instead of restoring one'
    # `.localeCompare(` -- the CODE. The comment above it names the function too, so the loose
    # form stayed true with the call itself replaced by a `<` comparison.
    assert '.localeCompare(' in sort, 'A-Z compares codepoints, which files accented titles nowhere'
    assert 'createDocumentFragment' in sort, 're-appending 200 rows one at a time is 200 reflows'


def test_the_board_binds_its_own_dropdowns_and_not_this_modals(hunter):
    """THE BUG THAT MADE THE DROPDOWNS LOOK DEAD. The board took its discipline group with a bare
    `document.querySelector('.rp-discs')` -- whichever comes FIRST in the document. This modal is
    included at the top of career.html, so it became the first, and the board bound to it: the
    board's own popovers got no controller, and the modal's got TWO. Two controllers on one root
    means a click opens the popover and the second handler, finding it already open, closes it again
    inside the same click. Nothing ever appears, and nothing errors."""
    from pathlib import Path

    from django.conf import settings

    _live('Anything')
    body = hunter.get('/career/', **CF).content.decode()

    # The precondition, asserted rather than assumed: the modal's group really is the first one.
    first = body.index('rp-discs')
    assert 'nc__filter' in body[first - 60:first], 'fixture wrong: the modal is not the first group'
    assert body.count('rp-discs') > 1, 'fixture wrong: the board renders no group to collide with'

    career = (Path(settings.BASE_DIR) / 'templates' / 'trophies' / 'career.html').read_text(
        encoding='utf-8')
    assert "document.querySelector('.rp-discs')" not in career, (
        'the board is back to taking whichever discipline group comes first in the page'
    )
    assert "advPanel.querySelector('.rp-discs')" in career


# -- what the second audit round found -------------------------------------------------------------

def test_the_filter_is_never_a_scroll_container():
    """IT CLIPPED ITS OWN POPOVERS. `overflow-x: auto` with an unset y-axis computes BOTH axes to
    auto (CSS Overflow 3), which made `.nc__filter` a scroll container -- and the popovers are
    absolutely positioned inside it, so on every phone they were clipped to the height of the chip
    row and never appeared. The same failure the dialog's `overflow: visible` prevents, one level
    down, introduced by the fix for the toolbar's height."""
    css = _elements_css()
    rule = _rule(css, '.nc__filter')

    assert 'overflow' not in rule, 'the filter clips the popovers positioned inside it'
    assert 'flex-wrap: nowrap' not in rule


def test_a_small_phone_drops_the_covers_rather_than_the_buttons():
    """That height has to come from somewhere. With `overflow: visible` on the dialog there is no
    scrollbar, so chrome that exceeds 88vh puts the footer buttons off screen unreachable -- and at
    375x667 the wrapped filter plus a hero row does exceed it."""
    css = _elements_css()

    assert '@media (max-width: 767px) and (max-height: 740px) { .nc__heroes { display: none; } }' in css


def test_a_preview_never_records_the_previewer_as_having_seen_a_wave(hunter):
    """A preview ignores the marker AND the 14-day floor, which makes its stamp the newest
    announcement on the site. The partial was otherwise byte-identical to the real one, so a staff
    member who opened the preview and closed it advanced their own marker to ~now -- losing every
    wave they had not been shown, unrecoverably, because the server refuses to rewind."""
    user = hunter.profile.user
    user.is_staff = True
    user.save(update_fields=['is_staff'])
    _live('Preview Me')

    body = hunter.get('/career/?preview=new-contracts', **CF).content.decode()
    modal = body.split('id="new-contracts"', 1)[1].split('</script>', 1)[0]

    tag = body.split('id="new-contracts"')[1].split('>')[0]
    assert 'data-auto' not in tag, 'the preview arms the dismissal'
    # The CODE, not the comments that name both options a few lines above them.
    assert 'onDismiss: function' not in modal, 'the preview still posts a marker'
    assert "seenKey: 'pp-new-contracts-seen'" not in modal, (
        'the preview can still re-post through the seen-key retry'
    )

    # ...and the real render still does both.
    real = hunter.get('/career/', **CF).content.decode()
    assert 'data-auto' in real.split('id="new-contracts"')[1].split('>')[0]
    assert 'onDismiss: function' in real


def test_a_real_dismissal_records_this_pages_stamp_not_a_parked_one():
    """The parked stamp is for the RETRY. Preferring it unconditionally meant a second tab's genuine
    dismissal was recorded as the first tab's older stamp, then cleared the key so nothing retried --
    and the reader met a wave they had already read."""
    dismiss = _partial().split('onDismiss: function', 1)[1].split('var stamp', 1)[1]

    assert 'opened ? PAGE_STAMP : (stored() || PAGE_STAMP)' in dismiss


def test_the_fast_reject_path_parks_its_stamp_before_rejecting():
    """DetailModal arms its retry on ANY rejection. Rejecting before the .then() that parks the stamp
    left nothing to replay, so the retry posted a later page's stamp -- the fortnight-wide skip this
    whole path exists to prevent."""
    partial = _partial()

    guard = partial.split('if (!(window.PlatPursuit && PlatPursuit.API))', 1)[1].split('}', 1)[0]
    assert 'STAMP_KEY' in guard, 'the retry is armed with nothing parked to replay'


def test_the_stale_device_keys_are_cleared_when_nothing_is_due(hunter):
    """Both keys are only ever cleared from the modal's own partial, which renders while a wave is
    DUE. Dismiss on a phone with a failed POST, dismiss successfully on a laptop, and the phone kept
    both keys forever -- then silently swallowed the NEXT wave's modal through the seen-key branch."""
    body = hunter.get('/career/', **CF).content.decode()

    assert 'id="new-contracts"' not in body, 'fixture wrong: a wave is due'
    assert "removeItem('pp-new-contracts-seen')" in body
    assert "removeItem('pp-new-contracts-stamp')" in body


def test_the_backstop_is_held_per_arm(hunter):
    """`held` was one latch, so the first modal to open cancelled the deadline for every arm still
    pending. On a new-contracts visit the explainer is openable at any moment from the summary card's
    edhint -- opening it disarmed the safety net for a modal it has nothing to do with."""
    _live('Anything')

    body = hunter.get('/career/', **CF).content.decode()

    hold = body.split('window.ppHoldCareerModal = function (name) {', 1)[1].split('};', 1)[0]
    assert 'if (name && !pending[name]) { return; }' in hold
    assert "ppHoldCareerModal('contracts')" in body, 'this modal holds anonymously again'


def test_the_empty_state_stays_in_the_accessibility_tree():
    """A live region that is `hidden` when its content changes is not reliably announced -- so
    toggling the element said nothing, which is the silence it was added to fix. Only its text
    changes now."""
    partial = _partial()

    assert 'empty.textContent' in partial, 'the live region is toggled rather than filled'
    assert 'empty.hidden' not in partial
    assert ':empty { margin: 0; }' in _elements_css(), 'an empty live region still takes up space'
