"""Five failures that reported success.

Every one of these came out of the TokenKeeper audit sweep and shares a shape: the code caught its own
error, wrote a line to a log nobody reads, and carried on as if nothing happened. That is worse than a
crash, because a crash is evidence. None of them had a test.
"""
import io

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError


# --- the markdown safety net that itself raised -------------------------------------------------

def test_markdown_degrades_to_escaped_text_instead_of_500(monkeypatch):
    """`process_markdown`'s except handler exists to fall back to escaped plain text. It imported
    `escape_html` from util_modules.language, which has never defined it, so the one path whose whole
    job is to degrade gracefully raised ImportError -- and only when something else had already gone
    wrong, which is why it survived."""
    from trophies.services import checklist_service

    def boom(*a, **k):
        raise RuntimeError('markdown2 exploded')

    monkeypatch.setattr(checklist_service.markdown2, 'markdown', boom, raising=False)

    out = checklist_service.ChecklistService.process_markdown('<script>alert(1)</script> & co')

    assert '<script>' not in out, 'the fallback must escape, not pass through'
    assert '&lt;script&gt;' in out
    assert '&amp;' in out


# --- the audit column that was always 300 -------------------------------------------------------

def test_calls_remaining_reads_the_key_the_keeper_actually_writes(monkeypatch):
    """The TokenKeeper records timestamps under `token:{token}:{machine_id}:timestamps` and enforces
    its window off that same key, so throttling was always right. This read omitted the machine
    component, so the count always came back 0 and every APIAuditLog row recorded the full 300."""
    from trophies.util_modules import cache as cache_mod

    monkeypatch.setenv('MACHINE_ID', 'worker-7')
    seen = {}

    class FakeRedis:
        def zcount(self, key, floor, ceil):
            seen['key'] = key
            return 12

    monkeypatch.setattr(cache_mod, 'redis_client', FakeRedis())

    assert cache_mod._calls_in_window('tok') == 12
    assert seen['key'] == 'token:tok:worker-7:timestamps'


def test_calls_in_window_survives_a_redis_outage(monkeypatch):
    """It is called while logging an API call that already succeeded. A Redis blip must not cost the
    audit row, let alone the sync."""
    from trophies.util_modules import cache as cache_mod

    class DeadRedis:
        def zcount(self, key, floor, ceil):
            raise ConnectionError('redis is gone')

    monkeypatch.setattr(cache_mod, 'redis_client', DeadRedis())
    assert cache_mod._calls_in_window('tok') == 0


# --- the third-party call on the sync hot path --------------------------------------------------

@pytest.fixture(autouse=True)
def _reset_local_ip():
    """The process-local IP cache is module state; without this it leaks between tests."""
    from trophies.util_modules import cache as cache_mod
    cache_mod._LOCAL_IP.update(value=None, expires=0.0)
    yield
    cache_mod._LOCAL_IP.update(value=None, expires=0.0)


def test_the_egress_ip_is_fetched_once_not_per_api_call(monkeypatch):
    """This was a blocking `requests.get` to a third-party IP echo on EVERY successful PSN call. A
    whale's initial sync makes thousands."""
    from trophies.util_modules import cache as cache_mod

    calls = []
    store = {}

    class FakeRedis:
        def get(self, key):
            return store.get(key)

        def set(self, key, value, ex=None):
            store[key] = value.encode() if isinstance(value, str) else value

    class FakeResponse:
        text = ' 203.0.113.7 '

        def raise_for_status(self):
            return None

    def fake_get(url, timeout=None):
        calls.append(url)
        return FakeResponse()

    monkeypatch.setattr(cache_mod, 'redis_client', FakeRedis())
    monkeypatch.setattr(cache_mod.requests, 'get', fake_get)

    assert cache_mod._egress_ip() == '203.0.113.7'
    for _ in range(20):
        cache_mod._egress_ip()

    assert len(calls) == 1, f'resolved the egress IP {len(calls)} times'
    # The SHARED layer specifically. Without this the test survives deleting either cache on its own
    # (the other covers for it), so removing cross-process sharing entirely would stay green.
    assert cache_mod._EGRESS_IP_KEY in store, 'nothing was written for other workers to reuse'


