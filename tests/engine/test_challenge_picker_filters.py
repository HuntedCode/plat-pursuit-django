"""The picker's paging and filters (2026-10-10): every list reaches the whole pool, and three filters narrow it.

Owner: "the modal ONLY shows 24 total games and doesn't let you scroll". Every panel was one `PAGE` and the sheet
ended there. What is pinned here:

**Paging reaches everything, once.** Pages are disjoint and together cover the pool, for the offset-paged square
and search panels and for the cursor-paged history panel, whose pages are filtered by a date SQL cannot see.

**The filters are display filters.** "In a badge" and the platforms narrow what is shown; the scarcity hatch
still counts the whole pool, so a toggle can never change which rule a square is under.

**Platforms ride every row**, as the union over the contract's member games, in one query per page.
"""
from datetime import timedelta
from pathlib import Path

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from challenges.models import CHALLENGE_TYPE_AZ, HATCH_THRESHOLD
from challenges.services import challenge_service as svc
from challenges.services import eligibility, picker
from tests.factories import (
    BadgeSeriesFactory,
    ConceptFactory,
    EarnedTrophyFactory,
    GameFactory,
    GroupBadgeFactory,
    IGDBMatchFactory,
    ProfileFactory,
    StageFactory,
    TrophyFactory,
    UserFactory,
)
from trophies.models import Contract, ContractBundle, EarnedContract

pytestmark = pytest.mark.django_db

ROOT = Path(__file__).resolve().parents[2]
JS = (ROOT / 'static' / 'js' / 'challenge-detail.js').read_text(encoding='utf-8')
_SEQ = {'n': 0}


def _hunter(client=None):
    user = UserFactory()
    profile = ProfileFactory(user=user, user_is_premium=True, is_linked=True)
    if client is not None:
        client.force_login(user)
    return profile


def _contract(name, *, platforms=('PS5',)):
    _SEQ['n'] += 1
    contract = Contract.objects.create(name=name, slug='pf-%d' % _SEQ['n'], is_live=True,
                                       igdb_id=840_000 + _SEQ['n'])
    concept = ConceptFactory(anchor_migration_completed_at=timezone.now())
    IGDBMatchFactory(concept=concept, igdb_id=contract.igdb_id, status='accepted')
    GameFactory(concept=concept, title_platform=list(platforms))
    contract.concept = concept   # handed back for the badge tests
    return contract


def _in_live_badge(*concepts, live=True):
    series = BadgeSeriesFactory()
    GroupBadgeFactory(series=series, is_live=live)
    stage = StageFactory(series_slug=series.series_slug)
    stage.concepts.add(*concepts)
    return stage


def _many(prefix, n, **kw):
    return [_contract('%s %03d' % (prefix, i), **kw) for i in range(n)]


def _names(rows):
    return [r['name'] for r in rows]


# ── paging: the square and search panels ─────────────────────────────────────────────────────────

def test_the_square_pages_past_the_first_page_and_reaches_every_game():
    profile = _hunter()
    challenge = svc.start(profile, CHALLENGE_TYPE_AZ)
    pool = _many('Bravo', picker.PAGE + 5)

    first = picker.slot_panel(profile, challenge, 'B')
    second = picker.slot_panel(profile, challenge, 'B', offset=first['offset'] + first['showing'])

    assert first['showing'] == picker.PAGE and first['more'] is True and first['total'] == len(pool)
    assert second['showing'] == 5 and second['more'] is False
    seen = _names(first['rows']) + _names(second['rows'])
    assert sorted(seen) == sorted(c.name for c in pool) and len(set(seen)) == len(seen)


def test_the_catch_up_block_rides_only_the_first_page():
    """It is its own short list; repeating it per page re-runs its trophy read for rows already on screen."""
    profile = _hunter()
    challenge = svc.start(profile, CHALLENGE_TYPE_AZ)
    _many('Bravo', picker.PAGE + 1)
    finished = _contract('Bravo Finished')
    EarnedContract.objects.create(profile=profile, contract=finished, has_platinum=True,
                                  platinum_reached_at=timezone.now())
    profile.user.date_joined = timezone.now() - timedelta(days=400)
    profile.user.save(update_fields=['date_joined'])
    plat = TrophyFactory(game=finished.concept.games.first(), trophy_type='platinum')
    EarnedTrophyFactory(profile=profile, trophy=plat, earned_date_time=timezone.now() - timedelta(days=10))

    first = picker.slot_panel(profile, challenge, 'B')
    later = picker.slot_panel(profile, challenge, 'B', offset=picker.PAGE)

    assert _names(first['catchup']) == ['Bravo Finished'], 'the fixture must put a game in the catch-up block'
    assert later['catchup'] == [] and later['catchup_more'] is False


