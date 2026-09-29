"""The reward surfaces: the two redeem doors, the panel's read layer, and what each viewer sees.

WHAT IS WORTH PINNING HERE, as opposed to in `test_challenge_rewards.py` (which owns the payout itself):

- **The doors must not inherit `_EditableRunMixin`.** It 404s a finished run, and a finished run is exactly
  when a hunter has the most XP to claim. That mixin is right for every OTHER write on this page, which is
  what makes this the easy mistake to make -- and a functional test of a mid-run claim would never catch it.
- **A refusal must arrive as a refusal.** `RewardError` subclasses `ChallengeError` so the views' single
  `except` covers it; if that subclassing is ever undone, every refusal here becomes a 500.
- **The panel and the payout must agree.** The panel offers exactly what the write will pay, and says so in
  a count -- so a Claim button can never be a promise the service breaks.
- **A visitor sees the worth and none of the buttons.** The run page is public; claiming is not.
"""
import json

import pytest
from django.urls import reverse
from django.utils import timezone

from challenges.models import CHALLENGE_TYPE_AZ, CHALLENGE_TYPE_JOBS
from challenges.services import challenge_service as svc
from challenges.services import rewards
from tests.factories import ConceptFactory, GameFactory, IGDBMatchFactory, ProfileFactory, UserFactory
from trophies.models import Contract, ContractXPGrant, EarnedContract, Job
from trophies.util_modules.constants import CHALLENGE_SLOT_JOB_XP

pytestmark = pytest.mark.django_db

_SEQ = {'n': 0}


def _hunter(client=None):
    user = UserFactory()
    profile = ProfileFactory(user=user, user_is_premium=True)
    if client is not None:
        client.force_login(user)
    return profile


def _contract(name, *, jobs=()):
    _SEQ['n'] += 1
    c = Contract.objects.create(name=name, slug=f"{name.lower().replace(' ', '-')}-{_SEQ['n']}",
                                is_live=True, igdb_id=820_000 + _SEQ['n'])
    if jobs:
        c.jobs.set(jobs)
    concept = ConceptFactory(anchor_migration_completed_at=timezone.now())
    IGDBMatchFactory(concept=concept, igdb_id=c.igdb_id, status='accepted')
    GameFactory(concept=concept)
    return c


def _jobs_run(profile, count=2):
    """A jobs run with `count` squares completed through detection, like a hunter's own."""
    challenge = svc.start(profile, CHALLENGE_TYPE_JOBS)
    jobs = list(Job.objects.order_by('slug')[:count])
    for job in jobs:
        contract = _contract(f'Game for {job.slug}', jobs=[job])
        svc.assign(challenge, profile, job.slug, contract)
        EarnedContract.objects.create(profile=profile, contract=contract, has_platinum=True,
                                      platinum_reached_at=timezone.now())
    svc.detect_for_profile(profile)
    challenge.refresh_from_db()
    return challenge, jobs


def _finish_jobs_run(profile):
    """A COMPLETE jobs run, which is the state the doors must work in and a mixin would 404."""
    return _jobs_run(profile, count=Job.objects.count())


def _redeem_url(challenge, key):
    return reverse('challenge_redeem', args=[challenge.id, key])


def _all_url(challenge):
    return reverse('challenge_redeem_all', args=[challenge.id])


# ── the doors, on the run state that matters ─────────────────────────────────────────────────────

def test_a_finished_run_can_still_be_claimed(client):
    """THE TEST FOR THE MIXIN THAT ISN'T THERE. Every other write door on this page resolves through
    `_EditableRunMixin`, which 404s a complete or hidden run -- correct for editing, and fatal here: a
    finished run is when every square is claimable. Wired that way, the whole feature would 404."""
    profile = _hunter(client)
    challenge, jobs = _finish_jobs_run(profile)
    assert challenge.is_complete is True, 'the fixture must actually finish the run'

    resp = client.post(_redeem_url(challenge, jobs[0].slug))

    assert resp.status_code == 200
    assert json.loads(resp.content)['granted'] == CHALLENGE_SLOT_JOB_XP


