"""The browse pass (DS-02, DS-04, DS-06 in docs/design/reviews/discovery.md).

- No stat tiles on the six browse list pages: each header's one number is what its grid shows. The heartbeat
  stats only those tiles read are deleted, not left computing for nobody.
- Detail pages (franchise, company, genre/theme) fold their four tiles into one line of facts.
- One card anatomy: the title keeps two lines, the hooks row keeps its height, the footer follows; nothing
  says what a game lacks; the trophy breakdown is on one-list cards only; the hunter's mark on the cover.
- A two-row phone toolbar, and breadcrumbs hidden on phones site-wide.
"""
import re
from pathlib import Path

import pytest
from django.core.cache import cache
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone

from tests.factories import (
    ConceptFactory, ConceptGenreFactory, GameFactory, GenreFactory, ProfileFactory, ProfileGameFactory,
    UserConceptRatingFactory,
)

pytestmark = pytest.mark.django_db

ROOT = Path(__file__).resolve().parents[2]
CARD_CSS = (ROOT / 'static' / 'css' / 'components' / 'game-card.css').read_text(encoding='utf-8')
BROWSE_CSS = (ROOT / 'static' / 'css' / 'components' / 'game-browse.css').read_text(encoding='utf-8')

# Every key the removed tiles read. A warm heartbeat carrying all of them must still render no tile.
_OLD_TILE_PAYLOAD = {
    'always': {'games_total': {'value': 40125, 'delta': 156}},
    'expanded': {
        'games_in_badges': {'value': 2095}, 'games_in_contracts': {'value': 312},
        'lists_total': {'value': 40125, 'delta': 37}, 'lists_regional': {'value': 912},
        'lists_with_plat': {'value': 3200}, 'franchises_total': {'value': 871},
        'series_total': {'value': 400}, 'franchise_games': {'value': 7000},
        'franchise_spinoffs': {'value': 120}, 'companies_total': {'value': 7895},
        'companies_developers': {'value': 2300}, 'companies_publishers': {'value': 1400},
        'company_games': {'value': 15000}, 'genres_with_games': {'value': 23},
        'themes_with_games': {'value': 21}, 'games_tagged': {'value': 5400}, 'tags_applied': {'value': 9800},
    },
}
_DELETED_KEYS = ('games_in_contracts', 'jobs_total', 'contracts_live', 'job_xp_banked', 'games_in_badges', 'lists_total', 'lists_regional', 'lists_with_plat', 'games_tagged',
                 'tags_applied', 'franchises_total', 'series_total', 'franchise_games', 'franchise_spinoffs',
                 'companies_total', 'companies_developers', 'companies_publishers', 'company_games')


def _rule(css, selector):
    """The declaration body of the rule whose selector list is exactly `selector`."""
    m = re.search(r'(?:^|\})\s*' + re.escape(selector) + r'\s*\{([^}]*)\}', css, re.M)
    assert m, f'no rule for {selector}'
    return m.group(1)


# ── Headers: no tiles, one count ──────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize('url_name', [
    'games_list', 'trophy_lists', 'recently_added', 'franchises_list', 'companies_list', 'genres_list',
])
def test_no_browse_list_page_renders_tiles_even_with_a_warm_heartbeat(client, url_name):
    GameFactory(title_platform=['PS5'])
    now = timezone.now()
    key = f"site_heartbeat_{now.date().isoformat()}_{now.hour:02d}"
    cache.set(key, _OLD_TILE_PAYLOAD, 120)
    try:
        resp = client.get(reverse(url_name))
    finally:
        cache.delete(key)
    content = resp.content.decode()

    assert resp.status_code == 200
    assert 'class="scard' not in content
    for gone in ('40,125', '7,895', '9,800', '5,400', 'In badge series', 'Spin-offs', 'Catalogue'):
        assert gone not in content, gone


def test_the_companies_count_names_its_unit(client):
    """It is the header's only number now, so it says what it counts, as the other five do."""
    content = client.get(reverse('companies_list')).content.decode()

    header = content[content.index('id="co-count"'):]
    assert 'companies shown</span>' in header[:400]   # 0 companies; it read a bare "shown"


