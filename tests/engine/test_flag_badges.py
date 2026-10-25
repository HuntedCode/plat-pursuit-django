"""Game flag badges: the rule (`trophies/services/flag_badges.py`), the tag that draws them and the sprite they draw
from (owner, 2026-10-24). The per-surface tests live with each surface; this is the shared core."""
import re
from pathlib import Path

import pytest
from django.db import connection
from django.template import Context, Template
from django.test.utils import CaptureQueriesContext

from tests.factories import ConceptFactory, GameFactory, IGDBMatchFactory
from trophies.services import flag_badges as fb

ROOT = Path(__file__).resolve().parents[2]
SPRITE = ROOT / 'templates' / 'partials' / 'icons' / '_game_flag_sprite.html'
SHOVEL = 'manually_flagged'

pytestmark = pytest.mark.django_db


def _matched(igdb_id=None, **game_kwargs):
    """A game whose concept has an IGDB match, so its work is every game sharing that IGDB id."""
    concept = ConceptFactory()
    match = IGDBMatchFactory(concept=concept, **({'igdb_id': igdb_id} if igdb_id else {}))
    return GameFactory(concept=concept, **game_kwargs), match.igdb_id


# ── the rule ─────────────────────────────────────────────────────────────────────────────────────────────────

def test_own_keys_reads_one_list_most_severe_first():
    game = GameFactory(is_obtainable=False, is_delisted=True, has_online_trophies=True, shovelware_status=SHOVEL)
    assert fb.own_keys(game) == ('unobtainable', 'delisted', 'online', 'shovelware')
    assert fb.own_keys(GameFactory()) == ()


def test_buggy_is_only_kept_when_asked():
    """Not an everywhere flag (owner, 2026-10-24): the Game pages and browse keep it, nothing new gains it."""
    game = GameFactory(has_buggy_trophies=True)
    assert fb.own_keys(game) == ()
    assert fb.own_keys(game, buggy=True) == ('buggy',)


def test_a_work_shows_a_flag_only_when_every_version_has_it():
    """The PS4 version delisted and the PS5 one on sale: the game is not delisted. Both online: it is online."""
    ps4, igdb = _matched(is_delisted=True, has_online_trophies=True)
    ps5, _ = _matched(igdb_id=igdb, has_online_trophies=True)

    keys = fb.work_keys_by_game([ps4, ps5])

    assert keys[ps4.pk] == ('online',) and keys[ps5.pk] == ('online',)


def test_versions_are_the_lists_on_the_game_page_the_card_links():
    """A whole-game card links the Game page, so its versions are that page's lists (`GamePageView._resolve`): a
    trusted match's IGDB page holds every game sharing the id, across concepts; an untrusted match lives on its
    concept's `c/` page, so only its own concept's games count; a blank np id is not a list there at all."""
    a, igdb = _matched(is_delisted=True)
    b, _ = _matched(igdb_id=igdb, is_delisted=True)
    _matched(is_delisted=False)                                          # another work entirely
    keys = fb.work_keys_by_game([a])
    assert keys[a.pk] == ('delisted',)

    # The np floor: a row with no np id is not a list on the page, so it cannot hold a flag back.
    GameFactory(concept=b.concept, np_communication_id='', is_delisted=False)
    assert fb.work_keys_by_game([a])[a.pk] == ('delisted',)

    # An UNTRUSTED match is the asymmetric case. Its own card lives on its concept's `c/` page, so only its own
    # concept's games count for it. But the IGDB page's membership is UNGATED, so it IS one of that page's lists,
    # and its being listed clears the flag on `a`'s card.
    concept = ConceptFactory()
    IGDBMatchFactory(concept=concept, igdb_id=igdb, status='pending_review')
    stray = GameFactory(concept=concept, is_delisted=True)
    assert fb.work_keys_by_game([a, stray]) == {a.pk: ('delisted',), stray.pk: ('delisted',)}
    GameFactory(concept=concept, is_delisted=False)
    assert fb.work_keys_by_game([a, stray]) == {a.pk: (), stray.pk: ()}
    b.is_delisted = False
    b.save(update_fields=['is_delisted'])
    other = ConceptFactory()
    IGDBMatchFactory(concept=other, igdb_id=igdb, status='pending_review')
    lone = GameFactory(concept=other, is_delisted=True)
    assert fb.work_keys_by_game([lone])[lone.pk] == ('delisted',), 'its c/ page is its own concept, not the IGDB one'


def test_an_unmatched_concept_is_its_own_games():
    lone = GameFactory(is_delisted=True)
    sibling = GameFactory(concept=lone.concept, is_delisted=True)
    assert fb.work_keys_by_game([lone])[lone.pk] == ('delisted',)
    sibling.is_delisted = False
    sibling.save(update_fields=['is_delisted'])
    assert fb.work_keys_by_game([lone])[lone.pk] == (), 'one listed sibling clears it'


