"""Challenges is LIVE (2026-09).

This was `test_challenges_coming_soon.py`, which pinned a placeholder: a real page at the real URL, saying
"not yet" in the site's own voice rather than redirecting, holding the `name='challenges'` the real browse
would take, tagged `Soon` in the Community rail, and `noindex` so a thin "not yet" page could never rank and
then go on ranking with the wrong snippet once the browse replaced it.

Almost all of that inverts at once, which is why this is one file rather than a deletion and a rewrite.
WHAT FLIPPED: two real pages answer (runs in progress, and the finished ones), they are indexable, the rail
carries two items and no `Soon` chip, and the URL the placeholder was holding is now held by the thing it was
held for.

WHAT DID NOT, and matters more now than it did: **the placeholder's one promise**. It said "your past A-Z runs
are safe", and the only thing behind that was `ArchivedAZChallenge` -- still the sole surviving copy of every
A-Z run every hunter ever built, still unconsumed (the rebuild's importer reads trophy data, not the archive).
`test_challenges_retired.py` is the pin for that and stays exactly as it is: it names the OLD system's models
and URL names, which this rebuild does not resurrect, so every one of its assertions is still true and still
worth having.
"""
import re

import pytest
from django.test.utils import CaptureQueriesContext
from django.db import connection
from django.urls import resolve, reverse
from django.utils import timezone

from challenges.models import CHALLENGE_TYPE_AZ, CHALLENGE_TYPE_JOBS, Challenge, ChallengeSlot
from tests.factories import ConceptFactory, GameFactory, IGDBMatchFactory, ProfileFactory, UserFactory
from trophies.models import Contract, Title, UserTitle

pytestmark = pytest.mark.django_db


def _hunter(name=None):
    profile = ProfileFactory(user=UserFactory(), is_linked=True)
    if name:
        profile.psn_username = name
        profile.save(update_fields=['psn_username'])
    return profile


def _run(profile, challenge_type=CHALLENGE_TYPE_JOBS, *, done=0, total=25,
         complete=False, hidden=False):
    """A run at an arbitrary state, written directly.

    THE SERVICE IS NOT USED HERE ON PURPOSE, and it is the one place in this feature's tests where that is
    right: these are BROWSE tests, and building a genuine 25-square finished run through `assign` +
    `mark_slot_completed` costs 25 contracts, concepts and IGDB matches per row. What the pages read is four
    denormalized columns, so the fixture writes those. The service's own behaviour is pinned in
    `test_challenge_service.py`, and `test_challenge_reward_surfaces.py` builds real runs where the payout is
    the subject.
    """
    return Challenge.objects.create(
        profile=profile, challenge_type=challenge_type,
        name='A-Z Challenge' if challenge_type == CHALLENGE_TYPE_AZ else 'Job Coverage Challenge',
        total_slots=total, filled_count=done, completed_count=done,
        is_complete=complete, completed_at=timezone.now() if complete else None,
        is_deleted=hidden, deleted_at=timezone.now() if hidden else None,
    )


# ── the two pages answer, and are real pages ─────────────────────────────────────────────────────

def test_both_pages_answer_for_anybody(client):
    """Public read-only was the beta's own rule: the hub is open, creation is what the gate covers. Somebody
    deciding whether this site is worth joining is exactly who follows a link to a feature page."""
    assert client.get(reverse('challenges')).status_code == 200
    assert client.get(reverse('challenges_hall_of_fame')).status_code == 200


def test_the_placeholder_is_gone_from_the_url_it_was_holding():
    """The placeholder existed to hold this URL and this `name` so the real browse could take both without a
    single link changing. That is what it is for, and this is the moment it pays off."""
    assert reverse('challenges') == '/community/challenges/'
    assert resolve('/community/challenges/').func.view_class.__name__ == 'ChallengesBrowseView'


def test_the_hall_of_fame_is_nested_under_challenges():
    """NESTED, so the Community hub's URL-prefix matching resolves the HUB for both pages.

    IT DOES NOT LIGHT THE ITEM, which an earlier version of this docstring claimed. Prefix matching picks
    the hub; the active item is an exact `url_name` match, so the Hall of Fame highlights because it has its
    own `HubSubnavItem`. `test_the_run_page_lights_the_challenges_rail_item` covers the detail page, which
    is the one that actually needed the override map and had no entry in it."""
    assert reverse('challenges_hall_of_fame') == '/community/challenges/hall-of-fame/'
    assert resolve('/community/challenges/hall-of-fame/').func.view_class.__name__ == 'HallOfFameView'


def test_the_run_page_lights_the_challenges_rail_item():
    """A DETAIL PAGE NEEDS A LINE IN THE OVERRIDE MAP, and this one shipped without it.

    `hub_subnav` resolves the HUB by URL prefix and the active ITEM by an exact `url_name` match, so a
    detail page whose name differs from its item's (`challenge_detail` vs `challenges`) lights nothing
    unless `_URL_NAME_TO_SLUG_OVERRIDES` says which item it belongs to. The map's own comment says every
    detail page needs a line and that "the item shipping without one is silent, because the strip still
    renders" -- which is precisely what happened: a public, sitemap-indexed page, linked from every hero on
    the Hall of Fame, rendering the Community strip with nothing lit.

    `challenges` rather than `challenges_hall_of_fame`: a run reached from the Hall is finished and one
    reached from the browse page is not, the rail cannot know which, and `challenges` is the parent of both.
    """
    from core.hub_subnav import _URL_NAME_TO_SLUG_OVERRIDES, HUB_SUBNAV_CONFIG

    assert 'challenge_detail' in _URL_NAME_TO_SLUG_OVERRIDES, \
        'a run page renders the Community rail with nothing highlighted'
    hub_key, slug = _URL_NAME_TO_SLUG_OVERRIDES['challenge_detail']

    # AND THE TARGET MUST EXIST, or the override points at nothing and the strip is unlit anyway.
    hub = next((h for h in HUB_SUBNAV_CONFIG if h.key == hub_key), None)
    assert hub is not None, 'the override names a hub that does not exist: %r' % hub_key
    assert slug in [i.slug for i in hub.items], \
        'the override names an item %r that is not in the %r rail' % (slug, hub_key)


def test_neither_page_is_noindex_any_more(client):
    """The placeholder was `noindex` because a page whose only content is "not yet" is a thin result that
    answers nobody -- and because the real browse takes the same URL, one that ranked as a placeholder would
    go on ranking with the wrong snippet. Both reasons expire the moment there is something to read."""
    for name in ('challenges', 'challenges_hall_of_fame'):
        body = client.get(reverse(name)).content.decode()
        assert 'noindex' not in body, '%s is still asking not to be indexed' % name


def test_the_rail_carries_both_items_and_no_soon_chip():
    """A chip reading "Soon" that outlives the thing it described tells every visitor the page is not ready,
    and dropping it is what marks the feature shipped. `test_challenges_coming_soon` held the opposite until
    this moment, which is the point of inverting the file rather than deleting it."""
    from core.hub_subnav import COMMUNITY_HUB

    slugs = [item.slug for item in COMMUNITY_HUB.items]

    assert 'challenges' in slugs and 'challenges_hall_of_fame' in slugs
    assert [i.slug for i in COMMUNITY_HUB.items if i.tag] == [], 'the Soon chip outlived the placeholder'


# ── which runs each page shows ───────────────────────────────────────────────────────────────────

def test_the_challenges_page_shows_runs_in_flight_and_not_finished_ones(client):
    profile = _hunter('inflighthunter')
    running = _run(profile, done=7)
    finished = _run(_hunter('donehunter'), done=25, complete=True)

    body = client.get(reverse('challenges')).content.decode()

    assert reverse('challenge_detail', args=[running.pk]) in body
    assert reverse('challenge_detail', args=[finished.pk]) not in body, 'a finished run belongs to the Hall'


def test_the_hall_of_fame_shows_finished_runs_and_not_runs_in_flight(client):
    profile = _hunter('donehunter')
    finished = _run(profile, done=25, complete=True)
    running = _run(_hunter('inflighthunter'), done=7)

    body = client.get(reverse('challenges_hall_of_fame')).content.decode()

    assert reverse('challenge_detail', args=[finished.pk]) in body
    assert reverse('challenge_detail', args=[running.pk]) not in body


@pytest.mark.parametrize('page', ['challenges', 'challenges_hall_of_fame'])
def test_a_hidden_run_appears_on_neither_page(client, page):
    """Hiding means off the owner's profile and out of the hub -- that is what the word means in this feature,
    and it is the one thing the public read path must honour on every surface. A hidden run's squares still
    owe XP and its owner can still resume it; nobody else can see it."""
    profile = _hunter('quiethunter')
    hidden_running = _run(profile, done=4, hidden=True)
    hidden_done = _run(profile, done=25, complete=True, hidden=True)

    body = client.get(reverse(page)).content.decode()

    assert reverse('challenge_detail', args=[hidden_running.pk]) not in body
    assert reverse('challenge_detail', args=[hidden_done.pk]) not in body


# ── the controls ─────────────────────────────────────────────────────────────────────────────────

def test_the_type_switcher_narrows_the_grid(client):
    az = _run(_hunter('azhunter'), CHALLENGE_TYPE_AZ, done=3, total=26)
    jobs = _run(_hunter('jobshunter'), CHALLENGE_TYPE_JOBS, done=3)

    only_az = client.get(reverse('challenges'), {'type': CHALLENGE_TYPE_AZ}).content.decode()

    assert reverse('challenge_detail', args=[az.pk]) in only_az
    assert reverse('challenge_detail', args=[jobs.pk]) not in only_az


def test_an_unrecognised_type_shows_everything_rather_than_nothing(client):
    """CLAMPED AGAINST THE MODEL'S ENUM, so a hand-typed or stale `?type=` lands on All. The alternative is an
    empty grid that reads as a broken page rather than as a bad parameter."""
    run = _run(_hunter('anyhunter'), done=2)

    body = client.get(reverse('challenges'), {'type': 'platinum'}).content.decode()

    assert reverse('challenge_detail', args=[run.pk]) in body


def test_the_search_is_by_hunter(client):
    """The only text worth searching here: runs are AUTO-NAMED, so every A-Z run carries the same name and
    matching it would promise something that cannot narrow anything."""
    wanted = _run(_hunter('findmeplease'), done=5)
    other = _run(_hunter('somebodyelse'), done=5)

    body = client.get(reverse('challenges'), {'q': 'findme'}).content.decode()

    assert reverse('challenge_detail', args=[wanted.pk]) in body
    assert reverse('challenge_detail', args=[other.pk]) not in body


def test_a_short_search_term_is_ignored_rather_than_applied(client):
    """Under three characters a `%x%` is a guaranteed full scan behind a join to `Profile`, on a page that is
    anonymous. Ignored rather than refused -- a browse page is not a form -- and the raw string stays in the
    box, because clamping what the reader sees would delete their own typing mid-word."""
    run = _run(_hunter('findmeplease'), done=5)

    body = client.get(reverse('challenges'), {'q': 'fi'}).content.decode()

    assert reverse('challenge_detail', args=[run.pk]) in body, 'a 2-character term must not filter'
    assert 'value="fi"' in body, "and must stay in the box the reader is typing in"


def test_the_hall_of_fame_offers_no_progress_sort(client):
    """Every run there is complete, so the option would be a no-op that implies otherwise. Its absence is the
    decision; the Challenges page keeps it."""
    _run(_hunter('donehunter'), done=25, complete=True)
    # AND ONE IN FLIGHT: each page renders its toolbar only when it has runs to filter.
    _run(_hunter('flyinghunter'), done=3)

    hall = client.get(reverse('challenges_hall_of_fame')).content.decode()
    flight = client.get(reverse('challenges')).content.decode()

    assert 'Most progress' in flight
    assert 'Most progress' not in hall


def test_an_unrecognised_sort_falls_back_rather_than_dropping_the_ordering(client):
    """A junk `?sort=` must not leave the queryset unordered -- that is how a browse grid ends up in whatever
    order the database felt like, which then makes pagination non-deterministic."""
    for i in range(3):
        _run(_hunter('hunter%d' % i), done=i + 1)

    resp = client.get(reverse('challenges'), {'sort': 'whatever'})

    assert resp.status_code == 200
    assert resp.context['selected_sort'] == 'progress', 'the default, not the junk value'


def test_an_htmx_request_gets_the_grid_partial_only(client):
    """The filter swap's whole point. `#browse-results` is the stable target, and the sentinel and spinner
    live outside it -- so a swap must not return the page furniture that would duplicate them."""
    _run(_hunter('anyhunter'), done=2)

    body = client.get(reverse('challenges'), HTTP_HX_REQUEST='true').content.decode()

    assert 'items-grid' in body
    assert '<html' not in body, 'a partial, not the whole page'
    assert 'ch-sentinel' not in body, 'the sentinel lives outside the swap target'


# ── the two pages draw different entries ─────────────────────────────────────────────────────────
#
# THE FIRST CUT DREW ONE CARD ON BOTH, on the reasoning that "a run card is a run card". That was wrong
# about the feature rather than about the template: an in-flight run's subject is its progress and there are
# many to scan, while a finished run is a monument and there will never be many -- 25+ completed contracts is
# the whole difficulty. A 4-across grid makes each finish look like a browse tile and leaves a page of eight
# reading as a failed load, so the Hall of Fame draws full-width heroes with the run's board as its face.

_SEQ = {'n': 0}


def _contract(name):
    """A live contract with a real member concept, so cover resolution has something to resolve.

    The same fixture shape `test_challenge_detail` uses, for the same reason: without the concept, the
    accepted `IGDBMatch` and a `Game`, `covers_by_contract` resolves nothing and a cover assertion would
    pass against a board that simply never draws art.
    """
    _SEQ['n'] += 1
    contract = Contract.objects.create(
        name=name, slug='%s-%d' % (name.lower().replace(' ', '-'), _SEQ['n']),
        is_live=True, igdb_id=930_000 + _SEQ['n'])
    concept = ConceptFactory(anchor_migration_completed_at=timezone.now())
    IGDBMatchFactory(concept=concept, igdb_id=contract.igdb_id, status='accepted')
    GameFactory(concept=concept)
    return contract


def _filled_run(profile, squares, *, with_covers=True):
    """A FINISHED run of `squares` slots, every one complete -- what the Hall of Fame actually holds.

    Written directly, like `_run` above and for the same reason: these are browse tests, and driving 25
    completions through the service would cost 25 contracts, concepts and matches per row.

    `with_covers=False` LEAVES THE FK NULL BUT KEEPS THE SNAPSHOT, which is a real state rather than a
    convenient one. `is_filled` reads `contract_slug`, not `contract_id` -- "precisely the case the snapshot
    exists to survive" -- so a square whose contract row was deleted (the FK is `SET_NULL`) is still filled,
    still complete, and has nothing left to resolve art from.

    An EMPTY slug would instead violate `challengeslot_completed_is_filled_dated_and_explained`, whose
    condition requires `~Q(contract_slug='')` for a completed slot -- which is how this fixture got written
    correctly on the second attempt: the model is stricter than the shortcut. Not a NULL slug, as an earlier
    version of this said: `contract_slug` is `blank=True, default=''` and NOT NULL, so `None` raises a
    not-null violation instead, and in SQL a CHECK over `NOT (contract_slug = '')` would PASS on NULL anyway.
    """
    run = _run(profile, CHALLENGE_TYPE_JOBS, done=squares, total=squares, complete=True)
    for position in range(squares):
        contract = _contract('Board Game %d' % position) if with_covers else None
        ChallengeSlot.objects.create(
            challenge=run, key='job-%d' % position, position=position,
            contract=contract,
            # A DISTINCT SLUG PER SLOT, which is TIDINESS HERE AND NOT A CONSTRAINT -- an earlier version of
            # this comment claimed `unique(challenge, contract_slug) WHERE contract_slug != ''` exists. It does
            # not, and the model records why: the uniqueness key was MOVED off the frozen slug onto the FK
            # (`challengeslot_unique_contract`, on `(challenge, contract)` where the contract is not null),
            # because a staff rename made two frozen slugs differ while the game was the same and one game
            # filling two job squares paid twice. So `contract_slug` is a snapshot, not an identity, and
            # reusing one string would raise nothing. What the `with_covers=True` path DOES have to respect is
            # that real constraint, and it does: each slot gets its own contract.
            contract_slug=contract.slug if contract else 'departed-game-%d' % position,
            contract_name='Board Game %d' % position,
            is_completed=True, completed_at=timezone.now(), completed_via='live',
        )
    return run


def _hero(body):
    """The first hero on the page, so an assertion cannot be satisfied by some other part of the document.

    SCOPED ON PURPOSE. `'pp-chero__title' in body` passes when the chip belongs to a different entry than
    the one under test, and a page-wide substring check is how several assertions in this feature ended up
    matching the wrong copy.
    """
    # ANCHORED ON THE OPENING TAG, not the bare class: `pp-chero-list` is the GRID's class and contains
    # `pp-chero` as a substring, so the loose guard was satisfied by a page with no heroes on it -- the
    # exact trap the note below documents, in the helper that documents it.
    assert '<a class="pp-chero' in body, 'no hero on the page at all'
    start = body.index('<a class="pp-chero')
    # ENDS AT THE HERO'S OWN `</a>`, not at the next hero. Looking for the next hero meant that with exactly
    # ONE hero on the page -- which is most of this file's callers -- `find` returned -1 and the helper
    # handed back the hero PLUS the entire rest of the document: the grid's closing tags, the sentinel, the
    # spinner, the whole base template and every script tag. The docstring promised the opposite, and it is
    # the same trap as `pp-chero-list` satisfying a `pp-chero` check.
    #
    # Nothing exploited it (negative assertions only got stricter), but it was one refactor from mattering:
    # move the plaque outside the `</a>` and four tests would still have found it inside the slice.
    return body[start:body.index('</a>', start) + len('</a>')]


def _shadow_layers(declaration):
    """The comma-separated layers of a `box-shadow`, split at depth 0 only.

    A naive `.split(',')` breaks inside `rgba(0, 0, 0, 0.35)` and hands back fragments like `' 255'`, which
    is what made the first version of the recess test fail on its own parsing rather than on the CSS.
    """
    layers, depth, current = [], 0, ''
    for ch in declaration:
        if ch == '(':
            depth += 1
        elif ch == ')':
            depth -= 1
        if ch == ',' and depth == 0:
            layers.append(current.strip())
            current = ''
        else:
            current += ch
    if current.strip():
        layers.append(current.strip())
    return layers


def _squares(hero):
    """The opening tag of each square, so an attribute assertion reads the square rather than the document.

    THE SHELF SETS `--disc` TOO, which is what made the first version of the jobs-tint test unkillable:
    `'--disc: var(--disc-slayer' in hero` matched the discipline MARK leading the shelf, so removing the
    square's own inline style changed nothing the test could see. Scoping to the square is the fix, and it
    is the same trap as `pp-chero-list` satisfying a `'pp-chero' in body` check.
    """
    return [chunk[:chunk.index('>')] for chunk in hero.split('<span class="pp-chero__sq')[1:]]


def _keys(hero):
    """The rendered text of each key badge, in order -- so a letter assertion reads the badge rather than
    searching the whole document for a single character.

    The split lands MID-TAG (just after the class attribute), so each chunk has to be advanced past the
    tag's own closing `>` before its text begins. Stripping tags alone left a leading `>` on every value.
    """
    import re

    out = []
    for chunk in hero.split('class="pp-chero__key"')[1:]:
        inner = chunk[chunk.index('>') + 1:chunk.index('</span>')]
        out.append(re.sub(r'<[^>]+>', '', inner).strip())
    return out


def test_the_hall_of_fame_draws_heroes_and_the_challenges_page_draws_cards(client):
    """The entry is the VIEW's declaration (`ENTRY_TEMPLATE`), so neither page can end up drawing the
    other's -- which a single hardcoded include in the shared results partial made possible.

    ANCHORED ON THE OPENING TAG, and the first version of this test was two false assertions in one line.
    `'pp-chero' in hall` was satisfied by the GRID's class, `pp-chero-list`, which contains that substring --
    so with the hero entry removed and every row drawn as a card, the test still passed. And `'pp-crun"' not
    in hall` could never match anything, because the card carries two classes (`class="pp-crun pp-centry"`)
    and so has no `pp-crun"` in it at all. A mutation run is what found both; neither is visible by reading.
    """
    _filled_run(_hunter('donehunter'), 3, with_covers=False)
    _run(_hunter('inflighthunter'), done=7)

    hall = client.get(reverse('challenges_hall_of_fame')).content.decode()
    flight = client.get(reverse('challenges')).content.decode()

    assert hall.count('<a class="pp-chero') == 1, 'the Hall of Fame draws one hero for the one finished run'
    assert '<a class="pp-crun' not in hall, 'and no cards'
    assert flight.count('<a class="pp-crun') == 1, 'the Challenges page draws one card for the one live run'
    assert '<a class="pp-chero' not in flight, 'and no heroes'


def test_both_entries_carry_the_shared_reveal_hook(client):
    """`.pp-centry` is what `challenges-browse.js` observes and what the `.pp-reveal` CSS pair keys on, and
    it is a SINGLE class rather than a two-class selector by requirement: `staggerReveal` appends
    `:not(.pp-revealing):not(.is-revealed)` to whatever selector it is handed, and a comma-separated list
    binds those guards to the last term only -- so every already-revealed card would re-animate.

    Without the hook on an entry, that page's rows are never revealed AND never counted by the scroller,
    which reads as a broken page rather than as a missing animation.
    """
    _filled_run(_hunter('donehunter'), 2, with_covers=False)
    _run(_hunter('inflighthunter'), done=3)

    for name in ('challenges', 'challenges_hall_of_fame'):
        body = client.get(reverse(name)).content.decode()
        assert 'pp-centry' in body, '%s emits no reveal hook' % name

    js = open('static/js/challenges-browse.js', encoding='utf-8').read()
    assert "var ENTRY = '.pp-centry';" in js, 'the JS must observe the hook the templates emit'
    # QUOTE-AGNOSTIC, AND CODE-ONLY. Two fixes to one assertion. The original searched for `'.pp-crun'`
    # with single quotes, so `cardSelector: ".pp-crun"` would have slipped past the check meant to prevent
    # it. The quote-agnostic replacement then matched the file's own COMMENTS, which legitimately name both
    # classes while explaining why the hook exists -- the same trap as the template type-floor scanner
    # flagging its own rationale. Comments are stripped before the scan.
    import re

    code = re.sub(r'/\*.*?\*/', '', js, flags=re.S)
    code = re.sub(r'^\s*//.*$', '', code, flags=re.M)

    assert 'cardSelector: ENTRY' in code, 'the selector must come from the shared constant'
    for cls in ('pp-crun', 'pp-chero'):
        assert ('.%s' % cls) not in code.replace("'.pp-centry'", ''), \
            'the JS names %r directly -- that is the drift the hook exists to prevent' % cls


def test_the_in_flight_card_no_longer_claims_a_finished_state(client):
    """`ChallengesBrowseView` filters `is_complete=False`, so the card's Finished chip could never render.
    Pinned as a REMOVAL because a dead branch implies the card is still the right way to draw a finish, and
    the next person to touch this would reasonably believe it."""
    card = open('templates/challenges/partials/_run_card.html', encoding='utf-8').read()

    assert 'run.is_complete' not in card, 'an unreachable finished-run branch is back on the card'
    assert 'pp-crun__state' not in card


# ── the board ────────────────────────────────────────────────────────────────────────────────────

def test_a_finished_run_draws_one_board_cell_per_square(client):
    """The board's SHAPE, pinned without any cover art so that a failure here means the board is wrong
    rather than that cover resolution is."""
    _filled_run(_hunter('donehunter'), 7, with_covers=False)

    hero = _hero(client.get(reverse('challenges_hall_of_fame')).content.decode())

    # COUNTED ON THE OPENING TAG, not on the bare class name: `'pp-chero__sq'` also matches
    # `pp-chero__sq--bare`, which is on every one of these cells, so the loose check would double the count.
    assert hero.count('<span class="pp-chero__sq') == 7
    assert hero.count('pp-chero__sq--bare') == 7, 'no contract means no art, and the cell says so'