def test_an_ip_lookup_outage_cannot_break_a_sync(monkeypatch):
    """THE serious half. This ran outside the try that guards the audit write, and log_api_call is
    called from inside _execute_api_call's try -- so an ipify outage raised, landed in that method's
    `except Exception`, which called log_api_call AGAIN, which raised again. The second raise escaped
    into the job worker's broad handler, whose `finally` still marks the job complete. Every sync
    would fail silently while reporting success."""
    from trophies.util_modules import cache as cache_mod

    class FakeRedis:
        def get(self, key):
            return None

        def set(self, key, value, ex=None):
            raise ConnectionError('redis is gone too')

    def dead_get(url, timeout=None):
        raise OSError('api.ipify.org unreachable')

    monkeypatch.setattr(cache_mod, 'redis_client', FakeRedis())
    monkeypatch.setattr(cache_mod.requests, 'get', dead_get)

    assert cache_mod._egress_ip() == 'unknown', 'an unknown IP is a worse audit row, not a lost sync'


def test_the_egress_ip_survives_a_redis_outage_without_hammering(monkeypatch):
    """With Redis down a Redis-only cache never populates, so EVERY call fell through to the blocking
    request again -- the original bug minus the raise. The process-local cache holds regardless."""
    from trophies.util_modules import cache as cache_mod

    calls = []

    class DeadRedis:
        def get(self, key):
            raise ConnectionError('redis is gone')

        def set(self, key, value, ex=None):
            raise ConnectionError('redis is gone')

    class FakeResponse:
        text = '203.0.113.9'

        def raise_for_status(self):
            return None

    monkeypatch.setattr(cache_mod, 'redis_client', DeadRedis())
    monkeypatch.setattr(cache_mod.requests, 'get',
                        lambda url, timeout=None: calls.append(url) or FakeResponse())

    for _ in range(20):
        assert cache_mod._egress_ip() == '203.0.113.9'

    assert len(calls) == 1, f'hit the network {len(calls)} times with Redis down'


def test_an_ip_outage_is_cached_so_it_does_not_retry_every_call(monkeypatch):
    """Returning "unknown" without caching meant every call retried with a 5s timeout while ipify was
    down. The sync stopped failing and became unusable instead, which is not an improvement."""
    from trophies.util_modules import cache as cache_mod

    calls = []

    class FakeRedis:
        def get(self, key):
            return None

        def set(self, key, value, ex=None):
            return None

    def dead_get(url, timeout=None):
        calls.append(url)
        raise OSError('api.ipify.org unreachable')

    monkeypatch.setattr(cache_mod, 'redis_client', FakeRedis())
    monkeypatch.setattr(cache_mod.requests, 'get', dead_get)

    for _ in range(15):
        assert cache_mod._egress_ip() == 'unknown'

    assert len(calls) == 1, f'retried a dead endpoint {len(calls)} times'


def test_an_error_page_is_not_cached_as_an_ip(monkeypatch):
    """Without raise_for_status a 5xx HTML body gets truncated to 45 chars and cached for an hour."""
    from trophies.util_modules import cache as cache_mod

    written = {}

    class FakeRedis:
        def get(self, key):
            return None

        def set(self, key, value, ex=None):
            written[key] = value

    class ErrorPage:
        text = '<html><head><title>502 Bad Gateway</title></head>'

        def raise_for_status(self):
            raise OSError('502')

    monkeypatch.setattr(cache_mod, 'redis_client', FakeRedis())
    monkeypatch.setattr(cache_mod.requests, 'get', lambda url, timeout=None: ErrorPage())

    assert cache_mod._egress_ip() == 'unknown'
    assert not written, 'an error page was cached as the egress IP for an hour'


def test_the_rate_limit_cap_follows_the_keepers_env_var(monkeypatch):
    """Hardcoding 300 here meant changing MAX_CALLS_PER_WINDOW made the column lie again."""
    from trophies.util_modules import cache as cache_mod

    monkeypatch.setenv('MAX_CALLS_PER_WINDOW', '500')
    assert cache_mod._max_calls() == 500


