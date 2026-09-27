"""My Challenges: the page, its two write doors, and the one thing the page has to get right.

THE VERB IS WHAT THIS FILE IS ABOUT. Hide-and-resume means a hidden run is not gone, so the card has
three states and the button has three words -- Start, Continue, Resume. Get the third wrong and a hunter
who hid a run with twelve squares filled is shown "Start", presses it, and is handed that run back. The
behaviour would be correct and the page would have lied about it.

The rest is the first security surface in this feature, so it is pinned rather than assumed: CSRF on
both write doors, a 404 (not a 403) for somebody else's run so an id cannot confirm it exists, and the
beta gate rendered as a disabled-but-focusable button with the reason as visible text rather than as an
attribute nobody can reach.
"""
import pytest
from django.test import Client, override_settings
from django.urls import reverse
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from pathlib import Path

from challenges.models import CHALLENGE_TYPE_AZ, CHALLENGE_TYPE_JOBS, Challenge
from challenges.views import MyChallengesView
from challenges.services import challenge_service as svc
from tests.factories import ConceptFactory, GameFactory, IGDBMatchFactory, ProfileFactory, UserFactory
from trophies.models import Contract

pytestmark = pytest.mark.django_db

open_beta = override_settings(CHALLENGES_BETA_MEMBERS_ONLY=False)

ROOT = Path(__file__).resolve().parents[2]

_SEQ = {'n': 0}


def _hunter(client, *, premium=True, linked=True):
    """A signed-in hunter, with the client logged in as them."""
    user = UserFactory()
    profile = ProfileFactory(user=user, user_is_premium=premium, is_linked=linked)
    client.force_login(user)
    return profile


def _contract(name):
    _SEQ['n'] += 1
    c = Contract.objects.create(name=name, slug=f"{name.lower().replace(' ', '-')}-{_SEQ['n']}",
                               is_live=True, igdb_id=700_000 + _SEQ['n'])
    concept = ConceptFactory(anchor_migration_completed_at=timezone.now())
    IGDBMatchFactory(concept=concept, igdb_id=c.igdb_id, status='accepted')
    GameFactory(concept=concept)
    return c


# ── the page's gates ─────────────────────────────────────────────────────────────────────────────

def test_a_signed_out_visitor_is_sent_to_log_in(client):
    resp = client.get(reverse('my_challenges'))

    assert resp.status_code == 302
    assert '/login' in resp.url or 'login' in resp.url


def test_an_account_with_no_linked_psn_is_sent_to_link_it(client):
    """The page is built out of the hunter's own completions, so without a linked profile
    `request.user.profile` raises and the page 500s. Same redirect My Lists and Career make."""
    _hunter(client, linked=False)

    resp = client.get(reverse('my_challenges'))

    assert resp.status_code == 302
    assert reverse('link_psn') in resp.url


def test_the_page_is_not_indexed(client):
    """Login-gated and about one hunter's own runs. Nothing here should rank or be crawled."""
    _hunter(client)

    body = client.get(reverse('my_challenges')).content.decode()

    assert 'noindex' in body and 'nofollow' in body


# ── the cards, and the verb ──────────────────────────────────────────────────────────────────────

def test_both_types_get_a_card_even_with_no_runs(client):
    """A list of what you own would show a newcomer nothing, and would hide Job Coverage from anybody
    mid-A-Z."""
    _hunter(client)

    body = client.get(reverse('my_challenges')).content.decode()

    assert 'A-Z Challenge' in body
    assert 'Job Coverage Challenge' in body


def test_an_empty_card_says_start(client):
    _hunter(client)

    cards = client.get(reverse('my_challenges')).context['cards']

    assert [c['verb'] for c in cards] == ['Start', 'Start']
    assert all(c['state'] == 'empty' for c in cards)


def test_an_active_run_says_continue(client):
    profile = _hunter(client)
    svc.start(profile, CHALLENGE_TYPE_AZ)

    cards = {c['type']: c for c in client.get(reverse('my_challenges')).context['cards']}

    assert cards[CHALLENGE_TYPE_AZ]['verb'] == 'Continue'
    assert cards[CHALLENGE_TYPE_AZ]['state'] == 'active'
    assert cards[CHALLENGE_TYPE_JOBS]['verb'] == 'Start'