def test_a_hidden_run_can_still_be_claimed(client):
    """Hiding is visibility, not a pause: detection keeps completing a hidden run's squares, so the XP
    those squares earned is still owed. `_EditableRunMixin` would 404 this too."""
    profile = _hunter(client)
    challenge, jobs = _jobs_run(profile)
    svc.hide(challenge, profile)

    resp = client.post(_redeem_url(challenge, jobs[0].slug))

    assert resp.status_code == 200
    assert json.loads(resp.content)['granted'] == CHALLENGE_SLOT_JOB_XP


def test_claim_all_pays_a_whole_run_in_one_request(client):
    profile = _hunter(client)
    challenge, _jobs = _finish_jobs_run(profile)
    expected = Job.objects.count() * CHALLENGE_SLOT_JOB_XP

    resp = client.post(_all_url(challenge))

    assert json.loads(resp.content)['granted'] == expected
    assert ContractXPGrant.objects.filter(profile=profile, source='challenge').count() == Job.objects.count()


def test_a_second_claim_answers_a_sentence_not_a_500(client):
    """`RewardError` subclasses `ChallengeError`, which is the only reason the view's single `except`
    catches it. Undo that subclassing and this becomes a 500 -- the refusal a hunter is most likely to
    trigger, by double-pressing."""
    profile = _hunter(client)
    challenge, jobs = _jobs_run(profile)
    client.post(_redeem_url(challenge, jobs[0].slug))

    resp = client.post(_redeem_url(challenge, jobs[0].slug))

    assert resp.status_code == 400
    assert 'already been redeemed' in json.loads(resp.content)['error']


def test_claim_all_twice_is_not_an_error(client):
    """A hunter pressing it twice, or whose other tab claimed first, has done nothing wrong."""
    profile = _hunter(client)
    challenge, _jobs = _jobs_run(profile)
    client.post(_all_url(challenge))

    resp = client.post(_all_url(challenge))

    assert resp.status_code == 200
    assert json.loads(resp.content)['granted'] == 0


def test_an_a_z_run_refuses_both_doors(client):
    profile = _hunter(client)
    challenge = svc.start(profile, CHALLENGE_TYPE_AZ)

    one = client.post(_redeem_url(challenge, 'A'))
    every = client.post(_all_url(challenge))

    assert one.status_code == 400 and every.status_code == 400
    assert 'Job Coverage' in json.loads(one.content)['error']


def test_somebody_elses_run_is_a_404_not_a_403(client):
    """Resolved through `profile=`, so an id alone cannot confirm another hunter's run exists -- the same
    rule every other door on this page follows."""
    owner = _hunter()
    challenge, jobs = _jobs_run(owner)
    _hunter(client)

    resp = client.post(_redeem_url(challenge, jobs[0].slug))

    assert resp.status_code == 404
    assert not ContractXPGrant.objects.filter(source='challenge').exists()


def test_an_anonymous_claim_is_refused_in_json_never_redirected(client):
    """A 302 to the login page is followed by `fetch`, which then reads a 200 with HTML -- so the client's
    SUCCESS path runs against a login screen. The whole reason these views sit on `_ChallengeJsonView`."""
    owner = _hunter()
    challenge, jobs = _jobs_run(owner)

    resp = client.post(_redeem_url(challenge, jobs[0].slug))

    assert resp.status_code in (401, 403), 'a redirect would read as success to the caller'
    assert resp['Content-Type'].startswith('application/json')


def test_a_get_is_not_a_claim(client):
    """POST-only. A GET that paid XP would be claimable by a crawler, a prefetch or a pasted link."""
    profile = _hunter(client)
    challenge, jobs = _jobs_run(profile)

    resp = client.get(_redeem_url(challenge, jobs[0].slug))

    assert resp.status_code == 405
    assert not ContractXPGrant.objects.filter(source='challenge').exists()


# ── what the reply carries ───────────────────────────────────────────────────────────────────────

