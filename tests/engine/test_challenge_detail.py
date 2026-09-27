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


def _az_run(profile):
    return svc.start(profile, CHALLENGE_TYPE_AZ)


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

    cards = client.get(_url(challenge)).context['cards']

    assert [c['label'] for c in cards] == list('ABCDEFGHIJKLMNOPQRSTUVWXYZ')


def test_a_jobs_run_labels_its_squares_with_job_NAMES(client):
    """`card-shark`, not `Card Shark`, is what a slug-only card would render -- and it would look like a
    content bug rather than a missing catalogue lookup."""
    challenge = svc.start(_hunter(), CHALLENGE_TYPE_JOBS)

    labels = {c['label'] for c in client.get(_url(challenge)).context['cards']}

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

    cards = {c['key']: c for c in client.get(_url(challenge)).context['cards']}

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

    cards = {c['key']: c for c in client.get(_url(challenge)).context['cards']}

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

    cards = {c['key']: c for c in client.get(_url(challenge)).context['cards']}

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

    cards = {c['key']: c for c in client.get(_url(challenge)).context['cards']}

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

    # An EMPTY A-Z run: the slots, and nothing else. No contracts means no cover resolution at all, and
    # no job catalogue because A-Z keys are their own labels.
    #
    # ONE, WITH A CAVEAT WORTH STATING: it is 1 here partly because `ProfileFactory(user=<instance>)`
    # seeds the reverse one-to-one cache on the user, so `self._viewer()` costs nothing. On a real request
    # `request.user.profile` is a query, making the live figures 2 / 5 / 6 rather than 1 / 4 / 5. The
    # FLATNESS below is unaffected and is the property this test exists for -- but calling this an exact
    # count without the caveat would be exact by accident.
    assert cost() == 1

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

    assert 'its squares are fixed' not in body


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

    cards = {c['key']: c for c in client.get(_url(challenge)).context['cards']}

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

    assert '<ul class="pp-csq-grid" role="list">' in body
    assert body.count('<li class="pp-csq') == 26


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

    assert 'pp-csq__art--empty' in body, 'the fixture did not produce an art-less square'
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

    assert 'its squares are fixed' not in body
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

    assert 'its squares are fixed' in body
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
