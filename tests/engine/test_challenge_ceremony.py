"""The claim ceremony on a Job Coverage redeem: the payload, and the wiring that plays it.

WHY THIS IS ITS OWN FILE. The payout has tests (`test_challenge_rewards`) and so do the doors
(`test_challenge_reward_surfaces`); this is about a third thing, which is that the ceremony a square's reward
plays is *the same one* a Contract claim plays. That is a structural claim about two code paths, and it is the
kind that rots quietly: nothing about a challenge redeem stops working if the animation drifts, so nothing
would notice.

IT REPLACED A DESIGN THAT COULD NOT WORK, and the reason is the thing most worth keeping written down. The
first version deferred the celebration to the hunter's next Career visit, with a marker on the nav until they
saw it. An audit killed it: every number in the payload is a DIFFERENCE between a reading taken before the
grant and one taken after, and a level is a threshold -- so a payload built on a later page load cannot know
what the hunter's level was when the square paid. Any other payout landing in the gap (a Contract claim in
another tab, a sync) is silently attributed to the square. The celebration therefore happens in the same
response as the payout, which is also the moment it means the most.
"""
from pathlib import Path

import pytest
from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from challenges.models import CHALLENGE_TYPE_AZ, CHALLENGE_TYPE_JOBS
from challenges.services import challenge_service as svc
from challenges.services import rewards
from tests.factories import ConceptFactory, GameFactory, IGDBMatchFactory, ProfileFactory, UserFactory
from trophies.models import Contract, EarnedContract, Job
from trophies.util_modules.constants import CHALLENGE_SLOT_JOB_XP

pytestmark = pytest.mark.django_db

_SEQ = {'n': 0}

ROOT = Path(__file__).resolve().parents[2]

#: Used where a source-text pin has to be scoped to one function's body.
NL = chr(10)


def _hunter(client=None):
    user = UserFactory()
    profile = ProfileFactory(user=user, user_is_premium=True)
    if client is not None:
        client.force_login(user)
    return profile


def _contract(name, *, jobs=()):
    _SEQ['n'] += 1
    c = Contract.objects.create(name=name, slug='%s-%d' % (name.lower().replace(' ', '-'), _SEQ['n']),
                                is_live=True, igdb_id=870_000 + _SEQ['n'])
    if jobs:
        c.jobs.set(jobs)
    concept = ConceptFactory(anchor_migration_completed_at=timezone.now())
    IGDBMatchFactory(concept=concept, igdb_id=c.igdb_id, status='accepted')
    GameFactory(concept=concept)
    return c


def _jobs_run(profile, count=2):
    challenge = svc.start(profile, CHALLENGE_TYPE_JOBS)
    jobs = list(Job.objects.order_by('slug')[:count])
    for job in jobs:
        contract = _contract('Game for %s' % job.slug, jobs=[job])
        svc.assign(challenge, profile, job.slug, contract)
        EarnedContract.objects.create(profile=profile, contract=contract, has_platinum=True,
                                      platinum_reached_at=timezone.now())
    svc.detect_for_profile(profile)
    challenge.refresh_from_db()
    return challenge, jobs


def _preload(profile, job, amount):
    """Put XP in a job the way a Contract claim would, so the next redeem is not the hunter's first.

    THE PRIMITIVE, not a factory row, because the payload is read out of `ProfileJobXP` and a hand-built row
    would let the fixture disagree with the levelling code it is meant to exercise. It needs the caller's
    transaction, which is exactly what `grant_job_xp_bulk` documents.
    """
    from trophies.services.contract_service import grant_job_xp_bulk

    with transaction.atomic():
        grant_job_xp_bulk(profile, [{'job': job, 'amount': amount, 'source': 'event', 'source_id': 0}])


# ── the payload ──────────────────────────────────────────────────────────────────────────────────

def test_one_square_pays_and_hands_back_a_ceremony_for_its_job():
    profile = _hunter()
    challenge, jobs = _jobs_run(profile, count=2)

    _slot, granted, ceremony = rewards.redeem_slot(challenge, profile, jobs[0].slug)

    assert granted == CHALLENGE_SLOT_JOB_XP
    assert [j['slug'] for j in ceremony['jobs']] == [jobs[0].slug], 'only the job that was paid'
    assert ceremony['jobs'][0]['xp'] == CHALLENGE_SLOT_JOB_XP
    assert ceremony['xp'] == CHALLENGE_SLOT_JOB_XP


