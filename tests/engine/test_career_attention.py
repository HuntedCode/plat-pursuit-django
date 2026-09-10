"""The two attention markers on the My Pursuit nav item: a claim COUNT and a NEW pill.

They render in the site-wide navbar and mobile tab bar, so the cost rules matter as much as the
behaviour: this runs on every page of the site for every signed-in hunter.
"""
import pytest
from django.core.cache import cache
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from tests.factories import ProfileFactory
from trophies.models import Contract, EarnedContract, Job
from trophies.services import career_attention, contracts_service

pytestmark = pytest.mark.django_db

CF = {'HTTP_CF_RAY': '8f0000000000abcd-LHR'}

_NEXT_IGDB = 7_000_000


@pytest.fixture(autouse=True)
def _clear_cache():
    """Both answers are cached, one of them SITE-WIDE, so a stale entry would leak between tests."""
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def hunter(client):
    profile = ProfileFactory(is_linked=True, sync_status='synced', total_trophies=10)
    profile.user.ui_flags = {'career_explainer': True}
    profile.user.save(update_fields=['ui_flags'])
    client.force_login(profile.user)
    client.profile = profile
    return client


def _contract(name, *, live=True, jobs=None, announced_days_ago=None):
    global _NEXT_IGDB
    _NEXT_IGDB += 1
    c = Contract.objects.create(name=name, slug=name.lower().replace(' ', '-'),
                                igdb_id=_NEXT_IGDB, is_live=live)
    c.jobs.set(jobs if jobs is not None else list(Job.objects.exclude(is_fallback=True)[:1]))
    if announced_days_ago is not None:
        when = timezone.now() - timezone.timedelta(days=announced_days_ago)
        Contract.objects.filter(pk=c.pk).update(
            went_live_at=when - timezone.timedelta(hours=6),
            announced_at=when, announcement_posted=True)
    return c


def _count_marker(body):
    """The claim count's text, or None if it did not render.

    A HELPER RATHER THAN A SUBSTRING, because `pp-navhub__n` is a PREFIX of `pp-navhub__new`. The
    moment the new-contracts marker became a word, every `'pp-navhub__n' not in body` in this file
    quietly started meaning "and no New pill either". Two of them failed immediately, which is the
    lucky version; the unlucky version is an assertion that stops testing anything and says nothing.
    """
    marker = 'class="pp-navhub__n"'
    if marker not in body:
        return None
    return body.split(marker, 1)[1].split('</span>', 1)[0].split('>', 1)[1]


def _nav_link(body):
    """The My Pursuit anchor, whole. Splitting on the words is not enough: they appear in the
    aria-label first, several hundred characters before the markup they describe."""
    return body.split('class="pp-navhub', 1)[1].split('</a>', 1)[0]


def _claimable(profile, contract):
    """A reward reached and not yet accepted -- the board's own definition of claimable."""
    return EarnedContract.objects.create(
        profile=profile, contract=contract, full_reached_at=timezone.now())


# -- the claim count --------------------------------------------------------------------------

def test_the_count_matches_the_board(hunter):
    """The badge and the board must agree or the badge is lying. This count is deliberately NOT
    computed the board's way -- the board annotates the whole catalogue with four correlated
    subqueries, which is the wrong shape for something on every page -- so the two definitions are
    pinned together here."""
    for i in range(3):
        _claimable(hunter.profile, _contract('Claim Me %d' % i))
    _contract('Untouched')

    assert career_attention.claimable_count(hunter.profile) == 3
    assert contracts_service.claimable_summary(hunter.profile)['count'] == 3


def test_an_accepted_reward_stops_counting(hunter):
    ec = _claimable(hunter.profile, _contract('Already Taken'))
    ec.full_accepted_at = timezone.now()
    ec.save(update_fields=['full_accepted_at'])
    cache.clear()

    assert career_attention.claimable_count(hunter.profile) == 0
    assert contracts_service.claimable_summary(hunter.profile)['count'] == 0


def test_an_unpublished_or_jobless_contract_never_counts(hunter):
    """Both are the board's gates (`is_live`, `has_jobs`). A claim badge for something the board
    will not show leads somewhere empty."""
    _claimable(hunter.profile, _contract('Withdrawn', live=False))
    _claimable(hunter.profile, _contract('No Jobs Yet', jobs=[]))

    assert career_attention.claimable_count(hunter.profile) == 0
    assert contracts_service.claimable_summary(hunter.profile)['count'] == 0


