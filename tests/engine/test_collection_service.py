"""Tests for the Collection Gallery (grouping-badge system).

The Collection (`/collection/`) is the Pursuer's single filter / sort / search wall of the badges they've
ENGAGED with: the live per-platform-group editions (Legacy HD / Ultra HD) of every series they either HOLD an
edition of (a UserGroupBadge) or have STARTED (a SeriesBadgeStanding with progress). Earned editions gleam,
in-progress ones carry THAT edition's own progress. These pin the contract:

- SCOPE: engaged series only (held OR started); untouched live series stay out (discovery lives on Browse).
- STATE per edition: held -> earned (holo when mastered); not-held + THIS edition has partial progress ->
  in_progress; else unearned (an edition the viewer has 0% on -- the series furthest-along would wrongly
  paint it).
- WALL: unearned editions are DROPPED, scoped per series and only where another edition of that series
  survives, so a series can never vanish entirely.
- SUMMARY: held/in-progress counts + a per-EDITION composition (Legacy HD / Ultra HD held counts).
- Live rarity (earned_count vs series pursuers), the "+N this week" recency window, sort options, and a fixed
  query count regardless of badge count (no live eval on the wall).

Per-edition progress is READ from the materialized SeriesBadgeStanding.group_progress read-model (written by the
sync's recompute_standing -- covered by test_badge_xp), so these tests just set group_progress on the standing.
State is derived through the shared badge_xp.edition_display_state, same as the live badge-detail view.
(Replaces the retired per-tier binder/Case suite.)
"""
from datetime import timedelta

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from trophies.models import GroupBadge, PlatformGroup, SeriesBadgeStanding, UserGroupBadge
from trophies.services import collection_service
from trophies.services.collection_service import DEFAULT_SORT, build_collection_context
from tests.factories import (
    BadgeSeriesFactory, GroupBadgeFactory, PlatformGroupFactory, ProfileFactory, StageFactory,
)

pytestmark = pytest.mark.django_db

# The two canonical editions, with the platform routing + display order they ship with in prod.
_EDITIONS = [('legacy-hd', 'Legacy HD', ['PS3', 'PSVITA'], 1), ('ultra-hd', 'Ultra HD', ['PS4', 'PS5'], 2)]


def _series(slug, name=None, badge_type='series', editions=_EDITIONS, live=True):
    """A BadgeSeries with a live GroupBadge per edition. Returns (series, {edition_key: GroupBadge}).
    PlatformGroupFactory get_or_creates on `key`, so the editions are shared across series -- we stamp
    name/platforms/sort_order explicitly so the values hold no matter which series created the group first."""
    series = BadgeSeriesFactory(series_slug=slug, name=name or slug, badge_type=badge_type)
    groups = {}
    for key, gname, platforms, order in editions:
        pg = PlatformGroupFactory(key=key)
        PlatformGroup.objects.filter(pk=pg.pk).update(name=gname, platforms=platforms, sort_order=order)
        pg.refresh_from_db()
        groups[key] = GroupBadgeFactory(series=series, platform_group=pg, is_live=live)
    return series, groups


def _hold(profile, gb, is_holo=False, earned_at=None):
    ugb = UserGroupBadge.objects.create(profile=profile, group_badge=gb, is_holo=is_holo)
    if earned_at is not None:
        UserGroupBadge.objects.filter(pk=ugb.pk).update(earned_at=earned_at)
    return ugb


def _standing(profile, slug, bp=0, xp=100, group_progress=None):
    """A SeriesBadgeStanding = the series is engaged (scope). Pass group_progress={edition_key: [cleared, gating]}
    to give an edition partial progress (the read-model the collection reads)."""
    return SeriesBadgeStanding.objects.create(
        profile=profile, series_slug=slug, xp=xp, progress_bp=bp, group_progress=group_progress or {},
    )


def _pursuers(slug, n):
    """n LINKED profiles with a standing -- the rarity denominator is the whole community now."""
    for _ in range(n):
        _standing(ProfileFactory(is_linked=True), slug, bp=1000)


def _frames_by_edition(ctx):
    return {f['group_key']: f for f in ctx['list_badges']}


# --- scope ---------------------------------------------------------------------


def test_scope_includes_held_and_started_series():
    """The wall shows series you HOLD an edition of AND series you've only STARTED (a standing row)."""
    profile = ProfileFactory()
    _, held = _series('rs-held')
    _hold(profile, held['ultra-hd'])
    _series('rs-started')
    _standing(profile, 'rs-started', bp=5000)

    ctx = build_collection_context(profile)

    assert {f['series_slug'] for f in ctx['list_badges']} == {'rs-held', 'rs-started'}
    # rs-held shows only the edition actually held (its other one is untouched inside a series in play);
    # rs-started has no per-edition entries at all, so it keeps both rather than vanishing.
    assert len(ctx['list_badges']) == 3


