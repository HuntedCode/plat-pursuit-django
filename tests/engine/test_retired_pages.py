"""The trophies-list page and the engine list/detail pages were retired -- their old URLs 301 to Browse games
so bookmarks / inbound links / search indices stay alive. Also pins that the game detail page shows engines as
plain text (no link) now that the engine detail page is gone."""
import pytest
from django.test import Client
from django.urls import reverse

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize("path", ["/trophies/", "/engines/", "/engines/unreal-engine-5/"])
def test_retired_pages_redirect_to_games(path):
    r = Client().get(path)
    assert r.status_code == 301
    assert r["Location"].rstrip("/") == "/games"


def test_game_detail_engine_shows_as_text_not_a_link():
    """The Engine about-fact renders the engine NAME with no href (the engine detail page is gone)."""
    from tests.factories import GameFactory, ConceptFactory, IGDBMatchFactory
    from trophies.models import GameEngine, ConceptEngine

    concept = ConceptFactory()
    IGDBMatchFactory(concept=concept)   # status auto_accepted -> trusted, so the About panel renders
    engine = GameEngine.objects.create(igdb_id=99001, name='Unreal Engine 5', slug='unreal-engine-5')
    ConceptEngine.objects.create(concept=concept, engine=engine)
    game = GameFactory(concept=concept, title_platform=['PS5'])

    html = Client().get(reverse('game_detail', args=[game.np_communication_id])).content.decode()

    assert 'Unreal Engine 5' in html                 # the engine still shows
    assert 'href="/engines/' not in html             # but not as a link to the retired page
