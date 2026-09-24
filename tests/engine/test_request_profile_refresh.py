"""Asking for another hunter's profile to be refreshed.

The capability already existed before this endpoint: `SearchSyncProfileView` re-syncs any named hunter,
open to anyone. What did not exist was a way to ask from the profile page you are looking at, which is
where the desire comes from -- the hero renders "Synced 5 days ago" and offered nothing to do about it.

Why it is more than a convenience: `refresh_profiles` only picks up an unlinked, non-Discord-verified
profile once it is SEVEN DAYS stale, and that is most of the hunters anyone browses. For those, asking
is the only thing that makes them current inside a week.

The endpoint is signed-in only, on the rule: open to ADD a hunter nobody tracks yet (the anonymous
landing pitch), signed-in to refresh one we already have.
"""
from datetime import timedelta
from unittest.mock import MagicMock, patch

import pytest
from django.core.cache import cache
from django.urls import reverse
from django.utils import timezone

from tests.factories import ProfileFactory
from trophies.services.sync_service import SyncService

pytestmark = pytest.mark.django_db


def _url(psn_username):
    return reverse('request_profile_refresh', kwargs={'psn_username': psn_username})


@pytest.fixture(autouse=True)
def _quiet_externals():
    """Stub the job queue and the outage flag; let the cooldown run for real.

    `PSNManager.profile_refresh` is the boundary, not `Profile.attempt_sync`: stubbing `attempt_sync`
    would disable the cooldown these tests are about. See the same note in `test_navbar_add_sync.py`,
    where patching it that way hid a refusal two tests believed they were exercising.
    """
    cache.clear()   # the rate limiter is cache-backed; a shared bucket would leak between tests
    with patch('trophies.psn_manager.PSNManager.profile_refresh') as refresh, \
         patch('trophies.psn_manager.PSNManager.is_psn_outage_active', return_value=False) as outage:
        yield {'profile_refresh': refresh, 'outage': outage}


def _viewer(client, psn_username='viewer'):
    """A signed-in member doing the asking."""
    member = ProfileFactory(psn_username=psn_username)
    client.force_login(member.user)
    return member


def _subject(psn_username='stalehunter', **kwargs):
    """The hunter being refreshed, outside their cooldown.

    `last_synced` is backdated explicitly because the model defaults it to `timezone.now`, so a factory
    hunter is inside the standard one-hour cooldown from the moment it exists.
    """
    kwargs.setdefault('last_synced', timezone.now() - timedelta(days=5))
    return ProfileFactory(psn_username=psn_username, **kwargs)


# ----------------------------------------------------------------------- the gate ----

def test_an_anonymous_visitor_is_refused_in_JSON_not_redirected(client, _quiet_externals):
    """A JSON endpoint must never answer with a redirect, and the first version of this test asserted
    the redirect and called it correct.

    `LoginRequiredMixin` 302s to the login page. `fetch` follows redirects by default, so the login HTML
    came back **200**, `response.ok` was true, and `API.request` returned the page as a STRING. The
    control then read `data.reason` off that string as `undefined`, announced "Updating now", polled,
    read the unchanged status, and finished with "Updated just now. Reload to see it." Nothing had been
    queued, and the hunter was told both that their refresh landed and that the page was now stale.

    Reachable by any session that expires between page load and click -- which is precisely when the
    control is on screen, because it renders only for an authenticated request.
    """
    _subject()

    resp = client.post(_url('stalehunter'))

    assert resp.status_code == 403, 'a redirect here reads as SUCCESS to fetch()'
    assert resp['Content-Type'].startswith('application/json')
    assert resp.json()['reason'] == SyncService.REFUSED_SIGN_IN
    _quiet_externals['profile_refresh'].assert_not_called()


def test_the_endpoint_does_not_use_the_redirecting_login_gate():
    """Structural, because the failure mode is invisible from the response of a test client that does
    not follow redirects: `LoginRequiredMixin` would put this back to a 302 and every assertion above
    except the status code would still read plausibly."""
    import inspect

    from trophies.views.sync_views import RequestProfileRefreshView

    assert 'LoginRequiredMixin' not in [c.__name__ for c in RequestProfileRefreshView.__mro__], \
        'a JSON endpoint is redirect-gated again'
    assert 'is_authenticated' in inspect.getsource(RequestProfileRefreshView)


def test_an_untracked_name_is_a_404_not_a_bad_request(client, _quiet_externals):
    """The name is well-formed; we simply do not have that hunter. Adding them is the navbar search's
    job and is open to everyone, so this must not read as "your input was wrong"."""
    _viewer(client)

    resp = client.post(_url('nobodyhasthisname'))

    assert resp.status_code == 404
    assert resp.json()['error']
    _quiet_externals['profile_refresh'].assert_not_called()


# ------------------------------------------------------------------ the happy path ----

def test_a_stale_hunter_is_queued_for_refresh(client, _quiet_externals):
    _viewer(client)
    subject = _subject()

    resp = client.post(_url('stalehunter'))

    assert resp.status_code == 200
    assert resp.json()['success'] is True
    _quiet_externals['profile_refresh'].assert_called_once()
    assert _quiet_externals['profile_refresh'].call_args.args[0] == subject


