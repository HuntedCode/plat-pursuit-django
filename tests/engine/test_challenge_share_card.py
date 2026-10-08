"""The challenge share card: what it draws, who may have it, and where the button is.

Two types have a card (A-Z, Job Coverage); the Plat Calendar does not yet, and stands in below for "a type
without a card".

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
import re
from unittest import mock

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from challenges.models import CHALLENGE_TYPE_AZ, CHALLENGE_TYPE_CALENDAR, CHALLENGE_TYPE_JOBS
from challenges.services import challenge_service as svc
from challenges.services import rewards
from challenges.services import share_card
from tests.factories import ConceptFactory, GameFactory, IGDBMatchFactory, ProfileFactory, UserFactory
from trophies.models import Contract, Job

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


def _jobs_run(profile):
    return svc.start(profile, CHALLENGE_TYPE_JOBS)


def _fill_job(run, profile, slug, *, complete=False):
    """Assign a contract carrying `slug` to that job's square, and optionally finish it."""
    contract = _contract(f'Game for {slug}')
    contract.jobs.set([Job.objects.get(slug=slug)])
    slot = svc.assign(run, profile, slug, contract)
    if complete:
        svc.mark_slot_completed(slot)
    run.refresh_from_db()
    return slot


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
    assert context['left'] == 25
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
    """Cards ship one type at a time, so the Calendar has none yet and must not inherit another's layout."""
    client = Client()
    profile = _hunter(client)
    run = svc.start(profile, CHALLENGE_TYPE_CALENDAR)
    assert client.get(_html_url(run)).status_code == 404
    assert client.get(_png_url(run)).status_code == 404


def test_the_owner_gets_a_job_coverage_preview():
    client = Client()
    run = _jobs_run(_hunter(client))
    resp = client.get(_html_url(run))
    assert resp.status_code == 200
    assert 'Job Coverage Challenge' in json.loads(resp.content)['html']


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


def test_a_job_coverage_run_page_offers_share():
    client = Client()
    run = _jobs_run(_hunter(client))
    assert f'data-html-url="{_html_url(run)}"' in _detail(client, run)


def test_a_calendar_run_page_offers_no_share_yet():
    client = Client()
    run = svc.start(_hunter(client), CHALLENGE_TYPE_CALENDAR)
    resp = client.get(reverse('challenge_detail', args=[run.id]))
    assert resp.status_code == 200, 'a page that failed to render offers no button either'
    assert 'data-challenge-share' not in resp.content.decode()


def test_my_challenges_offers_share_on_the_types_that_have_a_card():
    """Both live contract-backed runs carry Share; the Calendar's card is not built, so it has none -- and
    the page still renders ONE dialog however many buttons it holds."""
    client = Client()
    profile = _hunter(client)
    az = _az_run(profile)
    jobs = _jobs_run(profile)
    calendar = svc.start(profile, CHALLENGE_TYPE_CALENDAR)
    body = client.get(reverse('my_challenges')).content.decode()
    assert f'data-html-url="{_html_url(az)}"' in body
    assert f'data-html-url="{_html_url(jobs)}"' in body
    assert _html_url(calendar) not in body
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


# ── the Job Coverage board ───────────────────────────────────────────────────────────────────────────

def test_job_coverage_draws_five_discipline_shelves_in_radar_order():
    """FIVE SHELVES OF FIVE, the owner's layout, taken from the same grouping the live board draws: the
    radar's order (combat, exploration, mind, heart, finesse), each in its discipline's colour."""
    from core.services.completion_card_service import DISCIPLINE_COLOURS
    context = share_card.build_card_context(_jobs_run(_hunter()))
    shelves = context['shelves']
    assert [sh['label'] for sh in shelves] == ['Combat', 'Exploration', 'Mind', 'Heart', 'Finesse']
    assert [sh['colour'] for sh in shelves] == [DISCIPLINE_COLOURS[k] for k in
                                               ('combat', 'exploration', 'mind', 'heart', 'finesse')]
    assert all(len(sh['squares']) == 5 and sh['total'] == 5 for sh in shelves)
    assert all(sh['glyph'] for sh in shelves), 'a shelf lost its discipline glyph'
    assert 'rows' not in context