def test_a_hidden_run_says_resume_rather_than_start():
    """THE ONE THING THIS PAGE HAS TO GET RIGHT.

    Hiding does not delete, so pressing Start hands the hidden run back. If the card said "Start" a
    hunter would be handed a part-finished run with no warning -- correct behaviour, dishonest page. The
    badge says Hidden for the same reason: otherwise there is no way to tell a hidden run from no run.
    """
    client = Client()
    profile = _hunter(client)
    challenge = svc.start(profile, CHALLENGE_TYPE_AZ)
    svc.assign(challenge, profile, 'A', _contract('Astro Bot'))
    svc.hide(challenge, profile)

    resp = client.get(reverse('my_challenges'))
    card = {c['type']: c for c in resp.context['cards']}[CHALLENGE_TYPE_AZ]

    assert card['state'] == 'resumable'
    assert card['verb'] == 'Resume'
    assert card['run'].pk == challenge.pk
    assert 'Hidden' in resp.content.decode()


def test_the_card_shows_progress_and_what_is_merely_planned(client):
    """`planned` is filled-but-unfinished, and both numbers are computed in the VIEW -- an earlier
    template did it with chained `add` filters and produced nonsense."""
    profile = _hunter(client)
    challenge = svc.start(profile, CHALLENGE_TYPE_AZ)
    svc.assign(challenge, profile, 'A', _contract('Astro Bot'))
    svc.assign(challenge, profile, 'B', _contract('Bloodborne'))
    svc.mark_slot_completed(challenge.slots.get(key='A'))

    card = {c['type']: c for c in client.get(reverse('my_challenges')).context['cards']}[CHALLENGE_TYPE_AZ]

    assert card['planned'] == 1, 'one square filled but not finished'
    assert card['progress'] == round(1 / 26 * 100)


def test_the_hide_button_appears_only_on_an_active_run(client):
    """ALL THREE STATES, because two of them are not enough: checking empty and active only, the
    condition could be relaxed to `{% if card.run %}` and Hide would render on a HIDDEN run -- which is
    exactly what this test's name says must not happen -- while the test stayed green."""
    profile = _hunter(client)
    assert 'data-chal-hide' not in client.get(reverse('my_challenges')).content.decode()

    challenge = svc.start(profile, CHALLENGE_TYPE_AZ)
    assert 'data-chal-hide' in client.get(reverse('my_challenges')).content.decode()

    svc.hide(challenge, profile)
    assert 'data-chal-hide' not in client.get(reverse('my_challenges')).content.decode(), (
        'Hide rendered on a run that is already hidden'
    )


def test_finished_runs_are_listed_and_the_block_is_omitted_when_there_are_none(client):
    profile = _hunter(client)
    assert 'Finished' not in client.get(reverse('my_challenges')).content.decode()

    challenge = svc.start(profile, CHALLENGE_TYPE_AZ)
    Challenge.objects.filter(pk=challenge.pk).update(
        is_complete=True, completed_at=timezone.now())

    body = client.get(reverse('my_challenges')).content.decode()
    assert 'Finished' in body
    assert challenge.name in body


def test_a_run_hidden_after_finishing_leaves_the_history(client):
    """`completed()` is built on `visible()`, so hiding means "off my profile" on the hunter's own page
    as well as in the hub. Pinned because the alternative -- history ignoring the flag -- would be a
    defensible reading and is not the one this page takes."""
    profile = _hunter(client)
    challenge = svc.start(profile, CHALLENGE_TYPE_AZ)
    Challenge.objects.filter(pk=challenge.pk).update(
        is_complete=True, completed_at=timezone.now())
    challenge.refresh_from_db()
    svc.hide(challenge, profile)

    assert client.get(reverse('my_challenges')).context['finished'] == []


