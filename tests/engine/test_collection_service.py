"""Tests for collection_service.build_collection_context (the Collection album / Binder).

Pins the album contract: the FULL live-badge set shown (earned framed + unearned slots),
grouping into binder SETS by badge type (each type is its own binder view, paginated
within the set), PAGE_SIZE pagination, per-set + overall counts, id-based DOM anchors, and
-- the load-bearing one -- a CONSTANT query count regardless of badge count (the
whale-safety batch path: no per-badge UserBadge / UserBadgeProgress / Redis fan-out).
"""
import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from trophies.models import Badge, Profile, ProfileGamification, UserBadge
from trophies.services import collection_service
from trophies.services.collection_service import PAGE_SIZE, build_collection_context
from tests.factories import (
    BadgeFactory, ProfileFactory, UserBadgeFactory, UserBadgeProgressFactory,
)

pytestmark = pytest.mark.django_db


def _series(slug, badge_type='series', tiers=(1, 2, 3, 4), live=True):
    return [
        BadgeFactory(series_slug=slug, tier=t, badge_type=badge_type,
                     is_live=live, required_stages=5, display_series=slug)
        for t in tiers
    ]


def _all_frames(ctx):
    return [f for s in ctx['binder_sets'] for page in s['pages'] for f in page['frames']]


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


def test_each_badge_type_is_its_own_set():
    """Each badge type is a distinct binder view (set), even small ones -- one page each."""
    profile = ProfileFactory()
    _series('rs-series', 'series')
    _series('fr-one', 'franchise')

    sets = build_collection_context(profile)['binder_sets']

    assert [s['key'] for s in sets] == ['series', 'franchise']
    assert [s['label'] for s in sets] == ['Series', 'Franchises']
    assert all(len(s['pages']) == 1 for s in sets)  # 4 badges each -> one page


def test_page_size_splits_a_large_set():
    profile = ProfileFactory()
    for i in range(5):  # 5 series x 4 tiers = 20 badges in ONE type
        _series(f'rs-{i}', 'series')

    series_set = build_collection_context(profile)['binder_sets'][0]

    assert len(series_set['pages']) == 2  # 16 + 4
    assert len(series_set['pages'][0]['frames']) == PAGE_SIZE
    assert len(series_set['pages'][1]['frames']) == 4
    assert [p['number'] for p in series_set['pages']] == [1, 2]  # numbered within the set


def test_set_carries_earned_and_total_counts():
    profile = ProfileFactory()
    badges = _series('rs-count')
    UserBadgeFactory(profile=profile, badge=badges[0])
    UserBadgeFactory(profile=profile, badge=badges[1])

    s = build_collection_context(profile)['binder_sets'][0]

    assert s['total'] == 4 and s['earned'] == 2


def test_pages_pair_into_facing_page_spreads():
    profile = ProfileFactory()
    for i in range(5):  # 5 series x 4 = 20 badges -> 2 pages (16 + 4)
        _series(f'rs-{i}', 'series')

    s = build_collection_context(profile)['binder_sets'][0]

    assert len(s['pages']) == 2
    assert len(s['spreads']) == 1                  # 2 pages -> one facing-page spread
    assert s['spreads'][0]['left'] is s['pages'][0]
    assert s['spreads'][0]['right'] is s['pages'][1]


def test_odd_final_page_leaves_an_empty_right():
    profile = ProfileFactory()
    for i in range(9):  # 9 series x 4 = 36 badges -> 3 pages (16 + 16 + 4)
        _series(f'rs-{i}', 'series')

    s = build_collection_context(profile)['binder_sets'][0]

    assert len(s['pages']) == 3
    assert len(s['spreads']) == 2                  # [p0,p1] + [p2, empty]
    assert s['spreads'][1]['left'] is s['pages'][2]
    assert s['spreads'][1]['right'] is None


