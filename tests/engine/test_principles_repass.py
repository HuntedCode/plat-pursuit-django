"""The Page Principles re-pass of First Visit and Daily loop (2026-10-27): FV-21, DL-11..DL-14.

Records: docs/design/reviews/first-visit.md and daily-loop.md, "Principles re-pass".
"""
import re
from datetime import timedelta
from pathlib import Path

import pytest
from django.utils import timezone

from core.services.stats import compute_community_stats
from tests.factories import ConceptFactory, GameFactory, ProfileFactory
from trophies.models import Game, Job, ProfileJobXP

pytestmark = pytest.mark.django_db

ROOT = Path(__file__).resolve().parents[2]


def _css():
    return (ROOT / 'static/css/components/elements.css').read_text(encoding='utf-8')


def _region(body, start, end):
    return body[body.index(start):body.index(end, body.index(start))]


def _career(client, profile):
    client.force_login(profile.user)
    return client.get('/career/').content.decode()


# --- FV-21: the landing counts games, each once ---

def test_a_game_on_two_platforms_counts_once():
    concept = ConceptFactory()
    GameFactory(concept=concept, title_platform=['PS4'])
    GameFactory(concept=concept, title_platform=['PS5'])
    GameFactory(concept=ConceptFactory(), title_platform=['PS5'])

    counts = Game.objects.all().game_page_counts(since=timezone.now() - timedelta(days=7))

    assert counts['total'] == 2, 'trophy lists, not games: the PS4 and PS5 lists of one game counted twice'


def test_the_count_agrees_with_browse_games():
    concept = ConceptFactory()
    GameFactory(concept=concept); GameFactory(concept=concept)
    GameFactory(concept=None)
    GameFactory(concept=ConceptFactory(), np_communication_id='')   # Browse's np floor drops it

    browse = (Game.objects.filter(np_communication_id__isnull=False).exclude(np_communication_id='')
              .game_page_canonicals().count())

    assert compute_community_stats()['games']['total'] == browse == 2


def test_a_new_version_of_an_old_game_is_not_a_new_game_this_week():
    old = ConceptFactory()
    first = GameFactory(concept=old)
    Game.objects.filter(pk=first.pk).update(created_at=timezone.now() - timedelta(days=60))
    GameFactory(concept=old)                       # its PS5 list arrived this week
    GameFactory(concept=ConceptFactory())          # a game new this week

    assert Game.objects.all().game_page_counts(since=timezone.now() - timedelta(days=7))['weekly'] == 1


# --- DL-11: Career's hero, compacted ---

def test_the_hero_facts_are_one_line_not_four_tiles():
    rule = re.search(r'^\.hero-stats \{([^}]*)\}', _css(), re.M).group(1)

    assert 'display: flex' in rule and 'grid-template-columns' not in rule
    assert re.search(r'^\.hero-stat dd \{[^}]*order: -1', _css(), re.M), 'the number no longer leads its label'


def _media(css, query):
    """Every block for one media query, joined: a query can appear more than once in the file."""
    return '\n'.join(m.group(1) for m in re.finditer(re.escape(f'@media ({query}) {{') + r'(.*?)\n\}', css, re.S))


def test_the_ring_never_shrinks_below_the_size_its_caption_fits():
    """"Pursuer Lv" meets the arcs' glow below 104px on a phone and below 132px wider (the owner saw it clip)."""
    phone, wider = _media(_css(), 'max-width: 639px'), _media(_css(), 'min-width: 640px')

    assert re.search(r'\.career-hero__top \.lab-dna \{ width: 104px; height: 104px; \}', phone)
    assert re.search(r'\.career-hero__top \.lab-dna \{ width: 132px; height: 132px; \}', wider)


def test_on_a_phone_the_switcher_leads_its_row():
    assert re.search(r'\.career-viewbar > \[role="tablist"\] \{ order: -1;', _media(_css(), 'max-width: 639px'))


def test_the_identity_chip_has_one_copy_per_screen(client):
    profile = ProfileFactory(is_linked=True)
    ProfileJobXP.objects.create(profile=profile, job=Job.objects.get(slug='mage'), total_xp=12000, level=5)
    hero = _region(_career(client, profile), 'data-career-panel="hero"', '</section>')

    assert hero.count('Leads with ') == 2
    assert 'career-hero__chip"' in hero and 'career-hero__chiprow"' in hero
    css = _css()
    assert re.search(r'^\.hero-disc-chip\.career-hero__chiprow \{ display: none; \}', css, re.M), \
        'the phone copy shows beside the other one on wider screens'
    phone = _media(css, 'max-width: 639px')
    assert '.career-hero__chip { display: none; }' in phone and \
        '.hero-disc-chip.career-hero__chiprow { display: inline-flex; }' in phone


# --- DL-12: no "Tiers earned 0" ---

def test_tiers_earned_waits_for_a_first_tier(client):
    profile = ProfileFactory(is_linked=True)
    ProfileJobXP.objects.create(profile=profile, job=Job.objects.get(slug='mage'), total_xp=12000, level=5)
    stats = _region(_career(client, profile), '<dl class="hero-stats">', '</dl>')

    assert 'Career XP' in stats and 'Tiers earned' not in stats


def test_tiers_earned_shows_once_there_is_one(client):
    profile = ProfileFactory(is_linked=True)
    ProfileJobXP.objects.create(profile=profile, job=Job.objects.get(slug='mage'), total_xp=999_999, level=40)
    body = _career(client, profile)
    stats = _region(body, '<dl class="hero-stats">', '</dl>')

    tiers = re.search(r'<dt>Tiers earned</dt><dd class="pp-tally" data-countup="(\d+)"', stats)
    assert tiers and int(tiers.group(1)) > 0


# --- DL-13: the legend keys only what the ring draws ---

def test_a_newcomer_has_no_legend(client):
    body = _career(client, ProfileFactory(is_linked=True))

    assert 'Levels gained' not in _region(body, 'data-career-panel="hero"', '</section>')


# --- DL-14: no "0 Hidden" on the lobby ---

def _trophy_row(client, **profile_fields):
    profile = ProfileFactory(is_linked=True, sync_status='synced', **profile_fields)
    client.force_login(profile.user)
    body = client.get('/').content.decode()
    start = body.index('class="home-secondary')
    return body[body.rfind('<div', 0, start):body.index('</div>\n', body.index('Unearned'))
                + body[body.index('Unearned'):].index('\n        </div>') + 20]


def test_the_lobby_drops_hidden_when_nothing_is_hidden(client):
    row = _trophy_row(client, total_hiddens=0)

    assert 'Hidden' not in row and 'lg:grid-cols-4' not in row


def test_the_lobby_shows_hidden_when_there_is_some(client):
    row = _trophy_row(client, total_hiddens=3)

    assert '>3</div><div class="text-[0.7rem] text-base-content/50 mt-0.5">Hidden<' in row
    assert 'lg:grid-cols-4' in row
