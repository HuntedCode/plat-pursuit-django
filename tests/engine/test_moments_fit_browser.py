"""The celebration player never scrolls, in a real browser against the BUILT output.css (owner, 2026-10-25).

A set moment (two editions side by side) gave the dialog a vertical scrollbar: its rays were sized from the
whole row, so they hung below the dialog, and they SPIN, so a turning square's corners reach 1.41x as far at 45
degrees and the scrollbar came and went as they turned. The rays are sized from one medallion now, and the spin
is an inner layer their box clips. This renders the real overlay markup, parks the spin at its widest and asks
the dialog whether it can scroll, on a short laptop and a phone. Skips where Playwright's browser is missing.
"""
from datetime import timedelta
from pathlib import Path

import pytest
from django.template import Context, Template
from django.utils import timezone

from tests.engine.test_job_tiles_fit_browser import _SKIP_REASON
from tests.factories import BadgeSeriesFactory, GroupBadgeFactory, PlatformGroupFactory, ProfileFactory
from trophies.models import Moment, SeriesAward, UserGroupBadge
from trophies.services import moment_service

ROOT = Path(__file__).resolve().parents[2]
CSS = ROOT / 'static' / 'css' / 'output.css'

pytestmark = [pytest.mark.django_db, pytest.mark.skipif(_SKIP_REASON is not None, reason=_SKIP_REASON or '')]

# Show the player with its one moment in the stage (what moments.js does), the spin parked at 45 degrees and every
# other animation at its end state, then measure.
MEASURE = """() => {
  const root = document.getElementById('pp-moments'); root.hidden = false;
  const t = document.querySelector('template[data-moment]');
  document.querySelector('[data-moments-stage]').appendChild(t.content.cloneNode(true));
  for (const a of document.getAnimations()) {
    if (a.animationName === 'ppRaysSpin') { a.pause(); a.currentTime = 6800; }   // 0.8s delay + an eighth of 48s
    else { try { a.finish(); } catch (e) {} }
  }
  const d = root.querySelector('.pp-moments__dialog');
  return d.scrollHeight - d.clientHeight;
}"""


def _overlay(rf, kind):
    profile = ProfileFactory(moments_since=timezone.now() - timedelta(days=30), is_linked=True)
    series = BadgeSeriesFactory()
    for key, name, platforms in (('ultra-hd', 'Ultra HD', ['PS5']), ('legacy-hd', 'Legacy HD', ['PS3'])):
        gb = GroupBadgeFactory(series=series, is_live=True,
                               platform_group=PlatformGroupFactory(key=key, name=name, platforms=platforms))
        UserGroupBadge.objects.create(profile=profile, group_badge=gb, earned_at=timezone.now(), is_holo=True,
                                      mastered_at=timezone.now(), is_linked=True)
    SeriesAward.objects.create(profile=profile, series=series, kind=kind, since=timezone.now())
    moment_service.record_series_reward(profile, series, kind, timezone.now() - timedelta(hours=1))
    request = rf.get('/career/')
    request.user = profile.user
    return Template('{% load moments %}{% moments_overlay %}').render(Context({'request': request}))


@pytest.mark.parametrize('kind', [Moment.KIND_FULL_SET, Moment.KIND_SERIES_MASTER])
@pytest.mark.parametrize('viewport', [(1280, 630), (390, 760)])
def test_a_set_moment_never_gives_the_player_a_scrollbar(rf, tmp_path, kind, viewport):
    from playwright.sync_api import sync_playwright

    html = _overlay(rf, kind)
    assert 'pp-moment__set' in html
    page = tmp_path / 'moments.html'
    page.write_text(
        '<!doctype html><html data-theme="plat-pursuit-dark"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f'<link rel="stylesheet" href="{CSS.as_uri()}"></head><body class="bg-base-100">{html}</body></html>',
        encoding='utf-8')
    with sync_playwright() as p:
        b = p.chromium.launch()
        try:
            pg = b.new_page(viewport={'width': viewport[0], 'height': viewport[1]})
            pg.goto(page.as_uri())
            overflow = pg.evaluate(MEASURE)
        finally:
            b.close()
    assert overflow <= 0, f'the player scrolls by {overflow}px at {viewport}: the rays outgrow the dialog'
