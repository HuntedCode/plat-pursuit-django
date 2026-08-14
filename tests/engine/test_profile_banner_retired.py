"""Phase 0 of the profile rebuild: the banner, the challenge showcase and the stray card scripts.

Three separate retirements, grouped because they are all "make the surface smaller before rebuilding it".
What needs pinning is the part that rots quietly: a removed feature leaving its plumbing behind, where the
model still offers a field nothing writes, `absorb()` still migrates a relation that no longer exists, or a
type is still selectable with nothing able to render it.
"""
import re
from pathlib import Path

import pytest
from django.core.exceptions import FieldDoesNotExist

from tests.factories import ProfileFactory
from trophies.models import Profile, ProfileShowcase

pytestmark = pytest.mark.django_db

ROOT = Path(__file__).resolve().parents[2]
CF = {'HTTP_CF_RAY': '8f0000000000abcd-LHR'}


# ── The banner ────────────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize('field', ['selected_background', 'banner_image_url', 'banner_position'])
def test_the_banner_fields_are_gone_from_the_model(field):
    """Removed rather than left unused. A nullable column nothing reads is an invitation to write to it
    again, and `selected_background` in particular carried an index and an `absorb()` branch."""
    with pytest.raises(FieldDoesNotExist):
        Profile._meta.get_field(field)


def test_absorb_no_longer_migrates_profile_backgrounds():
    """`Concept.absorb()` must handle every relation TO Concept -- and exactly those. A branch for a
    relation that no longer exists would raise the moment two concepts merged during a sync."""
    src = (ROOT / 'trophies' / 'models.py').read_text(encoding='utf-8')
    absorb = src[src.index('    def absorb(self'):]
    absorb = absorb[:absorb.index('\n    def ', 1)]

    assert 'selected_by_profiles' not in absorb
    assert 'selected_background' not in absorb


def test_the_profile_page_renders_no_banner(client):
    profile = ProfileFactory(is_linked=True)

    body = client.get(f'/hunters/{profile.psn_username}/', **CF).content.decode()

    for marker in ('profile-banner', 'banner-image', 'banner-edit-btn', 'profile_banner_url'):
        assert marker not in body, f'{marker} survived the banner removal'


def test_the_banner_editor_and_picker_are_deleted():
    """Their only callers were the profile page and the settings form; left in the tree they would be
    dead files that still look load-bearing."""
    for path in ('templates/trophies/partials/profile_detail/banner_editor_modal.html',
                 'static/js/profile-banner-editor.js',
                 'static/js/game-background-picker.js'):
        assert not (ROOT / path).exists(), f'{path} is still in the tree'


def test_the_settings_api_no_longer_accepts_banner_settings():
    """The endpoint dispatches on a `setting` name and falls through to "Unknown setting", so a branch
    left behind would keep writing columns that no longer exist."""
    src = (ROOT / 'api' / 'user_settings_views.py').read_text(encoding='utf-8')

    for setting in ("'selected_background'", "'banner_image'", "'banner_position'"):
        assert f'elif setting == {setting}' not in src, f'the {setting} branch survived'


# ── The challenge showcase ────────────────────────────────────────────────────────────────────────

def test_the_challenge_showcase_type_is_retired():
    """Challenges are retired, and this type was ALREADY unrenderable -- offered as a valid choice while
    never registered in SHOWCASE_REGISTRY, so a stored row resolved to no descriptor at all."""
    assert not hasattr(ProfileShowcase, 'SHOWCASE_CHALLENGE')
    assert 'challenge_showcase' not in dict(ProfileShowcase.SHOWCASE_TYPES)


def test_every_offered_showcase_type_can_actually_render():
    """The invariant the challenge showcase broke: a type the model offers must have a descriptor, or
    `get_descriptor` raises for a row the user was allowed to create."""
    from trophies.services.showcase_service import SHOWCASE_REGISTRY

    offered = {value for value, _ in ProfileShowcase.SHOWCASE_TYPES}

    assert offered == set(SHOWCASE_REGISTRY), (
        f'offered but unrenderable: {sorted(offered - set(SHOWCASE_REGISTRY))}; '
        f'renderable but not offered: {sorted(set(SHOWCASE_REGISTRY) - offered)}'
    )


def test_the_migration_deletes_the_retired_rows_not_just_the_choices():
    """Django does not enforce `choices` in the database, so narrowing them leaves every existing row
    intact and unresolvable. Both halves have to ship together -- and the data half must run FIRST, while
    the value is still legal."""
    src = (ROOT / 'trophies' / 'migrations' / '0292_drop_challenge_showcase.py').read_text(encoding='utf-8')
    # Scoped to the operations list: both names are also mentioned in the helper's docstring above it, so
    # comparing first-occurrence indexes over the whole file compares prose against code.
    ops = src[src.index('operations = ['):]

    assert 'RunPython' in ops, 'the retired rows are never deleted'
    assert ops.index('RunPython') < ops.index('AlterField'), 'the rows are deleted after the choices narrow'


# ── The stray Pursuer Card scripts ────────────────────────────────────────────────────────────────

def test_the_pursuer_card_scripts_do_not_ship_site_wide():
    """They rode the global bundle from when the card was going to be a site-wide identity element. The
    card was dropped from the lobby and the tags were never removed, so every page shipped them for markup
    only one design workshop renders. They no-op without a card, so this was weight, not breakage."""
    base = (ROOT / 'templates' / 'base.html').read_text(encoding='utf-8')

    assert 'pursuer-card.js' not in base
    assert 'pursuer-card-forge.js' not in base


def test_the_workshop_that_renders_the_card_loads_them_itself():
    page = (ROOT / 'templates' / 'design' / 'pursuer_card_ranks.html').read_text(encoding='utf-8')

    assert '{% load static %}' in page, '{% static %} without the load tag is a 500'
    assert 'pursuer-card.js' in page and 'pursuer-card-forge.js' in page


def test_the_design_workshop_still_renders(client):
    """The card markup and its behaviour are kept deliberately -- the forge's fresh-sync choreography is
    reusable and its syncing->synced transition detection is not trivial to rewrite."""
    assert client.get('/design/pursuer-card-ranks/').status_code == 200
