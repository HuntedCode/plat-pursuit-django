"""Which badge a game leads with, when it belongs to several.

The rule reads the ATTRIBUTION a series carries, not its `badge_type` label:

    has a collection -> else has a franchise -> else has a developer -> else fallback

`collection`, `franchise` and `developer` are independent nullable FKs, and a `series`-type badge can
carry a franchise -- so ranking by the type label sorts that badge *below* one with no attribution at
all. That is what shipped first; these pin the corrected axis.

One rule, three surfaces (browse cards, game detail, the plat card's spine). The plat card's end of it
lives in `test_plat_cards.py`; this covers the shared helper and the batched browse-card query, whose
`.values()` has to carry the three FK ids for the sort to see them at all.
"""
import pytest
from django.contrib.auth.models import AnonymousUser
from django.test import RequestFactory

from tests.factories import (
    BadgeSeriesFactory, GameFactory, GroupBadgeFactory, PlatformGroupFactory, StageFactory,
)
from trophies.constants import badge_attribution_rank

pytestmark = pytest.mark.django_db


# ── The shared helper ─────────────────────────────────────────────────────────────────────────────

def test_the_rank_order_is_collection_franchise_developer_fallback():
    ranks = [
        badge_attribution_rank(collection_id=1, franchise_id=1, developer_id=1),
        badge_attribution_rank(franchise_id=1, developer_id=1),
        badge_attribution_rank(developer_id=1),
        badge_attribution_rank(),
    ]

    assert ranks == sorted(ranks) and len(set(ranks)) == 4


def test_the_most_significant_attribution_present_wins():
    """A series can carry several at once; the highest-ranking one decides."""
    assert badge_attribution_rank(collection_id=7, developer_id=3) == badge_attribution_rank(collection_id=7)


def test_a_null_fk_does_not_count_as_present():
    """The ids arrive straight off a model or a `.values()` row, so None is the common case."""
    assert badge_attribution_rank(None, None, None) == badge_attribution_rank()


def _anon_request():
    """build_game_card_context reads request.user for the per-user progress fill."""
    request = RequestFactory().get('/games/')
    request.user = AnonymousUser()
    return request


# ── The browse-card surface ───────────────────────────────────────────────────────────────────────

def _series(concept, name, *, attribution=None, badge_type='series'):
    """A live badge series on this concept carrying at most one attribution FK.

    `badge_type` stays 'series' throughout ON PURPOSE -- the label must not influence the order."""
    from trophies.models import Company, Franchise

    kwargs = {}
    slug = name.lower().replace(' ', '-')
    if attribution == 'collection':
        kwargs['collection'] = Franchise.objects.create(
            igdb_id=5001, name=name, slug=f'{slug}-c', source_type='collection')
    elif attribution == 'franchise':
        kwargs['franchise'] = Franchise.objects.create(
            igdb_id=5002, name=name, slug=f'{slug}-f', source_type='franchise')
    elif attribution == 'developer':
        kwargs['developer'] = Company.objects.create(igdb_id=5003, name=name, slug=f'{slug}-d')

    series = BadgeSeriesFactory(name=name, badge_type=badge_type, **kwargs)
    GroupBadgeFactory(series=series, platform_group=PlatformGroupFactory(), is_live=True)
    StageFactory(series_slug=series.series_slug).concepts.add(concept)
    return series


def test_browse_cards_lead_with_the_best_attributed_badge():
    """The batched query builds its rows with `.values()`, so the three FK ids have to be selected
    explicitly -- omit one and the sort silently can't see it."""
    from trophies.views.game_views import build_game_card_context

    game = GameFactory(title_name='Questy', title_platform=['PS5'])
    for name, attribution in [('Studio Badge', 'developer'), ('Bare Series', None),
                              ('Franchise Badge', 'franchise'), ('Collection Badge', 'collection')]:
        _series(game.concept, name, attribution=attribution)

    ctx = build_game_card_context([game], _anon_request())

    assert ctx['badge_map'][game.concept_id]['names'][0] == 'Collection Badge'


def test_browse_cards_agree_with_the_plat_card():
    """The two surfaces must name the same lead badge, or they disagree about what the game IS.

    Asserting the AGREEMENT rather than each surface's order separately is the point -- that is the
    invariant a future change to either one would break."""
    from core.services import completion_card_service as cards
    from trophies.views.game_views import build_game_card_context
    from tests.engine.test_plat_cards import _completed_game
    from tests.factories import ProfileFactory

    profile = ProfileFactory()
    game, _, standing = _completed_game(profile, with_platinum=True)
    for name, attribution in [('Studio Badge', 'developer'), ('Franchise Badge', 'franchise')]:
        _series(game.concept, name, attribution=attribution)

    browse_lead = build_game_card_context([game], _anon_request())['badge_map'][game.concept_id]['names'][0]
    card_lead = cards.get_card_data(profile, standing)['badge_lines'][0]['series_name']

    assert browse_lead == card_lead == 'Franchise Badge'