def test_sets_follow_canonical_order():
    profile = ProfileFactory()
    _series('ev-x', 'event')
    _series('rs-x', 'series')
    _series('co-x', 'collection')

    sets = build_collection_context(profile)['binder_sets']

    # _SECTION_ORDER = series, franchise, collection, megamix, developer, user, event
    assert [s['label'] for s in sets] == ['Series', 'Collections', 'Events']


# --- sorting (default set_number, plus the series-name option) -----------------


def _number(series, start):
    for i, b in enumerate(series, start=start):
        Badge.objects.filter(pk=b.pk).update(set_number=i)


def test_default_sort_is_set_number_edition_order():
    profile = ProfileFactory()
    _number(_series('alpha', 'series'), 5)   # alphabetically first, numbered LAST
    _number(_series('zeta', 'series'), 1)     # alphabetically last, numbered FIRST

    frames = _all_frames(build_collection_context(profile))  # default sort

    assert [f.get('set_number') for f in frames] == [1, 2, 3, 4, 5, 6, 7, 8]
    assert [f['series_name'] for f in frames[:4]] == ['zeta'] * 4  # edition beats alpha


def test_series_sort_orders_alphabetically():
    profile = ProfileFactory()
    _number(_series('zeta', 'series'), 1)
    _number(_series('alpha', 'series'), 5)

    frames = _all_frames(build_collection_context(profile, sort='series'))

    assert [f['series_name'] for f in frames[:4]] == ['alpha'] * 4  # alpha first under series sort
    assert [f['series_name'] for f in frames[4:]] == ['zeta'] * 4


def test_unnumbered_badges_sort_after_numbered():
    profile = ProfileFactory()
    _number(_series('zeta', 'series'), 1)
    _series('alpha', 'series')  # left unnumbered (set_number stays None)

    frames = _all_frames(build_collection_context(profile))

    assert [f['series_name'] for f in frames[:4]] == ['zeta'] * 4   # numbered first
    assert [f['series_name'] for f in frames[4:]] == ['alpha'] * 4  # unnumbered last


def test_context_exposes_sort_and_options_with_invalid_fallback():
    profile = ProfileFactory()
    _series('rs')

    ctx = build_collection_context(profile, sort='bogus')

    assert ctx['sort'] == 'set_number'  # invalid -> default
    assert ('set_number', 'Set number') in ctx['sort_options']
    assert ('series', 'Series name') in ctx['sort_options']


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


def test_frames_carry_series_slug_for_detail_link():
    """Each frame carries series_slug -- it powers the per-series detail link in the binder
    header and the series-name link in the list view."""
    profile = ProfileFactory()
    _series('rs-link')

    frame = _all_frames(build_collection_context(profile))[0]

    assert frame['series_slug'] == 'rs-link'


def test_binder_renders_series_header_linking_to_detail():
    """The binder groups each row of four tiers under a header that names the series, shows a
    tier pip per badge, and links to its badge detail page (regroup + url integration)."""
    from django.template.loader import render_to_string

    profile = ProfileFactory()
    badges = _series('rs-render')
    UserBadgeFactory(profile=profile, badge=badges[0])  # bronze earned -> one filled pip

    ctx = build_collection_context(profile)
    html = render_to_string('components/binder.html', {'binder_sets': ctx['binder_sets']})

    assert 'pp-binder__series-header' in html
    assert '/badges/rs-render/' in html                     # links to the series detail page (Browse catalog)
    assert html.count('pp-binder__series-pip') >= 4          # four tier pips
    assert 'is-filled' in html                               # the earned bronze pip


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

    assert ctx['binder_sets'] == []
    assert ctx['total_pages'] == 0
    assert ctx['summary']['total'] == 0
    assert ctx['list_badges'] == []
    assert ctx['themes'] == []
    assert ctx['showcase'] == []
    assert ctx['chase'] == []


# --- the Case: series groups (4 tiers bound together, never split) -------------


