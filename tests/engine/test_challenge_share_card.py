"""The challenge share card: what it draws, who may have it, and where the button is.

THREE THINGS THIS FILE IS FOR.

**The squares in the card mean what the board means.** A share card is the run's public face, so a square
that reads "done" on the card must be done on the page: done, assigned and open come from the same slot
fields the board reads, and the alphabet is split A-M over N-Z because that split is the layout.

**Owner only, and never a redirect.** The endpoints are fetched by the share dialog, so a refusal must be a
JSON status (the project's "JSON endpoints must never redirect" lesson) and somebody else's run, a hidden
run, or a type with no card yet must be indistinguishable 404s.

**Images are only cached for the download.** The preview hands the browser remote URLs; caching 27 images
on modal open is the cold-cache cost the plat card's `_art_path` was written to avoid. Pinned by counting
calls to the cache, not by reading source.
"""
import datetime
import json
from unittest import mock

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from challenges.models import CHALLENGE_TYPE_AZ, CHALLENGE_TYPE_JOBS
from challenges.services import challenge_service as svc
from challenges.services import share_card
from tests.factories import ConceptFactory, GameFactory, IGDBMatchFactory, ProfileFactory, UserFactory
from trophies.models import Contract

pytestmark = pytest.mark.django_db

_SEQ = {'n': 0}


def _hunter(client=None):
    user = UserFactory()
    profile = ProfileFactory(user=user, user_is_premium=True)
    if client is not None:
        client.force_login(user)
    return profile


def _contract(name, image=None):
    """A live contract with a real member concept, so the square has a cover to resolve."""
    _SEQ['n'] += 1
    contract = Contract.objects.create(
        name=name, slug=f"{name.lower().replace(' ', '-')}-{_SEQ['n']}",
        is_live=True, igdb_id=820_000 + _SEQ['n'])
    concept = ConceptFactory(anchor_migration_completed_at=timezone.now())
    IGDBMatchFactory(concept=concept, igdb_id=contract.igdb_id, status='accepted')
    # A real URL, so a square's cover is something the cache can be asked for. A factory game has none.
    GameFactory(concept=concept, title_image=image or f"https://img.example/cover-{_SEQ['n']}.png")
    return contract


def _az_run(profile):
    return svc.start(profile, CHALLENGE_TYPE_AZ)


def _squares(context):
    return [sq for row in context['rows'] for sq in row]


def _html_url(run):
    return reverse('challenge_card_html', args=[run.id])


def _png_url(run):
    return reverse('challenge_card_png', args=[run.id])


# ── what the card draws ──────────────────────────────────────────────────────────────────────────────

def test_the_alphabet_splits_a_to_m_over_n_to_z():
    """THE LAYOUT IS THE SPLIT. Thirteen and thirteen is what makes it read as the alphabet; a builder that
    halved the list any other way would still render 26 squares and look plausible."""
    context = share_card.build_card_context(_az_run(_hunter()))
    top, bottom = context['rows']
    assert [sq['key'] for sq in top] == list('ABCDEFGHIJKLM')
    assert [sq['key'] for sq in bottom] == list('NOPQRSTUVWXYZ')


def test_square_states_follow_the_slots():
    """Done, assigned and open, read from the same fields the board reads -- and a cover only where a game
    is, so an open square never borrows art."""
    profile = _hunter()
    run = _az_run(profile)
    svc.mark_slot_completed(svc.assign(run, profile, 'A', _contract('Astro Bot')))
    svc.assign(run, profile, 'B', _contract('Bloodborne'))
    run.refresh_from_db()

    by_key = {sq['key']: sq for sq in _squares(share_card.build_card_context(run))}
    assert by_key['A']['state'] == share_card.DONE
    assert by_key['B']['state'] == share_card.ASSIGNED
    assert by_key['C']['state'] == share_card.OPEN
    assert by_key['A']['cover'] and by_key['B']['cover']
    assert by_key['C']['cover'] == ''


