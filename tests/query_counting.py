"""Counting a request's queries from the same starting point every time.

The root conftest empties the Django cache before each test (`_cache_starts_empty`), so a test's FIRST render pays
for filling site-wide caches (the live group badges, the current art-reveal event) and every later render in that
test finds them warm. A test that compares two renders ("a big catalogue costs the same as a small one") would then
compare a cold render with a warm one and see a difference that has nothing to do with what it measures.
`cold_capture` empties the cache before counting, so every measured render starts cold and two counts compare like
with like.
"""
from contextlib import contextmanager

from django.core.cache import cache
from django.db import connection
from django.test.utils import CaptureQueriesContext


@contextmanager
def cold_capture():
    """`CaptureQueriesContext` over the default connection, starting from an empty cache."""
    cache.clear()
    with CaptureQueriesContext(connection) as ctx:
        yield ctx
