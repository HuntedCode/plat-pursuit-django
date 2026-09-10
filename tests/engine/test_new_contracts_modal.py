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


def test_the_job_chips_can_never_promise_more_than_the_list_shows(hunter):
    """Facets are built from the rendered rows, not a separate aggregate. Counted independently they
    would include contracts past the cap -- a chip leading to an emptier list than it advertised."""
    job = Job.objects.exclude(is_fallback=True).first()
    for i in range(4):
        _live('For One Job %02d' % i, jobs=[job])

    nc = new_contracts_modal.new_for(hunter.profile, hunter.profile.user)

    chip = next(j for j in nc['jobs'] if j['slug'] == job.slug)
    listed = sum(1 for r in nc['rows'] if job in r.jobs.all())
    assert chip['count'] == listed


def test_a_row_carries_its_job_slugs_for_the_filter(hunter):
    """The filter is client-side off this attribute; without it every chip shows an empty list."""
    job = Job.objects.exclude(is_fallback=True).first()
    _live('Filterable', jobs=[job])

    body = hunter.get('/career/', **CF).content.decode()
    modal = body.split('id="new-contracts"', 1)[1].split('</script>', 1)[0]

    assert 'data-nc-jobs="' in modal
    assert job.slug in modal.split('data-nc-jobs="', 1)[1].split('"', 1)[0]


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