def test_calls_in_window_ignores_calls_that_aged_out(monkeypatch):
    """The zset has no TTL and only the keeper prunes it, so counting every member counts calls that
    left the window long ago and under-reports what is left."""
    from trophies.util_modules import cache as cache_mod

    seen = {}

    class FakeRedis:
        def zcount(self, key, floor, ceil):
            seen.update(key=key, floor=floor, ceil=ceil)
            return 7

    monkeypatch.setenv('MACHINE_ID', 'worker-3')
    monkeypatch.setenv('WINDOW_SECONDS', '900')
    monkeypatch.setattr(cache_mod, 'redis_client', FakeRedis())

    assert cache_mod._calls_in_window('tok') == 7
    assert seen['key'] == 'token:tok:worker-3:timestamps'
    assert seen['ceil'] == '+inf', 'a bare zcard would count expired members'


# --- the watermark that skipped past a failure forever ------------------------------------------

@pytest.mark.django_db
def test_a_failed_series_is_retried_by_name_not_by_holding_the_window(monkeypatch):
    """The per-series handler catches and carries on, so a failure used to be swept away silently.

    The first fix held the watermark, and that was a runaway: the scan window grows a day every night
    forever, every affected series is re-swept over everyone who played it, and `_recompute_completion`
    becomes the blanket historical backfill its own docstring forbids -- overwriting PSN's exact
    grade-weighted progress with the count-based approximation, nightly, on a growing game set.

    So the watermark ADVANCES and the failure is queued by name. Nothing is dropped; nothing grows.
    """
    from datetime import timedelta

    from django.utils import timezone

    from trophies.management.commands import detect_dlc_and_refresh as mod
    from tests.factories import (
        BadgeSeriesFactory, ConceptFactory, GameFactory, GroupBadgeFactory, StageFactory,
        TrophyGroupFactory,
    )

    watermark = timezone.now() - timedelta(days=1)
    concept = ConceptFactory()
    game = GameFactory(concept=concept)
    TrophyGroupFactory(game=game, trophy_group_id='default',
                       created_at=watermark - timedelta(days=30))
    TrophyGroupFactory(game=game, trophy_group_id='001', created_at=timezone.now())

    series = BadgeSeriesFactory()
    stage = StageFactory(series_slug=series.series_slug, stage_number=1)
    stage.concepts.add(concept)
    GroupBadgeFactory(series=series)

    written, queued = [], []
    monkeypatch.setattr(mod.Command, '_set_watermark', lambda self, when: written.append(when))
    monkeypatch.setattr(mod.Command, '_queue_retries', lambda self, slugs: queued.extend(slugs))
    monkeypatch.setattr(mod.Command, '_read_retries', lambda self: set())
    monkeypatch.setattr(mod, 'evaluate_and_apply_batch',
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError('evaluation blew up')))

    with pytest.raises(CommandError):
        call_command('detect_dlc_and_refresh', since=watermark.isoformat(),
                     stdout=io.StringIO(), stderr=io.StringIO())

    assert len(written) == 1, 'holding the watermark is the runaway this replaced'
    assert queued == [series.series_slug], 'the failure was dropped instead of retried'


@pytest.mark.django_db
def test_a_queued_retry_is_swept_even_when_the_window_is_empty(monkeypatch):
    """The other half of the contract: retrying by name only works if the retry actually runs on a
    night when nothing new appeared, which is the normal case."""
    from trophies.management.commands import detect_dlc_and_refresh as mod
    from tests.factories import BadgeSeriesFactory, GroupBadgeFactory

    series = BadgeSeriesFactory()
    GroupBadgeFactory(series=series)

    swept = []
    monkeypatch.setattr(mod.Command, '_set_watermark', lambda self, when: None)
    monkeypatch.setattr(mod.Command, '_read_retries', lambda self: {series.series_slug})
    monkeypatch.setattr(mod.Command, '_clear_retries', lambda self, slugs: None)
    monkeypatch.setattr(mod, 'evaluate_and_apply_batch',
                        lambda profiles, badges, *a, **k: swept.append(badges) or
                        {'awarded': 0, 'revoked': 0, 'updated': 0})

    call_command('detect_dlc_and_refresh', stdout=io.StringIO(), stderr=io.StringIO())

    assert swept, 'a queued retry never ran, so the failure really was dropped'


