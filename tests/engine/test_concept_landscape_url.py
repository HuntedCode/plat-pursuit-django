"""Concept.get_landscape_url: the wide-banner image chain used by Contracts card banners.

Priority is bg_url (PSN GAMEHUB art, or IGDB art the sync backfilled into it) -> a trusted
IGDB match's screenshots -> its artworks -> None. The live IGDB fallbacks are the point:
an IGDB-anchored concept with an empty bg_url must still surface landscape media, and an
untrusted match must not.
"""

import pytest

from tests.factories import ConceptFactory, IGDBMatchFactory

pytestmark = pytest.mark.django_db


def test_prefers_bg_url_over_igdb():
    concept = ConceptFactory(bg_url="https://psn.example/bg.jpg")
    IGDBMatchFactory(concept=concept, igdb_artwork_image_ids=["art1"])
    assert concept.get_landscape_url() == "https://psn.example/bg.jpg"


def test_falls_back_to_igdb_screenshot_when_no_bg_url():
    concept = ConceptFactory(bg_url="")
    IGDBMatchFactory(
        concept=concept,
        igdb_artwork_image_ids=["art1"],
        igdb_screenshot_image_ids=["shot1"],
    )
    url = concept.get_landscape_url()
    assert "shot1" in url and "screenshot_big" in url  # screenshot wins over artwork


def test_falls_back_to_screenshot_when_no_artwork():
    concept = ConceptFactory(bg_url="")
    IGDBMatchFactory(
        concept=concept, igdb_artwork_image_ids=[], igdb_screenshot_image_ids=["shot1"]
    )
    assert "shot1" in concept.get_landscape_url()


def test_none_when_no_bg_url_and_no_igdb_media():
    concept = ConceptFactory(bg_url="")
    IGDBMatchFactory(
        concept=concept, igdb_artwork_image_ids=[], igdb_screenshot_image_ids=[]
    )
    assert concept.get_landscape_url() is None


def test_none_when_no_match_and_no_bg_url():
    concept = ConceptFactory(bg_url="")
    assert concept.get_landscape_url() is None


def test_ignores_untrusted_match():
    concept = ConceptFactory(bg_url="")
    IGDBMatchFactory(
        concept=concept, status="pending_review", igdb_artwork_image_ids=["art1"]
    )
    assert concept.get_landscape_url() is None


# --- landscape_urls (the carousel list) ---------------------------------------


def test_urls_ordered_bg_then_screenshot_then_artwork():
    concept = ConceptFactory(bg_url="https://psn.example/bg.jpg")
    IGDBMatchFactory(
        concept=concept,
        igdb_artwork_image_ids=["art1"],
        igdb_screenshot_image_ids=["shot1", "shot2"],
    )
    urls = concept.landscape_urls()
    assert urls[0] == "https://psn.example/bg.jpg"
    assert "shot1" in urls[1] and "shot2" in urls[2]
    assert "art1" in urls[3]


def test_urls_dedup_when_bg_equals_first_artwork():
    # sync backfills bg_url from artwork[0], so they can be identical -> one entry, not two
    shared = "https://images.igdb.com/igdb/image/upload/t_1080p/art1.jpg"
    concept = ConceptFactory(bg_url=shared)
    IGDBMatchFactory(
        concept=concept, igdb_artwork_image_ids=["art1"], igdb_screenshot_image_ids=["shot1"]
    )
    urls = concept.landscape_urls()
    assert urls.count(shared) == 1
    assert urls == [shared, "https://images.igdb.com/igdb/image/upload/t_screenshot_big/shot1.jpg"]


def test_urls_respects_limit():
    concept = ConceptFactory(bg_url="")
    IGDBMatchFactory(
        concept=concept,
        igdb_artwork_image_ids=["a1", "a2"],
        igdb_screenshot_image_ids=["s1", "s2", "s3"],
    )
    assert len(concept.landscape_urls(limit=3)) == 3


def test_urls_empty_when_no_media():
    concept = ConceptFactory(bg_url="")
    assert concept.landscape_urls() == []
