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


def _live(name, *, days_ago=0, jobs=None):
    c = Contract.objects.create(name=name, slug=name.lower().replace(' ', '-'),
                                igdb_id=abs(hash(name)) % 9_000_000 + 1_000_000, is_live=True)
    c.jobs.set(jobs or list(Job.objects.exclude(is_fallback=True)[:1]))
    Contract.objects.filter(pk=c.pk).update(
        went_live_at=timezone.now() - timezone.timedelta(days=days_ago))
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


def test_a_contract_that_is_not_live_is_never_announced(hunter):
    """Same structural gate the Discord announcer relies on: an unpublished contract has no
    went_live_at, so it cannot reach a reader."""
    c = _live('Staged')
    Contract.objects.filter(pk=c.pk).update(is_live=False, went_live_at=None)

    assert 'id="new-contracts"' not in hunter.get('/career/', **CF).content.decode()


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
    newest = _live('Newest', days_ago=1)

    body = hunter.get('/career/', **CF).content.decode()
    modal = body.split('id="new-contracts"', 1)[1]

    newest.refresh_from_db()
    assert newest.went_live_at.isoformat() in modal.replace('\\u002D', '-'), (
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

    assert 'ppSettleCareerModal' in modal
    assert 'ppHoldCareerModal' in modal, 'the backstop is not cancelled when this modal opens'


def test_the_gate_arms_when_this_modal_is_due(hunter):
    """The half that is easy to miss: every other gate test checks the UNARMED case, and dropping
    this flag from the gate leaves the suite green while every visit ships an unarmed gate."""
    _live('Anything')

    body = hunter.get('/career/', **CF).content.decode()

    assert 'id="new-contracts"' in body, 'fixture wrong: no modal is due'
    gate = body.split('ppAfterCareerModal', 1)[0]
    assert 'var pending = true' in gate, 'a modal is on the page but the gate is not holding'


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

    assert 'rp-disc__trigger' in modal and 'rp-pop__item' in modal
    assert 'var(--disc-%s)' % job.discipline in modal, 'the trigger is not tinted in its discipline'
    assert 'PlatPursuit.discPopovers' in modal, 'the modal opens its own popovers instead'


def test_escape_closes_an_open_popover_without_closing_the_modal():
    """Both discPopovers and DetailModal close on Escape from document in the bubble phase, so one
    press did both: dismissing a dropdown took the whole modal with it. The fix has to be CAPTURE
    phase (it runs before either) and has to be conditional on a popover actually being open, or
    Escape stops closing the modal at all."""
    partial = _partial()

    guard = partial.split("if (e.key !== 'Escape'", 1)[1].split('}, true);', 1)[0]
    assert 'rp-pop:not([hidden])' in guard, 'the guard swallows Escape when no popover is open'
    assert 'stopPropagation' in guard, 'the modal still closes underneath the popover'
    assert 'pops.closeAll' in guard, 'nothing closes the popover the press was meant for'


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


def test_the_dialog_never_clips_because_the_popovers_live_inside_it():
    """The discipline popovers are absolutely positioned inside the dialog, so ANY clipping on it
    cuts them off at its edge -- which is what made them look like they were not opening at all.
    This is the one rule that has to stay `visible` even though the box is height-capped."""
    dialog = _rule(_elements_css(), '.pp-howto .nc__dialog')

    assert 'overflow: visible' in dialog


def test_a_popover_with_no_room_below_it_opens_upward():
    """discPopovers flips at the horizontal viewport edge but always opens downward -- right on a
    page you can scroll, wrong in a dialog, where a popover taller than the room beneath it runs off
    the screen with no way to reach it. The measurement must happen AFTER the shared controller has
    opened it, or the popover is still hidden and has no box to measure."""
    partial = _partial()
    css = _elements_css()

    up = _rule(css, '.nc__toolbar .rp-pop--up')
    assert 'bottom: calc(100% + 6px)' in up and 'top: auto' in up

    wire = partial.split('discPopovers(filterRoot)', 1)[1]
    assert 'rp-pop--up' in wire, 'the flip is wired before the popover exists to measure'
    assert 'window.innerHeight' in wire, 'the flip is not measured against the viewport'


def test_the_heroes_are_two_four_six_by_breakpoint(hunter):
    """The server renders all six and CSS hides what does not fit, so the count follows the screen
    without a second render path. Both hide rules must exist: with only the mobile one a tablet
    shows all six in four columns, which is the overflow this replaced."""
    for i in range(new_contracts_modal.MAX_HEROES + 2):
        _live('Hero %02d' % i)

    body = hunter.get('/career/', **CF).content.decode()
    modal = body.split('id="new-contracts"', 1)[1].split('</script>', 1)[0]
    assert modal.count('class="nc__hero"') == 6, 'the server is not rendering six heroes'

    css = _elements_css()
    heroes = css.split('.nc__heroes {', 1)[1].split('.nc__hero-art', 1)[0]
    assert 'repeat(2, minmax(0, 1fr))' in heroes and 'nth-child(n + 3) { display: none' in heroes
    assert 'repeat(4, minmax(0, 1fr))' in heroes and 'nth-child(n + 5) { display: none' in heroes
    assert 'repeat(6, minmax(0, 1fr))' in heroes


def test_a_cover_can_never_grow_taller_than_the_screen_allows():
    """The grid column sets the width and `aspect-ratio` sets the height from it, so on a short
    viewport six covers were still tall enough to push the list off the bottom."""
    art = _rule(_elements_css(), '.nc__hero-art')

    assert 'max-height: 24vh' in art
    assert 'object-position: top' in _rule(_elements_css(), '.nc__hero-art img'), (
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