def test_a_job_square_carries_its_glyph_and_colour_and_a_letter_does_not():
    """The mark is the only difference between the boards: a job's glyph in its discipline colour, or the
    letter. A job square without a glyph would render its SLUG as text."""
    jobs = share_card.build_card_context(_jobs_run(_hunter()))
    for shelf in jobs['shelves']:
        for sq in shelf['squares']:
            assert sq['is_job'] and sq['glyph']
            # Its OWN discipline's colour, not merely a colour: the grey fallback is a hex too.
            assert sq['colour'] == shelf['colour'], (sq['key'], sq['colour'], shelf['label'])
    az = share_card.build_card_context(_az_run(_hunter()))
    assert not any(sq['glyph'] or sq['is_job'] for row in az['rows'] for sq in row)


def test_a_shelf_tallies_its_own_discipline():
    profile = _hunter()
    run = _jobs_run(profile)
    combat = list(Job.objects.filter(discipline='combat').values_list('slug', flat=True))
    _fill_job(run, profile, combat[0], complete=True)
    _fill_job(run, profile, combat[1], complete=True)
    _fill_job(run, profile, combat[2])
    shelves = {sh['label']: sh for sh in share_card.build_card_context(run)['shelves']}
    assert shelves['Combat']['done'] == 2
    assert [sq['state'] for sq in shelves['Combat']['squares']].count(share_card.ASSIGNED) == 1
    assert shelves['Mind']['done'] == 0


def test_the_xp_stat_is_what_was_paid_not_what_is_owed():
    """XP behind a Claim button is not the hunter's yet. Two finished squares, one claimed: 6,000, not
    12,000 -- the figure Career would show."""
    from trophies.util_modules.constants import CHALLENGE_SLOT_JOB_XP
    profile = _hunter()
    run = _jobs_run(profile)
    slugs = list(Job.objects.order_by('slug').values_list('slug', flat=True)[:2])
    for slug in slugs:
        _fill_job(run, profile, slug, complete=True)
    assert share_card.build_card_context(run)['xp_paid'] == 0
    rewards.redeem_slot(run, profile, slugs[0])
    assert share_card.build_card_context(run)['xp_paid'] == CHALLENGE_SLOT_JOB_XP


def test_the_job_card_renders_its_own_words():
    from django.template.loader import render_to_string
    profile = _hunter()
    run = _jobs_run(profile)
    slug = Job.objects.order_by('slug').values_list('slug', flat=True).first()
    _fill_job(run, profile, slug, complete=True)
    html = render_to_string(share_card.CARD_TEMPLATE, share_card.build_card_context(run))
    assert 'Job Coverage Challenge' in html and 'A&ndash;Z' not in html
    # NO SUBLINE on this card: its height went to the covers, and the plaque's 1/25 says the same thing.
    assert 'to go.' not in html and 'One game for every' not in html
    assert '>Job XP<' in html and '>Jobs<' in html
    assert re.search(r'>0</div>\s*<div[^>]*>Job XP<', html), 'nothing is paid yet, so the stat must read 0'
    rewards.redeem_slot(run, profile, slug)
    html = render_to_string(share_card.CARD_TEMPLATE, share_card.build_card_context(run))
    assert re.search(r'>6,000</div>\s*<div[^>]*>Job XP<', html), 'the XP stat lost its value'
    assert '1<span style="font-size: 22px; color: #8a939f;">/5</span>' in html, 'a shelf tally is missing'

    az = render_to_string(share_card.CARD_TEMPLATE, share_card.build_card_context(_az_run(_hunter())))
    assert '>Job XP<' not in az, 'A-Z pays no XP, so its plaque has no XP stat'


def test_the_job_card_costs_the_same_however_full_the_run():
    from django.db import connection
    from django.test.utils import CaptureQueriesContext
    profile = _hunter()
    run = _jobs_run(profile)
    slugs = list(Job.objects.order_by('slug').values_list('slug', flat=True))
    for slug in slugs[:2]:
        _fill_job(run, profile, slug, complete=True)
    with CaptureQueriesContext(connection) as few:
        share_card.build_card_context(run)
    for slug in slugs[2:10]:
        _fill_job(run, profile, slug, complete=True)
    with CaptureQueriesContext(connection) as many:
        share_card.build_card_context(run)
    assert len(many) == len(few), [q['sql'][:120] for q in many.captured_queries]