def test_the_pages_own_query_cost_is_exact(rf):
    """AN EXACT COUNT, measured on the VIEW rather than through the client.

    Both earlier versions were wrong in opposite directions. A ceiling of 20 was slack enough to hide a
    per-card N+1; tightening it to 12 made the test FLAKY -- it passed at 10 in a whole-file run and
    failed at 13 in isolation, because four site-wide context-processor queries cache for 60s in locmem
    and are warmed by whichever test ran first. A request-level budget cannot be both tight and stable
    while it is measuring other people's queries.

    So this measures `get_context_data` alone, where an exact number is meaningful and stable, and where
    a per-card N+1 shows up as +2 rather than vanishing into slack. Two shapes, because an empty card
    costs one query more than an active one (it asks `resumable_run` too).
    """
    profile = ProfileFactory(user_is_premium=True)
    request = rf.get(reverse('my_challenges'))
    request.user = profile.user

    def cost():
        view = MyChallengesView()
        view.request, view.kwargs = request, {}
        with CaptureQueriesContext(connection) as captured:
            view.get_context_data()
        return len(captured.captured_queries)

    # Two EMPTY cards: 3 reads each (active, resumable, visible-completed) + 2 for the history.
    assert cost() == 8

    svc.start(profile, CHALLENGE_TYPE_AZ)
    svc.start(profile, CHALLENGE_TYPE_JOBS)
    # Two ACTIVE cards: `resumable_run` is short-circuited, so one query fewer each.
    assert cost() == 6

    for i in range(12):
        Challenge.objects.create(
            profile=profile, challenge_type=CHALLENGE_TYPE_AZ, name=f'Old {i}', total_slots=26,
            is_complete=True, completed_at=timezone.now())
    # AND IT DOES NOT GROW: the history is one count plus one bounded slice however many runs exist.
    assert cost() == 6


# ── the beta gate, rendered rather than redirected ───────────────────────────────────────────────

def test_a_free_hunter_sees_the_reason_as_text_and_a_disabled_start(client):
    """NOT a redirect, and NOT `disabled`. A truly disabled button takes no pointer events and leaves
    the tab order, so its explanation is unreachable by keyboard, screen reader and touch alike -- which
    is why the reason is visible text in the header and the button only carries `aria-disabled`."""
    _hunter(client, premium=False)

    body = client.get(reverse('my_challenges')).content.decode()

    assert 'Challenges are in beta' in body
    assert 'aria-disabled="true"' in body
    assert 'disabled>' not in body, 'a disabled button leaves the tab order and cannot be reached'
    # And it must NOT promise a browse that does not exist yet: `challenges` still answers the
    # "coming back soon" placeholder, so sending a gated hunter there was a dead end.
    assert 'browsing is open' not in body


def test_a_member_sees_no_beta_notice(client):
    _hunter(client, premium=True)

    body = client.get(reverse('my_challenges')).content.decode()

    assert 'Challenges are in beta' not in body
    assert 'aria-disabled="true"' not in body


@open_beta
def test_when_the_beta_ends_a_free_hunter_gets_a_live_button(client):
    _hunter(client, premium=False)

    body = client.get(reverse('my_challenges')).content.decode()

    assert 'Challenges are in beta' not in body
    assert 'aria-disabled="true"' not in body


# ── starting and resuming ────────────────────────────────────────────────────────────────────────

def test_starting_creates_a_run_and_returns_to_the_page(client):
    profile = _hunter(client)

    resp = client.post(reverse('challenge_start', args=[CHALLENGE_TYPE_AZ]))

    assert resp.status_code == 302
    assert resp.url == reverse('my_challenges')
    assert Challenge.objects.filter(profile=profile, challenge_type=CHALLENGE_TYPE_AZ).count() == 1


def test_starting_again_resumes_the_hidden_run_and_says_so(client):
    profile = _hunter(client)
    challenge = svc.start(profile, CHALLENGE_TYPE_AZ)
    svc.assign(challenge, profile, 'A', _contract('Astro Bot'))
    svc.hide(challenge, profile)

    resp = client.post(reverse('challenge_start', args=[CHALLENGE_TYPE_AZ]), follow=True)

    challenge.refresh_from_db()
    assert challenge.is_deleted is False
    assert challenge.filled_count == 1
    assert Challenge.objects.filter(profile=profile, challenge_type=CHALLENGE_TYPE_AZ).count() == 1
    assert 'back where you left it' in resp.content.decode()