def test_the_payload_is_the_shape_the_contract_claim_answers_with():
    """THE POINT OF THE WHOLE ARRANGEMENT, pinned at the only place it can be: the keys.

    Both doors build this with `contract_service.ceremony_payload`, and the player is driven entirely by these
    keys -- so a key that exists on one path and not the other is an animation that plays differently
    depending on which button was pressed. `_empty_claim` is the contract path's own declaration of the shape
    (it is what `claim` returns when there is nothing to claim), which makes it the honest thing to compare
    against rather than a list copied into this test.
    """
    from trophies.services.contract_service import _empty_claim

    profile = _hunter()
    challenge, jobs = _jobs_run(profile, count=1)

    _slot, _granted, ceremony = rewards.redeem_slot(challenge, profile, jobs[0].slug)

    assert set(ceremony) == set(_empty_claim())


def test_the_payload_measures_this_write_and_not_the_hunters_total():
    """THE BRACKET, which is the one thing a deferred ceremony could never have got right.

    A job with 10,000 XP already in it is at some level already; the square adds 6,000. The payload must say
    "+6,000, from that level to the new one" -- not "+16,000" and not "from level 1". Both wrong answers are
    what a payload built anywhere but inside the write produces, because the before-reading no longer exists.

    A FRESH JOB CANNOT CATCH THE SECOND HALF of that: with nothing pre-loaded the delta and the total are the
    same number, so `xp` would pass either way. Hence the pre-load.
    """
    from trophies.services.contract_service import level_for_xp

    profile = _hunter()
    challenge, jobs = _jobs_run(profile, count=1)
    _preload(profile, jobs[0], 10_000)
    before = level_for_xp(10_000)

    _slot, _granted, ceremony = rewards.redeem_slot(challenge, profile, jobs[0].slug)

    row = ceremony['jobs'][0]
    assert row['xp'] == CHALLENGE_SLOT_JOB_XP, 'the DELTA, not the job total'
    assert row['from_level'] == before, 'the level before this write'
    assert row['to_level'] == level_for_xp(10_000 + CHALLENGE_SLOT_JOB_XP)
    assert row['to_level'] > row['from_level'], 'and a square is worth two job levels, so it moves'


def test_claim_all_brackets_the_pursuer_levels_it_crossed():
    """THE HALF THAT WAS LOST ONCE ALREADY, now visible in the payload rather than only in a log table.

    `grant_job_xp_bulk` logs job-tier milestones and nothing else, so a bare call loses every Pursuer rank the
    payout crossed -- permanently, because `ranks_crossed` is `old < min <= new` and the next claim starts from
    the already-raised level. A full run is 25 squares and 50 job levels, which cannot cross nothing; the
    payload saying so is the cheapest available proof that the reading was taken before the grant.
    """
    profile = _hunter()
    challenge, _jobs = _jobs_run(profile, count=Job.objects.count())

    _slots, granted, ceremony = rewards.redeem_all(challenge, profile)

    assert granted == Job.objects.count() * CHALLENGE_SLOT_JOB_XP
    pursuer = ceremony['pursuer']
    assert pursuer['to_level'] > pursuer['from_level']
    assert pursuer['ranks'], 'a 50-level jump crosses ranks, and they have to be in the payload'
    assert pursuer['rank_up'] or pursuer['div_up'], 'so the finale has something to play'


def test_nothing_owed_hands_back_no_ceremony():
    """A hunter pressing Claim all twice has not earned a second celebration, and the second press is not an
    error either -- so the payout says zero and the ceremony says None."""
    profile = _hunter()
    challenge, _jobs = _jobs_run(profile, count=2)
    rewards.redeem_all(challenge, profile)

    slots, granted, ceremony = rewards.redeem_all(challenge, profile)

    assert (slots, granted, ceremony) == ([], 0, None)


# ── the line above the number ────────────────────────────────────────────────────────────────────

def test_the_eyebrow_counts_squares_because_a_challenge_has_no_contracts():
    """THE ONE THING THIS PATH VARIES, and it is a correctness fix rather than a flourish: the player's
    default line counts `accepted`, a list of CONTRACT slugs. A challenge payout has none, so without a line
    of its own the overlay announced "Contract claimed" over a square's reward."""
    profile = _hunter()
    challenge, jobs = _jobs_run(profile, count=3)

    _slot, _granted, one = rewards.redeem_slot(challenge, profile, jobs[0].slug)
    _slots, _all_granted, many = rewards.redeem_all(challenge, profile)

    assert one['eyebrow'] == 'Square claimed'
    assert many['eyebrow'] == '2 squares claimed'
    assert one['accepted'] == [] and many['accepted'] == [], 'no contract was claimed here'