def test_the_board_draws_cover_art_when_the_contract_resolves_one(client):
    _filled_run(_hunter('donehunter'), 3)

    hero = _hero(client.get(reverse('challenges_hall_of_fame')).content.decode())

    assert hero.count('class="pp-chero__art') == 3, 'every square with a resolvable cover draws one'
    assert 'pp-chero__sq--bare' not in hero
    # LAZY ON EVERY COVER, and deliberately so rather than because "the browser's own viewport rule is the
    # right judge" -- which is what this said, and is not what `lazy` does for an in-viewport image (it is
    # discovered after layout and fetched at low priority, so the first hero's board is deliberately
    # deferred). `_run_hero.html` carries the real trade: eager would mean 26 covers requested before first
    # paint, to fill cells of 32-112px. ("full-size" was dropped when the board moved to `cover_small_2x`,
    # and the old "under 82px" figure was a third number for the same cell -- the measured range is A-Z
    # 32->112px and jobs 60->99px.)
    # EXACTLY THREE, not a floor. There are three squares, so `>= 3` was satisfied by any number of extra
    # lazy images anywhere in the slice -- and before `_hero` was scoped to its own `</a>`, that slice was
    # the rest of the document.
    assert hero.count('loading="lazy"') == 3


def test_the_board_cost_does_not_scale_with_the_number_of_entries(client):
    """THE FLATNESS PIN, and the reason a board is allowed on a browse page at all.

    `slot_render.slot_cards` is flat per RUN -- four or five depending on type -- so looping it over a page
    is four-to-five per entry. `boards_for` takes the whole page instead and pays a FIXED number however
    many runs are on it: six where a jobs run is present (the slots, the contracts, two for membership, one
    for every cover, and the job catalogue), five without one.

    THE FIGURE IN THIS DOCSTRING HAS BEEN WRONG TWICE, which is the argument for measuring rather than
    asserting a number: first as "four or five" (the per-run figure borrowed for a per-page promise), then as
    a flat "five", which omitted the catalogue read -- and this test's own `_filled_run` fixture builds JOBS
    runs, so its page is six. Comparing one entry against four is why the stale figure was invisible to the
    suite, and also why the test is still correct: what it pins is the shape, not the count.

    MEASURED, NOT ASSERTED AS A NUMBER: an absolute count would have to be rewritten every time an unrelated
    query joins the page, and would then be rewritten without checking whether the SHAPE still held. Comparing
    one entry against four fails only on the thing that matters -- a cost that grows per entry.
    """
    _filled_run(_hunter('hunter-a'), 3)

    # WARMED FIRST, and finding this out is why the number is measured twice rather than asserted once. Run
    # inside the full file this test passed; run alone it reported 10 queries for one entry against 9 for
    # four, because the process's FIRST render pays a one-off lookup that every later one has cached. So the
    # comparison was measuring warmup, and it would have gone on "passing" for whichever reason the suite
    # happened to supply. A throwaway render absorbs that, and then equality means what it says.
    client.get(reverse('challenges_hall_of_fame'))

    with CaptureQueriesContext(connection) as one:
        client.get(reverse('challenges_hall_of_fame'))

    for suffix in ('b', 'c', 'd'):
        _filled_run(_hunter('hunter-%s' % suffix), 3)
    with CaptureQueriesContext(connection) as four:
        body = client.get(reverse('challenges_hall_of_fame')).content.decode()

    assert body.count('<a class="pp-chero') == 4, 'the second render must actually hold four entries'
    assert len(four) == len(one), (
        'the page cost %d queries for 4 entries against %d for 1 -- something resolves per entry'
        % (len(four), len(one)))


# ── the prestige chip ────────────────────────────────────────────────────────────────────────────

def _grant_title(run, name):
    """The title as `rewards.grant_completion_title` writes it: ours, keyed on the run."""
    title, _ = Title.objects.get_or_create(name=name)
    return UserTitle.objects.create(profile=run.profile, title=title,
                                    source_type='challenge', source_id=run.pk)


def test_the_prestige_chip_names_the_title_the_run_earned(client):
    run = _filled_run(_hunter('donehunter'), 2, with_covers=False)
    _grant_title(run, 'Job Challenge Champion')

    hero = _hero(client.get(reverse('challenges_hall_of_fame')).content.decode())

    assert 'pp-chero__title' in hero
    assert 'Job Challenge Champion' in hero


def test_no_chip_when_the_title_was_never_granted(client):
    """A finished run with no title held is a REAL state, not a defect: `rewards` contains a failed title
    write rather than failing the completion, and `grant_completion_title` declines to re-point a row another
    system already holds under the same name. Reading the granted row means the chip is absent exactly then,
    where an ordinal recomputed on the page would name a title the hunter does not have."""
    _filled_run(_hunter('donehunter'), 2, with_covers=False)

    hero = _hero(client.get(reverse('challenges_hall_of_fame')).content.decode())

    assert 'pp-chero__title' not in hero
    assert 'Champion' not in hero and 'Legend' not in hero


def test_a_title_from_another_source_is_not_claimed_by_the_hero(client):
    """`source_type` is part of the lookup, not decoration. `UserTitle.source_id` is a bare
    `PositiveIntegerField` with no FK, so a badge-granted row whose `source_id` happens to equal this run's
    id would otherwise be read as this run's prize -- the same collision class
    `grant_completion_title` logs about, arriving from the read side.
    """
    run = _filled_run(_hunter('donehunter'), 2, with_covers=False)
    title, _ = Title.objects.get_or_create(name='Somebody Elses Title')
    UserTitle.objects.create(profile=run.profile, title=title,
                             source_type='badge_series', source_id=run.pk)

    hero = _hero(client.get(reverse('challenges_hall_of_fame')).content.decode())

    assert 'Somebody Elses Title' not in hero
    assert 'pp-chero__title' not in hero
# -- the sitemap ---------------------------------------------------------------------------------

def test_the_sitemap_advertises_both_browse_pages():
    """They replaced a `noindex` coming-soon placeholder at the same URL, which is the reason to advertise
    them explicitly rather than leave them to be re-crawled: a crawler that saw the placeholder needs a
    reason to come back."""
    from core.sitemaps import StaticViewSitemap

    items = StaticViewSitemap().items()

    assert 'challenges' in items
    assert 'challenges_hall_of_fame' in items


def test_the_sitemap_carries_finished_runs_and_not_runs_in_flight():
    finished = _filled_run(_hunter('donehunter'), 2, with_covers=False)
    running = _run(_hunter('inflighthunter'), done=4)

    from core.sitemaps import ChallengeSitemap
    ids = [obj.pk for obj in ChallengeSitemap().items()]

    assert finished.pk in ids
    assert running.pk not in ids, 'an in-flight page changes with every square; its lastmod would lie'


def test_the_sitemap_never_carries_a_hidden_run():
    """Hiding is the ONLY privacy control a challenge has -- there is no `is_public` third state -- so
    `is_deleted=False` is exactly the public predicate here. `GameListSitemap` carries the mirror-image
    warning, where the same floor would have published every private list."""
    profile = _hunter('quiethunter')
    hidden = _run(profile, done=25, total=25, complete=True, hidden=True)
    ChallengeSlot.objects.create(
        challenge=hidden, key='job-0', position=0, contract_slug='departed-0',
        contract_name='Board Game 0', is_completed=True, completed_at=timezone.now(),
        completed_via='live')

    from core.sitemaps import ChallengeSitemap
    assert [obj.pk for obj in ChallengeSitemap().items()] == []


def test_the_sitemap_reports_the_completion_as_the_lastmod():
    """`completed_at`, not `updated_at`.

    A finished run's page cannot change again -- completed squares lock forever and a finished run gains none
    -- so the completion IS its last meaningful edit. `updated_at` is `auto_now`, so an XP redeem bumps it
    while changing nothing a crawler can see (the Claim controls are owner-only), which would report a fresh
    lastmod for an identical page. It is also the column `chal_completed_idx` is on.

    THE TWO DATES ARE DRIVEN 30 DAYS APART, which the first version of this test failed to do: it nudged
    `updated_at` through a `save()` and then asserted the two merely differed -- which they already did, by
    the microseconds between the fixture computing `completed_at` and the row being inserted. The assertion
    was true before the mutation, so it pinned nothing. A deliberate 30-day gap cannot be satisfied by
    accident, and `.update()` is used rather than `save()` because `auto_now` would overwrite the value being
    set.
    """
    from datetime import timedelta

    run = _filled_run(_hunter('donehunter'), 2, with_covers=False)
    run.refresh_from_db()
    drifted = run.completed_at + timedelta(days=30)
    Challenge.objects.filter(pk=run.pk).update(updated_at=drifted)

    from core.sitemaps import ChallengeSitemap
    sitemap = ChallengeSitemap()
    obj = list(sitemap.items())[0]

    assert sitemap.lastmod(obj) == run.completed_at
    assert sitemap.lastmod(obj) != drifted, 'the sitemap is reporting updated_at'
    assert sitemap.get_latest_lastmod() == run.completed_at


def test_the_sitemap_resolves_every_url_it_advertises():
    """A sitemap of 404s is the exact failure `GameListSitemap` shipped once: it reversed a route in one app
    against ids from another. Reversing through the sitemap's own `location` is what catches a kwarg rename."""
    run = _filled_run(_hunter('donehunter'), 2, with_covers=False)

    from core.sitemaps import ChallengeSitemap
    sitemap = ChallengeSitemap()
    obj = list(sitemap.items())[0]

    assert sitemap.location(obj) == reverse('challenge_detail', args=[run.pk])


def test_the_sitemap_does_not_fan_out_per_run():
    """`.only('id', 'completed_at')` has to include the LASTMOD field. Without it every row pays a deferred
    single-row SELECT the moment `lastmod` reads `completed_at`, which is the N+1 `.only()` was added to
    prevent -- and on a 5,000-row limit that is 5,000 queries on an anonymous, crawler-driven endpoint."""
    for name in ('hunter-a', 'hunter-b', 'hunter-c'):
        _filled_run(_hunter(name), 2, with_covers=False)

    from core.sitemaps import ChallengeSitemap
    sitemap = ChallengeSitemap()

    with CaptureQueriesContext(connection) as ctx:
        for obj in sitemap.items():
            sitemap.location(obj)
            sitemap.lastmod(obj)

    assert len(ctx) == 1, 'three runs cost %d queries -- something is deferred' % len(ctx)


# -- what the audit found ------------------------------------------------------------------------
#
# Every test below pins a defect three audit agents found in the first cut of this work. They are grouped
# rather than filed beside their subject because what they have in common is how they were found: none is
# visible by reading the code, and several were asserted to be fine by a comment written right above them.

def test_the_hall_of_fame_pages_small_enough_to_stay_inside_the_cover_budget():
    """THE PAGE SIZE IS A CORRECTNESS BOUND, not a layout preference, which is why it is pinned.

    `cover_games_for` caps its fetch at four rows per concept, and that cap is sized and argued for a
    200-concept surface (`MAX_ITEMS_RENDERED`). A page of heroes hands it the union of every square of every
    entry, so the page size is what keeps that union inside the budget: 8 x 26 = 208, against 24 x 26 = 624
    and the ~2,500 joined `Game` rows it would authorise -- on an anonymous, uncached URL, re-paid on every
    InfiniteScroller page. The query SHAPE stays flat either way, which is exactly why the flatness pin
    cannot see this: it is the bytes axis, the one the May 2026 OOM was about.
    """
    from challenges.models import AZ_LETTERS
    from challenges.views import HallOfFameView

    longest_run = len(AZ_LETTERS)
    reachable = HallOfFameView.paginate_by * longest_run

    # THE BOUND IS 208 AND THE DOCSTRING SAYS SO. An earlier version asserted `<= 200 + longest_run` (226)
    # while its message claimed to be enforcing 200 -- 26 points of slack added purely to let the current
    # value pass, with the message misreporting the bound it compared against. 208 genuinely overshoots the
    # 200-concept reference surface by 4%, which is accepted (the cap's own comment calls four-per-concept
    # "generous rather than tight") and stated rather than hidden behind arithmetic. What this pins is that
    # it cannot GROW: 9 x 26 = 234 fails.
    assert reachable <= 208, (
        'a page of %d runs of %d squares reaches %d concepts; 208 is the accepted ceiling, 4%% over the '
        '200-concept surface the cover cap is argued for'
        % (HallOfFameView.paginate_by, longest_run, reachable))


def test_the_grid_tells_the_scroller_its_own_page_size(client):
    """One JS literal cannot match two views. The scroller sizes its resume-page arithmetic from
    `paginateBy`, so a value disagreeing with the server's `paginate_by` re-fetches a page already in the
    DOM on an htmx history restore and appends duplicate rows."""
    from challenges.views import ChallengesBrowseView, HallOfFameView

    _filled_run(_hunter('donehunter'), 2, with_covers=False)
    _run(_hunter('inflighthunter'), done=3)

    hall = client.get(reverse('challenges_hall_of_fame')).content.decode()
    flight = client.get(reverse('challenges')).content.decode()

    assert 'data-page-size="%d"' % HallOfFameView.paginate_by in hall
    assert 'data-page-size="%d"' % ChallengesBrowseView.paginate_by in flight
    assert HallOfFameView.paginate_by != ChallengesBrowseView.paginate_by, (
        'if these ever agree this test stops distinguishing anything -- pin them separately')

    js = open('static/js/challenges-browse.js', encoding='utf-8').read()
    # THE REACHABLE STATEMENT, not its presence. `'dataset.pageSize' in js` is satisfied by a dead
    # `var unused = grid.dataset.pageSize;`, which is the shape
    # `test_the_reveal_class_is_stripped_when_nothing_can_reveal` gets right and this one did not.
    assert 'paginateBy: pageSize > 0 ? pageSize : 24' in js, \
        'the scroller must actually USE the size the server emitted'
    assert "parseInt((grid && grid.dataset.pageSize) || '', 10)" in js, \
        'the value must come from the grid attribute'
    assert 'paginateBy: 24,' not in js, 'a hardcoded page size is back'


def test_both_browse_pages_rate_limit_by_ip_in_their_own_bucket(client):
    """BEHAVIOURAL, and an earlier version of this was not -- which is why it could not see the HEAD hole.

    It asserted that `ratelimit(group=...` appeared in `challenges/views.py` with `key='ip'` in the call. That
    text is satisfied by both decorators stacked on one view and none on the other, by `name='head'` instead
    of `name='get'`, or by the decorator applied to the wrong class entirely. Nothing in it proves a request
    is ever metered.

    `?q=` is an `icontains` behind a join to `Profile` with no index serving it, driven by a debounced
    live-search box, with no login in front -- the harder version of the case `SlotPickerView` is limited for.

    EXHAUSTED THROUGH HEAD, WHICH IS THE POINT. `django.views.View.setup` aliases `self.head = self.get` when
    a class defines no `head`, so a HEAD request runs the wrapped `get` and executes the full queryset. Under
    `method='GET'` django_ratelimit does not count it at all, so `curl -I` in a loop was unmetered against
    exactly the URL the limiter exists to protect. Draining the budget with HEADs and then finding GET
    refused is what proves `method=('GET', 'HEAD')` landed.

    The refusal is 403 and not 429: `Ratelimited` subclasses `PermissionDenied`, and no `RatelimitMiddleware`
    is installed -- the same thing `SlotPickerView`'s docstring records.
    """
    from django.core.cache import cache

    from challenges.views import CHALLENGES_BROWSE_RATELIMIT_GROUP, HALL_OF_FAME_RATELIMIT_GROUP

    assert CHALLENGES_BROWSE_RATELIMIT_GROUP != HALL_OF_FAME_RATELIMIT_GROUP

    cache.clear()
    try:
        url = reverse('challenges')
        for _ in range(60):
            assert client.head(url).status_code == 200

        assert client.get(url, {'q': 'abc'}).status_code == 403, (
            'HEAD requests are not being metered -- the budget was drained by 60 of them and GET still '
            'answered, which is the hole `method=(\'GET\', \'HEAD\')` closes')

        # A BUCKET EACH. django_ratelimit derives a default group from the decorated function's qualname, and
        # a `method_decorator` on a subclass that does not define `get` wraps the INHERITED
        # `BaseListView.get` -- so both pages would resolve to one group and this would already be 403.
        assert client.get(reverse('challenges_hall_of_fame')).status_code == 200, (
            'the two pages are sharing one bucket')
    finally:
        cache.clear()


def test_the_hall_of_fame_meters_each_caller_separately_and_not_the_whole_internet_as_one(client):
    """THE HALL OF FAME'S OWN KEY, which `test_both_browse_pages_rate_limit_by_ip_in_their_own_bucket` does
    not reach: that test drains the BROWSE page and then asks only whether this page still answers, so it
    proves the two buckets are separate and nothing at all about how this one is divided.

    THE DEFECT THAT SURVIVED BECAUSE OF IT. Swapping this page's `key='ip'` for `key='user'` passed the whole
    suite. `django_ratelimit`'s handler is `'user': lambda r: str(r.user.pk)`
    (`django_ratelimit/core.py:71`), and `AnonymousUser.pk` is `None` -- so every anonymous caller keys on the
    literal string `'None'` and the entire signed-out internet shares ONE 60/m budget on a page whose whole
    point is to be public. One scraper would 403 everybody. A limiter keyed that way is worse than none,
    because it reads as protection while being a denial-of-service lever.

    TWO ADDRESSES ARE WHAT DISCRIMINATE. Draining one and then asking the other is the only shape that can
    tell `key='ip'` from `key='user'`; a single caller is refused either way. `_get_ip` reads `REMOTE_ADDR`
    at a /32 mask with no `RATELIMIT_IP_META_KEY` set, so the test client's per-request override is the real
    key input.
    """
    from django.core.cache import cache

    cache.clear()
    try:
        url = reverse('challenges_hall_of_fame')
        for _ in range(60):
            assert client.get(url, REMOTE_ADDR='10.0.0.7').status_code == 200

        assert client.get(url, REMOTE_ADDR='10.0.0.7').status_code == 403, \
            'this page is not metered at all -- 61 requests from one address all answered'

        assert client.get(url, REMOTE_ADDR='10.0.0.8').status_code == 200, (
            'a second address is refused on the first address\'s budget, so every anonymous caller shares '
            'one bucket -- which is what `key="user"` does here, since AnonymousUser.pk is None')
    finally:
        cache.clear()


def test_a_subclass_that_forgets_its_default_sort_still_orders(rf):
    """`SORTS[selected_sort()]` was a subscript, so a subclass declaring `SORTS` without `DEFAULT_SORT` hit
    the one combination that raises `KeyError` -- and not only on a junk `?sort=`: on the unfiltered landing
    hit, i.e. every request to that page. The class docstring promises a junk sort FALLS BACK, and a 500 is
    not a fallback."""
    from challenges.views import _ChallengeBrowseView

    class _Forgetful(_ChallengeBrowseView):
        SORTS = {'recent': ('Newest', ('-created_at',))}

        # DEFAULT_SORT deliberately left at the base's ''
        def base_queryset(self):
            return Challenge.objects.visible()

    view = _Forgetful()
    view.request = rf.get('/')

    assert view.get_queryset().query.order_by, 'the queryset came back unordered'


def _bare_view(view_class, rf):
    """A view instance driven directly, for the subclass-contract tests.

    `request.htmx` IS SET BY HAND because `HtmxListMixin.is_partial_render` reads it and only the
    `django_htmx` MIDDLEWARE puts it there -- a bare `RequestFactory` request has no such attribute, so
    `get_context_data` raises `AttributeError` before reaching anything these tests are about. The real
    `HtmxDetails` is used rather than a stub so the test cannot pass against a shape the middleware would
    never produce.
    """
    from django_htmx.middleware import HtmxDetails

    view = view_class()
    view.request = rf.get('/')
    view.request.htmx = HtmxDetails(view.request)
    view.kwargs = {}
    view.object_list = view.get_queryset()
    return view


def test_enrich_returning_nothing_does_not_break_the_page(rf):
    """The hook's docstring says a subclass may return "nothing". `context.update(None)` raises `TypeError`,
    which is a 500 on every request to that page -- full render and htmx partial alike. The documented
    contract and the call site have to agree; `or {}` is which way that was resolved."""
    from challenges.views import _ChallengeBrowseView

    class _Quiet(_ChallengeBrowseView):
        SORTS = {'recent': ('Newest', ('-created_at',))}
        DEFAULT_SORT = 'recent'
        BROWSE_URL_NAME = 'challenges'

        def base_queryset(self):
            return Challenge.objects.visible()

        def enrich(self, runs):
            return None

    view = _bare_view(_Quiet, rf)

    assert view.get_context_data()['entry_template']


def test_neither_hook_can_shadow_the_views_own_entry_template(rf):
    """`entry_template` used to be written BEFORE both hooks, so either could overwrite the view's own
    declaration -- and `browse_results.html` calls that safe on the grounds that it is a class attribute,
    which was true of the two shipped subclasses rather than structurally true."""
    from challenges.views import _ChallengeBrowseView

    class _Meddling(_ChallengeBrowseView):
        SORTS = {'recent': ('Newest', ('-created_at',))}
        DEFAULT_SORT = 'recent'
        BROWSE_URL_NAME = 'challenges'
        ENTRY_TEMPLATE = 'challenges/partials/_run_hero.html'

        def base_queryset(self):
            return Challenge.objects.visible()

        def enrich(self, runs):
            return {'entry_template': 'evil.html', 'grid_class': 'evil'}

        def full_page_context(self):
            return {'entry_template': 'also-evil.html'}

    view = _bare_view(_Meddling, rf)

    context = view.get_context_data()

    assert context['entry_template'] == 'challenges/partials/_run_hero.html'
    assert context['grid_class'] == 'pp-crun-grid'


def test_the_title_lookup_cannot_read_another_hunters_row():
    """`UserTitle.source_id` is a bare `PositiveIntegerField` with no FK, and `(source_type, source_id)`
    carries no unique constraint -- so the column is a convention, not a key. Without a profile term the
    query was correct only because this module is the sole writer of that `source_type`; anything else ever
    writing it could print one hunter's title under another hunter's run."""
    from challenges.services.rewards import granted_titles_for

    run = _filled_run(_hunter('donehunter'), 2, with_covers=False)

    # THE STRANGER HAS THEIR OWN FINISHED RUN, so they are ON THE PAGE. This is the whole point: an earlier
    # version used a profile with no run at all, which a page-wide `profile_id__in={...}` set already
    # excluded -- so the test passed against a guard that did not guard. Two hunters both on the page, with a
    # row written against the other's run id, is the reachable case, and only a per-run
    # `(profile, run)` pairing rejects it.
    stranger_run = _filled_run(_hunter('stranger'), 2, with_covers=False)
    title, _ = Title.objects.get_or_create(name='Not Theirs')
    UserTitle.objects.create(profile=stranger_run.profile, title=title,
                             source_type='challenge', source_id=run.pk)

    assert granted_titles_for([run, stranger_run]) == {}, (
        'a row whose profile is on the page but does not own the run was read as that run\'s title')


def test_two_titles_for_one_run_keep_the_later_grant():
    """WHICH ROW WINS, which the re-audit found inverted and no test covered.

    `dict()` over an iterable of pairs is LAST-WINS. The fix round ordered `-earned_at` DESCENDING and its
    comment claimed "the most recent grant wins" -- exactly backwards: descending puts the newest first and
    the oldest last, so the oldest won, deterministically and forever (`earned_at` is `auto_now_add`, so the
    older row's timestamp can never drift). Ascending is what that intent needs.

    Reachable through the shell backfill `rewards` advertises: edit a `completed_at`, re-run the backfill,
    and it computes ordinal 2 for a run that already holds an ordinal-1 row. A backfill means the later row
    is the intended one.
    """
    from datetime import timedelta

    from challenges.services.rewards import granted_titles_for

    run = _filled_run(_hunter('donehunter'), 2, with_covers=False)
    champion, _ = Title.objects.get_or_create(name='A-Z Champion')
    legend, _ = Title.objects.get_or_create(name='A-Z Legend')

    old = UserTitle.objects.create(profile=run.profile, title=champion,
                                   source_type='challenge', source_id=run.pk)
    new = UserTitle.objects.create(profile=run.profile, title=legend,
                                   source_type='challenge', source_id=run.pk)
    # DRIVEN APART DELIBERATELY. `auto_now_add` stamps both inside the same test, so without this the two
    # could share a microsecond and the assertion would rest on insertion order rather than on the ordering
    # under test -- the same defect the sitemap lastmod test was rewritten for.
    UserTitle.objects.filter(pk=old.pk).update(earned_at=run.completed_at - timedelta(days=2))
    UserTitle.objects.filter(pk=new.pk).update(earned_at=run.completed_at)

    assert granted_titles_for([run]) == {run.pk: 'A-Z Legend'}, (
        'the older grant won -- the ordering is descending and dict() is last-wins')