def test_set_groups_bind_a_series_four_tiers_together():
    """Each set carries `groups` -- one per series, holding that series' four tiers in
    bronze->platinum order. This is what lets the Case render a series as one bound unit."""
    profile = ProfileFactory()
    _series('rs-a', 'series')
    _series('rs-b', 'series')

    s = build_collection_context(profile)['binder_sets'][0]

    assert [g['slug'] for g in s['groups']] == ['rs-a', 'rs-b']  # series-then-tier sort order
    for g in s['groups']:
        assert g['name']                                          # series display name
        assert [f['tier'] for f in g['tiers']] == ['bronze', 'silver', 'gold', 'platinum']


def test_groups_respect_the_active_sort():
    """Grouping walks the already-sorted frames, so the series-name sort reorders groups too."""
    profile = ProfileFactory()
    _number(_series('zeta', 'series'), 1)   # numbered first, alpha last
    _number(_series('alpha', 'series'), 5)

    s = build_collection_context(profile, sort='series')['binder_sets'][0]

    assert [g['slug'] for g in s['groups']] == ['alpha', 'zeta']  # alpha wins under series sort


# --- the Case: Showcase (proudest earned, swappable modes) --------------------


def _earn(profile, slug, tier, **badge_kwargs):
    badge = BadgeFactory(series_slug=slug, tier=tier, badge_type='series', is_live=True,
                         required_stages=5, display_series=slug, **badge_kwargs)
    UserBadgeFactory(profile=profile, badge=badge)
    return badge


def test_showcase_rarest_mode_orders_by_rarity(monkeypatch):
    monkeypatch.setattr(collection_service, 'get_earners_ranks', lambda slugs, pid: {})
    profile = ProfileFactory()
    _earn(profile, 'common', 1, rarity_pct=40.0)
    _earn(profile, 'rare', 1, rarity_pct=2.0)

    showcase = build_collection_context(profile)['showcase']

    rarest = next(m for m in showcase if m['mode'] == 'rarest')
    assert [f['series_name'] for f in rarest['frames']] == ['rare', 'common']  # 2% before 40%


def test_showcase_platinum_mode_only_appears_with_a_top_tier_earn(monkeypatch):
    monkeypatch.setattr(collection_service, 'get_earners_ranks', lambda slugs, pid: {})
    profile = ProfileFactory()
    _earn(profile, 'bronze-only', 1, rarity_pct=10.0)

    modes = {m['mode'] for m in build_collection_context(profile)['showcase']}
    assert 'rarest' in modes and 'platinum' not in modes   # nothing tier-4 earned yet

    _earn(profile, 'the-plat', 4, rarity_pct=1.0)
    showcase = build_collection_context(profile)['showcase']
    plat = next(m for m in showcase if m['mode'] == 'platinum')
    assert [f['tier'] for f in plat['frames']] == ['platinum']  # only the tier-4 earn


def test_showcase_is_empty_without_any_earned_badge():
    profile = ProfileFactory()
    _series('rs-x')   # all unearned

    assert build_collection_context(profile)['showcase'] == []


def test_showcase_caps_each_mode_at_six(monkeypatch):
    monkeypatch.setattr(collection_service, 'get_earners_ranks', lambda slugs, pid: {})
    profile = ProfileFactory()
    for i in range(8):
        _earn(profile, f'rs-{i}', 1, rarity_pct=float(i))

    rarest = next(m for m in build_collection_context(profile)['showcase'] if m['mode'] == 'rarest')

    assert len(rarest['frames']) == 6  # N_SHOW cap


# --- the Case: Chase (closest to complete, one per series) --------------------


