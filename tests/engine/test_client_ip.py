"""core/client_ip.py: the visitor's IP behind Cloudflare and Render.

allauth 65.14.2 stopped trusting X-Forwarded-For by default, so after the 2026-10 upgrade every
login, signup and reset limit keyed on REMOTE_ADDR, which behind Render is the proxy: one bucket
for the whole site. django-ratelimit had read REMOTE_ADDR all along, and ModerationLog took the
first X-Forwarded-For entry, which the client writes. These pin the one helper all three now use.

The addresses below are real public ones (TEST-NET ranges count as private to `ipaddress`).
"""
import pytest
from django.core.cache import cache
from django.test import RequestFactory
from django.urls import reverse

from core.client_ip import client_ip
from tests.factories import UserFactory

RENDER_PROXY = '10.214.3.7'      # REMOTE_ADDR behind Render: private, the same for everyone
VISITOR = '9.9.9.9'
OTHER_VISITOR = '8.8.4.4'
FORGED = '6.6.6.6'
CLOUDFLARE_EDGE = '172.70.120.15'  # inside 172.64.0.0/13


def _request(xff=None, remote=RENDER_PROXY, **headers):
    meta = {'REMOTE_ADDR': remote}
    if xff is not None:
        meta['HTTP_X_FORWARDED_FOR'] = xff
    meta.update(headers)
    return RequestFactory().get('/', **meta)


# ── the helper ──────────────────────────────────────────────────────────────────────────────

def test_without_a_proxy_the_peer_is_the_visitor():
    """Local dev and the test client: no X-Forwarded-For at all."""
    assert client_ip(_request(remote='127.0.0.1')) == '127.0.0.1'


def test_beta_reads_the_address_render_appended():
    """Beta is Render with no Cloudflare: Render appends the address that connected to it."""
    assert client_ip(_request(xff=VISITOR)) == VISITOR


def test_a_forged_forwarded_for_entry_is_ignored():
    """The client can write any X-Forwarded-For it likes; Render appends the real address after it."""
    assert client_ip(_request(xff=f'{FORGED}, {VISITOR}')) == VISITOR


def test_render_internal_hops_on_the_right_are_skipped():
    assert client_ip(_request(xff=f'{VISITOR}, 10.0.4.2, 172.16.0.9')) == VISITOR


def test_production_trusts_cloudflare_for_the_visitor():
    """Production is Cloudflare -> Render: the connecting address is a Cloudflare edge, so the
    visitor is the one CF-Connecting-IP names."""
    request = _request(xff=f'{VISITOR}, {CLOUDFLARE_EDGE}', HTTP_CF_CONNECTING_IP=VISITOR, HTTP_CF_RAY='8f00-IAD')
    assert client_ip(request) == VISITOR


def test_cloudflare_ipv6_edges_count_too():
    request = _request(xff=f'{VISITOR}, 2606:4700:3031::ac43:a1b2', HTTP_CF_CONNECTING_IP=VISITOR)
    assert client_ip(request) == VISITOR


def test_a_forged_cloudflare_header_on_a_direct_request_is_ignored():
    """Someone sending straight to the Render URL can set CF-Connecting-IP, but the address that
    connected is theirs, not Cloudflare's, so the header is not believed."""
    request = _request(xff=OTHER_VISITOR, HTTP_CF_CONNECTING_IP=FORGED, HTTP_CF_RAY='forged')
    assert client_ip(request) == OTHER_VISITOR


def test_a_bad_cloudflare_header_falls_back_to_the_edge():
    request = _request(xff=f'{VISITOR}, {CLOUDFLARE_EDGE}', HTTP_CF_CONNECTING_IP='not-an-ip')
    assert client_ip(request) == CLOUDFLARE_EDGE


@pytest.mark.parametrize('xff', ['unknown', 'garbage, , ', '10.0.0.1'])
def test_an_unusable_forwarded_for_falls_back_to_the_peer(xff):
    assert client_ip(_request(xff=xff)) == RENDER_PROXY


