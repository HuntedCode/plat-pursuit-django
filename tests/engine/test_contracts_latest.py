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


def test_the_latest_count_is_not_shrunk_by_latest_being_on():
    """Every chip counts the catalogue filtered by the OTHER active filters. For the Latest chip
    itself that means Latest is the one filter it must ignore -- otherwise switching it on would
    rewrite its own number to the result count and it could never be switched off knowingly."""
    profile = ProfileFactory(is_linked=True)
    _contract('New One', 'new-one', 870041, live_days_ago=1)
    _contract('New Two', 'new-two', 870042, live_days_ago=3)
    _contract('Old One', 'old-one', 870043, live_days_ago=NEW_CONTRACT_WINDOW_DAYS + 1)

    assert contracts_service.board_facets(profile, platforms=[])['new'] == 2
    assert contracts_service.board_facets(profile, platforms=[], new_only=True)['new'] == 2


def test_the_latest_count_DOES_narrow_with_the_other_filters():
    """The other half of the rule the name above promises: it rides the current view, so a job
    filter narrows it rather than reporting the whole catalogue."""
    profile = ProfileFactory(is_linked=True)
    jobs = list(Job.objects.exclude(is_fallback=True)[:2])
    _contract('New Driver', 'nd', 870044, live_days_ago=1, jobs=[jobs[0]])
    _contract('New Other', 'no', 870045, live_days_ago=1, jobs=[jobs[1]])

    facets = contracts_service.board_facets(profile, platforms=[], jobs=[jobs[0].slug])

    assert facets['new'] == 1


def test_every_OTHER_chip_count_respects_latest():
    """THE bug: Latest on, a handful of contracts in the grid, and the status chips still promising
    hundreds. Clicking one empties the board. That is verbatim the failure _filter_contracts'
    comment records from the removed `contract=` filter, and holding new_only back from
    board_facets reintroduced it."""
    profile = ProfileFactory(is_linked=True)
    jobs = list(Job.objects.exclude(is_fallback=True)[:2])
    _contract('Recent', 'recent', 870046, live_days_ago=1, jobs=[jobs[0]])
    for i in range(4):
        _contract('Ancient %d' % i, 'ancient-%d' % i, 870047 + i,
                  live_days_ago=NEW_CONTRACT_WINDOW_DAYS + 9, jobs=[jobs[0]])

    facets = contracts_service.board_facets(profile, platforms=[], new_only=True)

    assert facets['status']['all'] == 1, 'the status chips still counted the out-of-window contracts'
    assert facets['status']['available'] == 1
    assert facets['job'][jobs[0].slug] == 1, 'the job popover still promised five'
    assert facets['platform']['PS5'] == 0, 'platform chips ignored Latest'


def test_the_facet_counts_agree_with_what_the_board_returns():
    """The invariant behind all of the above, stated once: for any filter combination, the 'all'
    chip and the grid must report the same number. A chip that disagrees with its own board is the
    whole class of bug."""
    profile = ProfileFactory(is_linked=True)
    jobs = list(Job.objects.exclude(is_fallback=True)[:2])
    _contract('Recent', 'recent', 870051, live_days_ago=2, jobs=[jobs[0]])
    _contract('Older', 'older', 870052, live_days_ago=NEW_CONTRACT_WINDOW_DAYS + 4, jobs=[jobs[0]])

    for new_only in (False, True):
        facets = contracts_service.board_facets(profile, platforms=[], new_only=new_only)
        page = _page(profile, new_only=new_only)
        assert facets['status']['all'] == page['total'], f"disagree at new_only={new_only}"


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

    content = client.get('/career/?view=contracts').content.decode()

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

    content = client.get('/career/?view=contracts').content.decode()

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


def test_the_rendered_board_ships_facets_that_respect_latest(client):
    """The service half is pinned above; this is the WIRING. `_board_facets` builds its kwargs from
    a hand-listed key tuple, so new_only can be threaded into the service and still never reach it
    -- and the page would ship chips that disagree with the grid beside them."""
    import json

    profile = ProfileFactory(is_linked=True)
    client.force_login(profile.user)
    jobs = list(Job.objects.exclude(is_fallback=True)[:1])
    _with_member(_contract('Recent', 'recent-w', 870061, live_days_ago=1, jobs=jobs))
    for i in range(3):
        _with_member(_contract('Ancient %d' % i, 'ancient-w-%d' % i, 870062 + i,
                               live_days_ago=NEW_CONTRACT_WINDOW_DAYS + 9, jobs=jobs))

    body = client.get('/career/?view=contracts&new=1').content.decode()
    payload = body.split('id="rp-facets"', 1)[1].split('>', 1)[1].split('</script>', 1)[0]
    facets = json.loads(payload.replace('\u0022', '"'))

    assert facets['status']['all'] == 1, 'the shipped status chips ignore Latest'
    assert facets['new'] == 1


# ── Latest is Board-only, like status ────────────────────────────────────────────────────────────

def test_a_hand_typed_latest_cannot_narrow_history(client):
    """CSS hides the Latest chip in History, so honouring `?scope=history&new=1` -- a hand-edited
    URL, a stale bookmark -- would narrow History to the last 14 days with NO visible control to
    undo it. That is the exact hazard setScope's own comment exists to prevent, arriving by the one
    path setScope never sees: the initial load."""
    profile = ProfileFactory(is_linked=True)
    client.force_login(profile.user)

    params = _board_params_for(client, '/career/?view=contracts&scope=history&new=1')

    assert params['scope'] == 'history'
    assert params['new_only'] is False


def test_the_client_and_the_server_agree_about_it(client):
    """Fixing only seedFromURL would mean the page loads narrowed by the SSR render and silently
    widens on the next fetch. Both sides drop it, so both agree."""
    profile = ProfileFactory(is_linked=True)
    client.force_login(profile.user)

    src = client.get('/career/?view=contracts').content.decode()
    seed = src.split('function seedFromURL', 1)[1].split('state.newOnly', 1)[1].split(';', 1)[0]

    assert "'history'" in seed, 'seedFromURL honours ?new=1 in History; the server does not'


def _board_params_for(client, url):
    """Read what the view derived, through a real request."""
    from django.test import RequestFactory

    from trophies.views.career_views import _board_params

    from urllib.parse import urlparse
    parsed = urlparse(url)
    return _board_params(RequestFactory().get(parsed.path + '?' + parsed.query))