def test_the_reply_carries_the_square_and_the_panel(client):
    """SERVER-RENDERED, both of them, so the client never composes markup: the square comes from
    `_square_body.html` and the panel from `_rewards_panel.html` -- the same templates the page used."""
    profile = _hunter(client)
    challenge, jobs = _jobs_run(profile)

    data = json.loads(client.post(_redeem_url(challenge, jobs[0].slug)).content)

    assert [s['key'] for s in data['squares']] == [jobs[0].slug]
    # NAMES WHAT IT ASSERTS. `'pp-csq' in html` was satisfied by any of the square's inner classes, and
    # `'pp-cpay' in rewards_html` by the wrapper alone even if every row had been dropped -- both claimed
    # more than they checked. These assert the things that would actually be wrong.
    assert jobs[0].name in data['squares'][0]['html'] or challenge.slots.get(
        key=jobs[0].slug).contract_name in data['squares'][0]['html']
    # NOT on the presence of a Claim button: this panel uses the dual-state pattern, so every row renders
    # BOTH states and CSS reveals one. `data-cpay-claim` is in the markup of a paid row too, which makes its
    # presence meaningless as an assertion -- the row's MODIFIER is the state.
    assert 'pp-cpay__row--paid' in data['rewards_html'], 'the claimed square is marked paid'
    assert 'data-cpay-all' in data['rewards_html'], 'the other square is still claimable'
    assert rewards.pending_xp(challenge) == CHALLENGE_SLOT_JOB_XP


def test_an_unclaimed_square_carries_a_pip_on_the_page(client):
    """THE POSITIVE CASE, which was missing -- and its absence is why the test below was green while the pip
    demonstrably never left a hunter's screen. With only the negative assertion, deleting `xp_pending`
    outright passes: the pip then never exists anywhere, which is not what "loses its pip" means.
    """
    profile = _hunter(client)
    challenge, jobs = _jobs_run(profile)

    body = client.get(reverse('challenge_detail', args=[challenge.id])).content.decode()

    # AGAINST `len(jobs)`, not a literal: the 2 came from `_jobs_run`'s default argument, so changing that
    # default would have quietly changed what this test claimed without going red.
    assert body.count('pp-csq__pip') == len(jobs), 'one pip per unclaimed finished square'

    # AND NOT ON A SQUARE NOTHING CAN PAY. A pip promises XP that `redeem_all` skips when the job is gone,
    # which is the `atom is not None` term in `xp_pending` -- untested until now.
    Job.objects.filter(slug=jobs[0].slug).delete()
    body = client.get(reverse('challenge_detail', args=[challenge.id])).content.decode()
    assert body.count('pp-csq__pip') == len(jobs) - 1
    # AND THE SQUARE IS STILL DRAWN. Without this, a mutation that dropped atom-less squares from the grid
    # entirely would satisfy the count too -- "the pip went" and "the square went" are different facts.
    assert 'data-key="%s"' % jobs[0].slug in body


def test_a_completed_square_carries_its_key_so_a_claim_can_find_it(client):
    """THE BUG THE WHOLE PAYLOAD DEPENDED ON. `data-key` lived only on the editable `<button>` branch, and a
    square with unclaimed XP is by definition COMPLETE -- so it renders as the read-only `<div>`, and the
    client could never match the re-rendered squares a claim returns. The panel updated, the toast fired,
    and every orange pip stayed on the board until a manual reload.

    Asserted on the RENDERED PAGE rather than on the reply, because the reply was always correct: the gap
    was in what the page gave the client to find.
    """
    profile = _hunter(client)
    challenge, jobs = _jobs_run(profile)

    body = client.get(reverse('challenge_detail', args=[challenge.id])).content.decode()

    # SCOPED TO THE SQUARE, not searched across the whole body: a bare substring check would pass if the
    # key were put on the `<li>` wrapper, which the client does not look at.
    import re
    for job in jobs:
        pattern = r'<div[^>]*class="[^"]*pp-csq[^"]*"[^>]*data-key="%s"' % re.escape(job.slug)
        assert re.search(pattern, body), '%s has no key on its own square' % job.slug