def test_the_search_panel_pages_too():
    profile = _hunter()
    challenge = svc.start(profile, CHALLENGE_TYPE_AZ)
    _many('Zed Quest', picker.PAGE + 3)

    first = picker.search_panel(profile, challenge, 'Zed Quest')
    second = picker.search_panel(profile, challenge, 'Zed Quest', offset=picker.PAGE)

    assert first['more'] is True and second['more'] is False
    assert not set(_names(first['rows'])) & set(_names(second['rows']))
    assert first['total'] == picker.PAGE + 3


def test_an_offset_is_floored():
    """`?offset=` is untrusted: a negative or junk value is the first page."""
    assert picker._bounded('-5') == 0
    assert picker._bounded('lots') == 0
    assert picker._bounded(None) == 0


def test_the_ceiling_ends_the_list_instead_of_repeating_a_page(monkeypatch):
    """Clamping a deeper request back to the ceiling re-served the same rows with `more: true`, so a scrolling
    sheet appended one page forever. Past the ceiling there is nothing, and the last page says so."""
    monkeypatch.setattr(picker, 'MAX_OFFSET', 30)
    profile = _hunter()
    challenge = svc.start(profile, CHALLENGE_TYPE_AZ)
    _many('Bravo', 40)

    last = picker.slot_panel(profile, challenge, 'B', offset=24)
    beyond = picker.slot_panel(profile, challenge, 'B', offset=10 ** 9)

    assert last['showing'] == 6 and last['more'] is False
    assert beyond['rows'] == [] and beyond['more'] is False


# ── paging: the history panel's cursor ───────────────────────────────────────────────────────────

def _importable(profile, name, *, earned):
    contract = _contract(name)
    plat = TrophyFactory(game=contract.concept.games.first(), trophy_type='platinum')
    EarnedTrophyFactory(profile=profile, trophy=plat, earned_date_time=earned)
    EarnedContract.objects.create(profile=profile, contract=contract, has_platinum=True,
                                  platinum_reached_at=timezone.now())
    return contract


def test_history_pages_by_cursor_and_covers_every_importable_game_once():
    profile = _hunter()
    joined = timezone.now() - timedelta(days=400)
    profile.user.date_joined = joined
    profile.user.save(update_fields=['date_joined'])
    challenge = svc.start(profile, CHALLENGE_TYPE_AZ)
    after = joined + timedelta(days=5)
    made = [_importable(profile, 'Alpha %03d' % i, earned=after) for i in range(picker.PAGE + 4)]

    first = picker.history_panel(profile, challenge)
    second = picker.history_panel(profile, challenge, cursor=first['cursor'])

    by_name = {c.name: c for c in made}
    # A KEYSET: the cursor is the last offer this page used.
    assert first['showing'] == picker.PAGE and first['more'] is True
    assert first['cursor'] == by_name[first['rows'][-1]['name']].pk
    assert second['more'] is False and second['cursor'] is None
    seen = _names(first['rows']) + _names(second['rows'])
    assert sorted(seen) == sorted(c.name for c in made) and len(set(seen)) == len(seen)


def test_a_history_page_that_fills_mid_window_resumes_right_after_its_last_offer():
    """Pre- and post-join games alternate, so the page fills before the window does. The next page starts just
    past the last offer used: nothing skipped, nothing repeated."""
    profile = _hunter()
    joined = timezone.now() - timedelta(days=200)
    profile.user.date_joined = joined
    profile.user.save(update_fields=['date_joined'])
    challenge = svc.start(profile, CHALLENGE_TYPE_AZ)
    good, made = [], {}
    for i in range(picker.PAGE * 2 + 2):
        before = i % 2 == 0
        c = _importable(profile, 'Alpha %03d' % i,
                        earned=joined - timedelta(days=5) if before else joined + timedelta(days=5))
        made[c.name] = c
        if not before:
            good.append(c.name)

    first = picker.history_panel(profile, challenge)
    second = picker.history_panel(profile, challenge, cursor=first['cursor'])

    assert _names(first['rows']) == good[:picker.PAGE]
    assert first['cursor'] == made[good[picker.PAGE - 1]].pk, 'the next page starts just after the 24th offer'
    assert _names(second['rows']) == good[picker.PAGE:]