def test_a_free_hunter_cannot_start_during_the_beta_even_by_posting(client):
    """The button is only what a hunter SEES. The service is the gate, and this is the door a hand-rolled
    POST would come through."""
    profile = _hunter(client, premium=False)

    resp = client.post(reverse('challenge_start', args=[CHALLENGE_TYPE_AZ]), follow=True)

    assert not Challenge.objects.filter(profile=profile).exists()
    assert 'beta for members first' in resp.content.decode()


def test_starting_requires_a_post(client):
    _hunter(client)

    assert client.get(reverse('challenge_start', args=[CHALLENGE_TYPE_AZ])).status_code == 405


def test_starting_requires_a_csrf_token():
    """The write doors sit under the page's own path rather than /api/v1/, so they inherit Django's CSRF
    middleware rather than DRF's. Pinned because that is an easy thing to lose in a refactor."""
    client = Client(enforce_csrf_checks=True)
    profile = _hunter(client)

    resp = client.post(reverse('challenge_start', args=[CHALLENGE_TYPE_AZ]))

    assert resp.status_code == 403
    assert not Challenge.objects.filter(profile=profile).exists()


# ── hiding ───────────────────────────────────────────────────────────────────────────────────────

def test_hiding_takes_a_run_out_of_view_and_answers_json(client):
    profile = _hunter(client)
    challenge = svc.start(profile, CHALLENGE_TYPE_AZ)

    resp = client.post(reverse('challenge_hide', args=[challenge.pk]))

    assert resp.status_code == 200
    assert resp.json() == {'hidden': True}
    challenge.refresh_from_db()
    assert challenge.is_deleted is True


def test_hiding_is_idempotent(client):
    profile = _hunter(client)
    challenge = svc.start(profile, CHALLENGE_TYPE_AZ)
    client.post(reverse('challenge_hide', args=[challenge.pk]))

    assert client.post(reverse('challenge_hide', args=[challenge.pk])).status_code == 200


def test_somebody_elses_run_is_a_404_and_not_a_403(client):
    """A 403 confirms the run exists and is somebody's. Resolving through `profile=` means an id alone
    can never tell you that."""
    # Premium, or the beta gate refuses the fixture's own setup -- `ProfileFactory` defaults to free.
    other_profile = ProfileFactory(user_is_premium=True)
    theirs = svc.start(other_profile, CHALLENGE_TYPE_AZ)
    _hunter(client)

    resp = client.post(reverse('challenge_hide', args=[theirs.pk]))

    assert resp.status_code == 404
    theirs.refresh_from_db()
    assert theirs.is_deleted is False


def test_an_unknown_run_id_is_a_404_not_a_405(client):
    """`get_challenge` returns None rather than raising `Http404`, because this project's `handler404` is
    GET-only -- a raised Http404 on a POST comes back as a 405 listing GET/HEAD/OPTIONS."""
    _hunter(client)

    assert client.post(reverse('challenge_hide', args=[999999])).status_code == 404


def test_hiding_requires_a_post(client):
    profile = _hunter(client)
    challenge = svc.start(profile, CHALLENGE_TYPE_AZ)

    assert client.get(reverse('challenge_hide', args=[challenge.pk])).status_code == 405


def test_hiding_requires_a_csrf_token():
    client = Client(enforce_csrf_checks=True)
    profile = _hunter(client)
    challenge = svc.start(profile, CHALLENGE_TYPE_AZ)

    assert client.post(reverse('challenge_hide', args=[challenge.pk])).status_code == 403
    challenge.refresh_from_db()
    assert challenge.is_deleted is False


# ── the rail ─────────────────────────────────────────────────────────────────────────────────────

def test_my_challenges_sits_in_my_pursuit_tools():
    """Personal and login-gated, so it belongs beside My Lists rather than in Community with the public
    browse -- the same split Collection/Badges draws. `test_nav_reachability` covers resolution and that
    the URL lands inside its own hub; this pins the GROUP, which is the IA decision."""
    from core.hub_subnav import MY_PURSUIT_HUB

    item = next(i for i in MY_PURSUIT_HUB.items if i.slug == 'my_challenges')

    assert item.group == 'Tools'
    assert item.url_name == 'my_challenges'
    assert item.auth_required is True
    # THE PREFIX, which is the half that is easy to forget: the hub is resolved by PATH prefix, so a
    # rail item whose URL sits outside its own hub's prefixes drops you out of the hub the moment you
    # click it -- the rail vanishes or another hub's lights up.
    assert '/my-challenges/' in MY_PURSUIT_HUB.prefixes


