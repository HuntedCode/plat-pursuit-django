"""The PSN API client the TokenKeeper's workers use: psnawp's client with a thread-safe, in-memory
rate limiter and an optional proxy.

Its own module so it can be imported (and tested) without importing trophies.token_keeper, which
starts the TokenKeeper singleton on import.

The three classes copy psnawp's __init__s instead of calling them, because psnawp's RequestBuilder
opens an SQLite rate-limit bucket that locks under threads. Keep each in step with psnawp's own on
every psnawp upgrade; tests/engine/test_psn_client_init.py builds real clients and compares them.
"""
from random import choice

import requests
from psnawp_api import PSNAWP as BasePSNAWP
from psnawp_api.core.authenticator import Authenticator as BaseAuthenticator
from psnawp_api.core.request_builder import RequestBuilder as BaseRequestBuilder
from pyrate_limiter import Duration, InMemoryBucket, Limiter, Rate


class ProxiedRequestBuilder(BaseRequestBuilder):
    def __init__(self, common_headers, rate_limit, proxy_url=None):
        # DO NOT call super().__init__() - it creates an SQLite bucket which causes
        # "database is locked" errors in multi-threaded environments.
        # Instead, replicate initialization with thread-safe in-memory bucket.
        #
        # KEEP THIS IN STEP WITH psnawp's RequestBuilder.__init__ (psnawp_api/core/request_builder.py)
        # on every psnawp upgrade: tests/engine/test_psn_client_init.py builds a real client and
        # compares the attributes. psnawp 3.0.1 moved to pyrate-limiter 4, whose Limiter takes no
        # raise_when_fail/max_delay (the old call raised TypeError on every client build) and whose
        # try_acquire blocks until the rate allows the call; psnawp 3.0.1 also started keeping
        # `rate_limit` on the builder.

        from typing import cast
        self.common_headers = cast("dict[str, str]", common_headers)
        self.rate_limit = rate_limit

        # Create thread-safe limiter with in-memory bucket (not SQLite)
        bucket = InMemoryBucket([rate_limit])
        self.limiter = Limiter(bucket)

        # Initialize session (same as base class)
        self.session = requests.Session()
        self.session.headers.update(self.common_headers)

        # Add proxy support
        if proxy_url:
            self.session.proxies = {'http': proxy_url, 'https': proxy_url}

class ProxiedAuthenticator(BaseAuthenticator):
    def __init__(self, npsso_cookie, common_headers, rate_limit, proxy_url=None):
        # DO NOT call super().__init__() - it creates a RequestBuilder with an SQLite
        # bucket that spawns a Leaker daemon thread. That thread persists even after we
        # replace request_builder, causing "database is locked" errors.
        # Instead, replicate the parent's initialization with our proxied builder.
        import uuid
        self.npsso_cookie = npsso_cookie
        self.common_headers = common_headers
        self.request_builder = ProxiedRequestBuilder(common_headers, rate_limit, proxy_url=proxy_url)
        self.token_response = None
        self.cid = str(uuid.UUID(int=uuid.getnode()))

class ProxiedPSNAWP(BasePSNAWP):
    def __init__(self, npsso_cookie, headers=None, rate_limit=None, proxy_url=None):
        random_ua = [
            "Mozilla/5.0 (Linux; Android 6.0; Nexus 5 Build/MRA58N) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/55.0.6598.1817 Mobile Safari/537.36",
            "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Mobile Safari/537.36",
            "Mozilla/5.0 (Linux; Android 5.0; SM-G900P Build/LRX21T) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/39.0.5707.1741 Mobile Safari/537.36",
            "Mozilla/5.0 (Android 14; Mobile; rv:137.0) Gecko/137.0 Firefox/137.0",
            "Mozilla/5.0 (Linux; Android 6.0; Nexus 5 Build/MRA58N) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/55.0.9318.1385 Mobile Safari/537.36",
            "Mozilla/5.0 (iPhone; CPU iPhone OS 18_3_2 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) CriOS/135.0.7049.83 Mobile/15E148 Safari/604.1",
            "Mozilla/5.0 (iPhone; CPU iPhone OS 18_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.5 Mobile/15E148 Safari/604.1",
            "Mozilla/5.0 (iPhone; CPU iPhone OS 11_0 like Mac OS X) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/44.0.2469.1901 Mobile Safari/537.36",
            "Mozilla/5.0 (iPhone; CPU iPhone OS 18_3_2 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.3.1 Mobile/15E148 Safari/604.1",
            "Mozilla/5.0 (iPhone; CPU iPhone OS 18_3_2 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.3.1 Mobile/15E148 Safari/604.1",
        ]
        default_headers = {
            "User-Agent": choice(random_ua),
            "Accept-Language": "en-US,en;q=0.9",
            "Country": "US",
        }

        if rate_limit is None:
            rate_limit = Rate(1, Duration.SECOND * 3)

        self.authenticator = ProxiedAuthenticator(
            npsso_cookie=npsso_cookie,
            common_headers=default_headers,
            rate_limit=rate_limit,
            proxy_url=proxy_url
        )