def test_the_count_is_cached_and_the_claim_path_clears_it(hunter):
    """Cached because it renders on every page; cleared on a claim because a badge still standing
    after the claim would look broken in the one moment the hunter is looking straight at it."""
    _claimable(hunter.profile, _contract('Claim Me'))
    assert career_attention.claimable_count(hunter.profile) == 1

    EarnedContract.objects.update(full_accepted_at=timezone.now())
    assert career_attention.claimable_count(hunter.profile) == 1, 'not cached at all'

    career_attention.forget_claimable(hunter.profile)
    assert career_attention.claimable_count(hunter.profile) == 0


def test_the_count_reads_the_hunters_own_rows_not_the_catalogue(hunter):
    """The shape that makes it affordable. A catalogue-sized scan per page render is the thing this
    exists to avoid, so the cost must not grow with the number of contracts on the board."""
    _claimable(hunter.profile, _contract('Mine'))

    for i in range(25):
        _contract('Someone Elses %02d' % i)
    cache.clear()

    with CaptureQueriesContext(connection) as ctx:
        career_attention.claimable_count(hunter.profile)
    assert len(ctx.captured_queries) == 1


# -- the new-contracts marker --------------------------------------------------------------------

def test_the_marker_lights_for_an_announcement_newer_than_the_marker(hunter):
    _contract('Fresh', announced_days_ago=0)

    assert career_attention.has_new_contracts(hunter.profile.user) is True


def test_the_marker_agrees_with_the_modal(hunter):
    """A marker that leads to no modal teaches the reader to ignore markers. Both read the
    same marker and the same 14-day first-visit floor."""
    from trophies.util_modules.constants import NEW_CONTRACT_WINDOW_DAYS
    from trophies.services import new_contracts_modal

    _contract('Ancient', announced_days_ago=NEW_CONTRACT_WINDOW_DAYS + 10)

    user = hunter.profile.user
    assert career_attention.has_new_contracts(user) is False
    assert new_contracts_modal.new_for(hunter.profile, user)['rows'] == []

    _contract('Fresh', announced_days_ago=1)
    cache.clear()
    assert career_attention.has_new_contracts(user) is True
    assert new_contracts_modal.new_for(hunter.profile, user)['rows'] != []


def test_dismissing_the_modal_puts_the_marker_out(hunter):
    contract = _contract('Read It', announced_days_ago=0)
    user = hunter.profile.user
    assert career_attention.has_new_contracts(user) is True

    contract.refresh_from_db()
    hunter.post(reverse('api:user-quick-settings'),
                data={'setting': 'contracts_seen', 'value': contract.announced_at.isoformat()},
                content_type='application/json')

    user.refresh_from_db()
    assert career_attention.has_new_contracts(user) is False


def test_a_contract_that_was_never_posted_lights_nothing(hunter):
    """Same gate as the modal: `--baseline` records a backlog as known WITHOUT posting it, and the
    ~1,000 launch contracts are settled that way. A marker for them would be the whole catalogue
    announcing itself on the first render after deploy."""
    c = _contract('Baselined')
    Contract.objects.filter(pk=c.pk).update(announced_at=timezone.now(), announcement_posted=False)

    assert career_attention.has_new_contracts(hunter.profile.user) is False


def test_the_marker_costs_no_per_user_query(hunter):
    """THE TRICK THAT MAKES IT FREE. "Is anything new to this hunter" is a comparison between a
    per-user marker -- already on the user object, loaded by authentication -- and a global maximum.
    So the only fetch is one value shared by every visitor on the site."""
    _contract('Fresh', announced_days_ago=0)
    career_attention.latest_announced_at()          # warm the shared value

    with CaptureQueriesContext(connection) as ctx:
        career_attention.has_new_contracts(hunter.profile.user)
    assert len(ctx.captured_queries) == 0


def test_the_empty_board_is_cached_too(hunter):
    """`cache.get` cannot tell a stored None from a miss, so an empty board would re-query on every
    render of every page -- the one case where the site-wide trick would cost the most."""
    career_attention.latest_announced_at()

    with CaptureQueriesContext(connection) as ctx:
        assert career_attention.latest_announced_at() is None
    assert len(ctx.captured_queries) == 0


def test_announcing_relights_the_marker_immediately(hunter):
    """The shared value is cached for fifteen minutes. Without an explicit clear, the one event the
    whole feature is built around would take up to that long to show."""
    from core.services.contract_announcer import mark_announced

    _mark_seen(hunter)
    pending = _contract('Waiting')
    career_attention.latest_announced_at()          # cache the pre-announcement state

    mark_announced([pending])

    assert career_attention.has_new_contracts(hunter.profile.user) is True


def _mark_seen(hunter):
    user = hunter.profile.user
    user.ui_flags = dict(user.ui_flags, contracts_seen=timezone.now().isoformat())
    user.save(update_fields=['ui_flags'])