def test_a_contract_claims_eyebrow_is_left_alone():
    """The default has to survive, because the Career page has no line of its own to send. An empty string is
    what `claim` passes, and the player treats it as absent."""
    from trophies.services.contract_service import _empty_claim

    assert _empty_claim()['eyebrow'] == ''


def test_a_first_ever_claim_still_says_so():
    """`first_claim` outranks the eyebrow inside the player ("Your Pursuit begins"), and should: a hunter's
    first job XP ever is the bigger fact whichever door paid it. What matters here is that the flag is DERIVED
    and travels -- a challenge being somebody's first XP source is the normal case for a new hunter."""
    profile = _hunter()
    challenge, jobs = _jobs_run(profile, count=2)

    _slot, _granted, first = rewards.redeem_slot(challenge, profile, jobs[0].slug)
    _slot2, _granted2, second = rewards.redeem_slot(challenge, profile, jobs[1].slug)

    assert first['first_claim'] is True
    assert second['first_claim'] is False, 'only the first one is the first one'


# ── the reply and the page ───────────────────────────────────────────────────────────────────────

def test_both_doors_answer_with_the_ceremony(client):
    """Under `claim`, the key the Career page's reply already uses, because the same module plays it."""
    import json

    profile = _hunter(client)
    challenge, jobs = _jobs_run(profile, count=3)

    one = json.loads(client.post(reverse('challenge_redeem', args=[challenge.id, jobs[0].slug])).content)
    every = json.loads(client.post(reverse('challenge_redeem_all', args=[challenge.id])).content)

    assert one['claim']['jobs'][0]['slug'] == jobs[0].slug
    assert len(every['claim']['jobs']) == 2
    # And the toast fallback's figure is still there, for the case where the player cannot run.
    assert one['granted'] == CHALLENGE_SLOT_JOB_XP
    # THE NAV MARKER'S ANSWER, which is what lets the client take the pill down without a reload. Still true
    # after the first claim (two squares are owed), false once the run is paid out.
    assert one['xp_pending'] is True
    assert every['xp_pending'] is False


def test_the_pill_survives_a_claim_when_another_run_still_owes(client):
    """WHY `xp_pending` IS PROFILE-WIDE AND NOT THIS RUN'S.

    A hunter can finish a whole run, leave every square unclaimed, and start the next one -- sequential runs
    stop a second ACTIVE run, not a finished one with XP still owed. Paying out the new run then leaves the old
    one owing twenty-five squares, so the pill must stay lit.

    A RUN-SCOPED ANSWER PASSES EVERY OTHER TEST IN THIS FILE, because they all have one run: `pending_xp(run)`
    and `has_unclaimed_xp(profile)` agree until a second run exists. A mutation swapping one for the other was
    caught by nothing, which is what this test is for.

    THE FIRST RUN HAS TO BE COMPLETE, not merely hidden, and that is worth recording: `start` is
    resume-before-create, so hiding an unfinished run and pressing Start again returns THAT run. The first
    version of this fixture hid a two-square run and then "started" a second one, which was the same run and
    refused the assignment. Finishing is the only way to get a second.
    """
    import json

    profile = _hunter(client)
    old, _old_jobs = _jobs_run(profile, count=Job.objects.count())   # finished, deliberately unclaimed
    assert old.is_complete is True, 'the fixture must actually finish the first run'
    fresh, _jobs = _jobs_run(profile, count=1)
    assert fresh.pk != old.pk, 'the fixture needs two runs'

    body = json.loads(client.post(reverse('challenge_redeem_all', args=[fresh.id])).content)

    assert body['granted'] == CHALLENGE_SLOT_JOB_XP, 'the new run paid'
    assert body['xp_pending'] is True, "and the old run's squares are still owed, so the pill stays"


def test_a_second_claim_carries_no_ceremony(client):
    """`granted: 0` and `claim: null` -- the client's own guard reads the second, and a payload describing a
    payout that did not happen is the one thing that must not arrive."""
    import json

    profile = _hunter(client)
    challenge, _jobs = _jobs_run(profile, count=2)
    client.post(reverse('challenge_redeem_all', args=[challenge.id]))

    body = json.loads(client.post(reverse('challenge_redeem_all', args=[challenge.id])).content)

    assert body['granted'] == 0
    assert body['claim'] is None


