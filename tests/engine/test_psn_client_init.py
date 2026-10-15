"""Our PSN client classes (trophies/psn_client.py) copy psnawp's __init__s instead of calling them (psnawp's
builder opens an SQLite rate-limit bucket that locks under threads), so a psnawp upgrade that
changes those __init__s breaks ours without any error until a client is built.

That happened with psnawp 3.0.1: it moved to pyrate-limiter 4, whose Limiter rejects the
`raise_when_fail`/`max_delay` arguments our copy passed, so building any PSN client raised
TypeError and every sync would have stopped. CI stayed green because nothing built a client.
These build real clients (no network: construction makes no calls) and compare our copies with
psnawp's own classes.
"""
import ast
import pathlib
import threading
import time

import pytest
from psnawp_api import PSNAWP
from psnawp_api.core.authenticator import Authenticator
from psnawp_api.core.request_builder import RequestBuilder
from pyrate_limiter import Duration, InMemoryBucket, Rate

from trophies.psn_client import ProxiedAuthenticator, ProxiedPSNAWP, ProxiedRequestBuilder

TOKEN_KEEPER = pathlib.Path(__file__).resolve().parents[2] / 'trophies' / 'token_keeper.py'
HEADERS = {'User-Agent': 'test', 'Accept-Language': 'en-US', 'Country': 'US'}
RATE = Rate(1, Duration.SECOND)


def test_a_psn_client_can_be_built():
    client = ProxiedPSNAWP('fake-npsso')
    assert isinstance(client.authenticator.request_builder, ProxiedRequestBuilder)


def test_our_request_builder_sets_what_psnawps_does():
    ours = ProxiedRequestBuilder(HEADERS, RATE)
    theirs = RequestBuilder(HEADERS, RATE)
    assert set(vars(ours)) == set(vars(theirs))
    assert ours.rate_limit is RATE
    # The whole point of copying the __init__: an in-memory bucket, not psnawp's SQLite one.
    assert [type(b) for b in ours.limiter.bucket_factory.get_buckets()] == [InMemoryBucket]


def test_our_authenticator_sets_what_psnawps_does():
    ours = ProxiedAuthenticator('fake-npsso', HEADERS, RATE)
    theirs = Authenticator('fake-npsso', HEADERS, RATE)
    assert set(vars(ours)) == set(vars(theirs))


def test_our_client_sets_what_psnawps_does():
    assert set(vars(ProxiedPSNAWP('fake-npsso'))) == set(vars(PSNAWP('fake-npsso')))


PROXY = 'http://proxy.test:8080'


def test_the_proxy_reaches_the_session():
    builder = ProxiedRequestBuilder(HEADERS, RATE, proxy_url=PROXY)
    assert builder.session.proxies == {'http': PROXY, 'https': PROXY}


def test_the_clients_session_is_the_one_its_requests_use():
    client = ProxiedPSNAWP('fake-npsso', proxy_url=PROXY)
    assert client.session is client.authenticator.request_builder.session
    assert client.session.proxies == {'http': PROXY, 'https': PROXY}


def test_a_proxy_passed_by_position_is_refused():
    """The token refresh once passed the proxy second, into psnawp's `headers` slot, and every
    refreshed client went out without its proxy. Keyword-only makes that a TypeError."""
    with pytest.raises(TypeError):
        ProxiedPSNAWP('fake-npsso', PROXY)


def _client_builds_in_token_keeper():
    """Every ProxiedPSNAWP(...) call, bare or through a module (`psn_client.ProxiedPSNAWP(...)`)."""
    tree = ast.parse(TOKEN_KEEPER.read_text(encoding='utf-8'))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and node.module.endswith('psn_client'):
            for alias in node.names:
                assert alias.asname is None, f'ProxiedPSNAWP imported as {alias.asname}; this test would miss its calls'
    return [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and (getattr(node.func, 'id', None) or getattr(node.func, 'attr', None)) == 'ProxiedPSNAWP'
    ]


def test_every_client_the_token_keeper_builds_gets_its_proxy():
    """token_keeper.py starts the TokenKeeper on import, so its client builds are checked in the
    source: startup and the token refresh must both hand over the instance's proxy."""
    builds = _client_builds_in_token_keeper()
    assert len(builds) >= 2, f'expected the startup and refresh builds, found {len(builds)}'
    for call in builds:
        proxy = next((k.value for k in call.keywords if k.arg == 'proxy_url'), None)
        assert proxy is not None, f'token_keeper.py:{call.lineno} builds a client without its proxy'
        assert not (isinstance(proxy, ast.Constant) and proxy.value is None), f'token_keeper.py:{call.lineno} passes proxy_url=None'


@pytest.mark.parametrize('callers', [1, 3])
def test_the_limiter_paces_calls_as_psnawp_uses_it(callers):
    """psnawp calls limiter.try_acquire("psnawp-limiter") before each request and expects it to
    wait its turn (pyrate-limiter 4 blocks; our old v3 setup gave up after max_delay). One builder is shared by
    a worker's threads, so concurrent calls must queue too."""
    builder = ProxiedRequestBuilder(HEADERS, Rate(1, 200))  # one call per 200 ms
    stamps = []
    start = time.monotonic()

    def call():
        assert builder.limiter.try_acquire('psnawp-limiter')
        stamps.append(time.monotonic() - start)

    threads = [threading.Thread(target=call) for _ in range(callers * 2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)
    assert len(stamps) == callers * 2
    stamps.sort()
    gaps = [b - a for a, b in zip(stamps, stamps[1:])]
    # Paced gaps are about 0.25 s (rate plus the limiter's buffer); unpaced ones are near 0. The
    # wide margin absorbs a thread descheduled between its acquire and its stamp on slow CI.
    assert all(gap >= 0.1 for gap in gaps), gaps
