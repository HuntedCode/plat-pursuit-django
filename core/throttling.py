"""DRF throttling keyed on the visitor, not on whatever X-Forwarded-For says.

DRF's default `get_ident` returns the raw X-Forwarded-For header for anonymous callers, which the
caller writes: a fresh value per request is a fresh bucket, so the anonymous API had no throttle.
"""
import ipaddress

from rest_framework.throttling import UserRateThrottle

from core.client_ip import client_ip

#: IPv6 visitors are throttled by network, not address: a home connection is handed a whole /64
#: and can rotate through it. 64 matches allauth (RATE_LIMIT_IPV6_PREFIX) and django-ratelimit
#: (RATELIMIT_IPV6_MASK), so all three limiters agree on who one visitor is.
IPV6_PREFIX = 64


def throttle_key(ip):
    """The bucket for `ip`: the address itself for IPv4, its /64 network for IPv6."""
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return ip
    if addr.version == 6:
        return str(ipaddress.ip_network(f'{addr}/{IPV6_PREFIX}', strict=False))
    return ip


class VisitorUserRateThrottle(UserRateThrottle):
    """`UserRateThrottle` (signed-in callers by user id), with anonymous callers by real IP."""

    def get_ident(self, request):
        return throttle_key(client_ip(request))