def test_the_player_loads_for_the_owner_of_a_jobs_run(client):
    profile = _hunter(client)
    challenge, _jobs = _jobs_run(profile, count=2)

    body = client.get(reverse('challenge_detail', args=[challenge.id])).content.decode()

    assert 'claim-ceremony.js' in body
    # THE SPRITE, by the id the player actually references (`<use href="#jobicon-...">`). Without it every
    # job tile in the overlay draws an empty box, and nothing errors -- which is why this is pinned by the
    # shared string rather than by the tag's name. An earlier version of this line accepted either
    # `job-icon-sprite` or `jobicon`, and the first of those appears nowhere in the codebase: the assertion
    # could only ever pass on its second half, so half of it was decoration.
    assert '<symbol id="jobicon-' in body, 'the ceremony draws icons from the page sprite'


def test_the_player_does_not_load_where_it_could_never_play(client):
    """TWO GATES, because there are two ways to reach this page with no button to press. An A-Z run has no
    payout at all, and a visitor to somebody else's run gets no Claim controls -- neither should download a
    723-line animation they cannot open.

    EACH GATE IS TESTED ON ITS OWN, which the first version of this did not do: it built both runs for `owner`
    and then logged the client in as a THIRD profile, so the A-Z request was ALSO a non-owner request and both
    assertions passed on `is_owner` alone. An audit proved it by mutation -- deleting `rewards.per_square` from
    the template's condition left this green. So the A-Z run is now requested BY ITS OWNER, where `is_owner` is
    true and only the payout gate can stop the script loading.
    """
    az_owner = _hunter(client)
    az = svc.start(az_owner, CHALLENGE_TYPE_AZ)
    somebody_else = _hunter()
    jobs_run, _jobs = _jobs_run(somebody_else, count=1)

    own_az = client.get(reverse('challenge_detail', args=[az.id])).content.decode()
    somebody_elses = client.get(reverse('challenge_detail', args=[jobs_run.id])).content.decode()

    assert 'claim-ceremony.js' not in own_az, "its OWNER, and an A-Z run still pays no XP"
    assert 'claim-ceremony.js' not in somebody_elses, 'a visitor has no Claim button'


# ── the wiring, pinned by source text (this project has no JS runner) ────────────────────────────

def _code(path):
    """A JS file with its comment lines stripped.

    THE TRAP THIS EXISTS FOR is recorded in two other files already: an absence assertion matches the COMMENT
    that explains the absence, so the prose about a rule breaks the test for the rule.
    """
    js = (ROOT / 'static' / 'js' / path).read_text(encoding='utf-8')
    return '\n'.join(ln for ln in js.splitlines()
                     if not ln.lstrip().startswith(('//', '*', '/*', '*/')))


# ── the motion the audit found missing (source-text pins) ─────────────────────────────────────────

def test_the_reward_total_rolls_at_load_too():
    """The page animated one `.pp-tally` headline and left a second one 80px below it static, which is the half
    of "roll headline integers on reveal" that was missing. Gated on there being something to roll: counting up
    to zero on a fresh run is a frame of nothing."""
    code = _code('challenge-detail.js')

    assert "querySelector('[data-cpay-wrap] [data-countup]')" in code
    assert "parseInt(paid.dataset.countup || '0', 10) > 0" in code, 'not on a fresh run'


def test_the_entrance_does_not_replay_on_every_claim():
    """`pp-head-cascade` animates the wrapper's direct children and a claim replaces them, so the class has to
    come off before the first swap -- otherwise the card re-enters on every claim, which reads as arriving
    again rather than updating."""
    code = _code('challenge-detail.js')

    assert "classList.remove('pp-head-cascade')" in code