def test_untouched_live_series_excluded():
    """A live series the viewer has never engaged never appears -- discovery lives on Browse, not here."""
    profile = ProfileFactory()
    _, held = _series('rs-mine')
    _hold(profile, held['ultra-hd'])
    _series('rs-stranger')   # live, never touched

    ctx = build_collection_context(profile)

    assert {f['series_slug'] for f in ctx['list_badges']} == {'rs-mine'}


def test_non_live_group_excluded():
    """A non-live edition drops out even for an engaged series."""
    profile = ProfileFactory()
    series, groups = _series('rs-live')
    _standing(profile, 'rs-live', bp=4000)
    GroupBadge.objects.filter(id=groups['legacy-hd'].id).update(is_live=False)

    ctx = build_collection_context(profile)

    assert {f['group_key'] for f in ctx['list_badges']} == {'ultra-hd'}   # only the live edition


def test_empty_when_nothing_engaged():
    profile = ProfileFactory()
    _series('rs-a')
    _series('rs-b')

    ctx = build_collection_context(profile)

    assert ctx['list_badges'] == []
    assert ctx['summary']['total'] == 0
    assert ctx['themes'] == []


# --- per-edition state ---------------------------------------------------------


def test_held_edition_is_earned():
    profile = ProfileFactory()
    _, groups = _series('rs-earn')
    _hold(profile, groups['ultra-hd'])

    frames = _frames_by_edition(build_collection_context(profile))

    assert frames['ultra-hd']['state'] == 'earned'
    assert frames['ultra-hd']['progress_pct'] == 100


def test_edition_progress_is_per_edition_not_series_level():
    """THE fix: a started series shows each edition's OWN progress (from the read-model), not the series
    furthest-along. Progress on Ultra HD but 0% on Legacy HD -> Ultra in_progress, and Legacy is off the
    wall entirely rather than being painted with Ultra's progress. Matches what the inspect modal shows."""
    profile = ProfileFactory()
    _series('rs-wip')
    _standing(profile, 'rs-wip', group_progress={'ultra-hd': [3, 5]})   # only Ultra HD has progress

    frames = _frames_by_edition(build_collection_context(profile))

    assert frames['ultra-hd']['state'] == 'in_progress'
    assert frames['ultra-hd']['progress_pct'] == 60                     # 3 / 5
    assert 'legacy-hd' not in frames                                    # untouched -> dropped, never painted


def test_in_progress_frame_carries_stage_count():
    """An in-progress edition surfaces its stage count (done / total) from the read-model so the medallion
    can render "X / Y stages"."""
    profile = ProfileFactory()
    _series('rs-stages')
    _standing(profile, 'rs-stages', group_progress={'ultra-hd': [3, 5]})

    frames = _frames_by_edition(build_collection_context(profile))

    assert frames['ultra-hd']['state'] == 'in_progress'
    assert frames['ultra-hd']['stages_done'] == 3
    assert frames['ultra-hd']['stages_total'] == 5
    assert 'legacy-hd' not in frames        # untouched edition of a series in play -- off the wall entirely


def test_held_edition_does_not_lend_completion_to_the_other():
    """A held edition reads earned, and the OTHER edition does not borrow its completion (the
    series-furthest-along bug). That edition is absent from the read-model, so it is untouched -- and an
    untouched edition of a series already in play is dropped from the wall. Absence is now how this
    invariant is expressed, and it is the stronger form: the edition cannot be painted at all."""
    profile = ProfileFactory()
    _, groups = _series('rs-cross')
    _hold(profile, groups['ultra-hd'])
    _standing(profile, 'rs-cross', group_progress={'ultra-hd': [5, 5]})   # ultra complete; legacy not in the map

    frames = _frames_by_edition(build_collection_context(profile))

    assert frames['ultra-hd']['state'] == 'earned'
    assert 'legacy-hd' not in frames, 'the untouched edition is on the wall borrowing the held one'


def test_holo_hold_is_holographic():
    profile = ProfileFactory()
    _, groups = _series('rs-holo')
    _hold(profile, groups['ultra-hd'], is_holo=True)

    frames = _frames_by_edition(build_collection_context(profile))

    assert frames['ultra-hd']['is_holographic'] is True
    assert 'legacy-hd' not in frames        # the unheld, untouched edition is not on the wall to shimmer


# --- summary -------------------------------------------------------------------


