import pytest
from django.test import Client
from tests.factories import GameFactory, ProfileFactory, ConceptFactory, IGDBMatchFactory
from trophies.models import GameList, GameListItem

pytestmark = pytest.mark.django_db


def _list(profile, n, name):
    gl = GameList.objects.create(profile=profile, name=name, is_public=True, game_count=n, like_count=n * 3)
    for i in range(n):
        c = ConceptFactory()
        IGDBMatchFactory(concept=c)
        GameListItem.objects.create(game_list=gl, game=GameFactory(concept=c), position=i)
    return gl


def test_render():
    owner = ProfileFactory(is_linked=True)
    for n, name in ((0, 'Empty run'), (1, 'Solo'), (2, 'Pair'), (3, 'Trio'), (7, 'Seven deep')):
        _list(owner, n, name)
    c = Client()
    html = c.get('/community/lists/').content.decode()
    print('\n  status ok, tiles:', html.count('class="pp-gtile"'))
    for cls in ('is-1', 'is-2', 'is-3', 'is-4'):
        print(f'  mosaic {cls}:', html.count(f'pp-gtile__mosaic {cls}'))
    print('  empty-art placeholders:', html.count('pp-gtile__art--empty'))
    print('  author lines:', html.count('pp-gtile__author'))
    print('  like pills:', html.count('pp-gtile__stat'))
    print('  legacy first_game_image gone:', 'first_game_image' not in html)

    partial = c.get('/community/lists/', HTTP_HX_REQUEST='true').content.decode()
    print('  HTMX partial is grid-only:', partial.strip().startswith('<div id="items-grid"'))
    print('  partial bakes pp-reveal:', 'pp-reveal' in partial)
    print('  full page does NOT:', 'pp-reveal' not in html)
