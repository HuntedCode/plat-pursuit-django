"""The site-wide premium gradient theme is retired.

Scoped deliberately, because the name is shared by two other live things: `GameList.selected_theme` (a
list's own theme) and the `selected_themes` IGDB filter. Only `Profile.selected_theme` -- the premium
site-wide <body> gradient -- and its plumbing were removed.

`trophies/themes.py` itself STAYS. It still feeds share-card grounds, plat cards, the recap and the
subscribe page's preview swatches; only the profile's use of it went.
"""
from pathlib import Path

import pytest
from django.core.exceptions import FieldDoesNotExist
from django.test import Client

from tests.factories import ProfileFactory
from trophies.models import Profile

pytestmark = pytest.mark.django_db

ROOT = Path(__file__).resolve().parents[2]
CF = {'HTTP_CF_RAY': '8f0000000000abcd-LHR'}


def test_the_field_is_gone_from_the_profile():
    with pytest.raises(FieldDoesNotExist):
        Profile._meta.get_field('selected_theme')


def test_the_game_list_theme_is_untouched():
    """Same attribute name, different feature. A blanket removal would have taken it too."""
    from trophies.models import GameList

    assert GameList._meta.get_field('selected_theme') is not None


def test_no_body_style_hook_survives():
    """The theme was injected as an inline <body> style by a context processor. Both halves had to go --
    a live hook reading a variable nothing sets is a trap for the next person who defines that name."""
    base = (ROOT / 'templates' / 'base.html').read_text(encoding='utf-8')
    settings = (ROOT / 'plat_pursuit' / 'settings.py').read_text(encoding='utf-8')
    processors = (ROOT / 'plat_pursuit' / 'context_processors.py').read_text(encoding='utf-8')

    assert 'user_theme_style' not in base
    assert 'premium_theme_background' not in settings, 'the processor is still registered'
    assert 'def premium_theme_background' not in processors


def test_the_settings_api_no_longer_accepts_a_site_theme():
    src = (ROOT / 'api' / 'user_settings_views.py').read_text(encoding='utf-8')

    assert "elif setting == 'selected_theme'" not in src


def test_the_empty_premium_settings_form_is_gone():
    """Its only field was `selected_theme` once the banner went in Phase 0, so it had nothing left to
    edit -- and a ModelForm with no fields silently validates anything."""
    import trophies.forms as forms_module

    assert not hasattr(forms_module, 'PremiumSettingsForm')


def test_the_dashboard_theme_picker_module_is_gone():
    """It was DEREGISTERED when the theme column was dropped (a registered module whose provider reads a
    dropped column raises the moment it renders). The whole dashboard went in 2026-08, so the assertion
    narrows to the template: there is no registry left to be absent from."""
    assert not (ROOT / 'templates/trophies/partials/dashboard/premium_settings.html').exists()


def test_the_colour_grid_modal_is_deleted():
    assert not (ROOT / 'templates' / 'partials' / 'color_grid_modal.html').exists()


def test_themes_module_survives_for_the_features_that_still_use_it():
    """The removal is scoped to the PROFILE theme. Share-card grounds, plat cards, the recap and the
    subscribe page's preview swatches all still read this module."""
    from trophies.themes import GRADIENT_THEMES

    assert GRADIENT_THEMES, 'the theme catalogue was removed along with the profile feature'


def test_the_settings_page_still_renders_without_its_theme_card(client):
    """It lost a whole card, a form and a POST action; the remaining sections must still stand.

    `/users/subscribe/` is the other page that reads the theme catalogue, and it is deliberately NOT
    asserted here: it redirects under test because the Stripe Price fixtures do not exist in the test
    database, which would make this a test of the billing fixtures rather than of the theme removal. Its
    use of GRADIENT_THEMES is covered by the module test above, and it was checked in a browser.
    """
    profile = ProfileFactory(is_linked=True)
    client.force_login(profile.user)

    assert client.get('/users/settings/', **CF).status_code == 200


def test_a_profile_page_still_renders(client):
    profile = ProfileFactory(is_linked=True)

    assert client.get(f'/hunters/{profile.psn_username}/', **CF).status_code == 200