def test_summary_counts_and_pct_use_the_full_catalog():
    """earned/in_progress count the engaged wall, but the completion pct + `catalog_total` denominator are the
    WHOLE live badge catalog (not just engaged) -- an untouched live series still swells the denominator."""
    profile = ProfileFactory()
    _, a = _series('rs-a')
    _series('rs-b')
    _series('rs-untouched')                        # +2 live group badges in the catalog, never engaged
    _hold(profile, a['ultra-hd'])                  # 1 earned
    _standing(profile, 'rs-a')                     # engages a (legacy edition not in the read-model -> unearned)
    _standing(profile, 'rs-b', group_progress={'ultra-hd': [3, 10], 'legacy-hd': [3, 10]})  # both of b in progress

    summary = build_collection_context(profile)['summary']

    # The WALL: a's held edition + both of b's. a's legacy edition is untouched inside a series in play,
    # so it is dropped -- `total` counts what is shown, never more.
    assert summary['total'] == 3
    assert summary['catalog_total'] == 6  # all 3 series x 2 editions live
    assert summary['earned'] == 1
    assert summary['in_progress'] == 2    # both of b's editions
    assert summary['pct'] == 17           # 1 / 6 -> catalog completion, not engaged completion


def test_summary_holo_counts_mastered_holds():
    """summary.holo counts held editions flagged holo (mastered 100%); plain holds and unheld don't count."""
    profile = ProfileFactory()
    _, groups = _series('rs-holo')
    _hold(profile, groups['ultra-hd'], is_holo=True)
    _hold(profile, groups['legacy-hd'], is_holo=False)

    assert build_collection_context(profile)['summary']['holo'] == 1


def test_summary_editions_composition():
    """The header composition counts HELD badges per EDITION (Legacy HD / Ultra HD), sorted by the group's
    sort_order, each carrying its medallion metal for the dot colour."""
    profile = ProfileFactory()
    _, a = _series('rs-a')
    _, b = _series('rs-b')
    _hold(profile, a['ultra-hd'])
    _hold(profile, b['ultra-hd'])
    _hold(profile, b['legacy-hd'])

    editions = build_collection_context(profile)['summary']['editions']

    assert [e['key'] for e in editions] == ['legacy-hd', 'ultra-hd']   # sort_order 1 then 2
    counts = {e['key']: e['count'] for e in editions}
    assert counts == {'legacy-hd': 1, 'ultra-hd': 2}
    assert all(e['label'] and e['tier'] for e in editions)


def test_editions_composition_includes_a_zero_held_edition():
    """Every ENGAGED edition appears in the composition, even one you hold nothing of -- so the header stat grid
    is stable (a started series still contributes both its Legacy HD and Ultra HD cards, 0 allowed)."""
    profile = ProfileFactory()
    _, groups = _series('rs-partial')
    _hold(profile, groups['ultra-hd'])             # hold only Ultra HD
    _standing(profile, 'rs-partial', bp=10000)     # Legacy HD edition -> unearned, 0 held

    editions = build_collection_context(profile)['summary']['editions']

    assert {e['key'] for e in editions} == {'legacy-hd', 'ultra-hd'}   # both present
    assert {e['key']: e['count'] for e in editions} == {'legacy-hd': 0, 'ultra-hd': 1}


def test_recent_window_flags_new_and_counts():
    """summary.recent + per-frame is_new count only earns within the recent window; older earns don't."""
    profile = ProfileFactory()
    _, fresh = _series('rs-fresh')
    _, stale = _series('rs-stale')
    _hold(profile, fresh['ultra-hd'])   # earned_at defaults to now -> recent
    _hold(profile, stale['ultra-hd'], earned_at=timezone.now() - timedelta(days=collection_service._RECENT_DAYS + 1))

    ctx = build_collection_context(profile)
    by_slug = {f['series_slug']: f for f in ctx['list_badges'] if f['state'] == 'earned'}

    assert ctx['summary']['recent'] == 1
    assert by_slug['rs-fresh']['is_new'] is True
    assert by_slug['rs-stale']['is_new'] is False


# --- rarity + sort + themes ----------------------------------------------------


def test_rarity_from_earned_count_over_the_community():
    """Live rarity is earned_count over the whole community -- 1 of 4 -> 25% -> common."""
    profile = ProfileFactory(is_linked=True)
    _, groups = _series('rs-rare')
    _hold(profile, groups['ultra-hd'])
    _standing(profile, 'rs-rare', bp=10000)   # the viewer is a pursuer too (holders always have a standing)
    GroupBadge.objects.filter(id=groups['ultra-hd'].id).update(earned_count=1)
    _pursuers('rs-rare', 3)   # +3 others -> a community of 4; 1 of 4 earned -> 25%

    frame = _frames_by_edition(build_collection_context(profile))['ultra-hd']

    assert frame['rarity_pct'] == 25.0
    assert frame['rarity_class'] == 'common'