def test_the_claimed_square_loses_its_pip(client):
    """The pip is the only thing on the grid that says XP is waiting, so it has to go when the XP is paid --
    and it goes because the SERVER re-rendered the square, not because the client removed a class."""
    profile = _hunter(client)
    challenge, jobs = _jobs_run(profile)

    data = json.loads(client.post(_redeem_url(challenge, jobs[0].slug)).content)

    assert 'pp-csq__pip' not in data['squares'][0]['html']


def test_claim_all_returns_every_square_it_paid(client):
    profile = _hunter(client)
    challenge, jobs = _jobs_run(profile, count=3)

    data = json.loads(client.post(_all_url(challenge)).content)

    assert sorted(s['key'] for s in data['squares']) == sorted(j.slug for j in jobs)
    # The panel that comes back must say there is nothing left, rather than merely existing. Asserted on
    # Claim-ALL, which is rendered only while something is claimable -- a per-row Claim button is present in
    # both states by design and so cannot carry this.
    assert 'data-cpay-all' not in data['rewards_html']
    assert data['rewards_html'].count('pp-cpay__row--paid') == len(jobs)
    assert rewards.pending_xp(challenge) == 0


def test_the_claim_all_reply_does_not_scale_with_the_squares_it_paid():
    """THE PIN F1 NEEDED. The reply re-renders every square it paid, and the first batched version still
    lazy-loaded each square's contract -- 25 identical single-row fetches inside one response, on the request
    that is already holding the run's row lock and 25 `ProfileJobXP` locks. It was an improvement on the 125
    it replaced and it was not flat, while its docstring implied otherwise.

    MEASURED AS A DIFFERENCE, not as an absolute: the exact number moves whenever the panel or the cover
    chain changes, and a test that pins it teaches the next person to delete the test. What must hold is that
    paying FIVE squares costs the same as paying two.
    """
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    from challenges import views as challenge_views

    def cost(count):
        profile = _hunter()
        challenge, _jobs = _jobs_run(profile, count=count)
        slots, granted = rewards.redeem_all(challenge, profile)
        assert granted == count * CHALLENGE_SLOT_JOB_XP, 'the fixture must actually pay'
        with CaptureQueriesContext(connection) as captured:
            challenge_views._redeem_json(challenge, slots, granted)
        return len(captured.captured_queries)

    two, five = cost(2), cost(5)

    assert five == two, (
        'the reply scales with the squares it paid: %d for two, %d for five' % (two, five))

# ── the panel's read layer ───────────────────────────────────────────────────────────────────────

def test_the_panel_offers_exactly_what_the_write_will_pay():
    """The count on the Claim-all button IS `redeemable_slots`, so the button cannot promise a payout the
    service skips. This is the divergence an audit found in the first draft, where the panel counted every
    row -- including paid ones -- and offered "Claim all (25)" with nothing left."""
    profile = _hunter()
    challenge, jobs = _jobs_run(profile, count=3)

    before = rewards.summary(challenge)
    assert before['claimable_count'] == 3
    assert before['pending_xp'] == 3 * CHALLENGE_SLOT_JOB_XP

    rewards.redeem_slot(challenge, profile, jobs[0].slug)
    after = rewards.summary(challenge)

    assert after['claimable_count'] == 2
    assert len(after['rows']) == 3, 'a paid square stays in the ledger'
    assert [r['is_paid'] for r in after['rows']].count(True) == 1


def test_the_ledger_keeps_a_square_whose_job_was_deleted_and_offers_no_button():
    """It can never be paid, so it is neither claimable nor paid -- and it stays in the ledger, because a
    finished square vanishing from its own record is worse than one that cannot be claimed."""
    profile = _hunter()
    challenge, jobs = _jobs_run(profile, count=2)
    Job.objects.filter(slug=jobs[0].slug).delete()

    summary = rewards.summary(challenge)
    row = [r for r in summary['rows'] if r['key'] == jobs[0].slug][0]

    assert row['is_paid'] is False
    assert row['claimable'] is False
    assert summary['claimable_count'] == 1