def test_a_claim_is_acknowledged_where_it_can_be_seen():
    """THE BEAT THE CITED PATTERN HAS AND THIS PANEL DID NOT. The contract card flashes the row it just paid;
    this panel replaces its whole ledger with server markup, so a hunter returning from the ceremony found rows
    that were simply already green.

    ON THE DRAWER, NOT THE ROWS, and that is the whole point of this test. A row version shipped first and could
    never be seen: every row a claim pays has just BECOME paid, so the re-render puts it inside a `<details>`
    the server always emits closed, and content in a closed `<details>` is `display: none`. The class stayed
    on the row and fired whenever the receipts were expanded later -- dead motion plus a misfire.

    A CALL SITE, NOT THE DEFINITION. `assert 'flashPaid' in code` was the first version and an audit found it
    satisfied by the function declaration alone: both call sites could be deleted and it stayed green.
    """
    code = _code('challenge-detail.js')

    # BOTH CALL SITES, counted. `'flashClaimed(ctx);' in code` was the first attempt and a mutation run
    # showed it satisfied by EITHER call site alone -- so deleting the no-ceremony one (the path an A-Z
    # page and a missing-module bundle both take) left it green. Three occurrences: the declaration and
    # the two calls.
    assert code.count('flashClaimed(ctx)') >= 3, 'called on both paths, not merely defined'
    assert "'pp-cpay__done--just-paid'" in code
    assert "querySelector('.pp-cpay__done')" in code, 'the drawer, which is visible'
    assert 'pp-cpay__row--just-paid' not in code, 'and not the rows, which are not'


def test_the_nav_pill_fades_rather_than_blinking_out():
    """ITS EXIT, on a marker whose removal is otherwise instant -- and which, below 1280px and on the tab bar,
    is what lets a NEW lozenge appear in the same spot in the same frame (NEW is suppressed by the XP pill's
    own presence). Fading first makes that a hand-off.

    THE LABEL IS CORRECTED IMMEDIATELY, though: nobody should hear "XP waiting" about XP that is paid, not even
    for the length of a fade. And the removal is belt-and-braces, because `transitionend` does not fire under
    reduced motion or inside a `display: none` branch."""
    code = _code('challenge-detail.js')

    assert "classList.add('pp-navhub__xp--leaving')" in code
    assert "addEventListener('transitionend', done)" in code
    assert 'window.setTimeout(done, 400)' in code, 'transitionend is not guaranteed to fire'
    # THE CLAUSE IS STRIPPED BEFORE THE CLASS IS ADDED, so the accessible name is never stale -- read inside
    # `dropNavMarker` only. Taken over the whole file these two indices are the FIRST occurrence of each
    # anywhere, which happens to be right today and would pass unconditionally the moment an earlier
    # `setAttribute('aria-label'` appeared above it.
    body = code.split('function dropNavMarker()', 1)[1].split(NL + '        }', 1)[0]
    assert body.index("setAttribute('aria-label'") < body.index("classList.add('pp-navhub__xp--leaving')")


def test_only_one_claim_can_be_in_flight():
    """TWO PRESSES ON TWO DIFFERENT BUTTONS. `post` disables only the button it was handed, so pressing row 3's
    Claim and then row 5's before the first reply lands sent two requests -- and the second reply's panel swap
    DETACHES the first call's count-up target, so a tick deferred past a 12-28 second ceremony animates a node
    that is no longer in the document. It also stacked a second overlay on the first.

    THE FLAG IS MODULE-SCOPED, not per-button, because the whole point is that the two presses are on different
    buttons. `ccx-open` covers the window where the reply has landed and the overlay is up: a keyboard user can
    still reach a button behind it if focus was left there.
    """
    code = _code('challenge-detail.js')

    assert 'var claiming = false;' in code
    assert "if (claiming || document.body.classList.contains('ccx-open')) { return; }" in code
    assert code.count('claiming = false;') >= 3, 'cleared on every exit, or one failure locks the panel'


def test_the_focus_landing_announces_where_it_is():
    """Focusing the figure announced only digits -- the caption is a sibling and the section's
    `aria-labelledby` is not re-read on a programmatic focus. `#cpay-head` says "Reward", and it is always
    rendered, which also makes it the link in the chain that actually fires."""
    code = _code('challenge-detail.js')

    assert "querySelector('#cpay-head')" in code
    assert "querySelector('.pp-cpay__xp')" not in code, 'the bare figure is not a focus target'


def test_an_in_flight_claim_says_so_to_a_screen_reader():
    """The only in-flight feedback was a dim, which says "dead" rather than "working". `.pp-cta` now fades
    into it instead of snapping, and this is the half a screen reader gets."""
    code = _code('challenge-detail.js')

    assert "setAttribute('aria-busy', 'true')" in code
    assert code.count("removeAttribute('aria-busy')") >= 2, 'cleared on both exits, or a button stays busy'