def test_sort_options_and_invalid_fallback():
    profile = ProfileFactory()
    _series('rs')

    ctx = build_collection_context(profile, sort='bogus')

    assert ctx['sort'] == DEFAULT_SORT == 'progress'
    keys = {k for k, _ in ctx['sort_options']}
    assert {'progress', 'earned', 'rarity', 'series', 'edition', 'set_number'} <= keys


def test_themes_reflect_badge_types():
    profile = ProfileFactory()
    _series('rs-x', badge_type='series')
    _series('fr-x', badge_type='franchise')
    _standing(profile, 'rs-x', bp=4000)
    _standing(profile, 'fr-x', bp=4000)

    ctx = build_collection_context(profile)

    names = {t['name'] for t in ctx['themes']}
    assert names == {'Series', 'Franchises'}
    assert all(t['palette'] for t in ctx['themes'])


# --- resilience + whale-safety -------------------------------------------------


def test_build_failure_degrades_to_empty_context(monkeypatch):
    """A failure inside the build must degrade to an empty wall, never raise a 500."""
    monkeypatch.setattr(
        collection_service, '_engaged_series',
        lambda profile: (_ for _ in ()).throw(RuntimeError('boom')),
    )
    profile = ProfileFactory()
    _series('rs-x')

    ctx = build_collection_context(profile)

    assert ctx['list_badges'] == []
    assert ctx['summary']['total'] == 0


def test_own_reads_are_constant_regardless_of_badge_count():
    """The collection's OWN bookkeeping reads (holds / standings / participants / rank / earned-at / catalog
    total) stay flat regardless of badge count -- no per-badge N+1. (The live per-edition eval, stubbed here,
    is bounded by the engaged catalog and measured separately via profile_render, not asserted for query count.)"""
    profile = ProfileFactory()
    _series('rs-base')
    _standing(profile, 'rs-base', bp=4000)
    build_collection_context(profile)   # warm-up (first-call one-offs)

    with CaptureQueriesContext(connection) as small:
        build_collection_context(profile)

    for i in range(6):
        _series(f'rs-more-{i}', badge_type='series')
        _standing(profile, f'rs-more-{i}', bp=3000)
    for i in range(3):
        _series(f'fr-{i}', badge_type='franchise')
        _standing(profile, f'fr-{i}', bp=3000)

    with CaptureQueriesContext(connection) as large:
        build_collection_context(profile)

    assert len(large) == len(small)


# --- the Gallery template ------------------------------------------------------


def test_gallery_template_renders_edition_and_state_chips():
    from django.template.loader import render_to_string

    profile = ProfileFactory()
    _, groups = _series('rs-gal')
    _hold(profile, groups['ultra-hd'])
    _standing(profile, 'rs-gal', bp=10000)

    html = render_to_string('components/collection_gallery.html', build_collection_context(profile))

    assert 'pp-gallery__grid' in html and 'pp-gallery__card' in html
    assert html.count('data-gallery-cell') == 1               # only the held edition; legacy is untouched
    # Edition filter chips (replacing the retired tier chips) + the full State set incl "Not earned".
    # That chip was briefly removed on the claim that untouched editions are never built. They still are,
    # for a series with no progress anywhere (see the per-series scoping test), and without the chip those
    # cards could be neither filtered for nor filtered out.
    assert 'data-filter-edition="legacy-hd"' in html and 'data-filter-edition="ultra-hd"' in html
    assert 'data-filter-tier' not in html                     # tier chips are gone
    assert 'data-filter-state="unearned"' in html
    assert 'data-edition="ultra-hd"' in html                  # each cell tags its edition (for filter/sort)
    assert 'data-gallery-sort' in html and 'data-modal-url' in html
    assert 'pp-gallery__check' in html                        # the held edition wears the earned tick


def test_gallery_in_progress_cell_carries_stage_count_in_the_caption():
    """An in-progress cell carries its "X / Y" stage count as data-stages (the caption slot fills it in as
    "3 / 5 stages" via collection.js). The medallion's own count stays suppressed (`no_count`): the caption
    is already the stage figure's home, and a second copy under the bar repeats it a few pixels away."""
    from django.template.loader import render_to_string

    profile = ProfileFactory()
    _series('rs-ip')
    _standing(profile, 'rs-ip', group_progress={'ultra-hd': [3, 5]})

    html = render_to_string('components/collection_gallery.html', build_collection_context(profile))

    assert 'data-stages="3 / 5"' in html   # the caption source
    assert 'pp-med__count' not in html     # the caption states it; a second copy under the bar is a repeat


