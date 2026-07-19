"""Concept.get_landscape_url / landscape_urls: the wide-banner image chain used by banners,
contract-card carousels, share cards, and profile headers.

Priority is IGDB screenshots (the consistent, preferred source) -> IGDB artworks -> PSN bg_url
as a LAST-RESORT fallback. IGDB leads because its imagery is consistent per game entry, whereas
PSN art varies by platform (PS3 often has none). bg_url is only used when there is no trusted
IGDB media, so a match-less concept still surfaces its PSN art instead of a blank banner, and an
untrusted match is ignored.
"""

import pytest

from tests.factories import ConceptFactory, IGDBMatchFactory

pytestmark = pytest.mark.django_db


def test_prefers_igdb_screenshot_over_bg_url():
    concept = ConceptFactory(bg_url="https://psn.example/bg.jpg")
    IGDBMatchFactory(concept=concept, igdb_screenshot_image_ids=["shot1"])
    url = concept.get_landscape_url()
    assert "shot1" in url and "screenshot_big" in url   # IGDB screenshot wins, not PSN bg_url


def test_prefers_artwork_over_bg_url_when_no_screenshot():
    concept = ConceptFactory(bg_url="https://psn.example/bg.jpg")
    IGDBMatchFactory(concept=concept, igdb_artwork_image_ids=["art1"], igdb_screenshot_image_ids=[])
    assert "art1" in concept.get_landscape_url()          # artwork still beats bg_url


def test_screenshot_wins_over_artwork():
    concept = ConceptFactory(bg_url="")
    IGDBMatchFactory(
        concept=concept, igdb_artwork_image_ids=["art1"], igdb_screenshot_image_ids=["shot1"],
    )
    url = concept.get_landscape_url()
    assert "shot1" in url and "screenshot_big" in url


def test_falls_back_to_bg_url_when_no_igdb_media():
    concept = ConceptFactory(bg_url="https://psn.example/bg.jpg")
    IGDBMatchFactory(concept=concept, igdb_artwork_image_ids=[], igdb_screenshot_image_ids=[])
    assert concept.get_landscape_url() == "https://psn.example/bg.jpg"


def test_falls_back_to_bg_url_when_no_match():
    # A match-less concept has no IGDB media -> its PSN bg_url is the last-resort fallback (not blank).
    concept = ConceptFactory(bg_url="https://psn.example/bg.jpg")
    assert concept.get_landscape_url() == "https://psn.example/bg.jpg"


def test_none_when_no_igdb_media_and_no_bg_url():
    concept = ConceptFactory(bg_url="")
    IGDBMatchFactory(concept=concept, igdb_artwork_image_ids=[], igdb_screenshot_image_ids=[])
    assert concept.get_landscape_url() is None


def test_none_when_no_match_and_no_bg_url():
    concept = ConceptFactory(bg_url="")
    assert concept.get_landscape_url() is None


def test_untrusted_match_ignored_falls_back_to_bg_url():
    concept = ConceptFactory(bg_url="https://psn.example/bg.jpg")
    IGDBMatchFactory(
        concept=concept, status="pending_review",
        igdb_artwork_image_ids=["art1"], igdb_screenshot_image_ids=["shot1"],
    )
    assert concept.get_landscape_url() == "https://psn.example/bg.jpg"   # untrusted IGDB skipped


def test_untrusted_match_and_no_bg_url_is_none():
    concept = ConceptFactory(bg_url="")
    IGDBMatchFactory(concept=concept, status="pending_review", igdb_screenshot_image_ids=["shot1"])
    assert concept.get_landscape_url() is None


# --- landscape_urls (the carousel list) ---------------------------------------


def test_urls_ordered_screenshot_then_artwork_then_bg():
    concept = ConceptFactory(bg_url="https://psn.example/bg.jpg")
    IGDBMatchFactory(
        concept=concept,
        igdb_artwork_image_ids=["art1"],
        igdb_screenshot_image_ids=["shot1", "shot2"],
    )
    urls = concept.landscape_urls()
    assert "shot1" in urls[0] and "shot2" in urls[1]      # screenshots first
    assert "art1" in urls[2]                              # then artworks
    assert urls[3] == "https://psn.example/bg.jpg"        # PSN bg_url last


def test_logo_artwork_never_surfaces_when_enough_screenshots():
    # The reported bug: a transparent-logo artwork must NOT appear when the game has real
    # screenshots. With screenshots filling the cap, the artwork never reaches the gallery.
    logo = "cobyue_logo"
    concept = ConceptFactory(bg_url="")
    IGDBMatchFactory(
        concept=concept,
        igdb_artwork_image_ids=[logo],
        igdb_screenshot_image_ids=["s1", "s2", "s3", "s4", "s5"],
    )
    urls = concept.landscape_urls(limit=5)
    assert len(urls) == 5
    assert all(logo not in u for u in urls)              # the logo artwork is crowded out
    assert all("screenshot_big" in u for u in urls)      # all five slots are screenshots


def test_urls_dedup_when_bg_equals_an_artwork():
    # If bg_url happens to equal an artwork URL, it dedups to a single entry.
    shared = "https://images.igdb.com/igdb/image/upload/t_1080p/art1.jpg"
    concept = ConceptFactory(bg_url=shared)
    IGDBMatchFactory(
        concept=concept, igdb_artwork_image_ids=["art1"], igdb_screenshot_image_ids=["shot1"],
    )
    urls = concept.landscape_urls()
    assert urls.count(shared) == 1
    assert urls == [
        "https://images.igdb.com/igdb/image/upload/t_screenshot_big/shot1.jpg",
        shared,
    ]


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
