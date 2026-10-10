"""The run's own page: who can read it, what a square says, and what it costs to draw 26 of them.

THREE THINGS THIS FILE IS ACTUALLY FOR.

**Visibility**, because this is the first PUBLIC surface in the feature and the rule is inverted from
My Challenges: anyone may read a run, except a hidden one, which only its owner may read. A 404 rather
than a 403, so an id cannot be used to confirm that a hidden run exists.

**The snapshot**, which is the whole architectural bet of `ChallengeSlot` and is invisible until staff
touch the catalogue. A finished square must say what it said the day it was finished, so the test
renames the contract AFTER assigning it and demands the page still show the old name. Nothing else in
the suite can catch a reader that reached for `slot.contract.name`, because in every other test the two
strings are identical.

**Flatness**, which is not a nice-to-have here. A run is 26 squares and every filled one wants cover
art, which is exactly the shape CLAUDE.md's `raw_response` rule exists for -- so the cost is pinned as
an EXACT number and then re-measured with the squares filled, because a per-square cover lookup passes
every functional test in this file while quietly being the May 2026 OOM again.
"""
import re

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from challenges.models import CHALLENGE_TYPE_AZ, CHALLENGE_TYPE_JOBS, Challenge
from challenges.services import challenge_service as svc
from challenges.services import slot_render
from challenges.views import ChallengeDetailView
from tests.factories import ConceptFactory, GameFactory, IGDBMatchFactory, ProfileFactory, UserFactory
from trophies.models import Contract, Game, Job

pytestmark = pytest.mark.django_db

_SEQ = {'n': 0}


def _hunter(client=None):
    """A hunter, optionally signed in on `client`.

    No `linked` parameter: nothing in this file needs an unlinked profile (the link gate belongs to My
    Challenges, which owns the write doors), and `ProfileFactory` already defaults `is_linked=True`. It
    was here on the chance it would be wanted.
    """
    user = UserFactory()
    profile = ProfileFactory(user=user, user_is_premium=True)
    if client is not None:
        client.force_login(user)
    return profile


def _contract(name):
    """A live contract with a real member concept, so cover resolution has something to resolve."""
    _SEQ['n'] += 1
    contract = Contract.objects.create(
        name=name, slug=f"{name.lower().replace(' ', '-')}-{_SEQ['n']}",
        is_live=True, igdb_id=810_000 + _SEQ['n'])
    concept = ConceptFactory(anchor_migration_completed_at=timezone.now())
    IGDBMatchFactory(concept=concept, igdb_id=contract.igdb_id, status='accepted')
    GameFactory(concept=concept)
    return contract


def _joined_at(profile, when):
    """Move the account's creation date, which is what the importer measures against."""
    profile.user.date_joined = when
    profile.user.save(update_fields=['date_joined'])
    return profile


def _platted_at(profile, contract, when):
    """Completion as the TROPHY DATA records it, which is the date the importer reads."""
    from tests.factories import EarnedTrophyFactory, TrophyFactory
    from trophies.models import EarnedContract
    concept = ConceptFactory(anchor_migration_completed_at=timezone.now())
    IGDBMatchFactory(concept=concept, igdb_id=contract.igdb_id, status='accepted')
    game = GameFactory(concept=concept)
    plat = TrophyFactory(game=game, trophy_type='platinum')
    EarnedTrophyFactory(profile=profile, trophy=plat, earned_date_time=when)
    EarnedContract.objects.create(profile=profile, contract=contract, has_platinum=True,
                                  platinum_reached_at=timezone.now())


def _az_run(profile):
    return svc.start(profile, CHALLENGE_TYPE_AZ)


def _cards(resp):
    """Every card of a run, flattened out of its groups.

    The page draws GROUPS now -- one labelled shelf per discipline on a jobs run, and a single unlabelled
    group for A-Z, whose page is therefore unchanged. Most of these tests are about a SQUARE rather than
    about the grouping, so they read the flat list through here and stay about what they were about.
    """
    return [card for group in resp.context['groups'] for card in group['cards']]


def _url(challenge):
    return reverse('challenge_detail', args=[challenge.id])


# ── who may read it ──────────────────────────────────────────────────────────────────────────────

def test_anyone_may_read_a_run(client):
    """PUBLIC, and deliberately so: the Hall of Fame is the point of the feature, and a link that only
    works for its owner is not something you can show anybody."""
    challenge = _az_run(_hunter())

    assert client.get(_url(challenge)).status_code == 200


def test_a_hidden_run_is_a_404_for_a_stranger(client):
    """404 RATHER THAN 403. A 403 would confirm the run exists, which is the one thing hiding it was
    meant to stop."""
    challenge = _az_run(_hunter())
    svc.hide(challenge, challenge.profile)

    assert client.get(_url(challenge)).status_code == 404


def test_a_hidden_run_is_a_404_for_another_signed_in_hunter(client):
    """The signed-in branch of `get_queryset` widens the query for the VIEWER's own runs, and the easy
    way to write that is a filter that widens it for everybody's."""
    owner = _hunter()
    challenge = _az_run(owner)
    svc.hide(challenge, owner)
    _hunter(client)

    assert client.get(_url(challenge)).status_code == 404


def test_the_owner_still_reaches_their_hidden_run(client):
    """A hidden run stays openable by its owner.

    NOT because the UI links to it -- an earlier version of this docstring said "the card it sits on links
    here", and that is false. `my_challenges.html` emits the detail link only for an ACTIVE run; the
    resumable card renders a Resume form with no link, and hidden runs are excluded from the finished
    list, so the only routes in are a bookmark and browser history.

    Those are the reasons it must work. A hunter who had the page open, hid the run in another tab, and
    reloaded should not be handed a 404 for something that still exists and is still theirs. Whether the
    resumable card should also offer a way to look before resuming is a product question, flagged rather
    than assumed.
    """
    profile = _hunter(client)
    challenge = _az_run(profile)
    svc.hide(challenge, profile)

    assert client.get(_url(challenge)).status_code == 200


def test_a_hidden_run_is_not_indexed(client):
    """An owner can reach it, so it is served -- and a crawler that had already seen it must be told to
    drop it rather than keep serving a page its owner took down."""
    profile = _hunter(client)
    challenge = _az_run(profile)
    svc.hide(challenge, profile)

    body = client.get(_url(challenge)).content.decode()

    assert 'noindex' in body


def test_a_visible_run_is_indexed(client):
    challenge = _az_run(_hunter())

    body = client.get(_url(challenge)).content.decode()

    assert 'noindex' not in body


# ── the owner's affordances ──────────────────────────────────────────────────────────────────────

def test_a_signed_in_hunter_is_not_the_owner_of_somebody_elses_run(client):
    """THE CASE THAT WAS MISSING. The previous version of this test used an anonymous client, which made
    it a duplicate of the AnonymousUser test below and left `viewer is not None and viewer.id !=
    challenge.profile_id` -- the only branch where `is_owner` has a real comparison to get wrong --
    untested."""
    challenge = _az_run(_hunter())
    _hunter(client)

    context = client.get(_url(challenge)).context

    assert context['is_owner'] is False
    assert context['can_edit'] is False


