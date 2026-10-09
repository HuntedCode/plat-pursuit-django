"""The Pursuer rank table (2026-10-09): the hero's rank bar opens every rank, its divisions and the level
each starts at.

THE TABLE MUST AGREE WITH THE HERO AT EVERY LEVEL. `pursuer_rank_table` writes the division floors out;
`pursuer_rank_for_level` is what the hero, the claim ceremony and the milestone log actually use. A floor
the table prints that the hero disagrees with is a promise the site breaks, so the first test walks every
level from zero past the apex.
"""
from datetime import datetime, timezone as dt_tz
from pathlib import Path

import pytest

from trophies.models import Job, ProfileJobXP, ProgressionMilestone
from trophies.services.career_service import build_career_context
from trophies.util_modules.leveling import PURSUER_RANKS, pursuer_rank_for_level, pursuer_rank_table
from tests.factories import ProfileFactory

ROOT = Path(__file__).resolve().parents[2]
APEX = PURSUER_RANKS[-1][0]


# ── the table ───────────────────────────────────────────────────────────────────────────────────

def test_the_table_agrees_with_the_hero_at_every_level():
    for level in range(0, APEX + 200):
        cur = pursuer_rank_for_level(level)
        table = pursuer_rank_table(level)
        current = [t for t in table['tiers'] if t['current']]
        assert [t['key'] for t in current] == [cur['key']], level
        divs = [d for t in table['tiers'] for d in t['divisions'] if d['current']]
        if cur['division'] is None:
            assert divs == [], level
        else:
            assert [d['label'] for d in divs] == [cur['label']], level


def test_every_division_floor_is_where_the_hero_says_it_starts():
    for tier in pursuer_rank_table(0)['tiers']:
        for d in tier['divisions']:
            assert pursuer_rank_for_level(d['min_level'])['label'] == d['label']
            assert pursuer_rank_for_level(d['min_level'] - 1)['label'] != d['label']


def test_eleven_ranks_and_only_the_middle_nine_have_divisions():
    tiers = pursuer_rank_table(0)['tiers']
    assert [t['key'] for t in tiers] == [r[1] for r in PURSUER_RANKS]
    assert tiers[0]['divisions'] == [] and tiers[-1]['divisions'] == []
    for t in tiers[1:-1]:
        assert [d['roman'] for d in t['divisions']] == ['V', 'IV', 'III', 'II', 'I']
        assert t['divisions'][0]['min_level'] == t['min_level']
        floors = [d['min_level'] for d in t['divisions']]
        assert floors == sorted(set(floors)) and floors[-1] < t['next_level']


def test_levels_to_counts_down_to_each_locked_rank_and_is_zero_once_reached():
    table = pursuer_rank_table(100)   # Seeker
    by_key = {t['key']: t for t in table['tiers']}
    assert by_key['seeker']['levels_to'] == 0 and by_key['seeker']['reached']
    assert by_key['hunter']['levels_to'] == 115 - 100 and not by_key['hunter']['reached']
    assert by_key['ascendant']['levels_to'] == APEX - 100


# ── the page ────────────────────────────────────────────────────────────────────────────────────

def _climber():
    """A hunter in a divisioned rank, with the Recruit milestone logged."""
    profile = ProfileFactory(is_linked=True)
    ProfileJobXP.objects.create(profile=profile, job=Job.objects.get(slug='gunslinger'), total_xp=60000, level=60)
    ProgressionMilestone.objects.create(
        profile=profile, kind=ProgressionMilestone.PURSUER_RANK, key='recruit', name='Recruit', level_at=35,
        reached_at=datetime(2026, 9, 3, 12, tzinfo=dt_tz.utc))
    return profile


def _modal(client, profile):
    client.force_login(profile.user)
    html = client.get('/career/').content.decode()
    start = html.index('id="rank-ladder"')
    return html, html[start:html.index('</script>', start)]


@pytest.mark.django_db
def test_the_rank_bar_is_the_button_that_opens_the_table(client):
    html, _ = _modal(client, _climber())
    assert '<button type="button" class="pgl-open" data-rank-open aria-haspopup="dialog" aria-controls="rank-ladder"' in html


@pytest.mark.django_db
def test_the_table_marks_where_the_hunter_stands(client):
    profile = _climber()
    rank = build_career_context(profile)['hero']['pursuer_rank']
    assert rank['division'], 'the fixture must sit in a divisioned rank or this test proves less'
    _, modal = _modal(client, profile)

    current = modal[modal.index('aria-current="step"'):]
    assert f'style="--rk: var(--rank-{rank["key"]});" aria-current="step"' in modal
    assert modal.count('aria-current="step"') == 1   # the tier; its division says so in words
    assert 'You are here' in current[:current.index('</li>')]
    assert modal.count(', your division</span>') == 1
    assert f'<span class="sr-only">{rank["label"]}, Pursuer Level' in modal


@pytest.mark.django_db
def test_the_button_names_the_distance_to_the_next_rank(client):
    """Its aria-label replaces the visible text for a screen reader, so it must carry what that text says."""
    profile = _climber()
    rank = build_career_context(profile)['hero']['pursuer_rank']
    html, _ = _modal(client, profile)
    assert f'aria-label="Pursuer rank {rank["label"]}, {rank["levels_to_next"]} to {rank["next_label"]}. See every rank"' in html


@pytest.mark.django_db
def test_the_table_still_opens_when_the_career_body_fails(client, monkeypatch):
    """A failed jobs build still draws the hero (and its bar). The modal must come with it, or the bar is a
    button that opens nothing."""
    from trophies.services import career_service

    def boom(profile):
        raise RuntimeError('jobs down')

    monkeypatch.setattr(career_service, '_build_jobs', boom)
    html, _ = _modal(client, _climber())
    assert 'data-rank-open' in html and 'id="rank-ladder"' in html


@pytest.mark.django_db
def test_a_reached_rank_shows_the_day_it_was_reached(client):
    _, modal = _modal(client, _climber())
    recruit = modal[modal.index('>Recruit<'):]
    assert recruit[:recruit.index('</p>')].endswith('Reached 3 Sep 2026')


def test_a_rank_logged_but_since_lost_says_how_far_off_it_is_not_reached():
    """A milestone outlives a drop in level (a reconcile can take XP back). The row follows the level."""
    from django.template.loader import render_to_string

    table = pursuer_rank_table(100)   # Seeker: Hunter (115) not held
    for t in table['tiers']:
        t['reached_at'] = datetime(2026, 9, 1, tzinfo=dt_tz.utc) if t['key'] == 'hunter' else None
    html = render_to_string('trophies/partials/career/_rank_ladder.html', {'table': table, 'level': 100})
    hunter = html[html.index('>Hunter<'):]
    assert hunter[:hunter.index('</p>')].endswith('15 levels to go')


@pytest.mark.django_db
def test_a_locked_rank_says_how_far_off_it_is(client):
    profile = _climber()
    level = build_career_context(profile)['hero']['pursuer_level']
    _, modal = _modal(client, profile)
    ascendant = modal[modal.index('>Ascendant<'):]
    assert f'{APEX - level:,} levels to go. No divisions' in ascendant


def test_the_modal_closes_on_its_own_id_scoped_rule():
    css = (ROOT / 'static' / 'css' / 'components' / 'series-list.css').read_text(encoding='utf-8')
    assert '#rank-ladder.is-closing,' in css
    assert '#rank-ladder.is-closing .pp-detail-modal__dialog,' in css