def test_the_heartbeat_no_longer_computes_what_only_the_tiles_read():
    from core.services.site_heartbeat import compute_site_heartbeat

    expanded = compute_site_heartbeat()['expanded']

    for key in _DELETED_KEYS:
        assert key not in expanded, key
    # The readers that remain: the genre/theme switcher. Jobs' four went with its tiles (PR-02).
    for key in ('genres_with_games', 'themes_with_games'):
        assert key in expanded, key


def test_recently_added_drops_the_heartbeat_pair_but_keeps_its_switcher_counts(client):
    GameFactory(title_platform=['PS5'])

    resp = client.get(reverse('recently_added'))

    assert 'ra_catalog_total' not in resp.context and 'ra_new_this_week' not in resp.context
    assert resp.context['category_counts']['base_games'] == 1


# ── Detail pages: one line of facts ───────────────────────────────────────────────────────────────────

def _factline(**ctx):
    return render_to_string('trophies/partials/_catalog_factline.html', ctx)


def test_the_factline_names_lists_only_when_they_differ_from_the_games():
    same = _factline(total_games=12, total_versions=12, total_trophies=640, total_platinums=12)
    more = _factline(total_games=12, total_versions=18, total_trophies=640, total_platinums=12)

    assert '<b>12</b> games' in same and 'trophy list' not in same
    assert '<b>18</b> trophy lists' in more
    assert '<b>640</b> trophies' in same
    assert '>12</b> platinums' in same


def test_the_factline_says_nothing_about_platinums_that_are_not_there():
    """Page Principles rule 3: absence is not content."""
    line = _factline(total_games=1, total_versions=1, total_trophies=1, total_platinums=0)

    assert 'platinum' not in line
    assert '<b>1</b> game<' in line and '<b>1</b> trophy<' in line   # singulars


def test_franchise_and_company_detail_carry_the_factline_not_tiles(client):
    from tests.factories import CompanyFactory, ConceptCompanyFactory
    from trophies.models import ConceptFranchise, Franchise

    concept = ConceptFactory(unified_title='Fact Game')
    GameFactory(concept=concept, title_platform=['PS5'])
    fr = Franchise.objects.create(igdb_id=880001, name='Fact Saga', slug='fact-saga', source_type='franchise')
    ConceptFranchise.objects.create(concept=concept, franchise=fr)
    co = CompanyFactory(name='Fact Studio', slug='fact-studio')
    ConceptCompanyFactory(company=co, concept=concept, is_developer=True, is_publisher=False)

    for url in (reverse('franchise_detail', kwargs={'slug': fr.slug}),
                reverse('company_detail', kwargs={'slug': co.slug})):
        content = client.get(url).content.decode()
        assert 'class="pp-factline"' in content, url
        assert 'class="scard' not in content, url


def test_tag_detail_counts_trophy_lists_and_says_so(client):
    """Its count is of Game rows (lists) above a grid of one card per GAME: labelled "games", it read above
    the cards (the DS-04 mismatch). Zero platinums earned is left out rather than shown as 0."""
    genre = GenreFactory(name='Puzzle', slug='puzzle')
    for _ in range(3):
        ConceptGenreFactory(concept=GameFactory(title_platform=['PS5'], played_count=4).concept, genre=genre)

    content = client.get(reverse('genre_detail', kwargs={'slug': genre.slug})).content.decode()
    factline = content.split('class="pp-factline"')[1].split('</p>')[0]

    assert '<b>3</b> trophy lists' in factline
    assert '<b>12</b> times owned' in factline
    assert 'platinum' not in factline
    assert 'games in this genre' not in content


@pytest.mark.parametrize('avg, shown', [(0.4, False), (0.5, True), (34.0, True)])
def test_tag_detail_never_shows_a_completion_that_rounds_to_zero(client, avg, shown):
    genre = GenreFactory(name='Rhythm', slug='rhythm')
    ConceptGenreFactory(concept=GameFactory(title_platform=['PS5'], avg_completion=avg).concept, genre=genre)

    content = client.get(reverse('genre_detail', kwargs={'slug': genre.slug})).content.decode()
    factline = content.split('class="pp-factline"')[1].split('</p>')[0]

    assert ('average completion' in factline) is shown
    assert '>0%</b>' not in factline