def test_a_history_window_of_pre_join_games_still_hands_on_a_cursor():
    """The window filled with games finished before joining: nothing to show, but the next window may have some,
    so the cursor moves past this one rather than the panel ending."""
    profile = _hunter()
    joined = timezone.now() - timedelta(days=30)
    profile.user.date_joined = joined
    profile.user.save(update_fields=['date_joined'])
    challenge = svc.start(profile, CHALLENGE_TYPE_AZ)
    early = [_importable(profile, 'Alpha %03d' % i, earned=joined - timedelta(days=100))
             for i in range(picker.HISTORY_SCAN)]
    late = _importable(profile, 'Zulu Late', earned=joined + timedelta(days=2))

    first = picker.history_panel(profile, challenge)
    second = picker.history_panel(profile, challenge, cursor=first['cursor'])

    assert first['rows'] == [] and first['more'] is True and first['cursor'] == early[-1].pk
    assert _names(second['rows']) == [late.name] and second['more'] is False


def test_an_import_mid_list_does_not_make_the_next_page_skip_games():
    """History is the one panel that stays open while it is written to. An import locks its letter, which drops
    that letter's candidates out of the pool; a POSITION cursor then pointed past games that slid back, and the
    next page skipped them. A keyset resumes after the last offer whatever left the pool before it."""
    profile = _hunter()
    joined = timezone.now() - timedelta(days=400)
    profile.user.date_joined = joined
    profile.user.save(update_fields=['date_joined'])
    challenge = svc.start(profile, CHALLENGE_TYPE_AZ)
    after = joined + timedelta(days=5)
    alphas = [_importable(profile, 'Alpha %03d' % i, earned=after) for i in range(picker.PAGE)]
    bravos = [_importable(profile, 'Bravo %03d' % i, earned=after) for i in range(picker.PAGE)]

    first = picker.history_panel(profile, challenge)
    assert _names(first['rows']) == [c.name for c in alphas]
    svc.assign(challenge, profile, 'A', alphas[0], acknowledge_lock=True)   # the import: A is now locked

    second = picker.history_panel(profile, challenge, cursor=first['cursor'])
    assert _names(second['rows']) == [c.name for c in bravos], 'no Bravo may be skipped'


def test_a_cursor_that_is_not_a_contract_starts_over():
    profile = _hunter()
    joined = timezone.now() - timedelta(days=400)
    profile.user.date_joined = joined
    profile.user.save(update_fields=['date_joined'])
    challenge = svc.start(profile, CHALLENGE_TYPE_AZ)
    only = _importable(profile, 'Alpha One', earned=joined + timedelta(days=1))
    for junk in ('nope', 10 ** 9, None):
        assert _names(picker.history_panel(profile, challenge, cursor=junk)['rows']) == [only.name]


# ── the badge filter ─────────────────────────────────────────────────────────────────────────────

def test_the_badge_filter_keeps_only_games_in_a_live_badge():
    profile = _hunter()
    challenge = svc.start(profile, CHALLENGE_TYPE_AZ)
    badged = _contract('Bravo Badged')
    _contract('Bravo Plain')
    retired = _contract('Bravo Retired')
    _in_live_badge(badged.concept)
    _in_live_badge(retired.concept, live=False)

    shown = picker.slot_panel(profile, challenge, 'B', badge_only=True)

    assert _names(shown['rows']) == ['Bravo Badged'] and shown['total'] == 1
    assert picker.slot_panel(profile, challenge, 'B')['total'] == 3, 'off by default'


def test_the_badge_filter_counts_a_game_that_only_rides_a_bundle():
    """An episodic contract's games come only from its bundles; without that half it could never pass."""
    contract = _contract('Bundle Only')
    rider = ConceptFactory()
    bundle = ContractBundle.objects.create(contract=contract, label='Season')
    bundle.concepts.add(rider)
    _in_live_badge(rider)

    assert list(eligibility.in_live_badge(Contract.objects.filter(pk=contract.pk))) == [contract]


