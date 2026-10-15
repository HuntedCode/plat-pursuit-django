"""The visitor's IP address, for rate limits and moderation records.

`REMOTE_ADDR` is always Render's internal proxy, the same for every visitor. `X-Forwarded-For` is
a list in which only the entries the proxies appended can be trusted: anything to their left came
from the client and can be forged. So this walks the list from the right, skipping internal hops,
to find the address that connected to Render's edge. When that is a Cloudflare address,
`CF-Connecting-IP` names the visitor; otherwise the connecting address is the visitor.

Render appears to front every service with its own Cloudflare (a third-party header test against
an onrender.com service saw a Cloudflare hop and Cloudflare-set CF-Connecting-IP, and Cloudflare
answering a forged CF-Connecting-IP with a 403 before it reached the app), so in practice both
production and beta take the Cloudflare branch. The other branch keeps the answer right if that
ever changes. Check the live chain at /staff/request-ip/.

allauth (`ACCOUNT_ADAPTER`), django-ratelimit (`RATELIMIT_IP_META_KEY`), DRF throttling
(`core.throttling`) and the moderation logs all use this one function, so the rate limits and the
audit trail agree on who a visitor is.
"""
import ipaddress

#: https://www.cloudflare.com/ips/ (fetched 2026-10-15). Cloudflare changes these rarely and
#: announces it; a range missing here only means that Cloudflare edge is treated as the visitor
#: (coarser rate limits for users routed through it), never that a forged header is trusted.
CLOUDFLARE_NETWORKS = tuple(ipaddress.ip_network(net) for net in (
    '173.245.48.0/20', '103.21.244.0/22', '103.22.200.0/22', '103.31.4.0/22', '141.101.64.0/18',
    '108.162.192.0/18', '190.93.240.0/20', '188.114.96.0/20', '197.234.240.0/22', '198.41.128.0/17',
    '162.158.0.0/15', '104.16.0.0/13', '104.24.0.0/14', '172.64.0.0/13', '131.0.72.0/22',
    '2400:cb00::/32', '2606:4700::/32', '2803:f800::/32', '2405:b500::/32', '2405:8100::/32',
    '2a06:98c0::/29', '2c0f:f248::/32',
))


def _parse(value):
    """An IP address from a header value, or None. Tolerates spaces, a port suffix, an IPv6 zone
    and IPv4-mapped IPv6 (stored and rate-limited as the plain IPv4 address)."""
    value = (value or '').strip()
    if value.startswith('[') and ']' in value:  # [IPv6]:port
        value = value[1:value.index(']')]
    elif value.count(':') == 1:  # IPv4:port
        value = value.split(':', 1)[0]
    value = value.split('%', 1)[0]  # fe80::1%eth0: the zone is not part of the address
    try:
        ip = ipaddress.ip_address(value)
    except ValueError:
        return None
    return getattr(ip, 'ipv4_mapped', None) or ip


def _is_internal(ip):
    # Not just is_private: shared address space (100.64.0.0/10) and reserved ranges are not
    # visitors either, and treating a proxy hop as the visitor brings back the one-bucket bug.
    return not ip.is_global


def _is_cloudflare(ip):
    return any(ip in net for net in CLOUDFLARE_NETWORKS)


def client_ip(request):
    """The visitor's IP as a string, or '' when the request carries no usable address."""
    hops = [_parse(part) for part in request.META.get('HTTP_X_FORWARDED_FOR', '').split(',')]
    remote = _parse(request.META.get('REMOTE_ADDR'))
    # The address that connected to Render: the rightmost public hop. With no X-Forwarded-For
    # (local dev, tests) it is the direct peer.
    connecting = next((ip for ip in reversed(hops) if ip and not _is_internal(ip)), None)
    if connecting is None:
        connecting = remote
    if connecting is not None and _is_cloudflare(connecting):
        visitor = _parse(request.META.get('HTTP_CF_CONNECTING_IP'))
        if visitor is not None:
            return str(visitor)
    return str(connecting) if connecting is not None else ''