def test_an_unfinished_run_advertises_the_title_it_is_playing_for():
    profile = _hunter()
    challenge = svc.start(profile, CHALLENGE_TYPE_AZ)

    summary = rewards.summary(challenge)

    assert summary['title_name'] == 'A-Z Champion'
    assert summary['title_earned'] is False
    assert summary['per_square'] == 0, 'A-Z pays no job XP, ever'


def test_a_second_completion_reports_Legend_and_a_third_reports_no_new_title():
    """THE TENSE CASES `title_earned` EXISTS FOR, and nothing covered them: every other test exercised the
    first completion, where an off-by-one ordinal and a correct one are indistinguishable.
    """
    profile = _hunter()
    first, _jobs = _finish_jobs_run(profile)
    assert rewards.summary(first)['title_name'] == 'Job Challenge Champion'

    second, _more = _finish_jobs_run(profile)
    summary = rewards.summary(second)
    assert summary['title_name'] == 'Job Challenge Legend'
    assert summary['title_earned'] is True
    # And the FIRST run still reports what IT earned, not what the latest run did.
    assert rewards.summary(first)['title_name'] == 'Job Challenge Champion'

    third, _yet_more = _finish_jobs_run(profile)
    third_summary = rewards.summary(third)
    assert third_summary['title_name'] is None, 'a third completion mints nothing new'
    # The XP is still paid, though, which is the distinction the Start card got wrong.
    assert third_summary['per_square'] == CHALLENGE_SLOT_JOB_XP


def test_a_third_run_start_card_still_advertises_its_xp():
    """THE START CARD'S OWN BUG. `_reward` returned None whenever there was no title left to earn, and the
    template gated the whole line on it -- so a hunter with two completed Job Coverage runs saw a card
    advertising nothing while 6,000 XP a square was still being paid. The run page, which gates on
    `per_square`, said otherwise: two surfaces disagreeing about the same reward.
    """
    from challenges.views import MyChallengesView

    reward = MyChallengesView._reward(CHALLENGE_TYPE_JOBS, completed=2)

    assert reward is not None
    assert reward['title_name'] is None
    assert reward['per_square'] == CHALLENGE_SLOT_JOB_XP

    # And A-Z past its second completion genuinely has nothing to say, so it stays absent.
    assert MyChallengesView._reward(CHALLENGE_TYPE_AZ, completed=2) is None


def test_a_finished_run_reports_the_title_it_earned():
    profile = _hunter()
    challenge, _jobs = _finish_jobs_run(profile)

    summary = rewards.summary(challenge)

    assert summary['title_name'] == 'Job Challenge Champion'
    assert summary['title_earned'] is True


# ── what each viewer sees ────────────────────────────────────────────────────────────────────────

def test_a_visitor_sees_what_the_run_is_worth_and_no_claim_buttons(client):
    """The run page is public because the Hall of Fame is the point; claiming is not. So the worth is
    rendered for everybody and the buttons are gated on ownership INSIDE the partial."""
    owner = _hunter()
    challenge, _jobs = _jobs_run(owner)
    _hunter(client)

    body = client.get(reverse('challenge_detail', args=[challenge.id])).content.decode()

    assert 'pp-cpay' in body, 'a visitor still sees what the run pays'
    assert 'data-cpay-claim' not in body
    assert 'data-cpay-all' not in body


def test_the_owner_sees_the_claim_buttons(client):
    profile = _hunter(client)
    challenge, jobs = _jobs_run(profile)

    body = client.get(reverse('challenge_detail', args=[challenge.id])).content.decode()

    assert 'data-cpay-all' in body
    assert 'data-cpay-claim="%s"' % jobs[0].slug in body


def test_an_a_z_page_shows_its_title_and_no_xp_panel(client):
    profile = _hunter(client)
    challenge = svc.start(profile, CHALLENGE_TYPE_AZ)

    body = client.get(reverse('challenge_detail', args=[challenge.id])).content.decode()

    assert 'A-Z Champion' in body
    assert 'job XP waiting' not in body, 'there is no job XP on an A-Z run to wait for'