@pytest.mark.django_db
def test_a_crash_mid_run_does_not_lose_the_queued_retries(monkeypatch):
    """The first version popped the queue at the top of the run and re-queued at the bottom, with the
    whole refresh loop in between. A kill in that window dropped every popped slug -- and because
    their trophy groups predate the advanced watermark, no future window rediscovers them. That is
    the bug the queue exists to prevent, reintroduced by the mechanism meant to fix it.

    Reading must not consume. This asserts against the REAL Redis-backed implementations, not
    doubles, because the whole failure lived inside them.
    """
    from trophies.management.commands import detect_dlc_and_refresh as mod

    store = set()

    class FakeRedis:
        def smembers(self, key):
            return set(store)

        def srem(self, key, *members):
            store.difference_update(members)

        def sadd(self, key, *members):
            store.update(members)

    monkeypatch.setattr(mod, 'redis_client', FakeRedis())

    cmd = mod.Command()
    cmd._queue_retries(['a-series', 'b-series'])

    first = cmd._read_retries()
    assert first == {'a-series', 'b-series'}

    # Simulate the crash: nothing else runs. The queue must be intact.
    assert cmd._read_retries() == {'a-series', 'b-series'}, 'reading consumed the queue'

    # Only what succeeded is dropped.
    cmd._clear_retries(['a-series'])
    assert cmd._read_retries() == {'b-series'}


def test_the_scan_window_is_clamped(monkeypatch):
    """However old the stored watermark is. An unclamped window is what made holding it a runaway."""
    from datetime import timedelta

    from django.utils import timezone

    from trophies.management.commands.detect_dlc_and_refresh import Command, MAX_LOOKBACK

    now = timezone.now()
    ancient = (now - timedelta(days=400)).isoformat()

    class FakeRedis:
        def get(self, key):
            return ancient.encode()

    monkeypatch.setattr('trophies.management.commands.detect_dlc_and_refresh.redis_client', FakeRedis())

    resolved = Command()._resolve_watermark(None, now)

    assert resolved >= now - MAX_LOOKBACK - timedelta(seconds=5)


@pytest.mark.django_db
def test_the_dlc_watermark_advances_on_a_clean_pass(monkeypatch):
    """The other half: holding it on every run would re-sweep the same window forever."""
    from datetime import timedelta

    from django.utils import timezone

    from trophies.management.commands import detect_dlc_and_refresh as mod
    from tests.factories import (
        BadgeSeriesFactory, ConceptFactory, GameFactory, GroupBadgeFactory, StageFactory,
        TrophyGroupFactory,
    )

    watermark = timezone.now() - timedelta(days=1)
    concept = ConceptFactory()
    game = GameFactory(concept=concept)
    TrophyGroupFactory(game=game, trophy_group_id='default',
                       created_at=watermark - timedelta(days=30))
    TrophyGroupFactory(game=game, trophy_group_id='001', created_at=timezone.now())

    series = BadgeSeriesFactory()
    stage = StageFactory(series_slug=series.series_slug, stage_number=1)
    stage.concepts.add(concept)
    GroupBadgeFactory(series=series)

    written = []
    monkeypatch.setattr(mod.Command, '_set_watermark', lambda self, when: written.append(when))
    monkeypatch.setattr(mod, 'evaluate_and_apply_batch',
                        lambda *a, **k: {'awarded': 0, 'revoked': 0, 'updated': 0})

    call_command('detect_dlc_and_refresh', since=watermark.isoformat(),
                 stdout=io.StringIO(), stderr=io.StringIO())

    assert len(written) == 1, 'a clean pass must move the watermark forward'


# --- the cron that reported green while doing nothing -------------------------------------------

def test_the_hourly_refresh_exits_non_zero_when_a_step_fails(monkeypatch):
    """It wrote an ERROR to stdout and exited 0, so Render reported a green run while the landing
    served its static fixture indefinitely."""
    from core.management.commands import refresh_homepage_hourly as mod

    monkeypatch.setattr(mod, 'HOURLY_JOBS', [
        {'name': 'Site Heartbeat', 'key': 'hb', 'timeout': 60,
         'func': lambda: (_ for _ in ()).throw(RuntimeError('boom'))},
    ])

    with pytest.raises(CommandError) as exc:
        call_command('refresh_homepage_hourly', stdout=io.StringIO(), stderr=io.StringIO())

    # Named, because the landing-showcase block can append to `failed` independently: without this the
    # test passes when the showcase fails and the step under test quietly succeeded.
    assert 'Site Heartbeat' in str(exc.value)