def test_the_owner_of_an_unfinished_run_may_edit(client):
    profile = _hunter(client)

    context = client.get(_url(_az_run(profile))).context

    assert context['is_owner'] is True
    assert context['can_edit'] is True


def test_a_finished_run_is_read_only_even_for_its_owner(client):
    """Completed squares lock, so a run whose every square is complete has nothing left to change. The
    page says so rather than rendering controls that would refuse."""
    profile = _hunter(client)
    challenge = _az_run(profile)
    Challenge.objects.filter(pk=challenge.pk).update(is_complete=True, completed_at=timezone.now())

    context = client.get(_url(challenge)).context

    assert context['is_owner'] is True
    assert context['can_edit'] is False


def test_an_anonymous_visitor_does_not_crash_the_owner_check(client):
    """`request.user.profile` raises on AnonymousUser, and `is_owner` is computed for every viewer."""
    challenge = _az_run(_hunter())

    assert client.get(_url(challenge)).context['is_owner'] is False


# ── the grid ─────────────────────────────────────────────────────────────────────────────────────

def test_an_az_run_draws_one_square_per_letter(client):
    challenge = _az_run(_hunter())

    cards = _cards(client.get(_url(challenge)))

    assert [c['label'] for c in cards] == list('ABCDEFGHIJKLMNOPQRSTUVWXYZ')


def test_a_jobs_run_labels_its_squares_with_job_NAMES(client):
    """`card-shark`, not `Card Shark`, is what a slug-only card would render -- and it would look like a
    content bug rather than a missing catalogue lookup."""
    challenge = svc.start(_hunter(), CHALLENGE_TYPE_JOBS)

    labels = {c['label'] for c in _cards(client.get(_url(challenge)))}

    # Equality against the catalogue is the whole assertion. A second `labels & slugs` check looked like
    # a stronger guarantee and was implied by this one -- it could only ever fire if one job's NAME
    # equalled another job's slug.
    assert labels == set(Job.objects.values_list('name', flat=True))


def test_only_a_jobs_run_pays_for_the_icon_sprite(client):
    """25 unique glyphs referenced up to twice each is what the sprite is for; an A-Z run references
    none of them, and an unused inline sprite is bytes on the wire for nothing."""
    az = _az_run(_hunter())
    jobs = svc.start(_hunter(), CHALLENGE_TYPE_JOBS)

    assert 'jobicon-' not in client.get(_url(az)).content.decode()
    assert 'jobicon-' in client.get(_url(jobs)).content.decode()


def test_an_empty_square_is_empty_and_a_filled_one_is_not(client):
    profile = _hunter(client)
    challenge = _az_run(profile)
    svc.assign(challenge, profile, 'A', _contract('Astro Bot'))

    cards = {c['key']: c for c in _cards(client.get(_url(challenge)))}

    assert cards['A']['is_filled'] is True
    assert cards['A']['game_name'] == 'Astro Bot'
    assert cards['B']['is_filled'] is False
    assert cards['B']['game_name'] == ''


def test_a_filled_square_renders_its_game_name(client):
    """Scoped to the SQUARE's own element rather than to the page, because the run's auto-name and the
    breadcrumb both put strings in the body and a bare `in body` would pass on either."""
    profile = _hunter(client)
    challenge = _az_run(profile)
    svc.assign(challenge, profile, 'V', _contract('Vagrant Story'))

    body = client.get(_url(challenge)).content.decode()

    assert 'pp-csq__name" title="Vagrant Story">Vagrant Story<' in body


def test_a_completed_square_is_marked_as_such(client):
    profile = _hunter(client)
    challenge = _az_run(profile)
    slot = svc.assign(challenge, profile, 'K', _contract('Katamari Damacy'))
    svc.mark_slot_completed(slot)

    cards = {c['key']: c for c in _cards(client.get(_url(challenge)))}

    assert cards['K']['is_completed'] is True
    assert cards['J']['is_completed'] is False


def test_a_completed_square_carries_a_glyph_not_only_a_colour(client):
    """State must survive being read in greyscale, and `.pp-csq--done` is a ring colour on its own."""
    profile = _hunter(client)
    challenge = _az_run(profile)
    slot = svc.assign(challenge, profile, 'K', _contract('Katamari Damacy'))

    before = client.get(_url(challenge)).content.decode()
    svc.mark_slot_completed(slot)
    after = client.get(_url(challenge)).content.decode()

    assert 'pp-csq__check' not in before
    assert 'pp-csq__check' in after


# ── the snapshot, which is the point of the model ────────────────────────────────────────────────

def test_a_square_shows_the_name_it_was_ASSIGNED_not_the_live_one(client):
    """THE SNAPSHOT TEST, and the only one in the suite that can fail for a reader that reached for
    `slot.contract.name`: everywhere else the two strings are the same.

    Staff renames are routine -- `Contract.name` is itself a snapshot of the member concept's title at
    creation, and re-anchoring or a title correction changes it. A finished square must keep saying what
    it said, and for an A-Z run the stakes are higher than cosmetic: the letter a square belongs to is
    derived from that name, so a rename that crosses letters would otherwise put the wrong game under
    the wrong letter.
    """
    profile = _hunter(client)
    challenge = _az_run(profile)
    contract = _contract('Bloodborne')
    svc.assign(challenge, profile, 'B', contract)

    Contract.objects.filter(pk=contract.pk).update(name='Zzz Renamed By Staff')

    cards = {c['key']: c for c in _cards(client.get(_url(challenge)))}

    assert cards['B']['game_name'] == 'Bloodborne'
    assert 'Zzz Renamed By Staff' not in client.get(_url(challenge)).content.decode()


def test_a_square_survives_its_contract_being_deleted(client):
    """`contract` is SET_NULL precisely so a catalogue deletion cannot take a hunter's finished run with
    it. The square keeps its name and its state; only the cover goes."""
    profile = _hunter(client)
    challenge = _az_run(profile)
    contract = _contract('Demon Souls')
    slot = svc.assign(challenge, profile, 'D', contract)
    svc.mark_slot_completed(slot)

    contract.delete()

    cards = {c['key']: c for c in _cards(client.get(_url(challenge)))}

    assert cards['D']['game_name'] == 'Demon Souls'
    assert cards['D']['is_completed'] is True
    assert cards['D']['cover'] is None


# ── progress ─────────────────────────────────────────────────────────────────────────────────────

def test_progress_is_a_whole_percentage_of_the_run(client):
    profile = _hunter(client)
    challenge = _az_run(profile)
    for key, name in (('A', 'Ape Escape'), ('B', 'Bloodborne'), ('C', 'Control')):
        svc.mark_slot_completed(svc.assign(challenge, profile, key, _contract(name)))

    # 3 of 26 is 11.53...%, so this also pins that the arithmetic happens in the VIEW. Chained `add`
    # filters in the template produced nonsense for exactly this expression.
    assert client.get(_url(challenge)).context['progress'] == 12