def test_a_requested_refresh_asks_to_jump_the_queue(client, _quiet_externals):
    """A person is sitting looking at the page. The cron sweep lands hundreds of `profile_refresh` jobs
    in the same orchestrator queue every 15 minutes, so without the jump a requested refresh waits
    behind all of them."""
    _viewer(client)
    _subject()

    client.post(_url('stalehunter'))

    assert _quiet_externals['profile_refresh'].call_args.kwargs['jump_queue'] is True


def test_the_lookup_is_case_insensitive(client, _quiet_externals):
    """Profile URLs canonicalise to lowercase, but a hunter's display name is not, and this endpoint is
    called with whatever the page was rendered for."""
    _viewer(client)
    _subject('casehunter')

    assert client.post(_url('CaseHunter')).status_code == 200


#: `track_site_event` drops bot traffic, and `is_bot_user_agent` treats an ABSENT or very short UA as a
#: bot -- which the Django test client is, since it sends none. Any test asserting on a SiteEvent has to
#: look like a browser or it is asserting on a deliberately-skipped write.
BROWSER_UA = {'HTTP_USER_AGENT': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                                 '(KHTML, like Gecko) Chrome/128.0 Safari/537.36'}


def test_a_requested_refresh_is_recorded_like_a_search(client, _quiet_externals):
    """`SearchSyncProfileView` records `sync_search` for every add and refresh; without this the newer
    surface would be invisible to the same funnel, and "which surface do hunters actually use to ask" is
    the question this feature exists to answer."""
    from core.models import SiteEvent

    _viewer(client)
    _subject('trackedevent')

    client.post(_url('trackedevent'), **BROWSER_UA)

    event = SiteEvent.objects.filter(event_type='sync_search').latest('id')
    assert 'trackedevent' in event.object_id
    assert 'refresh_request' in event.object_id, 'the two surfaces are indistinguishable in the funnel'


def test_a_scripted_caller_is_not_recorded(client, _quiet_externals):
    """The other half, and the reason the test above needs a UA at all: `track_site_event` skips bot
    traffic, and an absent User-Agent counts as bot. The funnel is for real clicks."""
    from core.models import SiteEvent

    _viewer(client)
    _subject('untrackedevent')

    client.post(_url('untrackedevent'))   # no UA, as the test client sends none

    assert not SiteEvent.objects.filter(event_type='sync_search').exists()


def test_the_lookup_uses_the_index_rather_than_iexact():
    """`iexact` compiles to `UPPER(col) = UPPER(%s)` on Postgres, which neither the unique constraint nor
    `psn_username_idx` can serve -- a sequential scan over every Profile, ten a minute per member and
    unbounded across members. `profile_views.py` records this exact trap for this exact column.

    `.lower()` + exact is correct as well as cheap, because `Profile.save()` lowercases the field
    unconditionally, so a stored value is never anything else.
    """
    import inspect

    from trophies.views.sync_views import RequestProfileRefreshView

    source = inspect.getsource(RequestProfileRefreshView)
    assert 'psn_username__iexact' not in source, 'the lookup seq-scans the Profile table again'
    assert '.strip().lower()' in source


# ------------------------------------------------------------------- the refusals ----

def test_a_recently_synced_hunter_is_refused_with_a_countdown(client, _quiet_externals):
    """The per-profile cooldown, which is the real cost control here: any number of people asking about
    the same profile inside its window produce ONE refresh, so the PSN cost per profile is capped
    however many ask. A factory hunter is inside it by construction."""
    _viewer(client)
    ProfileFactory(psn_username='freshhunter')   # last_synced defaults to now

    resp = client.post(_url('freshhunter'))

    assert resp.status_code == 429
    body = resp.json()
    assert body['reason'] == SyncService.REFUSED_COOLDOWN
    assert body['seconds_to_next_sync'] > 0, 'a refusal that will not say when is barely a refusal'
    _quiet_externals['profile_refresh'].assert_not_called()


def test_a_cooldown_refusal_carries_the_profile_it_refused(client, _quiet_externals):
    """So a caller can still offer the hunter. The refusal means "already current", not "no such
    hunter", and a control that goes red without a way forward is worse than the lie it replaced."""
    _viewer(client)
    ProfileFactory(psn_username='freshone')

    body = client.post(_url('freshone')).json()

    assert body['psn_username'] == 'freshone'
    assert body['slug'] == reverse('profile_detail', kwargs={'psn_username': 'freshone'})


def test_a_profile_already_syncing_reports_that_rather_than_a_new_sync(client, _quiet_externals):
    """This endpoint targets arbitrary browsed hunters, so it is the caller most likely to land on a
    profile mid-sync -- and the one where a person is sitting watching the page."""
    _viewer(client)
    _subject('alreadygoing', sync_status='syncing')

    resp = client.post(_url('alreadygoing'))

    assert resp.status_code == 200
    assert resp.json()['reason'] == SyncService.ALREADY_SYNCING
    _quiet_externals['profile_refresh'].assert_not_called()


