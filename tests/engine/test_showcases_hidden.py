"""Profile customization is hidden pending a ground-up rebuild (2026-08).

Hidden, not deleted, and the distinction matters more here than anywhere else this pattern has been
applied: every showcase row is a CHOICE a hunter made -- the platinums in their case, their six
favourite games, the badges they picked out, the title they wear. Deleting the tables would throw
away authored content that no sync can regenerate. So the models, the service, the API views, all
five display partials, the editor page and its JS controller are all intact behind a closed door.

What this pins is that nothing leads INTO it, and that the data is still there. The two are the whole
point of hiding rather than deleting, and they fail in opposite directions: a leaked door quietly
re-opens an unfinished surface, and a lost row is unrecoverable.

Why it was hidden rather than finished: the profile's About surface was going to pair these with a
trophy timeline, and exploring that turned up a timeline that had rendered nowhere since the header
rebuild AND a second, competing customization story in the Pursuer Card. Rather than ship a tab that
hid working customization behind a click to pair it with a husk, the whole surface comes off and gets
rebuilt deliberately later. The timeline was deleted outright in the same change (no user data, no
pixels); see `test_anon_profile_render.py` for what that leaves behind.

Modelled on `test_lists_hidden.py`, which is the house pattern for this.
"""
import ast
from pathlib import Path

import pytest
from django.contrib.auth.models import AnonymousUser
from django.test import RequestFactory
from django.urls import reverse

from tests.factories import ProfileFactory
from trophies.views.profile_views import ProfileDetailView

pytestmark = pytest.mark.django_db

ROOT = Path(__file__).resolve().parents[2]

#: Withdrawn outright, not redirected. All four are WRITES -- an endpoint left answering would let
#: anything still holding a reference file rows into a system with no door, which the rebuild would
#: then have to reconcile.
WITHDRAWN_API = [
    '/api/v1/profile/showcases/',
    '/api/v1/profile/showcases/reorder/',
    '/api/v1/profile/showcases/platinum_case/',
    '/api/v1/profile/showcases/platinum_case/config/',
]


def test_the_editor_sends_you_home(client):
    resp = client.get('/profile-editor/')

    assert resp.status_code == 302, f'the editor still renders (got {resp.status_code})'
    assert resp['Location'] == '/', f'it redirects to {resp["Location"]}, not the homepage'


def test_the_editor_redirect_is_temporary_so_it_can_be_taken_back(client):
    """A 301 is cached by the browser indefinitely. Using one here would keep sending people to the
    homepage long after the rebuilt system ships -- and specifically the people who customized their
    profile most, because they are the ones holding the bookmark."""
    from django.urls import resolve

    assert resolve('/profile-editor/').func.view_initkwargs['permanent'] is False


def test_the_old_my_pursuit_path_goes_straight_home_not_through_the_editor(client):
    """It used to 301 to `profile_editor`, which now 302s to `/` -- a double hop for every visitor
    holding the older of the two bookmarks. Still 301 itself, because the /my-pursuit/ -> root move IS
    permanent whatever happens to customization."""
    resp = client.get('/my-pursuit/profile-editor/')

    assert resp.status_code == 301
    assert resp['Location'] == '/', f'still hops through {resp["Location"]}'


def test_the_url_name_still_reverses():
    """The parked showcase section reverses `profile_editor` twice. Dropping the name would make that
    template unrenderable, which would turn a curtain into a demolition."""
    assert reverse('profile_editor') == '/profile-editor/'


@pytest.mark.parametrize('url', WITHDRAWN_API)
def test_the_api_no_longer_accepts_writes_into_a_system_with_no_door(client, url):
    profile = ProfileFactory(is_linked=True)
    client.force_login(profile.user)

    # A POST to an unrouted path answers 405 rather than 404 on this site: the custom `handler404` is a
    # view that only allows GET/HEAD/OPTIONS, so it rejects the METHOD before it ever reports the missing
    # route. Compared against a control path that has never existed, so this asserts "answers like an
    # unrouted path" rather than a specific code -- pinning 404 here would be pinning a quirk.
    control = client.post('/api/v1/definitely-not-a-route/', {}).status_code

    assert client.get(url).status_code == 404, f'{url} is still routed'
    assert client.post(url, {}).status_code == control, f'{url} still accepts writes'


def test_the_dashboards_badge_showcase_endpoints_are_untouched(client):
    """Deliberately NOT withdrawn despite the name. `/api/v1/badges/showcase/` belongs to the
    dashboard's own badge module (dashboard.js:352), not to profile customization -- retiring it is
    the dashboard sunset's job, and taking it out here would have broken an unrelated surface."""
    # Asserted on ROUTING, not on a status code: `!= 404` also passes on a 500 from a broken view, and
    # what this guards is that the route still exists at all.
    from django.urls import Resolver404, resolve

    for url in ('/api/v1/badges/showcase/', '/api/v1/badges/showcase/reorder/'):
        try:
            resolve(url)
        except Resolver404:
            raise AssertionError(f'{url} was withdrawn with the profile showcases -- it is the dashboard\'s')


