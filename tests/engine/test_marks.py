"""The mark system and the role split.

One denorm (`Profile.display_mark`), one precedence (staff > mod > supporter level), two writers
(reconcile through the profile, CustomUser.save on role changes) -- and the perk it makes true:
the storefront sells 'a supporter mark beside your name, everywhere your name appears', so the
resolution and the surfaces are pinned here.
"""
import colorsys

import pytest
from django.template.loader import render_to_string

from users.constants import SERVICE_MARKS, SUPPORT_TIERS
from users.services.marks import mark_style, resolve_display_mark, worn_supporter_level
from users.services.subscription_service import SubscriptionService
from tests.factories import ProfileFactory, UserFactory

pytestmark = pytest.mark.django_db


# ------------------------------------------------------------------------- the role split ----

def test_admin_role_carries_django_admin_access_and_moderator_does_not():
    """is_staff goes back to meaning exactly 'can log into the Django admin': admins keep it in
    lockstep, a demotion to moderator cannot leave it behind by accident."""
    user = UserFactory()
    user.role = 'admin'
    user.save()
    assert user.is_staff is True

    user.role = 'moderator'
    user.save()
    user.refresh_from_db()
    assert user.is_staff is False
    assert user.is_moderator is True


def test_superusers_keep_staff_regardless_of_role():
    user = UserFactory(is_superuser=True, is_staff=True)
    user.role = 'moderator'
    user.save()
    assert user.is_staff is True


def test_moderators_can_preview_dormant_badge_series(client):
    """The ONE gate the split widens: unpublished badge preview. Everything else stays
    admin-only by his call (the mod toolset is a planned rebuild)."""
    from tests.factories import BadgeSeriesFactory

    series = BadgeSeriesFactory()  # no live group badges -> dormant -> hidden from the public
    mod = UserFactory()
    mod.role = 'moderator'
    mod.save()

    from django.urls import reverse
    url = reverse('badge_detail', kwargs={'series_slug': series.series_slug})

    client.force_login(mod)
    response = client.get(url)
    assert response.status_code == 200, 'a moderator could not preview a dormant series'

    client.force_login(UserFactory())
    assert client.get(url).status_code == 404


# ------------------------------------------------------------------------- the resolution ----

def test_precedence_is_staff_then_mod_then_supporter():
    user = UserFactory(premium_tier='patron')

    user.role = 'admin'
    assert resolve_display_mark(user, is_premium=True) == 'staff'
    user.role = 'moderator'
    assert resolve_display_mark(user, is_premium=True) == 'mod'
    user.role = ''
    assert resolve_display_mark(user, is_premium=True) == 'patron'
    assert resolve_display_mark(user, is_premium=False) == ''


def test_legacy_tiers_resolve_through_the_grandfathered_map():
    user = UserFactory(premium_tier='supporter')
    assert resolve_display_mark(user, is_premium=True) == worn_supporter_level('supporter')
    assert worn_supporter_level('supporter') == 'sponsor'


def test_the_denorm_follows_activation_and_deactivation():
    profile = ProfileFactory()
    user = profile.user

    SubscriptionService.activate_subscription(user, 'benefactor', 'stripe')
    profile.refresh_from_db()
    assert profile.display_mark == 'benefactor'

    SubscriptionService.deactivate_subscription(user, 'stripe')
    profile.refresh_from_db()
    assert profile.display_mark == ''


def test_a_role_outranks_premium_and_a_demotion_restores_the_level():
    """His rule: many staff also pay; the service mark overrides while held, and taking the role
    away must fall back to the paid level, not to nothing."""
    profile = ProfileFactory()
    user = profile.user
    SubscriptionService.activate_subscription(user, 'patron', 'stripe')

    user.refresh_from_db()
    user.role = 'admin'
    user.save()
    profile.refresh_from_db()
    assert profile.display_mark == 'staff'

    user.role = ''
    user.save()
    profile.refresh_from_db()
    assert profile.display_mark == 'patron', 'losing the role lost the paid mark too'


# ------------------------------------------------------------------------- the styling -------

def test_mark_style_answers_every_key():
    assert mark_style('staff')['kind'] == 'service'
    assert mark_style('mod')['label'] == 'Moderator'
    patron = mark_style('patron')
    assert patron['kind'] == 'supporter' and patron['stars'] == 2
    assert mark_style('') is None and mark_style(None) is None
    assert mark_style('nonsense') is None


def test_service_colours_keep_their_distance_from_the_giving_ramp():
    """Crimson and spring green must never blur with a ladder hue at 11px -- the same measured
    standard the ramp itself is held to."""
    def hls(hex_value):
        r, g, b = (int(hex_value.lstrip('#')[i:i + 2], 16) / 255 for i in (0, 2, 4))
        h, l, _ = colorsys.rgb_to_hls(r, g, b)
        return h * 360, l

    for key, mark in SERVICE_MARKS.items():
        mh, ml = hls(mark['colour'])
        for tier in SUPPORT_TIERS:
            th, tl = hls(tier['colour'])
            hue_gap = min(abs(mh - th), 360 - abs(mh - th))
            assert hue_gap > 25 or abs(ml - tl) > 0.12, (
                f"{key} sits too close to {tier['slug']} ({hue_gap:.0f} deg apart)"
            )


# ------------------------------------------------------------------------- the rendering ----

def _render(name, mark, size=None):
    return render_to_string('components/name_mark.html',
                            {'name': name, 'mark': mark_style(mark), 'size': size})


