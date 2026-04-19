from django.http import HttpResponse
from django.test import RequestFactory, SimpleTestCase

from plat_pursuit.middleware import BotCanonicalRedirectMiddleware


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
        ]:
            with self.subTest(ua=ua):
                response = self.middleware(self._request(
                    '/games/NPWR00352_00/someuser/',
                    ua=ua,
                ))
                self.assertEqual(response.status_code, 301, msg=ua)
                self.assertEqual(response['Location'], '/games/NPWR00352_00/')