def test_progress_refuses_to_divide_by_a_zero_slot_run(rf):
    """THE ZERO IS SET IN MEMORY ONLY, and the two ways that did not work are the reason it is written
    this way.

    `update(total_slots=0)` is rejected by `challenge_total_slots_positive`, which is Postgres being
    right and the test being impossible to set up. Building an UNSAVED `Challenge` instead got further
    and then failed in `slot_cards`, because a reverse relation needs a primary key -- so a zero-slot run
    cannot be handed to this view from either direction.

    Which establishes what the guard in `progress` really is: not a reachable state, but arithmetic
    declining to trust a column constraint from inside an expression that cannot check one. So the run is
    saved normally and only the attribute the view reads is zeroed. If the constraint is ever relaxed --
    a variable-length challenge type would have to relax it -- this is the test that says the page still
    renders instead of 500ing.
    """
    profile = ProfileFactory(user_is_premium=True)
    challenge = _az_run(profile)
    request = rf.get(_url(challenge))
    request.user = profile.user

    challenge.total_slots = 0
    challenge.completed_count = 0

    view = ChallengeDetailView()
    view.request, view.kwargs, view.object = request, {'challenge_id': challenge.id}, challenge

    assert view.get_context_data()['progress'] == 0


# ── flatness, which is the whole reason `slot_render` exists ──────────────────────────────────────

def test_the_pages_query_cost_does_not_scale_with_its_squares(rf):
    """AN EXACT COUNT, measured on `get_context_data` rather than through the client, for the reason
    `test_my_challenges.py` records: a request-level budget cannot be both tight and stable while it is
    also measuring four site-wide context processors that cache for 60s in locmem.

    MEASURED TWICE, and the second measurement is the real test. A per-square cover lookup passes every
    other test in this file; it shows up only as this number moving when the squares fill.
    """
    profile = ProfileFactory(user_is_premium=True)
    challenge = _az_run(profile)
    request = rf.get(_url(challenge))
    request.user = profile.user

    def cost():
        view = ChallengeDetailView()
        view.request, view.kwargs, view.object = request, {'challenge_id': challenge.id}, challenge
        with CaptureQueriesContext(connection) as captured:
            view.get_context_data()
        return len(captured.captured_queries)

    # An EMPTY A-Z run: the slots, one COUNT for `history_is_open`, and one COUNT for the reward panel's
    # title ordinal. No contracts means no cover resolution at all, and no job catalogue because A-Z keys are
    # their own labels.
    #
    # THE THIRD QUERY IS THE REWARD PANEL, and it is paid by EVERY reader including a visitor -- unlike the
    # importer's gate above it. That is deliberate rather than an oversight: "what is this run worth" is
    # exactly the question a Hall of Fame visitor is asking, so the answer cannot be owner-only. It is one
    # indexed COUNT over this hunter's completed runs of this type, which is what decides Champion vs Legend.
    #
    # A JOBS RUN PAYS THREE MORE on top of that, all constant and all bounded by the run rather than by the
    # hunter: the claimable keys, the finished squares, and a SECOND read of the job catalogue (`key_atoms`
    # is called by `slot_groups` for the board and again by `summary` for the panel's chips). That duplicate
    # is named rather than hidden -- it is 25 rows and the alternative was threading atoms through two
    # unrelated call sites -- and it is the first thing to remove if this page ever needs to get cheaper.
    #
    # THE SECOND QUERY IS THE HISTORY IMPORTER'S GATE, and what it is NOT is the expensive question. It asks
    # "has this hunter finished an A-Z run", which is one indexed COUNT; asking "is anything importable"
    # would mean the date read over trophy data on every view of the page. It is paid only by an OWNER
    # looking at their own A-Z run -- `can_edit` and the type check both short-circuit before it -- so a
    # visitor's page, which is most reads once the Hall of Fame exists, still costs 1.
    #
    # ONE MORE CAVEAT WORTH STATING: these figures exclude one query because
    # `ProfileFactory(user=<instance>)` seeds the reverse one-to-one cache on the user, so `self._viewer()`
    # costs nothing. On a real request `request.user.profile` is a query, so every number here is one higher.
    # The FLATNESS below is unaffected and is the property this test exists for -- but calling this an exact
    # count without the caveat would be exact by accident.
    assert cost() == 3

    empty = cost()
    for key, name in (('A', 'Ape Escape'), ('B', 'Bloodborne'), ('C', 'Control'),
                      ('D', 'Dark Souls'), ('E', 'Elden Ring')):
        svc.assign(challenge, profile, key, _contract(name))
    challenge.refresh_from_db()
    filled_five = cost()

    # Five covers cost a FIXED batch -- membership, then the covers -- not five lookups.
    assert filled_five > empty
    for key, name in (('F', 'Fallout'), ('G', 'Gravity Rush'), ('H', 'Hades'),
                      ('I', 'Ico'), ('J', 'Journey')):
        svc.assign(challenge, profile, key, _contract(name))
    challenge.refresh_from_db()

    assert cost() == filled_five, 'doubling the filled squares changed the query count'


def test_a_jobs_run_pays_exactly_one_query_for_its_catalogue():
    """25 squares, 25 job names, one query. The obvious wrong version resolves `Job` per square, which
    is 25 queries that no functional test notices."""
    profile = ProfileFactory(user_is_premium=True)
    challenge = svc.start(profile, CHALLENGE_TYPE_JOBS)

    with CaptureQueriesContext(connection) as captured:
        cards = slot_render.slot_cards(challenge)

    assert len(cards) == 25
    assert len(captured.captured_queries) == 2, 'expected the slots plus the job catalogue'


def test_the_cover_query_does_not_drag_the_igdb_blob_along():
    """CLAUDE.md's rule, pinned rather than trusted: `raw_response` is the ~30 KB IGDB blob that no cover
    template reads and that triggered the May 2026 web-server OOM when concurrent renders piled up the
    join payload. `cover_games_for` defers it; this fails if a future edit stops reusing it.

    SCOPED TO THE ONE QUERY THAT COULD CARRY IT, and the two versions that were not are the reason.

    Joining every query into one string and asserting `'raw_response' not in sql` is vacuous: it passes
    just as happily when no cover was resolved at all. Adding `assert 'igdb_match' in sql` was meant to
    fix that and did not -- it PASSED, for the wrong reason. Django emits table names, not relation names,
    so the cover query joins `"trophies_igdbmatch"`, which does not contain the substring `igdb_match`.
    What supplied it was the MEMBERSHIP query, whose `values('igdb_match__igdb_id')` produces the column
    alias `AS "igdb_match__igdb_id"` -- a query that selects two integer columns and could never have held
    the blob. So the guard was satisfied by a query incapable of failing it, which is verbatim the trap
    `test_gamelists_actions` records fixing.

    The cover query is identifiable: it is the one selecting `FROM "trophies_game"` while joining
    `"trophies_igdbmatch"`. Find that query, assert it exists, and check only it.
    """
    profile = ProfileFactory(user_is_premium=True)
    challenge = _az_run(profile)
    svc.assign(challenge, profile, 'A', _contract('Ape Escape'))
    challenge.refresh_from_db()

    with CaptureQueriesContext(connection) as captured:
        slot_render.slot_cards(challenge)

    cover_queries = [q['sql'] for q in captured.captured_queries
                     if 'FROM "trophies_game"' in q['sql'] and 'trophies_igdbmatch' in q['sql']]

    assert len(cover_queries) == 1, (
        'expected exactly one Game-joining-IGDBMatch query; found %d. With none, the assertion below '
        'proves nothing.' % len(cover_queries))
    assert 'raw_response' not in cover_queries[0]