def test_claiming_clears_the_badge_for_real(hunter):
    """Through the real claim path, not the helper. The badge is cached, so nothing about the claim
    itself would drop it -- the hunter would take their reward and watch the count stay put."""
    from trophies.services import contract_service

    contract = _contract('Claim Me')
    _claimable(hunter.profile, contract)
    assert career_attention.claimable_count(hunter.profile) == 1

    contract_service.claim(hunter.profile, contract=contract)

    assert career_attention.claimable_count(hunter.profile) == 0


# -- what actually renders --------------------------------------------------------------------

def test_the_nav_carries_both_markers(hunter):
    for i in range(2):
        _claimable(hunter.profile, _contract('Claim Me %d' % i))
    _contract('Fresh', announced_days_ago=0)

    body = hunter.get('/career/', **CF).content.decode()
    nav = _nav_link(body)

    assert _count_marker(body) == '2', 'the claim count is missing or wrong'
    # A WORD, not a light -- the correction the avatar's What's New marker already made. Asserted on
    # the CONTENT, because a class name alone cannot tell a word from a dot.
    assert '>New</span>' in nav, 'the new-contracts marker is a bare dot again'
    # The markers are aria-hidden, so the LABEL has to carry the same thing in words.
    assert 'ready to claim' in nav and 'new contracts on the board' in nav


def test_a_staff_preview_lights_both_markers(hunter):
    """The markers only show when there is something to say, which makes them the hardest thing on
    the site to look at deliberately -- you need an unclaimed reward and an unseen announcement at
    the same moment. The preview door is how you look at them anyway."""
    user = hunter.profile.user
    user.is_staff = True
    user.save(update_fields=['is_staff'])

    body = hunter.get('/career/?preview=career-markers', **CF).content.decode()

    assert _count_marker(body) is not None and 'pp-navhub__new' in body


def test_the_preview_can_force_a_count(hunter):
    """`&n=` is how you look at the 9+ cap without earning twelve rewards, and `&n=0` is how you look
    at the pill on its own."""
    user = hunter.profile.user
    user.is_staff = True
    user.save(update_fields=['is_staff'])

    body = hunter.get('/career/?preview=career-markers&n=12', **CF).content.decode()
    assert _count_marker(body) == '9+'

    body = hunter.get('/career/?preview=career-markers&n=0', **CF).content.decode()
    assert _count_marker(body) is None and 'pp-navhub__new' in body


def test_the_preview_writes_nothing(hunter):
    """Every preview door on the site shares this: looking never spends anything, so it never has to
    be undone. This one bypasses the claim state AND the seen marker, so it is the one that would
    hurt most if it left a trace."""
    user = hunter.profile.user
    user.is_staff = True
    user.save(update_fields=['is_staff'])
    before = dict(user.ui_flags)

    hunter.get('/career/?preview=career-markers&n=5', **CF)

    user.refresh_from_db()
    assert user.ui_flags == before
    assert career_attention.claimable_count(hunter.profile) == 0


def test_the_preview_is_team_only(hunter):
    """It shows markers for state the viewer does not have, so the querystring cannot be something
    anybody can type."""
    body = hunter.get('/career/?preview=career-markers', **CF).content.decode()

    assert _count_marker(body) is None and 'pp-navhub__new' not in body


def test_a_staff_account_sees_nothing_without_the_querystring(hunter):
    """The half that is easy to miss: every other preview test asks whether the door OPENS. Drop the
    slug check and it is not a door at all -- every page a staff member loads would wear markers for
    state they do not have, which is worse than no preview."""
    user = hunter.profile.user
    user.is_staff = True
    user.save(update_fields=['is_staff'])

    body = hunter.get('/career/', **CF).content.decode()
    assert _count_marker(body) is None and 'pp-navhub__new' not in body

    # ...and somebody else's door does not open this one either.
    body = hunter.get('/career/?preview=whats-new', **CF).content.decode()
    assert _count_marker(body) is None and 'pp-navhub__new' not in body


def test_every_preview_door_is_the_same_door():
    """Three surfaces need this now, and copies drift -- the What's New door had to be pulled out of
    its modal precisely because the avatar dot's half had been left behind, and half a preview is not
    a preview. Nobody should be hand-rolling the fourth."""
    from pathlib import Path

    from django.conf import settings

    for rel in ('core/whats_new.py', 'trophies/views/career_views.py',
                'trophies/services/career_attention.py'):
        src = (Path(settings.BASE_DIR) / rel).read_text(encoding='utf-8')
        assert 'core.previews import previewing' in src, '%s hand-rolls the preview gate' % rel
        assert "GET.get('preview')" not in src, '%s still reads the querystring itself' % rel


