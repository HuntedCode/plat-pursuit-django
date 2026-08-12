import pytest
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from tests.factories import GameFactory, ProfileFactory, ConceptFactory, IGDBMatchFactory
from trophies.models import GameList, GameListItem

pytestmark = pytest.mark.django_db


def _list(profile, n_games, name='List'):
    gl = GameList.objects.create(profile=profile, name=name, is_public=True, game_count=n_games)
    for i in range(n_games):
        concept = ConceptFactory()
        IGDBMatchFactory(concept=concept)
        GameListItem.objects.create(game_list=gl, game=GameFactory(concept=concept), position=i)
    return gl


def test_baseline():
    owner = ProfileFactory(is_linked=True)
    c = Client()
    for n_lists in (5, 20):
        GameList.objects.all().delete()
        for i in range(n_lists):
            _list(owner, 3, name=f'List {i}')
        c.get('/community/lists/')                       # warm
        with CaptureQueriesContext(connection) as ctx:
            c.get('/community/lists/')
        print(f"\n  BROWSE, {n_lists} lists on the page : {len(ctx)} queries")

    GameList.objects.all().delete()
    for n_items in (5, 40):
        gl = _list(owner, n_items, name=f'Detail {n_items}')
        c.get(f'/community/lists/{gl.id}/')
        with CaptureQueriesContext(connection) as ctx:
            c.get(f'/community/lists/{gl.id}/')
        print(f"  DETAIL, {n_items} items            : {len(ctx)} queries")
