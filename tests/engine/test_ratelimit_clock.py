"""The root conftest pins the rate limiter's clock for every test (`_ratelimit_clock_is_fixed`).

`django_ratelimit` counts in fixed windows keyed on `int(time.time())`, so a test that spends a whole minute's
budget flaked whenever its requests straddled a window boundary. These pin the pin: the limiter's clock holds
still for the test, and every other clock keeps running.
"""
import time

import django_ratelimit.core as rl_core


def test_the_limiter_clock_holds_still_for_the_whole_test():
    first = rl_core.time.time()
    time.sleep(0.02)
    assert rl_core.time.time() == first


def test_every_other_clock_keeps_running():
    """The old per-file fixture froze `time.time` itself, for every caller. This one must not."""
    before = time.time()
    time.sleep(0.02)
    assert time.time() > before
    assert rl_core.time is not time, 'the limiter must read a stand-in, not the real module'