# ── the audit's findings, pinned ─────────────────────────────────────────────────────────────────

def test_the_breadcrumb_renders_its_labels():
    """B1: the partial reads `item.text`, and this page passed `label` -- so both crumbs rendered as
    EMPTY elements and the JSON-LD carried two blank names. Nothing tested the breadcrumb, which is
    exactly why it shipped."""
    client = Client()
    _hunter(client)

    body = client.get(reverse('my_challenges')).content.decode()

    # SCOPED TO THE <nav>, because both labels also appear in the navbar and the page heading -- so a bare
    # `in body` passed with the bug still present. The third wrong-scope assertion on this branch.
    start = body.index('aria-label="Breadcrumb"')
    crumbs = body[start:body.index('</nav>', start)]

    assert 'My Pursuit' in crumbs
    assert 'My Challenges' in crumbs
    assert 'Home' in crumbs
    assert "></a>" not in crumbs, "a breadcrumb rendered as an empty anchor"


def test_a_lapsed_member_can_still_continue_a_run_they_already_started():
    """B4: the beta gate fired BEFORE the resume branches, so it refused a hunter their own existing run.

    It gates CREATING, never keeping -- the line `gamelists._refuse_if_not_member` draws, where a lapsed
    member keeps everything and only loses making more. Reachable two ways: a membership ending, or the
    flag being switched on after runs already exist.
    """
    client = Client()
    profile = _hunter(client, premium=True)
    challenge = svc.start(profile, CHALLENGE_TYPE_AZ)

    profile.user_is_premium = False          # membership lapses
    profile.save(update_fields=['user_is_premium'])

    resumed = svc.start(profile, CHALLENGE_TYPE_AZ)
    assert resumed.pk == challenge.pk, 'a lapsed member was refused their own active run'

    # And the same for a hidden one, which is the other half of the branch the gate used to precede.
    svc.hide(challenge, profile)
    assert svc.start(profile, CHALLENGE_TYPE_AZ).pk == challenge.pk

    # But still no NEW run of the other type.
    with pytest.raises(svc.ChallengeError, match='beta for members first'):
        svc.start(profile, CHALLENGE_TYPE_JOBS)


def test_starting_reports_which_of_its_three_branches_fired():
    """B5: the view used `filled_count` to pick its message, which cannot tell a fresh run from an empty
    resumed one and said "back where you left it" about a run that had never gone away."""
    client = Client()
    profile = _hunter(client)

    challenge, outcome = svc.start_reporting(profile, CHALLENGE_TYPE_AZ)
    assert outcome == svc.CREATED

    assert svc.start_reporting(profile, CHALLENGE_TYPE_AZ)[1] == svc.ALREADY_ACTIVE

    svc.hide(challenge, profile)
    assert svc.start_reporting(profile, CHALLENGE_TYPE_AZ)[1] == svc.RESUMED


def test_a_double_submit_says_nothing_rather_than_claiming_a_resume(client):
    """The one case the old message was actively WRONG rather than merely silent: a stale tab or a
    back-button repost on a run that never left."""
    profile = _hunter(client)
    challenge = svc.start(profile, CHALLENGE_TYPE_AZ)
    svc.assign(challenge, profile, 'A', _contract('Astro Bot'))

    body = client.post(reverse('challenge_start', args=[CHALLENGE_TYPE_AZ]), follow=True).content.decode()

    assert 'back where you left it' not in body


def test_a_fresh_run_says_it_is_ready(client):
    """The other half: creating a run used to be a silent 302, because `filled_count` was 0."""
    _hunter(client)

    body = client.post(reverse('challenge_start', args=[CHALLENGE_TYPE_AZ]), follow=True).content.decode()

    assert 'is ready' in body