def test_a_hidden_run_is_read_only_for_its_owner(client):
    """THE BUG THIS FILE MISSED FIRST TIME. `can_edit` was `is_owner and not is_complete`, so the owner of
    a hidden, unfinished run got `can_edit=True` -- and the template's own "this run is hidden" copy was
    unreachable dead code, because inside `is_owner and not can_edit` the condition reduced to
    `is_complete`. The dead copy was the evidence that the predicate was wrong.

    It matters beyond the message: `start` is what brings a hidden run back, so an editable hidden run
    would let a hunter fill squares on a run that is on nobody's page and in no hub.
    """
    profile = _hunter(client)
    challenge = _az_run(profile)
    svc.hide(challenge, profile)

    context = client.get(_url(challenge)).context

    assert context['is_owner'] is True
    assert context['can_edit'] is False


def test_the_owner_of_a_hidden_run_is_told_how_to_get_it_back(client):
    """The note that could never render. Scoped to its own sentence rather than to a word, because
    "hidden" also appears in the header badge a few lines above it."""
    profile = _hunter(client)
    challenge = _az_run(profile)
    svc.hide(challenge, profile)

    body = client.get(_url(challenge)).content.decode()

    assert 'This run is hidden' in body
    assert 'to bring it back with its progress' in body


def test_a_stranger_is_told_nothing_about_a_run_they_do_not_own(client):
    """Both notes are addressed to the owner. A visitor reading somebody's finished run has no squares to
    fix and no run to bring back."""
    owner = _hunter()
    challenge = _az_run(owner)
    Challenge.objects.filter(pk=challenge.pk).update(is_complete=True, completed_at=timezone.now())

    body = client.get(_url(challenge)).content.decode()

    assert 'This run is finished, so its' not in body


def test_the_trail_never_sends_a_reader_somewhere_they_cannot_go(client):
    """A PUBLIC page must not trail through login-gated pages. It did: Home / My Pursuit / My Challenges,
    both of the middle two login-gated, so an anonymous reader following the trail from a page that never
    asked them to sign in was bounced to a login screen -- and a signed-in visitor was sent to their OWN
    runs from a page about somebody else's.

    Asserted as "every crumb URL is reachable anonymously" rather than as a list of names, so it keeps
    holding if the trail is reworded.
    """
    challenge = _az_run(_hunter())

    crumbs = client.get(_url(challenge)).context['breadcrumb']

    urls = [c['url'] for c in crumbs if c.get('url')]
    assert urls, 'the trail has no links at all'
    for url in urls:
        assert client.get(str(url)).status_code == 200, '%s is not reachable anonymously' % url


@pytest.mark.parametrize('slug, expected', [
    # BOTH SHAPES, because they fail differently and the first version only tested one.
    # `order_by('slug').first()` is deterministically `architect`, which has no hyphen -- so it caught a
    # `'-' in key` implementation (that would have returned `architect` unchanged) but never exercised the
    # hyphen replacement the docstring advertised. A regression to `key.replace('_', ' ').title()` passed.
    ('architect', 'Architect'),
    ('card-shark', 'Card Shark'),
])
def test_a_deleted_job_leaves_a_square_with_a_readable_name(client, slug, expected):
    """A `Job` row deleted under an in-flight run is the one way the catalogue can move out from under a
    frozen slot. The square loses its icon and its discipline colour -- nothing can restore those -- but
    it must not start reading `card-shark` at a hunter as though that were a game's title."""
    profile = _hunter(client)
    challenge = svc.start(profile, CHALLENGE_TYPE_JOBS)
    Job.objects.filter(slug=slug).delete()

    cards = {c['key']: c for c in _cards(client.get(_url(challenge)))}

    assert cards[slug]['label'] == expected


def test_every_square_is_a_list_item(client):
    """26 sibling divs each ending in screen-reader text read as one undelimited run of prose. A list gives
    a boundary and a count.

    `role="list"` IS ASSERTED, not just the tag. Tailwind's preflight applies `list-style: none` to every
    `ul`, and WebKit strips the implicit `list` role from a list styled that way -- so on iOS Safari, the
    dominant screen-reader pairing for a 375px-first page, the bare `<ul>` announced nothing and this whole
    change bought zero. The role is the part that does the work.
    """
    challenge = _az_run(_hunter())

    body = client.get(_url(challenge)).content.decode()

    assert 'class="pp-csq-grid" role="list"' in body
    # SCOPED TO THE GRID. A bare `body.count('<li>')` returned 51: the navbar and the breadcrumb are lists
    # too, so the count was measuring the whole page's chrome. The `<li>` is now the grid item and the square
    # sits inside it -- that split is what lets an editable square be a real `<button>` -- so counting list
    # items inside the grid still counts squares, which is what this test is about.
    grid = body.split('pp-csq-grid', 1)[1].split('</ul>', 1)[0]
    assert grid.count('<li>') == 26
    assert grid.count('class="pp-csq ') == 26


def test_the_visible_text_of_a_filled_square_is_hidden_from_a_screen_reader(client):
    """Otherwise every filled square is announced twice: once as visible text, once as the `sr-only`
    sentence that exists to replace it."""
    profile = _hunter(client)
    challenge = _az_run(profile)
    svc.assign(challenge, profile, 'A', _contract('Astro Bot'))

    body = client.get(_url(challenge)).content.decode()

    assert '<span class="pp-csq__body" aria-hidden="true">' in body


def test_a_square_with_no_art_is_not_dimmed_by_the_text_scrim(client):
    """The scrim exists to make the name legible over ART. Over the no-art field -- itself a designed
    dark gradient -- it solved nothing and took that field's own glyph below the contrast floor."""
    profile = _hunter(client)
    challenge = _az_run(profile)
    contract = _contract('Bloodborne')
    Game.objects.filter(concept__igdb_match__igdb_id=contract.igdb_id).delete()
    svc.assign(challenge, profile, 'B', contract)

    body = client.get(_url(challenge)).content.decode()

    assert 'pp-csq__noart' in body, 'the fixture did not produce an art-less square'
    assert 'pp-csq__scrim' not in body


def test_the_header_card_is_the_design_systems_page_header(client):
    """The class guard checks that every class COMPILES, not that the right ones are present. This card
    shipped missing `border-2 border-base-300` and `shadow-lg shadow-neutral`, which compile fine and
    leave the page's header flat and edgeless beside every other page's."""
    challenge = _az_run(_hunter())

    body = client.get(_url(challenge)).content.decode()

    # EACH CLASS SEPARATELY, not the 8-token string this asserted first. The concatenation pinned the
    # ORDER too, so a harmless reorder would have failed it -- and a test that fails for a non-reason is a
    # test someone loosens rather than reads.
    header = body.split('pp-head-cascade')[0].rsplit('<section', 1)[-1]
    for cls in ('border-2', 'border-base-300', 'border-l-4', 'border-l-primary',
                'shadow-lg', 'shadow-neutral', 'bg-base-200/90'):
        assert cls in header, 'the page-header card is missing %s' % cls