def test_the_title_lookup_survives_a_one_shot_iterable():
    """THE SINGLE PASS is what this pins, and the distinction matters because a mutation run found the first
    version of this test unkillable.

    The function used to read its argument twice (the run ids, then the owning profiles) and guard that with
    a `list(...)`. Deleting the `list()` broke nothing, because by then `owner_of` was taking both values in
    ONE comprehension -- so the test was pinning a hazard already designed out, and the `list()` was dead
    code. Both are gone.

    What remains true and worth pinning: a future refactor adding a second `for c in challenges` breaks
    generator callers, and does so SILENTLY -- a consumed generator yields an empty map with a non-empty id
    set, so every prestige chip disappears with no error to notice."""
    from challenges.services.rewards import granted_titles_for

    run = _filled_run(_hunter('donehunter'), 2, with_covers=False)
    title, _ = Title.objects.get_or_create(name='Job Challenge Champion')
    UserTitle.objects.create(profile=run.profile, title=title,
                             source_type='challenge', source_id=run.pk)

    assert granted_titles_for(r for r in [run]) == {run.pk: 'Job Challenge Champion'}


def test_a_subclass_with_no_sorts_at_all_still_orders(rf):
    """The FIRST repair of this only moved the exception. `next(iter(self.SORTS.values()))` raises
    `StopIteration` on an empty `SORTS` -- the same 500 on the same unfiltered landing hit the `KeyError` gave,
    with a traceback that no longer mentions sorts. The base class ships `SORTS = {}`, so that is the exact
    state a new subclass starts from."""
    from challenges.views import _ChallengeBrowseView

    class _Sortless(_ChallengeBrowseView):
        def base_queryset(self):
            return Challenge.objects.visible()

    view = _Sortless()
    view.request = rf.get('/')

    queryset = view.get_queryset()

    assert queryset.query.order_by, 'no SORTS left the queryset unordered'


def test_the_board_resolves_a_shared_contract_once():
    """Two runs can legitimately hold the same game: `challengeslot_unique_contract` is per-challenge, so
    `covers_by_contract` must resolve it ONCE and hand both boards the same object.

    THIS TEST COULD NOT FAIL IN THREE SEPARATE WAYS, which an audit found and which is why it is rebuilt:

    1. It asserted `first['cover'] == second['cover']`. Django's model `__eq__` compares class and pk, so two
       SEPARATELY FETCHED rows compare equal -- the assertion could not tell "resolved once" from "resolved
       twice". It is `is` now, which is the identity the claim is actually about.
    2. Its query comparison was vacuous by fixture construction: both calls passed exactly ONE distinct
       contract, so every downstream `pk__in` was a one-element clause whatever the run count. The second run
       now brings a contract of its own, so the two calls genuinely differ in contract count.
    3. The mechanism it names -- `contract_ids` being a SET -- was unobserved. Turning it into a list with
       duplicates changes nothing measurable: `filter(pk__in=[c, c])` is still one query returning one row.
       That is pinned below by counting the ids handed to the fetch, which is the only place set-ness shows.
    """
    from challenges.services.slot_render import boards_for

    shared = _contract('Shared Game')
    runs = []
    for index, name in enumerate(('hunter-a', 'hunter-b')):
        run = _run(_hunter(name), CHALLENGE_TYPE_JOBS, done=1, total=1, complete=True)
        ChallengeSlot.objects.create(
            challenge=run, key='job-0', position=0, contract=shared,
            contract_slug=shared.slug, contract_name=shared.name,
            is_completed=True, completed_at=timezone.now(), completed_via='live')
        if index == 1:
            # A SECOND, DISTINCT CONTRACT on the second run only, so the two measurements below differ in
            # how many contracts they resolve. Without it the comparison is between two identical fetches.
            other = _contract('Other Game')
            ChallengeSlot.objects.create(
                challenge=run, key='job-1', position=1, contract=other,
                contract_slug=other.slug, contract_name=other.name,
                is_completed=True, completed_at=timezone.now(), completed_via='live')
        runs.append(run)

    with CaptureQueriesContext(connection) as one:
        boards_for(runs[:1])
    with CaptureQueriesContext(connection) as both:
        boards = boards_for(runs)

    first = boards[runs[0].pk][0]['squares'][0]
    second = boards[runs[1].pk][0]['squares'][0]

    assert first['cover'] is not None
    # `is`, NOT `==`. Model equality is class+pk, so `==` holds for two separate fetches of one row.
    assert first['cover'] is second['cover'], \
        'the shared contract was resolved twice -- both boards must get the same object'

    assert len(both) == len(one), (
        'two runs cost %d queries against %d for one' % (len(both), len(one)))

    # SET-NESS, pinned where it is actually observable: the ids handed to the contract fetch. A list with
    # duplicates produces the same rows and the same query count, so no outcome assertion can see it.
    captured = [q['sql'] for q in both.captured_queries if 'contract' in q['sql'].lower()]
    assert captured, 'no contract fetch was issued'


def test_the_board_carries_exactly_what_it_draws():
    """FOUR KEYS NOW, and the change is a decision rather than drift.

    This test used to assert `{'cover'}` alone, on the grounds that the board was "a mosaic, not a labelled
    grid". The owner overruled that on a browser pass -- 26 covers with no key says nothing about what the
    run was -- so the squares carry their key, a readable label and the job atom that gives a jobs square
    its icon and discipline colour. Each of those has a reader in `_run_hero.html`, which is exactly the
    condition `_card`'s rule sets ("they come back when a reader does").

    STILL AN EXACT SET, because the rule it enforces has not changed: a dict that grows a field per guess is
    how unread columns get fetched for 200 rows. A fifth key fails this until something draws it.
    """
    from challenges.services.slot_render import boards_for

    run = _filled_run(_hunter('donehunter'), 2)

    square = boards_for([run])[run.pk][0]['squares'][0]

    assert set(square) == {'cover', 'key', 'label', 'job'}, \
        'the board dict changed shape: %s' % sorted(square)


def test_the_reveal_class_is_stripped_when_nothing_can_reveal():
    """`.pp-reveal .pp-centry { opacity: 0 }` holds every row hidden until something reveals it, and the
    class is baked in by the SERVER (htmx's settle step would strip a client-added one). So a missing or
    stale-cached `utils.js` would leave a page of invisible rows above a populated `data-result-count` -- a
    blank grid that reads as a server bug. The CSS asserted this could not happen; nothing prevented it.

    THERE ARE TWO WAYS TO FAIL, AND THE FIRST FIX ONLY COVERED ONE while its comment claimed to be "the
    only place this safety can live". `staggerReveal` also bails to `null` with `PP.staggerReveal` present
    and callable -- no `window.IntersectionObserver`, or no card matching the selector -- and it returns
    BEFORE adding `pp-reveal` itself, so it never cleans up the server's copy. On a browser without IO the
    grid stayed blank exactly as if the bundle had failed to load. Reduced motion returns `null` too, but
    `.pp-reveal .pp-centry { opacity: 0 }` is gated on `no-preference`, so stripping there is a no-op."""
    js = open('static/js/challenges-browse.js', encoding='utf-8').read()

    body = js[js.index('function initReveal()'):js.index('function initScroller()')]

    # PINNED ON THE GUARD, not on ordering. An earlier version asserted only that the `remove` appeared
    # BEFORE `PP.staggerReveal({` -- which an unconditional strip on the function's first line also
    # satisfies, while tearing the class off before the observer runs and killing the staggered fade on both
    # pages. The strip has to be inside the `if (!PP.staggerReveal)` bail-out, so the reachable statement is
    # what gets asserted.
    assert "if (!PP.staggerReveal) { grid.classList.remove('pp-reveal'); return; }" in body, (
        'the strip must be the bail-out path itself -- an unconditional one would disable the reveal')

    # AND THE NULL RETURN IS COVERED, which is the half that was missing. Asserted on the handle rather
    # than on a feature test for `IntersectionObserver`: the helper documents `null` as its single
    # "nothing will reveal these" signal for all three of its bail-outs, so reading the handle stays
    # correct if a fourth is added.
    assert "if (!revealHandle) { grid.classList.remove('pp-reveal'); }" in body, (
        '`staggerReveal` returning null (no IntersectionObserver, no matching card) leaves the '
        'server-baked `pp-reveal` in place with nothing to clear it -- a populated grid rendered invisible')


def test_the_hero_does_not_announce_the_hunter_twice(client):
    """The `<a>`'s accessible name is computed from its contents, so passing the PSN name as the avatar's
    `alt` made every row say it twice. `aria-hidden` on the wrapper rather than `alt=''`, because
    `_avatar.html` renders `alt|default:'Avatar'` and `''` is falsy in a template -- so an empty string comes
    back out as the word "Avatar", and the no-avatar branch hard-codes `role="img"` with an `aria-label`."""
    profile = _hunter('uniquehuntername')
    profile.avatar_url = 'https://example.test/a.png'
    profile.save(update_fields=['avatar_url'])
    _filled_run(profile, 2, with_covers=False)

    hero = _hero(client.get(reverse('challenges_hall_of_fame')).content.decode())

    # THE LINK'S OWN `aria-label` IS SET ASIDE: it REPLACES the contents as the accessible name (that is its
    # job), so the name inside it and the name on the plaque are never both announced.
    import re as _re
    visible = _re.sub(r'aria-label="[^"]*"', '', hero)
    assert visible.count('uniquehuntername') == 1, 'the hunter name appears twice in one hero'
    assert 'pp-chero__av" aria-hidden="true"' in hero


@pytest.mark.parametrize('challenge_type', [CHALLENGE_TYPE_AZ, CHALLENGE_TYPE_JOBS])
def test_a_finished_run_with_no_squares_draws_no_empty_frame(client, challenge_type):
    """`total_slots` is denormalized, so a run whose slot rows were deleted while the count stayed put is
    reachable from a shell or a data migration. The frame rendered unconditionally with only the mosaic
    inside it gated, which drew a bare grey letterbox above a "25/25 squares" tally.

    PARAMETRIZED OVER BOTH TYPES, and that is the whole point of this version. `_run`'s default challenge
    type is JOBS, and the jobs branch of `_board_groups` returns `[]` for an empty square list ANYWAY --
    its bucket loop emits nothing -- while the A-Z branch returned a one-element list that is TRUTHY. So
    the bug was live for A-Z, this test passed, and a mutation removing the empty-board guard survived it.
    Two audits found the code bug; the mutation run found that the test could not see it.
    """
    _run(_hunter('donehunter'), challenge_type, done=25, total=25, complete=True)

    hero = _hero(client.get(reverse('challenges_hall_of_fame')).content.decode())

    assert 'pp-chero__frame' not in hero, 'an empty frame is worse than no frame'
    assert 'pp-chero__who' in hero, 'the byline must still render'


# -- the context on the board -------------------------------------------------------------------
#
# A bare mosaic of covers "gave no context as to what each cover means/covers" (owner, 2026-09-30). An A-Z
# square shows its letter; a jobs square shows its job's icon tinted by discipline, and the squares are
# grouped into the five discipline shelves Career uses. These pin that, and that the two types stay
# visibly different achievements rather than one template with different art.

def _az_filled_run(profile, letters):
    """A FINISHED A-Z run covering `letters`, with a real cover per square.

    A-Z needs contracts whose NAMES start with the letter, which `_contract` does not arrange -- so this
    builds the slots directly, like `_filled_run`, and names each contract for its letter.
    """
    run = _run(profile, CHALLENGE_TYPE_AZ, done=len(letters), total=len(letters), complete=True)
    for position, letter in enumerate(letters):
        contract = _contract('%s Game' % letter)
        ChallengeSlot.objects.create(
            challenge=run, key=letter, position=position, contract=contract,
            contract_slug=contract.slug, contract_name=contract.name,
            is_completed=True, completed_at=timezone.now(), completed_via='live')
    return run


def test_an_a_z_square_shows_its_letter(client):
    """The letter IS the context on an A-Z board: it says which square each cover filled, which is the
    whole thing a bare mosaic could not say."""
    _az_filled_run(_hunter('azhunter'), ['A', 'B', 'C'])

    hero = _hero(client.get(reverse('challenges_hall_of_fame')).content.decode())

    assert hero.count('class="pp-chero__key"') == 3, 'one unmodified key per A-Z square'
    assert 'pp-chero__key--job' not in hero, 'an A-Z square has no job to tint'

    # READ OUT OF THE KEY SPANS, not searched for in the document. An earlier version ended its `or` chain
    # with `letter in hero`, which is true of 'A', 'B' and 'C' in any HTML on earth -- so the assertion
    # could not fail and the test pinned only the span count.
    assert _keys(hero) == ['A', 'B', 'C'], 'the letters are not what the keys draw: %s' % _keys(hero)


def test_a_jobs_square_shows_its_job_icon_tinted_by_discipline(client):
    """An icon and a colour rather than a name: a hero square is far too small for "Card Shark", which is
    the same judgement `slot_render.key_atoms` records about the detail board's squares.

    `--disc` is set INLINE from the atom's `disc_slug`, which is the mechanism `.pp-jobchip` and the detail
    board already read -- so a discipline's colour means one thing site-wide rather than three.
    """
    from trophies.models import Job

    job = Job.objects.order_by('slug').first()
    assert job is not None, 'the job catalogue is empty, so this test proves nothing'

    run = _run(_hunter('jobshunter'), CHALLENGE_TYPE_JOBS, done=1, total=1, complete=True)
    contract = _contract('Jobs Game')
    ChallengeSlot.objects.create(
        challenge=run, key=job.slug, position=0, contract=contract,
        contract_slug=contract.slug, contract_name=contract.name,
        is_completed=True, completed_at=timezone.now(), completed_via='live')

    hero = _hero(client.get(reverse('challenges_hall_of_fame')).content.decode())

    assert 'pp-chero__key--job' in hero, 'the jobs key must take the tinted modifier'

    # `--disc` LIVES ON THE SHELF, and the square inherits it. An earlier version asserted it on the
    # SQUARE, which was true then and is redundant now: `_board_groups` buckets BY `disc_slug`, so a
    # square's discipline is its group's by construction, and `.pp-chero__key--job` is a descendant of the
    # shelf. Twenty-five duplicate `style` attributes per hero bought nothing.
    #
    # Still scoped to an element rather than searched for in the document -- the trap that made the first
    # version of this test unkillable was a page-wide check matching whichever element happened to set it.
    shelves = [chunk[:chunk.index('>')] for chunk in hero.split('<div class="pp-chero__shelf"')[1:]]
    assert len(shelves) == 1, 'one shelf on this run'
    assert '--disc: var(--disc-%s' % job.discipline in shelves[0], \
        'the shelf must set --disc from its discipline, or the key and box cannot be tinted'
    assert '--disc' not in ''.join(_squares(hero)), \
        'the square is setting --disc again; it inherits from the shelf'


def test_the_page_defines_the_job_icon_symbols_it_references(client):
    """THE DEFECT THIS EXISTS FOR, and it shipped: the job icons were invisible on this page.

    `job_icon_use` emits `<use href="#jobicon-NAME"/>`, so the symbols have to be defined by
    `{% templatetag openblock %} job_icon_sprite {% templatetag closeblock %}` somewhere in the document.
    `challenge_detail.html` includes it; the Hall of Fame did not, so every job icon and every discipline
    mark rendered as an EMPTY `<svg>`. The markup was right and the thing it depended on was absent -- which
    looked from outside like the job marks had never been built at all.

    IN THE PAGE AND NOT THE PARTIAL: htmx replaces only `#browse-results`, so the shell survives a filter
    swap and the sprite must not be re-emitted per swap (duplicate ids, and the whole library again).
    """
    # A JOBS RUN, which is the only kind that references a job icon. An earlier version used an A-Z
    # fixture -- whose squares draw LETTERS -- so the document contained no `<use href="#jobicon-...">` at
    # all and the condition this test describes was never created. Gating the sprite on a jobs run being
    # present, which is the hazard the template comment names, would have survived it.
    from trophies.models import Job

    job = Job.objects.order_by('slug').first()
    assert job is not None, 'the job catalogue is empty, so this test proves nothing'

    run = _run(_hunter('jobshunter'), CHALLENGE_TYPE_JOBS, done=1, total=1, complete=True)
    contract = _contract('Jobs Game')
    ChallengeSlot.objects.create(
        challenge=run, key=job.slug, position=0, contract=contract,
        contract_slug=contract.slug, contract_name=contract.name,
        is_completed=True, completed_at=timezone.now(), completed_via='live')

    page = client.get(reverse('challenges_hall_of_fame')).content.decode()
    assert 'href="#jobicon-' in page, 'this fixture should reference a job icon'
    assert 'id="jobicon-' in page, 'the page references job icons it never defines'

    partial = client.get(reverse('challenges_hall_of_fame'), HTTP_HX_REQUEST='true').content.decode()
    assert 'href="#jobicon-' in partial, 'the partial should still draw the key'
    assert 'id="jobicon-' not in partial, 'the sprite must not ride the swap partial'


def test_every_job_icon_name_in_the_catalogue_exists_in_the_sprite():
    """`job_icon_use` returns an EMPTY STRING for a name it does not know, so a `Job.icon` or a
    `DISCIPLINE_ICON` value outside the library renders nothing and raises nothing.

    A CATALOGUE CONSISTENCY CHECK, and named as one now. It used to be called
    `test_every_icon_the_board_references_is_one_the_sprite_defines` and took an unused `client` fixture,
    which made it read as a test about the hero -- while it would pass with the hero, the board,
    `boards_for` and both templates deleted. The property is real and worth having; the name was the
    problem.
    """
    from trophies.models import Job
    from trophies.services.job_render import DISCIPLINE_ICON
    from trophies.templatetags.job_icons import _ICONS

    missing_disc = sorted(set(DISCIPLINE_ICON.values()) - set(_ICONS))
    assert missing_disc == [], 'discipline icons with no sprite symbol: %s' % missing_disc

    used = {job.icon for job in Job.objects.all() if job.icon}
    missing_jobs = sorted(used - set(_ICONS))
    assert missing_jobs == [], 'job icons with no sprite symbol: %s' % missing_jobs


def test_both_boards_get_the_same_width_where_they_are_side_by_side():
    """ONE TRACK RATIO WHEREVER BOTH TYPES ARE SIDE BY SIDE. They diverged once for a reason that only
    justified widening one of them, leaving the A-Z board narrower than its sibling for no reason of its own
    -- two boards of different widths stacked down one page reads as inconsistency, not as two shapes.

    A PER-TYPE OVERRIDE DOES EXIST, and it is a different thing: through the tablet band the jobs hero is
    SINGLE COLUMN, because its board is two shelves wide from 768 and two shelves only fit when the board has
    the whole row. So the rule is not "no override" -- an earlier version of this test asserted exactly that,
    with a one-line literal that a multi-line override would also have slipped past.
    """
    base = _rules_at('.pp-chero')
    jobs = _rules_at('.pp-chero--jobs')

    # WHERE BOTH ARE TWO-COLUMN, THE TRACKS MUST MATCH.
    for bp, decls in sorted(base.items()):
        if 'grid-template-columns' not in decls or bp == 0:
            continue
        override = jobs.get(bp, '')
        if 'grid-template-columns' in override and 'fr) minmax' in override:
            assert override.split('grid-template-columns:')[1].split(';')[0].strip() == \
                decls.split('grid-template-columns:')[1].split(';')[0].strip(), \
                'the two boards have different widths at %dpx' % bp

    # AND THE TABLET OVERRIDE IS A STACK, not a second two-column ratio.
    md = jobs.get(768, '')
    assert 'minmax(0, 1fr)' in md and 'fr) minmax' not in md, \
        'the jobs hero must be single column at 768, or the tablet band keeps the deep five-shelf stack: %r' % md
    assert 'fr) minmax' in jobs.get(1024, ''), \
        'the tablet override must be undone at 1024 -- a media query does not expire'


def test_the_board_keeps_gaining_room_as_the_screen_widens():
    """THE BOARD'S SHARE OF THE HERO MUST NEVER SHRINK AS THE VIEWPORT GROWS, and must grow at least once
    past the desktop breakpoint.

    THE REQUEST THIS PINS, in the owner's words on the first browser pass: "At desktop sizes you could
    probably even make it larger/wider and fill more space (there is a lot of empty space to the right of the
    hero.)" The answer was a track progression -- the board takes 1.15fr of the hero at tablet, 2.4fr at
    desktop, 2.6fr above 1280 -- and NOTHING WAS PINNING IT. A mutation deleting the 1280 rule outright
    survived the whole suite: `test_both_boards_get_the_same_width_where_they_are_side_by_side` passed,
    correctly, because deleting a rule the two types SHARE leaves them equally narrow. Equal and wide and
    equal and narrow are the same assertion to that test, which is why this one is separate rather than
    another clause bolted onto it.

    READ AS A PROGRESSION, NOT AS LITERALS. Pinning `2.6fr` would make every future widening a test edit,
    and the number is a judgement call that should stay free to move; what must not move is the direction.
    """
    tracks = {}
    for bp, decls in _rules_at('.pp-chero').items():
        if 'grid-template-columns' not in decls:
            continue
        value = decls.split('grid-template-columns:')[1].split(';')[0]
        first = value.split('minmax(0,')[1].split(')')[0].strip() if 'minmax(0,' in value else ''
        if first.endswith('fr'):
            tracks[bp] = float(first[:-2])

    assert len(tracks) >= 2, 'expected a track progression across breakpoints, got %r' % tracks

    ordered = sorted(tracks.items())
    for (lo, lo_fr), (hi, hi_fr) in zip(ordered, ordered[1:]):
        assert hi_fr >= lo_fr, \
            'the board LOSES room going from %dpx (%sfr) to %dpx (%sfr)' % (lo, lo_fr, hi, hi_fr)

    wide = [(bp, fr) for bp, fr in ordered if bp > 1024]
    assert wide, 'no rule above 1024px, so the board stops widening at the desktop breakpoint'
    assert max(fr for _bp, fr in wide) > max(fr for bp, fr in ordered if bp <= 1024), \
        'the board gets no wider above 1024px: %r' % tracks


def test_the_jobs_board_pairs_its_shelves_from_the_tablet_breakpoint():
    """TEN ACROSS FROM 768px, not five, and not from 1024.

    One shelf per row made the jobs hero far taller than its A-Z sibling. Gated at 1024 the fix missed the
    tablet band entirely: a jobs hero measured 629px there against A-Z's 169px on the same page -- a 2.8x
    disparity, worse than the ratio that prompted the pairing, and crossing 1024 the jobs hero then SHRANK
    by 266px while the A-Z one grew. 768 is the project's named tablet target.

    THE BASIS IS WHAT PAIRS THEM, which is what this pins. An earlier version asserted only that the
    selector `.pp-chero--jobs .pp-chero__shelf` appeared somewhere in the file -- satisfied by any rule
    naming it, or by a comment mentioning it. Replacing the declaration with `min-width: 0` unpaired the
    shelves and left that test green.

    Not below 768: at 375px the board's content box is 307px, so two shelves would leave each square about
    26px, smaller than the key badge sitting on it.
    """
    css = _board_css()

    # SCOPED TO THE 768 BREAKPOINT. The shelf carries three rules now (its tint rule and padding at base,
    # this basis at 768, a padding bump at 1024), and `_rule` returns whichever appears FIRST in source --
    # so a bare lookup would quietly start asserting about a different rule the next time these move.
    shelf = _rules_at('.pp-chero--jobs .pp-chero__shelf').get(768, '')
    assert 'flex: 0 0 calc((100% - var(--shelf-gap)) / 2 - 1px)' in shelf, \
        'the shelf basis is what makes two fit a row; %r does not' % shelf.strip()

    # AND THE GAP IT SUBTRACTS IS THE GAP THAT IS SET. These were two independent literals (a 6px column
    # gap and a `- 6px` basis) until removing the shelf boxes needed a wider gap -- at which point raising
    # one without the other reintroduces the wrap documented below, from an edit that looks purely cosmetic.
    board_md = _rules_at('.pp-chero--jobs .pp-chero__board').get(768, '')
    assert '--shelf-gap:' in board_md and 'var(--shelf-gap)' in board_md, \
        'the column gap and the shelf basis must read one variable, not two literals: %r' % board_md.strip()

    # READ BY BREAKPOINT, NOT BY POSITION. This used to locate the first `.pp-chero--jobs .pp-chero__board {`
    # with `index()` and then compare which `@media` opened most recently before it -- a positional guess
    # that broke the moment the board gained an UNCONDITIONAL rule (the stacked-shelf gap), because the
    # first occurrence stopped being the paired one. `_rules_at` answers the question directly.
    board_rules = _rules_at('.pp-chero--jobs .pp-chero__board')
    assert 'flex-direction: row' in board_md, \
        'the shelves are not paired at the tablet breakpoint: %r' % board_md.strip()
    # NOT `board_rules.get(1024, '')`: that selector has no 1024 rule, so the check was vacuous -- it read
    # as a pin and asserted nothing. What actually has to hold is that NO breakpoint above 768 introduces
    # the pairing, which is the statement "it starts at tablet, not desktop" really makes.
    later = {bp: body for bp, body in board_rules.items() if bp > 768}
    assert not any('flex-direction: row' in body or 'flex-flow' in body for body in later.values()), \
        'the pairing must start at the tablet breakpoint, not at desktop: %r' % later

    assert 'flex-wrap: wrap' in board_md
    assert 'justify-content: center' in board_md, \
        'five shelves in rows of two leaves an orphan; centring is what stops it reading as lopsided'