def test_the_card_count_of_finished_runs_obeys_the_pages_own_rule(client):
    """B3: the card used `completed_run_count`, which counts HIDDEN finished runs -- right for naming a
    run and for the importer, wrong on a card. It made the page say "1 finished before" above a Finished
    list showing none."""
    profile = _hunter(client)
    old = Challenge.objects.create(
        profile=profile, challenge_type=CHALLENGE_TYPE_AZ, name='Old run', total_slots=26,
        is_complete=True, completed_at=timezone.now())
    svc.hide(old, profile)

    resp = client.get(reverse('my_challenges'))
    card = {c['type']: c for c in resp.context['cards']}[CHALLENGE_TYPE_AZ]

    assert card['visible_completed_count'] == 0, 'a hidden finished run was counted on the card'
    assert resp.context['finished'] == []


def test_the_header_tally_counts_every_finished_run_not_just_the_listed_ones(client):
    """B2: the tally rendered the SLICED list's length, so a hunter past the limit read the limit back.
    Also pins `HISTORY_LIMIT`, which nothing tested -- deleting the slice left every test green."""
    profile = _hunter(client)
    limit = MyChallengesView.HISTORY_LIMIT
    for i in range(limit + 3):
        Challenge.objects.create(
            profile=profile, challenge_type=CHALLENGE_TYPE_AZ, name=f'Run {i}', total_slots=26,
            is_complete=True, completed_at=timezone.now())

    resp = client.get(reverse('my_challenges'))

    assert resp.context['finished_total'] == limit + 3
    assert len(resp.context['finished']) == limit, 'HISTORY_LIMIT is not applied'

    # SCOPED TO THE TALLY. `str(limit + 3) in body` was VACUOUS: 27 appears in the header star's SVG
    # path data and in every "27 Sep" date, so it could not fail on any date -- reverting the template
    # to `finished|length` left it green. Fourth wrong-scope assertion on this branch.
    body = resp.content.decode()
    start = body.index('pp-tally')
    tally = body[start:body.index('</span>', start)]
    assert str(limit + 3) in tally, f'the tally rendered something other than {limit + 3}'


def test_a_lapsed_members_existing_run_keeps_a_live_button(client):
    """THE RENDER HALF of the beta-gate fix, which was unpinned -- dropping `card.state == 'empty'`
    from the template's condition left every test green while gating the button on a run the service
    will happily let them continue. The exact symptom the fix was about, inverted: the page and the
    service disagreeing about whose button is live."""
    profile = _hunter(client, premium=True)
    svc.start(profile, CHALLENGE_TYPE_AZ)
    profile.user_is_premium = False
    profile.save(update_fields=['user_is_premium'])

    body = client.get(reverse('my_challenges')).content.decode()
    az_card = body[body.index('A-Z Challenge'):body.index('Job Coverage Challenge')]

    assert 'Continue' in az_card
    assert 'aria-disabled' not in az_card, 'a lapsed member was shown a dead Continue'
    # And the OTHER card, where they genuinely cannot start, still is gated.
    assert 'aria-disabled' in body[body.index('Job Coverage Challenge'):]


def test_the_three_outcome_constants_are_distinct():
    """`test_starting_reports_which_...` asserts each branch returns the matching constant, which is
    satisfied if two of them share a value."""
    assert len({svc.CREATED, svc.RESUMED, svc.ALREADY_ACTIVE}) == 3


def test_both_write_doors_share_one_rate_limit_bucket():
    """T4: the whole point of `CHALLENGE_WRITE_RATELIMIT_GROUP` is that create-hide-create cannot outrun
    one door by using the other. Removing `group=` from either decorator silently splits the bucket and
    doubles the effective rate, and nothing noticed.

    Asserted on the decorators' own configuration rather than by firing 31 requests: the rate is a
    number somebody may tune, and a test that breaks when they tune it teaches them to delete it.
    """
    import re

    import challenges.views as views

    assert views.CHALLENGE_WRITE_RATELIMIT_GROUP
    src = (ROOT / 'challenges' / 'views.py').read_text(encoding='utf-8')

    # EVERY ARGUMENT, not just `group`. django_ratelimit hashes group, rate, key and methods together
    # into the bucket identity, so changing `rate` or `key` on one door splits it just as surely -- and
    # an earlier version pinned only `group`, then asserted a COUNT of 2 that a third write door would
    # break while the sharing was still perfectly correct.
    calls = [' '.join(c.split()) for c in re.findall(r'ratelimit\((.*?)\)\)', src, re.S)]

    assert len(calls) >= 2, 'both write doors must be rate limited'
    assert len(set(calls)) == 1, f'the write doors do not share one bucket: {set(calls)}'
    assert 'group=CHALLENGE_WRITE_RATELIMIT_GROUP' in calls[0]