def test_the_player_reads_a_line_from_the_payload():
    """Otherwise the challenge's own eyebrow is built, sent, and ignored -- and the overlay says "Contract
    claimed" over a square's reward. The default must survive underneath it, for the Career page."""
    code = _code('claim-ceremony.js')

    assert 'if (p.eyebrow) return p.eyebrow;' in code
    assert "' Contracts claimed'" in code, 'the contract default is still there for its own caller'


def test_the_player_cleans_up_after_itself_when_it_cannot_build():
    """THE HALF THE CALLER CANNOT CLEAN UP, which is why the guard has to be inside `play`.

    Once `play` has attached a document-level `keydown` handler and taken focus, a throw leaves two things a
    caller has no handle on: the listener, and the `lastFocus` it captured. A re-audit found the reachable
    path -- the reduced-motion branch runs `settlePage` and `finishAll` synchronously after both, so a
    malformed ladder propagates out with the overlay half-built. The cost is not a missing animation: every
    later Escape keypress anywhere on the page runs the dead ceremony's teardown, swallowing the key from
    other handlers (a run page also has a `<dialog>`) and yanking focus somewhere the hunter has left.

    THE ASYNC HALF IS THE SAME BUG THROUGH A DIFFERENT DOOR. `play` returns `dismissed`, not its promise
    chain, so a throw in the chain is an unhandled rejection: the overlay freezes with its controls still
    inert, `dismissed` never resolves, and anything awaiting it waits forever -- including this feature's
    deferred reward count-up.

    `teardown()` is the answer to both. It removes the listener and the body classes, detaches the root and
    restores focus, and its own `torn` guard makes a later dismissal a no-op.
    """
    code = _code('claim-ceremony.js')

    # AS CONTIGUOUS BLOCKS. Two separate `in code` checks would pass with either guard deleted, since
    # `teardown()` is called from half a dozen places in this file.
    assert ('} catch (e) {' + chr(10) + '                teardown();') in code, (
        'the synchronous branch must tear down before it re-throws'
    )
    assert 'throw e;' in code, 'and must re-throw: a ceremony that cannot build is this file\'s bug'
    # CONTIGUOUS, and this one had to be fixed after an audit MUTATION-VERIFIED that it was not. It was
    # three independent `in code` checks, and `teardown();` appears four times in that file -- so deleting it
    # from inside this very `.catch` left all three true and the test green. The comment above claimed
    # otherwise, which is the failure mode twice over: a pin that does not pin, and a note asserting it does.
    assert ("'ClaimCeremony failed mid-play', err); }" + chr(10)
            + '                teardown();') in code, (
        'the promise chain must tear down, not merely log'
    )


def test_the_redeem_path_cannot_report_a_paid_claim_as_a_failure():
    """THE WHOLE RISK OF PLAYING AN ANIMATION INSIDE A SUCCESS HANDLER. Anything that throws between the
    reply arriving and the handler returning lands in `post`'s `catch`, which says "Could not claim that XP"
    for XP the ledger has already taken. This file has been there once, through a `matches(':focus-visible')`
    call in the same function.

    `play()` is not a bare promise call: it builds the overlay, appends it to `<body>`, sets `body.ccx-open`
    and recedes the page, all synchronously, before returning. So the module guard is not enough -- a throw
    after the append leaves `.ccx` in the DOM at `z-index: 80` with a transparent scrim, and the page looks
    normal while being completely unclickable until a reload. The `catch` has to un-wedge that, not just
    report it.
    """
    code = _code('challenge-detail.js')

    assert 'PP.ClaimCeremony' in code, 'the redeem path must play the ceremony'
    assert '!Ceremony || !Ceremony.play' in code, 'and must not assume the module is loaded'
    assert '!claim.jobs || !claim.jobs.length' in code, 'nor open an overlay with nothing to animate'
    # AS ONE CONTIGUOUS BLOCK, not as two strings that happen to both appear. Written as
    # `'try {' in code and 'Ceremony.play(...)' in code` this passed with the guard removed, because
    # `challenge-detail.js` has other `try` blocks -- so the pin certified a guard that was no longer there.
    # A mutation run is what found that; the assertion could not.
    assert ('try {' + chr(10) + '                return Ceremony.play(claim) || Promise.resolve();') in code, (
        'the play call itself must be inside the try, not merely near one'
    )
    # THE UN-WEDGE, by the three things `play` sets before it can throw. A `catch` that only logged would
    # leave the hunter on a page they cannot click.
    assert "classList.remove('ccx-open')" in code
    assert "classList.remove('pp-receded')" in code
    assert "querySelectorAll('.ccx')" in code
    # THE TOAST IS A FALLBACK, not a duplicate: a full-screen ceremony and a toast announcing the same number
    # is two answers to one question.
    assert 'var ceremony = played(data.claim);' in code and 'if (ceremony) {' in code