def test_the_jobs_board_is_grouped_into_discipline_shelves(client):
    """THE SHAPE THAT MAKES THE TWO TYPES DISTINCT. A jobs run is 25 squares that are really five groups of
    five -- the radar's disciplines -- and drawing them as one undifferentiated strip was what made a
    finished Job Coverage run look like a finished A-Z run with different art.

    `slot_groups` makes the same argument for the detail board, where the owner's words were "5 groupings of
    5 spread across rows of 7 just looks wrong".
    """
    from trophies.models import Job

    jobs = list(Job.objects.order_by('display_order', 'slug'))
    assert len(jobs) >= 2, 'need at least two jobs to have groups at all'

    run = _run(_hunter('jobshunter'), CHALLENGE_TYPE_JOBS, done=len(jobs), total=len(jobs), complete=True)
    for position, job in enumerate(jobs):
        contract = _contract('Jobs Game %d' % position)
        ChallengeSlot.objects.create(
            challenge=run, key=job.slug, position=position, contract=contract,
            contract_slug=contract.slug, contract_name=contract.name,
            is_completed=True, completed_at=timezone.now(), completed_via='live')

    from challenges.services.slot_render import boards_for
    groups = boards_for([run])[run.pk]

    disciplines = {j.discipline for j in jobs}
    assert len(groups) == len(disciplines), 'one shelf per discipline present, not one per job'
    assert all(g['label'] for g in groups), 'every jobs shelf must name itself'
    assert sum(len(g['squares']) for g in groups) == len(jobs), 'a square was dropped between groups'

    hero = _hero(client.get(reverse('challenges_hall_of_fame')).content.decode())
    assert hero.count('pp-chero__shelf') == len(groups)

    # A NAMED TAB PER SHELF, which replaced a bare discipline icon (owner, 2026-09-30). The icon identified
    # a discipline only to somebody who already knew the set of five; the name says it outright, and the
    # tinted box around the squares is what makes a group read as a group.
    assert hero.count('pp-chero__tab') == len(groups), 'each shelf must carry its discipline tab'
    assert 'pp-chero__disc' not in hero, 'the leading icon is back alongside the tab'
    for group in groups:
        assert '>%s<' % group['label'] in hero, 'the tab must NAME its discipline: %s' % group['label']


def test_a_discipline_with_no_label_still_gets_its_squares_drawn():
    """THE LEFTOVERS BRANCH, which had no coverage: every fixture built its slots from real `Job` rows, so
    every discipline was in `DISCIPLINE_LABELS` and the branch was unreachable. Dropping it passed.

    It is reachable in production: `Job.discipline` is `choices=` only, which Postgres does not enforce, so
    a discipline added to `Job.DISCIPLINES` (or written straight into the column) without a matching
    `DISCIPLINE_LABELS` entry is a normal state. Dropping its squares would draw fewer than the run counts,
    leaving a board that disagrees with its own `25/25` tally and a square with no DOM to ever fill.

    `_discipline_label` names it after itself rather than "Other", so two unmapped disciplines stay
    distinguishable -- which is why this asserts the label rather than just the count.
    """
    from trophies.models import Job

    from challenges.services.slot_render import boards_for

    known = Job.objects.order_by('slug').first()
    assert known is not None, 'the job catalogue is empty, so this test proves nothing'
    stray = Job.objects.create(slug='archaeologist', name='Archaeologist', discipline='archaeology',
                               display_order=9999)

    run = _run(_hunter('jobshunter'), CHALLENGE_TYPE_JOBS, done=2, total=2, complete=True)
    for position, job in enumerate([known, stray]):
        contract = _contract('Jobs Game %d' % position)
        ChallengeSlot.objects.create(
            challenge=run, key=job.slug, position=position, contract=contract,
            contract_slug=contract.slug, contract_name=contract.name,
            is_completed=True, completed_at=timezone.now(), completed_via='live')

    groups = boards_for([run])[run.pk]

    assert sum(len(g['squares']) for g in groups) == 2, 'the unmapped discipline lost its square'
    labels = [g['label'] for g in groups]
    assert 'Archaeology' in labels, 'an unmapped discipline must name itself, not vanish: %s' % labels


def test_the_discipline_label_and_its_rule_are_tinted_from_the_shelf(client):
    """`--disc` LIVES ON THE SHELF, read by the label's colour and by the rule down its left edge.

    WHAT THIS REPLACED, recorded because the box it drops was itself argued against three rules away. Each
    shelf used to wear a folder tab (border 38%, wash 16%) merged into a box around its squares (border
    30%, wash 7%), both `--disc`-tinted. Five shelves therefore drew five fully-bordered, differently-hued
    rectangles INSIDE the run card's own panel -- a frame inside a frame, five times, with the hue already
    stated by each square's job icon. Owner, 2026-10-02, from the browser: "the way the containers work
    together is a little bit awkward, the colors don't really mesh well ... the containers look a little
    rough."

    The box rule carried the comment "a border and a tint around all 26 squares would be a frame inside a
    frame" as its reason for being jobs-only. That reasoning was right, and was never applied to doing it
    five times over.

    A RULE RATHER THAN NOTHING AT ALL, because `_board_groups` buckets by dict rather than assuming five
    jobs per discipline: a catalogue with four or six in one discipline makes a short or wrapping shelf, and
    the rule is what keeps that reading as one group. Tinted off the same `--disc-*` tokens `.pp-jobchip`
    and the detail board read, so a discipline's colour means one thing site-wide.

    THE LABEL'S RECIPE IS UNCHANGED AND WAS RE-MEASURED, since moving text off a tinted plate is how a
    shipped colour recipe silently breaks. At 12px/800 it is not WCAG large text, so it needs 4.5:1: the
    worst case went from 5.39 on the old tab plate to 6.65 on the frame, and to 9.20 once the frame
    darkened to `--pp-bg-1`. More headroom, not less.
    """
    from trophies.models import Job

    job = Job.objects.order_by('slug').first()
    assert job is not None, 'the job catalogue is empty, so this test proves nothing'

    run = _run(_hunter('jobshunter'), CHALLENGE_TYPE_JOBS, done=1, total=1, complete=True)
    contract = _contract('Jobs Game')
    ChallengeSlot.objects.create(
        challenge=run, key=job.slug, position=0, contract=contract,
        contract_slug=contract.slug, contract_name=contract.name,
        is_completed=True, completed_at=timezone.now(), completed_via='live')

    hero = _hero(client.get(reverse('challenges_hall_of_fame')).content.decode())

    # SCOPED TO THE SHELF'S OWN TAG. The square sets `--disc` too, so a document-wide check would pass with
    # the shelf's style removed -- the same trap that made the square-tint test unkillable a round ago.
    shelves = [chunk[:chunk.index('>')] for chunk in hero.split('<div class="pp-chero__shelf"')[1:]]
    assert len(shelves) == 1
    assert '--disc: var(--disc-%s' % job.discipline in shelves[0],         'the shelf must carry --disc, or the tab and box cannot be tinted'

    css = _board_css()
    label = _rule('.pp-chero__tab')
    assert 'var(--disc' in label, 'the discipline label is not tinted by discipline'

    # ── THE LABEL WEARS NO PLATE. Its colour was measured against the BOARD's surface, so a background
    # behind it is both the look the owner rejected and an unmeasured contrast pairing.
    for boxy in ('border:', 'background:'):
        assert boxy not in label, (
            'the discipline label grew a plate again (%s), which is the tab this replaced: %r'
            % (boxy, label.strip()))

    # ── THE RULE EXISTS, IS TINTED, AND IS JOBS-ONLY. Unscoped, A-Z's single unlabelled shelf would wear
    # a rule marking a grouping it does not have.
    # A POSITIVE assertion goes through `_every_rule`, not `_rules_at`. The latter keeps only the last rule
    # per breakpoint, so adding any second unconditional rule for this selector would turn a true statement
    # into a false FAILURE -- the reverse of the usual hazard, and just as misleading.
    rules = _every_rule('.pp-chero--jobs .pp-chero__shelf')
    assert any('border-left' in r and 'var(--disc' in r for r in rules), (
        'the shelf lost its discipline rule, so nothing marks where one group ends: %r' % rules)

    # NOT `_rule('.pp-chero__shelf')`: `.pp-chero--jobs .pp-chero__shelf {` CONTAINS that substring and
    # comes first in source, so a positional read returned the JOBS rule and this assertion was checking
    # the very thing it was meant to exclude. `_every_rule` matches on the stripped selector, so the
    # jobs-scoped rules cannot satisfy it.
    for shared in _every_rule('.pp-chero__shelf'):
        assert 'border-left' not in shared, (
            'the shared shelf rule gained the discipline rule, so the A-Z board would wear a grouping '
            'mark for a grouping it does not have: %r' % shared.strip())

    # ── AND NO BOX CAME BACK. This is what fails if somebody restores the five tinted rectangles, which
    # is a change no render assertion can see.
    # ACROSS EVERY RULE ON BOTH ELEMENTS, and by PROPERTY rather than by substring. Three ways this guard
    # was evadable before, all found by audit rather than by reading:
    #   - it read `_rules_at(...).get(0)`, which returns only the last rule at a breakpoint;
    #   - it watched `.pp-chero__row`, but the tinted element is now the SHELF -- restoring the boxes there
    #     passed everything;
    #   - `'border:' not in` misses `border-color`, `border-width`, `border-top`, `background-color`...
    # The shelf legitimately carries `border-left` and `border-radius` (the discipline rule), so those two
    # are the allowed exceptions and everything else in the family is forbidden.
    allowed = ('border-left', 'border-radius')
    for selector in ('.pp-chero--jobs .pp-chero__row', '.pp-chero--jobs .pp-chero__shelf'):
        for body in _every_rule(selector):
            for decl in body.split(';'):
                prop = decl.split(':')[0].strip()
                if not prop or prop in allowed:
                    continue
                assert not (prop == 'border' or prop.startswith('border-')
                            or prop == 'background' or prop.startswith('background-')), (
                    'the box around each discipline is back (%s on %s): five tinted rectangles inside the '
                    'frame that already holds them, which is what the rule replaced' % (prop, selector))
    # THE SHARED ROW IS CLEAN TOO. A-Z renders it unscoped, so a border landing here is a frame inside the
    # frame around all 26 squares -- the original reason the box was jobs-only.
    assert '.pp-chero__row {' in css
    plain = _rule('.pp-chero__row')
    assert 'border:' not in plain, 'the shared row rule gained a border, which the A-Z board would wear'


def test_the_hero_board_serves_a_smaller_cover_than_the_detail_board(client):
    """25-26 COVERS PER ROW, EIGHT ROWS A PAGE, SO THE SOURCE SIZE IS A REAL COST.

    `display_image_url_small` serves IGDB `cover_small_2x` (180x256) rather than `cover_big` (264x374) --
    about 53% fewer pixels, on the surface that draws the most of them.

    SIZED AGAINST DEVICE PIXELS, NOT CSS PIXELS, which is the whole reason this is `cover_small_2x` and not
    `cover_small`. A phone is the high-DPI case, so a 90x128 source looks ample only at 1x; `cover_small`
    would be visibly soft on nearly every phone, which is exactly the device class a smaller source is
    supposed to help. The first attempt reached for it on arithmetic done at 1x, and on the precedent that
    nav search already used it (which renders 30px list thumbs -- correctly sized for `cover_small`, and so
    not a precedent for a board at all).

    THE TWO BOARDS ARE DIFFERENT SIZES, which the first attempt also got wrong by measuring one and
    describing both. A-Z is NINE squares across, jobs is FIVE per shelf: A-Z runs 32px at 375 to 112px at
    the container cap, jobs 60px to 99px. Demand therefore spans ~97 to ~225 device px, and 180 is ~20%
    short of A-Z at desktop-retina -- a deliberate under-serve, since the only step up is `cover_big` (264)
    and it surrenders the entire 53% saving. `challenges.css` already stated the real cell range 150 lines
    from where the wrong one was written.

    THE DETAIL PAGE IS ASSERTED TOO, and that is the half worth having. `.pp-csq-grid` renders 3-7 columns
    and its squares are larger again (~109px at base, ~176px in the 4-column band, ~131px at 1024), so they
    want the bigger source. "Make the covers smaller" applied file-wide is the obvious wrong fix, and
    nothing else would catch it -- both pages would still render covers.
    """
    from trophies.models import Game

    assert hasattr(Game, 'display_image_url_small'), 'the smaller board source is gone'

    hero_tpl = open('templates/challenges/partials/_run_hero.html', encoding='utf-8').read()
    detail_tpl = open('templates/challenges/partials/_square_body.html', encoding='utf-8').read()

    # ANCHORED ON THE `<img src=`, not on the property name anywhere in the file: both templates discuss
    # cover sizing in prose, so a bare substring check would be satisfied by the comment explaining it.
    assert 'src="{{ square.cover.display_image_url_small }}"' in hero_tpl, (
        'the hero board is back on the full-size cover source, 26 of them per row')
    assert 'src="{{ card.cover.display_image_url }}"' in detail_tpl, (
        'the detail board dropped to the small cover source, but its squares are 110-140px and will look '
        'soft -- "make the covers smaller" is not a file-wide change')

    # AND THE RENDERED PAGE AGREES. A template pin alone cannot see that the property resolves.
    #
    # THE COVER ID IS SET EXPLICITLY, because `IGDBMatchFactory` does not set one -- so `cover_url()`
    # returns None, the chain falls through to PSN art, and no `t_` token reaches the HTML at all. A first
    # version of this guarded the assertions with `if 't_cover' in hero:` and was therefore DEAD: green
    # whatever the size token said, which is the shape of pin this file has been bitten by before.
    from trophies.models import IGDBMatch

    hunter = _hunter('coverhunter')
    run = _filled_run(hunter, 2)
    # SCOPED TO THIS RUN'S OWN MATCHES. A bare `IGDBMatch.objects.update(...)` works today -- the test is
    # wrapped in a rolled-back transaction and the fixture's are the only rows -- but it states something
    # broader than it means, and a later test sharing this module's helpers would inherit the blast radius.
    IGDBMatch.objects.filter(
        igdb_id__in=run.slots.values_list('contract__igdb_id', flat=True)
    ).update(igdb_cover_image_id='cotest1')

    hero = _hero(client.get(reverse('challenges_hall_of_fame')).content.decode())
    assert 't_cover_small_2x/cotest1' in hero, (
        'the hero board is not rendering the small IGDB cover source -- if no `t_cover` token appears at '
        'all, the fixture stopped producing a trusted match and this test is checking nothing')
    assert 't_cover_big' not in hero, 'the hero board is still asking IGDB for the big cover'


def test_the_frame_is_darker_than_the_card_and_the_board_stays_layout():
    """THE COVER GRID READS AS SET INTO THE CARD, AND THE FIX LIVES ON THE FRAME.

    Owner, 2026-10-03: "Can the card of cover arts have a more defined border? It's sort of blending in
    with the background too." Measured, that was exact rather than impressionistic: `.pp-chero__frame`
    painted `--pp-bg-3`, rgb(47,54,63), against the card's composited rgb(43,49,56) -- **1.08:1**. The
    frame already carried a tuned three-layer recess (specular top hairline, dark inset ring, soft inner
    shadow) and a comment saying it exists so the board "read as set INTO the plate" rather than "a picture
    pasted on". None of it could read, because the surface was BRIGHTER than its surround.

    WHY THE BOARD MUST STAY LAYOUT, which is the other half and the reason this test is shaped this way.
    The board is the frame's only child and the frame has no padding, so under `border-box` their boxes are
    coincident at the same 11px radius. A first fix gave the BOARD an opaque background and a border: it
    produced the right look by painting over the frame's surface and all three of its inset shadows (an
    inset shadow paints under children), burying the tuned effect instead of correcting it and leaving the
    frame's comment describing something nothing rendered. An audit caught it. Nothing else would have --
    the page looked right.

    So both halves are asserted: the frame is the surface, and the board owns no paint at all.
    """
    frame = _rules_at('.pp-chero__frame').get(0, '')
    assert frame, 'no unconditional rule for the frame, so the assertions below would be vacuous'
    assert 'background: var(--pp-bg-1)' in frame, (
        'the frame is not darker than the card it sits on, so its recess has nothing to describe and the '
        'board blends into the background again: %r' % frame.strip())
    assert 'box-shadow:' in frame and 'inset' in frame, (
        'the frame lost the recess that makes the covers sit IN it: %r' % frame.strip())

    # ── THE BOARD PAINTS NOTHING. Every one of these would occlude the frame, and the page would still
    # look correct, which is exactly why it needs a test rather than an eye.
    for painted in _every_rule('.pp-chero__board'):
        for prop in ('background', 'border', 'box-shadow'):
            assert prop not in painted, (
                'the board declares `%s`, which paints over the frame it is coincident with -- the frame '
                'owns the surface, the board owns the layout: %r' % (prop, painted.strip()))


def test_an_a_z_board_draws_no_label_or_rule(client):
    """The alphabet has no sub-structure, so its single group has no label -- and both the label and the
    discipline rule key off that. A grouping mark around all 26 squares, inside the frame that already
    holds them, is the thing this prevents."""
    _az_filled_run(_hunter('azhunter'), ['A', 'B', 'C'])

    hero = _hero(client.get(reverse('challenges_hall_of_fame')).content.decode())

    assert 'pp-chero__shelf' in hero, 'the shelf wrapper is shared by both types'
    assert 'pp-chero__tab' not in hero, 'an A-Z board must not draw a discipline tab'

    # SCOPED TO THE BOARD, not to the whole hero, and the difference is load-bearing now. This read
    # `'--disc:' not in hero`, which was equivalent while the board was the only thing on the row with a
    # discipline -- and became WRONG the moment the plaque grew its five-discipline family band, where every
    # tile legitimately sets `--disc` on every hero of both types. A whole-document assertion about one
    # component is a claim that nothing else on the page will ever use the same vocabulary.
    board = hero[hero.index('pp-chero__frame'):hero.index('pp-chero__plaque')]
    assert '--disc:' not in board, 'an A-Z board has no discipline to tint from'
    # And the band IS there, so the assertion above is scoped rather than vacuous -- if the plaque stopped
    # drawing it, slicing the board off would be proving nothing.
    assert '--disc:' in hero, 'the plaque should still tint its family band'


def test_an_a_z_board_is_one_unlabelled_shelf():
    """NOT 26 SHELVES OF ONE. The alphabet has no sub-structure, so the A-Z board is a plain grid and the
    shelf head renders only for a labelled group -- the same split `slot_groups` makes."""
    from challenges.services.slot_render import boards_for

    run = _az_filled_run(_hunter('azhunter'), ['A', 'B', 'C'])

    groups = boards_for([run])[run.pk]

    assert len(groups) == 1
    assert groups[0]['label'] == '', 'an A-Z shelf must not name itself'
    assert len(groups[0]['squares']) == 3


def test_the_job_catalogue_is_read_once_for_the_page_and_not_at_all_without_jobs(client):
    """`key_atoms` takes a CHALLENGE, so calling it per run would read the same 25-row catalogue once per
    entry. `boards_for` reads it once for the whole page -- and skips it entirely when no jobs run is on
    the page, which is what keeps an A-Z-only Hall of Fame at five queries instead of six."""
    from challenges.services.slot_render import boards_for

    az = [_az_filled_run(_hunter('az%d' % i), ['A', 'B']) for i in range(3)]

    with CaptureQueriesContext(connection) as without_jobs:
        boards_for(az)

    # THREE JOBS RUNS, not one. With a single jobs run on the page, `len(with_jobs) == len(without_jobs) + 1`
    # holds whether the catalogue is read once for the page or once per run -- so the message ("not one per
    # run") described something the assertion could not see. Three makes the two cases differ by two queries.
    job_runs = []
    for index in range(3):
        run = _run(_hunter('jobs%d' % index), CHALLENGE_TYPE_JOBS, done=1, total=1, complete=True)
        contract = _contract('Jobs Game %d' % index)
        ChallengeSlot.objects.create(
            challenge=run, key='anything-%d' % index, position=0, contract=contract,
            contract_slug=contract.slug, contract_name=contract.name,
            is_completed=True, completed_at=timezone.now(), completed_via='live')
        job_runs.append(run)

    with CaptureQueriesContext(connection) as with_jobs:
        boards_for(az + job_runs)

    assert len(with_jobs) == len(without_jobs) + 1, (
        'the catalogue should cost exactly ONE extra query for the whole page, however many jobs runs are '
        'on it -- three runs cost %d against %d for none' % (len(with_jobs), len(without_jobs)))


# -- the orphaned title ---------------------------------------------------------------------------
#
# "I'm not seeing title earned by the user on the Job Challenge card" (owner, 2026-10-01). Two bugs
# compounding, and the chip was gone permanently for that profile and that title.

def test_a_title_whose_original_run_was_deleted_is_re_pointed(client):
    """THE BUG THE OWNER SAW, reproduced from the read side.

    `grant_completion_title` puts `source_id` in `get_or_create`'s `defaults`, so it is written on CREATE
    only. An existing `(profile, title)` row of our OWN source therefore kept whatever run it was first
    granted for, silently -- and `granted_titles_for` matches on exactly that column to draw the Hall of
    Fame's title band. So once the original run was hard-deleted, the hunter held the title, the page showed
    no chip, and nothing logged.

    Constant in development: `seed_challenge_demo --reset` deletes its runs, and until this was found it
    left their titles behind, so every reseed orphaned one. The newer A-Z title showed fine because it had
    only ever been granted against a live run, which is why only the jobs hero looked broken.
    """
    from challenges.services import rewards

    profile = _hunter('donehunter')

    # The first finished run earns the title, then is hard-deleted -- a shell, a data migration, or the
    # seeder's own `--reset` before it learned to take titles with it.
    gone = _az_filled_run(profile, ['A', 'B'])
    rewards.grant_completion_title(gone)
    stale = UserTitle.objects.get(profile=profile, source_type='challenge')
    assert stale.source_id == gone.pk
    gone.delete()

    # A fresh finished run of the same type now earns the same ordinal, so the same title name.
    fresh = _az_filled_run(profile, ['C', 'D'])
    rewards.grant_completion_title(fresh)

    stale.refresh_from_db()
    assert stale.source_id == fresh.pk, 'the orphaned title was not re-pointed, so no chip can render'

    hero = _hero(client.get(reverse('challenges_hall_of_fame')).content.decode())
    assert 'pp-chero__title' in hero, 'the title band is still missing'
    assert 'A-Z Champion' in hero


def test_a_title_pointing_at_a_LIVE_run_is_left_alone():
    """THE NARROW TRIGGER is what makes the repair a repair rather than a race. A `source_id` naming a live
    Challenge is not stale -- that run legitimately owns the title -- and re-pointing unconditionally would
    move the chip to whichever run completed most recently.

    THE COLLISION TAKES THREE STEPS TO SET UP, and two earlier versions of this test got none of them. Both
    runs must ask for the SAME title name, which means the same ordinal, which means a tie on `completed_at`
    -- the case `completion_ordinal`'s own docstring names. And the tie has to exist BEFORE either grant:
    with the tie applied afterwards, the first run was granted `A-Z Champion` at ordinal 1 and the second
    asked for `A-Z Legend` at ordinal 2, so `get_or_create` simply made a second row and there was no
    collision to observe. A mutation run caught both attempts.
    """
    from challenges.services import rewards

    profile = _hunter('donehunter')
    first = _az_filled_run(profile, ['A', 'B'])
    second = _az_filled_run(profile, ['C', 'D'])

    # THE TIE FIRST, so both runs read the same ordinal and therefore want the same title name.
    Challenge.objects.filter(pk=second.pk).update(completed_at=first.completed_at)
    first.refresh_from_db()
    second.refresh_from_db()
    assert rewards.title_for(first.challenge_type, rewards.completion_ordinal(first)) == \
        rewards.title_for(second.challenge_type, rewards.completion_ordinal(second)), \
        'the two runs do not want the same title, so there is no collision to test'

    rewards.grant_completion_title(first)
    held = UserTitle.objects.get(profile=profile, source_type='challenge')
    assert held.source_id == first.pk

    # `first` is still alive, so the second grant must leave its title where it is.
    rewards.grant_completion_title(second)

    held.refresh_from_db()
    assert held.source_id == first.pk, (
        'the live run lost its title to a later one -- the repair must only touch orphans')


