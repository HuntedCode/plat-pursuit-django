"""The Page Principles re-pass of First Visit and Daily loop (2026-10-27): FV-21, DL-11..DL-14.

Records: docs/design/reviews/first-visit.md and daily-loop.md, "Principles re-pass".
"""
import re
from datetime import timedelta
from pathlib import Path

import pytest
from django.utils import timezone

from core.services.stats import compute_community_stats
from tests.factories import ConceptFactory, GameFactory, IGDBMatchFactory, ProfileFactory
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


def test_the_count_agrees_with_browse_games(client):
    """Read off the Browse Games page itself, so a change to its own query is caught here too."""
    concept = ConceptFactory()
    GameFactory(concept=concept); GameFactory(concept=concept)
    GameFactory(concept=None)
    GameFactory(concept=ConceptFactory(), np_communication_id='')   # Browse's np floor drops it

    page = client.get('/games/').content.decode()
    browse = int(re.search(r'data-games-count data-countup="(\d+)"', page).group(1))

    assert compute_community_stats()['games']['total'] == browse == 2


def test_split_concepts_sharing_a_trusted_igdb_id_are_one_game():
    """Deliberately-split concepts that one IGDB game holds are one game page, so one game."""
    for _ in range(2):
        concept = ConceptFactory()
        IGDBMatchFactory(concept=concept, igdb_id=777001)
        GameFactory(concept=concept)

    assert Game.objects.all().game_page_counts(since=timezone.now() - timedelta(days=7))['total'] == 1


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
    phone = _media(_css(), 'max-width: 639px')
    assert re.search(r'\.career-viewbar > \[role="tablist"\] \{ order: -1;', phone)
    assert '.career-viewbar > [role="tablist"] > .pp-switch__chip { flex: 1 1 0;' in phone, \
        'a full-width switcher with packed chips leaves an empty bordered end'


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
    hero = _region(_career(client, ProfileFactory(is_linked=True)), 'data-career-panel="hero"', '</section>')

    assert 'class="lab-dna' in hero, 'the ring itself is gone, which would pass this for the wrong reason'
    assert 'Levels gained' not in hero


def _hunter_with(levels):
    """A Pursuer whose jobs sit at these levels ({job slug: level}); XP to match."""
    profile = ProfileFactory(is_linked=True)
    for slug, level in levels.items():
        ProfileJobXP.objects.create(profile=profile, job=Job.objects.get(slug=slug),
                                    total_xp=max(level - 1, 0) * 3000 + 1000, level=level)
    return profile


def test_a_first_claim_that_levels_nothing_draws_a_plain_ring_and_no_lead(client):
    """The usual first claim: XP in a job, no level gained. The helper's even five-way split read as a
    composition the hunter does not have, and every discipline tied, so "Leads with" named the first one."""
    hero = _region(_career(client, _hunter_with({'mage': 1})), 'data-career-panel="hero"', '</section>')

    assert 'class="lab-dna' in hero and 'lab-dna__arc"' not in hero and 'lab-dna__peek' not in hero
    assert 'Leads with' not in hero and 'Levels gained' not in hero


def test_a_level_gained_brings_the_arcs_and_the_lead(client):
    hero = _region(_career(client, _hunter_with({'mage': 3})), 'data-career-panel="hero"', '</section>')

    assert hero.count('lab-dna__arc"') == 5
    assert f'Leads with {Job.objects.get(slug="mage").get_discipline_display()}' in hero


def test_a_tie_leads_with_nobody(client):
    mind, combat = Job.objects.get(slug='mage').discipline, Job.objects.get(slug='slayer').discipline
    assert mind != combat
    hero = _region(_career(client, _hunter_with({'mage': 3, 'slayer': 3})), 'data-career-panel="hero"', '</section>')

    assert 'Leads with' not in hero, 'two disciplines tied on levels gained, and one was named the lead'
    assert hero.count('lab-dna__arc"') == 5, 'a tie still draws the arcs; only the lead waits'


@pytest.mark.parametrize('totals, lead', [
    ({'combat': 2, 'mind': 1}, 'combat'),
    ({'combat': 2, 'mind': 2}, None),          # a tie
    ({'combat': 0, 'mind': 0}, None),          # nothing gained
    ({'combat': 0}, None),                     # one discipline, nothing gained: still nobody
    ({}, None),
])
def test_the_lead_is_one_discipline_strictly_ahead(totals, lead):
    from trophies.services.career_service import leading_discipline

    ring = [{'slug': s, 'label': s.title(), 'total': n} for s, n in totals.items()]
    got = leading_discipline(ring)

    assert (got['slug'] if got else None) == lead


def test_the_lobby_ring_goes_plain_too(client):
    profile = _hunter_with({'mage': 1})
    profile.sync_status = 'synced'; profile.save(update_fields=['sync_status'])
    client.force_login(profile.user)
    body = client.get('/').content.decode()
    card = _region(body, 'home-moat--career', '</a>')

    assert 'class="lab-dna lab-dna--sm' in card and 'lab-dna__arc"' not in card


# --- DL-14: no "0 Hidden" on the lobby ---

def _trophy_row(client, **profile_fields):
    profile = ProfileFactory(is_linked=True, sync_status='synced', **profile_fields)
    client.force_login(profile.user)
    body = client.get('/').content.decode()
    # From the row's own opening tag to the line that closes it (the row's children are one line each).
    return _region(body, 'class="home-secondary', '\n        </div>')


def test_the_lobby_drops_hidden_when_nothing_is_hidden(client):
    row = _trophy_row(client, total_hiddens=0)

    assert 'Hidden' not in row and 'lg:grid-cols-4' not in row


def test_the_lobby_shows_hidden_when_there_is_some(client):
    row = _trophy_row(client, total_hiddens=3)

    assert '>3</div><div class="text-[0.7rem] text-base-content/50 mt-0.5">Hidden<' in row
    assert 'lg:grid-cols-4' in row