def test_the_partial_renders_each_register():
    staff = _render('Jeff', 'staff')
    assert '#e0564f' in staff and 'aria-label="Staff"' in staff
    assert 'pp-supname' in staff

    mod = _render('Mo', 'mod')
    assert '#59c96f' in mod and 'aria-label="Moderator"' in mod

    patron = _render('Pat', 'patron')
    assert patron.count('pp-supstar') == 2
    assert 'PlatPursuit Patron' in patron

    backer = _render('Bea', 'backer')
    assert 'is-outline' in backer, "Backer's single star lost its outline state"

    plain = _render('Nobody', '')
    assert 'pp-supname' not in plain and 'Nobody' in plain


def test_marked_surfaces_read_the_denorm():
    """The three main templates render the mark from entry/profile.display_mark -- the whole
    point of the denorm is that surfaces never re-derive it."""
    row = render_to_string('trophies/partials/leaderboard_row.html', {
        'entry': {'psn_username': 'Hunter', 'display_mark': 'staff', 'rank': 1,
                  'avatar_url': '', 'flag': '', 'displayed_title': '', 'value': 1},
        'board': {'slug': 'x'},
    })
    assert 'aria-label="Staff"' in row
    assert 'lb-row__prem' not in row, 'the legacy amber star survived'


def test_the_wall_carries_the_service_override(client):
    """A paying staff member's name AND sub-line wear the service colour on the credits, the
    sub-line saying so plainly ("Staff"); the stars stay their paid level's."""
    from unittest.mock import patch
    from django.core.cache import cache
    cache.delete('support:stats')

    profile = ProfileFactory(display_psn_username='StaffPayer')
    user = profile.user
    SubscriptionService.activate_subscription(user, 'patron', 'stripe')
    user.refresh_from_db()
    user.role = 'admin'
    user.save()

    with patch('users.views.SubscriptionService.get_prices_from_stripe', return_value={}):
        body = ' '.join(client.get('/support/').content.decode().split())
    card = body[body.index('StaffPayer') - 1200:body.index('StaffPayer') + 900]
    assert f"--svc-t: {SERVICE_MARKS['staff']['colour']}" in card
    assert 'Staff' in card, 'the service label did not reach the wall sub-line'
    assert 'PlatPursuit Patron' not in card, 'the level line should yield to the service label'


def test_the_extended_surfaces_carry_the_mark():
    """The perk says 'everywhere your name appears' -- Career hero, Pursuer Card, the recap
    share image and the game-detail quick takes each keep that word, from the denorm."""
    import types
    from django.utils import timezone
    from users.constants import SUPPORT_TIERS

    # Career hero: the service dict hands the denorm to the template unchanged.
    from trophies.services.career_service import build_career_context
    profile = ProfileFactory(display_mark='patron')
    assert build_career_context(profile)['hero']['display_mark'] == 'patron'

    # Pursuer Card: the name line renders through the shared partial.
    card = render_to_string('partials/components/_pursuer_card.html', {'card': {
        'name': 'Marked', 'display_mark': 'staff',
        'rank': {'key': 'wanderer', 'label': 'Wanderer'},
        'platinums': 0, 'showcase': {'recent': [], 'rarest': []},
    }})
    assert 'aria-label="Staff"' in card and 'pp-supname' in card

    # Recap share card: Playwright renders with no stylesheet, so the mark is inline --
    # the name in the mark colour, the glyph filled in it.
    cornerstone = next(t['colour'] for t in SUPPORT_TIERS if t['slug'] == 'cornerstone')
    share = render_to_string('recap/partials/recap_share_card.html', {
        'username': 'Star', 'mark': mark_style('cornerstone'), 'format_type': 'landscape',
    })
    name_zone = share[share.index('Star') - 400:share.index('Star') + 900]
    assert f'color: {cornerstone}' in name_zone
    assert f'fill="{cornerstone}"' in name_zone
    assert name_zone.count('<svg') >= 5, 'cornerstone wears five stars'

    # Plat Cards (the My Pursuit share cards): both the single plat card and the platinum
    # grid are Playwright-rendered like the recap card, so the mark is inline there too.
    staff_colour = SERVICE_MARKS['staff']['colour']
    plat = render_to_string('shareables/plat_card.html', {
        'username': 'Wrench', 'mark': mark_style('staff'), 'variant': 'platinum',
        'total_platinums': 1, 'badge_lines': [],
    })
    zone = plat[plat.index('Wrench') - 400:plat.index('Wrench') + 900]
    assert f'color: {staff_colour}' in zone and f'fill="{staff_colour}"' in zone

    grid = render_to_string('shareables/partials/platinum_grid_card.html', {
        'username': 'Wrench', 'mark': mark_style('staff'), 'plat_rows': [],
        'width': 800, 'height': 600, 'header_height': 80, 'footer_height': 40,
        'padding': 24, 'section_gap': 12, 'total_plats': 0, 'icon_type': 'trophy',
        'theme_key': 'default',
    })
    zone = grid[grid.index('Wrench') - 400:grid.index('Wrench') + 900]
    assert f'color: {staff_colour}' in zone and f'fill="{staff_colour}"' in zone

    # Quick-take blurb: the author's name link renders the mark from profile.display_mark.
    author = types.SimpleNamespace(psn_username='mo', display_psn_username='Mo',
                                   avatar_url='', display_mark='mod')
    blurb = render_to_string('trophies/partials/game_detail/_blurb_card.html', {
        'b': types.SimpleNamespace(id=1, profile_id=1, profile=author, overall_rating=4,
                                   blurb='Tidy platinum.', updated_at=timezone.now()),
        'viewer_profile_id': None,
    })
    assert 'aria-label="Moderator"' in blurb and 'pp-supname' in blurb
