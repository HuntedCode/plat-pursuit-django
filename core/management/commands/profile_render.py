"""
Profile memory allocation during a single page render.

Hits a URL via Django's test client, takes tracemalloc snapshots before
and after the measured render, and prints the top Python source lines
responsible for memory growth. Use this locally (DEBUG=True) to find
where a request's heap allocations are coming from without shipping
instrumentation to production.

Usage:
    python manage.py profile_render /games/NPWR09337_00/
    python manage.py profile_render /companies/maximum-entertainment/ --user my_username
    python manage.py profile_render /community/reviews/leisure-suit-larry-box-office-bust-ps3/
    python manage.py profile_render /games/NPWR09337_00/ --top 50
    python manage.py profile_render /games/NPWR09337_00/ --no-warmup

Notes:
- By default a warm-up render runs first so module imports, lazy template
  loads, and one-time signal/regex compilation don't show up in the diff.
  This isolates *steady-state* per-request allocations.
- The warm-up also populates Redis caches (image URLs, game stats, etc.),
  so the measured render sees a warm cache. If the OOMs you're chasing
  happen on cache misses (rapid-fire requests against different games),
  pass --no-warmup to measure the cold-cache path instead.
- Run both ways and compare. If --no-warmup is dramatically larger, the
  leak is in the cold-cache build branches. If they're similar, the leak
  is on the always-runs path.
- DB query count requires either DEBUG=True or the force_debug_cursor flag,
  which this command toggles for you.
- The test client bypasses gunicorn but exercises the full Django request
  cycle including middleware, view, template render, and context
  processors, so allocation patterns match a real request closely.
"""
import linecache
import tracemalloc

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import connection
from django.test import Client


def _resolve_default_host():
    """Pick a concrete host from ALLOWED_HOSTS so the test client doesn't
    get rejected by SecurityMiddleware in production (where 'testserver'
    is not in ALLOWED_HOSTS). Skips wildcard entries like '.example.com'."""
    allowed = list(getattr(settings, 'ALLOWED_HOSTS', []) or [])
    if '*' in allowed:
        return 'localhost'
    for host in allowed:
        if host and not host.startswith('.'):
            return host
    return 'localhost'


class Command(BaseCommand):
    help = "Profile memory allocation during a single page render."

    def add_arguments(self, parser):
        parser.add_argument(
            'url',
            help='URL path to render, e.g. /games/NPWR09337_00/',
        )
        parser.add_argument(
            '--user',
            help='Username to authenticate as (default: anonymous).',
        )
        parser.add_argument(
            '--top',
            type=int,
            default=30,
            help='Number of top allocation sites to print (default: 30).',
        )
        parser.add_argument(
            '--frames',
            type=int,
            default=4,
            help='Stack frames to show per allocation site (default: 4).',
        )
        parser.add_argument(
            '--no-warmup',
            action='store_true',
            help=(
                'Skip the warm-up render. Useful for measuring cold-cache '
                'allocation patterns (matches the prod OOM scenario where '
                'rapid-fire requests hit different games with empty caches).'
            ),
        )
        parser.add_argument(
            '--host',
            default=None,
            help=(
                'Host header to send with the test request. Defaults to the '
                'first concrete entry in settings.ALLOWED_HOSTS so the '
                'request passes SecurityMiddleware in production.'
            ),
        )

    def handle(self, *args, **options):
        url = options['url']
        username = options.get('user')
        top = options['top']
        frames_to_show = options['frames']
        skip_warmup = options['no_warmup']
        host = options.get('host') or _resolve_default_host()

        client = Client()
        if username:
            User = get_user_model()
            try:
                user = User.objects.get(username=username)
            except User.DoesNotExist:
                self.stderr.write(self.style.ERROR(f'User {username!r} not found.'))
                return
            client.force_login(user)

        connection.force_debug_cursor = True

        if not skip_warmup:
            # Warm-up render so import / lazy-load cost doesn't pollute the
            # diff. Note: this also populates Redis caches that the measured
            # render will read instead of rebuilding. Pass --no-warmup to
            # measure the cold-cache path instead.
            warm = client.get(url, HTTP_HOST=host)
            if warm.status_code >= 400:
                self.stderr.write(self.style.ERROR(
                    f'Warm-up returned status {warm.status_code} for {url}'
                ))
                return

        connection.queries_log.clear()

        tracemalloc.start(25)
        snap_before = tracemalloc.take_snapshot()
        response = client.get(url, HTTP_HOST=host)
        snap_after = tracemalloc.take_snapshot()
        tracemalloc.stop()

        if response.status_code >= 400:
            self.stderr.write(self.style.ERROR(
                f'Render returned status {response.status_code}'
            ))
            return

        diff = snap_after.compare_to(snap_before, 'lineno')
        total_diff_mb = sum(s.size_diff for s in diff) / 1024 / 1024
        n_queries = len(connection.queries)
        response_size = len(response.content)

        bar = '=' * 78
        sub = '-' * 78
        self.stdout.write(bar)
        self.stdout.write(f'URL:           {url}')
        self.stdout.write(f'Host:          {host}')
        self.stdout.write(f'User:          {username or "anonymous"}')
        self.stdout.write(f'Warm-up:       {"skipped (cold-cache mode)" if skip_warmup else "ran (warm-cache mode)"}')
        self.stdout.write(f'Status:        {response.status_code}')
        self.stdout.write(f'Response size: {response_size:,} bytes ({response_size/1024:.1f} KB)')
        self.stdout.write(f'DB queries:    {n_queries}')
        self.stdout.write(f'Net allocated: {total_diff_mb:.2f} MB')
        self.stdout.write(bar)
        self.stdout.write(f'Top {top} allocation sites by size_diff (lineno-aggregated):')
        self.stdout.write(sub)

        # Drop tracemalloc-internal noise and the test client harness so the
        # signal stays focused on app code and Django internals.
        def keep(stat):
            fn = stat.traceback[0].filename
            noise = (
                'tracemalloc',
                'site-packages\\tracemalloc',
                'django\\test\\client.py',
                'django/test/client.py',
            )
            return not any(n in fn for n in noise)

        kept = [s for s in diff if keep(s)]

        for i, stat in enumerate(kept[:top], 1):
            top_frame = stat.traceback[0]
            size_mb = stat.size_diff / 1024 / 1024
            self.stdout.write(
                f'#{i:2d}  {size_mb:+8.3f} MB  '
                f'(blocks {stat.count_diff:+7d})  '
                f'{top_frame.filename}:{top_frame.lineno}'
            )
            for frame in stat.traceback[:frames_to_show]:
                src = linecache.getline(frame.filename, frame.lineno).strip()
                if src:
                    self.stdout.write(f'      {frame.filename}:{frame.lineno}')
                    self.stdout.write(f'          {src[:120]}')

        self.stdout.write(bar)
        self.stdout.write(
            'Send the entire output above to your collaborator. The lines '
            'starting with "#NN" identify which Python source line allocated '
            'the most memory during the measured render.'
        )