def test_an_unstarted_edition_of_a_series_in_play_is_dropped():
    """The reversal. An untouched edition used to be kept as a "waiting mount" carrying "0 / 4 stages", on
    the theory that was the most motivating place for the figure. It read as clutter instead: engagement is
    per SERIES, so starting one edition mounts every other edition of that series, and each new
    compatibility grouping multiplies what the hunter never asked to see.
    """
    from django.template.loader import render_to_string

    profile = ProfileFactory()
    _series('rs-unstarted')
    # What recompute_standing writes: an entry per EARNABLE edition, started or not.
    _standing(profile, 'rs-unstarted', group_progress={'ultra-hd': [2, 5], 'legacy-hd': [0, 4]})

    ctx = build_collection_context(profile)
    frames = _frames_by_edition(ctx)

    assert frames['ultra-hd']['state'] == 'in_progress'
    assert 'legacy-hd' not in frames, 'the untouched edition is still on the wall'

    html = render_to_string('components/collection_gallery.html', ctx)
    assert 'data-stages="0 / 4"' not in html


def test_a_series_with_no_progress_anywhere_keeps_all_its_editions():
    """The filter is scoped PER SERIES, and only where something in that series survives.

    Dropping every untouched edition unconditionally also deleted whole series: a standing whose
    `group_progress` is empty -- stale, or written before that read-model materialized every earnable
    edition -- reads as untouched on every edition, so a series the hunter genuinely has progress in would
    silently vanish from their collection. Losing a series is a worse failure than showing a spare edition.
    """
    profile = ProfileFactory()
    _series('rs-nomap')
    _standing(profile, 'rs-nomap', bp=4000)      # engaged, but no per-edition entries

    frames = _frames_by_edition(build_collection_context(profile))

    assert set(frames) == {'legacy-hd', 'ultra-hd'}, 'an engaged series vanished from the wall'


def test_an_earned_edition_carries_a_full_stage_count():
    """The permanent meter needs a figure in every state, so an earned badge reads N / N behind a full bar.

    Taken from `gating`, never from the read-model's `cleared`: edition_display_state short-circuits on
    `held` and returns 100 without reading either number, so nothing has ever depended on what `cleared`
    holds for a held row. A row that stopped tracking at hold would render a full bar over "0 / 5".
    """
    profile = ProfileFactory()
    _, groups = _series('rs-earned')
    _hold(profile, groups['ultra-hd'])
    _standing(profile, 'rs-earned', group_progress={'ultra-hd': [0, 5]})   # deliberately stale numerator

    frame = _frames_by_edition(build_collection_context(profile))['ultra-hd']

    assert frame['state'] == 'earned'
    assert (frame['stages_done'], frame['stages_total']) == (5, 5)


def test_the_chase_count_is_per_edition_never_the_series_stage_count():
    """The regression this file exists to prevent.

    A stage only GATES an edition if some game in it runs on that platform group, so the series' Stage
    count is not the edition's denominator. Deriving it that way (briefly shipped) told a Legacy HD
    hunter "0 / 8" for a series whose extra stages are PS5-only -- and then SHRANK it to "1 / 5" the
    moment they cleared one, a goal that gets smaller as you approach it. Only the read-model knows."""
    profile = ProfileFactory()
    _series('rs-split')
    for n in (1, 2, 3, 4, 5, 6, 7, 8):          # eight stages in the series...
        StageFactory(series_slug='rs-split', stage_number=n)
    # ...but only five of them gate the Legacy HD edition, which is what the engine materialized.
    _standing(profile, 'rs-split', group_progress={'legacy-hd': [0, 5], 'ultra-hd': [0, 8]})

    frames = _frames_by_edition(build_collection_context(profile))

    assert frames['legacy-hd']['chase_total'] == 5, 'must be the edition gating count, not 8 stages'
    assert frames['ultra-hd']['chase_total'] == 8


def test_an_edition_that_cannot_be_earned_advertises_no_chase():
    """gating_count == 0 means the badge is not offered in that platform group at all (its games are
    delisted or unobtainable there), so recompute_standing stores no entry. The card must stay blank
    rather than inviting a hunter into a chase with no finish line."""
    profile = ProfileFactory()
    _series('rs-unoffered')
    for n in (1, 2, 3):
        StageFactory(series_slug='rs-unoffered', stage_number=n)
    _standing(profile, 'rs-unoffered', group_progress={'ultra-hd': [1, 3]})   # legacy-hd absent

    frames = _frames_by_edition(build_collection_context(profile))

    # Absence is the strongest form of "advertises no chase": the unoffered edition is untouched inside a
    # series in play, so it never reaches the wall to invite anyone into a chase with no finish line.
    assert 'legacy-hd' not in frames
    assert frames['ultra-hd']['state'] == 'in_progress'


