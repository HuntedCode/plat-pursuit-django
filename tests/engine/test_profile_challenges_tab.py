"""The profile's Challenges tab (2026-10-09): a hunter's runs, without the browse page.

Same two rules as the Lists tab, for the same reasons:

  VISIBLE RUNS ONLY, including on your own profile. Hiding a run means "off my profile"; My Challenges is
  where an owner manages hidden ones.

  THE CHIP IS CONDITIONAL. It is offered only when there is a visible run behind it, and a hand-typed
  `?tab=challenges` on a hunter with none lands on Games instead of a tab with no chip.
"""
import re
from datetime import timedelta
from pathlib import Path

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from challenges.models import CHALLENGE_TYPE_AZ, CHALLENGE_TYPE_CALENDAR, CHALLENGE_TYPE_JOBS, Challenge
from challenges.services import challenge_service as svc
from tests.factories import ProfileFactory

pytestmark = pytest.mark.django_db

ROOT = Path(__file__).resolve().parents[2]
CF = {'HTTP_CF_RAY': '8f0000000000abcd-LHR'}
_TAB_BAR = re.compile(r'<div class="pp-switch".*?</div>', re.S)


def _hunter(psn='runner'):
    profile = ProfileFactory(is_linked=True, psn_username=psn)
    profile.user_is_premium = True
    profile.save(update_fields=['user_is_premium'])
    return profile


def _url(profile, tab=None):
    url = f'/hunters/{profile.psn_username}/'
    return f'{url}?tab={tab}' if tab else url


def _tab_bar(html):
    found = _TAB_BAR.search(html)
    return found.group(0) if found else ''


def _finish(run, when=None):
    Challenge.objects.filter(pk=run.pk).update(is_complete=True, completed_at=when or timezone.now(),
                                               completed_count=run.total_slots, filled_count=run.total_slots)


# ── the chip ────────────────────────────────────────────────────────────────────────────────────

def test_a_run_earns_the_chip(client):
    hunter = _hunter()
    svc.start(hunter, CHALLENGE_TYPE_AZ)
    assert 'tab=challenges' in _tab_bar(client.get(_url(hunter), **CF).content.decode())


def test_no_runs_means_no_chip(client):
    bar = _tab_bar(client.get(_url(_hunter()), **CF).content.decode())
    assert bar, 'the switcher did not render at all; this test would pass vacuously'
    assert 'tab=challenges' not in bar


def test_a_hidden_run_earns_no_chip_even_for_its_owner(client):
    hunter = _hunter()
    run = svc.start(hunter, CHALLENGE_TYPE_AZ)
    svc.hide(run, hunter)
    client.force_login(hunter.user)
    bar = _tab_bar(client.get(_url(hunter), **CF).content.decode())
    assert bar and 'tab=challenges' not in bar


def test_the_chip_carries_the_challenge_flag(client):
    hunter = _hunter()
    svc.start(hunter, CHALLENGE_TYPE_AZ)
    bar = _tab_bar(client.get(_url(hunter), **CF).content.decode())
    chip = bar[bar.index('id="profile-tab-challenges"'):]
    chip = chip[:chip.index('</a>')]
    assert '<path d="M4 15s1-1 4-1 5 2 8 2 4-1 4-1V3' in chip, 'the Challenges chip draws no icon'


def test_a_typed_tab_with_nothing_behind_it_lands_on_games(client):
    html = client.get(_url(_hunter(), 'challenges'), **CF).content.decode()
    assert 'id="challenges-grid"' not in html
    assert 'aria-selected="true"' in _tab_bar(html), 'no chip is selected, which reads as broken'


def test_a_stale_chip_pushes_the_tab_it_actually_rendered(client):
    """A chip that went stale (the last run hidden after the page drew it) lands on Games, and the pushed URL
    must say Games: the page script reads the tab from the URL to wire the grid."""
    hunter = _hunter()
    resp = client.get(_url(hunter, 'challenges'), HTTP_HX_REQUEST='true', HTTP_HX_TARGET='tab-content', **CF)
    assert resp.status_code == 200
    assert 'id="games-grid"' in resp.content.decode()
    assert resp['HX-Push-Url'] == f'/hunters/{hunter.psn_username}/?tab=games'


def test_a_tab_that_renders_as_asked_pushes_nothing_extra(client):
    hunter = _hunter()
    svc.start(hunter, CHALLENGE_TYPE_AZ)
    resp = client.get(_url(hunter, 'challenges'), HTTP_HX_REQUEST='true', HTTP_HX_TARGET='tab-content', **CF)
    assert 'id="challenges-grid"' in resp.content.decode()
    assert 'HX-Push-Url' not in resp


def test_a_full_page_load_puts_the_rendered_tab_in_the_url(client):
    """The non-HTMX half of the stale-tab fix: the page rewrites `?tab=` to what the server rendered."""
    html = client.get(_url(_hunter(), 'challenges'), **CF).content.decode()
    assert "var renderedTab = 'games';" in html
    assert "history.replaceState(history.state, '', fixed.pathname + fixed.search + fixed.hash);" in html


# ── the tab ─────────────────────────────────────────────────────────────────────────────────────

