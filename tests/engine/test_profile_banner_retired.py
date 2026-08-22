"""Phase 0 of the profile rebuild: the banner, the challenge showcase and the stray card scripts.

Three separate retirements, grouped because they are all "make the surface smaller before rebuilding it".
What needs pinning is the part that rots quietly: a removed feature leaving its plumbing behind, where the
model still offers a field nothing writes, `absorb()` still migrates a relation that no longer exists, or a
type is still selectable with nothing able to render it.
"""
import ast
import re
from pathlib import Path

import pytest
from django.core.exceptions import FieldDoesNotExist

from tests.factories import ProfileFactory
from trophies.models import Profile

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


# ── The showcase system ───────────────────────────────────────────────────────────────────────────

def test_the_whole_showcase_system_is_gone():
    """Profile customization was REMOVED in 2026-08, not parked.

    It was hidden first, behind a comment in `profile_detail.html` that said explicitly not to delete it --
    "every row is a choice a hunter made" -- on the expectation that the surface would come back. The
    profile was then rebuilt without it, so the rows stopped being a choice anyone could act on and the
    door stopped leading anywhere.

    What this pins is the part that rots: a removed feature leaving its plumbing behind. Six showcase
    types, a registry, a service, an editor view, a parked API and two tables all went together, so the
    check is that none of them came back one import at a time.
    """
    from django.apps import apps

    model_names = {m.__name__ for m in apps.get_app_config('trophies').get_models()}
    assert not {'ProfileShowcase', 'ProfileBadgeShowcase'} & model_names, (
        'a showcase model is back; the tables were dropped in 0303'
    )

    for gone in (
        ROOT / 'trophies' / 'services' / 'showcase_service.py',
        ROOT / 'api' / 'profile_showcase_views.py',
        ROOT / 'templates' / 'trophies' / 'profile_editor.html',
        ROOT / 'static' / 'js' / 'profile-editor.js',
        ROOT / 'templates' / 'trophies' / 'partials' / 'profile_showcases',
        ROOT / 'templates' / 'trophies' / 'partials' / 'profile_detail' / 'profile_showcases_section.html',
    ):
        assert not gone.exists(), f'{gone.name} is back'


def test_the_profile_page_no_longer_reserves_a_slot_for_them():
    """The hidden band left a `{% comment %}` block holding its place. A removed feature keeping a
    placeholder is how the next reader concludes it is coming back."""
    profile = (ROOT / 'templates' / 'trophies' / 'profile_detail.html').read_text(encoding='utf-8')
    assert 'rendered_showcases' not in profile
    assert 'SHOWCASES sat here' not in profile


def test_the_editor_url_still_sends_you_home(client):
    """`ProfileEditorView` is deleted but `/profile-editor/` still resolves, to a redirect. Recovered from
    `test_showcases_hidden.py`, which was deleted with the showcase system -- five of its tests covered
    routing that is still live, and they went with the premise that was obsolete. That is the
    deletion-specific failure mode this file exists to catch, caught on itself."""
    resp = client.get('/profile-editor/')

    assert resp.status_code == 302, f'the editor still renders (got {resp.status_code})'
    assert resp['Location'] == '/', f'it redirects to {resp["Location"]}, not the homepage'


def test_the_editor_redirect_stays_temporary(client):
    """Still a 302, but for a different reason than when it was written.

    It was temporary because customization was PARKED and would return. Customization is now removed and
    may never return -- but a 301 is cached by the browser indefinitely, so it is the harder of the two to
    undo. A temporary redirect on a URL nobody visits costs nothing; a permanent one on a path we might
    reuse costs a support thread.
    """
    from django.urls import resolve

    assert resolve('/profile-editor/').func.view_initkwargs['permanent'] is False


def test_the_old_my_pursuit_path_goes_straight_home(client):
    """Still 301 itself: the /my-pursuit/ -> root move IS permanent whatever happens to customization.
    What matters is that it does not hop THROUGH `profile_editor` and cost a second redirect."""
    resp = client.get('/my-pursuit/profile-editor/')

    assert resp.status_code == 301
    assert resp['Location'] == '/', f'still hops through {resp["Location"]}'


def test_no_url_conf_imports_a_view_it_no_longer_routes():
    """An import with nothing using it is the residue a teardown leaves, and it is what makes the next
    reader think the routes are still there.

    Generalised from the version deleted with `test_showcases_hidden.py`, which checked a hard-coded list
    of names. Asserting "every import is referenced somewhere in the file" needs no list, so it covers the
    next teardown as well as the last one -- which matters right now, because the dashboard's views and
    routes are coming out next and a urls.py importing a view it no longer routes is exactly how that
    breaks.
    """
    for rel in ('plat_pursuit/urls.py', 'api/urls.py'):
        tree = ast.parse((ROOT / rel).read_text(encoding='utf-8'))
        imported = {a.asname or a.name for n in ast.walk(tree)
                    if isinstance(n, (ast.Import, ast.ImportFrom)) for a in n.names}
        used = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
        used |= {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
        # A dotted import (`import x.y`) binds the root name; compare on that.
        orphans = {name for name in imported if name.split('.')[0] not in used}

        assert not orphans, f'{rel} imports {sorted(orphans)} but references none of them'


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
    reusable and its syncing->synced transition detection is not trivial to rewrite. Staff-gated since
    the 2026-08 design-lab strip (it renders the production partial, so it stays a regression surface)."""
    profile = ProfileFactory(is_linked=True)
    profile.user.is_staff = True
    profile.user.save(update_fields=['is_staff'])
    client.force_login(profile.user)

    assert client.get('/design/pursuer-card-ranks/').status_code == 200