def test_a_malformed_read_model_row_stays_blank():
    """The shape guard degrades a corrupt entry to "no progress for THIS badge, never raise". It must
    not then be rescued into a confident wrong number by a fallback."""
    profile = ProfileFactory()
    _series('rs-corrupt')
    StageFactory(series_slug='rs-corrupt', stage_number=1)
    _standing(profile, 'rs-corrupt', group_progress={'ultra-hd': 'nonsense'})

    frames = _frames_by_edition(build_collection_context(profile))

    assert frames['ultra-hd']['chase_total'] == 0


def test_an_earned_edition_has_nothing_left_to_chase():
    """The chase count is about what is LEFT. An earned badge must not caption "5 / 5 stages" where its
    rarity grade belongs."""
    profile = ProfileFactory()
    _, groups = _series('rs-done')
    _hold(profile, groups['ultra-hd'])
    _standing(profile, 'rs-done', group_progress={'ultra-hd': [5, 5]})

    frame = _frames_by_edition(build_collection_context(profile))['ultra-hd']

    assert frame['state'] == 'earned'
    assert (frame['chase_done'], frame['chase_total']) == (0, 0)


def test_collection_page_is_a_single_gallery_with_stat_grid(client):
    """The page ships as one Gallery: no Case, no view toggle -- a Career-style .scard stat grid in the header
    (editions + in-progress + holographic) over the wall, with the full catalog as the collected denominator."""
    profile = ProfileFactory(is_linked=True)
    _, a = _series('rs-page')
    _hold(profile, a['ultra-hd'])
    _series('rs-catalog-only')   # unengaged -> only swells the "N of M collected" denominator
    client.force_login(profile.user)

    html = client.get(reverse('badge_collection')).content.decode()

    assert 'class="pp-minibar"' in html and 'data-minibar-coll-filters' in html
    assert 'scard__value' in html and 'In progress' in html and 'Holographic' in html   # the stat grid
    assert '/ 4</span>' in html                               # earned 1 of the full 4-badge catalog (2 series x 2)
    assert 'data-edition-jump' not in html                    # the old edition-jump chips are gone
    assert 'pp-coll-tier' not in html                         # the old tier-composition row is gone
    assert 'data-collection-view' not in html                 # the Case/Gallery view toggle is gone


# --- the detail modal ----------------------------------------------------------


def test_collection_badge_modal_renders_group_modal(client):
    profile = ProfileFactory(is_linked=True)
    _, groups = _series('rs-modal')
    _hold(profile, groups['ultra-hd'])
    client.force_login(profile.user)

    resp = client.get(reverse('collection_badge_modal', args=[groups['ultra-hd'].id]))

    assert resp.status_code == 200
    html = resp.content.decode()
    assert 'pp-med' in html   # the group medallion detail rendered
    # The collection modal offers a jump to the badge detail page, deep-linked to THIS edition's tab.
    assert f'href="{reverse("badge_detail", args=["rs-modal"])}?group=ultra-hd"' in html
    assert 'pp-bdetail__link' in html


def test_collection_badge_modal_404_for_unknown(client):
    profile = ProfileFactory(is_linked=True)
    client.force_login(profile.user)

    resp = client.get(reverse('collection_badge_modal', args=[999999]))

    assert resp.status_code == 404


def test_the_wall_shows_a_permanent_meter_with_a_full_one_marked():
    """The bar is on every card now, not just in-progress ones, so it reads as a column down the wall.

    A full bar is marked `is-full` rather than just sitting at 100%: a gauge pinned at max and a finished
    thing look identical otherwise, and on a wall that is mostly earned badges the distinction IS the
    information.
    """
    from django.template.loader import render_to_string

    profile = ProfileFactory()
    _, groups = _series('rs-meter')
    _hold(profile, groups['ultra-hd'])
    _standing(profile, 'rs-meter', group_progress={'ultra-hd': [5, 5]})

    html = render_to_string('components/collection_gallery.html', build_collection_context(profile))

    assert 'pp-med__meter' in html, 'an earned badge has no meter -- the bar is not permanent'
    # Counted, not just present: a bug that stamped `is-full` on EVERY meter would satisfy a bare
    # substring check while destroying the only distinction the marking exists to make.
    assert html.count('is-full') == 1, 'the full marking is missing or not specific to the completed bar'
    assert 'pp-med__count' not in html, 'the stage count belongs to the caption, not a second line under the bar'
    # The earned cell carries the stage total so the caption can say what the badge TOOK, not just when.
    assert 'data-stage-total="5"' in html