def test_a_live_run_counts_what_is_left_and_shows_no_title():
    profile = _hunter()
    run = _az_run(profile)
    svc.mark_slot_completed(svc.assign(run, profile, 'A', _contract('Astro Bot')))
    svc.assign(run, profile, 'B', _contract('Bloodborne'))
    run.refresh_from_db()

    context = share_card.build_card_context(run)
    assert context['is_complete'] is False
    assert context['completed_count'] == 1
    assert context['assigned_count'] == 1
    assert context['letters_left'] == 25
    assert context['title'] is None


def test_a_finished_run_shows_the_title_it_earned():
    """READ FROM THE GRANT, not computed from the run's ordinal, so the card shows the title exactly when
    the hunter holds it -- the same rule `granted_titles_for` gives the run's own page."""
    profile = _hunter()
    run = _az_run(profile)
    for letter in 'ABCDEFGHIJKLMNOPQRSTUVWXYZ':
        svc.mark_slot_completed(svc.assign(run, profile, letter, _contract(f'{letter} Game')))
    run.refresh_from_db()
    assert run.is_complete

    context = share_card.build_card_context(run)
    assert context['title'] == 'A-Z Champion'
    assert all(sq['state'] == share_card.DONE for sq in _squares(context))


def test_days_count_inclusively():
    """A run started and finished on the same day took one day, not zero -- the way a hunter would say it."""
    run = _az_run(_hunter())
    run.completed_at = run.created_at
    assert share_card._days(run) == 1
    run.completed_at = run.created_at + datetime.timedelta(days=9)
    assert share_card._days(run) == 10


def test_a_live_run_counts_to_now():
    run = _az_run(_hunter())
    assert share_card._days(run, now=run.created_at + datetime.timedelta(days=4)) == 5


def test_days_are_the_hunters_days_not_utc():
    """The card prints its date in the hunter's zone, so the count must use the same days. 18:00 PDT on
    Oct 1 is 01:00 UTC on Oct 2: counted in UTC, a run to 08:00 PDT Oct 2 was ONE day beside "Started
    Oct 1"; the hunter counts two."""
    from zoneinfo import ZoneInfo
    run = _az_run(_hunter())
    run.created_at = datetime.datetime(2026, 10, 2, 1, 0, tzinfo=datetime.timezone.utc)
    run.completed_at = datetime.datetime(2026, 10, 2, 15, 0, tzinfo=datetime.timezone.utc)
    with timezone.override(ZoneInfo('America/Los_Angeles')):
        assert share_card._days(run) == 2
    with timezone.override(ZoneInfo('UTC')):
        assert share_card._days(run) == 1


def test_the_preview_never_touches_the_image_cache():
    """THE PREVIEW HANDS THE BROWSER REMOTE URLS. Caching here would turn opening the dialog into up to 27
    synchronous downloads on a cold cache."""
    profile = _hunter()
    run = _az_run(profile)
    svc.assign(run, profile, 'A', _contract('Astro Bot'))
    with mock.patch('challenges.services.share_card.ShareImageCache.fetch_and_cache') as fetch:
        share_card.build_card_context(run)
    fetch.assert_not_called()


