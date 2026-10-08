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

#: Used where a CSS pin has to be anchored to the start of a line.
NL = chr(10)


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


def _panel_halves(body):
    """(open list, collapsed disclosure) for the reward panel only.

    SCOPED TO THE PANEL FIRST. Partitioning the whole page on `<details` puts EVERYTHING after the disclosure
    into the second half -- including the board, whose squares render the same game names the ledger does -- so
    a "not in the collapsed half" assertion was reading the rest of the document and failed on a row that was
    correctly in the open list.
    """
    panel = body.split('data-cpay-wrap', 1)[1].split('</section>', 1)[0]
    head, _sep, collapsed = panel.partition('<details')
    return head, collapsed


def _row_name(job):
    """The ledger row's game name for a fixture job, which identifies the row without a test-only hook.

    `_jobs_run` names each contract `Game for <slug>`, and `slot.contract_name` is what the row renders -- so
    this is content, unique per job, and it survives markup changes that an attribute would not.
    """
    return 'Game for %s' % job.slug


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
        slots, granted, _ceremony = rewards.redeem_all(challenge, profile)
        assert granted == count * CHALLENGE_SLOT_JOB_XP, 'the fixture must actually pay'
        with CaptureQueriesContext(connection) as captured:
            challenge_views._redeem_json(challenge, slots, granted, _ceremony, profile)
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

    reward = MyChallengesView._reward(CHALLENGE_TYPE_JOBS, completed=2, profile=None)

    assert reward is not None
    assert reward['title_name'] is None
    assert reward['per_square'] == CHALLENGE_SLOT_JOB_XP

    # And A-Z past its second completion genuinely has nothing to say, so it stays absent.
    assert MyChallengesView._reward(CHALLENGE_TYPE_AZ, completed=2, profile=None) is None


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
    # NEWLINES NORMALISED, because several pins in this file anchor a selector to the start of a line with
    # `chr(10)`. `read_text` uses universal newlines today, but this repo's `core.autocrlf` converts the working
    # copy to CRLF on its next touch, and a pin that depends on a checkout setting fails on a fresh clone rather
    # than on a real regression.
    raw = (Path(__file__).resolve().parents[2] / 'static' / 'css' / 'components'
           / 'challenges.css').read_text(encoding='utf-8')
    return raw.replace(chr(13) + chr(10), chr(10))


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


# ── the ledger's shape (design audit + owner decision, 2026-09-30) ───────────────────────────────

def test_the_claimed_rows_collapse_and_the_claimable_ones_do_not(client):
    """THE OWNER'S CHOICE, and the reason is height: a finished run has 25 finished squares, which was
    ~1,250px of ledger on desktop and ~2,300px at 375px where the rows wrap -- all of it above the board the
    page is about, and on a finished run every row of it inert.

    THE SPLIT IS BY WHAT A HUNTER CAN ACT ON, not by count: rows with a Claim button stay in the open list,
    paid rows collapse behind a disclosure labelled with how many there are. A fully-paid run is one line.
    """
    profile = _hunter(client)
    challenge, jobs = _jobs_run(profile, count=3)
    rewards.redeem_slot(challenge, profile, jobs[0].slug)

    body = client.get(reverse('challenge_detail', args=[challenge.id])).content.decode()

    assert 'Claimed (1)' in body, 'the disclosure says how many are inside it'
    open_list, collapsed = _panel_halves(body)
    assert 'data-cpay-claim' in open_list, 'the claimable rows are NOT behind the disclosure'
    assert 'data-cpay-claim' not in collapsed, 'and nothing actionable is hidden inside it'
    # BOTH DIRECTIONS. The three assertions above only check that claimable rows stay OUT of the disclosure --
    # a mutation that put every row in the open list as well passed all of them, because the disclosure still
    # rendered its own copy. What makes this a split rather than a duplication is that a paid row appears in
    # exactly one place.
    #
    # IDENTIFIED BY THE GAME NAME, which the fixture makes unique per job (`Game for <slug>`). It replaced a
    # `data-cpay-row` attribute that existed only for a post-claim flash; when that flash moved onto the drawer
    # the attribute had no consumer left and was deleted, so pinning it would have been pinning nothing.
    assert _row_name(jobs[0]) not in open_list, 'a paid row is not also in the open list'
    assert _row_name(jobs[1]) not in collapsed, 'nor an unpaid one in the disclosure'


