"""DRF throttling keyed on the visitor, not on whatever X-Forwarded-For says.

DRF's default `get_ident` returns the raw X-Forwarded-For header for anonymous callers, which the
caller writes: a fresh value per request is a fresh bucket, so the anonymous API had no throttle.

The Discord bot is exempt. It calls PlatPursuit for every Discord member through its one account,
so a per-user limit made all of them share one 60-a-minute bucket: on a busy minute every member's
commands failed together. django-ratelimit already exempts it (`api.views.bot_exempt_rate`), its
endpoints are bot-only, and its commands carry their own per-member cooldowns.
"""
import ipaddress

from rest_framework.throttling import UserRateThrottle

from api.permissions import is_discord_bot
from core.client_ip import client_ip

#: IPv6 visitors are throttled by network, not address: a home connection is handed a whole /64
#: and can rotate through it. 64 matches allauth (RATE_LIMIT_IPV6_PREFIX) and django-ratelimit
#: (RATELIMIT_IPV6_MASK), so all three limiters agree on who one visitor is.
IPV6_PREFIX = 64


def throttle_key(ip):
    """The bucket for `ip`: the address itself for IPv4, the start of its /64 for IPv6 (the same
    shape allauth and django-ratelimit key on)."""
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return ip
    if addr.version == 6:
        return str(ipaddress.ip_network(f'{addr}/{IPV6_PREFIX}', strict=False).network_address)
    return ip


class VisitorUserRateThrottle(UserRateThrottle):
    """`UserRateThrottle` (signed-in callers by user id), with anonymous callers by real IP, and the
    Discord bot exempt."""

    def allow_request(self, request, view):
        if is_discord_bot(request):
            return True
        return super().allow_request(request, view)

    def get_ident(self, request):
        return throttle_key(client_ip(request))
