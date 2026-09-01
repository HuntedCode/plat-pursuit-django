"""The "Latest" contract window: one definition (NEW_CONTRACT_WINDOW_DAYS on `went_live_at`) read
by the board's Latest chip, its facet count, and the card's New marker.

Keyed on went_live_at rather than created_at on purpose: the candidate pipeline stages a contract
long before staff publish it, so created_at would call a weeks-old draft "new" the day it ships.
"""
import pytest
from django.utils import timezone

from tests.factories import ProfileFactory
from trophies.models import Contract, Job
from trophies.services import contracts_service
from trophies.util_modules.constants import NEW_CONTRACT_WINDOW_DAYS

pytestmark = pytest.mark.django_db


def _contract(name, slug, igdb_id, *, live_days_ago=None, jobs=None):
    c = Contract.objects.create(name=name, slug=slug, igdb_id=igdb_id, is_live=True)
    c.jobs.set(jobs or list(Job.objects.exclude(is_fallback=True)[:2]))
    # save() stamps went_live_at to now; move it to place the contract in or out of the window.
    stamp = (timezone.now() - timezone.timedelta(days=live_days_ago)) if live_days_ago is not None else None
    Contract.objects.filter(pk=c.pk).update(went_live_at=stamp)
    c.refresh_from_db()
    return c


def _page(profile, **kw):
    """platforms=[] disables the platform filter. contracts_page defaults it to MODERN_PLATFORMS,
    which requires a member GAME on that platform -- these fixtures are contracts without members,
    so the default would empty every result and the window assertions would pass vacuously."""
    kw.setdefault('platforms', [])
    return contracts_service.contracts_page(profile, **kw)


def test_new_only_keeps_recent_and_drops_old():
    profile = ProfileFactory(is_linked=True)
    _contract('Fresh', 'fresh', 870001, live_days_ago=1)
    _contract('Stale', 'stale', 870002, live_days_ago=NEW_CONTRACT_WINDOW_DAYS + 5)

    names = {c['name'] for c in _page(profile, new_only=True)['contracts']}

    assert names == {'Fresh'}
    assert {c['name'] for c in _page(profile)['contracts']} == {'Fresh', 'Stale'}


def test_launch_contracts_with_no_stamp_are_not_new():
    """THE launch decision: the ~1,000 badge-derived contracts carry went_live_at = NULL, so the
    chip is empty on day one and fills as waves land, rather than calling the whole catalogue new."""
    profile = ProfileFactory(is_linked=True)
    _contract('Launch Set', 'launch-set', 870011, live_days_ago=None)

    assert _page(profile, new_only=True)['contracts'] == []
    card = _page(profile)['contracts'][0]
    assert card['is_new'] is False


def test_the_window_boundary():
    """Just inside the window is new; just outside is not."""
    profile = ProfileFactory(is_linked=True)
    _contract('Inside', 'inside', 870021, live_days_ago=NEW_CONTRACT_WINDOW_DAYS - 1)
    _contract('Outside', 'outside', 870022, live_days_ago=NEW_CONTRACT_WINDOW_DAYS + 1)

    assert {c['name'] for c in _page(profile, new_only=True)['contracts']} == {'Inside'}


def test_card_marker_matches_the_filter():
    """The card's New marker and the chip must never disagree -- they read one cutoff."""
    profile = ProfileFactory(is_linked=True)
    _contract('Marked', 'marked', 870031, live_days_ago=2)
    _contract('Unmarked', 'unmarked', 870032, live_days_ago=NEW_CONTRACT_WINDOW_DAYS + 2)

    by_name = {c['name']: c for c in _page(profile)['contracts']}

    assert by_name['Marked']['is_new'] is True
    assert by_name['Unmarked']['is_new'] is False
    filtered = {c['name'] for c in _page(profile, new_only=True)['contracts']}
    assert filtered == {n for n, c in by_name.items() if c['is_new']}


def test_facet_count_reflects_the_other_filters_not_itself():
    """The Latest chip's count rides the same filtered set as the status chips, so narrowing by
    platform narrows it too -- but turning Latest ON must not shrink its own count."""
    profile = ProfileFactory(is_linked=True)
    _contract('New One', 'new-one', 870041, live_days_ago=1)
    _contract('New Two', 'new-two', 870042, live_days_ago=3)
    _contract('Old One', 'old-one', 870043, live_days_ago=NEW_CONTRACT_WINDOW_DAYS + 1)

    facets = contracts_service.board_facets(profile, platforms=[])

    assert facets['new'] == 2


def test_new_only_composes_with_other_filters():
    """It narrows rather than replacing: Latest + a job filter returns their intersection."""
    profile = ProfileFactory(is_linked=True)
    jobs = list(Job.objects.exclude(is_fallback=True)[:2])
    _contract('New Driver', 'new-driver', 870051, live_days_ago=1, jobs=[jobs[0]])
    _contract('New Other', 'new-other', 870052, live_days_ago=1, jobs=[jobs[1]])
    _contract('Old Driver', 'old-driver', 870053,
              live_days_ago=NEW_CONTRACT_WINDOW_DAYS + 3, jobs=[jobs[0]])

    names = {c['name'] for c in
             _page(profile, new_only=True, jobs=[jobs[0].slug])['contracts']}

    assert names == {'New Driver'}


# ── The board UI wiring (chip is a toggle, marker rides the card) ─────────────────────────────────