def test_a_fully_claimed_run_collapses_its_whole_ledger(client):
    """The Hall-of-Fame case, and the worst one for height: every square finished, every one paid, nothing to
    act on. The open list should not render at all."""
    profile = _hunter(client)
    challenge, _jobs = _finish_jobs_run(profile)
    rewards.redeem_all(challenge, profile)
    total = Job.objects.count()

    body = client.get(reverse('challenge_detail', args=[challenge.id])).content.decode()

    assert 'Claimed (%d)' % total in body
    head, collapsed = _panel_halves(body)
    # THE LIST ELEMENT ITSELF, not just its rows, and that distinction is the whole test. Gating the open
    # `<ul>` on `rows` rather than on the UNPAID count leaves an empty `<ul role="list">` with its own top
    # margin sitting above the disclosure -- and an empty list contains no rows, so an assertion about rows
    # cannot see it. A first version of this line asserted a per-row attribute's absence and a mutation run
    # proved it caught nothing: `class="pp-cpay__rows"` is the open list (the collapsed one is
    # `pp-cpay__rows pp-cpay__rows--done`), so that is what must be absent.
    assert 'class="pp-cpay__rows"' not in head, 'not even an empty open ledger when nothing is owed'
    # COUNTED BY THE CLASS ONLY A COLLAPSED ROW CARRIES. Not `pp-cpay__row`, which is a substring of both
    # `pp-cpay__row--paid` and `pp-cpay__rows` and read 52 for 25 rows.
    assert collapsed.count('pp-cpay__row--paid') == total, 'and every paid row is inside the disclosure'


def test_the_panel_says_what_a_whole_run_pays(client):
    """THE FORWARD-LOOKING NUMBER, in the foot rather than the headline (owner, 2026-09-30). The headline
    measures what has been CLAIMED and keeps that meaning for the whole run -- so on a fresh run it is a zero,
    and the answer to "what is this worth" goes where somebody asking it will read it instead of swapping what
    the big number means part-way through.
    """
    profile = _hunter(client)
    challenge, _jobs = _jobs_run(profile, count=1)
    full = CHALLENGE_SLOT_JOB_XP * challenge.total_slots

    body = client.get(reverse('challenge_detail', args=[challenge.id])).content.decode()

    assert '{:,} for a full run'.format(full) in body
    assert rewards.summary(challenge)['full_xp'] == full, 'and the service owns the arithmetic'


def test_an_untouched_run_offers_the_figure_without_a_claimed_total_to_show(client):
    """The zero state: the headline is honestly 0 and the foot still answers what the run is worth."""
    profile = _hunter(client)
    challenge = svc.start(profile, CHALLENGE_TYPE_JOBS)

    body = client.get(reverse('challenge_detail', args=[challenge.id])).content.decode()

    assert '{:,} for a full run'.format(CHALLENGE_SLOT_JOB_XP * challenge.total_slots) in body
    assert rewards.summary(challenge)['paid_count'] == 0
    assert 'Claimed (' not in body, 'and no empty disclosure'


def test_the_reward_card_joins_the_pages_entrance(client):
    """EVERY OTHER BLOCK ON THIS PAGE HAS ONE: the header card's body cascades and the board's squares stagger
    in, and the reward card between them simply appeared. Career runs the cascade on both of its stacked
    cards, which is the precedent this follows."""
    profile = _hunter(client)
    challenge, _jobs = _jobs_run(profile, count=1)

    body = client.get(reverse('challenge_detail', args=[challenge.id])).content.decode()

    assert 'scard mb-3 pp-head-cascade' in body


def test_the_job_chip_does_not_repeat_itself_in_a_tooltip(client):
    """`.pp-jobchip__name` renders the row's label with no clamp, so a `title` carrying the same string is
    noise a screen reader reads twice. The `title` on the game name is the one that earns it -- that text IS
    ellipsised."""
    profile = _hunter(client)
    challenge, jobs = _jobs_run(profile, count=1)
    label = Job.objects.get(slug=jobs[0].slug).name

    body = client.get(reverse('challenge_detail', args=[challenge.id])).content.decode()
    panel = body.split('data-cpay-wrap')[1].split('</section>')[0]

    assert 'title="%s"' % label not in panel, 'the chip repeats nothing'
    assert 'pp-cpay__game" title=' in panel, 'while the ellipsised game name keeps its tooltip'