def _tab(client, hunter):
    html = client.get(_url(hunter, 'challenges'), **CF).content.decode()
    start = html.index('id="challenges-grid"')
    end = html.find('<footer', start)
    return html[start:end if end != -1 else len(html)]


def test_runs_in_progress_come_first_then_finished(client):
    hunter = _hunter()
    done = svc.start(hunter, CHALLENGE_TYPE_AZ)
    _finish(done)
    live = svc.start(hunter, CHALLENGE_TYPE_JOBS)
    tab = _tab(client, hunter)

    live_at = tab.index(reverse('challenge_detail', args=[live.id]))
    done_at = tab.index(reverse('challenge_detail', args=[done.id]))
    assert tab.index('>In progress<') < live_at < tab.index('>Finished<') < done_at
    when = Challenge.objects.get(pk=done.pk).completed_at
    assert f'Finished <time datetime="{when.isoformat()}">' in tab[done_at:], 'the finished card shows no finish date'


def test_runs_in_progress_follow_the_types_own_order(client):
    """A-Z, Job Coverage, Calendar, as My Challenges offers them. Started in an order that matches neither
    oldest-first, newest-first nor the type keys' alphabetical order (az, calendar, jobs), so none of those
    sorts can pass for this one."""
    hunter = _hunter()
    jobs = svc.start(hunter, CHALLENGE_TYPE_JOBS)
    az = svc.start(hunter, CHALLENGE_TYPE_AZ)
    calendar = svc.start(hunter, CHALLENGE_TYPE_CALENDAR)
    tab = _tab(client, hunter)
    at = [tab.index(reverse('challenge_detail', args=[run.id])) for run in (az, jobs, calendar)]
    assert at == sorted(at)


def test_finished_runs_come_newest_first_and_stop_at_the_limit(client, monkeypatch):
    from trophies.views.profile_views import ProfileDetailView

    hunter = _hunter()
    older = svc.start(hunter, CHALLENGE_TYPE_AZ)
    _finish(older, timezone.now() - timedelta(days=3))
    newer = svc.start(hunter, CHALLENGE_TYPE_AZ)
    _finish(newer)

    tab = _tab(client, hunter)
    assert tab.index(reverse('challenge_detail', args=[newer.id])) < tab.index(reverse('challenge_detail', args=[older.id]))

    monkeypatch.setattr(ProfileDetailView, 'CHALLENGES_TAB_FINISHED_LIMIT', 1)
    tab = _tab(client, hunter)
    assert reverse('challenge_detail', args=[newer.id]) in tab
    assert reverse('challenge_detail', args=[older.id]) not in tab


def test_the_cards_leave_out_the_hunter_on_their_own_profile(client):
    hunter = _hunter()
    svc.start(hunter, CHALLENGE_TYPE_AZ)
    assert 'pp-crun__hunter' not in _tab(client, hunter)


def test_a_hidden_run_never_appears_on_the_tab(client):
    hunter = _hunter()
    shown = svc.start(hunter, CHALLENGE_TYPE_AZ)
    hidden = svc.start(hunter, CHALLENGE_TYPE_JOBS)
    svc.hide(hidden, hunter)
    tab = _tab(client, hunter)
    assert reverse('challenge_detail', args=[shown.id]) in tab
    assert reverse('challenge_detail', args=[hidden.id]) not in tab


def test_a_private_profile_serves_no_tab_even_over_htmx(client):
    """An HTMX request renders the tab template directly, which never reaches the page's privacy guard."""
    hunter = _hunter()
    svc.start(hunter, CHALLENGE_TYPE_AZ)
    hunter.psn_history_public = False
    hunter.save(update_fields=['psn_history_public'])
    resp = client.get(_url(hunter, 'challenges'), HTTP_HX_REQUEST='true', **CF)
    assert resp.status_code == 200
    assert reverse('challenge_detail', args=[Challenge.objects.get(profile=hunter).id]) not in resp.content.decode()


def test_the_tab_costs_the_same_however_many_runs(client):
    """The same number of queries however many runs are on the tab: nothing is read per card."""
    one = _hunter('one')
    svc.start(one, CHALLENGE_TYPE_AZ)
    many = _hunter('many')
    for kind in (CHALLENGE_TYPE_AZ, CHALLENGE_TYPE_JOBS, CHALLENGE_TYPE_CALENDAR):
        _finish(svc.start(many, kind)) if kind != CHALLENGE_TYPE_CALENDAR else svc.start(many, kind)
    svc.start(many, CHALLENGE_TYPE_AZ)

    def cost(profile):
        with CaptureQueriesContext(connection) as ctx:
            client.get(_url(profile, 'challenges'), HTTP_HX_REQUEST='true', **CF)
        return len(ctx.captured_queries)

    assert cost(one) == cost(many)


def test_the_finished_list_is_bounded():
    from trophies.views.profile_views import ProfileDetailView

    assert ProfileDetailView.CHALLENGES_TAB_FINISHED_LIMIT == 24


def test_the_page_reveals_the_tab_on_the_run_card():
    page = (ROOT / 'templates' / 'trophies' / 'profile_detail.html').read_text(encoding='utf-8')
    assert "else if (tab === 'challenges') { cardSel = revealSel = '.pp-crun'; }" in page
