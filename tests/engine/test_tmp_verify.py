import pytest
from django.test import Client
from tests.factories import GameFactory, ProfileFactory, ConceptFactory, IGDBMatchFactory
from trophies.models import GameList, GameListItem

pytestmark = pytest.mark.django_db


def test_detail_still_renders():
    owner = ProfileFactory(is_linked=True)
    gl = GameList.objects.create(profile=owner, name='Soulslike Run', is_public=True, game_count=3)
    names = []
    for i in range(3):
        concept = ConceptFactory()
        IGDBMatchFactory(concept=concept)
        g = GameFactory(concept=concept, title_name=f'Bloodborne {i}')
        names.append(g.title_name)
        GameListItem.objects.create(game_list=gl, game=g, position=i)

    resp = Client().get(f'/community/lists/{gl.id}/')
    html = resp.content.decode()
    print('status', resp.status_code, '| items in ctx', len(resp.context['items']))
    for n in names:
        assert n in html, f'{n} missing from the page'
    assert 'Soulslike Run' in html
    print('all three games render, list name renders')

