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
    component, so zcard always returned 0 and every APIAuditLog row recorded the full 300."""
    from trophies.util_modules import cache as cache_mod

    monkeypatch.setenv('MACHINE_ID', 'worker-7')
    seen = {}

    class FakeRedis:
        def zcard(self, key):
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
        def zcard(self, key):
            raise ConnectionError('redis is gone')

    monkeypatch.setattr(cache_mod, 'redis_client', DeadRedis())
    assert cache_mod._calls_in_window('tok') == 0


# --- the third-party call on the sync hot path --------------------------------------------------

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

    def fake_get(url, timeout=None):
        calls.append(url)
        return FakeResponse()

    monkeypatch.setattr(cache_mod, 'redis_client', FakeRedis())
    monkeypatch.setattr(cache_mod.requests, 'get', fake_get)

    assert cache_mod._egress_ip() == '203.0.113.7'
    for _ in range(20):
        cache_mod._egress_ip()

    assert len(calls) == 1, f'resolved the egress IP {len(calls)} times'


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


# --- the watermark that skipped past a failure forever ------------------------------------------

@pytest.mark.django_db
def test_the_dlc_watermark_is_held_when_a_series_fails(monkeypatch):
    """The per-series handler catches and carries on, so the watermark advanced regardless -- and the
    next run only scans groups created AFTER it. A series that raised was never swept again, and its
    owners kept a false "100% complete" permanently, evidenced by one ERROR line in a nightly log."""
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
    # The game predates the window (so the new group reads as DLC rather than a brand-new game).
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
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError('evaluation blew up')))

    with pytest.raises(CommandError):
        call_command('detect_dlc_and_refresh', since=watermark.isoformat(),
                     stdout=io.StringIO(), stderr=io.StringIO())

    assert not written, 'the watermark advanced past a series that never got swept'


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

    with pytest.raises(CommandError):
        call_command('refresh_homepage_hourly', stdout=io.StringIO(), stderr=io.StringIO())