def test_the_download_caches_every_image_once_and_survives_a_failure():
    """Each distinct URL is fetched ONCE (two squares can share a cover), and one that fails comes back
    empty so the square falls to its no-art state rather than breaking the render."""
    profile = _hunter()
    profile.avatar_url = 'https://img.example/avatar.png'
    profile.save(update_fields=['avatar_url'])
    run = _az_run(profile)
    svc.assign(run, profile, 'A', _contract('Astro Bot'))
    svc.assign(run, profile, 'B', _contract('Bloodborne'))
    # Two squares on ONE image: regional siblings and re-releases share cover art often enough.
    shared = 'https://img.example/shared.png'
    svc.assign(run, profile, 'D', _contract('Dead Cells', image=shared))
    svc.assign(run, profile, 'E', _contract('Elden Ring', image=shared))
    run.refresh_from_db()

    preview = share_card.build_card_context(run)
    assert {sq['cover'] for sq in _squares(preview) if sq['key'] in 'DE'} == {shared}
    by_key = {sq['key']: sq for sq in _squares(preview)}
    failing = by_key['B']['cover']

    def fake(url):
        return '' if url == failing else '/api/v1/share-temp/' + str(abs(hash(url))) + '.png'

    with mock.patch('challenges.services.share_card.ShareImageCache.fetch_and_cache',
                    side_effect=fake) as fetch:
        context = share_card.build_card_context(run, cache_images=True)

    fetched = [c.args[0] for c in fetch.call_args_list]
    assert len(fetched) == len(set(fetched)), 'a URL was fetched more than once'
    assert shared in fetched
    assert 'https://img.example/avatar.png' in fetched
    assert context['avatar_image'].startswith('/api/v1/share-temp/')
    cached = {sq['key']: sq for sq in _squares(context)}
    assert cached['A']['cover'].startswith('/api/v1/share-temp/')
    assert cached['B']['cover'] == ''
    assert cached['C']['cover'] == ''
    assert cached['D']['cover'] == cached['E']['cover'] != '', 'a shared image must land on both squares'


def test_the_card_renders_all_three_states():
    """The template branches on the state STRINGS the builder emits, so a renamed state renders the wrong
    markup without failing anything else: an unknown state falls into the filled branch and silently loses
    the assigned square's dimming."""
    from django.template.loader import render_to_string
    profile = _hunter()
    run = _az_run(profile)
    svc.mark_slot_completed(svc.assign(run, profile, 'A', _contract('Astro Bot')))
    run.refresh_from_db()

    html = render_to_string(share_card.CARD_TEMPLATE, share_card.build_card_context(run))
    assert 'share-image-content' in html
    assert 'In progress' in html and 'Complete<' not in html
    assert '1 letter down, 25 to go.' in html
    assert 'dashed' in html, 'the open well is gone'
    assert '#3add9e' in html, 'the done check is gone'
    assert 'opacity: 0.34' not in html, 'a square with nothing assigned was dimmed'

    svc.assign(run, profile, 'B', _contract('Bloodborne'))
    run.refresh_from_db()
    html = render_to_string(share_card.CARD_TEMPLATE, share_card.build_card_context(run))
    assert html.count('opacity: 0.34') == 1, 'the assigned square is not dimmed'
    assert '1</span> game in progress' in html


def test_the_card_costs_the_same_however_full_the_run():
    """26 squares, and a cover lookup per square would be the May 2026 OOM shape again. The count must not
    move with the number of filled squares."""
    from django.db import connection
    from django.test.utils import CaptureQueriesContext
    profile = _hunter()
    run = _az_run(profile)
    for letter in 'AB':
        svc.assign(run, profile, letter, _contract(f'{letter} Game'))
    run.refresh_from_db()
    with CaptureQueriesContext(connection) as few:
        share_card.build_card_context(run)
    for letter in 'CDEFGHIJ':
        svc.assign(run, profile, letter, _contract(f'{letter} Game'))
    run.refresh_from_db()
    with CaptureQueriesContext(connection) as many:
        share_card.build_card_context(run)
    assert len(many) == len(few), [q['sql'][:120] for q in many.captured_queries]


def test_filenames_are_header_safe():
    """A Content-Disposition value with a quote or a non-ASCII character in it breaks the download in
    some browsers; PSN names can carry either."""
    profile = _hunter()
    profile.display_psn_username = 'Ōkami "Fan"'
    profile.save(update_fields=['display_psn_username'])
    name = share_card.filename_for(_az_run(profile))
    assert name.endswith('.png')
    assert name.isascii()
    assert '"' not in name and ' ' not in name and '--' not in name
    assert 'A-Z-Challenge' in name


def test_a_name_that_sanitizes_to_nothing_still_names_the_file():
    run = _az_run(_hunter())
    run.profile.display_psn_username = '\u014c\u014c'
    run.name = '\u2605'
    assert share_card.filename_for(run) == 'challenge-card.png'