@pytest.mark.parametrize('xff, expected', [
    (f'{VISITOR}:51234', VISITOR),
    ('[2001:4860:4860::8888]:443', '2001:4860:4860::8888'),
    (f'  {VISITOR}  ', VISITOR),
])
def test_ports_and_spaces_are_tolerated(xff, expected):
    assert client_ip(_request(xff=xff)) == expected


def test_no_address_at_all_is_empty():
    assert client_ip(_request(remote='')) == ''


@pytest.mark.parametrize('hop', ['100.64.12.1', '192.0.2.9', '240.0.0.1'])
def test_shared_and_reserved_hops_are_not_visitors(hop):
    """Shared address space (100.64.0.0/10) is not private to `ipaddress`, but it is a proxy hop,
    not a visitor; reading it as one would put everyone back in one bucket."""
    assert client_ip(_request(xff=f'{VISITOR}, {hop}')) == VISITOR


def test_ipv4_mapped_addresses_are_read_as_ipv4():
    """Otherwise a mapped Cloudflare edge is not recognised, and django-ratelimit's /64 mask for
    anything with a colon puts every mapped visitor in one bucket."""
    request = _request(xff=f'::ffff:{VISITOR}, ::ffff:{CLOUDFLARE_EDGE}', HTTP_CF_CONNECTING_IP=f'::ffff:{VISITOR}')
    assert client_ip(request) == VISITOR


def test_an_ipv6_zone_is_dropped():
    """A zone id is not part of the address, and Postgres' inet column rejects one."""
    request = _request(xff=f'{VISITOR}, {CLOUDFLARE_EDGE}', HTTP_CF_CONNECTING_IP='2001:4860:4860::8888%eth0')
    assert client_ip(request) == '2001:4860:4860::8888'


# ── who uses it ─────────────────────────────────────────────────────────────────────────────

def test_allauth_reads_the_visitor_not_the_proxy():
    from allauth.account.adapter import get_adapter

    request = _request(xff=f'{FORGED}, {VISITOR}')
    assert get_adapter(request).get_client_ip(request) == VISITOR


def test_django_ratelimit_reads_the_visitor_not_the_proxy():
    from django_ratelimit.core import _get_ip

    assert _get_ip(_request(xff=f'{FORGED}, {VISITOR}')) == VISITOR


THROTTLED = 'Too many failed login attempts'


@pytest.mark.django_db
def test_allauth_rate_limits_are_per_visitor_behind_the_proxy(client):
    """The regression itself: one visitor using up the failed-login limit (10/m/ip) must not lock
    out a second visitor who reaches the app through the same Render proxy. Each attempt uses a
    different email so the per-email part of the limit never trips first. allauth reports this
    limit as a form error on the re-rendered login page, not as a 429."""
    cache.clear()
    url = reverse('account_login')

    def attempt(visitor, email):
        resp = client.post(url, {'login': email, 'password': 'wrong'},
                           REMOTE_ADDR=RENDER_PROXY, HTTP_X_FORWARDED_FOR=visitor)
        assert resp.status_code == 200
        return THROTTLED in resp.content.decode()

    assert not any(attempt(VISITOR, f'nobody{n}@example.com') for n in range(10))
    assert attempt(VISITOR, 'nobody-final@example.com'), 'the 11th failure from one visitor is throttled'
    assert not attempt(OTHER_VISITOR, 'someone-else@example.com'), 'a different visitor is not throttled'
    cache.clear()


def test_drf_throttles_anonymous_callers_by_visitor_not_by_raw_header():
    """DRF's own get_ident returns the raw X-Forwarded-For, so a fresh forged value per request
    was a fresh bucket."""
    from core.throttling import VisitorUserRateThrottle

    throttle = VisitorUserRateThrottle()
    first = throttle.get_ident(_request(xff=f'1.1.1.1, {VISITOR}'))
    second = throttle.get_ident(_request(xff=f'2.2.2.2, {VISITOR}'))
    assert first == second == VISITOR