# ── The card ──────────────────────────────────────────────────────────────────────────────────────────

def _viewer(client, game, **pg):
    viewer = ProfileFactory(is_linked=True)
    ProfileGameFactory(profile=viewer, game=game, **pg)
    client.force_login(viewer.user)


def test_the_hunters_mark_reads_their_percentage(client):
    game = GameFactory(title_name='Marked Game', title_platform=['PS5'])
    _viewer(client, game, progress=42)

    content = client.get(reverse('trophy_lists')).content.decode()

    assert '<span class="pp-gcard__mark">42%</span>' in content


def test_the_hunters_mark_reads_platinum_once_earned(client):
    game = GameFactory(title_name='Plat Game', title_platform=['PS5'])
    _viewer(client, game, progress=100, has_plat=True)

    content = client.get(reverse('trophy_lists')).content.decode()

    mark = content.split('class="pp-gcard__mark pp-gcard__mark--plat">')[1].split('</span>')[0]
    assert mark.endswith('Platinum')


def test_no_mark_signed_out(client):
    GameFactory(title_name='Unplayed Game', title_platform=['PS5'])

    assert 'pp-gcard__mark' not in client.get(reverse('trophy_lists')).content.decode()


def test_no_mark_on_a_game_owned_but_not_started(client):
    """0% is not a mark worth the art (rule 3): the mark rides the bar's own gate."""
    game = GameFactory(title_name='Owned Unstarted', title_platform=['PS5'])
    _viewer(client, game, progress=0)

    assert 'pp-gcard__mark' not in client.get(reverse('trophy_lists')).content.decode()


@pytest.mark.parametrize('progress, has_plat, expected', [
    (88, True, 'Platinum'),   # platinumed, then DLC landed: still "Platinum", not 88%
    (100, False, '100%'),     # a list with no platinum, finished: the number, not a platinum it can't have
])
def test_the_mark_at_its_edges(client, progress, has_plat, expected):
    game = GameFactory(title_name='Edge Game', title_platform=['PS5'])
    _viewer(client, game, progress=progress, has_plat=has_plat)

    content = client.get(reverse('trophy_lists')).content.decode()
    mark = content.split('class="pp-gcard__mark')[1].split('</span>')[0]

    assert mark.endswith(expected)
    assert ('pp-gcard__mark--plat' in mark) is has_plat


def test_the_rating_rides_the_footer_only_when_rated(client):
    rated = GameFactory(title_name='Rated Game', title_platform=['PS5'])
    UserConceptRatingFactory(concept=rated.concept, overall_rating=4.0)
    # Played, so its footer's right side renders: the rating gate is then the only thing keeping a star off it.
    GameFactory(title_name='Unrated Game', title_platform=['PS5'], played_count=5)

    content = client.get(reverse('trophy_lists')).content.decode()

    def card(title):
        start = content.index(f'alt="{title}"')
        return content[start:content.index('</a>', start)]

    assert 'pp-gcard__star' not in card('Unrated Game')   # no star of any kind, not a placeholder one
    rated_foot = card('Rated Game').split('class="pp-gcard__footr"')[1]
    assert 'pp-gcard__star' in rated_foot and '4.0' in rated_foot


def test_the_footer_renders_even_with_no_platform(client):
    """It is the pinned last row: a card without it would sit a row short of its neighbours."""
    GameFactory(title_name='Platformless', title_platform=[])

    content = client.get(reverse('trophy_lists')).content.decode()

    assert 'class="pp-gcard__foot"' in content