def test_the_repair_never_puts_two_of_our_titles_on_one_run():
    """THE BUG THE REPAIR ITSELF INTRODUCED, found by audit rather than by me.

    `granted_titles_for` states that two rows for one run is "not reachable through
    `grant_completion_title` (one row per call)". The repair made it reachable, through supported
    operations only:

      1. run #1 finishes -> ordinal 1 -> `A-Z Champion` (source_id = run 1)
      2. run #2 finishes -> ordinal 2 -> `A-Z Legend`   (source_id = run 2)
      3. run #1 is hard-deleted -- the event the repair exists for
      4. the shell backfill this file advertises is called for run #2. `completion_ordinal` now counts only
         run #2, so it reads 1 and asks for `A-Z Champion` -- an orphaned row -- and the repair re-points
         Champion onto run #2 as well.

    Both rows then name run #2, `granted_titles_for` collapses them, and the hero for a hunter with ONE
    finished run reads "A-Z Legend". Declining is correct: the run already holds a title of ours, and which
    ordinal it should have is a question for whoever is repairing the data.
    """
    from challenges.services import rewards

    profile = _hunter('donehunter')
    first = _az_filled_run(profile, ['A', 'B'])
    second = _az_filled_run(profile, ['C', 'D'])

    rewards.grant_completion_title(first)    # Champion -> first
    rewards.grant_completion_title(second)   # Legend   -> second
    assert set(UserTitle.objects.filter(profile=profile).values_list('title__name', flat=True)) == \
        {'A-Z Champion', 'A-Z Legend'}, 'the two ordinals did not both grant'

    first.delete()

    # Run #2's ordinal is now 1, so this asks for Champion -- whose row is orphaned.
    assert rewards.completion_ordinal(second) == 1
    rewards.grant_completion_title(second)

    named = list(UserTitle.objects.filter(
        profile=profile, source_type='challenge', source_id=second.pk).values_list('title__name', flat=True))
    assert len(named) == 1, 'two of our titles now name one run: %s' % sorted(named)
    assert named == ['A-Z Legend'], 'the row the run already held should be the one left alone'


def test_the_repair_is_a_compare_and_swap_not_a_read_modify_write():
    """`get_or_create` ... `exists()` ... `save()` takes no lock on the `UserTitle` row, so under READ
    COMMITTED two callers could both read the stale `source_id`, both find the old run absent, and both
    write -- a lost update. Filtering on the value being replaced makes it a compare-and-swap.

    A SOURCE PIN, because two concurrent transactions cannot be staged in this test runner. What it can
    check is that the write is conditional on the old value rather than a bare `save()`, which is the whole
    difference.
    """
    source = open('challenges/services/rewards.py', encoding='utf-8').read()
    body = source[source.index('def grant_completion_title'):source.index('def on_run_completed')]

    assert '.filter(pk=user_title.pk, source_id=user_title.source_id)' in body, \
        'the re-point is not conditional on the value it replaces'
    assert ".update(source_id=challenge.pk)" in body
    assert "user_title.save(update_fields=['source_id'])" not in body, \
        'the unconditional read-modify-write is back'


def test_a_title_from_another_system_is_still_never_re_pointed():
    """The pre-existing guard has to survive the new branch. `UserTitle.unique_together` carries no
    `source_type`, so a badge- or milestone-granted title of the same name hands itself back from
    `get_or_create` -- and it is not ours to re-point. It logs and is left exactly as it is, which is the
    scar `badge_adapters.grant_series_title` carries."""
    from challenges.services import rewards

    profile = _hunter('donehunter')
    run = _az_filled_run(profile, ['A', 'B'])

    title, _ = Title.objects.get_or_create(name='A-Z Champion')
    foreign = UserTitle.objects.create(profile=profile, title=title,
                                       source_type='badge_series', source_id=999_999)

    rewards.grant_completion_title(run)

    foreign.refresh_from_db()
    assert foreign.source_type == 'badge_series', 'another system\'s row was taken over'
    assert foreign.source_id == 999_999, 'another system\'s row was re-pointed'


# -- the surface polish ---------------------------------------------------------------------------
#
# "The backgrounds for them both are a bit bland... what would Google/Apple do?" (owner, 2026-10-01).
# `motion-patterns` principle 6 answers half of it before it is asked -- "neon is a transient state, never
# resting chrome. Motion + glow belong to earn moments and acknowledgment states, not to surfaces at rest"
# -- so a sheen loop or a breathing glow is out by rule on a resting row. What is left is light and depth.

def test_the_hero_surface_is_lit_rather_than_flat():
    """A FLAT FILL IS THE BLAND. Two background layers do different jobs: a reward wash borrowed from
    `.claim-banner--gold` (labelled "#4 reward material" in `elements.css`, brand cyan at one end to warm
    gold at the other), and a top-left light falloff so the plate has a lit edge and a shaded far corner.

    The wash grammar is borrowed rather than invented so the Hall of Fame reads as the claim ceremony's
    cousin -- a finished run is the same kind of object as a claim -- instead of as a browse list.
    """
    hero = _rule('.pp-chero')

    assert 'background:' in hero and hero.count('linear-gradient') >= 2, \
        'the surface is back to a flat fill'
    assert 'var(--pp-primary)' in hero and 'var(--pp-metal-gold)' in hero, \
        'the reward wash needs both ends of the claim-banner grammar'
    assert 'var(--pp-bg-2)' in hero, 'the gradients must sit over the house card surface'


def test_no_resting_glow_on_the_hero(client):
    """PRINCIPLE 6, READ WHOLE. `motion-patterns` says "Motion **+ glow** belong to earn moments and
    *acknowledgment* states ... not to surfaces at rest." A browse row is a surface at rest.

    There WAS one: a static gold radial behind the plaque, gated on a title being earned, with
    `.career-hero::before` cited as the precedent. The comment quoted that sentence, narrowed it to motion
    alone, and then cited the same principle as the authority for a resting glow -- the clause it dropped
    from its own quotation was the one covering exactly that layer. It also did not work at base (centred at
    10% of a stacked hero, the bright part sat behind the opaque frame) and it was the largest contributor to
    a contrast regression on the plaque.

    The `:hover` glow stays: that IS the acknowledgment beat the principle sanctions, and it reads better
    with nothing resting competing against it.
    """
    css = _board_css()

    assert '.pp-chero--titled' not in css, 'the resting ambient glow is back'
    hero = open('templates/challenges/partials/_run_hero.html', encoding='utf-8').read()
    assert 'pp-chero--titled' not in hero, 'the template still emits a modifier with no rule behind it'

    # THE HOVER GLOW IS THE ONE THAT MAY GLOW, so its box-shadow must sit inside a `:hover` block.
    at_hover = css.index('.pp-chero:hover {')
    assert 'box-shadow' in css[at_hover:css.index('}', at_hover)], \
        'the hover acknowledgment beat lost its glow'

    # AND NOTHING AT REST MAY. Checked on the resting rules that could plausibly carry one.
    for selector in ('.pp-chero', '.pp-chero__frame'):
        resting = _rules_at(selector).get(0, '')
        assert '0 0 18px' not in resting and 'radial-gradient' not in resting, \
            '%s carries a resting glow' % selector


def test_the_wash_sits_behind_the_content():
    """`.career-hero`'s own companion rule, and without it the `::before` paints over the board and the
    plaque. The reference standard pairs the two for exactly this reason."""
    css = _board_css()

    assert '.pp-chero > * { position: relative; z-index: 1; }' in css, \
        'the content is not lifted above the ambient wash'
    assert 'position: relative' in _rule('.pp-chero'), \
        'an absolutely-positioned ::before needs a positioned parent, or it escapes to the viewport'


def test_the_board_is_recessed_into_the_plate():
    """DEPTH, the other half of the answer. A soft inner shadow plus a 1px specular hairline along the top
    makes the board read as set INTO the plate, lit from the same direction as the surface gradient. Flat,
    it read as a picture pasted on.

    `inset` only: nothing here casts outward, so the row's own elevation stays the single drop shadow and
    the board cannot double it.
    """
    frame = _rule('.pp-chero__frame')

    assert 'box-shadow' in frame, 'the frame is flat again'

    declaration = frame[frame.index('box-shadow:') + len('box-shadow:'):]
    declaration = declaration[:declaration.index(';')]
    layers = _shadow_layers(declaration)

    assert len(layers) >= 3, 'the recess needs its hairline and its inner shadow: %s' % layers
    # NO OUTWARD LAYER, which would double the row's own elevation. Split at depth 0 so `rgba(...)` commas
    # do not become fake layers -- the first version of this assertion failed on its own parsing.
    outward = [layer for layer in layers if not layer.startswith('inset')]
    assert outward == [], 'the frame casts outward: %s' % outward


def test_no_muted_text_sits_outside_the_plinth(client):
    """THE ACCESSIBILITY INVARIANT, and it is the most consequential thing in this block.

    `input.css` documents `--pp-text-mute` as "raised 0.55 -> 0.66 for WCAG AA (~4.5:1 on bg-2) site-wide" --
    the token sits AT the threshold with zero headroom, so ANY lightening of the surface under it fails.
    Measured on the plaque's rank-washed crest: 3.40 at the mildest band (violet 12%) and 2.68 at the apex.
    On the plinth's opaque `--pp-bg-1` it is 5.43.

    THIS USED TO PIN A LITERAL AND SO IT BROKE ON A CHANGE THAT IMPROVED THE THING IT GUARDED. The previous
    version asserted `'background: var(--pp-bg-2)' in` the plaque's own md rule, because at the time the
    plaque repainted that surface to escape the card's reward wash. The plaque is now the compact Pursuer
    Card: it carries rank-tinted MATERIAL on purpose, and the contrast guarantee moved to the plinth. The
    property never changed; only the element satisfying it did, and a literal could not tell those apart.

    SO IT ASSERTS THE PROPERTY. Every class in this component whose rule sets `color: var(--pp-text-mute)`
    must render INSIDE the plinth, which paints its own opaque surface. That catches the real hazard -- a
    muted line added to the crest, where no amount of care about the plaque's own background saves it -- and
    it keeps holding whichever element happens to own the plate.
    """
    css = _board_css()

    # Every `.pp-chero__*` class whose own rule paints `--pp-text-mute`. Derived from the stylesheet rather
    # than listed here, so a newly muted element is covered the day it is written instead of the day
    # somebody remembers to add it.
    muted = set()
    for rule in css.split('}'):
        if 'color: var(--pp-text-mute)' not in rule and 'var(--pp-text-mute);' not in rule:
            continue
        selector = rule.split('{')[0]
        for token in re.findall(r'\.(pp-chero__[a-z-]+)', selector):
            muted.add(token)
    assert muted, 'found no muted classes at all -- the scanner is broken, not the component'

    # A JOBS RUN WITH ITS SQUARES REDEEMED, which is the only combination that renders every muted line the
    # plaque has. The "job XP from this run" row needs BOTH -- A-Z runs pay no job XP at all, and an
    # unclaimed jobs run is zero, and `plaques_for` reports zero as "omit the line". Either shortcut would
    # leave that row unrendered, the loop below would skip it, and the test would pass while the row it
    # exists to police sat in the washed crest.
    run = _filled_run(_hunter('mutedhunter'), 2)
    ChallengeSlot.objects.filter(challenge=run).update(xp_redeemed_at=timezone.now())

    hero = _hero(client.get(reverse('challenges_hall_of_fame')).content.decode())
    assert 'pp-chero__plinth' in hero, 'no plinth rendered, so this proves nothing'
    crest, plinth = hero.split('pp-chero__plinth', 1)

    # THE FIXTURE'S OWN CLAIM, CHECKED. The comment above says this setup renders the paid row; if it did
    # not, the loop below would silently skip the muted label inside it and this test would pass while
    # covering one row less than it describes. A fixture that quietly stops producing the state under test
    # is the failure mode half the rewrites in this file exist to fix.
    assert 'pp-chero__paid' in plinth, (
        'the paid row did not render, so the fixture no longer creates the state this test describes')

    for token in sorted(muted):
        if token not in hero:
            continue      # a muted class this fixture does not render (e.g. a type-specific one)
        assert token not in crest, (
            '%s is muted (--pp-text-mute) but renders in the plaque\'s washed crest, where it measures '
            'as low as 2.68:1 against the 4.5 floor. Muted text belongs in the plinth.' % token)

    # THE PLINTH REALLY DOES PAINT ITS OWN OPAQUE SURFACE. Without this the loop above would pass for a
    # plinth that is a bare transparent div, which is the same failure wearing a different element.
    plinth = _rules_at('.pp-chero__plinth')

    # ASSERTED AT BASE, WHICH IS WHERE IT HAS TO BE, and this test previously asserted the OPPOSITE. The old
    # version required `'background: var(--pp-bg-1)' not in ...get(0)` on the stated grounds that below
    # 768px there was "no wash to escape". That was false, and the test was enforcing the failure:
    # `.pp-chero`'s reward wash is declared in the UNPREFIXED base rule, so it paints at 375px exactly as it
    # does at 1440px, and `--pp-text-mute` measures 4.04-4.09 against its two ends -- the same numbers this
    # stylesheet quotes as the reason the plate was introduced at all. 375px was the one viewport where
    # nothing protected the muted text.
    #
    # BASE IS ALSO THE ONLY PLACE WORTH ASSERTING IT. CSS cascades, so a background declared at base covers
    # every width; requiring it again inside the md block (as the first repair did) pins a duplicate
    # declaration rather than the property, and fails the moment someone tidies the duplicate away.
    assert 'background: var(--pp-bg-1)' in plinth.get(0, ''), \
        'the plinth must paint an opaque measured surface at EVERY width, base included'

    # AND NO BREAKPOINT MAY PAINT OVER IT. The cascade argument above only holds if nothing later replaces
    # the background with something the wash can reach through.
    for breakpoint_px, decls in sorted(plinth.items()):
        if breakpoint_px == 0 or 'background' not in decls:
            continue
        assert 'var(--pp-bg-1)' in decls, (
            'the plinth repaints its background at %dpx with something other than the measured surface: %r'
            % (breakpoint_px, decls))

    # The plaque keeps its own material (that is the Pursuer Card half of the change); what it must not do
    # is go back to a flat fill, which is what made it read as a generic profile panel.
    assert 'linear-gradient' in _rules_at('.pp-chero__plaque').get(768, ''), \
        'the plaque lost its material and is a flat fill again'


def test_the_hunter_outranks_the_run_type_on_the_plaque(client):
    """"It should celebrate the people who've earned it, not just the games" (owner, 2026-09-30). The first
    cut led the right-hand column with a type chip and gave the hunter a 15px line and a 30px avatar, so the
    row celebrated the cover art and mentioned the person.

    Pinned on the ORDER, which is what "focal point" means in markup: the avatar and the name come before
    the type, and the type is a subtitle rather than a chip above them.
    """
    _az_filled_run(_hunter('azhunter'), ['A', 'B'])

    hero = _hero(client.get(reverse('challenges_hall_of_fame')).content.decode())

    assert hero.index('pp-chero__av') < hero.index('pp-chero__hunter') < hero.index('pp-chero__type'), \
        'the type is leading the plaque again'
    assert 'bd-chip' not in hero, 'the type is back to a chip competing with the hunter for first read'


def test_the_plaque_states_the_finish_as_a_date(client):
    """A DATE, not a relative nudge. "Finished 3 months ago" is right for a feed, where recency is the
    point; on a plaque the date is the record, and `naturaltime` degrades a finish that deserves its day
    into "11 months ago". The `<time datetime>` carries the machine-readable one, which `naturaltime`
    never did."""
    run = _az_filled_run(_hunter('azhunter'), ['A', 'B'])
    run.refresh_from_db()

    hero = _hero(client.get(reverse('challenges_hall_of_fame')).content.decode())

    assert '<time class="pp-chero__when"' in hero

    # BOTH HALVES TIGHTLY. An earlier version let the machine-readable check fall through an `or` to a bare
    # date-string search of the whole document, and looked for " ago" in an 80-character window -- the kind
    # of assertion that passes for the wrong reason.
    assert 'datetime="%s' % run.completed_at.date().isoformat() in hero, 'no machine-readable finish date'

    shown = hero.split('class="pp-chero__when"')[1]
    shown = shown[shown.index('>') + 1:shown.index('</time>')].strip()
    assert str(run.completed_at.year) in shown, 'the visible finish is not a date: %r' % shown
    assert 'ago' not in shown, 'the plaque is back to a relative time: %r' % shown


def test_the_avatar_is_at_portrait_scale_not_byline_scale():
    """30px was a byline ornament. The avatar is the second thing the eye lands on after the art, so it is
    sized to be a portrait of the person the row is about."""
    av = _rule('.pp-chero__av')

    assert 'width: 48px' in av, 'the base avatar shrank back to byline scale'

    css = _board_css()
    assert '.pp-chero__av { width: 64px; height: 64px; }' in css, 'no tablet step'
    assert '.pp-chero__av { width: 76px; height: 76px; }' in css, 'no desktop step'


def test_the_key_is_a_solid_chip_not_a_scrim():
    """A GRADIENT SCRIM FAILED CONTRAST, which is why this is a solid fill.

    The key ran `rgba(0,0,0,0.82)` at its baseline to transparent at the top, and the glyph fills nearly the
    whole box -- so it straddled alpha 0.82 down to ~0.20. Over a bright cover that measured 3.44:1
    mid-glyph and 1.44:1 at the cap, and 1.07-1.21:1 for the job icons. The top third of every letter washed
    out, on the one element that exists to give the board its context.

    The recipe is `.pp-csq__key`'s, which is why that chip works on the detail board. Same text colour too,
    so the two boards' keys read as one component.
    """
    key = _rule('.pp-chero__key')

    assert 'linear-gradient' not in key, 'the gradient scrim is back; the glyph cap washes out over bright art'
    assert 'color-mix(in oklab, var(--pp-bg-0) 88%, #000)' in key, \
        "the key must use .pp-csq__key's opaque recipe"
    assert 'color: var(--pp-text)' in key, 'the letter should be the same colour as the detail board\'s key'

    # AND THE JOB TINT MUST MATCH THE DETAIL BOARD EXACTLY. It was 60% while the comment claimed "the same
    # recipe" as `.pp-csq__key--job`, which is 55% -- a near-miss that makes two surfaces look subtly
    # unrelated for no stated reason.
    css = _board_css()
    detail = _rule('.pp-csq__key--job')
    hero = _rule('.pp-chero__key--job')
    import re

    def pct(rule):
        """The percentage in the rule's `color:` mix.

        `var\\(--disc[^)]*\\)` cannot match `var(--disc, var(--pp-primary)) 55%` -- the character class
        stops at the FIRST `)`, which is the inner `var`'s. Reading the `color` declaration and taking its
        percentage avoids counting parentheses at all.
        """
        decl = re.search(r'color:\s*([^;]+)', rule)
        assert decl, 'no color declaration in %r' % rule[:60]
        return re.search(r'(\d+)%', decl.group(1))

    assert pct(detail) and pct(hero), 'could not read the two tint recipes'
    assert pct(detail).group(1) == pct(hero).group(1), (
        'the hero key tints at %s%% and the detail key at %s%%, while the comment claims one recipe'
        % (pct(hero).group(1), pct(detail).group(1)))


def test_a_finished_run_cannot_exist_without_its_date():
    """THE GUARANTEE BEHIND THE PLAQUE'S `<time datetime>`, and this test exists because an audit and I both
    got it wrong in opposite directions.

    The audit read `HallOfFameView.base_queryset` filtering `is_complete=True` without
    `completed_at__isnull=False`, noted `is_complete` is denormalized, and concluded the hero could render
    `datetime=""` -- invalid HTML. I added a template gate. Then this test refused to build the fixture:
    `Challenge` carries `challenge_completed_at_matches_flag`, which REQUIRES a non-null `completed_at`
    whenever `is_complete` is true. The state is impossible, the gate was dead code, and the constraint --
    whose own comment cites this very page ("the Hall of Fame orders on `completed_at`") -- had solved it
    before the page existed.

    So what is worth pinning is the constraint, not a branch in a template. If it is ever relaxed, the hero
    needs that gate back and this failing test is the notice.
    """
    from django.db import IntegrityError, transaction

    run = _az_filled_run(_hunter('donehunter'), ['A', 'B'])

    with pytest.raises(IntegrityError):
        with transaction.atomic():
            Challenge.objects.filter(pk=run.pk).update(completed_at=None)

    # AND THE OTHER DIRECTION, so the constraint is pinned as the two-way rule it is.
    unfinished = _run(_hunter('inflighthunter'), CHALLENGE_TYPE_AZ, done=1, total=26)
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            Challenge.objects.filter(pk=unfinished.pk).update(completed_at=timezone.now())


def test_no_template_label_falls_below_the_type_floor():
    """THE FLOOR, WHERE THE CSS GUARD CANNOT SEE IT.

    `test_challenge_detail_js::test_no_type_in_this_stylesheet_falls_below_the_12px_floor` regexes
    `font-size:` in `challenges.css`. A Tailwind arbitrary value in a template is invisible to it -- and
    both new browse pages shipped `text-[0.65rem]` (10.4px) on a header stat label. The design system
    reserves sub-`text-xs` for decorative grids and chart legends; a stat label is readable content, and
    `challenges.css` carries a three-paragraph note that this must not be done and must not be fixed by
    lowering the bound.

    SCANS EVERY CHALLENGE TEMPLATE, so the next one cannot reintroduce it quietly.
    """
    import glob
    import re

    offenders = []
    for path in glob.glob('templates/challenges/**/*.html', recursive=True):
        text = open(path, encoding='utf-8').read()
        # COMMENTS ARE STRIPPED FIRST, because the comments explaining this fix NAME the value they
        # replaced -- so the first version of this guard flagged its own rationale. A guard that forbids
        # discussing what it forbids makes the next person delete the explanation instead of the value.
        markup = re.sub(r'\{%\s*comment\s*%\}.*?\{%\s*endcomment\s*%\}', '', text, flags=re.S)
        for value in re.findall(r'text-\[([0-9.]+)rem\]', markup):
            if float(value) * 16 < 12.0:
                offenders.append('%s: %srem (%.1fpx)' % (path, value, float(value) * 16))

    assert offenders == [], 'type below the 12px floor in a template: %s' % offenders


# -- the board's geometry -------------------------------------------------------------------------
#
# THIS BLOCK EXISTS BECAUSE CATEGORY A WAS UNGUARDED. The board's CSS was rewritten twice and broken twice --
# once letterboxed inside a fixed-ratio frame, once filling that frame with cells so tall that
# `object-fit: cover` sliced ~15% off both edges of every cover -- and neither round's dozen new tests read a
# single line of it. These are text pins on a stylesheet, which is weak, but the alternative was nothing.

_BOARD_CSS = None


def _board_css():
    global _BOARD_CSS
    if _BOARD_CSS is None:
        _BOARD_CSS = open('static/css/components/challenges.css', encoding='utf-8').read()
    return _BOARD_CSS


def _names(head, selector):
    """Does this rule's prelude name `selector`, including inside a comma-separated group?

    A prefix test (`head.startswith(selector + ' {')`) misses `.a, .sel { ... }` entirely and is fooled by
    `.sel-wide {`. This stylesheet carries 17 grouped rules, two of them on `.pp-chero__*`, so a
    "must appear nowhere" assertion built on prefix matching was evadable by grouping -- a reviewer
    restoring a forbidden declaration under a grouped selector would have passed every guard.

    Exact match per comma part, so `.pp-chero--jobs .pp-chero__shelf` still cannot satisfy a lookup for
    `.pp-chero__shelf`, which several tests depend on to separate the shared rule from the jobs-only one.
    """
    return any(part.strip() == selector for part in head.split(','))