@pytest.mark.parametrize('ip, key', [
    (VISITOR, VISITOR),
    ('2601:147:4002:4930:6ceb:691b:2b4c:26ea', '2601:147:4002:4930::/64'),
    ('2601:147:4002:4930::1', '2601:147:4002:4930::/64'),
    ('2601:147:4002:4931::1', '2601:147:4002:4931::/64'),
    ('', ''),
])
def test_drf_buckets_ipv6_by_its_64_like_the_other_limiters(ip, key):
    """A home IPv6 connection gets a whole /64 and can rotate through it; allauth and
    django-ratelimit already bucket by /64, and the DRF throttle now agrees with them."""
    from core.throttling import throttle_key

    assert throttle_key(ip) == key


@pytest.mark.django_db
def test_rotating_ipv6_addresses_in_one_64_shares_one_drf_bucket():
    from django.contrib.auth.models import AnonymousUser

    from core.throttling import VisitorUserRateThrottle

    class TwoPerMinute(VisitorUserRateThrottle):
        rate = '2/min'

    def from_address(address):
        request = _request(xff=f'{address}, {CLOUDFLARE_EDGE}', HTTP_CF_CONNECTING_IP=address)
        request.user = AnonymousUser()
        return request

    cache.clear()
    same_home = ['2601:147:4002:4930::1', '2601:147:4002:4930::2', '2601:147:4002:4930::3']
    allowed = [TwoPerMinute().allow_request(from_address(a), view=None) for a in same_home]
    assert allowed == [True, True, False], 'a third address in the same /64 is the same visitor'
    assert TwoPerMinute().allow_request(from_address('2601:147:4002:5000::1'), view=None),         'a different /64 is a different visitor'
    cache.clear()


def test_drf_uses_the_visitor_throttle_by_default():
    from django.conf import settings

    assert settings.REST_FRAMEWORK['DEFAULT_THROTTLE_CLASSES'] == ['core.throttling.VisitorUserRateThrottle']


@pytest.mark.django_db
def test_moderation_logs_store_the_visitor_not_a_forged_entry():
    """The logs used to take the first X-Forwarded-For entry, which the client writes."""
    from trophies.models import ModerationLog, ReviewModerationLog
    from tests.factories import CommentFactory, ReviewFactory

    moderator = UserFactory(is_staff=True)
    request = _request(xff=f'{FORGED}, {VISITOR}')

    CommentFactory().soft_delete(moderator=moderator, reason='spam', request=request)
    ReviewFactory().soft_delete(moderator=moderator, reason='spam', request=request)

    assert ModerationLog.objects.latest('pk').ip_address == VISITOR
    assert ReviewModerationLog.objects.latest('pk').ip_address == VISITOR


# ── the staff check page ────────────────────────────────────────────────────────────────────

@pytest.mark.django_db
def test_the_request_ip_page_is_staff_only(client):
    url = reverse('admin_request_ip')
    assert client.get(url).status_code == 302

    client.force_login(UserFactory())
    assert client.get(url).status_code == 302


@pytest.mark.django_db
def test_the_request_ip_page_shows_the_chain_and_the_result(client):
    client.force_login(UserFactory(is_staff=True))
    resp = client.get(reverse('admin_request_ip'), REMOTE_ADDR=RENDER_PROXY, HTTP_X_FORWARDED_FOR=f'{FORGED}, {VISITOR}')

    assert resp.status_code == 200
    assert resp.json() == {
        'client_ip': VISITOR,
        'remote_addr': RENDER_PROXY,
        'x_forwarded_for': f'{FORGED}, {VISITOR}',
        'cf_connecting_ip': '',
        'cf_ray': '',
    }