def test_other_medallion_surfaces_keep_the_in_progress_only_meter():
    """`always_meter` is opt-in, so this stays a property of the two collection walls. Badge detail, the
    showcases and the Case mix medallions with other content, where a full bar under every earned badge is
    noise rather than a column to read."""
    from django.template.loader import render_to_string

    frame = {'tier': 'gold', 'state': 'earned', 'art_layers': [], 'stages_done': 5, 'stages_total': 5}

    plain = render_to_string('components/badge_medallion.html', {'frame': frame})
    opted = render_to_string('components/badge_medallion.html', {'frame': frame, 'always_meter': True})

    assert 'pp-med__meter' not in plain, 'the permanent meter leaked onto every medallion on the site'
    assert 'pp-med__meter' in opted


def test_every_meter_state_has_a_colour_to_draw_with():
    """A completed badge rendered an EMPTY bar, and no render test could have caught it.

    `--meter-c` was declared only on `.pp-med--in_progress` / `--maintenance`, the two states that could
    draw a meter before `always_meter` let an earned badge draw one. An undefined custom property does not
    fall back to a previous declaration -- it makes the whole value invalid -- so every `color-mix()`
    referencing it dropped the fill's `background` entirely. Full markup, correct width, no colour.

    Pinned on the BASE selector: any future state that draws a meter inherits a colour rather than
    silently rendering nothing.
    """
    import re
    from pathlib import Path

    css = (Path(__file__).resolve().parents[2]
           / 'static' / 'css' / 'components' / 'badge-medallion.css').read_text(encoding='utf-8')
    css = re.sub(r'/\*[\s\S]*?\*/', '', css)     # the comment above the rule explains the bug

    # ALL base rules, not the first: `.pp-med` is declared several times (layout, sizing, tokens), and
    # `re.search` would have tested whichever came first.
    bases = re.findall(r'(?m)^\.pp-med\s*\{([^}]*)\}', css)
    assert any('--meter-c' in b for b in bases), (
        '--meter-c is not defined on the base medallion, so a state without its own declaration draws an '
        'invisible meter'
    )


def test_the_full_meter_glow_is_not_declared_inside_the_clipped_track():
    """The glow was invisible even though it rendered.

    `.pp-med__meter--smooth` sets `overflow: hidden` to clip the fill to the rounded track -- which clips
    the fill's `box-shadow` with it. A glow declared on the fill is therefore cut off exactly at the bar's
    edge. It belongs on the TRACK, which nothing clips and which at 100% is the same shape as the fill.
    """
    import re
    from pathlib import Path

    css = (Path(__file__).resolve().parents[2]
           / 'static' / 'css' / 'components' / 'badge-medallion.css').read_text(encoding='utf-8')
    css = re.sub(r'/\*[\s\S]*?\*/', '', css)

    track = re.search(r'\.pp-med__meter--smooth\.is-full\s*\{([^}]*)\}', css)
    assert track and 'box-shadow' in track.group(1), (
        'the full-meter glow is not on the track, so the smooth bar clips it away'
    )

    # And it must not be (re)declared on the fill, where it would be clipped.
    for rule in re.findall(r'([^{}]*)\{([^}]*)\}', css):
        sel, body = rule
        if 'is-full' in sel and 'meter-fill' in sel:
            assert 'box-shadow' not in body, (
                'the full glow is back on the clipped fill -- it will render and be cut off'
            )


def test_the_card_gives_the_edition_its_own_line_in_its_tier_colour():
    """Card reads: bar, name, edition, stat. The edition earns a line because both editions of a series can
    sit side by side on this wall, identical but for that word -- and it takes the card's `--tier-c`, so the
    colour separates them before the word is read."""
    from django.template.loader import render_to_string

    profile = ProfileFactory()
    _, groups = _series('rs-ed')
    _hold(profile, groups['ultra-hd'])
    _hold(profile, groups['legacy-hd'])
    _standing(profile, 'rs-ed', group_progress={'ultra-hd': [5, 5], 'legacy-hd': [4, 4]})

    html = render_to_string('components/collection_gallery.html', build_collection_context(profile))

    assert html.count('pp-gallery__edition') == 2, 'the edition does not have its own caption line'
    assert 'Ultra HD' in html and 'Legacy HD' in html
    # The colour comes from the CARD's data-tier via --tier-c, which is why the line needs no per-edition
    # class. Pinned on the two rules that actually supply it: `data-tier` alone is true of every card and
    # always has been, so asserting its presence proved nothing about the colour this test is named for.
    import re
    from pathlib import Path

    css = (Path(__file__).resolve().parents[2]
           / 'static' / 'css' / 'components' / 'collection-gallery.css').read_text(encoding='utf-8')
    assert '.pp-gallery__card[data-tier=' in css, 'nothing defines --tier-c on the card'
    edition = re.search(r'\.pp-gallery__edition\s*\{([^}]*)\}', css)
    assert edition and 'var(--tier-c' in edition.group(1), (
        'the edition line does not take the card tier colour'
    )