def test_the_badge_filter_never_moves_the_hatch():
    """A display filter: the hatch counts the whole pool, so narrowing the view cannot open it."""
    profile = _hunter()
    challenge = svc.start(profile, CHALLENGE_TYPE_AZ)
    _many('Bravo', HATCH_THRESHOLD + 2)
    finished = _contract('Bravo Done')
    EarnedContract.objects.create(profile=profile, contract=finished, has_platinum=True,
                                  platinum_reached_at=timezone.now())
    # THE FINISHED GAME IS IN A BADGE and the open ones are not, so under the filter the pool is empty while
    # the finished game survives it. If the hatch counted the FILTERED pool it would open and offer it here.
    _in_live_badge(finished.concept)

    narrowed = picker.slot_panel(profile, challenge, 'B', badge_only=True)

    assert narrowed['total'] == 0
    assert not eligibility.hatch_is_open(profile, challenge, 'B')
    assert narrowed['catchup'] == [], 'a narrowed view must not be read as thin supply'


# ── the platform filter, and platforms on every row ──────────────────────────────────────────────

def test_the_platform_filter_keeps_games_on_any_chosen_platform():
    profile = _hunter()
    challenge = svc.start(profile, CHALLENGE_TYPE_AZ)
    _contract('Bravo Five', platforms=('PS5',))
    _contract('Bravo Four', platforms=('PS4',))
    _contract('Bravo Three', platforms=('PS3',))

    assert _names(picker.slot_panel(profile, challenge, 'B', platforms=['PS4'])['rows']) == ['Bravo Four']
    assert set(_names(picker.slot_panel(profile, challenge, 'B', platforms=['PS4', 'PS3'])['rows'])) == {
        'Bravo Four', 'Bravo Three'}
    assert picker.slot_panel(profile, challenge, 'B')['total'] == 3, 'no filter by default'


def test_an_unknown_platform_is_dropped_not_obeyed():
    assert picker.clean_platforms(['PS4', 'GAMECUBE', 'PS4', 'PS5']) == ('PS5', 'PS4')
    assert picker.clean_platforms(None) == ()


def test_every_row_carries_its_platforms_in_display_order_with_a_tone():
    profile = _hunter()
    challenge = svc.start(profile, CHALLENGE_TYPE_AZ)
    contract = _contract('Bravo Cross', platforms=('PS4', 'PS5'))
    GameFactory(concept=contract.concept, title_platform=['PSVITA'])

    row = picker.slot_panel(profile, challenge, 'B')['rows'][0]

    assert row['platforms'] == [{'name': 'PS5', 'tone': 'primary'}, {'name': 'PS4', 'tone': 'accent'},
                                {'name': 'PSVita', 'tone': 'secondary'}]   # the filter chip's own label


def test_the_search_and_history_rows_carry_platforms_too():
    profile = _hunter()
    joined = timezone.now() - timedelta(days=400)
    profile.user.date_joined = joined
    profile.user.save(update_fields=['date_joined'])
    challenge = svc.start(profile, CHALLENGE_TYPE_AZ)
    _contract('Quill Search', platforms=('PS4',))
    _importable(profile, 'Alpha Import', earned=joined + timedelta(days=1))

    assert picker.search_panel(profile, challenge, 'Quill')['rows'][0]['platforms'][0]['name'] == 'PS4'
    assert picker.history_panel(profile, challenge)['rows'][0]['platforms'][0]['name'] == 'PS5'


def test_platforms_cost_one_query_per_page_however_many_rows():
    profile = _hunter()
    challenge = svc.start(profile, CHALLENGE_TYPE_AZ)
    _contract('Bravo One')

    def cost():
        with CaptureQueriesContext(connection) as ctx:
            picker.slot_panel(profile, challenge, 'B')
        return len(ctx.captured_queries)

    one = cost()
    _many('Bravo More', 10, platforms=('PS4', 'PS5'))
    assert cost() == one


# ── the doors ────────────────────────────────────────────────────────────────────────────────────

