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
from django.utils import timezone

from challenges.models import CHALLENGE_TYPE_AZ, CHALLENGE_TYPE_JOBS, Challenge
from challenges.services import challenge_service as svc
from tests.factories import ConceptFactory, GameFactory, IGDBMatchFactory, ProfileFactory, UserFactory
from trophies.models import Contract

pytestmark = pytest.mark.django_db

open_beta = override_settings(CHALLENGES_BETA_MEMBERS_ONLY=False)

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
    """A hidden run is already hidden, and an empty card has nothing to hide."""
    profile = _hunter(client)
    empty = client.get(reverse('my_challenges')).content.decode()
    assert 'data-chal-hide' not in empty

    svc.start(profile, CHALLENGE_TYPE_AZ)

    assert 'data-chal-hide' in client.get(reverse('my_challenges')).content.decode()


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


def test_the_page_cost_does_not_grow_with_a_hunters_runs(client, django_assert_max_num_queries):
    """Flat by construction: two reads per type plus one bounded history query. Nothing here touches
    trophy data, which the rest of this feature does."""
    profile = _hunter(client)
    svc.start(profile, CHALLENGE_TYPE_AZ)
    svc.start(profile, CHALLENGE_TYPE_JOBS)
    for _ in range(4):
        done = Challenge.objects.create(
            profile=profile, challenge_type=CHALLENGE_TYPE_AZ, name='Old run', total_slots=26,
            is_complete=True, completed_at=timezone.now())
        assert done.pk

    with django_assert_max_num_queries(20):
        client.get(reverse('my_challenges'))


# ── the beta gate, rendered rather than redirected ───────────────────────────────────────────────

def test_a_free_hunter_sees_the_reason_as_text_and_a_disabled_start(client):
    """NOT a redirect, and NOT `disabled`. A truly disabled button takes no pointer events and leaves
    the tab order, so its explanation is unreachable by keyboard, screen reader and touch alike -- which
    is why the reason is visible text in the header and the button only carries `aria-disabled`."""
    _hunter(client, premium=False)

    body = client.get(reverse('my_challenges')).content.decode()

    assert 'beta for members first' in body
    assert 'browsing is open now' in body
    assert 'aria-disabled="true"' in body
    assert 'disabled>' not in body, 'a truly disabled button cannot be focused or announced'


def test_a_member_sees_no_beta_notice(client):
    _hunter(client, premium=True)

    body = client.get(reverse('my_challenges')).content.decode()

    assert 'beta for members first' not in body
    assert 'aria-disabled="true"' not in body


@open_beta
def test_when_the_beta_ends_a_free_hunter_gets_a_live_button(client):
    _hunter(client, premium=False)

    body = client.get(reverse('my_challenges')).content.decode()

    assert 'beta for members first' not in body
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


def test_an_unknown_type_cannot_be_posted(client):
    profile = _hunter(client)

    client.post(reverse('challenge_start', args=['calendar']), follow=True)

    assert not Challenge.objects.filter(profile=profile).exists()


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