def test_every_control_in_the_panel_meets_the_touch_floor():
    """THE ARGUMENT FOR MOVING THE CLAIM OFF THE SQUARE was that a ~109px tile cannot give a button a real
    target -- both the panel and the stylesheet say so by name. `.pp-cta` is about 32px and declares no
    `min-height`, so without this the reward panel did not honour the reason it exists.

    ALL THREE CONTROLS, which is why this test was renamed. It pinned the per-row button only, and the one that
    was actually missing the floor was CLAIM-ALL -- the primary control of the feature, the button a hunter
    reaches for on a finished run, on a phone. A design audit found it at ~32px. The disclosure's summary is a
    control too, so it is named here as well; anything else added to that rule has to keep meeting the floor or
    this fails, which is the point of asserting the selector list rather than one selector.
    """
    css = _challenge_css()

    # TWO RULES NOW, and joined here rather than written as literals because `_rule` matches an exact selector
    # at the start of a line and the stylesheet puts each selector on its own. The summary moved out of the
    # buttons' rule because `flex: 0 0 auto` is inert on a child of a non-flex `<details>`; the attribute
    # selector gained a `.pp-cpay` scope.
    buttons = _rule(css, '\n'.join(['.pp-cpay__claim,', '.pp-cpay [data-cpay-all]']))
    summary = _rule(css, '.pp-cpay__done-sum')

    assert 'min-height: 44px' in buttons
    assert 'min-height: 44px' in summary, 'the disclosure is a control too'


def test_the_receipts_drawer_looks_like_a_control():
    """IT HAD NO AFFORDANCE AT ALL when it shipped. `display: flex` on a `<summary>` overrides the UA's
    `display: list-item`, which removes the native disclosure triangle in every engine -- so `CLAIMED (25)`
    rendered as a plain uppercase label, and on a finished run that line is the only thing between the panel's
    head and its foot. The comment above it claimed the native marker was kept.

    AND ITS DIVIDER NAMED A TOKEN THAT DOES NOT EXIST (`--pp-line`). `border-top: 1px solid var(--pp-line)` is
    a SHORTHAND, so an invalid value takes the whole declaration down to its initial values -- including
    `border-top-style: none`. The rule was absent, not mis-coloured, which is why nothing looked obviously
    wrong.
    """
    css = _challenge_css()
    sum_rule = _rule(css, '.pp-cpay__done-sum')

    assert 'list-style: none' in sum_rule, 'flex already removed the marker; say so explicitly'
    assert '.pp-cpay__done-sum::-webkit-details-marker' in css, 'older WebKit needs the pseudo-element too'
    # LINE-ANCHORED, because `.pp-cpay__done[open] > .pp-cpay__done-sum::before` CONTAINS the bare selector --
    # so `'.pp-cpay__done-sum::before' in css` passed with the base rule renamed away, satisfied by the
    # `[open]` rule alone. A mutation run found it; that is the third substring trap in this file's history
    # (`pp-cpay__row` in `pp-cpay__row--paid`, `pp-cpay__claim` in the merged selector list).
    assert (NL + '.pp-cpay__done-sum::before {') in css, 'and something has to replace it'
    assert (NL + '.pp-cpay__done[open] > .pp-cpay__done-sum::before') in css, 'which turns when it opens'
    assert 'var(--pp-divider)' in _rule(css, '.pp-cpay__done'), 'a real token, not `--pp-line`'


def test_every_token_the_challenge_css_uses_is_defined():
    """THE GENERAL FORM OF THE `--pp-line` BUG, which is worth a guard because its failure mode is silence: an
    undefined custom property in a SHORTHAND kills the whole declaration, and in a longhand falls back to the
    initial value. Either way nothing errors, nothing logs, and the rule simply does not apply.

    Definitions are looked for in `input.css` (the house `:root`) and in the component files, since a token
    scoped to one component is legitimate.
    """
    import re
    from pathlib import Path

    from django.conf import settings

    base = Path(settings.BASE_DIR) / 'static' / 'css'
    css = (base / 'components' / 'challenges.css').read_text(encoding='utf-8')
    sources = [(base / 'input.css').read_text(encoding='utf-8')]
    sources += [p.read_text(encoding='utf-8') for p in (base / 'components').glob('*.css')]

    used = set(re.findall(r'var\((--(?:pp|disc)-[a-z0-9-]+)', css))
    defined = set()
    for text in sources:
        defined |= set(re.findall(r'^\s*(--(?:pp|disc)-[a-z0-9-]+)\s*:', text, re.M))

    assert used, 'the guard found no tokens at all, so it is measuring nothing'
    assert not (used - defined), 'undefined in challenges.css: %s' % sorted(used - defined)