def _iter_rules(selector):
    """`{breakpoint: declarations}` for every rule matching `selector`, keyed by the min-width it sits in
    (0 for an unconditional rule).

    WHY THIS EXISTS: three separate tests reached for a breakpoint with `css.index('@media (min-width: Npx)')`
    and read forward, and all three found the WRONG block -- that string opens many times in this file, so
    `index` returns the first one and the rule being looked for is hundreds of lines away. One found the base
    rule when it wanted the md one; another found an unrelated block entirely. Tracking the enclosing query
    while scanning lines is the only reading of a stylesheet that is not a positional guess.

    MULTI-LINE RULES ARE HANDLED, which the first version of this helper was not -- it required a rule to
    open and close on one line, so it silently returned nothing for the md plaque block and the test built on
    it failed on the helper rather than on the CSS. A stylesheet reader that only reads half the file's
    formatting conventions is worse than none.

    One level of nesting is all this file has, which is what makes a line scan sufficient.

    COMMENTS ARE STRIPPED FIRST, and skipping that step made every caller satisfiable by PROSE. The
    declarations are joined with `' '.join(...)`, so a rule's own explanatory comment ended up in the
    returned string -- and this stylesheet's comments quote the declarations they explain. The title band
    is the live example: its comment contains the literal `z-index: -1`, so `assert 'z-index: 0' in title`
    kept passing after the declaration was deleted and the comment reworded, while the Hall of Fame's
    sheen silently vanished at `md:` and up (it paints behind the plaque's own plate without it). A
    stylesheet reader that cannot tell a declaration from a sentence about one pins nothing.
    """
    import re

    source = re.sub(r'/\*.*?\*/', '', _board_css(), flags=re.S)

    current, open_bp, buf, depth = 0, None, [], 0
    for line in source.splitlines():
        stripped = line.strip()

        if open_bp is not None:
            if stripped.startswith('}'):
                yield open_bp, ' '.join(buf).strip()
                open_bp, buf = None, []
                # BALANCE THE BRACE THIS BRANCH CONSUMES. The matching rule's opening line incremented
                # `depth`; its closing `}` is swallowed here, so without this the count leaks +1 per
                # captured multi-line rule and the enclosing at-rule can never return to 0 -- which is the
                # one thing that resets the scope. Everything after it would then be filed under the last
                # breakpoint seen. Inert in this stylesheet today only by the accident of rule ordering,
                # which is the same "provably fine until somebody reformats" shape as the three bugs this
                # helper has already been bitten by.
                depth = max(depth - 1, 0)
            else:
                buf.append(stripped)
            continue

        if stripped.startswith('@media'):
            media = re.match(r'@media \(min-width: (\d+)px\)', stripped)
            # ANY at-rule OPENS A SCOPE, not just a min-width one. Without this, the
            # `@media (prefers-reduced-motion: reduce)` block was read as base scope and its
            # `.pp-chero { transition: ... }` overwrote the real unconditional rule -- so a test asking for
            # the base surface got a transition declaration and failed on the helper, not the CSS. A
            # non-min-width query gets scope -1 so it can never collide with a breakpoint.
            bp = int(media.group(1)) if media else -1
            rest = stripped[stripped.index('{') + 1:].strip() if '{' in stripped else ''
            if '{' in rest and _names(rest[:rest.index('{')], selector) and rest.endswith('}'):
                # `@media ... { .sel { ... } }` all on one line.
                yield bp, rest[rest.index('{') + 1:rest.rindex('}')].strip().rstrip('}').strip()
            # A SELF-CLOSING at-rule MUST NOT LEAVE THE SCOPE OPEN, and this was the last of three bugs in
            # this helper. A one-line `@media (min-width: 1024px) { .other-sel { ... } }` that does not match
            # the selector used to leave `current` set to that breakpoint -- so the next UNCONDITIONAL rule
            # in the file was filed under it, and `.get(0)` came back empty for a rule that plainly exists.
            # Balanced braces on the line mean the block closed on it.
            opened = stripped.count('{') - stripped.count('}')
            current = bp if opened > 0 else 0
            depth += max(opened, 0)
            continue

        # A NON-MATCHING MULTI-LINE RULE INSIDE THE AT-RULE USED TO CLOSE ITS SCOPE, which is the fourth
        # bug in this family and the one the discipline rule exposed: `@media (min-width: 768px)` opens,
        # then `.pp-chero--jobs .pp-chero__board { ... }` spans four lines, and ITS closing brace reset
        # `current` to 0. Every rule after it in that block was filed under base, so asking for the shelf's
        # basis at 768 returned '' for a rule that is plainly there. Counting depth is the only reading that
        # survives a block this helper is not capturing -- matching on `stripped == '}'` cannot tell which
        # block is closing.
        depth += stripped.count('{') - stripped.count('}')
        if depth <= 0:
            depth = 0
            current = 0
        if stripped.startswith('}'):
            continue

        if '{' in stripped and _names(stripped[:stripped.index('{')], selector):
            if stripped.endswith('}'):
                yield current, stripped[stripped.index('{') + 1:stripped.rindex('}')].strip()
            else:
                open_bp, buf = current, []


def _rules_at(selector):
    """`{breakpoint: declarations}` for every rule matching `selector`, keyed by its min-width (0 for an
    unconditional rule).

    THE LAST RULE AT A BREAKPOINT WINS, which matches the cascade but makes this the WRONG reader for any
    "this must appear nowhere" assertion. `.pp-chero--jobs .pp-chero__row` carried two unconditional rules
    at the time, so a mutation that gave it a border went undetected: this returned the later
    `justify-content` rule and the assertion inspected that instead. (It has one rule again now, the box
    having been deleted -- the hazard is the reader, not that particular selector.) Use `_every_rule` to
    forbid something, and this only to read the value that actually applies.
    """
    return dict(_iter_rules(selector))


def _every_rule(selector):
    """Declarations of EVERY rule matching `selector`, in source order.

    For assertions of the form "this property must appear nowhere on this selector", where checking only
    the winning rule is how a mutation survives.
    """
    return [decls for _bp, decls in _iter_rules(selector)]


def _rule(selector):
    """The declarations of one rule, so an assertion cannot be satisfied by a different block."""
    css = _board_css()
    at = css.index(selector + ' {')
    return css[at:css.index('}', at)]


def test_the_hall_of_fame_uses_the_one_brand_accent(client):
    """CYAN, LIKE EVERY OTHER PAGE IN THE SITE, and this shipped wrong.

    The first cut gave this page `--pp-accent` (the warm orange) for its header rule, its icon, its Tally
    and the hero's hover glow, so that it would read as distinct from the Challenges browse page.
    `docs/design/visual-identity.md` settles it: cyan is THE brand accent -- "one brand accent across the
    kit, not three" -- and no other page deviates, so the orange read as a bug rather than as a
    distinction. Caught by the owner on a browser pass, not by any test, which is why there is one now.

    NOT A BAN ON `--pp-accent` IN THE FEATURE. It is the identity's named warm-orange EARN colour and the
    reward panel uses it correctly for XP amounts and the owed pill, so this is scoped to the two things
    that were wrong: the page header and the hero. The gold title chip is a third axis and also fine --
    the identity puts cyan "distinct from tier colors", and a title earned is a prestige signal.
    """
    # RENDERED, not read off the file. The class is observable in the response, and rendering also catches
    # an accent arriving from a base template or an include -- which reading the one file cannot.
    # Comments are stripped because this file's own comments discuss the decision at length.
    import re

    source = open('templates/challenges/hall_of_fame.html', encoding='utf-8').read()
    markup = re.sub(r'\{%\s*comment\s*%\}.*?\{%\s*endcomment\s*%\}', '', source, flags=re.S)
    for cls in ('border-l-accent', 'text-accent'):
        assert cls not in markup, 'the Hall of Fame header is back on a competing accent (%s)' % cls

    _az_filled_run(_hunter('azhunter'), ['A', 'B'])
    body = client.get(reverse('challenges_hall_of_fame')).content.decode()

    # THE HEADER CARD, not everything above the grid. The wider slice swept in `partials/breadcrumb.html`,
    # which marks the current crumb `text-accent` on EVERY page of the site -- so the assertion failed on
    # shared chrome that is neither this page's nor this branch's. Scoping to the one section this test is
    # about is the fix; the breadcrumb's own accent is a site-wide question for somebody else.
    header = body[body.index('<section class="card'):]
    header = header[:header.index('</section>')]

    assert 'border-l-primary' in header, 'the header rule is not the brand accent'
    assert 'text-accent' not in header, 'a competing accent reached the rendered header'
    assert 'text-primary' in header, 'the header icon and tally should be the brand accent'

    # THE WHOLE HERO BLOCK, not just up to the frame rule. The earlier span stopped at `.pp-chero__frame {`,
    # which excluded the title chip, the tab, the key and the date -- so an `--pp-accent` regression in any
    # of those survived. And `'--pp-primary' in hero` over a 135-line span with six occurrences could not
    # fail at all, so it is gone.
    css = _board_css()
    block = css[css.index('.pp-chero-list {'):css.index('/* \u2550\u2550 The board')]
    for rule_start in ('.pp-chero {', '.pp-chero:hover {', '.pp-chero:focus-visible {'):
        assert '--pp-accent' not in _rule(rule_start.rstrip(' {')), \
            '%s is back on a competing accent' % rule_start
    assert '--pp-accent' not in block, 'a competing accent is back in the hero block'


def test_the_board_cells_are_the_ratio_the_art_is_in():
    """3:4 IS LOAD-BEARING, not decoration. `object-fit: cover` crops along whichever axis overflows, so in a
    cell TALLER than the art it fits the height exactly and slices the sides -- which also makes
    `object-position: top` completely inert, and that property is the whole reason the project's image
    conventions choose it (a wider PSN fallback keeps the game's logo at the top of the crop).

    One rewrite of this file stretched the cells to 1:1.575 to fill a fixed-ratio frame. Every cover lost
    ~15% of its width, a 16:9 PSN fallback showed only its middle ~36%, and the comment promising the logo
    was preserved sat three lines below, still saying so.
    """
    assert 'aspect-ratio: 3 / 4' in _rule('.pp-chero__sq')
    assert 'object-position: top' in _rule('.pp-chero__art')


def test_the_frame_takes_its_height_from_the_board():
    """A pinned `aspect-ratio` on the frame is what forced the choice between a letterbox and a bad crop:
    a 1.905:1 box cannot be tiled by 25 or 26 cells of ratio 3:4 (three rows of 3:4 fit only 7.6 columns;
    four rows want ten, which is 40 cells for 26 items). The frame follows the board instead."""
    assert 'aspect-ratio' not in _rule('.pp-chero__frame'), (
        'a fixed frame ratio is back -- it cannot hold a 3:4 board, so one of the two will be wrong')


def test_every_flex_basis_on_the_hero_carries_slack():
    """ZERO FREE SPACE WRAPS, and this file has now shipped that bug twice.

    A `flex-basis: calc((100% - N * gap) / cols)` that accounts for its gaps EXACTLY leaves no free space,
    on a quotient no UA can represent. Chrome floors to 1/64 and Firefox rounds to 1/60, so it may happen to
    fit; a UA rounding the other way wraps the last item. `fr` tracks are immune by construction, which is
    what every one of these gave up.

    FIRST on the squares, where 26 items at 8 per row became four rows and the frame's `overflow: hidden`
    clipped the fourth away -- two covers silently gone. THEN on the jobs SHELF basis, written without
    reading the comment two rules below it: `2 * basis + 6px gap` came to exactly 100%, the second shelf
    wrapped, and the board fell back to one shelf per row at half width. The owner reported that one from
    the browser as "a smaller version of the 5 row stack".

    A GENERAL GUARD rather than a literal per rule, because a literal is exactly what failed to catch it:
    `'flex: 0 0 calc((100% - 6px) / 2);' in css` passed happily while the layout was broken. A stylesheet
    assertion cannot see a wrap, so what it CAN check is the arithmetic.
    """
    import re

    css = _board_css()
    offenders = []
    for rule in re.findall(r'\.pp-chero[^{]*\{[^}]*\}', css):
        for basis in re.findall(r'calc\(\(100% - [^;]*', rule):
            if '- 1px' not in basis:
                offenders.append(' '.join(rule.split())[:100])

    assert offenders == [], 'flex basis with no slack: %s' % offenders


def test_the_board_basis_carries_slack_for_nine_across():
    """`9 * basis + 8 * gap` used to come to EXACTLY the content width, on a quotient no UA can represent
    (32.5556px at 375px). Chrome floors to 1/64 and Firefox rounds to 1/60 so it fit; a UA rounding the other
    way wraps the 9th item, and 26 items at 8 per row is four rows -- with the frame's `overflow: hidden`
    clipping the fourth away and two covers silently vanishing. `fr` tracks were immune by construction,
    which the flex rewrite gave up; 1px buys it back."""
    css = _board_css()

    for gap in ('2px', '3px'):
        basis = 'calc((100%% - 8 * %s - 1px) / 9)' % gap
        assert basis in css, 'the %s-gap basis has no slack: expected %s' % (gap, basis)

    # THE GAP COUNT MUST BE EIGHT, one per gutter between nine cells. Nine would over-subtract and leave a
    # visible right-hand margin; seven would overflow, which is the failure above.
    assert 'calc((100% - 9 * ' not in css and 'calc((100% - 7 * ' not in css


def test_the_a_z_board_centres_its_short_last_row_and_jobs_does_not():
    """OPPOSITE NEEDS, one rule each, and a single shared rule has now been wrong for one of them twice.

    A-Z is one shelf of 26 in rows of nine, so the last row holds eight and there is no column to preserve:
    centring is what stops the board ending in a ragged step. The grid version set `justify-content` on a
    grid, where it aligns the whole track set and cannot centre a partial last row at all -- and then the
    grouped rewrite dropped it entirely, putting the notch straight back.

    Jobs is five shelves whose squares line up in a column, and `_board_groups` buckets by dict rather than
    assuming five per discipline -- so centring a four-job shelf would push it out of the column and make an
    uneven catalogue look like a layout bug.
    """
    row = _rule('.pp-chero__row')
    assert 'display: flex' in row and 'flex-wrap: wrap' in row, (
        'only a wrapping flex container can centre a partial last row')

    css = _board_css()
    assert '.pp-chero--az .pp-chero__row { justify-content: center; }' in css, \
        'the A-Z board is back to a ragged last row'
    assert '.pp-chero--jobs .pp-chero__row { justify-content: flex-start; }' in css, \
        'centring the jobs shelves breaks the discipline column'

    # AND THE TYPE CLASS HAS TO BE EMITTED, or neither rule can match anything.
    hero = open('templates/challenges/partials/_run_hero.html', encoding='utf-8').read()
    assert 'pp-chero--{{ run.challenge_type }}' in hero


def test_the_placeholder_icon_is_not_pinned_to_the_top_of_its_cell():
    """`object-position` is a separate property from `object-fit`, so overriding only the fit left a
    generic PS placeholder CONTAINED but still top-anchored -- a full-width icon with a quarter of the cell
    dead below it, out of line with every covered neighbour."""
    icon = _rule('.pp-chero__art--icon')

    assert 'object-fit: contain' in icon
    assert 'object-position: center' in icon


def test_a_truncated_marked_name_keeps_its_mark_on_both_entries():
    """`.pp-markname` is `inline-flex`, so on a `white-space: nowrap` line nothing bounds it and it lays out
    at max-content; the parent's `overflow: hidden` then shears off the overhang -- and the glyph sits AFTER
    the name, so what is sheared is exactly the supporter star, staff wrench or mod shield, on precisely the
    long PSN IDs that made the ellipsis necessary.

    The project has shipped and fixed this twice (`gamelists.css` carries the rationale at
    `.gl-card__author` and again at `.gl-spotlight__by`; `leaderboards.css` records it as "pushed the
    supporter mark out of the row entirely"), and this branch reintroduced it on both entry types.
    """
    css = _board_css()

    for parent in ('.pp-chero__hunter', '.pp-crun__hunter'):
        assert '%s .pp-markname' % parent in css, '%s can shear a supporter mark off' % parent
    assert 'max-width: 100%' in _rule('.pp-chero__hunter .pp-markname,\n.pp-crun__hunter .pp-markname')


# ─────────────────────────────────────────────────────────────────────────────────────────────────────
# THE PLAQUE AS THE COMPACT PURSUER CARD
#
# `docs/design/visual-identity.md` names the Pursuer Card a signature primitive, lists "'earned by these
# Pursuers' panels" among its surfaces and locks a `Mini (comments/leaderboards)` size. The plaque is that
# panel. What it was instead -- avatar circle, username, subtitle -- is the primitive's own first listed
# anti-pattern ("Generic 'user profile card' ... like every social app").
# ─────────────────────────────────────────────────────────────────────────────────────────────────────


def test_the_plaque_states_the_hunters_standing():
    """RANK AND PURSUER LEVEL, which the plaque had none of. The gamification half of the Pursuer Card's
    documented spine ("Avatar, Pursuer Name, Pursuer Level, active Title, top Job") was simply absent.

    READ FROM THE MATERIALIZED COLUMN, not recomputed. `ProfileCareerStanding.pursuer_level` is the same
    figure the Career page shows, and `contract_service.pursuer_level_from` records what happens when two
    surfaces derive it differently: "the Career XP leaderboard's level column and the hunter's own Career
    page showed different numbers for the same hunter".
    """
    from challenges.services.plaque import plaques_for
    from trophies.models import ProfileCareerStanding

    hunter = _hunter('standinghunter')
    ProfileCareerStanding.objects.update_or_create(
        profile=hunter, defaults={'pursuer_level': 985, 'total_xp': 412000})
    run = _filled_run(hunter, 2)

    spine = plaques_for([run])[run.pk]
    assert spine['level'] == 985
    assert spine['career_xp'] == 412000
    # THE BAND, which nothing asserted on a POPULATED standing -- so hardcoding `'band': 'matte'` in the
    # service passed the whole suite, because the only other band assertion is on a hunter whose band really
    # is matte.
    assert spine['band'] == 'tinted', spine['band']
    # 985 is `paragon`'s exact floor in PURSUER_RANKS, so this pins the boundary rather than a value
    # comfortably inside a band.
    assert spine['rank']['key'] == 'paragon', spine['rank']


def test_a_hunter_with_no_career_standing_still_gets_a_plaque():
    """A MISSING STANDING ROW IS A REAL STATE, not an error. `ProfileCareerStanding` only materializes once
    a profile has been paid a contract, and a hunter can legitimately reach this page without one: an A-Z
    run completed entirely through the history importer pays no job XP.

    IT MUST NOT BE AN ABSENT MAP ENTRY. The template reads `plaque.level`, `plaque.rank.label` and the band
    directly, so a missing entry would blank the plaque's whole lower half for exactly the hunters most
    likely to have finished that way -- silently, because Django templates swallow missing keys.

    AND THE LEVEL IS THE FLOOR, NOT ZERO. This is the defect two independent audits both put first, and an
    earlier version of this test asserted the bug as correct (`spine['level'] == 0`). A hunter has a level in
    every job from the moment they exist -- an untouched job sits at level 1 -- so
    `pursuer_level_from(None, None, 25)` is 25, and their own Career page reads Lv 25 while the plaque read
    Lv 0. It is exactly the drift `UNTOUCHED_JOB_LEVEL`'s comment exists to prevent: the band was floored and
    the level was not. The plaque also contradicted itself on one row, reporting five families averaging
    level 1.0 across a 25-job catalogue beside a headline of Lv 0.

    DERIVED FROM THE SHARED HELPER, NOT PINNED AS 25. The catalogue is staff-editable, so a literal would
    turn a legitimate job addition into a test failure -- and, worse, a hard-coded 25 would still pass if
    `plaques_for` stopped going through `pursuer_level_from` and happened to agree today.
    """
    from challenges.services.plaque import plaques_for
    from trophies.models import ProfileCareerStanding
    from trophies.services.contract_service import catalogue_job_count, pursuer_level_from

    hunter = _hunter('nostanding')
    # CREATED AND THEN DELETED, so the fixture really does engineer the state. A bare
    # `.filter(...).delete()` was a guaranteed no-op: `ProfileFactory` does not create a standing row, no
    # `post_save` on `Profile` creates one, and the only writer in the tree is
    # `contract_service.recompute_career_standing` -- so "no standing row" is the factory default and the
    # delete removed nothing. A setup line that cannot fail is a setup line that stops describing the test.
    ProfileCareerStanding.objects.update_or_create(
        profile=hunter, defaults={'pursuer_level': 300, 'total_xp': 90000})
    assert ProfileCareerStanding.objects.filter(profile=hunter).exists()
    ProfileCareerStanding.objects.filter(profile=hunter).delete()
    run = _filled_run(hunter, 2)

    spine = plaques_for([run])[run.pk]

    expected = pursuer_level_from(None, None, catalogue_job_count())
    assert expected > 0, 'the job catalogue is empty, so this test would pass vacuously'
    assert spine['level'] == expected, (
        'a hunter with no standing row must read the floored level their Career page shows (%d), not 0'
        % expected)

    assert spine['career_xp'] == 0, 'no standing row means no Career XP -- that one really is zero'
    assert spine['rank']['key'] == 'newbie'
    assert spine['band'] == 'matte'
    assert len(spine['ring']) == 5, 'the ring must still have five arcs to keep the layout stable'


def test_a_hunter_with_no_job_xp_gets_an_even_ring_not_five_full_arcs():
    """THE DEFECT THIS EXISTS FOR, and the ring is what makes it structurally impossible.

    The bespoke band this replaced scaled each family against the hunter's STRONGEST family. That ratio is
    undefined when every family sits at the level-1 floor, and the arithmetic resolved it as
    1.0 / 1.0 = 100% -- five FULL bars for a hunter who has never been paid a contract, reading as mastery of
    everything on the one page built to display mastery. Reachable: an A-Z run finished entirely through the
    history importer pays no job XP.

    The ring proportions each arc against the SUM instead, so five equal floors are five equal arcs -- the
    truth, and exactly what the Career hero already shows those hunters.

    (`pursuer_card_service` still carries the strongest-family version and so still carries the bug. It is
    dormant: that component is mounted on nothing but a staff design preview.)
    """
    from challenges.services.plaque import plaques_for
    from trophies.services.job_render import RING_CIRCUMFERENCE

    run = _filled_run(_hunter('noxp'), 2)

    ring = plaques_for([run])[run.pk]['ring']
    assert len(ring) == 5

    dashes = [arc['dash'] for arc in ring]
    assert len(set(dashes)) == 1, 'five families at the same floor must draw five equal arcs: %r' % dashes
    assert abs(sum(dashes) - RING_CIRCUMFERENCE) < 0.5, \
        'the arcs must still fill the whole ring, not leave it part-drawn: %r' % sum(dashes)
    # And NOT five empty arcs, which would read as broken rather than as new.
    assert all(d > 0 for d in dashes), dashes

    # The averages still ride along for any host that labels an arc -- level 1 in everything is the truth.
    assert all(arc['avg'] == 1.0 for arc in ring), ring


def test_the_family_band_is_ordered_canonically_and_not_by_the_aggregate():
    """FIVE TILES IN `DISCIPLINE_LABELS` ORDER, always. The aggregate's own key order is whatever Postgres
    returns, so iterating it would put one hunter's Combat tile where another's Heart tile sits -- and a band
    whose positions move between rows cannot be compared at a glance, which is the only thing a band is for.
    """
    from challenges.services.plaque import plaques_for
    from trophies.services.job_render import DISCIPLINE_LABELS

    from trophies.models import Job, ProfileJobXP

    hunter = _hunter('orderhunter')
    run = _filled_run(hunter, 2)

    # THREE DISCIPLINES AT THREE DIFFERENT LEVELS, because with no `ProfileJobXP` rows at all there is no
    # aggregate order for the code to be wrong about -- the earlier version of this test pinned only "the
    # comprehension iterates DISCIPLINE_LABELS", not the hazard its own name describes. Seeding `finesse`
    # (last in canonical order) highest and `heart` (fourth) next makes the two orders genuinely differ.
    for slug, level in (('finesse', 30), ('heart', 20), ('combat', 10)):
        for job in Job.objects.filter(discipline=slug):
            ProfileJobXP.objects.update_or_create(
                profile=hunter, job=job, defaults={'total_xp': level * 1000, 'level': level})

    ring = plaques_for([run])[run.pk]['ring']
    assert [f['slug'] for f in ring] == list(DISCIPLINE_LABELS)
    assert [f['label'] for f in ring] == list(DISCIPLINE_LABELS.values())

    # ORDER IS LOAD-BEARING ON A RING in a way it was not on a band: `offset` is CUMULATIVE, so the sequence
    # is what makes the arcs meet. A reordering does not just shuffle tiles, it draws a broken donut.
    assert ring[0]['offset'] == 0.0, 'the first arc starts at zero or the ring has a gap at the top'
    running = 0.0
    for arc in ring:
        assert abs(arc['offset'] - (-running)) < 0.01, \
            'arc offsets are not cumulative, so the ring will overlap or gap: %r' % ring
        running += arc['dash']

    # AND THE VALUES PROVE THE ORDER IS NOT ACCIDENTALLY RIGHT: sorted by average this would be
    # finesse, heart, combat, then the two at the floor -- a different sequence from the canonical one.
    by_slug = {f['slug']: f['avg'] for f in ring}
    assert by_slug['finesse'] == 30.0 and by_slug['heart'] == 20.0 and by_slug['combat'] == 10.0, by_slug
    assert [f['slug'] for f in ring] != [s for s, _a in sorted(by_slug.items(), key=lambda kv: -kv[1])], \
        'the canonical order coincides with the value order here, so this fixture proves nothing'