def test_ground_themes_are_the_eight_designed_grounds():
    """A run has 26 games, so there is no single image to back it: an art ground would silently render
    as a gradient. Named explicitly rather than re-derived, so the test cannot agree with a broken filter."""
    from trophies.themes import get_ground_themes
    assert [k for k, _ in get_ground_themes()] == [
        'ppSubstrate', 'ppMidnight', 'ppEmber', 'ppAurora', 'ppFog', 'ppTide', 'ppClay', 'retroWave']


@pytest.mark.parametrize('rel', ['trophies/recap_views.py', 'trophies/views/profile_views.py'])
def test_the_sibling_pickers_share_the_filter(rel):
    """One palette across the three cards: the recap and the Profile Card read the same helper, rather
    than each keeping its own copy of the art-ground filter."""
    from pathlib import Path
    src = (Path(__file__).resolve().parents[2] / rel).read_text(encoding='utf-8')
    assert 'get_ground_themes()' in src
    assert "if not t.get('is_game_art')" not in src


# ── who may have it ──────────────────────────────────────────────────────────────────────────────────

def test_the_owner_gets_the_preview_and_a_filename():
    client = Client()
    run = _az_run(_hunter(client))
    resp = client.get(_html_url(run))
    assert resp.status_code == 200
    data = json.loads(resp.content)
    assert 'A&ndash;Z Challenge' in data['html']
    assert data['filename'].endswith('.png')


@pytest.mark.parametrize('url', [_html_url, _png_url])
def test_somebody_elses_run_is_a_404(url):
    client = Client()
    _hunter(client)
    run = _az_run(_hunter())
    resp = client.get(url(run))
    assert resp.status_code == 404
    assert resp['Content-Type'].startswith('application/json')


@pytest.mark.parametrize('url', [_html_url, _png_url])
def test_signed_out_is_json_never_a_redirect(url):
    """`fetch` follows a 302 to the login page and reads a 200 -- the dialog would then paint the login
    page's HTML as a card."""
    run = _az_run(_hunter())
    resp = Client().get(url(run))
    assert resp.status_code in (401, 403)
    assert resp['Content-Type'].startswith('application/json')


@pytest.mark.parametrize('url', [_html_url, _png_url])
def test_a_hidden_run_has_no_card(url):
    """Hiding takes a run out of every public place, and a share card is the most public place there is."""
    client = Client()
    profile = _hunter(client)
    run = _az_run(profile)
    svc.hide(run, profile)
    assert client.get(url(run)).status_code == 404


def test_a_type_without_a_card_is_a_404():
    """Cards ship one type at a time, so Job Coverage has none yet and must not inherit A-Z's layout."""
    client = Client()
    profile = _hunter(client)
    run = svc.start(profile, CHALLENGE_TYPE_JOBS)
    assert client.get(_html_url(run)).status_code == 404
    assert client.get(_png_url(run)).status_code == 404


# ── the download ─────────────────────────────────────────────────────────────────────────────────────

def _render(client, run, query=''):
    with mock.patch('core.services.playwright_renderer.render_png', return_value=b'PNGBYTES') as render, \
            mock.patch('challenges.services.share_card.ShareImageCache.fetch_and_cache', return_value=''):
        resp = client.get(_png_url(run) + query)
    return resp, render


def test_the_download_is_a_named_png_attachment():
    client = Client()
    run = _az_run(_hunter(client))
    resp, _ = _render(client, run)
    assert resp.status_code == 200
    assert resp['Content-Type'] == 'image/png'
    assert resp.content == b'PNGBYTES'
    assert resp['Content-Disposition'] == f'attachment; filename="{share_card.filename_for(run)}"'


def test_a_named_ground_reaches_the_renderer():
    client = Client()
    run = _az_run(_hunter(client))
    _, render = _render(client, run, '?theme=ppEmber')
    assert render.call_args.kwargs['theme_key'] == 'ppEmber'