def test_the_runs_state_pill_is_the_house_chip(client):
    """Both states, and the tone with them -- a pill that renders with no tone class is the failure this
    would otherwise miss."""
    profile = _hunter(client)
    finished = _az_run(profile)
    Challenge.objects.filter(pk=finished.pk).update(is_complete=True, completed_at=timezone.now())

    # NO `class="badge" not in body` HERE. `test_no_template_reaches_for_daisyuis_badge` checks the
    # template SOURCE, per template, which is strictly stronger -- a render only exercises the branch it
    # took. These assertions are about the right TONE reaching the right state, which the source test
    # cannot see.
    body = client.get(_url(finished)).content.decode()
    assert 'bd-chip bd-chip--success' in body

    hidden = _az_run(profile)
    svc.hide(hidden, profile)

    body = client.get(_url(hidden)).content.decode()
    assert 'bd-chip bd-chip--ghost' in body


def test_an_active_run_gets_no_read_only_note(client):
    """The (not finished, not hidden) case. Both notes are about a run you CANNOT change; an active run is
    the normal state and deserves no explanation at all."""
    profile = _hunter(client)

    body = client.get(_url(_az_run(profile))).content.decode()

    assert 'This run is finished, so its' not in body
    assert 'This run is hidden' not in body


def test_a_finished_run_that_was_also_hidden_says_both_things(client):
    """Reachable in production -- hide a run you already finished -- and the reason the notes are two
    independent `{% templatetag openblock %} if {% templatetag closeblock %}` blocks rather than an
    if/else. They are different facts and an either/or would have to drop one."""
    profile = _hunter(client)
    challenge = _az_run(profile)
    Challenge.objects.filter(pk=challenge.pk).update(is_complete=True, completed_at=timezone.now())
    challenge.refresh_from_db()
    svc.hide(challenge, profile)

    body = client.get(_url(challenge)).content.decode()

    assert 'This run is finished, so its' in body
    assert 'This run is hidden' in body


def test_an_empty_job_square_still_names_its_job(client):
    """THE FIX THAT NOTHING PINNED. The job line lived inside the `is_filled` branch, so an EMPTY Job
    Coverage square rendered only a discipline-tinted glyph -- no name, at any breakpoint, desktop
    included. Half the run type could not answer the one question the grid exists to answer ("which
    squares are still open"), while the screen-reader line answered it fine.

    Four mutation runs found this gap: reverting the fix broke no test at all.
    """
    challenge = svc.start(_hunter(), CHALLENGE_TYPE_JOBS)

    body = client.get(_url(challenge)).content.decode()

    # Every one of the 25 squares is empty on a fresh run, and every one must name its job.
    assert body.count('class="pp-csq__job"') == 25
    for name in Job.objects.values_list('name', flat=True):
        assert '>%s<' % name in body, '%s is not named on the grid' % name


def test_an_empty_az_square_does_not_repeat_its_letter(client):
    """The other half of the same decision. An A-Z square's key IS its name, and the centred well already
    says it -- a second copy would put one datum in two of the cell's three slots for no gain. So the job
    line's placement is not "always render the body"; it is "render it when it carries something"."""
    challenge = _az_run(_hunter())

    body = client.get(_url(challenge)).content.decode()

    assert 'pp-csq__job' not in body
    assert 'pp-csq__body' not in body, 'an all-empty A-Z run needs no body at all'


def test_a_filled_job_square_names_the_job_AND_the_game(client):
    """Both lines, because the square answers two questions: which job this is, and what filled it."""
    profile = _hunter(client)
    challenge = svc.start(profile, CHALLENGE_TYPE_JOBS)
    job = Job.objects.order_by('slug').first()
    contract = _contract('Astro Bot')
    contract.jobs.add(job)
    svc.assign(challenge, profile, job.slug, contract)

    body = client.get(_url(challenge)).content.decode()

    assert 'class="pp-csq__job" title="%s">%s<' % (job.name, job.name) in body
    assert 'pp-csq__name" title="Astro Bot">Astro Bot<' in body


# ── the editable square, and the picker it opens ──────────────────────────────────────────────────

def test_an_owners_unfinished_square_is_a_real_button(client):
    """A REAL `<button>`, not `role="button"`: keyboard activation, focus and semantics come free, where the
    role attribute needs Enter and Space wired by hand and gets one of them wrong more often than not."""
    profile = _hunter(client)
    challenge = _az_run(profile)

    body = client.get(_url(challenge)).content.decode()

    assert body.count('<button type="button"') >= 26
    assert 'data-cpick-open data-key="A"' in body


def test_a_visitors_squares_are_not_buttons(client):
    """A cell that takes focus and does nothing is worse than one that does not."""
    challenge = _az_run(_hunter())

    body = client.get(_url(challenge)).content.decode()

    assert 'data-cpick-open' not in body


def test_a_completed_square_is_not_a_button_even_for_its_owner(client):
    """It can never be reassigned or cleared, so there is nothing for a press to do -- the run-level
    `can_edit` rule, applied per square."""
    profile = _hunter(client)
    challenge = _az_run(profile)
    slot = svc.assign(challenge, profile, 'B', _contract('Bloodborne'))
    svc.mark_slot_completed(slot)

    body = client.get(_url(challenge)).content.decode()

    assert 'data-cpick-open data-key="B"' not in body
    assert 'data-cpick-open data-key="A"' in body, 'the other squares must still open'


def test_the_picker_ships_only_to_someone_who_can_use_it_but_the_script_ships_to_everyone(client):
    """Markup nobody can reach is markup that rots, so the DIALOG is still gated on `can_edit`.

    THE SCRIPT IS NOT, and it used to be. The file now also owns the page's entrance -- the tally counting up
    and the Horizon filling from zero -- which belongs to every viewer rather than to the one hunter who can
    change the run. The Hall of Fame is people reading somebody else's finished run, which is the case the
    animation is most for. Its picker half bails on its own guard when there is no dialog.
    """
    owner = _hunter(client)
    mine = _az_run(owner)

    body = client.get(_url(mine)).content.decode()
    assert 'id="cpick"' in body
    assert 'challenge-detail.js' in body

    theirs = _az_run(_hunter())
    other = client.get(_url(theirs)).content.decode()
    assert 'id="cpick"' not in other, 'a visitor must not get write markup'
    assert 'challenge-detail.js' in other, 'but they do get the entrance'


def test_a_finished_run_gets_no_picker(client):
    """Every square is locked, so there is nothing to pick. `can_edit` already says so; this pins that the
    template asks it rather than asking `is_owner`."""
    profile = _hunter(client)
    challenge = _az_run(profile)
    Challenge.objects.filter(pk=challenge.pk).update(is_complete=True, completed_at=timezone.now())

    body = client.get(_url(challenge)).content.decode()

    assert 'id="cpick"' not in body
    assert 'data-cpick-open' not in body