def test_chase_orders_by_progress_and_dedupes_per_series(monkeypatch):
    monkeypatch.setattr(collection_service, 'get_earners_ranks', lambda slugs, pid: {})
    profile = ProfileFactory()
    a = _series('ch-a', 'series')   # required_stages=5
    b = _series('ch-b', 'series')
    UserBadgeProgressFactory(profile=profile, badge=a[0], completed_concepts=4)  # 0.8
    UserBadgeProgressFactory(profile=profile, badge=b[0], completed_concepts=1)  # 0.2
    # a second in-progress tier of the SAME series must not add a second chase entry
    UserBadgeProgressFactory(profile=profile, badge=a[1], completed_concepts=2)  # 0.4

    chase = build_collection_context(profile)['chase']

    assert [f['series_name'] for f in chase] == ['ch-a', 'ch-b']  # higher fraction first
    assert chase[0]['tier'] == 'bronze'                            # the closest tier of ch-a wins


def test_chase_is_empty_without_progress():
    profile = ProfileFactory()
    _series('rs-x')   # no UserBadgeProgress rows

    assert build_collection_context(profile)['chase'] == []


def test_chase_caps_at_four(monkeypatch):
    monkeypatch.setattr(collection_service, 'get_earners_ranks', lambda slugs, pid: {})
    profile = ProfileFactory()
    for i in range(6):
        s = _series(f'ch-{i}', 'series')
        UserBadgeProgressFactory(profile=profile, badge=s[0], completed_concepts=i + 1)

    chase = build_collection_context(profile)['chase']

    assert len(chase) == 4  # N_CHASE cap


def test_case_template_renders_medallions_showcase_chase_and_tablist(monkeypatch):
    """The Case template renders the medallion grid plus the Showcase/Chase sections, and wires the
    set tabs to their panels (role=tab -> aria-controls -> role=tabpanel) for screen readers."""
    from django.template.loader import render_to_string

    monkeypatch.setattr(collection_service, 'get_earners_ranks', lambda slugs, pid: {})
    profile = ProfileFactory()
    badges = _series('rs-case')
    UserBadgeFactory(profile=profile, badge=badges[0])                           # earned -> Showcase
    UserBadgeProgressFactory(profile=profile, badge=badges[1], completed_concepts=3)  # in-progress -> Chase

    html = render_to_string('components/collection_case.html', build_collection_context(profile))

    assert 'pp-med' in html                                   # medallion component rendered
    assert 'pp-showcase' in html and 'pp-chase' in html       # both top-of-Case sections present
    # tablist wiring: the tab controls a panel that points back at the tab.
    assert 'id="case-tab-series"' in html
    assert 'aria-controls="case-panel-series"' in html
    assert 'id="case-panel-series"' in html
    assert 'role="tabpanel"' in html and 'aria-labelledby="case-tab-series"' in html


# --- list view (the sortable/filterable sibling of the binder) ----------------


def test_list_badges_flatten_every_frame_with_theme_and_palette():
    profile = ProfileFactory()
    _series('rs-x', 'series')
    _series('fr-x', 'franchise')

    ctx = build_collection_context(profile)

    assert len(ctx['list_badges']) == 8  # same set as the binder, flattened
    for b in ctx['list_badges']:
        assert b['theme'] and b['palette']        # section context attached
        assert b['dom_id'].startswith('card-')
        assert b['series_name']                   # carries the frame fields


def test_themes_are_distinct_sections_in_canonical_order():
    profile = ProfileFactory()
    _series('ev-x', 'event')
    _series('rs-x', 'series')

    themes = build_collection_context(profile)['themes']

    # _SECTION_ORDER puts series before event; each theme carries its palette.
    assert [t['name'] for t in themes] == ['Series', 'Events']
    assert all(t['palette'] for t in themes)


def test_build_failure_degrades_to_empty_context(monkeypatch):
    """A failure inside the set build must degrade to an empty album, not raise a 500."""
    monkeypatch.setattr(
        collection_service, '_build_sets',
        lambda profile, sort: (_ for _ in ()).throw(RuntimeError('boom')),
    )
    profile = ProfileFactory()
    _series('rs-x')

    ctx = build_collection_context(profile)

    assert ctx['binder_sets'] == []
    assert ctx['total_pages'] == 0