def test_the_designed_shape_keeps_the_designed_geometry():
    """Five shelves of five, two across and three deep: 78x104 covers on 162px shelves. The art was too small
    to make out at the first cut's 64x85 (owner, 2026-10-08), which is the one thing the card is for."""
    board = share_card.build_card_context(_jobs_run(_hunter()))['board']
    assert (board['cover_w'], board['cover_h'], board['shelf_w']) == (78, 104, 162)
    assert (board['mark_px'], board['well_px'], board['tally_px']) == (26, 33, 36)


def test_the_job_card_has_the_slim_plaque_and_a_z_keeps_its_own():
    """The height three rows of covers need comes out of the Job Coverage plaque; the A-Z card's approved
    plaque is untouched."""
    jobs = share_card.build_card_context(_jobs_run(_hunter()))
    az = share_card.build_card_context(_az_run(_hunter()))
    assert jobs['plaque'] == share_card._PLAQUE['slim']
    assert az['plaque'] == share_card._PLAQUE['full']
    assert az['plaque']['avatar'] == 72 and az['plaque']['num'] == 44


def _fits(context):
    """Every shelf on the card's 1112px content width, in two rows."""
    board, shelves = context['board'], context['shelves']
    width = len(shelves) * board['shelf_w'] + (len(shelves) - 1) * share_card._SHELF_GAP_MIN
    columns = (board['shelf_w'] + share_card._COVER_GAP) // (board['cover_w'] + share_card._COVER_GAP)
    assert width <= share_card._BOARD_WIDTH, f'{len(shelves)} shelves need {width}px'
    rows = share_card._SHELF_ROWS
    assert all(len(sh['squares']) + 1 <= columns * rows for sh in shelves), f'a shelf wraps past {rows} rows'


def test_a_deleted_job_still_fits_and_never_prints_its_slug():
    """A deleted `Job` puts its square on a SIXTH shelf (`slot_groups` keeps it drawable). Hardcoded, six
    204px shelves overflowed the card, and a square with no glyph printed its slug as text."""
    profile = _hunter()
    run = _jobs_run(profile)
    victim = Job.objects.order_by('slug').first()
    slug = victim.slug
    victim.delete()

    context = share_card.build_card_context(run)
    assert len(context['shelves']) == 6
    _fits(context)
    orphan = next(sq for sh in context['shelves'] for sq in sh['squares'] if sq['key'] == slug)
    assert orphan['glyph'] == share_card._FALLBACK_JOB_GLYPH
    assert orphan['colour'] == share_card._NO_DISCIPLINE_COLOUR

    from django.template.loader import render_to_string
    html = render_to_string(share_card.CARD_TEMPLATE, context)
    assert f'>{slug}<' not in html, 'the deleted job square printed its slug'
    # AND THE MARKUP USES THE NUMBERS. Computing a geometry that fits is worthless if the template still
    # draws the designed 204px shelves and 64px covers.
    board = context['board']
    assert html.count(f'width: {board["shelf_w"]}px; flex-shrink: 0;') == 6, 'the shelves ignore the geometry'
    # COUNTED, not merely present: the tally cell is drawn at the cover size too, so an `in` check passed with
    # every square still at 64x85. One per square plus one tally per shelf.
    sized = html.count(f'width: {board["cover_w"]}px; height: {board["cover_h"]}px;')
    assert sized == 25 + 6, f'{sized} cells at the computed size; the squares ignore the geometry'


def test_a_discipline_edit_still_fits_the_card():
    """A staff edit to `Job.discipline` makes a shelf of SIX, which needs a seventh cell for its tally -- more
    than two columns of three rows hold. Wrapping a fourth row would push the plaque off the 630px canvas, so
    the board widens its shelves and shrinks the covers instead."""
    profile = _hunter()
    run = _jobs_run(profile)
    moved = Job.objects.filter(discipline='mind').first()
    moved.discipline = 'combat'
    moved.save(update_fields=['discipline'])

    context = share_card.build_card_context(run)
    assert max(len(sh['squares']) for sh in context['shelves']) == 6
    _fits(context)
    assert context['board']['cover_w'] < share_card._COVER_W_MAX
