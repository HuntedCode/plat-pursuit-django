"""Career's first screen for a new hunter (fix plan branch 4; DL-01, DL-02, DL-09 and the gains legend).

The design record is docs/design/reviews/career-first-arrival.md. A new hunter's history has already earned
contracts; Career leads with what claiming them makes the hunter, and drops the zeros that said nothing.
"""
import itertools
import re

import pytest
from django.utils import timezone

from tests.factories import ProfileFactory
from trophies.models import Contract, EarnedContract, Job, ProfileJobXP

pytestmark = pytest.mark.django_db

_seq = itertools.count(98001)


def _claimable(profile, jobs, n=1):
    for _ in range(n):
        i = next(_seq)
        contract = Contract.objects.create(name=f'arr-{i}', slug=f'arr-{i}', igdb_id=i, is_live=True)
        contract.jobs.set(Job.objects.filter(slug__in=jobs))
        EarnedContract.objects.create(profile=profile, contract=contract, full_reached_at=timezone.now())


def _career(client, profile):
    client.force_login(profile.user)
    return client.get('/career/').content.decode()


def _region(body, start, end):
    return body[body.index(start):body.index(end, body.index(start))]


def test_the_banner_says_what_claiming_makes_the_hunter(client):
    profile = ProfileFactory(is_linked=True)
    _claimable(profile, ['pathfinder'], n=3)        # 18,000 XP = +6 levels
    _claimable(profile, ['gunslinger'])             # 6,000 XP = +2

    banner = _region(_career(client, profile), 'claim-banner', '</a>')

    assert 'Claim them to reach <b class="pp-tally text-primary">Pursuer Lv 9</b> &middot; Newbie' in banner
    assert re.search(r'Pathfinder <span class="pp-tally">1 &rarr; 7</span>', banner)
    assert re.search(r'Gunslinger <span class="pp-tally">1 &rarr; 3</span>', banner)
    assert '24,000' in banner, 'the XP detail line went'


def test_each_job_tile_previews_the_level_a_claim_takes_it_to(client):
    """25 identical "1 Initiate" cards were a wall of zeros (DL-02); a tile with XP waiting now says where
    it goes. A tile with nothing waiting says nothing extra."""
    profile = ProfileFactory(is_linked=True)
    _claimable(profile, ['pathfinder'], n=3)

    body = _career(client, profile)

    tile = _region(body, 'data-slug="pathfinder"', 'job__name')
    assert 'class="job__lv-to pp-tally"' in tile and '&rarr; 7' in tile
    untouched = _region(body, 'data-slug="gunslinger"', 'job__name')
    assert 'job__lv-to' not in untouched


def test_no_preview_without_anything_claimable(client):
    body = _career(client, ProfileFactory(is_linked=True))
    assert 'claim-banner' not in body and 'job__lv-to' not in body


def test_the_summary_waits_for_the_first_claim_and_lives_in_the_hero(client):
    """Four tiles reading 0, 0, 1.0 and 1 said nothing (DL-02), and the card titled "Career" that held them
    pushed every view off a desktop's first screen (DL-01, DL-09)."""
    newcomer = ProfileFactory(is_linked=True)
    body = _career(client, newcomer)
    assert 'hero-stats' not in body
    assert '>Career</h2>' not in body, 'the card titled "Career" is back inside the Career page'

    veteran = ProfileFactory(is_linked=True)
    ProfileJobXP.objects.create(profile=veteran, job=Job.objects.get(slug='mage'), total_xp=12000, level=5)
    body = _career(client, veteran)
    hero = _region(body, 'data-career-panel="hero"', '</section>')
    assert '<dl class="hero-stats">' in hero, 'the summary is not in the hero'
    assert 'Best job level' in hero and 'data-countup="5"' in hero


def test_the_legend_reads_as_levels_gained(client):
    """Each value is what that discipline adds to the Pursuer Level, so it is said as a gain, not "Combat 0"
    beside five Combat jobs at Lv 1. Only the disciplines that gained are listed (owner, 2026-10-27, review
    DL-13): the four "+0" rows keyed segments the ring does not draw."""
    profile = ProfileFactory(is_linked=True)
    ProfileJobXP.objects.create(profile=profile, job=Job.objects.get(slug='mage'), total_xp=12000, level=5)

    legend = _region(_career(client, profile), 'lab-dna-legend-wrap', '</ul>')

    assert 'Levels gained' in legend
    vals = re.findall(r'lab-dna-legend__val">\+<span data-countup="(\d+)">', legend)
    assert vals == ['4']


def test_a_new_pursuer_gets_one_line_not_two(client):
    """"No title equipped" over "New Pursuer" was two lines saying there is nothing here yet."""
    body = _career(client, ProfileFactory(is_linked=True))
    hero = _region(body, 'data-career-panel="hero"', '</section>')
    assert 'New Pursuer' in hero and 'No title equipped' not in hero


def test_the_view_switcher_lines_up_with_the_sub_switch(client):
    """DL-09: the view switcher sat at the right edge (the site-wide Tab Group standard) while the Dossier /
    Sheet switch under it sat at the left. Both are right-aligned now, and the explainer link from the old
    "Career" card holds the row's other end."""
    body = _career(client, ProfileFactory(is_linked=True))
    switch = body.index('aria-label="Career view"')
    row = body[body.rindex('<div class="flex', 0, switch):switch]
    assert 'justify-between' in row and 'How does my Career work?' in row, 'the explainer is not at the row start'
    layout = body.index('aria-label="Jobs layout"')
    assert body[body.rindex('<div', 0, body.rindex('<div', 0, layout)):layout].startswith('<div class="flex justify-end">')


def test_a_claim_that_moves_nothing_promises_nothing(client):
    """One three-job contract pays each job 2,000 XP, short of a level. The tile of a job already holding XP
    must not print "5 -> 5", and the banner must not say "Claim it to reach" the level the hunter is at."""
    profile = ProfileFactory(is_linked=True)
    ProfileJobXP.objects.create(profile=profile, job=Job.objects.get(slug='mage'), total_xp=12000, level=5)
    _claimable(profile, ['mage', 'tycoon', 'pathfinder'])

    body = _career(client, profile)

    assert 'job__lv-to' not in _region(body, 'data-slug="mage"', 'job__name'), 'an unraised job promised a level'
    banner = _region(body, 'claim-banner', '</a>')
    assert 'to reach' not in banner, 'the banner promised a level claiming does not move'
    assert '6,000' in banner, 'the XP detail must still say what is waiting'