def test_the_grid_carries_the_run_id_the_script_needs(client):
    """The JS builds its endpoint URLs from this. Without it every request would go to `/slot/.../` on
    `undefined` and 404 -- silently, since the panel would just say it could not load."""
    profile = _hunter(client)
    challenge = _az_run(profile)

    body = client.get(_url(challenge)).content.decode()

    assert 'data-challenge-id="%d"' % challenge.id in body


def test_the_tally_is_addressable_so_a_write_can_move_it(client):
    profile = _hunter(client)
    challenge = _az_run(profile)

    body = client.get(_url(challenge)).content.decode()

    assert 'data-cpick-tally' in body


# ── the discipline shelves ────────────────────────────────────────────────────────────────────────

def test_a_jobs_run_is_grouped_into_five_discipline_shelves(client):
    """25 squares are really five groups of five -- the radar's disciplines -- and a flat seven-across grid
    broke every group mid-row, so the structure was invisible."""
    challenge = svc.start(_hunter(), CHALLENGE_TYPE_JOBS)

    groups = client.get(_url(challenge)).context['groups']

    assert [g['label'] for g in groups] == ['Combat', 'Exploration', 'Mind', 'Heart', 'Finesse']
    assert all(g['total'] == 5 for g in groups)
    assert sum(g['total'] for g in groups) == 25


def test_the_shelf_order_is_the_radars_not_the_alphabets(client):
    """Sorting the `discipline` COLUMN gives combat, exploration, finesse, heart, mind -- which agrees with
    the canonical order for two disciplines and then diverges, the kind of wrong that reads as right.
    `DISCIPLINE_LABELS` is the one definition of the sequence."""
    from trophies.services.job_render import DISCIPLINE_LABELS

    challenge = svc.start(_hunter(), CHALLENGE_TYPE_JOBS)

    groups = client.get(_url(challenge)).context['groups']

    assert [g['slug'] for g in groups] == list(DISCIPLINE_LABELS)
    assert [g['slug'] for g in groups] != sorted(g['slug'] for g in groups), (
        'alphabetical and canonical must differ, or this test proves nothing'
    )


def test_an_az_run_is_one_unlabelled_group_so_its_page_is_unchanged(client):
    """The alphabet has no sub-structure, so A-Z draws a single plain grid exactly as before -- not 26 groups
    of one, and not a labelled shelf."""
    challenge = _az_run(_hunter())

    groups = client.get(_url(challenge)).context['groups']

    assert len(groups) == 1
    assert groups[0]['label'] == ''
    assert len(groups[0]['cards']) == 26
    body = client.get(_url(challenge)).content.decode()
    assert 'pp-csq-shelf--plain' in body
    assert 'pp-csq-shelf__head' not in body


def test_each_shelf_reports_its_own_progress(client):
    """What the label area is FOR. The page's tally says how the RUN is going and cannot say how one
    discipline is."""
    profile = _hunter(client)
    challenge = svc.start(profile, CHALLENGE_TYPE_JOBS)
    job = Job.objects.order_by('slug').first()
    contract = _contract('Astro Bot')
    contract.jobs.add(job)
    svc.mark_slot_completed(svc.assign(challenge, profile, job.slug, contract))

    groups = {g['slug']: g for g in client.get(_url(challenge)).context['groups']}

    assert groups[job.discipline]['done'] == 1
    for slug, group in groups.items():
        if slug != job.discipline:
            assert group['done'] == 0


def test_every_card_knows_its_position_in_the_whole_run(client):
    """`forloop.counter0` restarts per GROUP -- five at a time on a jobs run -- so the lazy-image threshold of
    seven would never be reached and all 25 covers would load eagerly, silently undoing an earlier fix. The
    index is stamped once, across the run."""
    challenge = svc.start(_hunter(), CHALLENGE_TYPE_JOBS)

    cards = _cards(client.get(_url(challenge)))

    assert [c['index'] for c in cards] == list(range(25))


def test_the_board_is_what_carries_the_run_id(client):
    """A jobs run draws FIVE grids, so anything binding to `.pp-csq-grid` would have found only the first --
    the click delegation, and every square lookup after a write."""
    profile = _hunter(client)
    challenge = svc.start(profile, CHALLENGE_TYPE_JOBS)

    body = client.get(_url(challenge)).content.decode()

    assert 'class="pp-csq-board" data-challenge-id="%d"' % challenge.id in body
    assert body.count('class="pp-csq-grid" role="list"') == 5


def test_a_job_whose_discipline_is_not_in_the_labels_still_gets_drawn():
    """A SQUARE THAT EXISTS MUST BE DRAWABLE, which the grouping quietly stopped honouring. `slot_keys_for`
    builds a run from `Job.objects` with NO discipline filter and `Job.discipline` is `choices=` only, which
    Postgres does not enforce -- so a sixth discipline seeded without a matching `DISCIPLINE_LABELS` entry
    gave a run with a slot that had no group, no DOM, and therefore no way to ever be filled. The tally said
    x/26 while the page drew 25."""
    profile = _hunter()
    job = Job.objects.order_by('slug').first()
    Job.objects.filter(pk=job.pk).update(discipline='archaeology')
    challenge = svc.start(profile, CHALLENGE_TYPE_JOBS)

    groups = slot_render.slot_groups(challenge)

    assert sum(g['total'] for g in groups) == challenge.total_slots
    assert any(job.slug in [c['key'] for c in g['cards']] for g in groups)
    # NAMED AFTER ITSELF. This asserted `'Other'` when it was written, which was asserting the wrong
    # answer: two unmapped disciplines both read "Other" and were indistinguishable from each other and from
    # the deleted-`Job` group. See `test_an_unmapped_discipline_is_named_after_itself_not_lumped_into_other`.
    # What matters here is that it is NOT silently folded into a real discipline.
    unknown = [g for g in groups if job.slug in [c['key'] for c in g['cards']]][0]
    assert unknown['label'] == 'Archaeology'
    assert unknown['slug'] not in ('combat', 'exploration', 'mind', 'heart', 'finesse')
    # And it sorts after the five canonical shelves rather than displacing them.
    assert [g['slug'] for g in groups][:1] == ['combat']


def test_every_group_reports_progress_whatever_the_challenge_type():
    """ONE FUNCTION, ONE DICT SHAPE. The A-Z branch omitted `done`/`total` because its template never draws a
    head, so a Python consumer reading `group['total']` raised `KeyError` on exactly one challenge type --
    the kind of difference that stays invisible until it is a 500."""
    for challenge_type in (CHALLENGE_TYPE_AZ, CHALLENGE_TYPE_JOBS):
        challenge = svc.start(_hunter(), challenge_type)

        groups = slot_render.slot_groups(challenge)

        for group in groups:
            assert group['done'] == 0
            assert group['total'] == len(group['cards'])
        assert sum(g['total'] for g in groups) == challenge.total_slots