def test_a_game_with_no_concept_is_a_work_of_one():
    game = GameFactory(concept=None, has_online_trophies=True)
    assert fb.work_keys_by_game([game]) == {game.pk: ('online',)}


def test_concept_keyed_surfaces_use_the_same_rule():
    a, igdb = _matched(is_obtainable=False)
    b, _ = _matched(igdb_id=igdb, is_obtainable=False)
    empty = ConceptFactory()

    keys = fb.work_keys_by_concept([a.concept_id, b.concept_id, empty.pk, None])

    assert keys[a.concept_id] == ('unobtainable',) and keys[b.concept_id] == ('unobtainable',)
    assert keys[empty.pk] == (), 'a concept with no games yet says nothing'


def test_a_page_costs_two_queries_however_many_games():
    """Bounded by the page, never by its size: the anchors' ids, then every version's flags."""
    games = [_matched(is_delisted=True)[0] for _ in range(6)] + [GameFactory() for _ in range(4)]

    with CaptureQueriesContext(connection) as ctx:
        fb.work_keys_by_game(games)

    assert len(ctx.captured_queries) == 2
    assert fb.work_keys_by_game([]) == {} and fb.work_keys_by_concept([]) == {}


# ── the tag ──────────────────────────────────────────────────────────────────────────────────────────────────

def _render(src, **ctx):
    return Template('{% load flag_badges %}' + src).render(Context(ctx))


def test_the_tag_draws_each_key_from_the_sprite_with_a_label():
    html = _render("{% flag_badges keys 'sm' 'row' %}", keys=('unobtainable', 'online'))

    assert html.startswith('<span class="pp-gflags pp-gflags--row pp-gflags--sm">')
    assert html.count('class="pp-gflag ') == 2
    assert '<span class="pp-gflag pp-gflag--error" title="Unobtainable">' in html
    assert '<use href="#pp-gflag-online"/>' in html
    assert '<span class="sr-only">Online trophies</span>' in html, 'a screen reader hears the flag'


def test_the_tag_draws_nothing_for_no_flags_and_skips_unknown_keys():
    """No empty container: it would still be a layout box on every unflagged card."""
    assert _render('{% flag_badges keys %}', keys=()) == ''
    assert _render('{% flag_badges keys %}', keys=None) == ''
    assert _render('{% flag_badges keys %}', keys=('<script>',)) == ''
    assert 'pp-gflags--stack pp-gflags--md' in _render("{% flag_badges keys 'huge' 'diagonal' %}", keys=('online',))


def test_the_filter_reads_a_games_own_columns():
    game = GameFactory(is_delisted=True, has_buggy_trophies=True)
    assert _render('{{ g|flag_keys|join:"," }}', g=game) == 'delisted'
    assert _render('{{ g|flag_keys:True|join:"," }}', g=game) == 'delisted,buggy'


# ── the sprite ───────────────────────────────────────────────────────────────────────────────────────────────

def test_every_flag_has_one_symbol_carrying_its_label_and_tone():
    """The sprite is the ONE copy of each glyph, and the script reads label and tone off it."""
    html = Template(SPRITE.read_text(encoding='utf-8')).render(Context())
    for key, (label, tone) in fb.FLAG_META.items():
        assert html.count(f'id="pp-gflag-{key}"') == 1, key
        assert f'id="pp-gflag-{key}" data-label="{label}" data-tone="{tone}"' in html, key
    assert set(re.findall(r'id="pp-gflag-(\w+)"', html)) == set(fb.FLAG_META) == set(fb.FLAG_ORDER)


def test_each_symbol_carries_the_lucide_stroke_itself():
    """A `<use>` copy inherits from the symbol, never from the sprite around it: attributes on the outer `<svg>`
    reach nothing, which would draw every badge as a filled black blob."""
    src = SPRITE.read_text(encoding='utf-8')
    symbols = re.findall(r'<symbol [^>]*>', src)
    assert len(symbols) == len(fb.FLAG_META)
    for tag in symbols:
        assert 'fill="none"' in tag and 'stroke="currentColor"' in tag and 'stroke-width="2"' in tag, tag


def test_base_mounts_the_sprite_once():
    base = (ROOT / 'templates' / 'base.html').read_text(encoding='utf-8')
    assert base.count("partials/icons/_game_flag_sprite.html") == 1


def test_the_script_helper_draws_from_the_sprite_not_a_copy():
    js = (ROOT / 'static' / 'js' / 'utils.js').read_text(encoding='utf-8')
    body = js[js.index('function flagBadges(keys, size, layout) {'):js.index('window.PlatPursuit.flagBadges')]
    assert "document.getElementById('pp-gflag-' + key)" in body
    assert "getAttribute('data-label')" in body and "getAttribute('data-tone')" in body
    for label, _tone in fb.FLAG_META.values():
        assert label not in body, f'{label!r} is copied into the script'
    assert 'innerHTML' not in body