def test_the_mobile_tab_bar_carries_them_too(hunter):
    """The bar is the only nav a phone sees, so markers that live only in the desktop navbar reach
    nobody on mobile -- and mobile is where a nav marker matters most."""
    _claimable(hunter.profile, _contract('Claim Me'))
    _contract('Fresh', announced_days_ago=0)

    body = hunter.get('/career/', **CF).content.decode()
    tab = body.split('class="mobile-tabbar-item', 1)[1].split('</a>', 1)[0]

    assert 'pp-navhub__n--tab' in tab, 'the tab bar has no claim count'
    assert 'pp-navhub__new--tab' in tab, 'the tab bar has no New marker'
    assert 'ready to claim' in tab and 'new contracts on the board' in tab


def test_the_tab_bar_shows_NEW_alone_when_nothing_is_claimable(hunter):
    """The common case, and the one the suppression rule could easily have broken: new contracts and
    nothing to claim. `.pp-navhub__n--tab ~ .pp-navhub__new--tab` is a SIBLING selector, so with no
    count rendered there is nothing to match and the pill stands on its own -- but a rule written one
    character differently (`.pp-navhub__new--tab { display: none }`) would hide it always, and the
    markup test above would still pass because the span is rendered either way."""
    _contract('Fresh', announced_days_ago=0)

    body = hunter.get('/career/', **CF).content.decode()
    tab = body.split('class="mobile-tabbar-item', 1)[1].split('</a>', 1)[0]

    assert 'pp-navhub__new--tab' in tab, 'no New marker on the phone nav'
    assert 'pp-navhub__n--tab' not in tab, 'fixture wrong: something is claimable'
    # The suppression must be the SIBLING form, or the pill is hidden with nothing to hide it for.
    css_rule = '.pp-navhub__n--tab ~ .pp-navhub__new--tab { display: none; }'
    from pathlib import Path

    from django.conf import settings
    css = (Path(settings.BASE_DIR) / 'static' / 'css' / 'components' / 'chrome.css').read_text(
        encoding='utf-8')
    assert css_rule in css
    # ANCHORED TO THE START OF A RULE. The sibling rule above CONTAINS this exact substring, so the
    # unanchored form asserted that the correct CSS was absent -- a guard that fails when the code is
    # right, which is the same bug as one that passes when it is wrong.
    assert (chr(10) + '.pp-navhub__new--tab { display: none') not in css, (
        'the New pill is hidden unconditionally, not only behind a count'
    )


def test_the_tab_bar_shows_one_marker_at_a_time():
    """Both at once would overlap on a 20px icon, so the New pill steps aside for the count -- the
    count already says there is something waiting, which is the more urgent half. CSS-only, because
    both are rendered and the suppression is a sibling rule."""
    from pathlib import Path

    from django.conf import settings

    css = (Path(settings.BASE_DIR) / 'static' / 'css' / 'components' / 'chrome.css').read_text(
        encoding='utf-8')

    assert '.pp-navhub__n--tab ~ .pp-navhub__new--tab { display: none; }' in css


def test_a_quiet_account_gets_no_markers(hunter):
    body = hunter.get('/career/', **CF).content.decode()

    assert _count_marker(body) is None
    assert 'pp-navhub__new' not in body


def test_the_count_is_capped_in_the_markup(hunter):
    """A three-digit badge would widen the nav item enough to move the row."""
    for i in range(12):
        _claimable(hunter.profile, _contract('Claim Me %02d' % i))

    body = hunter.get('/career/', **CF).content.decode()

    assert _count_marker(body) == '9+'


def test_an_anonymous_visitor_pays_nothing(monkeypatch):
    """The processor returns before it does ANY work for a viewer with no profile -- the
    whole cost for them, and it runs on the Django admin and every anonymous page too.

    Asserting the empty dict cannot see this: without the guard the try/except returns exactly
    the same empty dict, having reached it by raising. What separates the two is the LOG -- so
    every anonymous page render on the site would write a debug traceback, which is control
    flow by exception and a noisy way to do nothing."""
    from plat_pursuit import context_processors as cp

    logged = []
    monkeypatch.setattr(cp.logger, 'debug', lambda *a, **kw: logged.append(a))

    class _Req:
        user = None

    assert cp.career_attention(_Req()) == {}
    assert not logged, 'an anonymous request fell through to the exception handler'


def test_the_markers_never_break_a_page(hunter, monkeypatch):
    """Fails closed, like the What's New and moderation processors: a hunter loses a marker for one
    render and nobody gains one. A nav that 500s because a badge could not be counted would be a
    poor trade for a marker."""
    def _boom(*a, **kw):
        raise RuntimeError('cache is down')

    monkeypatch.setattr(career_attention, 'claimable_count', _boom)

    assert hunter.get('/career/', **CF).status_code == 200