def test_each_shelf_is_a_named_region_and_so_is_its_list(client):
    """A `<section>` WITH NO ACCESSIBLE NAME IS NOT A REGION -- every engine exposes it as a plain generic, and
    the `<h2>` inside does not name it. So five unnamed sections bought nothing: no landmark, no way for
    assistive tech to move between disciplines. `aria-labelledby` pointing at the heading is what promotes it,
    and naming the `<ul>` the same way attributes "list, 5 items" to Combat rather than leaving it floating.
    """
    challenge = svc.start(_hunter(), CHALLENGE_TYPE_JOBS)

    body = client.get(_url(challenge)).content.decode()

    assert body.count('<section class="pp-csq-shelf"') == 5
    for slug in ('combat', 'exploration', 'mind', 'heart', 'finesse'):
        assert 'aria-labelledby="csq-shelf-%s"' % slug in body
        assert 'id="csq-shelf-%s"' % slug in body
    # Both the section and the list, so the count is attributed to the discipline.
    assert body.count('aria-labelledby="csq-shelf-combat"') == 2


def test_an_az_run_gets_a_plain_div_not_an_unnamed_landmark(client):
    """The alphabet has one unlabelled group, so there is no heading to name a region with -- and a `<section>`
    that cannot be named is a semantically empty wrapper pretending to be a landmark. A `<div>` is honest."""
    challenge = _az_run(_hunter())

    body = client.get(_url(challenge)).content.decode()

    assert '<div class="pp-csq-shelf pp-csq-shelf--plain">' in body
    assert '<section class="pp-csq-shelf"' not in body
    # `id="csq-shelf-` and `aria-labelledby`, NOT a bare `csq-shelf-`: that substring also occurs inside the
    # class `pp-csq-shelf--plain`, so the loose form failed on markup that was entirely correct. The eighth
    # time on this branch an assertion has matched something other than what it meant to read.
    assert 'id="csq-shelf-' not in body
    # THE SHELF'S labelledby, not any: the page's tutorial modal is correctly labelled by its own heading.
    assert 'aria-labelledby="csq-shelf-' not in body
    # Still one grid of 26, still announced as a list.
    assert body.count('class="pp-csq-grid" role="list"') == 1


def test_the_sprite_is_on_a_jobs_page_for_a_viewer_who_cannot_edit_it(client):
    """THE GRID DRAWS GLYPHS FOR EVERYBODY, not only for the owner who can open a picker. `job_icon_sprite` is
    emitted on challenge type alone, and it has to be: a non-owner and a finished run both render 25 squares
    whose icons are `<use>` references, so gating the sprite on `can_edit` would leave every one of them
    pointing at nothing. The existing pin did not distinguish the viewer."""
    owner = _hunter()
    challenge = svc.start(owner, CHALLENGE_TYPE_JOBS)
    _hunter(client)  # somebody else, signed in

    body = client.get(_url(challenge)).content.decode()

    assert 'id="jobicon-' in body
    assert 'href="#jobicon-' in body
    # The picker's MARKUP is what editability gates; the script ships to everyone, because it also owns the
    # page's entrance. (This asserted the script's absence until the entrance moved into it.)
    assert 'id="cpick"' not in body


def test_an_az_shelf_reports_real_progress_rather_than_a_hardcoded_zero():
    """THE A-Z BRANCH COMPUTES `done`, it does not fake it. Every other test reads a fresh run, where 0 is
    both the right answer and the answer a hardcoded literal would give -- so `'done': 0` would have passed
    the suite."""
    profile = _hunter()
    challenge = _az_run(profile)
    contract = _contract('Astro Bot')
    svc.mark_slot_completed(svc.assign(challenge, profile, 'A', contract))

    groups = slot_render.slot_groups(challenge)

    assert len(groups) == 1
    assert groups[0]['done'] == 1
    assert groups[0]['total'] == 26


def test_grouping_the_squares_costs_no_query_beyond_drawing_them():
    """THE VIEW CALLS `slot_groups`, but both flatness pins call `slot_cards` -- so a query added inside the
    grouping layer would be invisible to the tests that exist to catch exactly that. Asserted as a DIFFERENCE
    so this stays about the grouping rather than re-pinning the whole page's cost."""
    challenge = svc.start(_hunter(), CHALLENGE_TYPE_JOBS)

    with CaptureQueriesContext(connection) as cards:
        slot_render.slot_cards(challenge)
    with CaptureQueriesContext(connection) as groups:
        slot_render.slot_groups(challenge)

    assert len(groups.captured_queries) == len(cards.captured_queries)


def test_no_two_shelves_can_share_a_dom_id(client):
    """`aria-labelledby` HAS TO RESOLVE TO THE RIGHT HEADING. Duplicate ids make every shelf point at the
    first matching one, so several would announce the same discipline -- worse than having no name.

    Both colliding groups are reachable by the route the leftover branch exists for: `Job.discipline` is
    `choices=` with no database constraint. A deleted `Job` leaves a blank slug, and a job whose discipline is
    literally `other` produced the same string, so `slug|default:'other'` gave two `csq-shelf-other`.
    """
    profile = _hunter(client)
    jobs = list(Job.objects.order_by('slug')[:2])
    Job.objects.filter(pk=jobs[0].pk).update(discipline='other')
    challenge = svc.start(profile, CHALLENGE_TYPE_JOBS)
    Job.objects.filter(pk=jobs[1].pk).delete()

    body = client.get(_url(challenge)).content.decode()

    ids = re.findall(r'id="(csq-shelf-[^"]*)"', body)
    assert len(ids) == len(set(ids)), 'duplicate shelf ids: %r' % ids
    # Every `aria-labelledby` names an id that exists exactly once.
    refs = set(re.findall(r'aria-labelledby="(csq-shelf-[^"]*)"', body))
    assert refs
    for ref in refs:
        assert ids.count(ref) == 1


def test_an_unmapped_discipline_is_named_after_itself_not_lumped_into_other():
    """TWO SHELVES BOTH TITLED "Other" is not a name. An earlier version returned `'Other'` for every unmapped
    discipline, so two jobs seeded under different unmapped values were indistinguishable from each other AND
    from the squares whose `Job` row was deleted. `label_for_key` already degrades a slug to a readable name
    for the same reason; this does the same thing."""
    profile = _hunter()
    job = Job.objects.order_by('slug').first()
    Job.objects.filter(pk=job.pk).update(discipline='deep_sea_archaeology')
    challenge = svc.start(profile, CHALLENGE_TYPE_JOBS)

    groups = slot_render.slot_groups(challenge)
    mine = [g for g in groups if job.slug in [c['key'] for c in g['cards']]][0]

    assert mine['label'] == 'Deep Sea Archaeology'
    assert mine['dom_id'] == 'csq-shelf-deep_sea_archaeology'
    # The blank-slug group keeps "Other", because there is no job left to name it after.
    assert slot_render._discipline_label('') == 'Other'


# ── the run's own counters ────────────────────────────────────────────────────────────────────────