def test_the_latest_chip_is_a_toggle_not_a_status(client):
    """Status chips are single-select; Latest must NOT join that group or picking it would clear
    the status. It renders as its own group with aria-pressed, and the card marker ships too."""
    profile = ProfileFactory(is_linked=True)
    client.force_login(profile.user)

    content = client.get('/career/?tab=contracts').content.decode()

    assert 'rp-newfilter' in content, 'the Latest toggle must be its own group'
    assert 'rp-chip--new' in content and 'aria-pressed="false"' in content
    # It must not be inside the single-select status row, i.e. carry no data-filter.
    chip = content.split('rp-chip--new', 1)[1].split('>')[0]
    assert 'data-filter' not in chip, 'Latest must not be a status chip'
    assert 'data-new' in chip


def _with_member(contract, platform='PS5'):
    """Give a contract a real member game so it survives the board's default platform filter.
    Membership is DERIVED: an ANCHORED concept with a TRUSTED match on the contract's igdb_id."""
    from django.utils import timezone as tz

    from tests.factories import ConceptFactory, GameFactory, IGDBMatchFactory

    concept = ConceptFactory(unified_title=contract.name)
    concept.anchor_migration_completed_at = tz.now()
    concept.save(update_fields=['anchor_migration_completed_at'])
    IGDBMatchFactory(concept=concept, igdb_id=contract.igdb_id, status='accepted')
    return GameFactory(concept=concept, title_name=contract.name, title_platform=[platform])


def test_the_new_marker_renders_only_inside_the_window(client):
    """The card marker is what makes recency visible without clicking the chip."""
    profile = ProfileFactory(is_linked=True)
    client.force_login(profile.user)
    fresh = _contract('Brand New', 'brand-new', 870061, live_days_ago=1)
    _with_member(fresh)
    old_c = _contract('Long Standing', 'long-standing', 870062,
                      live_days_ago=NEW_CONTRACT_WINDOW_DAYS + 3)
    _with_member(old_c)

    content = client.get('/career/?tab=contracts').content.decode()

    assert 'Brand New' in content and 'Long Standing' in content, 'both contracts should render'
    assert content.count('rp-tile__new') == 1, 'exactly the in-window contract gets the marker'


# ── The empty state must not promise results Latest still filters out ─────────────────────────────

def test_relaxation_counts_are_measured_with_latest_still_on():
    """The smart-empty panel says 'drop <label> to see N'. N is a PROMISE about what the user will
    see next, so it has to be counted with Latest still applied -- otherwise dropping the suggested
    filter lands them back on an empty board."""
    profile = ProfileFactory(is_linked=True)
    jobs = list(Job.objects.exclude(is_fallback=True)[:2])
    # Old contracts under the OTHER job: visible if you drop the job filter, but never if Latest is on.
    for i, slug in enumerate(('old-a', 'old-b')):
        _contract(slug.title(), slug, 870101 + i,
                  live_days_ago=NEW_CONTRACT_WINDOW_DAYS + 5, jobs=[jobs[1]])

    s = contracts_service.suggest_relaxation(
        profile, jobs=[jobs[0].slug], platforms=[], new_only=True)

    assert s is None, 'nothing to relax: dropping the job still leaves zero NEW contracts'
    loose = contracts_service.suggest_relaxation(profile, jobs=[jobs[0].slug], platforms=[])
    assert loose and loose['count'] == 2, 'without Latest the same drop really would show 2'


def test_latest_itself_is_offered_as_the_relaxation():
    """When Latest is the filter doing the emptying, the panel should offer to drop IT."""
    profile = ProfileFactory(is_linked=True)
    _contract('Only Old', 'only-old', 870111, live_days_ago=NEW_CONTRACT_WINDOW_DAYS + 9)

    s = contracts_service.suggest_relaxation(profile, platforms=[], new_only=True)

    assert s is not None and s['kind'] == 'new' and s['count'] == 1


def test_scroll_appended_cards_get_the_tooltip_window(client):
    """The results partial renders the same card; it needs new_window_days or the marker's tooltip
    reads 'Added in the last  days'."""
    profile = ProfileFactory(is_linked=True)
    client.force_login(profile.user)
    _with_member(_contract('Scrolled', 'scrolled', 870121, live_days_ago=1))

    content = client.get('/career/contracts/results/',
                         HTTP_X_REQUESTED_WITH='XMLHttpRequest').content.decode()

    assert 'rp-tile__new' in content
    assert 'last %d days' % NEW_CONTRACT_WINDOW_DAYS in content


def test_every_board_param_the_url_can_gain_is_also_cleared():
    """syncURL() wipes a FIXED key list before re-appending what buildParams emits. A key written
    but never cleared survives being switched off -- the URL keeps claiming a filter the board has
    dropped, seedFromURL turns it back on next reload, and switching it on again appends a
    duplicate. Read out of the template rather than asserted by hand so a NEW filter added to
    buildParams without a matching delete fails here instead of in someone's shared link."""
    import pathlib
    import re

    src = (pathlib.Path(__file__).resolve().parents[2]
           / 'templates' / 'trophies' / 'career.html').read_text(encoding='utf-8')

    body = src.split('function buildParams', 1)[1].split('function noFilters', 1)[0]
    emitted = set(re.findall(r"p\.(?:set|append)\('([a-z_]+)'", body))
    emitted.discard('page')   # syncURL strips page explicitly

    cleared = set(re.findall(r"'([a-z_]+)'",
                             src.split('function syncURL', 1)[1].split('.forEach', 1)[0]))

    assert emitted <= cleared, f"buildParams emits {emitted - cleared}, which syncURL never clears"
