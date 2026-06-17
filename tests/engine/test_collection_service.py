"""Tests for collection_service.build_collection_context (the Collection album / Binder).

Pins the album contract: the FULL live-badge set shown (earned framed + unearned slots),
grouping into binder pages by badge type (a new type starts a fresh page), PAGE_SIZE
pagination, the earned summary, id-based DOM anchors, and -- the load-bearing one -- a
CONSTANT query count regardless of badge count (the whale-safety batch path: no per-badge
UserBadge / UserBadgeProgress / Redis fan-out).
"""
import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from trophies.models import Profile, ProfileGamification, UserBadge
from trophies.services import collection_service
from trophies.services.collection_service import PAGE_SIZE, build_collection_context
from tests.factories import BadgeFactory, ProfileFactory, UserBadgeFactory

pytestmark = pytest.mark.django_db


def _series(slug, badge_type='series', tiers=(1, 2, 3, 4), live=True):
    return [
        BadgeFactory(series_slug=slug, tier=t, badge_type=badge_type,
                     is_live=live, required_stages=5, display_series=slug)
        for t in tiers
    ]


def _all_frames(ctx):
    return [f for page in ctx['pages'] for f in page['frames']]


def test_full_set_shown_with_earned_and_unearned():
    profile = ProfileFactory()
    badges = _series('rs-a')
    UserBadgeFactory(profile=profile, badge=badges[0])  # earn tier 1 only

    ctx = build_collection_context(profile)

    frames = _all_frames(ctx)
    assert len(frames) == 4                       # the whole set, not just earned
    states = {f['state'] for f in frames}
    assert 'earned' in states and 'unearned' in states
    assert ctx['summary'] == {'total': 4, 'earned': 1, 'pct': 25, 'by_tier': {'bronze': 1}}


def test_non_live_badges_excluded():
    profile = ProfileFactory()
    _series('rs-live')
    _series('rs-hidden', live=False)

    ctx = build_collection_context(profile)

    assert ctx['summary']['total'] == 4  # only the live series


def test_new_badge_type_starts_a_fresh_page():
    """Two small types (4 + 4 badges) must NOT share a page even though 8 < PAGE_SIZE --
    each binder page labels exactly one section."""
    profile = ProfileFactory()
    _series('rs-series', 'series')
    _series('fr-one', 'franchise')

    ctx = build_collection_context(profile)

    assert ctx['total_pages'] == 2
    themes = [p['theme'] for p in ctx['pages']]
    assert themes == ['Series', 'Franchises']  # _SECTION_ORDER: series before franchise


def test_page_size_splits_a_large_section():
    profile = ProfileFactory()
    for i in range(5):  # 5 series x 4 tiers = 20 badges in ONE type
        _series(f'rs-{i}', 'series')

    ctx = build_collection_context(profile)

    assert ctx['total_pages'] == 2  # 16 + 4
    assert len(ctx['pages'][0]['frames']) == PAGE_SIZE
    assert len(ctx['pages'][1]['frames']) == 4


def test_sections_follow_canonical_order():
    profile = ProfileFactory()
    _series('ev-x', 'event')
    _series('rs-x', 'series')
    _series('co-x', 'collection')

    ctx = build_collection_context(profile)

    # _SECTION_ORDER = series, franchise, collection, megamix, developer, user, event
    assert [p['theme'] for p in ctx['pages']] == ['Series', 'Collections', 'Events']


def test_summary_counts_earned_by_tier():
    profile = ProfileFactory()
    badges = _series('rs-tiered')
    UserBadgeFactory(profile=profile, badge=badges[1])  # silver
    UserBadgeFactory(profile=profile, badge=badges[2])  # gold

    ctx = build_collection_context(profile)

    assert ctx['summary']['earned'] == 2
    assert ctx['summary']['by_tier'] == {'silver': 1, 'gold': 1}


def test_frames_use_id_based_dom_anchor_and_allow_flip():
    profile = ProfileFactory()
    badges = _series('rs-dom')

    ctx = build_collection_context(profile)

    frame = _all_frames(ctx)[0]
    expected_ids = {f"card-{b.id}" for b in badges}
    assert {f['dom_id'] for f in _all_frames(ctx)} == expected_ids
    assert frame['allow_flip'] is True


def test_series_xp_comes_from_denormalized_gamification(monkeypatch):
    """The earned card's back-of-card series XP is read from the denormalized
    ProfileGamification.series_badge_xp (one read), not recomputed per badge."""
    monkeypatch.setattr(collection_service, 'get_earners_ranks', lambda slugs, pid: {})
    profile = ProfileFactory()
    badges = _series('rs-xp')
    UserBadgeFactory(profile=profile, badge=badges[0])  # earn signal legitimately recomputes
    ProfileGamification.objects.update_or_create(
        profile=profile, defaults={'series_badge_xp': {'rs-xp': 7777}},
    )
    # Re-fetch as a request would: the earn signal cached a stale gamification on the
    # in-memory profile, so read the denormalized value off a clean instance.
    profile = Profile.objects.get(pk=profile.pk)
    # Only NOW (after setup is done) forbid recompute: the album render must read the
    # denormalized value, never call calculate_series_xp per badge.
    monkeypatch.setattr(
        'trophies.services.xp_service.calculate_series_xp',
        lambda *a, **k: (_ for _ in ()).throw(AssertionError('must not recompute series XP')),
    )

    ctx = build_collection_context(profile)

    earned = next(f for f in _all_frames(ctx) if f['state'] == 'earned')
    assert earned['series_xp'] == 7777


def test_query_count_is_constant_regardless_of_badge_count(monkeypatch):
    """The whale-safety guarantee: building the album over MANY more badges issues the same
    number of queries as a few (no per-badge N+1). Unearned-only so the earners-rank Redis
    call short-circuits on an empty earned set."""
    monkeypatch.setattr(collection_service, 'get_earners_ranks', lambda slugs, pid: {})
    profile = ProfileFactory()
    _series('rs-base')  # 4 badges, one type
    build_collection_context(profile)  # warm-up: absorb any one-time/first-call queries

    with CaptureQueriesContext(connection) as small:
        build_collection_context(profile)

    # Grow BOTH badge count AND the number of badge types (sections): a regression that
    # batched the rank/XP maps per-section instead of once would scale with type count.
    for i in range(5):
        _series(f'rs-more-{i}', 'series')
    for i in range(3):
        _series(f'fr-{i}', 'franchise')
    for i in range(3):
        _series(f'co-{i}', 'collection')
    for i in range(2):
        _series(f'ev-{i}', 'event')

    with CaptureQueriesContext(connection) as large:
        build_collection_context(profile)

    assert len(large) == len(small)  # constant across both badge count and type count


def test_no_badges_returns_empty_summary():
    profile = ProfileFactory()

    ctx = build_collection_context(profile)

    assert ctx['pages'] == []
    assert ctx['total_pages'] == 0
    assert ctx['summary']['total'] == 0


def test_build_failure_degrades_to_empty_context(monkeypatch):
    """A failure inside the page build must degrade to an empty album, not raise a 500."""
    monkeypatch.setattr(
        collection_service, '_build_pages',
        lambda profile: (_ for _ in ()).throw(RuntimeError('boom')),
    )
    profile = ProfileFactory()
    _series('rs-x')

    ctx = build_collection_context(profile)

    assert ctx['pages'] == []
    assert ctx['total_pages'] == 0