def test_the_write_endpoints_light_the_rail():
    """T5: an item shipping without a `_URL_NAME_TO_SLUG_OVERRIDES` line is SILENT -- the strip renders
    with nothing lit. The map's own comment says so, and the two lines this page added were untested;
    deleting them broke nothing."""
    from core.hub_subnav import _URL_NAME_TO_SLUG_OVERRIDES

    for name in ('challenge_start', 'challenge_hide'):
        assert _URL_NAME_TO_SLUG_OVERRIDES.get(name) == ('my_pursuit', 'my_challenges')


def test_a_finished_run_can_still_be_hidden(client):
    """T6: reachable only by a hand-rolled POST (the template gates Hide on `state == 'active'`), and it
    has to work -- a hunter must be able to take a completed run off their profile. It rests on `hide`
    deliberately NOT going through `_lock_challenge`, which refuses a finished run, so a future tidy-up
    swapping those in would break it silently."""
    profile = _hunter(client)
    challenge = svc.start(profile, CHALLENGE_TYPE_AZ)
    Challenge.objects.filter(pk=challenge.pk).update(
        is_complete=True, completed_at=timezone.now())

    resp = client.post(reverse('challenge_hide', args=[challenge.pk]))

    assert resp.status_code == 200
    challenge.refresh_from_db()
    assert challenge.is_deleted is True
    assert not Challenge.objects.completed().filter(pk=challenge.pk).exists()


def test_an_unknown_type_is_refused_with_a_reason(client):
    """Replaces a looser twin that asserted only "no row was created", which a 500 also satisfies."""
    profile = _hunter(client)

    resp = client.post(reverse('challenge_start', args=['calendar']), follow=True)

    assert 'not a challenge type' in resp.content.decode()
    assert not Challenge.objects.filter(profile=profile).exists()


# ── the team preview door ────────────────────────────────────────────────────────────────────────

def test_staff_can_preview_the_free_tier_view(client):
    """`?preview=challenges-free`, the sanctioned door -- staff only, writes nothing, and strictly
    narrows what the viewer is shown. The alternative a reader might reach for is flipping your own
    `user_is_premium` in a shell, which mutates a real row and follows you onto every other page."""
    profile = _hunter(client, premium=True)
    profile.user.is_staff = True
    profile.user.save(update_fields=['is_staff'])

    plain = client.get(reverse('my_challenges')).content.decode()
    assert 'Challenges are in beta' not in plain

    previewed = client.get(reverse('my_challenges') + '?preview=challenges-free').content.decode()
    assert 'Challenges are in beta' in previewed
    assert 'aria-disabled="true"' in previewed


def test_the_preview_door_is_staff_only(client):
    """A querystring anybody could type must not change what anybody sees. `core.previews.is_team`
    enforces it; this pins that this page goes through it rather than reading `request.GET` itself."""
    _hunter(client, premium=True)   # a member, not staff

    body = client.get(reverse('my_challenges') + '?preview=challenges-free').content.decode()

    assert 'Challenges are in beta' not in body
    assert 'aria-disabled="true"' not in body


def test_the_previewed_refusal_actually_refuses(client):
    """THE DOOR OPENS THE WHOLE THING, which `core.previews` insists on. A preview that dimmed the
    button and then let the POST through would be showing a state no free hunter can reach."""
    profile = _hunter(client, premium=True)
    profile.user.is_staff = True
    profile.user.save(update_fields=['is_staff'])

    resp = client.post(
        reverse('challenge_start', args=[CHALLENGE_TYPE_AZ]) + '?preview=challenges-free',
        follow=True)

    assert not Challenge.objects.filter(profile=profile).exists()
    assert 'beta for members first' in resp.content.decode()