def test_the_runs_progress_bar_carries_a_hook_of_its_own(client):
    """`.pp-horizon` IS A SHARED PRIMITIVE AND THIS PAGE HAS TWO OF THEM. The nav's hidden sync bar
    (`.pp-avsync__prog`) comes FIRST in the document, so `document.querySelector('.pp-horizon')` found that
    one -- every write set `--horizon-progress` on a hidden element in the chrome and the run's bar did not
    move until a reload. The owner reported it as "the counter goes up but the bar does not".

    This pins both halves: that the hook exists, and that the hazard is real -- if the nav bar ever stops
    coming first, the test still passes and the hook is still correct, but the comment explaining it would be
    stale, so the ORDER is asserted too.
    """
    import re as _re

    challenge = svc.start(_hunter(client), CHALLENGE_TYPE_JOBS)

    body = client.get(_url(challenge)).content.decode()

    assert 'data-cpick-horizon' in body
    bars = [m.start() for m in _re.finditer(r'class="pp-horizon[" ]', body)]
    assert len(bars) >= 2, 'the collision this hook exists for is gone; re-read the JS comment'
    hook = body.index('data-cpick-horizon')
    # The run's bar is INSIDE the hook and is not the first on the page.
    assert bars[0] < hook, 'another .pp-horizon no longer precedes the run\'s own'
    assert any(b > hook for b in bars)


def test_the_tally_declares_its_own_countup_target(client):
    """`countUp` animates the TEXT to the value in `data-countup`, so the attribute is the target and the text
    is what a reader sees if the script never runs. Both have to be the real number, or a no-JS reader gets a
    zero and a JS reader gets a number that never moves."""
    profile = _hunter(client)
    challenge = _az_run(profile)
    contract = _contract('Astro Bot')
    svc.mark_slot_completed(svc.assign(challenge, profile, 'A', contract))

    body = client.get(_url(challenge)).content.decode()

    assert 'data-cpick-tally data-countup="1">1<' in body


# ── the history importer's two doors ──────────────────────────────────────────────────────────────

def test_the_owner_of_a_first_az_run_gets_the_history_door(client):
    """THE FLOW IT EXISTS FOR. A hunter who has just started an A-Z run and knows their library already covers
    half the alphabet is not thinking about a particular square yet, so making them open one to find the
    importer is the wrong first step (owner's note)."""
    profile = _hunter(client)
    challenge = _az_run(profile)

    resp = client.get(_url(challenge))
    body = resp.content.decode()

    assert resp.context['history_is_open'] is True
    # THE EXACT ATTRIBUTE. `data-cpick-history` is a PREFIX of `data-cpick-history-switch`, so the loose form
    # was satisfied by the in-sheet toggle alone and could not detect the page button going missing.
    assert 'data-cpick-history>' in body or 'data-cpick-history ' in body
    assert 'Import from your history' in body
    # And the in-sheet toggle, which is the same door for somebody who already opened a square.
    assert 'data-cpick-history-switch' in body


def test_a_job_coverage_run_gets_no_history_door(client):
    """A-Z ONLY. Rendering a door onto a panel that can only explain itself would be worse than not having it."""
    profile = _hunter(client)
    challenge = svc.start(profile, CHALLENGE_TYPE_JOBS)

    resp = client.get(_url(challenge))

    assert resp.context['history_is_open'] is False
    assert 'data-cpick-history' not in resp.content.decode()


def test_a_hunter_who_finished_a_run_gets_no_history_door(client):
    """The importer is a one-time head start, and `importer_is_available` owns that rule -- so the door cannot
    outlive what `assign` will accept."""
    profile = _hunter(client)
    done = _az_run(profile)
    Challenge.objects.filter(pk=done.pk).update(is_complete=True, completed_at=timezone.now())
    second = _az_run(profile)

    resp = client.get(_url(second))

    assert resp.context['history_is_open'] is False
    assert 'data-cpick-history' not in resp.content.decode()


def test_a_visitor_gets_no_history_door_and_pays_nothing_for_the_answer(client):
    """`can_edit` SHORT-CIRCUITS FIRST, which is the whole reason the flag is ordered the way it is. Once the
    Hall of Fame exists most reads of this page are visitors, and none of them can use the importer -- so none
    of them should pay its COUNT."""
    owner = _hunter()
    challenge = _az_run(owner)
    _hunter(client)

    with CaptureQueriesContext(connection) as captured:
        resp = client.get(_url(challenge))

    assert resp.context['history_is_open'] is False
    assert 'data-cpick-history' not in resp.content.decode()
    # The gate's own query names the challenge table with a COUNT; a visitor must not have run one.
    # `SELECT COUNT(` RATHER THAN `'COUNT' in sql`: the run's own SELECT lists the column
    # `completed_count`, which contains the substring COUNT, so the loose form matched the page's ordinary
    # read and failed against behaviour that was entirely correct. The third substring trap on this branch.
    #
    # AND NOT EVERY SUCH COUNT ANY MORE, which is the distinction this test now has to draw. The reward
    # panel asks a COUNT of the same table and shape, for the title ordinal, and a visitor DOES pay it on
    # purpose -- "what is this run worth" is the Hall of Fame's own question. The importer's gate is
    # identifiable by what it filters on: `is_complete` AND the challenge TYPE, with no `is_deleted` term.
    # So the two are told apart by their predicates rather than by counting queries, because a count would
    # go stale the next time either surface changed.
    counts = [q['sql'] for q in captured.captured_queries
              if 'FROM "challenges_challenge"' in q['sql'] and 'SELECT COUNT(' in q['sql'].upper()]
    # The panel's ordinal count is expected, once; anything BEYOND one is the gate leaking back in.
    assert len(counts) <= 1, 'a visitor paid for more than the reward panel: %r' % counts
    # And the surest evidence is the flag itself: `can_edit` short-circuits before the gate, so a visitor
    # cannot have asked the importer's question whatever else the page read.
    assert resp.context['can_edit'] is False


def test_the_history_door_asks_the_cheap_question_not_the_expensive_one(client):
    """DELIBERATELY NOT "is anything importable?". That needs the completion-date read over trophy data -- the
    tables the whale rule protects -- and would run on every view of the page.

    PINNED BY WHAT THE PAGE DOES NOT READ. The first version of this test was a copy of the one above with
    weaker assertions: it claimed to show that "a hunter with NOTHING importable still gets the door" while
    creating no completions at all, and counted nothing, so it would have passed even if the flag had started
    asking the expensive question. This sets up a hunter whose only completion is PRE-JOIN -- nothing
    importable -- and asserts both that the door still renders and that no trophy table was touched.
    """
    profile = _hunter(client)
    _joined_at(profile, timezone.now() - timezone.timedelta(days=365))
    challenge = _az_run(profile)
    old = _contract('Ape Escape')
    _platted_at(profile, old, timezone.now() - timezone.timedelta(days=800))

    with CaptureQueriesContext(connection) as captured:
        resp = client.get(_url(challenge))

    assert resp.context['history_is_open'] is True
    assert 'Import from your history' in resp.content.decode()
    trophy_reads = [q['sql'] for q in captured.captured_queries
                    if 'trophies_earnedtrophy' in q['sql'] or 'trophies_profilegame' in q['sql']]
    assert trophy_reads == [], 'the page render asked the expensive question: %d reads' % len(trophy_reads)