def test_the_square_door_reads_offset_badge_and_platforms(client):
    profile = _hunter(client)
    challenge = svc.start(profile, CHALLENGE_TYPE_AZ)
    _many('Bravo', picker.PAGE + 2, platforms=('PS4',))
    badged = _contract('Bravo Badge', platforms=('PS5',))
    _in_live_badge(badged.concept)
    url = reverse('challenge_slot', args=[challenge.pk, 'B'])

    page2 = client.get(url, {'offset': picker.PAGE}).json()
    assert page2['offset'] == picker.PAGE and page2['more'] is False and len(page2['rows']) == 3
    assert page2['rows'][0]['platforms'][0]['name'] in ('PS4', 'PS5')

    assert [r['name'] for r in client.get(url, {'badge': '1'}).json()['rows']] == ['Bravo Badge']
    assert [r['name'] for r in client.get(url, {'platform': 'PS5'}).json()['rows']] == ['Bravo Badge']
    assert client.get(url, {'offset': 'junk'}).json()['offset'] == 0


def test_the_history_and_search_doors_carry_their_paging(client):
    """Both doors pass paging through, with enough rows that a next page exists: a one-row result or a closed
    history panel would satisfy a weaker check without paging anything."""
    profile = _hunter(client)
    joined = timezone.now() - timedelta(days=400)
    profile.user.date_joined = joined
    profile.user.save(update_fields=['date_joined'])
    challenge = svc.start(profile, CHALLENGE_TYPE_AZ)
    _many('Quill', picker.PAGE + 1)
    for i in range(picker.PAGE + 1):
        _importable(profile, 'Alpha %03d' % i, earned=joined + timedelta(days=1))

    search_url = reverse('challenge_search', args=[challenge.pk])
    search = client.get(search_url, {'q': 'Quill'}).json()
    assert search['more'] is True and search['offset'] == 0 and search['rows'][0]['platforms']
    assert len(client.get(search_url, {'q': 'Quill', 'offset': picker.PAGE}).json()['rows']) == 1

    history_url = reverse('challenge_history', args=[challenge.pk])
    history = client.get(history_url).json()
    assert history['open'] is True and history['more'] is True and isinstance(history['cursor'], int)
    rest = client.get(history_url, {'cursor': history['cursor']}).json()
    assert len(rest['rows']) == 1 and rest['more'] is False


# ── the sheet ────────────────────────────────────────────────────────────────────────────────────

def _detail(client, profile):
    challenge = svc.start(profile, CHALLENGE_TYPE_AZ)
    return client.get(reverse('challenge_detail', args=[challenge.pk])).content.decode()


def test_the_sheet_renders_its_filters_at_their_defaults(client):
    html = _detail(client, _hunter(client))
    filters = html[html.index('data-cpick-filters'):html.index('data-cpick-status')]
    assert '<input type="checkbox" data-cpick-qualify checked>' in filters
    assert '<input type="checkbox" data-cpick-badge>' in filters
    for value, _ in picker.PLATFORM_FILTERS:
        assert '<input type="checkbox" data-cpick-platform value="%s">' % value in filters
    assert 'checked' not in filters.replace('data-cpick-qualify checked', '')


def test_the_catch_up_block_sits_above_the_endless_rows(client):
    """Below them it was reachable only after scrolling every page of the pool."""
    html = _detail(client, _hunter(client))
    assert (html.index('<section class="pp-cpick__catchup"') < html.index('data-cpick-rows aria-describedby')
            < html.index('data-cpick-more hidden'))


def test_the_filters_reset_with_the_sheet_and_ride_every_request():
    reset = JS[JS.index('function reset() {'):]
    reset = reset[:reset.index('\n        }')]
    assert 'els.qualify.checked = true;' in reset and 'els.badge.checked = false;' in reset
    assert 'box.checked = false;' in reset and 'paging = null;' in reset
    assert 'PP.API.request(withFilters(url))' in JS
    assert "PP.API.request(withFilters('/my-challenges/' + challengeId + '/history/?q='" in JS


def test_the_qualify_toggle_keeps_a_term_in_the_square():
    run = JS[JS.index('var run = function () {'):]
    run = run[:run.index('};')]
    assert "if (openKey !== null && els.qualify && els.qualify.checked) { load(openKey, term); return; }" in run
    assert run.index('els.qualify.checked') < run.index('load(null, term);')