def test_the_caption_is_fixed_not_sort_adaptive():
    """Five things on every card, the same regardless of sort: bar, name, edition, stages/date, rarity.

    The stat used to be written by `collection.js` from the active sort key, so the same earned badge read
    "Complete" under the default sort, a rarity grade under most others, and "5 stages - date" only under
    "Recently earned" -- while the profile wall always said the last of those. A card that rewords itself
    when you re-sort is telling you about the control, not about the badge.
    """
    from django.template.loader import render_to_string

    profile = ProfileFactory(is_linked=True)
    _, groups = _series('rs-fixed')
    _hold(profile, groups['ultra-hd'])
    _standing(profile, 'rs-fixed', bp=10000, group_progress={'ultra-hd': [5, 5]})
    # A community to grade against: with none, `rarity_class` is empty and the line correctly renders
    # nothing -- which would have made the rarity assertion below pass or fail on the fixture, not the card.
    GroupBadge.objects.filter(id=groups['ultra-hd'].id).update(earned_count=1)
    _pursuers('rs-fixed', 3)

    html = render_to_string('components/collection_gallery.html', build_collection_context(profile))

    assert '5 stages' in html, 'the stat is not server-rendered'
    assert 'pp-gallery__rarity' in html, 'rarity has no permanent line'
    assert 'Common' in html, 'the rarity line shows no grade'
    # The empty slot the JS used to fill is gone; nothing should be waiting on a script to say what a card is.
    assert 'data-gallery-stat' not in html


def test_the_sort_no_longer_rewrites_what_a_card_says():
    """Pinned in the JS as well: the helpers that composed the adaptive stat are gone, not just unused."""
    from pathlib import Path

    js = (Path(__file__).resolve().parents[2] / 'static' / 'js' / 'collection.js').read_text(encoding='utf-8')

    assert 'statText' not in js
    assert 'data-gallery-stat' not in js, 'applySort still writes the caption'
    # Sorting itself must survive -- only the caption rewriting went.
    assert 'compareBy' in js and 'function applySort' in js


def test_a_badge_nobody_has_earned_shows_the_nudge_not_a_blank():
    """Every card keeps the same shape. A badge with zero earners site-wide gets no GRADE -- `rarity_for`
    withholds one deliberately, since 0% is under every threshold and the arithmetic would call it Mythic --
    so the slot took the site's existing "Be the first" nudge rather than rendering nothing.

    Note rarity grades the BADGE, not the viewer: an in-progress badge others own still shows its real
    grade. Only zero earners produces the nudge.
    """
    from django.template.loader import render_to_string

    profile = ProfileFactory(is_linked=True)
    _series('rs-nobody')
    _standing(profile, 'rs-nobody', bp=4000, group_progress={'ultra-hd': [1, 5]})
    _pursuers('rs-nobody', 3)          # a community exists, but earned_count stays 0

    html = render_to_string('components/collection_gallery.html', build_collection_context(profile))

    assert 'Be the first' in html, 'an ungraded badge leaves the rarity slot empty'
    assert 'pp-gallery__rarity--nudge' in html, 'the nudge is styled as though it were a grade'


def test_an_unearned_badge_others_own_still_shows_its_real_grade():
    """The distinction the nudge must not swallow: not-earned-BY-YOU is not the same as not-earned-by-anyone,
    and a card in progress on a badge 25% of the community holds should say Common, not "Be the first"."""
    from django.template.loader import render_to_string

    profile = ProfileFactory(is_linked=True)
    _, groups = _series('rs-theirs')
    _standing(profile, 'rs-theirs', bp=4000, group_progress={'ultra-hd': [1, 5]})
    GroupBadge.objects.filter(id=groups['ultra-hd'].id).update(earned_count=1)
    _pursuers('rs-theirs', 3)          # 1 of 4 -> 25% -> common

    html = render_to_string('components/collection_gallery.html', build_collection_context(profile))

    assert 'Common' in html
    assert 'Be the first' not in html, 'a graded badge the viewer has not earned shows the nudge'