def test_an_a_z_run_draws_no_reward_panel(client):
    """A-Z PAYS NO XP, so there is no payout panel at all -- not an empty one.

    The title used to live in the panel, which made an A-Z run render a full-width bordered card containing
    one line of text: exactly the "empty card reads as a broken block" failure the gate was written to
    avoid, catching a different case than the one that bit. The title is a fact about the RUN, so it moved
    to the header card beside the Finished/Hidden chip, and the section is now gated on the payout alone.
    """
    profile = _hunter(client)
    run = svc.start(profile, CHALLENGE_TYPE_AZ)

    body = client.get(reverse('challenge_detail', args=[run.id])).content.decode()

    assert 'data-cpay-wrap' not in body, 'an A-Z run has no payout panel'
    # And the title is still on the page, in the header card where it belongs.
    assert 'A-Z Champion' in body


def test_the_start_card_says_what_each_type_is_worth(client):
    """The one surface a hunter sees BEFORE committing. Leaving the reward off it made the reward a
    surprise, and made the two types impossible to compare without starting one."""
    _hunter(client)

    body = client.get(reverse('my_challenges')).content.decode()

    assert 'A-Z Champion' in body
    assert 'Job Challenge Champion' in body
    # The XP line only where there is XP.
    assert 'job XP per square' in body


def test_the_start_card_and_the_run_page_quote_the_same_figure(client):
    """Two surfaces, one constant. A second hard-coded figure is how a page ends up advertising XP the
    service does not pay."""
    profile = _hunter(client)
    challenge, _jobs = _jobs_run(profile)

    card = client.get(reverse('my_challenges')).content.decode()
    page = client.get(reverse('challenge_detail', args=[challenge.id])).content.decode()

    figure = '{:,}'.format(CHALLENGE_SLOT_JOB_XP)
    assert figure in card
    assert figure in page


# ── the fixes a functional test cannot see ───────────────────────────────────────────────────────

def _challenge_css():
    from pathlib import Path
    return (Path(__file__).resolve().parents[2] / 'static' / 'css' / 'components'
            / 'challenges.css').read_text(encoding='utf-8')


def _rule(css, selector):
    """The declarations of one rule, by exact selector at the start of a line.

    `re.escape` AND NOTHING ELSE. The first version stripped every escape `re.escape` added and then put a
    single literal backslash back in front -- which happened to re-escape a leading dot and so worked for
    every selector tried, while turning `button.pp-csq` into `\b` (a word boundary) and silently returning
    ''. A helper whose failure mode is an empty string is one that makes its callers vacuous, so it raises
    instead: a guard that cannot find its rule has stopped guarding anything.
    """
    import re
    m = re.search(r'^%s \{(.*?)\}' % re.escape(selector), css, re.S | re.M)
    assert m, 'no rule found for %s -- this guard would otherwise pass vacuously' % selector
    return m.group(1)


def test_the_pip_does_not_share_the_key_chips_corner():
    """IT DID, and every unclaimed square was affected. `.pp-csq__key` is at `top: 6px; left: 6px;
    z-index: 3`, and the pip shipped at the identical box -- so the orange badge painted straight over the
    job's discipline icon (or the A-Z letter) on every square with XP waiting, at every breakpoint. A square
    with unclaimed XP is always filled, so the chip is always there to be covered.

    PINNED AS A RULE ABOUT THE TWO SELECTORS rather than as a screenshot, because this is the kind of
    collision that is invisible in markup and obvious on screen -- and the comment that shipped with the bug
    had checked the OTHER corner and concluded it was safe.
    """
    css = _challenge_css()
    pip = _rule(css, '.pp-csq__pip')
    key = _rule(css, '.pp-csq__key')
    check = _rule(css, '.pp-csq__check')

    # THE OTHER TWO CORNERS ARE THE POINT. Asserting only that the pip is not at `left: 6px` passed for
    # `right: 6px` (straight onto the check), for `left: 8px` (still over the chip), and for having no
    # horizontal position at all -- three ways to reintroduce the bug this guard is named for.
    assert 'left: 6px' in key, 'the chip owns the top-left; if that moved, this guard needs rewriting'
    assert 'right: 6px' in check and 'width: 18px' in check, 'the check owns the top-right'
    # A DECLARATION, not a substring. `'left' not in pip` false-trips on any `padding-left`, `border-left`,
    # `text-align: left` or the word "left" in an in-block comment -- and is DEFEATED by
    # `inset-inline-start: 6px`, which anchors the pip left again while passing both assertions.
    import re
    assert re.search(r'(^|\s)(left|inset-inline-start)\s*:', pip) is None, (
        'the pip must not be anchored left, where the key chip lives')
    assert 'right: 28px' in pip, (
        'the pip must clear the check: 6px edge + its 18px + a 4px gap')