def test_a_queued_refresh_says_so_distinctly_from_an_in_flight_one(client, _quiet_externals):
    """The two successes must be distinguishable, or a UI cannot tell "I just started this" from
    "this was already running" and will narrate the wrong beat."""
    _viewer(client)
    _subject('queuedone')

    body = client.post(_url('queuedone')).json()

    assert body['success'] is True
    assert body['reason'] == '', 'a freshly queued refresh is not `already_syncing`'


def test_an_outage_is_a_503_and_reads_as_temporary(client, _quiet_externals):
    """Distinguished from the cooldown's 429 on purpose: one means "come back in 40 minutes", the other
    means "nothing you do will help right now"."""
    _viewer(client)
    _subject()
    _quiet_externals['outage'].return_value = True

    resp = client.post(_url('stalehunter'))

    assert resp.status_code == 503
    assert resp.json()['reason'] == SyncService.REFUSED_OUTAGE
    _quiet_externals['profile_refresh'].assert_not_called()


def test_one_account_cannot_spend_tokens_across_unlimited_profiles(client, _quiet_externals):
    """The cooldown caps the cost PER PROFILE; this caps how many DIFFERENT profiles one account can
    spend tokens on. Both bounds are needed -- the cooldown alone would happily let one member refresh
    ten thousand distinct hunters."""
    _viewer(client)
    for i in range(10):
        _subject(f'stale{i:04d}')
        assert client.post(_url(f'stale{i:04d}')).status_code == 200

    _subject('onetoomany')
    resp = client.post(_url('onetoomany'))

    assert resp.status_code == 429
    assert resp.json()['reason'] == SyncService.REFUSED_RATE_LIMIT, \
        'the rate limit and the cooldown must be distinguishable; they are both 429'


def test_the_rate_limit_is_keyed_per_account_not_globally(client, _quiet_externals):
    """One member exhausting their budget must not lock everybody else out."""
    _viewer(client, 'firstviewer')
    for i in range(10):
        _subject(f'stale{i:04d}')
        client.post(_url(f'stale{i:04d}'))
    _subject('afterthelimit')
    assert client.post(_url('afterthelimit')).status_code == 429

    client.logout()
    _viewer(client, 'secondviewer')

    assert client.post(_url('afterthelimit')).status_code == 200


# -------------------------------------------------- the jump, at the Redis boundary ----

class TestQueueDirection:
    """`lpush` vs `rpush`, asserted at the Redis call.

    They are one character apart and getting them the wrong way round silently produces the OPPOSITE of
    the intent -- a requested refresh sent to the BACK of the queue -- with nothing anywhere to notice.
    Nothing above these tests would fail.

    The class exists to override the module's autouse fixture: that one stubs
    `PSNManager.profile_refresh`, which is exactly the function under test here. The first version of
    these tests inherited it and asserted against the mock, so neither push ever happened.
    """

    @pytest.fixture(autouse=True)
    def _quiet_externals(self):
        """Replaces the module fixture by name (nearest scope wins), keeping only the outage stub."""
        cache.clear()
        with patch('trophies.psn_manager.PSNManager.is_psn_outage_active', return_value=False):
            yield {}

    @staticmethod
    def _push_calls(jump):
        from trophies.psn_manager import PSNManager

        subject = _subject('directionhunter')
        fake = MagicMock()
        # Nothing on this path reads `redis_client.get` -- the outage flag is stubbed at
        # `is_psn_outage_active` by the class fixture. Set anyway so a future reader is not surprised by
        # a truthy Mock if that changes.
        fake.get.return_value = None
        with patch('trophies.psn_manager.redis_client', fake):
            PSNManager.profile_refresh(subject, jump_queue=jump)
        return fake

    def test_a_jumped_job_goes_to_the_end_the_worker_reads_next(self):
        """The worker does `brpop`, which pops the TAIL, and the normal path `lpush`es to the head. So
        the queue is FIFO, and `rpush` is what gets served next."""
        fake = self._push_calls(jump=True)

        assert fake.rpush.called, 'a jumped job was not pushed to the end brpop reads'
        assert not fake.lpush.called
        assert fake.rpush.call_args.args[0] == 'orchestrator_jobs'

    def test_an_ordinary_refresh_still_queues_normally(self):
        """The regression guard for the cron. `refresh_profiles` calls `profile_refresh` with no flag and
        must keep FIFO: if the default flipped, every scheduled sweep would serve its own jobs
        newest-first and the most-stale profiles would starve."""
        fake = self._push_calls(jump=False)

        assert fake.lpush.called
        assert not fake.rpush.called
        assert fake.lpush.call_args.args[0] == 'orchestrator_jobs'


def test_the_scheduled_sweep_does_not_ask_to_jump():
    """`refresh_profiles` must not pass `jump_queue`. If it did, the cron would front-run itself and the
    flag would mean nothing for the humans it was added for."""
    import inspect

    from core.management.commands import refresh_profiles

    source = inspect.getsource(refresh_profiles)
    assert 'jump_queue' not in source, 'the scheduled sweep is jumping its own queue'