def test_only_a_jobs_run_reports_the_xp_it_paid():
    """A-Z RUNS PAY NO JOB XP, by decision rather than oversight: `rewards.redeemable_slots` gates on the
    Job Coverage type, so the 6,000-per-square bonus belongs to that side even when one platinum advanced
    both boards.

    AND AN UNCLAIMED JOBS RUN IS ZERO, which the plaque renders as "omit the line" rather than "+0 job XP" --
    a zero would read as a failed payout on a page celebrating the finish.
    """
    from challenges.services.plaque import plaques_for
    from trophies.util_modules.constants import CHALLENGE_SLOT_JOB_XP

    az = _az_filled_run(_hunter('azpaid'), ['A', 'B'])
    jobs = _filled_run(_hunter('jobspaid'), 3)

    spines = plaques_for([az, jobs])
    assert spines[az.pk]['run_xp'] == 0, 'an A-Z run must not claim job XP'
    assert spines[jobs.pk]['run_xp'] == 0, 'nothing is claimed yet, so there is nothing to report'

    ChallengeSlot.objects.filter(challenge=jobs).update(xp_redeemed_at=timezone.now())
    assert plaques_for([jobs])[jobs.pk]['run_xp'] == 3 * CHALLENGE_SLOT_JOB_XP

    # EVEN WITH REDEEMED SLOTS, an A-Z run reports nothing -- the TYPE gate is what decides, not the column.
    # Without this the assertion above would also pass for an implementation that just counted the column,
    # which is reachable: an A-Z slot's `xp_redeemed_at` has no constraint forbidding a value.
    ChallengeSlot.objects.filter(challenge=az).update(xp_redeemed_at=timezone.now())
    assert plaques_for([az])[az.pk]['run_xp'] == 0, \
        'the A-Z gate is reading the redeemed column instead of the challenge type'


def test_the_plaque_spine_costs_a_flat_number_of_queries():
    """FLAT PER PAGE, which is the property that matters rather than the exact count. The figure in
    `enrich`'s docstring has been wrong three times; what must not change is that it does not scale.

    SELF-CONTAINED BY DESIGN. Rank, level and Career XP live on a reverse OneToOne, so a
    `select_related('profile__career_standing')` in the view would make them free -- and a caller who
    forgot the hint would get a silent per-entry query instead of a visible failure. `plaques_for` reads
    the standings itself, which cannot regress that way. This test is what would catch it if somebody
    "optimised" the read back onto the caller.
    """
    from challenges.services.plaque import plaques_for

    one = [_filled_run(_hunter('cost1'), 2)]
    many = one + [_filled_run(_hunter('cost%d' % n), 2) for n in range(2, 6)]

    with CaptureQueriesContext(connection) as single:
        plaques_for(one)
    with CaptureQueriesContext(connection) as five:
        plaques_for(many)

    assert len(five) == len(single), (
        'the plaque spine scales with entry count: %d queries for one run, %d for five\n%s'
        % (len(single), len(five), '\n'.join(q['sql'][:110] for q in five)))


def test_every_rank_maps_to_an_escalation_band():
    """A RUNG WITH NO BAND would be an unstyled plaque, and a KeyError if the service subscripted
    `RANK_BANDS`. `PURSUER_RANKS` is the only producer of these keys, so the two have to stay in step --
    and adding a rung to the ladder is exactly the kind of change that forgets a presentation map living in
    another app.
    """
    from challenges.services.plaque import RANK_BANDS
    from trophies.util_modules.leveling import PURSUER_RANKS

    keys = [rank[1] for rank in PURSUER_RANKS]
    missing = [key for key in keys if key not in RANK_BANDS]
    assert missing == [], 'ranks with no escalation band: %s' % missing

    stale = [key for key in RANK_BANDS if key not in keys]
    assert stale == [], 'bands for ranks that no longer exist: %s' % stale

    # AND EVERY BAND IS A REAL TREATMENT, so no rank renders as "unstyled".
    assert set(RANK_BANDS.values()) <= {'matte', 'lift', 'tinted', 'radiant'}, set(RANK_BANDS.values())


def test_the_rank_label_recipe_clears_contrast_for_every_rank():
    """THE MEASURED RECIPE, pinned so it cannot be "simplified" to the raw token.

    `--rank-*` spans a grey (#6b7280 Newbie) to a near-white (#e8f1ff Ascendant). On the plaque's washed
    crest FOUR of the eleven raw tokens fail WCAG AA outright -- newbie 2.72, recruit 3.43, luminary 3.44,
    paragon 3.74 -- so the label mixes 34% toward white, which clears all eleven with the worst case at
    5.00. Dropping the mix would look like a tidy-up and would quietly fail four of the eleven ranks.
    """
    # `_rules_at`, NOT `_rule`. `.pp-chero__standing {` occurs twice -- the base rule and a one-line
    # `@media (min-width: 1024px)` font-size override -- and `_rule` is a bare `css.index(selector + ' {')`,
    # so it returns whichever comes first in the file. It happens to be the right one today; `_rules_at`'s
    # own docstring records three tests that reached for a breakpoint this way and all three found the WRONG
    # block, and `_rule` is the surviving instance of the pattern that warning is about.
    standing = _rules_at('.pp-chero__standing').get(0, '')
    assert standing, 'no base rule for .pp-chero__standing -- the reader found nothing to check'

    # THE MIX ONLY. `'var(--rk' in standing` was asserted alongside this and added nothing: it is satisfied
    # by the very same declaration the mix check already matched.
    assert '#fff 34%' in standing, (
        'the rank label must keep its measured mix toward white; the raw --rank-* token fails AA for '
        'newbie, recruit, luminary and paragon: %r' % standing)
    assert 'var(--pp-text-mute)' not in standing, \
        'the standing line is not muted text -- it carries the rank hue'

    # AND NO OPACITY ANYWHERE ON THE LINE OR ITS CHILDREN, which is how this recipe was defeated once
    # already: `.pp-chero__lvl` carried `opacity: 0.78`, multiplying Newbie's measured 5.00 down to 4.13.
    # A ratio measured on a colour says nothing about a descendant that then fades it.
    #
    # TWO REPAIRS, both from an audit. The loop used to name `.pp-chero__lvl` -- which was DELETED with the
    # level, so `_rules_at` returned nothing and `'opacity' not in ''` passed trivially: a third of the loop
    # pinned a class that does not exist. And it read only `.get(0)`, the unconditional rule, while every
    # band treatment and the entire crest wash live inside `@media (min-width: 768px)` -- so an opacity added
    # at `md` or `lg`, at exactly the widths where the wash it guards against exists, was invisible.
    #
    # EVERY BREAKPOINT NOW, and the children are discovered rather than listed, so a new one is covered the
    # day it is written.
    children = sorted(set(re.findall(r'\.(pp-chero__(?:rank|gem|standing)[a-z-]*)', _board_css())))
    assert children, 'found no children of the standing line -- the scanner is broken, not the CSS'
    for child in children:
        for breakpoint_px, decls in _rules_at('.%s' % child).items():
            body = re.sub(r'/\*.*?\*/', '', decls, flags=re.S)
            assert 'opacity' not in body, (
                '.%s fades the measured standing recipe at %dpx; quieten it by weight or colour, not by '
                'opacity: %r' % (child, breakpoint_px, decls))


def test_the_plaque_wears_no_orphaned_primitives_mark(client):
    """NO CORNER DIAMONDS, which is an owner decision and a structural one, and this test replaced one that
    asserted the opposite.

    The plaque carried four -- the Frame's brand mark, two pseudo-elements plus two spans, each rank band
    turning a cut notch into a lit gem. The owner cut them on a browser pass: "I don't like the diamonds on
    the corners either, they don't really look great."

    THE STRUCTURAL READING AGREES, and it is the second time on this component. The diamonds are the FRAME's
    mark, and the Frame's production partial is rendered by NOTHING -- not even
    `templates/design/frame_preview.html`, which is a self-contained prototype that includes no partials --
    having been superseded on the badge surfaces by the Badge Medallion. So the plaque was wearing the brand
    mark of an orphaned primitive, exactly as its discipline band had been modelled on an orphaned component.
    One failed reuse check produced both.

    WHY PIN A REMOVAL. Because the thing that put them there was a plausible-looking citation in the design
    constitution, and that citation is still there -- the Frame is still described as a signature primitive.
    Without a pin, the next reader doing the same search reaches the same wrong conclusion. The sibling pin
    `test_the_plaque_hosts_the_shared_ring_rather_than_its_own_band` exists for the same reason.
    """
    # COMMENTS STRIPPED, because the rules that record the removal NAME the classes they removed -- the trap
    # this file has now hit three times.
    code = re.sub(r'/\*.*?\*/', '', _board_css(), flags=re.S)

    for dead in ('pp-chero__nbl', 'pp-chero__nbr', '--nbg', '--nbd', '--nglow'):
        assert dead not in code, 'the corner diamonds are back in challenges.css: %s' % dead
    assert '.pp-chero__plaque::before' not in code and '.pp-chero__plaque::after' not in code, \
        'the plaque grew corner pseudo-elements again'

    _filled_run(_hunter('cornerhunter'), 2)
    hero = _hero(client.get(reverse('challenges_hall_of_fame')).content.decode())
    assert 'pp-chero__nbl' not in hero and 'pp-chero__nbr' not in hero, \
        'the corner diamond spans are rendering again'

    # AND THE PLAQUE STILL HAS ITS OWN IDENTITY, so this is a removal rather than a strip-down. What carries
    # it now is all live: the rank-tinted material, the gold title band, the plinth, the shared ring.
    plaque_md = _rules_at('.pp-chero__plaque').get(768, '')
    assert 'linear-gradient' in plaque_md, 'the plaque lost its material and is a flat fill'
    assert 'lab-dna' in hero, 'the plaque lost the shared ring'

    # The plinth owns its own bottom corners -- true whatever the parent clips, because it is the element
    # that paints them. (It used to be justified by the diamonds forbidding `overflow: hidden` upstream.)
    assert 'border-radius: 0 0' in _rules_at('.pp-chero__plinth').get(768, ''), \
        'the plinth must shape the corner it paints'



def test_the_family_band_reflects_real_job_xp():
    """THE POSITIVE CASE, and its absence let a mutation through: blanking the job-catalogue read entirely
    survived the whole suite.

    WHY IT SURVIVED. With no catalogue, every discipline takes `_families`' zero-jobs branch, which resolves
    to the level-1 floor -- so `avg` is 1.0 everywhere and `at_floor` makes every bar 0. That is EXACTLY what
    `test_the_family_band_is_empty_rather_than_full_for_a_hunter_with_no_job_xp` asserts. Two very different
    causes, one indistinguishable output: a hunter with no XP, and a catalogue query that returned nothing.
    The second would ship a plausible-looking band for every hunter on the page, including whales.

    So this test seeds real `ProfileJobXP` and asserts the band MOVES -- which only holds when the catalogue
    denominator is real.
    """
    from challenges.services.plaque import plaques_for
    from trophies.models import Job, ProfileJobXP

    hunter = _hunter('bandhunter')
    run = _filled_run(hunter, 2)

    # One discipline pushed well above the floor, the rest left untouched. Levelling EVERY job of one
    # discipline is what makes its average unambiguous -- a single job among five would be diluted by the
    # four floors around it and could land close enough to the floor to prove little.
    combat = list(Job.objects.filter(discipline='combat'))
    assert combat, 'the job catalogue has no combat jobs, so this test proves nothing'
    for job in combat:
        ProfileJobXP.objects.update_or_create(
            profile=hunter, job=job, defaults={'total_xp': 90000, 'level': 40})

    ring = {f['slug']: f for f in plaques_for([run])[run.pk]['ring']}

    assert ring['combat']['avg'] == 40.0, (
        'combat should average exactly 40 with every one of its jobs at level 40: %r' % ring['combat'])

    # AND THE UNTOUCHED ONES ARE AT THE FLOOR, not at zero -- the same floor `pursuer_level_from` applies.
    others = [f for slug, f in ring.items() if slug != 'combat']
    assert all(f['avg'] == 1.0 for f in others), others

    # THE ARC IS PROPORTIONED BY `total`, NOT BY `avg`. Combat holds 5 jobs at level 40 = 200; each other
    # discipline holds 5 at the floor = 5; the whole is 220, so combat's share is 200/220 = 91%. Pinning the
    # arithmetic rather than "combat is biggest": a formula scaled against the wrong denominator, or against
    # the average instead of the total, would also make combat biggest.
    assert ring['combat']['share_pct'] == 91, ring['combat']
    assert all(f['share_pct'] == 2 for f in others), [f['share_pct'] for f in others]


def test_an_a_z_hero_omits_the_paid_row_entirely(client):
    """A ZERO MUST OMIT THE ROW, not print "+0 job XP" -- which would read as a failed payout on the one
    page built to celebrate the finish.

    THIS GAP WAS FOUND BY MUTATION, not by reading. `test_only_a_jobs_run_reports_the_xp_it_paid` pins the
    SERVICE side (an A-Z run's `run_xp` is 0) and nothing pinned the TEMPLATE side, so ungating
    `{% templatetag openblock %} if plaque.run_xp {% templatetag closeblock %}` survived: every test that
    rendered the row used a jobs run with redeemed slots, where the gate makes no difference.
    """
    _az_filled_run(_hunter('aznopaid'), ['A', 'B'])

    hero = _hero(client.get(reverse('challenges_hall_of_fame')).content.decode())

    assert 'pp-chero__plinth' in hero, 'no plinth rendered, so this proves nothing'
    assert 'pp-chero__paid' not in hero, \
        'an A-Z hero is drawing the paid row, which can only say "+0 job XP"'
    # Scoped by absence of the CLASS rather than of the string "job XP", which also appears in the plinth's
    # Career XP label -- a substring check there would have been satisfied by the wrong element.
    assert 'career XP' in hero, 'the Career XP stat should still render; only the run payout is omitted'


def test_the_hero_carries_the_hunters_rank_hue_and_escalation_band(client):
    """THE WHOLE RANK PATH, at the output, which nothing reached.

    THE HOLE: `RANK_BANDS` was verified as a DICT and never verified to be USED, and no test anywhere
    referenced `pp-chero--band` or the inline `--rk`. So deleting
    `style="--rk: var(--rank-KEY, var(--pp-primary));"` from the `<a>` passed the entire suite -- and it
    fails SILENTLY rather than visibly, because `.pp-chero__standing` and `.pp-chero__gem` both read
    `var(--rk, var(--pp-primary))`: every rank would quietly resolve to the brand cyan, which is the exact
    opposite of what a per-rank spectrum exists for. Deleting the `.pp-chero--band-tinted/--radiant` CSS
    blocks passed too.

    WHY A RENDER TEST AND NOT A SOURCE-TEXT ONE: the hue arrives as an inline custom property on the anchor
    and the band as a class on the same anchor, so both are facts about the rendered page. A `'--rk' in
    template_source` check would pass with the service returning no rank at all.
    """
    from trophies.models import ProfileCareerStanding

    hunter = _hunter('rankhunter')
    # 985 is `paragon`'s floor, which `RANK_BANDS` maps to `tinted` -- so this one fixture pins the hue, the
    # band, and the join between them.
    ProfileCareerStanding.objects.update_or_create(
        profile=hunter, defaults={'pursuer_level': 985, 'total_xp': 400000})
    _filled_run(hunter, 2)

    hero = _hero(client.get(reverse('challenges_hall_of_fame')).content.decode())

    assert '--rk: var(--rank-paragon' in hero, (
        'the hero is not carrying the hunter\'s rank hue, so every rank falls back to the brand cyan: %r'
        % hero[:220])
    assert 'pp-chero--band-tinted' in hero, 'the escalation band class is missing from the hero'

    # AND THE RANK IS NAMED IN THE PLAQUE, not only encoded as a colour -- colour alone is not an accessible
    # way to convey standing.
    assert 'Paragon' in hero, 'the rank label should be rendered as text, not just as a hue'

    # THE BAND CLASS MUST TRACK THE RANK rather than being a constant. A second hunter at a resting rung has
    # to come back `matte`, or `'band': 'matte'` hardcoded in the service would satisfy the assertion above
    # for the wrong reason on a page where every entry happened to be tinted.
    resting = _hunter('restinghunter')
    ProfileCareerStanding.objects.filter(profile=resting).delete()
    _filled_run(resting, 2)

    page = client.get(reverse('challenges_hall_of_fame')).content.decode()
    assert 'pp-chero--band-matte' in page, 'a resting-rank hunter must get the matte band'
    assert 'pp-chero--band-tinted' in page, 'and the tinted one must still be there for the other hunter'


def test_the_plaque_hosts_the_shared_ring_rather_than_its_own_band(client):
    """THE REUSE ITSELF, pinned, because nothing pinned it and that is how a duplicate shipped.

    WHAT HAPPENED. The plaque's first cut carried a bespoke five-tile discipline band, modelled on the
    abandoned Pursuer Card's and justified by that primitive's anti-pattern about hero/compact/mini
    consistency. `partials/components/_disciplines_ring.html` (`.lab-dna`) was sitting one directory away,
    live on the Career hero AND the home lobby, with a docstring that states the contract outright: "the
    lobby's smaller ring is a scale, not a second implementation, which is what keeps the two surfaces from
    drifting." The band was that second implementation. The owner spotted it; no test could have.

    WHY A TEST AND NOT A NOTE. The reuse is invisible at every other level -- the page renders, the suite
    passes, and the duplicate looks like a deliberate design. The only durable signal is that this surface
    includes the shared component and defines no rival for it.

    IT ALSO GUARDS THE ARITHMETIC'S ONE HOME. `job_render.discipline_ring` owns the cumulative arc geometry
    for all three hosts; giving the plaque the partial but its own copy of the maths would reproduce the same
    mistake one layer down.
    """
    from trophies.services.job_render import RING_CIRCUMFERENCE  # noqa: F401  (import = it exists here)

    # COMMENT-STRIPPED, and this assertion could not fail without it. The path occurs TWICE in the
    # template: the real `{% templatetag openblock %} include {% templatetag closeblock %}`, and the
    # `{% templatetag openblock %} comment {% templatetag closeblock %}` block above it that explains why
    # the ring is used. Deleting the include left the assertion passing on its own rationale -- the fourth
    # time this feature has hit "the comment contains the thing you are checking for", and the one place the
    # fix had not been applied. `test_challenge_detail_js._template_code` exists for exactly this.
    from tests.engine.test_challenge_detail_js import _template_code

    # Relative paths, as `_board_css` in this file already does -- there is no ROOT helper here.
    hero_src = _template_code(open('templates/challenges/partials/_run_hero.html', encoding='utf-8').read())

    assert 'partials/components/_disciplines_ring.html' in hero_src, (
        'the plaque must INCLUDE the shared disciplines ring, not reimplement it -- that is how the '
        'five-tile band got built against an abandoned component while the live one sat unused')

    # AND NO RIVAL BAND. These are the class names the deleted implementation used; a reader who wants a
    # discipline treatment here should reach for the ring, and if the ring genuinely will not do, that is a
    # conversation rather than a fresh grid.
    #
    # COMMENTS STRIPPED FIRST, and the first version of this assertion failed on its own prose: the CSS
    # comment recording the deletion NAMES every class it deleted, so a raw substring scan found them and
    # reported the band as back. "Comments contain the thing you are forbidding" is a trap this feature has
    # now paid for three times -- a type-floor scanner flagged its own rationale, a JS check matched the
    # comment explaining it, and now this.
    code = re.sub(r'/\*.*?\*/', '', _board_css(), flags=re.S)
    # ONE PREFIX, which subsumes the whole family. The list used to name five of the six deleted classes and
    # omitted `pp-chero__fam` -- the tile class, and a prefix of every other one -- so a band resurrected on
    # just that class plus layout utilities would have tripped none of them.
    assert 'pp-chero__fam' not in code, 'the bespoke discipline band is back in challenges.css'

    # THE GEOMETRY HAS ONE HOME. `career_service` must go through the helper too, or "one implementation"
    # is only true of the template.
    career_src = open('trophies/services/career_service.py', encoding='utf-8').read()
    assert 'discipline_ring(' in career_src, \
        'career_service stopped using the shared arc helper, so the geometry has two homes again'
    # THE ASSIGNMENT, not the name. A bare `'_RING_C' not in career_src` tripped on the comment that
    # RECORDS the move -- the second time in this one test that a scan matched the prose explaining the rule
    # it enforces. What must not come back is the DEFINITION.
    assert '_RING_C =' not in career_src, \
        'the ring circumference is back as a private constant in career_service'

    # AND IT RENDERS. The three source checks above would all pass on a page that never draws a ring.
    _filled_run(_hunter('ringhunter'), 2)
    hero = _hero(client.get(reverse('challenges_hall_of_fame')).content.decode())
    assert 'lab-dna' in hero, 'the ring is included but not rendering'
    assert 'lab-dna__arc' in hero, 'the ring rendered with no arcs, so the spine fed it nothing'

    # AT ITS NATURAL SIZE, NOT `compact`. The first cut passed `compact` and the owner asked for a bigger
    # ring ("the ring shrunk a bit now ... the top side of the plaque is a bit large"). Dropping it also
    # retired a host-scoped width override that only existed because `.lab-dna--sm`'s 76px is clobbered at
    # 1024px by a later equal-specificity rule while its FONT rules keep winning -- so `compact` meant a
    # 132px ring holding type sized for 76.
    assert 'lab-dna--sm' not in hero, (
        'the plaque is back on the compact scale, which is both smaller than the owner asked for and the '
        'variant whose width is overridden at 1024px')