def test_the_claim_button_meets_the_touch_floor():
    """THE ARGUMENT FOR MOVING THE CLAIM OFF THE SQUARE was that a ~109px tile cannot give a button a real
    target -- both the panel and the stylesheet say so by name. `.pp-cta` is about 29px and declares no
    `min-height`, so without this the reward panel did not honour the reason it exists."""
    css = _challenge_css()

    assert 'min-height: 44px' in _rule(css, '.pp-cpay__claim')


def test_the_focus_target_survives_the_last_claim():
    """PINNED BY SOURCE TEXT, which is how this project pins JS (there is no runner).

    The fallback chain has to end at something that is ALWAYS rendered. It first ended at the XP headline --
    which lives inside the same `pending_xp` block as Claim-all, so the two were co-extensive and the
    fallback was unreachable. The case it existed for is claiming the LAST owed square: the whole block
    disappears, and without a real fallback a keyboard user is dropped onto `<body>`.
    """
    from pathlib import Path

    js = (Path(__file__).resolve().parents[2] / 'static' / 'js'
          / 'challenge-detail.js').read_text(encoding='utf-8')
    # COMMENTS STRIPPED, because the absence assertion below matched the comment that EXPLAINS the absence.
    # `test_challenge_detail_js.py` records the same trap for `innerHTML`: an absence assertion has to carry
    # the syntax of the thing it forbids, or the prose about the rule breaks the test for the rule.
    code = '\n'.join(ln for ln in js.splitlines()
                     if not ln.lstrip().startswith(('//', '*', '/*', '*/')))

    assert "wrap.querySelector('.pp-cpay')" in code, 'the fallback must reach the panel root'
    # And only for a keyboard press: moving focus after a mouse click scrolls the hunter back to the top.
    #
    # `event.detail === 0`, NOT `matches(':focus-visible')`. This assertion used to require the latter --
    # which was dead code, because `post` disables the button before the request and a disabled element is
    # blurred, so it could never match. A source-text pin cannot see that, so the test certified a branch
    # that never ran. It also cannot see a `matches()` that THROWS on an older engine, which is the other
    # half of why the selector is gone.
    assert 'event.detail === 0' in code, 'keyboard-ness must be read at click time, before the disable'
    assert "matches(':focus-visible')" not in code, 'a disabled button can never match :focus-visible'
    assert 'preventScroll: true' in code


def test_every_claim_button_is_distinguishable_to_a_screen_reader(client):
    """Twenty-five buttons whose whole accessible name is "Claim" are one button to a rotor. The visible
    label stays short; `aria-label` carries the row."""
    profile = _hunter(client)
    challenge, jobs = _jobs_run(profile, count=3)

    body = client.get(reverse('challenge_detail', args=[challenge.id])).content.decode()

    import re
    labels = re.findall(r'aria-label="Claim [\d,]+ XP for ([^"]+)"', body)
    assert len(labels) == 3
    assert len(set(labels)) == 3, 'each row names its own job: %r' % labels
    # THE JOB'S NAME, not its slug: `card-shark` in an accessible name reads as a content bug, and the
    # slug would satisfy a distinctness check just as well.
    assert set(labels) == {job.name for job in jobs}, labels
    # The figure is in the name too, so the label says what pressing it is worth. WCAG 2.5.3 still holds:
    # the visible word "Claim" is contained in the accessible name.
    assert 'aria-label="Claim %s XP for' % '{:,}'.format(CHALLENGE_SLOT_JOB_XP) in body