def test_no_plat_stays_and_plat_goes(client):
    """A platinum is the norm, so the card no longer says "Plat"; its absence is what a hunter scans for."""
    GameFactory(title_name='Platless', title_platform=['PS5'], defined_trophies={'bronze': 3, 'silver': 0, 'gold': 0, 'platinum': 0})
    GameFactory(title_name='Platted', title_platform=['PS5'], defined_trophies={'bronze': 3, 'silver': 0, 'gold': 0, 'platinum': 1})

    content = client.get(reverse('trophy_lists')).content.decode()

    assert content.count('>No Plat<') == 1
    assert '>Plat<' not in content


# ── The card's one anatomy, in its CSS (no JS/CSS runner: source pins, docs/reference) ────────────────

def test_the_hooks_row_keeps_a_fixed_height():
    body = _rule(CARD_CSS, '.pp-gcard__hooks')
    assert re.search(r'(?<![-\w])height:\s*26px', body), 'the hooks row must keep its height when empty'


def test_the_footer_follows_the_fixed_rows_rather_than_pinning_to_the_bottom():
    """Pinned with `margin-top: auto`, a footer whose platform chips wrapped grew UPWARD and lifted its top rule
    22px above its neighbours' (the final audit's catch). Following the fixed-height rows, it lines up and a
    wrap grows downward. So no rule may pin the card's footer."""
    for m in re.finditer(r'([^{}]*pp-gcard__foot[^{}]*)\{([^}]*)\}', CARD_CSS):
        assert 'margin-top: auto' not in m.group(2), f'{m.group(1).strip()} pins the footer to the bottom again'


def test_contract_job_icons_drop_out_whole():
    """One line with `overflow: hidden` sliced the last icon into a sliver on a phone. Wrapping inside one
    icon's height drops an icon that does not fit to a hidden second line instead."""
    jobs = _rule(CARD_CSS, '.pp-gcard__ctr-jobs')
    assert 'flex-wrap: wrap' in jobs and 'height: 15px' in jobs and 'overflow: hidden' in jobs


def test_the_mark_takes_the_bars_colour():
    """Amber is the hunt for a platinum; a finished list without one reads green and progress with no platinum
    to chase reads slate, as the bar under the mark does."""
    assert 'var(--pp-success)' in _rule(CARD_CSS, '.pp-gcard__cover:has(.pp-gcard__barfill--full) .pp-gcard__mark')
    assert 'var(--pp-info)' in _rule(CARD_CSS, '.pp-gcard__cover:has(.pp-gcard__barfill--prog) .pp-gcard__mark')


def test_no_plat_is_never_the_thing_that_gets_clipped():
    """On a 375px phone badge + contract + "No Plat" need ~152px of a 149px body, and `.pp-gcard` clips
    overflow. The contract chip clips inside itself first; "No Plat" never shrinks."""
    assert 'flex-shrink: 0' in _rule(CARD_CSS, '.pp-gcard__hooks > .pp-gcard__plat--no')
    ctr = _rule(CARD_CSS, '.pp-gcard__ctr')
    assert 'min-width: 0' in ctr and 'overflow: hidden' in ctr


def test_the_title_keeps_two_lines():
    title = _rule(CARD_CSS, '.pp-gcard__title')
    assert 'min-height: 2.5em' in title and 'line-height: 1.25' in title


# ── Phone chrome ──────────────────────────────────────────────────────────────────────────────────────

def test_the_phone_toolbar_is_two_rows():
    """Search takes the first row outright; the sort grows from a ZERO basis so the buttons beside it fit
    the second (from `auto` it pushed Filters onto a third row on Browse Games)."""
    block = BROWSE_CSS[BROWSE_CSS.index('/* TWO ROWS ON A PHONE'):]
    block = block[:block.index('\n}\n')]
    assert '.pp-gbrowse__bar > .pp-bgal__search { flex-basis: 100%; }' in block
    assert '.pp-gbrowse__sort { flex: 1 1 0; min-width: 0; }' in block


def test_breadcrumbs_hide_on_phones_but_the_schema_stays(client):
    content = client.get(reverse('trophy_lists')).content.decode()

    trail = content[:content.index('aria-label="Breadcrumb"')]
    assert trail[trail.rindex('<div'):].startswith('<div class="hidden md:flex')
    assert '"BreadcrumbList"' in content
