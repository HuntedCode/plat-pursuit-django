"""The LOGGING config must route every logger our code uses to the console at INFO.

Loggers missing from LOGGING used to fall through to Python's last-resort handler, which
drops INFO entirely: "Email sent successfully" (core.services.email_service) and every
other core/trophies/notifications INFO line never reached the server logs. The names are
discovered by scanning the source rather than listed here, so a new app or named logger
that LOGGING forgot fails this test instead of going quiet in prod.
"""

import logging
import os
import re
from functools import lru_cache
from pathlib import Path

from django.conf import settings

from plat_pursuit.logging_filters import SuppressBotMagnet405

_GET_LOGGER = re.compile(r"""getLogger\(\s*(?:(__name__)|(['"])([\w.]+)\2)\s*\)""")
_SKIP_DIRS = {
    "venv", ".venv", "env", "node_modules", ".git", "static", "staticfiles", "media", "tests",
    "__pycache__",
}


@lru_cache(maxsize=1)
def _logger_names_in_source():
    """Every logger name our code asks for: string literals as written, __name__ as the
    module's dotted path. Top-level scripts are skipped: they run as __main__ and
    configure their own logging (run_refresh_loop.py)."""
    root = Path(settings.BASE_DIR)
    names = set()
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]  # prune, never descend venv
        if Path(dirpath) == root:
            continue
        for filename in filenames:
            if not filename.endswith(".py"):
                continue
            path = Path(dirpath, filename)
            for match in _GET_LOGGER.finditer(path.read_text(encoding="utf-8")):
                if match.group(1):
                    names.add(".".join(path.relative_to(root).with_suffix("").parts))
                else:
                    names.add(match.group(3))
    return frozenset(names)


def _is_console(handler):
    return (
        isinstance(handler, logging.StreamHandler)
        and handler.level <= logging.INFO
        and any(isinstance(f, SuppressBotMagnet405) for f in handler.filters)
    )


def _reaches_console_at_info(name):
    """Walk the chain the way logging.Logger.callHandlers does."""
    logger = logging.getLogger(name)
    if not logger.isEnabledFor(logging.INFO):
        return False
    node = logger
    while node is not None:
        if any(_is_console(h) for h in node.handlers):
            return True
        if not node.propagate:
            return False
        node = node.parent
    return False


def test_the_source_scan_finds_the_loggers_it_is_meant_to_police():
    names = _logger_names_in_source()
    # The two that prompted the fix: a __name__ logger and the signup adapter.
    assert "core.services.email_service" in names
    assert "users.adapters" in names
    # A string-literal logger.
    assert "psn_api" in names


def test_every_logger_our_code_uses_reaches_the_console_at_info():
    silent = sorted(n for n in _logger_names_in_source() if not _reaches_console_at_info(n))
    assert silent == [], f"Add these to LOGGING['loggers'] in settings.py: {silent}"


def test_third_party_loggers_stay_at_warning():
    # Root sits at WARNING so libraries' INFO chatter (urllib3 under requests/psnawp) stays out.
    library = logging.getLogger("urllib3.connectionpool")
    assert not library.isEnabledFor(logging.INFO)
    assert library.isEnabledFor(logging.WARNING)
    assert _reaches_console_at_info("urllib3.connectionpool") is False


def test_third_party_warnings_reach_the_formatted_console():
    # Previously these hit the last-resort handler: unformatted, on stderr.
    assert any(_is_console(h) for h in logging.getLogger().handlers)


def test_django_records_are_emitted_once():
    # `django` holds the same console handler as the root. Letting it propagate would print
    # every request/server line twice.
    django_logger = logging.getLogger("django")
    assert any(_is_console(h) for h in django_logger.handlers)
    assert django_logger.propagate is False
