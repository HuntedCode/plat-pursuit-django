"""Mastery, Full Set and Series Master on the surfaces (badge rewards phase 4, owner 2026-10-25): the badge page's
mastered line, mastery progress, masters and set band, the retired section's Mastery note, the medallion's
mastered state everywhere, and "Mastered" no longer meaning holo.
"""
from datetime import timedelta
from pathlib import Path

import pytest
from django.urls import reverse
from django.utils import timezone

from trophies.models import SeriesAward, UserGroupBadge
from trophies.services.badge_apply import evaluate_and_apply
from tests.engine.test_badge_rewards_apply import CROSS, LEGACY, ULTRA, _finish, _hunter, _series
from tests.factories import ProfileFactory

pytestmark = pytest.mark.django_db
ROOT = Path(__file__).resolve().parents[2]


def _page(client, series, profile=None):
    """The badge page, as `profile` sees their own (signed in, in the TEST database), else as a visitor."""
    if profile:
        client.force_login(profile.user)
    url = reverse('badge_detail', kwargs={'series_slug': series.series_slug})
    resp = client.get(url)
    assert resp.status_code == 200, (url, resp.status_code, resp.get('Location'))
    return resp.content.decode()


def test_a_mastered_badge_says_so_with_its_date_and_wears_the_crown(client):
    series, badges, games = _series((1, [ULTRA]), legacy=False)
    hunter = _hunter()
    _finish(hunter, games[1][0], ago=timedelta(days=3))
    evaluate_and_apply(hunter)

    body = _page(client, series, hunter)
    since = UserGroupBadge.objects.get(profile=hunter).mastered_at
    assert f'Mastered &middot; since {since:%b} {since.day}, {since.year}' in body
    assert 'pp-med--mastered' in body and 'mastery-crown.svg' in body
    assert '&#10022; Holographic' not in body, 'Mastered replaces the holo line, which it includes'


def test_a_held_badge_short_of_mastery_shows_how_far(client):
    series, _, games = _series((0, [ULTRA]), (1, [ULTRA]), legacy=False)
    hunter = _hunter()
    _finish(hunter, games[1][0])
    _finish(hunter, games[0][0], full=False)
    evaluate_and_apply(hunter)

    assert 'Mastery: 1 of 2 stages at 100%' in _page(client, series, hunter)


def test_the_page_counts_and_names_the_current_masters_and_only_when_there_are_any(client):
    series, badges, games = _series((1, [ULTRA]), legacy=False)
    assert 'bd2-comm__masters' not in _page(client, series), 'absence is not content'

    masters = [ProfileFactory(is_linked=True, psn_username=f'master{i}') for i in range(2)]
    for m in masters:
        UserGroupBadge.objects.create(profile=m, group_badge=badges['ultra-hd'], is_holo=True, is_linked=True,
                                      mastered_at=timezone.now())
    body = _page(client, series)
    assert 'id="bd2-masters-ultra-hd"' in body and '2</b> masters' in body
    for m in masters:
        assert reverse('profile_detail', args=[m.psn_username]) in body
    assert 'href="#bd2-masters-ultra-hd"' in body, 'the hero tally links to them'


def test_the_set_band_reads_where_the_hunter_is_and_what_it_takes(client):
    series, badges, games = _series((1, [CROSS]), (2, [ULTRA]), (3, [LEGACY]))
    assert 'hold all 2 editions, master them all for Series Master' in _page(client, series)

    hunter = _hunter()
    _finish(hunter, games[1][0])
    _finish(hunter, games[2][0])
    evaluate_and_apply(hunter)
    assert '<b>Full Set</b> &middot; 1 of 2 editions held' in _page(client, series, hunter)

    _finish(hunter, games[3][0], full=False)
    evaluate_and_apply(hunter)
    body = _page(client, series, hunter)
    assert '<b>Full Set</b> &middot; every edition held, since' in body and '(1 so far)' in body
    assert SeriesAward.objects.filter(profile=hunter, kind=SeriesAward.KIND_FULL_SET).exists()


def test_a_one_edition_series_has_no_set_band(client):
    series, _, _ = _series((1, [ULTRA]), legacy=False)
    assert 'bd2-comm__set' not in _page(client, series)


def test_the_no_longer_obtainable_section_says_those_stages_count_toward_mastery(client):
    """Owner, 2026-10-25: stages that cannot be bought any more never gate the badge, only Mastery, and the
    page must say so."""
    series, _, games = _series((1, [ULTRA]), (2, [ULTRA]), legacy=False)
    games[2][0].is_obtainable = False
    games[2][0].save()
    body = _page(client, series)
    assert 'No longer obtainable' in body
    assert 'They do count toward Mastery, which takes every stage at 100%.' in body


def test_mastered_means_mastery_and_holo_says_holographic():
    """Holo was called "mastered" until Mastery existed. The browse cards now say each for what it is."""
    for rel in ('templates/trophies/partials/badge_list/gallery_card.html',
                'templates/trophies/partials/badge_list/badge_list_items.html'):
        src = (ROOT / rel).read_text(encoding='utf-8')
        assert 'Mastered (holographic)' not in src, rel
        assert "{% if card.is_mastered %}Mastered{% elif card.is_holo %}Holographic" in src, rel
