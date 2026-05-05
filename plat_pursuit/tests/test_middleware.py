from unittest import mock

from django.http import HttpResponse
from django.test import RequestFactory, SimpleTestCase, override_settings

from plat_pursuit.middleware import (
    BotCanonicalRedirectMiddleware,
    CloudflareOriginGuardMiddleware,
    MemoryDeltaMiddleware,
    _DANGER_RSS_MB,
    _HEAVY_REQUEST_MB,
)


def _passthrough(request):
    return HttpResponse('ok')


class BotCanonicalRedirectMiddlewareTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.middleware = BotCanonicalRedirectMiddleware(_passthrough)

    def _request(self, path, ua='', qs=''):
        full_path = f'{path}?{qs}' if qs else path
        return self.factory.get(full_path, HTTP_USER_AGENT=ua)

    def test_bot_on_profile_scoped_game_path_redirects_to_canonical(self):
        response = self.middleware(self._request(
            '/games/NPWR00352_00/deviousmeister/',
            ua='meta-webindexer/1.1 (+https://developers.facebook.com/docs/sharing/webmasters/crawler)',
        ))
        self.assertEqual(response.status_code, 301)
        self.assertEqual(response['Location'], '/games/NPWR00352_00/')

    def test_bot_on_profile_scoped_badge_path_preserves_query_string(self):
        response = self.middleware(self._request(
            '/my-pursuit/badges/remedy/deviousmeister/',
            ua='meta-webindexer/1.1',
            qs='tier=3',
        ))
        self.assertEqual(response.status_code, 301)
        self.assertEqual(response['Location'], '/my-pursuit/badges/remedy/?tier=3')

    def test_bot_on_canonical_game_path_passes_through(self):
        response = self.middleware(self._request(
            '/games/NPWR00352_00/',
            ua='meta-webindexer/1.1',
        ))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b'ok')

    def test_bot_on_canonical_badge_path_passes_through(self):
        response = self.middleware(self._request(
            '/my-pursuit/badges/remedy/',
            ua='Googlebot/2.1',
        ))
        self.assertEqual(response.status_code, 200)

    def test_human_on_profile_scoped_path_passes_through(self):
        response = self.middleware(self._request(
            '/games/NPWR00352_00/deviousmeister/',
            ua='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        ))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b'ok')

    def test_bot_on_unrelated_path_passes_through(self):
        response = self.middleware(self._request(
            '/community/profiles/foo/',
            ua='meta-webindexer/1.1',
        ))
        self.assertEqual(response.status_code, 200)

    def test_empty_user_agent_passes_through(self):
        response = self.middleware(self._request(
            '/games/NPWR00352_00/deviousmeister/',
            ua='',
        ))
        self.assertEqual(response.status_code, 200)

    def test_multiple_bot_user_agents_all_match(self):
        for ua in [
            'facebookexternalhit/1.1',
            'Googlebot/2.1 (+http://www.google.com/bot.html)',
            'Mozilla/5.0 (compatible; bingbot/2.0; +http://www.bing.com/bingbot.htm)',
            'CCBot/2.0 (https://commoncrawl.org/faq/)',
            'Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko; compatible; Claude-SearchBot/1.0)',
            'Mozilla/5.0 (compatible; PerplexityBot/1.0; +https://perplexity.ai/bot)',
            'Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko; compatible; Amzn-SearchBot/0.1) Chrome/119.0.6045.214 Safari/537.36',
            'Mozilla/5.0 (compatible; Barkrowler/0.9; +https://babbar.tech/crawler)',
        ]:
            with self.subTest(ua=ua):
                response = self.middleware(self._request(
                    '/games/NPWR00352_00/someuser/',
                    ua=ua,
                ))
                self.assertEqual(response.status_code, 301, msg=ua)
                self.assertEqual(response['Location'], '/games/NPWR00352_00/')

    def test_legacy_badges_prefix_redirects_directly_to_canonical(self):
        response = self.middleware(self._request(
            '/badges/remedy/deviousmeister/',
            ua='Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko; compatible; Amzn-SearchBot/0.1)',
        ))
        self.assertEqual(response.status_code, 301)
        self.assertEqual(response['Location'], '/my-pursuit/badges/remedy/')

    def test_legacy_achievements_badges_prefix_redirects_directly_to_canonical(self):
        response = self.middleware(self._request(
            '/achievements/badges/remedy/deviousmeister/',
            ua='Mozilla/5.0 (compatible; Barkrowler/0.9; +https://babbar.tech/crawler)',
            qs='tier=2',
        ))
        self.assertEqual(response.status_code, 301)
        self.assertEqual(response['Location'], '/my-pursuit/badges/remedy/?tier=2')

    def test_legacy_badges_canonical_path_passes_through(self):
        # The non-profile legacy /badges/<slug>/ still exists as a 301 route
        # handled by urls.py; our middleware should only match the profile-
        # scoped variant, not the bare slug.
        response = self.middleware(self._request(
            '/badges/remedy/',
            ua='meta-webindexer/1.1',
        ))
        self.assertEqual(response.status_code, 200)


@override_settings(DEBUG=False)
class CloudflareOriginGuardMiddlewareTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.middleware = CloudflareOriginGuardMiddleware(_passthrough)

    def _request(self, path, cf_ray=None, qs=''):
        full_path = f'{path}?{qs}' if qs else path
        headers = {}
        if cf_ray is not None:
            headers['HTTP_CF_RAY'] = cf_ray
        return self.factory.get(full_path, **headers)

    def test_guarded_path_without_cf_ray_redirects_to_public_origin(self):
        response = self.middleware(self._request(
            '/games/NPWR00352_00/deviousmeister/',
        ))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response['Location'],
            'https://platpursuit.com/games/NPWR00352_00/deviousmeister/',
        )

    def test_guarded_path_with_cf_ray_passes_through(self):
        response = self.middleware(self._request(
            '/games/NPWR00352_00/deviousmeister/',
            cf_ray='9f16cd984feb2732-EWR',
        ))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b'ok')

    def test_guarded_badge_path_without_cf_ray_preserves_query_string(self):
        response = self.middleware(self._request(
            '/my-pursuit/badges/remedy/deviousmeister/',
            qs='tier=3',
        ))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response['Location'],
            'https://platpursuit.com/my-pursuit/badges/remedy/deviousmeister/?tier=3',
        )

    def test_legacy_badge_prefix_without_cf_ray_is_guarded(self):
        response = self.middleware(self._request(
            '/badges/remedy/deviousmeister/',
        ))
        self.assertEqual(response.status_code, 302)

    def test_canonical_paths_without_cf_ray_pass_through(self):
        # Non-profile-scoped paths are intentionally unguarded so Render health
        # checks on `/` and other non-expensive routes still serve normally.
        for path in (
            '/',
            '/games/NPWR00352_00/',
            '/my-pursuit/badges/remedy/',
            '/community/profiles/someone/',
            '/accounts/login/',
        ):
            with self.subTest(path=path):
                response = self.middleware(self._request(path))
                self.assertEqual(response.status_code, 200, msg=path)

    def test_guard_logs_bypass_with_grep_friendly_tag(self):
        with self.assertLogs('plat_pursuit.middleware', level='INFO') as captured:
            self.middleware(self._request(
                '/games/NPWR00352_00/deviousmeister/',
            ))
        self.assertTrue(
            any('CF_BYPASS_BLOCKED' in msg for msg in captured.output),
            f'expected a CF_BYPASS_BLOCKED log line, got: {captured.output}',
        )

    @override_settings(DEBUG=True)
    def test_guard_is_disabled_when_debug_is_true(self):
        # Local dev requests never carry a CF-Ray header; if the guard fired
        # in DEBUG it would bounce every localhost hit to production.
        response = self.middleware(self._request(
            '/games/NPWR00352_00/deviousmeister/',
        ))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b'ok')


class MemoryDeltaMiddlewareTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.middleware = MemoryDeltaMiddleware(_passthrough)

    def _request(self, path='/'):
        return self.factory.get(path)

    def test_no_log_when_rss_unavailable(self):
        # On dev OSes (Windows/macOS) /proc/self/status doesn't exist, so
        # _read_rss_kb returns 0 and the middleware must be a no-op.
        with mock.patch('plat_pursuit.middleware._read_rss_kb', return_value=0):
            with self.assertNoLogs('plat_pursuit.middleware', level='INFO'):
                response = self.middleware(self._request())
        self.assertEqual(response.status_code, 200)

    def test_no_log_when_delta_below_threshold(self):
        # Simulate Linux RSS reads but with a tiny delta (5 MB).
        rss_values = iter([100_000, 105_000])
        with mock.patch(
            'plat_pursuit.middleware._read_rss_kb',
            side_effect=lambda: next(rss_values),
        ):
            with self.assertNoLogs('plat_pursuit.middleware', level='INFO'):
                self.middleware(self._request())

    def test_log_when_delta_above_threshold(self):
        # Simulate a 100 MB allocation between the before/after reads.
        # Start RSS is below the danger threshold (300 MB) so no
        # REQUEST_START_HOT line should fire — only the post-response
        # HEAVY_REQUEST line.
        rss_values = iter([100_000, 100_000 + 100 * 1024])  # +100 MB in KB
        with mock.patch(
            'plat_pursuit.middleware._read_rss_kb',
            side_effect=lambda: next(rss_values),
        ):
            with self.assertLogs('plat_pursuit.middleware', level='INFO') as captured:
                self.middleware(self._request('/games/NPWR00352_00/'))
        self.assertTrue(
            any('HEAVY_REQUEST' in msg for msg in captured.output),
            f'expected a HEAVY_REQUEST log line, got: {captured.output}',
        )
        self.assertTrue(
            any('path=/games/NPWR00352_00/' in msg for msg in captured.output),
            f'expected path in log, got: {captured.output}',
        )

    def test_request_start_hot_fires_when_worker_is_above_danger_rss(self):
        # Start above the danger threshold, grow by less than the heavy
        # threshold — should log only REQUEST_START_HOT.
        start_kb = (_DANGER_RSS_MB + 20) * 1024
        end_kb = start_kb + 5 * 1024
        rss_values = iter([start_kb, end_kb])
        with mock.patch(
            'plat_pursuit.middleware._read_rss_kb',
            side_effect=lambda: next(rss_values),
        ):
            with self.assertLogs('plat_pursuit.middleware', level='INFO') as captured:
                self.middleware(self._request('/some/path/'))
        self.assertTrue(
            any('REQUEST_START_HOT' in msg for msg in captured.output),
            f'expected REQUEST_START_HOT, got: {captured.output}',
        )
        self.assertFalse(
            any('HEAVY_REQUEST' in msg for msg in captured.output),
            f'unexpected HEAVY_REQUEST when delta is small: {captured.output}',
        )

    def test_request_start_hot_does_not_fire_below_danger_rss(self):
        # Start below danger, grow by less than the heavy threshold — no logs.
        start_kb = (_DANGER_RSS_MB - 50) * 1024
        end_kb = start_kb + 10 * 1024
        rss_values = iter([start_kb, end_kb])
        with mock.patch(
            'plat_pursuit.middleware._read_rss_kb',
            side_effect=lambda: next(rss_values),
        ):
            with self.assertNoLogs('plat_pursuit.middleware', level='INFO'):
                self.middleware(self._request('/some/path/'))