def test_an_import_updates_the_history_list_in_place():
    """Reloading put a hunter who imported from page three back on page one, every time."""
    assign = JS[JS.index('function assign(slug, key, confirmed, button, stay) {'):]
    stay = assign[assign.index('if (stay && stillOpen() && !slot.is_complete)'):]
    stay = stay[:stay.index('close(function ()')]
    assert stay.index("dropHistoryKey(key,") < stay.index('loadHistory(')
    assert "els.rows.lastChild.setAttribute('data-cpick-key', row.key);" in JS


def test_show_more_keeps_focus_and_hands_it_on():
    sync = JS[JS.index('function syncMore() {'):]
    sync = sync[:sync.index('\n        }')]
    assert "setAttribute('aria-disabled'" in sync and '.disabled =' not in sync
    more = JS[JS.index('function loadMore(manual) {'):]
    more = more[:more.index('\n        }')]
    assert 'if (target) { target.focus(); }' in more


def test_a_filtered_list_says_it_is_filtered():
    """Empty under a filter is not "no games left": the games are there, hidden."""
    assert "filtering() ? 'No games here match your filters.'" in JS
    assert "panel.total + ' shown with your filters'" in JS


def test_a_new_list_starts_at_the_top():
    load = JS[JS.index('function load(key, query) {'):]
    load = load[:load.index('\n        }')]
    history = JS[JS.index('function loadHistory(query) {'):]
    history = history[:history.index('\n        }')]
    assert 'els.body.scrollTop = 0;' in load and 'els.body.scrollTop = 0;' in history


def test_a_page_lands_only_on_the_panel_that_asked_for_it():
    more = JS[JS.index('function loadMore(manual) {'):]
    more = more[:more.index('\n        }')]
    assert 'if (seq !== requestSeq || paging !== p) { return; }' in more
    assert "root: els.body || null" in JS


def test_the_browse_page_links_my_challenges_and_the_hall_of_fame_does_not(client):
    # A RUN, so the toolbar renders: an empty hub shows its empty state instead, whose own "Start a challenge"
    # is the way to My Challenges there.
    svc.start(_hunter(client), CHALLENGE_TYPE_AZ)
    browse = client.get(reverse('challenges')).content.decode()
    hof = client.get(reverse('challenges_hall_of_fame')).content.decode()
    assert 'href="%s"' % reverse('my_challenges') in browse[browse.index('data-browse-form'):]
    assert 'aria-label="My Challenges"' in browse
    assert 'aria-label="My Challenges"' not in hof


def test_an_anonymous_browse_page_offers_no_my_challenges_button(client):
    svc.start(_hunter(), CHALLENGE_TYPE_AZ)
    assert 'data-browse-form' in client.get(reverse('challenges')).content.decode(), 'the toolbar must render'
    assert 'aria-label="My Challenges"' not in client.get(reverse('challenges')).content.decode()


# ── the job buttons on a phone ────────────────────────────────────────────────────────────────────

CSS = (ROOT / 'static' / 'css' / 'components' / 'challenges.css').read_text(encoding='utf-8')


def _phone_block():
    """The picker's phone-only rule block for the square buttons, comment-free."""
    start = CSS.index('@media (max-width: 767.98px) {\n    .pp-cpick__keys {')
    return CSS[start:CSS.index('\n}\n', start)]


def test_a_phone_lays_the_squares_out_as_a_full_width_two_column_grid():
    """Hunters' screenshots: beside the cover each 44px pill took its own line, so a six-job game stood six tall
    lozenges high. On a phone the squares are a two-column grid that takes the whole row under the cover."""
    block = _phone_block()
    assert 'grid-template-columns: repeat(2, minmax(0, 1fr));' in block
    assert '.pp-cpick__rows--search .pp-cpick__row-main { display: contents; }' in block
    assert '.pp-cpick__rows--search .pp-cpick__row-main > .pp-cpick__keys { grid-column: 1 / -1; }' in block
    # Drawn smaller, still 44px to the finger.
    assert 'min-height: 36px;' in block and "content: ''; position: absolute; inset: -3px 0;" in block


def test_a_square_label_can_ellipsise_on_a_phone():
    """A bare text node in a flex button can only be clipped mid-letter, so the label gets its own span."""
    assert "labelText.className = 'pp-cpick__key-label';" in JS
    assert JS.index("pick.textContent = single ? 'Add this game to '") < JS.index('labelText.appendChild(pick.firstChild);')
    assert '.pp-cpick__key-label { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }' in _phone_block()