def test_the_preview_survives_its_own_redirect(client):
    """Otherwise previewing the refusal lands you on the un-previewed page: the gated error message
    above live buttons."""
    profile = _hunter(client, premium=True)
    profile.user.is_staff = True
    profile.user.save(update_fields=['is_staff'])

    resp = client.post(
        reverse('challenge_start', args=[CHALLENGE_TYPE_AZ]) + '?preview=challenges-free')

    assert 'preview=challenges-free' in resp.url


def test_previewing_does_not_take_away_a_run_you_already_have(client):
    """The same line the real gate draws: it gates creating, never keeping. A previewing staff member
    with an active run still gets to continue it."""
    profile = _hunter(client, premium=True)
    profile.user.is_staff = True
    profile.user.save(update_fields=['is_staff'])
    challenge = svc.start(profile, CHALLENGE_TYPE_AZ)

    client.post(reverse('challenge_start', args=[CHALLENGE_TYPE_AZ]) + '?preview=challenges-free')

    challenge.refresh_from_db()
    assert challenge.is_deleted is False
    assert Challenge.objects.filter(profile=profile).count() == 1


def test_the_beta_card_makes_the_ask_and_the_reassurance(client):
    """OWNER'S CALL, 2026-09-27: the beta needs to draw real attention, not sit in small print.

    Four things have to be on the page for a free hunter, and this pins all four because dropping any
    one of them turns it from an honest explanation into either a gate or a nag:
      - that it IS a beta,
      - that members are in first,
      - that everyone gets it when the beta ends,
      - and a route in now, for somebody who wants one.
    """
    _hunter(client, premium=False)

    # WHITESPACE-NORMALISED, because the template wraps this copy across lines and a raw `in body`
    # then fails on a sentence that is present and correct. Reflowing prose is a formatting change,
    # not a content one, and the test should not break on it.
    body = ' '.join(client.get(reverse('my_challenges')).content.decode().split())

    assert 'Challenges are in beta' in body
    assert 'Members are running them first' in body
    assert 'Everyone can start one when the beta ends' in body
    assert 'Nothing here stays members-only' in body
    assert reverse('support_hub') in body
    assert 'Become a supporter' in body


def test_the_beta_card_uses_the_house_premium_pattern(client):
    """An ACCENT border is what makes this read as the same kind of card a hunter has met elsewhere;
    `border-base-300` would make it an ordinary content block and lose the point of the treatment.

    `warning` rather than the pattern's `primary` is the one deliberate departure (owner's call), and it
    only works because nothing else on the page spends yellow any more -- the Hidden badge moved to
    `ghost` the same day. If something reintroduces yellow here, this card stops being the loud thing."""
    _hunter(client, premium=False)

    # SCOPED TO THE CARD'S OWN <section>, not a fixed character window -- a lookbehind of N characters
    # would silently start matching the header card above the moment the markup changed length.
    body = client.get(reverse('my_challenges')).content.decode()
    headline = body.index('Challenges are in beta')
    card = body[body.rindex('<section', 0, headline):headline]

    assert 'border-warning/40' in card
    assert 'border-base-300' not in card, 'the beta card lost its accent and reads as an ordinary block'


def test_a_member_never_sees_the_beta_card(client):
    _hunter(client, premium=True)

    assert 'Challenges are in beta' not in client.get(reverse('my_challenges')).content.decode()


def test_the_beta_card_asks_for_support_last(client):
    """The ask sits at the FOOT of the card, after everything a hunter gets either way.

    Order is the whole difference between an earnest appeal and a toll booth: the beta, the promise that
    nothing stays members-only, and the date-free "when the beta ends" all come first. Pinned as an
    ORDER rather than a presence, because moving this line above the reassurance would pass any test
    that only checked the words were somewhere on the page.
    """
    _hunter(client, premium=False)

    body = ' '.join(client.get(reverse('my_challenges')).content.decode().split())

    assert 'Supporters keep us running' in body
    assert 'please consider helping us build it' in body
    assert body.index('Nothing here stays members-only') < body.index('Supporters keep us running')
    assert body.index('Everyone can start one when the beta ends') < body.index('Supporters keep us running')