@pytest.mark.parametrize('theme', ['', '?theme=nope', '?theme=ppArt'])
def test_an_unknown_or_art_ground_falls_back_to_the_house_ground(theme):
    """Only the designed grounds render: an arbitrary site gradient, or an art ground with no art to
    composite, falls back to Substrate rather than erroring a download."""
    client = Client()
    run = _az_run(_hunter(client))
    _, render = _render(client, run, theme)
    assert render.call_args.kwargs['theme_key'] == 'ppSubstrate'


def test_head_requests_spend_the_download_budget():
    """`View.setup` aliases `head` to the WRAPPED `get`, so a limiter on `method='GET'` alone leaves `curl -I`
    rendering PNGs unmetered. Measured by behaviour, because a source-text pin cannot prove what a decorator
    counts."""
    client = Client()
    run = _az_run(_hunter(client))
    with mock.patch('core.services.playwright_renderer.render_png', return_value=b'PNG'), \
            mock.patch('challenges.services.share_card.ShareImageCache.fetch_and_cache', return_value=''):
        statuses = [client.head(_png_url(run)).status_code for _ in range(21)]
    assert statuses[:20] == [200] * 20
    assert statuses[20] == 403, 'the 21st HEAD in a minute was not refused'


def test_a_render_failure_is_a_json_500():
    """CardDownload shows the error line on a non-ok status; a raised exception must not become an HTML
    500 page the dialog cannot read."""
    client = Client()
    run = _az_run(_hunter(client))
    with mock.patch('core.services.playwright_renderer.render_png', side_effect=RuntimeError('boom')), \
            mock.patch('challenges.services.share_card.ShareImageCache.fetch_and_cache', return_value=''):
        resp = client.get(_png_url(run))
    assert resp.status_code == 500
    assert 'error' in json.loads(resp.content)


# ── where the button is ──────────────────────────────────────────────────────────────────────────────

def _detail(client, run):
    return client.get(reverse('challenge_detail', args=[run.id])).content.decode()


def test_the_owner_sees_share_on_their_run_page():
    client = Client()
    run = _az_run(_hunter(client))
    body = _detail(client, run)
    assert f'data-html-url="{_html_url(run)}"' in body
    assert 'id="cc-share"' in body
    assert 'js/challenge-share' in body


def test_a_visitor_sees_no_share_and_no_dialog():
    run = _az_run(_hunter())
    client = Client()
    _hunter(client)
    body = _detail(client, run)
    assert 'data-challenge-share' not in body
    assert 'id="cc-share"' not in body
    assert 'js/challenge-share' not in body


def test_a_job_coverage_run_page_offers_no_share_yet():
    client = Client()
    run = svc.start(_hunter(client), CHALLENGE_TYPE_JOBS)
    assert 'data-challenge-share' not in _detail(client, run)


def test_my_challenges_offers_share_on_the_live_az_run_only():
    """The live A-Z card carries Share; the Job Coverage card does not, because its card is not built."""
    client = Client()
    profile = _hunter(client)
    az = _az_run(profile)
    jobs = svc.start(profile, CHALLENGE_TYPE_JOBS)
    body = client.get(reverse('my_challenges')).content.decode()
    assert f'data-html-url="{_html_url(az)}"' in body
    assert _html_url(jobs) not in body
    assert body.count('id="cc-share"') == 1


def test_my_challenges_offers_share_on_a_finished_az_run():
    client = Client()
    profile = _hunter(client)
    run = _az_run(profile)
    for letter in 'ABCDEFGHIJKLMNOPQRSTUVWXYZ':
        svc.mark_slot_completed(svc.assign(run, profile, letter, _contract(f'{letter} Game')))
    body = client.get(reverse('my_challenges')).content.decode()
    # Scoped to the finished list itself: the live card above it carries no share for a finished run.
    finished = body[body.index('<ul class="mt-2 divide-y'):]
    assert f'data-html-url="{_html_url(run)}"' in finished
    assert 'pp-cshare--compact' in finished