def test_the_reward_total_ticks_when_it_is_actually_on_screen():
    """THE ANIMATION THAT NOBODY COULD HAVE SEEN. The panel swap and the ceremony happen in one synchronous
    task, so a count-up started with the swap ran behind a scrim fading to near-opaque over 320ms and a
    ceremony that holds the screen for ten seconds or more. The number the whole panel is built around -- a
    claimed total that only ever grows, which is why it was made the animated one -- finished out of sight on
    every real claim, and the total had silently jumped by the time the overlay closed.

    So `apply` hands the plan back and `post` chooses the moment: on dismissal when a ceremony played,
    immediately when none did.
    """
    code = _code('challenge-detail.js')

    assert 'return tickPlan;' in code, 'the swap hands the count-up back rather than starting it'
    assert 'ceremony.then(function () { countFrom(plan); flashClaimed(ctx); });' in code, (
        'and it runs when the overlay closes, together with the rows it paid'
    )
    # NOT ONLY THERE: with no ceremony the number must still tick, which is the case an A-Z page and a
    # missing-module bundle both take.
    assert code.count('countFrom(plan)') >= 3


def test_the_claim_takes_the_nav_pill_down_with_it():
    """THE ONE PIECE OF MARKUP A CLAIM CHANGES THAT THE REPLY DOES NOT CARRY.

    The chrome is server-rendered and this page does not reload -- deliberately, because the reply already
    carries the panel and the squares. The nav was rendered before the claim, so clearing the cached marker
    server-side does nothing to the pill already on screen: it sits on My Pursuit advertising XP that has been
    paid, for the rest of the page's life. `rewards._grant` states that this must not happen "in the one
    moment the hunter is looking straight at it", so the fix belongs here rather than in the comment.

    THE ACCESSIBLE NAME IS THE HALF THAT GETS FORGOTTEN. The pills are `aria-hidden`; the state is one clause
    in the nav item's `aria-label`. Removing the lozenge alone leaves a screen reader announcing XP waiting
    after it was claimed -- the same contradiction, for the reader who cannot see that it has gone.
    """
    code = _code('challenge-detail.js')

    assert 'if (data.xp_pending === false) { dropNavMarker(); }' in code, (
        'only an explicit false removes it -- the answer is profile-wide and the server owns it'
    )
    assert "querySelectorAll('.pp-navhub__xp')" in code, 'both chromes, by the shared class'
    assert "getAttribute('aria-label')" in code, 'and the clause comes out of the accessible name'


def test_the_aria_clause_is_the_same_string_in_all_three_files():
    """A LITERAL SHARED BETWEEN A SCRIPT AND TWO TEMPLATES IS A COUPLING, and this is the pin for it. The
    client strips the clause by exact match, so a reworded template silently leaves it in place: the pill
    vanishes and a screen reader still says XP is waiting. Rebuilding the label from the surviving pills
    instead would mean a second renderer for copy the server owns.
    """
    clause = ', Job XP waiting to be claimed'
    # STRIPPED, not raw: this paragraph quotes the clause, so reading the raw file would let the comment satisfy
    # the assertion even if the `CLAUSE` literal were reworded.
    js = _code('challenge-detail.js')
    navbar = (ROOT / 'templates' / 'partials' / 'navbar.html').read_text(encoding='utf-8')
    tabbar = (ROOT / 'templates' / 'partials' / 'mobile_tabbar.html').read_text(encoding='utf-8')
    # AND THE FOURTH COPY, in Python: the sub-nav's XP mark announces the same thing, and it is the copy most
    # likely to be missed because it is not in a template. It carries the clause WITHOUT the leading comma,
    # since the strip builds a whole label rather than appending to one.
    ctx = (ROOT / 'plat_pursuit' / 'context_processors.py').read_text(encoding='utf-8')

    assert clause in js
    assert clause in navbar, 'the desktop nav must say it the same way the script strips it'
    assert clause in tabbar, 'and so must the mobile tab bar'
    assert clause.lstrip(', ') in ctx, "and the sub-nav mark, which is where a reword would be forgotten"