def test_the_focus_target_survives_the_last_claim():
    """PINNED BY SOURCE TEXT, which is how this project pins JS (there is no runner).

    The fallback chain has to end at something that is ALWAYS rendered, because the case it exists for is
    claiming the LAST owed square: Claim-all is gone from the swapped-in panel, and without a fallback a
    keyboard user is dropped onto `<body>` and has to tab the whole page back.

    WHICH LINK CATCHES THAT CASE HAS CHANGED, and this docstring had it backwards for a while. It said the XP
    headline sat inside the same `pending_xp` block as Claim-all and so was co-extensive with it, making the
    panel root the only real fallback. That was true when it was written and stopped being true when the
    panel's head lost its gate ("a head that never disappears"): `.pp-cpay__xp` now renders for any jobs run,
    so IT catches the last claim and `.pp-cpay` is the link with no known way to fire. The assertion below is
    unchanged and still right -- the chain must end somewhere unconditional -- but it is belt-and-braces, not
    the working fallback.
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


# ── the owed-XP indicator, per type and across runs (owner, 2026-09-30) ──────────────────────────

@pytest.mark.django_db
def test_the_card_points_at_xp_owed_on_a_run_it_is_not_showing(client):
    """THE CASE THE FEATURE EXISTS FOR, and the one a card-scoped version would have missed.

    `card['run']` is the ACTIVE run, or a resumable one, or nothing. A FINISHED run with unclaimed squares is
    none of those -- so before this, the Job Coverage card read "Start" while thousands of XP sat on a run the
    page did not mention. That is what the owner reported as the nav's XP pill looking stuck: the pill is
    profile-wide and the page was not, so nothing here could explain it.
    """
    profile = _hunter(client)
    finished, _jobs = _jobs_run(profile, count=Job.objects.count())
    assert finished.is_complete, 'the fixture needs a FINISHED run, which no card will show'
    assert svc.active_run(profile, CHALLENGE_TYPE_JOBS) is None, 'so the card is on its empty state'
    owed_xp = Job.objects.count() * CHALLENGE_SLOT_JOB_XP

    body = client.get(reverse('my_challenges')).content.decode()

    assert '+{:,} waiting'.format(owed_xp) in body, 'the figure, not a bare mark'
    # AND IT IS A ROUTE -- read off the CHIP, not the page. This page also lists finished runs with links, so
    # `reverse(...) in body` was true whether or not the chip carried the href, and a mutation replacing it
    # with `#` passed. The chip's own `href` is the only thing that answers "does this go anywhere".
    import re
    href = re.search(r'<a class="pp-cowed" href="([^"]+)"', body)
    assert href, 'the chip must render as a link'
    assert href.group(1) == reverse('challenge_detail', args=[finished.pk])


@pytest.mark.django_db
def test_the_indicator_is_absent_once_everything_is_claimed(client):
    profile = _hunter(client)
    challenge, _jobs = _jobs_run(profile, count=2)
    rewards.redeem_all(challenge, profile)

    body = client.get(reverse('my_challenges')).content.decode()

    # SCOPED TO THE CARDS. `'waiting' not in body` over a whole rendered document is the class of assertion
    # that has already gone wrong twice on this branch -- it passes or fails on copy anywhere on the page.
    cards = ''.join(c for c in body.split('<section class="card') if 'Challenge' in c)
    assert 'pp-cowed' not in cards
    assert 'waiting' not in cards


@pytest.mark.django_db
def test_an_a_z_card_never_shows_owed_xp(client):
    """A-Z pays no job XP, so its card has nothing to owe -- and the indicator is scoped by TYPE, which is what
    keeps a jobs run's owed XP off the A-Z card.

    SLICED ON THE CARD BOUNDARY, which is the fix for a pin that could not fail. It sliced from 'A-Z' to
    'pp-cwr', and the chip renders AFTER the reward line -- so the slice always ended before the chip's position
    and `'pp-cowed' not in az_card` was true whatever the code did. An audit proved it: deleting the per-type
    filter entirely, so a jobs run's XP rendered on BOTH cards, left 98 tests green. Nothing pinned the scoping.
    """
    profile = _hunter(client)
    _jobs_run(profile, count=2)
    svc.start(profile, CHALLENGE_TYPE_AZ)

    body = client.get(reverse('my_challenges')).content.decode()
    # PICKED BY THE CARD'S HEADING, not by index and not by the type's full name: the full name also appears in
    # a run's `data-name`, and the card heading is the short label. Splitting on `<section class="card` also
    # catches the page-header card, so
    # positional indices were off by one and silently read the wrong chunk -- a slice that lands on the wrong
    # element is the same defect as a slice that is too short.
    chunks = body.split('<section class="card')
    az = next(c for c in chunks if '>A-Z</h2>' in c)
    jobs = next(c for c in chunks if '>Job Coverage</h2>' in c)

    assert 'pp-cowed' in jobs, 'the jobs card has one'
    assert 'pp-cowed' not in az, 'and the A-Z card does not'


@pytest.mark.django_db
def test_owed_runs_is_one_aggregated_query_whatever_the_history(client):
    """Per the per-user queryset rule: a `values().annotate(Count)` over the partial index, not a fetch and a
    Python tally. A hunter with many finished runs must cost what a fresh one does."""
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    profile = _hunter()
    # EACH RUN MUST BE FINISHED BEFORE THE NEXT EXISTS -- runs are sequential, so a loop of `_jobs_run` would
    # RESUME the same run and then refuse to re-assign a completed square. Finishing is the only way to get a
    # second, which is also why this fixture is expensive and two runs is the sample.
    first, _a = _jobs_run(profile, count=Job.objects.count())
    assert first.is_complete

    with CaptureQueriesContext(connection) as one_run:
        rows_one = rewards.owed_runs(profile)

    second, _b = _jobs_run(profile, count=Job.objects.count())
    assert second.is_complete and second.pk != first.pk

    with CaptureQueriesContext(connection) as two_runs:
        rows_two = rewards.owed_runs(profile)

    assert len(one_run.captured_queries) == 1
    assert len(two_runs.captured_queries) == 1, 'one aggregate however many runs owe'
    assert len(rows_one) == 1 and len(rows_two) == 2, 'and each owing run is named'
    assert all(row['xp'] == row['owed'] * CHALLENGE_SLOT_JOB_XP for row in rows_two)
    # NEWEST FIRST -- arbitrary but STABLE. Not "the run they were last on": nothing tracks per-run activity,
    # which `owed_runs`' own docstring says in as many words.
    assert [row['challenge_id'] for row in rows_two] == [second.pk, first.pk]


@pytest.mark.django_db
def test_owed_runs_flags_a_hidden_run_and_still_counts_it():
    """Hidden runs owe real XP and the payout door still pays them, so leaving them out would make this
    disagree with the nav pill -- but My Challenges does not list them, so a caller sending somebody to the XP
    needs to know the link is the only route."""
    profile = _hunter()
    challenge, _jobs = _jobs_run(profile, count=2)
    svc.hide(challenge, profile)

    rows = rewards.owed_runs(profile)

    assert [row['challenge_id'] for row in rows] == [challenge.pk]
    assert rows[0]['is_hidden'] is True


@pytest.mark.django_db
def test_the_chip_describes_the_run_it_links_to(client):
    """EVERY FIGURE IS ABOUT THE LINKED RUN. `xp` was a SUM across every owing run while the link went to one of
    them, so the chip advertised "+300,000 waiting" and landed the hunter on a panel offering 150,000, with
    nothing on the page naming the other run. A chip that describes one run and links to another is the failure
    this feature exists to fix, one level in."""
    import re

    profile = _hunter(client)
    first, _a = _jobs_run(profile, count=Job.objects.count())
    second, _b = _jobs_run(profile, count=Job.objects.count())
    one_run = Job.objects.count() * CHALLENGE_SLOT_JOB_XP

    body = client.get(reverse('my_challenges')).content.decode()
    chunks = body.split('<section class="card')
    jobs = next(c for c in chunks if '>Job Coverage</h2>' in c)

    href = re.search(r'<a class="pp-cowed" href="([^"]+)"', jobs)
    assert href.group(1) == reverse('challenge_detail', args=[second.pk]), 'the newest owing run'
    assert '+{:,} waiting'.format(one_run) in jobs, "that run's figure, not the total of both"
    assert '+{:,} waiting'.format(one_run * 2) not in jobs
    # AND IT SAYS THERE IS MORE, in the accessible name, rather than pretending to total it.
    assert '1 other run also owes XP' in jobs
    del first


@pytest.mark.django_db
def test_the_chip_says_when_the_xp_is_not_on_the_card_s_own_run(client):
    """THE HALF THAT STOPS A MISREAD. On an empty card the button says Start, and Start creates a NEW run and
    pays nothing -- so a bare "+150,000 waiting" directly above it invites exactly the wrong press."""
    profile = _hunter(client)
    finished, _jobs = _jobs_run(profile, count=Job.objects.count())
    assert svc.active_run(profile, CHALLENGE_TYPE_JOBS) is None, 'so the card offers Start'

    body = client.get(reverse('my_challenges')).content.decode()

    assert 'waiting on another run' in body


@pytest.mark.django_db
def test_the_chip_calls_a_run_hidden_only_when_the_linked_one_is(client):
    """`hidden` was `any(...)` while the link was the newest run, so the label could call the linked run hidden
    when it was not -- and the actually-hidden run, the one the docstring says this link is the only route to,
    still had no route from this page."""
    profile = _hunter(client)
    older, _a = _jobs_run(profile, count=Job.objects.count())
    svc.hide(older, profile)
    newer, _b = _jobs_run(profile, count=Job.objects.count())

    body = client.get(reverse('my_challenges')).content.decode()
    chunks = body.split('<section class="card')
    jobs = next(c for c in chunks if '>Job Coverage</h2>' in c)

    assert 'of a hidden run' not in jobs, 'the LINKED run is the visible one'
    assert reverse('challenge_detail', args=[newer.pk]) in jobs


@pytest.mark.django_db
def test_the_chip_says_nothing_extra_when_the_xp_is_on_the_cards_own_run(client):
    """THE OTHER SIDE OF THE "on another run" CLAUSE, and nothing pinned it: a mutation deleting the
    `same_run` guard from the visible span left the suite green, because the only test rendering this state
    asserts the chip is THERE rather than what it says. A card offering the very run that owes must not tell a
    hunter the XP is somewhere else."""
    profile = _hunter(client)
    challenge, _jobs = _jobs_run(profile, count=2)
    assert svc.active_run(profile, CHALLENGE_TYPE_JOBS).pk == challenge.pk, 'the card offers this run'

    body = client.get(reverse('my_challenges')).content.decode()
    chunks = body.split('<section class="card')
    jobs = next(c for c in chunks if '>Job Coverage</h2>' in c)

    assert 'pp-cowed' in jobs, 'the chip is there'
    assert 'on another run' not in jobs, 'and does not send them away from the run being offered'
    assert 'of another run' not in jobs, 'nor say so to a screen reader'
    assert 'of a hidden run' not in jobs


@pytest.mark.django_db
def test_the_chip_does_not_call_the_cards_own_resumable_run_elsewhere(client):
    """THE CASE THE TWO COPIES DISAGREED ON. `resumable_run` deliberately returns a HIDDEN run -- that is how
    the card offers "Resume" instead of "Start" -- so `hidden` and `same_run` can both be true. Branching the
    aria on `hidden` FIRST made it announce "of a hidden run" about the very run the card was offering, while
    the visible text said nothing. Both copies branch on `same_run` first now."""
    profile = _hunter(client)
    challenge, _jobs = _jobs_run(profile, count=2)
    svc.hide(challenge, profile)
    assert svc.resumable_run(profile, CHALLENGE_TYPE_JOBS).pk == challenge.pk, 'the card offers to resume it'

    body = client.get(reverse('my_challenges')).content.decode()
    chunks = body.split('<section class="card')
    jobs = next(c for c in chunks if '>Job Coverage</h2>' in c)

    assert 'pp-cowed' in jobs
    assert 'of a hidden run' not in jobs, 'the card IS showing it, so it is not elsewhere'
    assert 'on another run' not in jobs
