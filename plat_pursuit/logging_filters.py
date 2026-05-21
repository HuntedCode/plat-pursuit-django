"""Logging filters that suppress routine bot/scanner noise.

These filters drop log records whose only signal value is "a botnet exists,"
keeping the console focused on records that can actually drive incident
response or product decisions.
"""
import logging
import re


# Bot-magnet URLs: paths that scripted scanners spray with method-mismatched
# requests dozens of times per minute. Each one is a known-rejected route
# (405 / 404) so the warning carries no diagnostic value.
#
# Patterns:
#   /accounts/confirm-email/   -> allauth's keyless verification-sent landing
#                                 page (GET only). Bots POST hoping to bypass
#                                 the <key> requirement.
#   /xmlrpc.php                -> WordPress pingback / brute-force probe; we
#                                 don't run WordPress.
#   /wp-login.php, /wp-admin*  -> WordPress login probes; ditto.
BOT_MAGNET_405_PATTERN = re.compile(
    r'Method Not Allowed \([A-Z]+\): /'
    r'(accounts/confirm-email/|xmlrpc\.php|wp-login\.php|wp-admin)',
    re.IGNORECASE,
)


class SuppressBotMagnet405(logging.Filter):
    """Drop 405-Method-Not-Allowed warnings on known bot-magnet URLs.

    Narrow on purpose: only swallows the specific message pattern Django emits
    from `django.request` when a request hits a route with the wrong method on
    one of the URLs in BOT_MAGNET_405_PATTERN. Everything else (real 405s on
    real endpoints, 500s, debug output) flows through untouched.
    """

    def filter(self, record):
        return not BOT_MAGNET_405_PATTERN.search(record.getMessage())