def test_the_profile_does_not_run_the_showcase_provider_for_anyone(monkeypatch):
    """Asserted on the CALL, not on the context value. A version that builds the data and then hides it
    in the template passes a "context is empty" check and still does the work -- which is the exact
    failure `test_anon_profile_render.py` was written about after the 2026-08-09 outage.

    Both viewers, because the provider was never auth-gated: showcases rendered for logged-out visitors
    on purpose, so "it stopped running for anon" would be no evidence at all."""
    from trophies.services.showcase_service import ProfileShowcaseService

    calls = []
    monkeypatch.setattr(
        ProfileShowcaseService, 'get_rendered_showcases',
        staticmethod(lambda p: calls.append('showcases') or []),
    )

    profile = ProfileFactory(is_linked=True, psn_history_public=True)
    for user in (AnonymousUser(), ProfileFactory(is_linked=True).user):
        request = RequestFactory().get(f'/hunters/{profile.psn_username}/')
        request.user = user
        request.htmx = False

        view = ProfileDetailView()
        view.request = request
        view.object = profile
        view.kwargs = {'psn_username': profile.psn_username}
        context = view.get_context_data(object=profile)

        assert not context.get('rendered_showcases')

    assert not calls, 'the profile still builds showcases'


def test_the_profile_page_no_longer_includes_the_band():
    src = (ROOT / 'templates' / 'trophies' / 'profile_detail.html').read_text(encoding='utf-8')

    # The FILENAME, not an exact include line: quoting, whitespace or `{% include var %}` would all slip
    # past a byte-exact match. The surviving comment block above does not name the file, so this cannot
    # collide with the explanation of its own removal.
    assert 'profile_showcases_section.html' not in src


def test_no_url_conf_imports_a_view_it_no_longer_routes():
    """A name imported with nothing using it is the residue a teardown leaves, and it is what makes the
    next person think the routes are still there."""
    for rel, names in (
        ('plat_pursuit/urls.py', {'ProfileEditorView'}),
        ('api/urls.py', {'AddShowcaseView', 'RemoveShowcaseView',
                         'ReorderShowcasesView', 'UpdateShowcaseConfigView'}),
    ):
        tree = ast.parse((ROOT / rel).read_text(encoding='utf-8'))
        imported = {a.asname or a.name for n in ast.walk(tree)
                    if isinstance(n, ast.ImportFrom) for a in n.names}

        assert not (imported & names), f'{rel} imports {sorted(imported & names)} but routes none of them'


def test_every_row_a_hunter_authored_is_still_there():
    """The whole reason this is a curtain. These are choices no sync can regenerate, so the tables and
    the service that reads them have to survive the surface being taken down."""
    from trophies.models import ProfileShowcase
    from trophies.services.showcase_service import ProfileShowcaseService, SHOWCASE_REGISTRY

    from tests.factories import GameFactory, ProfileGameFactory

    profile = ProfileFactory(is_linked=True)
    # REAL games, so the provider has something to resolve. Pointed at ids that do not exist, it returns
    # empty slots and the only thing left to assert is `is not None` -- which a provider gutted to
    # `return []` would also satisfy, i.e. a test that cannot fail for the reason it claims.
    games = [GameFactory() for _ in range(2)]
    for game in games:
        ProfileGameFactory(profile=profile, game=game)
    ProfileShowcase.objects.create(profile=profile, showcase_type='favorite_games',
                                   config={'game_ids': [g.id for g in games]})

    stored = ProfileShowcase.objects.get(profile=profile, showcase_type='favorite_games')
    assert stored.config == {'game_ids': [g.id for g in games]}, 'a hunter\'s selections did not survive'

    # And the service still READS them: the descriptor resolves AND the provider returns the rows behind
    # the config. That is what makes the restore a matter of reopening a door rather than rebuilding a
    # reader, which is the entire claim of hiding rather than deleting.
    assert 'favorite_games' in SHOWCASE_REGISTRY
    rendered = ProfileShowcaseService.get_rendered_showcases(profile)
    assert [e['showcase'].showcase_type for e in rendered] == ['favorite_games']
    assert rendered[0]['data']['has_items'], 'the provider no longer reads the hunter\'s selections'


def test_the_registry_and_the_model_still_agree():
    """A type offered by the model but absent from the registry stores rows that display nothing --
    which is exactly what `challenge_showcase` did before it was removed. Parked code still has to hold
    this invariant, or the restore inherits a broken type."""
    from trophies.models import ProfileShowcase
    from trophies.services.showcase_service import SHOWCASE_REGISTRY

    assert set(dict(ProfileShowcase.SHOWCASE_TYPES)) == set(SHOWCASE_REGISTRY)


def test_the_dead_editor_template_key_is_gone():
    """Every descriptor carried an `editor_template` pointing into
    `trophies/partials/profile_editor/` -- a directory that has never existed. Nothing read the key, so
    its only effect was sending the next reader after files that were never written."""
    src = (ROOT / 'trophies' / 'services' / 'showcase_service.py').read_text(encoding='utf-8')

    # The KEY, not the word: the module docstring explains the removal, so a bare substring check
    # would fail on its own explanation.
    assert "'editor_template':" not in src
    assert not (ROOT / 'templates' / 'trophies' / 'partials' / 'profile_editor').exists()
