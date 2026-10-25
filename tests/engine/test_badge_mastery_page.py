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
    assert 'hold both editions, master them all for Series Master' in _page(client, series)

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


def test_the_masters_list_names_the_first_few_in_order_and_counts_the_rest(client):
    from trophies.services.badge_leaderboards import MASTERS_SHOWN

    series, badges, _ = _series((1, [ULTRA]), legacy=False)
    base = timezone.now() - timedelta(days=60)
    masters = [ProfileFactory(is_linked=True, psn_username=f'm{i:02d}') for i in range(MASTERS_SHOWN + 2)]
    for i, m in enumerate(masters):
        UserGroupBadge.objects.create(profile=m, group_badge=badges['ultra-hd'], is_holo=True, is_linked=True,
                                      mastered_at=base + timedelta(days=i))
    body = _page(client, series)
    shown = [m for m in masters if reverse('profile_detail', args=[m.psn_username]) in body]
    assert shown == masters[:MASTERS_SHOWN], 'the first to master it, in order'
    assert '>+2</li>' in body


def test_another_hunters_page_shows_their_mastery_and_set(client):
    series, badges, games = _series((1, [CROSS]))
    other = _hunter(psn_username='Other')
    _finish(other, games[1][0], ago=timedelta(days=2))
    evaluate_and_apply(other)

    client.force_login(ProfileFactory(is_linked=True, psn_username='Viewer').user)
    resp = client.get(reverse('badge_detail_with_profile', args=[series.series_slug, other.psn_username]),
                      HTTP_CF_RAY='8f0000000000abcd-LHR')
    body = resp.content.decode()
    assert resp.status_code == 200
    assert 'Mastered &middot; since' in body and '<b>Full Set</b> &middot; every edition held' in body


def test_a_badge_earned_already_mastered_says_so_in_its_one_moment(rf):
    from django.template import Context, Template

    _, badges, games = _series((1, [ULTRA]), legacy=False)
    hunter = _hunter()
    _finish(hunter, games[1][0])
    evaluate_and_apply(hunter)
    request = rf.get('/career/')
    request.user = hunter.user
    html = Template('{% load moments %}{% moments_overlay %}').render(Context({'request': request}))
    assert html.count('<template data-moment') == 1
    assert 'Badge earned and mastered' in html and 'pp-med--mastered' in html


def test_the_inspector_keeps_rim_and_cast_together_in_every_state():
    """The documented gotcha: the inspector animates the backdrop's filter, so its mastered rules must carry the
    rim AND the grounding cast in rest, is-opening and is-closing, or the pick-up snaps instead of fading."""
    import re

    css = re.sub(r'/\*[\s\S]*?\*/', '', (ROOT / 'static/css/components/badge-inspect.css').read_text(encoding='utf-8'))
    rules = re.findall(r'([^{}]*pp-med--mastered[^{}]*nth-child\(1\)[^{}]*)\{([^}]*)\}', css)
    selectors = ' '.join(sel for sel, _ in rules)
    assert 'is-opening' in selectors and 'is-closing' in selectors and len(rules) >= 2
    for _, body in rules:
        assert 'var(--mast-rim)' in body and 'drop-shadow(var(--med-cast)' in body, body


def test_the_set_line_reads_the_stored_rewards_like_the_hero_reads_its_hold(client):
    """The hero's Mastered line reads the stored hold; the set line must read the stored award, or between
    evaluations the page says "Mastered" above "hold both editions"."""
    series, badges, games = _series((1, [CROSS]), (2, [ULTRA]), (3, [LEGACY]))
    hunter = _hunter()                                   # no progress at all: only the stores say otherwise
    for gb in badges.values():
        UserGroupBadge.objects.create(profile=hunter, group_badge=gb, is_holo=True, is_linked=True,
                                      mastered_at=timezone.now())
    for kind in (SeriesAward.KIND_FULL_SET, SeriesAward.KIND_SERIES_MASTER):
        SeriesAward.objects.create(profile=hunter, series=series, kind=kind, since=timezone.now())

    body = _page(client, series, hunter)
    assert '<b>Series Master</b> &middot; every edition mastered, since' in body
