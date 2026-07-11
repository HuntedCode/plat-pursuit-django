"""Tests for the /badges/ Series view -- the per-series ROW list (the default, non-gallery mode).

The discovery cousin of the Browse Gallery: one row per badge SERIES showing the next-tier medallion,
trophy-type counts, and (auth) next-tier progress. These pin the rebuilt contract: the --pp-* row markup,
multi-select badge_type/completion_status filters (OR'd), the infinite-scroll rows partial for XHR page
fetches (+ a past-end fetch returning no rows so the scroller stops), and slug search.
"""
import pytest
from django.urls import reverse

from tests.factories import BadgeFactory, ProfileFactory, UserBadgeFactory

pytestmark = pytest.mark.django_db

SERIES = reverse('badges_list')


def _series(slug, badge_type='series', tiers=(1, 2, 3, 4)):
    return [
        BadgeFactory(series_slug=slug, tier=t, badge_type=badge_type,
                     is_live=True, required_stages=5, display_series=slug)
        for t in tiers
    ]


def test_series_renders_pp_slist_rows(client):
    """The default view renders the rebuilt --pp-* row list, comprehensive (trophy-type grid), each row
    linking to the public badge_detail page."""
    _series('rs-series')

    resp = client.get(SERIES)
    html = resp.content.decode()

    assert resp.status_code == 200
    assert 'pp-slist__row' in html                 # rebuilt row (not the legacy DaisyUI card)
    assert 'pp-slist__trophies' in html            # comprehensive: the 4-col trophy-type grid
    assert '/badges/rs-series/' in html
    assert 'pp-vtoggle' in html                     # shared Series|Gallery toggle present


def test_series_row_emits_tier_name_for_css_accent(client):
    """The row's data-tier must be a tier NAME (bronze/silver/gold/platinum) so series-list.css's
    [data-tier="..."] --tier-c accents match -- not a DaisyUI semantic color (warning/secondary/...),
    which would silently leave every tier the same fallback cyan. Regression for the audit HIGH finding."""
    _series('rs-tiername')

    html = client.get(SERIES).content.decode()

    assert 'data-tier="bronze"' in html
    for semantic in ('data-tier="warning"', 'data-tier="secondary"', 'data-tier="error"', 'data-tier="primary"'):
        assert semantic not in html


def test_series_multi_badge_type_filter_ORs(client):
    """?badge_type=series&badge_type=developer returns BOTH (badge_type__in), not just one."""
    _series('rs-a', badge_type='series')
    _series('dev-b', badge_type='developer')
    _series('fr-c', badge_type='franchise')

    html = client.get(SERIES, {'badge_type': ['series', 'developer']}).content.decode()

    assert '/badges/rs-a/' in html
    assert '/badges/dev-b/' in html
    assert '/badges/fr-c/' not in html             # franchise excluded


def test_series_completion_status_multi_filter(client):
    """?completion_status=completed (auth) keeps only fully-earned series; not-started is excluded."""
    profile = ProfileFactory()
    done = _series('rs-done')
    _series('rs-fresh')
    for b in done:
        UserBadgeFactory(profile=profile, badge=b)   # earn every tier -> completed
    client.force_login(profile.user)

    html = client.get(SERIES, {'completion_status': ['completed']}).content.decode()

    assert '/badges/rs-done/' in html
    assert '/badges/rs-fresh/' not in html


def test_series_earned_row_is_marked(client):
    """A series the viewer has any tier of gets the green is-earned rail."""
    profile = ProfileFactory()
    badges = _series('rs-earn')
    UserBadgeFactory(profile=profile, badge=badges[0])   # bronze earned
    client.force_login(profile.user)

    html = client.get(SERIES).content.decode()

    assert 'pp-slist__row is-earned' in html


def test_series_xhr_returns_bare_rows_partial(client):
    """An InfiniteScroller page fetch (XHR) returns just the rows partial -- no page chrome/toolbar."""
    _series('rs-xhr')

    resp = client.get(SERIES, {'page': '1'}, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
    html = resp.content.decode()

    assert 'pp-slist__row' in html
    assert 'pp-vtoggle' not in html                 # bare partial, no shared chrome
    assert 'id="filter-form"' not in html           # ... and no toolbar form


def test_series_xhr_past_end_returns_no_rows(client):
    """A page fetch past the last page emits NO rows so the scroller sees zero and stops (get_page would
    otherwise clamp to the last page and loop it forever)."""
    _series('rs-end')   # one series, far under a full page

    resp = client.get(SERIES, {'page': '9'}, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
    html = resp.content.decode()

    # No row anchors -> the scroller extracts zero cards and stops. (Assert on the detail link, not the
    # 'pp-slist__row' class -- the empty grid's wrapper is 'pp-slist__rows', which substring-matches.)
    assert '/badges/rs-end/' not in html


def test_series_search_filters_by_slug(client):
    _series('elden-ring-series')
    _series('dark-souls-series')

    html = client.get(SERIES, {'series_slug': 'elden'}).content.decode()

    assert '/badges/elden-ring-series/' in html
    assert '/badges/dark-souls-series/' not in html