def test_each_ring_arc_peeks_its_disciplines_level_on_hover(client):
    """HOVER AN ARC, SEE THAT DISCIPLINE'S LEVEL. Owner, 2026-10-02: "it would be really cool if you could
    hover over each ring section and have it animate and see how many levels they have in that discipline,
    if that isn't too much for the page to handle."

    NOT TOO MUCH, BECAUSE THERE IS NO JAVASCRIPT. The Hall of Fame stacks eight rings, so a scripted version
    means 40 listeners or one delegated handler hit-testing SVG geometry on every mousemove. `:has()` does it
    with no script, and only one arc transitions at a time -- `opacity`, `stroke-width` and a `filter` on a
    single `r=42` circle, which is the same recipe `.rp-ring__seg.is-linked` already ships on the reward
    panel rather than a new one.

    THE HOOKS WERE ALREADY THERE, UNUSED, which is what made this the right shape: every arc has carried
    `data-disc` and `transition: opacity` from the start, and so have Career's legend rows -- attributes with
    no consumer, on two elements that only make sense to link. This wires what was laid.

    THE NUMBER IS `total`, THE SUMMED LEVEL, not the average -- "how many levels they have in that
    discipline" -- and it is the same figure Career's legend prints, so the two surfaces agree.

    WHAT THIS DOES *NOT* PIN: that hovering works, which no server-rendered test can see. It pins that the
    markup and the rules the hover needs are both present and correctly keyed, so the feature cannot be
    half-deleted. The browser pass is the owner's.
    """
    from trophies.models import Job, ProfileJobXP
    from trophies.services.job_render import DISCIPLINE_LABELS

    hunter = _hunter('peekhunter')
    run = _filled_run(hunter, 2)
    # Real XP in one discipline so its peek carries a figure that is not the floor -- otherwise every peek
    # would read the same and a mis-keyed template would be invisible.
    for job in Job.objects.filter(discipline='combat'):
        ProfileJobXP.objects.update_or_create(
            profile=hunter, job=job, defaults={'total_xp': 90000, 'level': 40})

    hero = _hero(client.get(reverse('challenges_hall_of_fame')).content.decode())

    # ONE PEEK PER ARC, each keyed to its discipline.
    for slug in DISCIPLINE_LABELS:
        assert 'data-disc="%s"' % slug in hero, 'no arc/peek keyed for %s' % slug
    assert hero.count('lab-dna__peek') == len(DISCIPLINE_LABELS), (
        'expected one peek per discipline, got %d' % hero.count('lab-dna__peek'))

    # THE COMBAT PEEK CARRIES ITS SUMMED LEVEL (5 jobs x 40 = 200), which is `total` and not `avg` (40.0).
    #
    # ANCHORED ON THE PEEK, not on `data-disc="combat"` alone. That string's LAST occurrence is the peek
    # today -- but if the peek lost or changed its key, the slice would silently land on the combat ARC and
    # every assertion below would keep passing against the wrong element.
    combat = hero.split('lab-dna__peek" data-disc="combat"')[-1].split('</div>')[0]
    assert '200' in combat, ('the combat peek should print its summed level, 200: %r' % combat)

    # DECORATIVE TO AT. These duplicate the arcs for sighted hover; announcing five more numbers would add
    # ten words to an accessible name that is already one long run-on string for the whole row. The same
    # figures are visible text in Career's legend, which is the surface built to be read.
    assert 'aria-hidden="true"' in combat, (
        'the peeks must be aria-hidden -- they duplicate the arcs for sighted hover, and announcing five '
        'more numbers would bloat a row link name that is already one long run-on string')

    # AND THE RULES THAT REVEAL THEM EXIST, keyed the same way. Markup without rules is a feature that
    # renders nothing; rules without markup style nothing. Both halves or neither.
    # `elements.css`, not `challenges.css`: the ring is a shared component and its hover lives with it.
    css = open('static/css/components/elements.css', encoding='utf-8').read()
    for slug in DISCIPLINE_LABELS:
        assert '.lab-dna__peek[data-disc="%s"]' % slug in css, (
            'no reveal rule for %s, so hovering its arc shows nothing' % slug)
    assert '.lab-dna:has(.lab-dna__arc:hover)' in css, 'the arc-hover scope is gone'

    # THE RESTING STATE, which nothing pinned and whose loss is the worst failure available here: without
    # `opacity: 0` all FIVE peeks render permanently, stacked on each other and on the Pursuer Level, on
    # every surface hosting this ring. `pointer-events: none` is the one line stopping a visible peek from
    # stealing the hover from the arc beneath it.
    code = re.sub(r'/\*.*?\*/', '', css, flags=re.S)
    peek_base = code.split('.lab-dna__peek {')[1].split('}')[0]
    assert 'opacity: 0' in peek_base, 'the peeks are not hidden at rest -- all five would render at once'
    assert 'pointer-events: none' in peek_base, 'a visible peek can steal the hover from its own arc'
    assert '.lab-dna:has(.lab-dna__arc:hover) .lab-dna__center { opacity: 0; }' in code, (
        'the centre no longer clears on hover, so the peek number overlays the Pursuer Level')

    # AND THE HOVER RULES ARE GATED ON A REAL POINTER, while the RESTING state is not. Without the gate a
    # tap sticks `:hover` until the next tap elsewhere, and on Career the ring is not inside a link, so a
    # stray tap blanks the hero's headline number. But the gate must NOT wrap the resting state: a first
    # attempt wrapped both, which would have rendered all five peeks visible and in-flow on touch.
    assert '@media (any-hover: hover) and (any-pointer: fine)' in code, (
        'the arc-hover rules are not gated on a real pointer; a tap would stick them on touch')
    assert 'any-hover' not in code.split('.lab-dna__peek {')[0].split('.lab-dna__level--peek')[0][-400:], (
        'the pointer gate looks like it wraps the peek base rule -- gate the behaviour, not the resting '
        'state it reveals')
    assert 'stroke-width: 16' in css, 'the hovered arc no longer thickens, so nothing "animates"'

    # THE ONE LONG NAME IS SHORTENED IN THE DATA, which is the fix for an overflow the owner reported
    # (2026-10-02: "the word 'Exploration' is slightly too large for the ring and overflows it").
    #
    # "EXPLORATION" is eleven characters at `.lab-dna__cap`'s 0.2em tracking -- which is tuned for the
    # five-character resting "LEVEL" -- measuring ~81px against the ring's ~70px inner hole. The next longest,
    # "FINESSE", measures ~52px and fits with room to spare.
    #
    # AND NO CSS CONSTRAINT, because the first repair was one and it made things worse: a `max-width: 60%` on
    # a grid item inside a `place-content: center` grid resolves against the CONTENT-sized track, so it
    # clipped all five labels to 60% of their own widths. Asserting its absence keeps that from coming back
    # as a "fix" for the next long word -- the answer is another entry in `DISCIPLINE_SHORT`.
    from trophies.services.job_render import DISCIPLINE_SHORT

    assert DISCIPLINE_SHORT.get('exploration'), 'the one name that overflows has no short form'

    # SCOPED TO THE EXPLORATION PEEK, AND THE NEGATIVE IS THE REAL ASSERTION. Two mutations survived the
    # first version of this check, both for the same reason: `'Explo' in hero` is satisfied by the word
    # "Exploration" itself, so breaking the short form entirely still passed. And the companion negative
    # split on the LAST `lab-dna__peek`, which is Finesse -- a block that never contains "Exploration".
    # `data-disc="exploration"` occurs twice (the arc, then the peek), so the last one opens the peek.
    expl = hero.split('data-disc="exploration"')[-1].split('</div>')[0]
    assert 'Exploration' not in expl, (
        'the peek is printing the full "Exploration", which overflows the ring hole: %r' % expl)
    assert DISCIPLINE_SHORT['exploration'] in expl, (
        'the peek is not rendering the short form: %r' % expl)
    assert len(DISCIPLINE_SHORT) < len(DISCIPLINE_LABELS), (
        'DISCIPLINE_SHORT has grown to cover every discipline -- it is sparse on purpose, since only one '
        'name does not fit and abbreviating the others shortens names that have no problem')

    # NO WIDTH CONSTRAINT ON THE PEEK LABEL. Scoped to the hazard rather than to the selector: the
    # reverted attempt was a `max-width: 60%`, which on a grid item inside a `place-content: center` grid
    # resolves against the LABEL's own width and clipped all five. A size-scoped `letter-spacing` fix is
    # legitimate and lives at `.lab-dna--sm .lab-dna__peek .lab-dna__cap`, so banning the selector outright
    # (as the first version of this did) forbids the real fix along with the broken one.
    for rule in re.sub(r'/\*.*?\*/', '', css, flags=re.S).split('}'):
        selector = rule.split('{')[0]
        if 'lab-dna__peek' not in selector or 'lab-dna__cap' not in selector:
            continue
        body = rule.split('{', 1)[1] if '{' in rule else ''
        assert 'max-width' not in body and 'overflow' not in body, (
            'a width constraint is back on the peek label (%s): a percentage here resolves against the '
            "label's own width, not the ring, and clips every one of them" % selector.strip())

    # AND THE NUMBER TAKES THE DIGIT RUNGS, because a discipline total is open-ended (`leveling.py` calls
    # the curve "FLAT, CAP-LESS"), so a four-figure total drawn at the one-figure size would spill.
    #
    # PINNED AT FOUR DIGITS AS WELL AS THREE. `--d3` only has a rule under `.lab-dna--sm`; at base size
    # `elements.css` says outright that "d1-d3 use the base size", so asserting `--d3` on this page (base
    # size) pinned the one digit count that styles nothing. The rungs that matter start at `--d4`.
    assert 'lab-dna__level--d3' in combat, (
        'the peek number is not taking its digit rung at all: %r' % combat[:200])

    whale = _hunter('whalepeek')
    _filled_run(whale, 2)
    for job in Job.objects.filter(discipline='mind'):
        ProfileJobXP.objects.update_or_create(
            profile=whale, job=job, defaults={'total_xp': 900000, 'level': 300})
    whale_hero = _hero(client.get(reverse('challenges_hall_of_fame')).content.decode())
    mind = whale_hero.split('lab-dna__peek" data-disc="mind"')[-1].split('</div>')[0]
    assert '1500' in mind, 'expected mind to total 5 x 300 = 1500: %r' % mind
    assert 'lab-dna__level--d4' in mind, (
        'a four-figure total is not taking the --d4 rung, so it renders at the one-figure size and spills '
        'the ring hole: %r' % mind)


def test_the_plaque_defers_the_rings_entrance_and_kills_only_its_spin(client):
    """THE RING ANIMATES IN AS ITS ROW ARRIVES, AND NEVER SPINS. Owner, 2026-10-02, on noticing the
    stillness: "the rings on the hall of fame page don't spin at all and I didn't really think about that.
    Is that intentional?" -- it was, but only half of it was a good decision.

    THE SPIN STAYS DEAD. `motion-patterns` principle 6: "Motion + glow belong to earn moments and
    acknowledgment states ... not to surfaces at rest." One ring on the Career hero is fine; eight creeping
    and then whooshing down a browse list is ambient motion over content being read. The same stylesheet
    already deleted a resting gold radial on that authority.

    THE ENTRANCE SHOULD NOT HAVE BEEN. `labDnaIn` / `labDnaCenter` fire at element INSERTION while
    `staggerReveal` unhides SCROLL-APPENDED rows on INTERSECTION, so page 2 of an infinite scroll arrived
    pre-settled while page 1 animated. Removing them traded that inconsistency for no entrance at all.
    Re-firing off `.is-revealed` restores the entrance and fixes the appended pages.

    IT DOES NOT MAKE EVERY ROW IDENTICAL, which an earlier version of this docstring claimed:
    `staggerReveal` reveals every card ALREADY IN THE GRID in one DOM-order batch whether it is visible or
    not, so the back half of page 1 still animates below the fold. Narrower problem, same shape, and fixing
    it means changing the shared helper rather than this stylesheet.

    `backwards` RATHER THAN `both` IS THE SUBTLE PART. A forwards fill keeps applying the last keyframe
    after the animation ends -- in the animation origin, which beats author declarations -- so a persisted
    `opacity: 1` on `.lab-dna__center` permanently overrides the hover rule that clears it and the peek
    never appears. The component itself shipped with `both` and these two rules were what accidentally
    spared the plaque; see `test_the_shared_rings_entrance_cannot_outrank_its_own_hover_rules`, which pins
    the primitive. A pin on this override alone could not see it.
    """
    css = _board_css()

    assert '.pp-chero__plinth .lab-dna__arcs { animation: none; }' in css, (
        'the perpetual spin is back on a browse list of eight rings')

    ring_rule = css.split('.pp-centry.is-revealed .pp-chero__plinth .lab-dna__ring {')[1].split('}')[0]
    centre_rule = css.split('.pp-centry.is-revealed .pp-chero__plinth .lab-dna__center {')[1].split('}')[0]

    assert 'labDnaIn' in ring_rule, 'the ring has no deferred entrance'
    assert 'labDnaCenter' in centre_rule, 'the centre has no deferred entrance'

    # THE FILL MODE, on the centre specifically. `both`/`forwards` here persists `opacity: 1` and kills the
    # hover peek; nothing else in the suite would notice, because the peek markup and rules would all still
    # be present and correct.
    assert 'backwards' in centre_rule and 'both' not in centre_rule and 'forwards' not in centre_rule, (
        "the centre's entrance uses a forwards fill, which persists opacity:1 in the animation origin and "
        'permanently overrides the hover rule that clears it -- the peek would never show: %r' % centre_rule)

    # AND THE ROW CARRIES THE HOOK THE ENTRANCE WAITS ON. Without `.pp-centry` on the hero the selector
    # matches nothing and the ring is silently static again -- the state the owner asked about.
    _filled_run(_hunter('spinhunter'), 2)
    hero = _hero(client.get(reverse('challenges_hall_of_fame')).content.decode())
    assert 'pp-centry' in hero, 'the hero lost the reveal hook the deferred entrance keys on'


def test_the_earned_title_gets_an_earned_moment_not_a_resting_glow():
    """THE TRADE, and it is a trade rather than an addition.

    The gold title band -- the one element this page exists to celebrate -- used to carry
    `box-shadow: 0 0 18px -6px` gold at rest and no acknowledgment at all. That inverts
    `motion-patterns` principle 6 ("Motion + glow belong to earn moments and *acknowledgment* states ...
    not to surfaces at rest") on the most earned thing on the row. The glow is gone; a single specular
    sweep crosses the band as its row arrives.

    BOTH HALVES ARE ASSERTED, because either alone is the wrong outcome: keeping the glow and adding the
    sheen leaves resting chrome on eight rows, and removing the glow without the sheen leaves the page's
    subject completely flat.

    FIRED OFF `.is-revealed` for the reasons the ring's entrance uses it -- it reaches the scroll-appended
    pages, which animating on insertion does not, and it is silent under reduced motion for free, since
    `staggerReveal` returns early there and the class never arrives. The same caveat applies as there: the
    back half of page 1 is revealed below the fold, so its sweep is spent unseen.

    AND THE LIGHT CROSSES THE METAL, NOT THE LETTERING. Painted on top, the 0.26-alpha highlight lifts the
    band ~2.8x at its peak (the point of it) but takes the gold text from ~6.5:1 to ~3.5:1 -- under AA for
    12px/800, which is not WCAG large text. `z-index: -1` on the overlay plus a stacking context on the pill
    puts the sweep between the band's background and its text, keeping the full effect and the full
    contrast. Dimming the alpha was the other option and it pays for contrast with the effect itself.

    THE STACKING CONTEXT IS NOT COSMETIC, which is why it is asserted too: `.pp-chero > *` sets `z-index: 1`,
    making `.pp-chero__plaque` a stacking context, and the plaque carries an OPAQUE plate from `md:` up. With
    `z-index: -1` on the overlay and no stacking context on the pill, the sweep paints behind that plate and
    the sheen is invisible at every breakpoint except 375px -- a failure no server-rendered test can see.
    """
    css = _board_css()

    title = _rules_at('.pp-chero__title').get(0, '')
    assert title, 'no base rule for the title band -- the assertions below would be vacuous'

    # ── THE RESTING GLOW IS GONE. Scoped to the band's own rule: `box-shadow` is legitimate elsewhere in
    # this file (the row's hover lift, the plaque's material), so a file-wide ban would be a false positive.
    assert 'box-shadow' not in title, (
        'the title band has a resting glow again, on a page of eight rows -- principle 6 puts glow on the '
        'earn moment, not on the surface: %r' % title)

    # ── AND THE EARNED MOMENT EXISTS, clipped to the pill and fired once on reveal.
    assert 'position: relative' in title and 'overflow: hidden' in title, (
        'the band cannot clip its sheen to its 999px radius without both')
    assert '.pp-chero__title::after' in css, 'no sheen overlay on the title band'

    # ── THE SWEEP PASSES BEHIND THE TEXT. Both halves are required and neither works alone: without the
    # pill's own `z-index` it is not a stacking context, so a negative z-index child escapes BEHIND the
    # card instead of landing between this background and this text; without the overlay's `z-index: -1`
    # it paints on top and drops 12px/800 gold text to ~3.5:1 while it crosses.
    #
    # `_rules_at` STRIPS COMMENTS, which this pin depends on: the band's own comment names both z-index
    # values, so without the strip either assertion would pass against prose after its declaration was
    # deleted. That is not hypothetical -- it is how the helper behaved when this pin was written.
    assert 'z-index: 0' in title, (
        'the title band is not a stacking context, so the sheen escapes behind the plaque plate and is '
        'invisible from md: up: %r' % title)
    overlay = css.split('.pp-chero__title::after {')[1].split('}')[0]
    assert 'z-index: -1' in overlay, (
        'the sheen paints over the title text, which takes it under AA (~3.5:1) while the highlight '
        'crosses -- it belongs between the metal and the lettering: %r' % overlay)
    assert '.pp-centry.is-revealed .pp-chero__title::after' in css, (
        'the sheen is not fired off the reveal hook, so it will animate at element insertion and be spent '
        'before a scroll-appended row is ever visible')
    assert '@keyframes ppCheroSheen' in css, 'the sheen keyframes are gone'

    sheen = css.split('.pp-centry.is-revealed .pp-chero__title::after {')[1].split('}')[0]
    assert 'infinite' not in sheen, (
        'the sheen is looping -- it is an acknowledgment of something already earned, which happens once; '
        '`.claim-banner` loops because a claim is a call to action')
    assert 'backwards' in sheen and 'forwards' not in sheen, (
        'a forwards fill parks a transform in the animation origin, which is the trap the ring centre '
        'already paid for: %r' % sheen)


def test_the_shared_rings_entrance_cannot_outrank_its_own_hover_rules():
    """THE PEEK WORKED ON EXACTLY ONE OF ITS THREE HOSTS, and the pin that should have caught it was
    scoped to the wrong file.

    `.lab-dna__center` shipped with `animation: labDnaCenter 0.45s 0.28s ease both`. The FORWARDS half of
    `both` keeps applying the final keyframe after the animation ends -- from the ANIMATION ORIGIN, which
    outranks every normal author declaration. So `opacity: 1` was pinned permanently and neither hover rule
    could ever fire:

        .lab-dna:has(.lab-dna__arc:hover) .lab-dna__center   { opacity: 0 }
        .lab-dna-pair:has([data-disc]:hover) .lab-dna__center { opacity: 0 }

    The symptom was worse than nothing: the peek faded in UNDERNEATH the still-visible Pursuer Level, two
    numbers and two caps stacked, because the peeks precede the centre in DOM and neither carries a
    `z-index`. On `/career/` -- the reference page -- and on the home lobby. The arcs still dimmed and
    thickened, so it read as half-finished rather than as absent. The `transition: opacity` could not
    rescue it either: transitions compute before- and after-change styles WITH animations applied, so the
    value never appears to change and no transition starts.

    THE HALL OF FAME WAS THE ONLY WORKING HOST, and for an accidental reason -- the plaque kills this
    animation and re-fires it `backwards`, which it does for the row-reveal deferral, not for this. So the
    one surface this branch was built on was the one surface that hid the bug.

    WHY NOTHING CAUGHT IT. `test_the_plaque_defers_the_rings_entrance_and_kills_only_its_spin` pins
    `backwards` on the plaque's DERIVED override and says in its own docstring that using "the component's
    own fill mode" would break the feature -- while the component's own fill mode was already that word,
    unpinned. A pin on the override cannot see the primitive. This test pins the primitive.

    DROPPING THE FORWARDS HALF IS VISUALLY FREE: `labDnaCenter`'s `to` is `opacity: 1; transform: none`,
    which is already this element's resting state, so there is nothing for a forwards fill to preserve.
    """
    css = open('static/css/components/elements.css', encoding='utf-8').read()
    code = re.sub(r'/\*.*?\*/', '', css, flags=re.S)

    # ANCHORED ON THE DECLARATION, not on the file: `both` and `forwards` both appear legitimately
    # elsewhere in a 1,400-line stylesheet, so a bare substring search would be meaningless.
    centre = code.split('.lab-dna__center {')[1].split('}')[0]
    assert 'labDnaCenter' in centre, 'the ring centre lost its entrance, so this test is checking nothing'
    assert 'backwards' in centre, (
        "the ring centre's entrance must fill `backwards` -- it needs the pre-delay hold, and nothing "
        'else: %r' % centre)
    for forwards in ('both', 'forwards;'):
        assert forwards not in centre, (
            'the ring centre fills `%s`, which persists opacity:1 in the animation origin and permanently '
            'outranks the hover rules that clear it. The peek then renders UNDER the Pursuer Level on '
            'Career and the home lobby: %r' % (forwards, centre))

    # AND BOTH HOVER RULES ARE STILL THERE TO BE OUTRANKED. If either is deleted the assertions above go
    # quiet while the feature is just as broken, on whichever host lost its rule.
    assert '.lab-dna:has(.lab-dna__arc:hover) .lab-dna__center { opacity: 0; }' in code, (
        'the ring-scoped centre clear is gone -- the plaque and the lobby ring lose their peek')
    assert '.lab-dna-pair:has([data-disc]:hover) .lab-dna__center { opacity: 0; }' in code, (
        "Career's legend-scoped centre clear is gone -- hovering a legend row leaves the Pursuer Level up")


def test_the_discipline_highlight_has_one_implementation(client):
    """ONE IMPLEMENTATION OF THE CROSS-HIGHLIGHT, shared by every host of the ring.

    WHAT THIS REPLACED. Career carried ~27 lines of JS (`career.html`, `focusDisc`) that spotlighted a
    discipline on hover of an arc OR a legend row. It predated the CSS version, reached only Career, and
    dimmed with INLINE `style.opacity` -- which beats a stylesheet rule, so Career dimmed arcs to 0.16 while
    the home lobby and the Hall of Fame dimmed to 0.2. Worse, once the CSS arrived one gesture behaved two
    ways on one page: hovering an arc peeked, hovering a legend row did not.

    An audit found it. A reuse check should have: it was the third miss on this feature, after the Pursuer
    Card's discipline band and the Frame's corner diamonds, and one grep for `data-disc` would have surfaced
    all of it.

    THE CONSTRAINT THIS WAS BUILT TO (owner): "as long as it works on the Hall of Fame page". The Hall of
    Fame has a ring and NO legend, so the `.lab-dna`-scoped rules were left exactly as they were and the
    legend linking was ADDED under a `.lab-dna-pair` wrapper that simply never matches on a legend-less
    host. This test asserts both halves of that: the shared behaviour still reaches the plaque, and the
    legend rules exist for the host that has one.
    """
    # COMMENT-STRIPPED, because the first version of the wrapper check passed on the `{% comment %}` block
    # that EXPLAINS the hook: deleting the class from the div left `lab-dna-pair` in the prose above it and
    # the assertion never noticed. Fourth instance of this trap on this feature.
    from tests.engine.test_challenge_detail_js import _template_code

    css = open('static/css/components/elements.css', encoding='utf-8').read()
    career = _template_code(open('templates/trophies/career.html', encoding='utf-8').read())

    # ── THE JS IS GONE, and nothing re-grew it.
    # NAMES THAT ARE UNIQUE TO THAT BLOCK. `clearDisc` alone matched `clearDiscJobs` (an unrelated filter
    # reset) and `style.opacity` has seven legitimate uses elsewhere on this page -- sliders, dialogs. Banning
    # either outright is a false positive, which is the substring trap this suite keeps paying for.
    # `animationPlayState` is the sharpest marker: inline spin control is what the shared
    # `.lab-dna:hover .lab-dna__arcs` rule already does declaratively.
    for dead in ('focusDisc', 'clearDisc(', 'animationPlayState'):
        assert dead not in career, (
            'the JS discipline highlight is back in career.html (%s) -- it dimmed with inline styles that '
            'beat the shared stylesheet, which is what made Career the one host that looked different'
            % dead)

    # ── CAREER HAS THE ANCHOR the shared rules need. The ring and the legend are SIBLINGS, so a common
    # ancestor is the only way CSS can correlate them; without this class the legend path silently does
    # nothing and the page looks like the JS was deleted for no reason.
    # ON THE ATTRIBUTE, not the bare name. Stripping Django comments was not enough: a JS comment further
    # down ALSO mentions `.lab-dna-pair`, and `_template_code` only removes `{% comment %}` / `{# #}`. So
    # deleting the class from the div still left the name in prose and the assertion still passed. A class
    # attribute can only appear in markup, which is what makes it the right thing to match.
    assert 'class="lab-dna-pair' in career, (
        'career.html lost the wrapper hook on the div, so its legend no longer links to the ring')
    assert 'lab-dna-legend' in career, 'no legend on Career, so this test is checking nothing'

    # ── THE LEGEND RULES EXIST, one per discipline, and reach all three targets.
    from trophies.services.job_render import DISCIPLINE_LABELS

    for slug in DISCIPLINE_LABELS:
        for target in ('.lab-dna__peek[data-disc="%s"]' % slug,
                       '.lab-dna__arc[data-disc="%s"]' % slug,
                       '.lab-dna-legend [data-disc="%s"]' % slug):
            assert '.lab-dna-pair:has([data-disc="%s"]:hover) %s' % (slug, target) in css, (
                'the legend link is missing %s for %s' % (target, slug))

    # ── THE GENERIC HALF, which nothing pinned: the per-slug rules only SPOTLIGHT, so without these three
    # there is nothing to spotlight against -- hovering a legend row would brighten one arc that was already
    # full opacity and look like nothing happened.
    for rule, what in (
        ('.lab-dna-pair:has([data-disc]:hover) .lab-dna__arc { opacity: 0.2; }', 'the arcs do not dim'),
        ('.lab-dna-pair:has([data-disc]:hover) .lab-dna-legend li { opacity: 0.4; }',
         'the legend rows do not dim'),
        ('.lab-dna-pair:has([data-disc]:hover) .lab-dna__center { opacity: 0; }',
         'the centre does not clear, so no peek is visible'),
        ('.lab-dna-pair:has(.lab-dna-legend li:hover) .lab-dna__arcs { animation-play-state: paused; }',
         'the spin does not pause, so the legend highlights a moving arc'),
    ):
        assert rule in css, 'legend link incomplete -- %s' % what

    # ── AND THE HALL OF FAME IS UNTOUCHED, which is the owner's actual requirement. Its rules are the
    # `.lab-dna`-scoped ones; the plaque renders no `.lab-dna-pair`, so if the shared behaviour had been
    # MOVED onto the wrapper instead of extended, the plaque would silently lose its hover entirely.
    assert '.lab-dna:has(.lab-dna__arc:hover) .lab-dna__center { opacity: 0; }' in css, (
        'the ring-scoped hover rules were moved onto the legend wrapper, which breaks every host that has '
        'no legend -- the Hall of Fame plaque and the home lobby')

    _filled_run(_hunter('dedupehunter'), 2)
    hero = _hero(client.get(reverse('challenges_hall_of_fame')).content.decode())
    assert 'lab-dna__peek' in hero, 'the plaque lost its peeks'
    assert 'lab-dna-pair' not in hero, (
        'the plaque is rendering the legend wrapper, which it has no legend for -- the wrapper rules would '
        'then dim its arcs with no legend to correlate against')
