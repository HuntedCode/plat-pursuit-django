"""Tests for the DLC navigation strip on the public roadmap detail page.

Published base-game and DLC guides should cross-link at the top of the page so
readers can jump between them. Unpublished siblings must NOT be linked.
"""

import pytest
from django.urls import reverse

from trophies.models import Roadmap, RoadmapStep
from tests.factories import ConceptFactory, ConceptTrophyGroupFactory, GameFactory

pytestmark = pytest.mark.django_db


def _published_roadmap(concept, ctg, *, status="published"):
    roadmap = Roadmap.objects.create(
        concept=concept, concept_trophy_group=ctg, status=status,
    )
    # A roadmap only links in the strip once it has content (a step or guide).
    RoadmapStep.objects.create(roadmap=roadmap, title="Do the thing")
    return roadmap


def _game_with_base_and_dlc(dlc_status="published"):
    concept = ConceptFactory()
    game = GameFactory(concept=concept, title_platform=["PS5"])
    base_ctg = ConceptTrophyGroupFactory(
        concept=concept, trophy_group_id="default", display_name="Base Game",
        sort_order=0,
    )
    dlc_ctg = ConceptTrophyGroupFactory(
        concept=concept, trophy_group_id="001", display_name="DLC One",
        sort_order=1,
    )
    _published_roadmap(concept, base_ctg)
    _published_roadmap(concept, dlc_ctg, status=dlc_status)
    return game


def _get(client, url):
    # The Cloudflare-bypass middleware 302-redirects guarded paths that arrive
    # without a CF-Ray header (direct-origin traffic). Supply one so the test
    # client reaches the view instead of bouncing to the proxied canonical URL.
    return client.get(url, HTTP_CF_RAY="test-ray")


def _base_url(game):
    return reverse("roadmap_detail", kwargs={"np_communication_id": game.np_communication_id})


def _dlc_url(game, group="001"):
    return reverse(
        "roadmap_detail_dlc",
        kwargs={"np_communication_id": game.np_communication_id, "trophy_group_id": group},
    )


def test_published_dlc_is_linked_from_base_guide(client):
    game = _game_with_base_and_dlc(dlc_status="published")

    resp = _get(client, _base_url(game))

    assert resp.status_code == 200
    body = resp.content.decode()
    assert _dlc_url(game) in body  # the DLC sibling links from the base page


def test_published_base_is_linked_from_dlc_guide(client):
    game = _game_with_base_and_dlc(dlc_status="published")

    resp = _get(client, _dlc_url(game))

    assert resp.status_code == 200
    body = resp.content.decode()
    assert _base_url(game) in body  # and back again from the DLC page


def test_unpublished_dlc_is_not_linked(client):
    game = _game_with_base_and_dlc(dlc_status="draft")

    resp = _get(client, _base_url(game))

    assert resp.status_code == 200
    body = resp.content.decode()
    assert _dlc_url(game) not in body  # draft DLC must not be linked publicly


def test_draft_dlc_is_linked_for_a_writer_in_preview_mode(client):
    from tests.factories import ProfileFactory

    game = _game_with_base_and_dlc(dlc_status="draft")
    writer = ProfileFactory(roadmap_role="writer")
    client.force_login(writer.user)

    # Preview mode is gated to authenticated writers; they see draft siblings.
    resp = _get(client, _base_url(game) + "?preview=true")

    assert resp.status_code == 200
    body = resp.content.decode()
    # The draft DLC links, and the strip carries the preview flag through.
    assert _dlc_url(game) + "?preview=true" in body


def test_strip_absent_when_only_base_is_published(client):
    # No DLC roadmap at all: the page renders, just with nothing to link to.
    concept = ConceptFactory()
    game = GameFactory(concept=concept, title_platform=["PS5"])
    base_ctg = ConceptTrophyGroupFactory(concept=concept, trophy_group_id="default")
    _published_roadmap(concept, base_ctg)

    resp = _get(client, _base_url(game))

    assert resp.status_code == 200
    assert _dlc_url(game) not in resp.content.decode()
