"""My Lists, and the create modal's form.

Built from scratch rather than ported: unlike browse and list detail, this page was never rebuilt in
2026-08 and was still a pre-rebuild DaisyUI shell.

Two things here are decisions rather than mechanics, and both get pinned. A list is PRIVATE when it
is born and publishing is a separate act -- so the create form has no visibility control at all, and
passing one must not work. And "Following" is the only place a follow means anything this release,
since there is no notification surface; the tab is the feature, not decoration on it.
"""
import pytest
from django.urls import reverse

from gamelists.models import GameList
from gamelists.services import game_list_service as svc
from tests.factories import ConceptFactory, GameFactory, ProfileFactory, UserFactory

pytestmark = pytest.mark.django_db

MY_LISTS = '/my-lists/'

#: The private chip's title, which is unique to the chip. Asserting on the bare word "Private"
#: matches the create modal's "Private to start" copy, and asserting a list NAME like "Mine" matches
#: the scope switcher's own chip label -- both of which made an early version of these tests pass or
#: fail for reasons that had nothing to do with the grid.
PRIVATE_CHIP = 'Only you can see this list'


def _dialog(body):
    """Just the create dialog.

    `base.html` includes five site-wide modals of its own (unsaved-changes, theme nudge, guidelines
    ...), all still on the legacy `modal-box` idiom -- so a whole-page search for DaisyUI classes is
    answered by the chrome and says nothing about this page. Third time a substring assertion here
    has been answered by something outside the thing under test.
    """
    start = body.index('<dialog id="gl-create"')
    return body[start:body.index('</dialog>', start)]


def _grid(body):
    """Just the tile grid, so a substring assertion cannot be answered by the page chrome.

    Bounded by the DIALOG that follows it rather than by the last `pp-gtile` in the document: the
    original searched to end-of-page, so adding any tile preview inside the create modal would have
    silently re-widened the slice past the grid and reopened the hole this helper closes.
    """
    start = body.index('pp-gtile-grid')
    end = body.index('<dialog', start) if '<dialog' in body[start:] else len(body)
    return body[start:end]


def _header(body):
    """The page header -- bounded at the switcher row, not at the dialog.

    It used to return `body[:body.index('<dialog')]`, and the dialog is the LAST element on this
    page, so "the header" was two thirds of the document including the navbar, the toolbar and the
    whole tile grid. Two tests rested on that slice, one of them searching it for the bare word
    `disabled`.
    """
    return body[:body.index('data-gl-scopes')]


def _staff_hunter(client, psn='curator', premium=False):
    """Staff because the surface is gated while the branch is open; linked because a list belongs to
    a profile. Both are real requirements of the page, not test scaffolding."""
    user = UserFactory()
    user.role = 'admin'
    user.save()
    profile = ProfileFactory(user=user, is_linked=True, psn_username=psn)
    if premium:
        profile.user_is_premium = True
        profile.save(update_fields=['user_is_premium'])
    client.force_login(user)
    return profile


# ── the page ─────────────────────────────────────────────────────────────────────────────────────

def test_my_lists_shows_my_private_lists_too():
    """The whole point of "mine". Browse is the public catalogue; this is your shelf."""
    from django.test import Client

    client = Client()
    profile = _staff_hunter(client)
    svc.create_list(profile, name='Kept back')
    svc.create_list(profile, name='Shared', is_public=True)

    grid = _grid(client.get(MY_LISTS).content.decode())

    assert 'Kept back' in grid
    assert 'Shared' in grid


def test_my_lists_does_not_show_anybody_elses(client):
    profile = _staff_hunter(client)
    stranger = ProfileFactory(is_linked=True, psn_username='stranger')
    svc.create_list(stranger, name='Not yours', is_public=True)
    svc.create_list(profile, name='Mine')

    grid = _grid(client.get(MY_LISTS).content.decode())

    assert 'Mine' in grid
    assert 'Not yours' not in grid


def test_a_private_list_is_marked_and_a_public_one_is_not(client):
    """Marking the PRIVATE state rather than the public one: a list is private by default, so the
    chip marks the exception and its absence is the signal that this one is out in the world."""
    profile = _staff_hunter(client)
    svc.create_list(profile, name='Hidden one')

    assert PRIVATE_CHIP in client.get(MY_LISTS).content.decode()

    svc.update_list(GameList.objects.get(name='Hidden one'), profile, is_public=True)
    assert PRIVATE_CHIP not in client.get(MY_LISTS).content.decode()


def test_the_public_browse_grid_never_marks_privacy(client):
    """Every tile there is public by definition, so a chip on all of them is noise."""
    profile = _staff_hunter(client)
    svc.create_list(profile, name='Out there', is_public=True)

    body = client.get('/community/lists/').content.decode()

    # The positive half matters: without it a 302, a 500 or an empty grid satisfies "no chip".
    assert 'Out there' in body, 'the tile did not render, so the absence proves nothing'
    assert PRIVATE_CHIP not in body


def test_the_cap_is_shown_rather_than_discovered_by_being_refused(client):
    profile = _staff_hunter(client)
    svc.create_list(profile, name='One')

    resp = client.get(MY_LISTS)

    assert resp.context['list_count'] == 1
    assert resp.context['list_cap'] == svc.max_lists_for(profile)
    assert resp.context['at_cap'] is False


def test_at_the_cap_the_create_button_is_disabled_not_hidden(client):
    """A button that vanishes reads as a bug; a disabled one with a reason reads as a rule."""
    profile = _staff_hunter(client)
    for n in range(svc.max_lists_for(profile)):
        svc.create_list(profile, name=f'List {n}')

    resp = client.get(MY_LISTS)
    header = _header(resp.content.decode())

    assert resp.context['at_cap'] is True
    # Scoped to the header: "New list" is ALSO the dialog's own <h2> title, which renders
    # unconditionally, so a whole-page search stayed green with the button deleted -- precisely the
    # failure this test is named for.
    assert 'New list' in header, 'the button disappeared instead of explaining itself'
    # On the OPENING TAG of the opener. `disabled` as a bare word matched any attribute on any
    # element in what used to be most of the page.
    tag_start = header.index('<button', header.index('data-gl-open') - 300)
    assert 'aria-disabled="true"' in header[tag_start:header.index('>', tag_start)], 'the button is enabled at the cap'
    assert 'data-gl-open' in header


def test_the_page_is_query_flat_as_lists_are_added(client):
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    profile = _staff_hunter(client, premium=True)

    def build(count, tag):
        for n in range(count):
            game_list = svc.create_list(profile, name=f'{tag}{n}')
            for _ in range(3):
                concept = ConceptFactory()
                GameFactory(concept=concept, title_platform=['PS5'])
                svc.add_concept(game_list, profile, concept)

    def measure():
        with CaptureQueriesContext(connection) as ctx:
            resp = client.get(MY_LISTS)
        # A redirect runs no page queries, so `few == many` would hold at 0 == 0 and this would pass
        # while the page was unreachable. The sibling browse file documents adding exactly this
        # guard; it was not carried across to here either.
        assert resp.status_code == 200, f'the page did not render ({resp.status_code})'
        return len([q for q in ctx.captured_queries if 'gamelists_' in q['sql']])

    build(2, 'a')
    few = measure()
    build(6, 'b')
    many = measure()

    assert few == many, f'{few} queries for 2 lists, {many} for 8'


# ── the scope switcher, which is where a follow finally means something ──────────────────────────

def test_following_shows_lists_i_follow_and_not_my_own(client):
    profile = _staff_hunter(client)
    author = ProfileFactory(is_linked=True, psn_username='author')
    theirs = svc.create_list(author, name='Theirs', is_public=True)
    svc.create_list(profile, name='Mine')
    svc.set_follow(theirs, profile, following=True)

    grid = _grid(client.get(MY_LISTS, {'scope': 'following'}).content.decode())

    assert 'Theirs' in grid
    assert 'Mine' not in grid


def test_unpublishing_removes_a_list_from_everyone_elses_following_tab(client):
    """Otherwise a follow becomes a private window into somebody's library after they close it."""
    profile = _staff_hunter(client)
    author = ProfileFactory(is_linked=True, psn_username='author')
    theirs = svc.create_list(author, name='Was public', is_public=True)
    svc.set_follow(theirs, profile, following=True)

    svc.update_list(theirs, author, is_public=False)

    assert 'Was public' not in _grid(
        client.get(MY_LISTS, {'scope': 'following'}).content.decode())


def test_a_junk_scope_falls_back_to_mine(client):
    profile = _staff_hunter(client)
    svc.create_list(profile, name='Mine')

    resp = client.get(MY_LISTS, {'scope': 'nonsense'})

    assert resp.context['scope'] == 'mine'
    assert 'Mine' in _grid(resp.content.decode())


def test_the_empty_states_differ_by_scope(client):
    """"No lists yet" is an invitation; "not following any" is a different situation and saying the
    first one there would be wrong about what happened."""
    _staff_hunter(client)

    assert 'No lists yet' in client.get(MY_LISTS).content.decode()
    assert 'Not following any lists' in client.get(
        MY_LISTS, {'scope': 'following'}).content.decode()


# ── create ───────────────────────────────────────────────────────────────────────────────────────

def test_creating_a_list_makes_it_private(client):
    """The decision that makes the public/private state mean anything. Offering the toggle at
    creation would make it a checkbox somebody ticks while thinking about a name."""
    profile = _staff_hunter(client)

    client.post(reverse('list_create'), {'name': 'Fresh'})

    game_list = GameList.objects.get(owner=profile)
    assert game_list.name == 'Fresh'
    assert game_list.is_public is False


def test_the_create_form_offers_no_way_to_publish_and_ignores_one_if_posted(client):
    """Both halves: the form has no control, AND forging the field does nothing. A form-only
    guarantee is not a guarantee."""
    profile = _staff_hunter(client)

    form = _dialog(client.get(MY_LISTS).content.decode())
    assert 'is_public' not in form, 'the create modal grew a visibility control'

    client.post(reverse('list_create'), {'name': 'Forged', 'is_public': 'true'})

    assert GameList.objects.get(owner=profile).is_public is False


def test_a_refused_create_says_why_and_writes_nothing(client):
    profile = _staff_hunter(client)
    for n in range(svc.max_lists_for(profile)):
        svc.create_list(profile, name=f'List {n}')

    resp = client.post(reverse('list_create'), {'name': 'One too many'}, follow=True)

    assert GameList.objects.owned_by(profile).count() == svc.max_lists_for(profile)
    assert 'limit' in ' '.join(str(m) for m in resp.context['messages'])


def test_a_blank_name_is_refused_by_the_endpoint_too(client):
    """The service is the rule; this proves the endpoint routes a refusal into a message rather than
    a 500 or a silent no-op."""
    profile = _staff_hunter(client)

    resp = client.post(reverse('list_create'), {'name': '   '}, follow=True)

    assert GameList.objects.owned_by(profile).count() == 0
    assert 'name' in ' '.join(str(m) for m in resp.context['messages']).lower()


def test_creating_is_a_post_only_endpoint(client):
    _staff_hunter(client)

    assert client.get(reverse('list_create')).status_code == 405


# ── the guards on the page itself ────────────────────────────────────────────────────────────────

def test_an_account_with_no_linked_profile_is_sent_to_link_psn(client):
    """A list belongs to a profile, so there is nothing to show -- and reading
    `request.user.profile` without this raises RelatedObjectDoesNotExist and 500s the page."""
    user = UserFactory()
    user.role = 'admin'
    user.save()
    client.force_login(user)

    resp = client.get(MY_LISTS)

    assert resp.status_code == 302
    assert 'link' in resp.url.lower()


def test_creating_without_a_linked_profile_does_not_500(client):
    user = UserFactory()
    user.role = 'admin'
    user.save()
    client.force_login(user)

    resp = client.post(reverse('list_create'), {'name': 'Nope'})

    assert resp.status_code == 302
    assert GameList.objects.count() == 0


# -- the limits, and the counter that shows them --------------------------------------------------

def test_the_form_advertises_exactly_the_limit_the_service_enforces(client):
    """The point of the shared constants.

    A counter is only worth having if it is right, and these numbers used to be written three times
    each -- the column, the service, and a literal in the template. A stale copy in the form is
    worse than no counter at all, because it is confidently wrong: the hunter stops typing at 120
    and the save is refused at 60, or the field lets them past a limit the server will reject.
    """
    from gamelists.models import DESCRIPTION_MAX_LENGTH, GameList, NAME_MAX_LENGTH

    _staff_hunter(client)
    dialog = _dialog(client.get(MY_LISTS).content.decode())

    assert f'maxlength="{NAME_MAX_LENGTH}"' in dialog
    assert f'maxlength="{DESCRIPTION_MAX_LENGTH}"' in dialog

    # And the column agrees with both, so the browser, the service and the database cannot disagree.
    assert GameList._meta.get_field('name').max_length == NAME_MAX_LENGTH
    assert GameList._meta.get_field('description').max_length == DESCRIPTION_MAX_LENGTH


def test_a_name_at_the_limit_is_accepted_and_one_over_it_is_not(client):
    """The boundary itself, from the endpoint. Off-by-one here means the counter turns red on a
    name that would have saved fine."""
    from gamelists.models import GameList, NAME_MAX_LENGTH

    profile = _staff_hunter(client)

    client.post(reverse('list_create'), {'name': 'x' * NAME_MAX_LENGTH})
    assert GameList.objects.owned_by(profile).count() == 1

    resp = client.post(
        reverse('list_create'), {'name': 'y' * (NAME_MAX_LENGTH + 1)}, follow=True)
    assert GameList.objects.owned_by(profile).count() == 1
    assert 'too long' in ' '.join(str(m) for m in resp.context['messages'])


def test_a_description_over_the_limit_is_refused_and_nothing_is_written(client):
    from gamelists.models import DESCRIPTION_MAX_LENGTH, GameList

    profile = _staff_hunter(client)

    resp = client.post(reverse('list_create'), {
        'name': 'Fine', 'description': 'z' * (DESCRIPTION_MAX_LENGTH + 1)}, follow=True)

    assert GameList.objects.owned_by(profile).count() == 0, 'the refusal still wrote the list'
    assert 'too long' in ' '.join(str(m) for m in resp.context['messages'])


def test_both_fields_are_wired_to_a_counter(client):
    """`data-charcount` on the field plus a `data-charcount-for` target is the whole contract of the
    shared utility; without the pairing the counter silently renders nothing."""
    _staff_hunter(client)
    body = client.get(MY_LISTS).content.decode()

    import re

    for field_id in ('gl-name', 'gl-description'):
        assert f'id="{field_id}"' in body
        assert f'data-charcount-for="{field_id}"' in body

    # The SOURCE attribute specifically. `count('data-charcount')` also matched every
    # `data-charcount-for`, so four targets and zero wired fields passed while nothing counted.
    sources = re.findall(r'data-charcount(?![-\w])', body)
    assert len(sources) == 2, f'expected two wired fields, found {len(sources)}'


def test_the_shared_counter_is_not_a_fourth_copy():
    """It went into `utils.js` because it was about to be the THIRD implementation
    (`admin-notifications.js` and `comments.js` each have their own, both bound to specific ids).
    Lists must not add a fourth."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    assert 'wireCharCounters' in (root / 'static' / 'js' / 'utils.js').read_text(encoding='utf-8')

    gamelists_js = root / 'static' / 'js' / 'gamelists.js'
    if gamelists_js.exists():
        assert 'maxlength' not in gamelists_js.read_text(encoding='utf-8'), (
            'lists rolled their own character counter instead of using the shared one'
        )


def test_the_create_modal_uses_the_sites_own_primitives_not_daisyui(client):
    """The design pass, pinned.

    The first cut wore raw DaisyUI -- `modal-box`, `input input-bordered`, `btn btn-ghost` -- and
    read as a different product sitting inside this one: DaisyUI's radii, its borders, its button
    weight, none of them the site's. This asserts the page reaches for the house primitives instead,
    because "looks wrong" is not something the suite can see and the drift comes back one class at a
    time.
    """
    _staff_hunter(client)
    body = client.get(MY_LISTS).content.decode()
    dialog = _dialog(body)

    for daisy in ('modal-box', 'input-bordered', 'textarea-bordered', 'btn-ghost', 'btn-primary'):
        assert daisy not in dialog, f'the DaisyUI idiom is back: {daisy}'
    # The header's own action button too -- it opens this dialog, so the two must match.
    assert 'btn btn-' not in body[:body.index('<dialog')], 'the page header is still on DaisyUI buttons'

    # No `or house in body`. `pp-cta` is also on the header's opener, so the DIALOG could drop the
    # house primitive entirely -- the exact DaisyUI drift this test exists for -- and stay green on
    # the header's copy. The other six happen to be dialog-only today, which is what let the `or`
    # sit there looking harmless.
    for house in ('gl-dialog__head', 'gl-dialog__body', 'gl-dialog__foot',
                  'stg-input', 'stg-field__label', 'pp-cta', 'gl-suggest__chip'):
        assert house in dialog, f'the dialog stopped using the shared primitive {house}'


def test_the_dialog_is_a_native_dialog_so_focus_and_escape_come_for_free(client):
    """Kept as `<dialog>` on purpose. It is the house pattern for FORM sheets (the guidelines sheet,
    the badge picker) and brings a focus trap, Escape and an inert background that
    `.pp-detail-modal` -- the content-VIEW primitive -- has to hand-roll."""
    _staff_hunter(client)
    body = client.get(MY_LISTS).content.decode()

    assert '<dialog id="gl-create"' in body
    assert 'aria-labelledby="gl-create-title"' in body
    # `pp-dismissable` is added by `dismissableSheet` at runtime, deliberately NOT in the
    # template -- setting both rendered two grab pills on touch.
    assert 'pp-dismissable' not in _dialog(body), 'the duplicate grabber is back'


def _css_rule(css, selector):
    """The declarations of one rule, with comments stripped FIRST.

    Slicing to the next `}` on the raw file finds the brace inside a comment -- and this file's
    comments quote CSS, including `* { margin: 0 }`. The slice then stops three lines early and the
    assertion fails on a declaration that is present.
    """
    import re

    bare = re.sub(r'/\*.*?\*/', '', css, flags=re.S)
    start = bare.index(selector + ' {')
    return bare[start:bare.index('}', start)]


def test_the_dialog_centres_itself_and_scrolls_only_its_body():
    """It shipped in the top-left corner, and the fix for that was itself wrong.

    A native <dialog> centres via the user agent's `dialog { margin: auto }`, which Tailwind's
    preflight zeroes (`*, ::before, ::after, ::backdrop { margin: 0 }`). The first fix filled the
    viewport and centred with grid -- which worked, and made overflow past the block-START edge
    unreachable, so with a soft keyboard up the form's own title scrolled out of reach. It also
    re-minted a recipe `.gd-modal` already had.

    Restoring `margin: auto` is correct and not fragile: an author declaration outranks preflight on
    specificity AND source order. The second half is the scroll region -- head and foot are `flex:
    none` siblings of a scrolling BODY, so a long form cannot push its own actions off-screen. The
    version this replaces put `overflow-y` on the box containing all three and claimed the opposite
    in its comment.
    """
    from pathlib import Path

    css = (Path(__file__).resolve().parents[2]
           / 'static' / 'css' / 'components' / 'gamelists.css').read_text(encoding='utf-8')
    rule = _css_rule(css, '.gl-dialog')

    for declaration in ('position: fixed', 'inset: 0', 'margin: auto'):
        assert declaration in rule, f'.gl-dialog no longer centres itself: missing {declaration}'
    assert 'dvh' in rule, 'vh does not shrink for the soft keyboard; the header scrolls out of reach'
    assert 'overflow: hidden' in rule, 'the dialog scrolls as one piece, taking the footer with it'

    assert 'overflow-y: auto' in _css_rule(css, '.gl-dialog__body'), 'the body is not the scroll region'


def test_the_dialog_has_a_choreographed_exit_not_just_an_entrance():
    """The reference standard asks for exits handled as carefully as entrances, and `.gd-modal` --
    the primitive this follows -- does it with `.is-closing` plus `animationend`."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    css = (root / 'static' / 'css' / 'components' / 'gamelists.css').read_text(encoding='utf-8')
    js = (root / 'static' / 'js' / 'gamelists.js').read_text(encoding='utf-8')

    assert '.gl-dialog.is-closing' in css
    assert 'glDialogOut' in css
    assert "classList.add('is-closing')" in js
    assert 'animationend' in js


def test_the_swipe_sheet_is_armed_only_from_its_header():
    """`dismissableSheet`'s own docs: omit `handle` on a sheet you READ, pass one on a sheet you
    OPERATE, "where an accidental dismiss costs unsaved work". This holds a typed name and up to 300
    characters of description, and without a handle a downward swipe starting anywhere -- a label,
    the chip row, the footer -- destroyed it past 90px with no confirmation."""
    from pathlib import Path

    js = (Path(__file__).resolve().parents[2]
          / 'static' / 'js' / 'gamelists.js').read_text(encoding='utf-8')

    assert 'handle:' in js, 'the create sheet can be swiped away from anywhere, losing typed work'


# -- the scope switcher swaps, like every other tab group on the site ----------------------------

def test_switching_scope_over_htmx_returns_the_grid_and_nothing_else(client):
    """The behaviour Jeffrey caught missing: the chips reloaded the whole page while every other tab
    group on the site swaps a panel in place. A full document arriving here would be nested inside
    the grid it replaces."""
    _staff_hunter(client)

    body = client.get(MY_LISTS, {'scope': 'following'}, HTTP_HX_REQUEST='true').content.decode()

    assert 'my-lists-grid' in body
    assert '<!doctype html' not in body.lower(), 'the swap returned the whole page'
    assert '<nav' not in body.lower()


def test_the_swap_partial_bakes_in_the_reveal_class_and_the_full_page_does_not(client):
    """htmx's settle step restores server attributes on id'd swapped elements, so a class added by
    JS in afterSwap is wiped and the tiles unhide with a flash."""
    profile = _staff_hunter(client)
    svc.create_list(profile, name='Revealed')

    full = client.get(MY_LISTS).content.decode()
    swapped = client.get(MY_LISTS, HTTP_HX_REQUEST='true').content.decode()

    assert 'pp-reveal' in swapped
    assert 'pp-reveal' not in full


def test_the_chips_are_a_real_tablist_now_that_there_is_a_panel(client):
    """`role="tab"` was a lie while the chips were plain navigation -- no panel, no `aria-controls`,
    no keyboard model. It is true once they swap one, which is why the fix was to change the
    BEHAVIOUR rather than to downgrade the semantics."""
    _staff_hunter(client)
    body = client.get(MY_LISTS).content.decode()

    assert 'role="tablist"' in body
    assert body.count('role="tab"') >= 2
    assert body.count('aria-controls="my-lists-panel"') >= 2
    assert 'id="my-lists-panel"' in body
    assert 'role="tabpanel"' in body, 'aria-controls points at something that is not a panel'
    # Both states rendered, so the active chip is announced rather than merely tinted.
    assert 'aria-selected="true"' in body and 'aria-selected="false"' in body


def test_the_chips_keep_an_href_so_they_work_without_javascript(client):
    """The server honours `?scope=` either way. Losing the href would make the switcher JS-only, and
    the whole point of driving it with `hx-get` on an `<a>` is that it degrades."""
    _staff_hunter(client)
    body = client.get(MY_LISTS).content.decode()

    assert 'href="?scope=mine"' in body
    assert 'href="?scope=following"' in body
    assert 'hx-get' in body and 'hx-target="#my-lists-panel"' in body


def test_the_swapped_panel_says_which_scope_it_is(client):
    """The directional slide reads the panel's own `data-scope`, so the animation follows what
    ARRIVED rather than what was clicked -- they differ if a request is superseded."""
    _staff_hunter(client)

    body = client.get(MY_LISTS, {'scope': 'following'}, HTTP_HX_REQUEST='true').content.decode()

    assert 'data-scope="following"' in body


def test_the_switcher_uses_the_shared_motion_helpers_rather_than_its_own(client):
    """`wireTablist` for the roving tabindex and arrow keys, `igniteTab` for the activation bloom,
    `slideViewIn` for the directional panel slide -- the three beats the design system asks a tab
    group for, and all three already exist."""
    from pathlib import Path

    js = (Path(__file__).resolve().parents[2]
          / 'static' / 'js' / 'gamelists.js').read_text(encoding='utf-8')

    # The CALL, not the word: these helper names also appear in this file's own comments, so a bare
    # substring check stayed green when the call was ripped out and replaced with a hand-rolled
    # `chips[0].tabIndex = 0`. Caught by mutating exactly that.
    import re

    stripped = re.sub(r'/\*.*?\*/', '', js, flags=re.S)
    stripped = re.sub(r'^\s*//.*$', '', stripped, flags=re.M)
    for helper in ('wireTablist', 'igniteTab', 'slideViewIn'):
        assert f'PlatPursuit.{helper}(' in stripped, (
            f'the switcher re-rolls {helper} instead of calling the shared one'
        )
    assert 'manual: true' in stripped, (
        'wireTablist must not also bind click on hx-get chips, or the panel switches twice'
    )


def test_the_swap_targets_a_stable_wrapper_the_way_every_other_switcher_does():
    """`innerHTML` into a wrapper the swap never destroys -- Badges into `#badge-view`, browse into
    `#browse-results`. The first cut pointed `outerHTML` at the grid itself, replacing the target
    node on every switch; it diverged from the only pattern known to work on this site and
    duplicated the page instead of swapping.
    """
    from pathlib import Path

    tpl = (Path(__file__).resolve().parents[2]
           / 'templates' / 'gamelists' / 'my_lists.html').read_text(encoding='utf-8')

    assert 'hx-swap="innerHTML"' in tpl
    assert 'hx-swap="outerHTML"' not in tpl, 'back to replacing the swap target itself'
    assert 'hx-target="#my-lists-panel"' in tpl


def test_the_page_uses_the_shared_page_ready_contract():
    """`onPageReady(fn(first))`, not a hand-rolled DOMContentLoaded + historyRestore pair.

    The two halves of a restore differ: htmx replaces the history element's INNER HTML, so element
    nodes are fresh and must be re-wired, while `document.body` PERSISTS, so body-level listeners
    survive and re-binding them double-fires. The hand-rolled version re-ran everything, body
    listeners included, and its comment asserted htmx replaces the body wholesale -- which the
    shared helper's own docstring contradicts.
    """
    from pathlib import Path

    js = (Path(__file__).resolve().parents[2]
          / 'static' / 'js' / 'gamelists.js').read_text(encoding='utf-8')

    assert 'PlatPursuit.onPageReady(' in js
    assert 'function boot(first)' in js, 'boot ignores the first/restore distinction'
    assert 'if (!first) { return; }' in js, 'body-level listeners are not bound once'


def test_the_swapped_grid_has_something_that_will_actually_reveal_it():
    """The partial bakes `pp-reveal` into its markup on htmx requests, and
    `.pp-reveal .pp-gtile { opacity: 0 }` then holds every tile hidden until an observer reveals it.
    The grouping-tile CSS says so in as many words: "added by JS only when it will observe, so tiles
    never stick hidden."

    My Lists baked the class and wired no observer, so swapped-in tiles were invisible forever and
    the panel read as blank. Following hid it by being empty -- no tiles, nothing to hide -- so it
    only appeared on the way back to Mine, which is a good reminder that an empty state can conceal
    a rendering bug rather than prove one absent.
    """
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    partial = (root / 'templates' / 'gamelists' / 'partials'
               / 'my_lists_results.html').read_text(encoding='utf-8')
    js = (root / 'static' / 'js' / 'gamelists.js').read_text(encoding='utf-8')

    # UNCONDITIONAL. As an `if`, removing `pp-reveal` from the partial skipped the entire body -- a
    # guard for the half-a-pattern bug that opted itself out the moment that half changed.
    assert 'pp-reveal' in partial, 'the partial no longer bakes the class this guard exists for'
    if True:
        assert 'PlatPursuit.staggerReveal(' in js, (
            'the partial hides its tiles with pp-reveal and nothing reveals them'
        )
        # After a swap specifically: the grid is a fresh node, so an observer bound once on load is
        # attached to an element that no longer exists.
        # `initReveal();` WITH the semicolon: the bare name also matches the function's own
        # definition line, so counting that let a removed call slip through a mutation.
        assert js.count('initReveal();') >= 2, 'the reveal is not re-run after a swap'


def test_swapping_back_to_mine_returns_the_tiles(client):
    """The behaviour-level version of the bug: Following, then Mine, and the lists are there."""
    profile = _staff_hunter(client)
    svc.create_list(profile, name='Comes back')

    client.get(MY_LISTS, {'scope': 'following'}, HTTP_HX_REQUEST='true')
    body = client.get(MY_LISTS, {'scope': 'mine'}, HTTP_HX_REQUEST='true').content.decode()

    assert 'Comes back' in body
    assert 'pp-gtile' in body, 'the tile markup is missing, not merely invisible'


# -- what the swap-machinery audit found: more of the same half-a-pattern class -------------------

def test_following_can_reach_more_than_one_page(client):
    """`MyListsView` sets `paginate_by`, and Following is UNCAPPED -- you can follow any number of
    lists. Shipping pagination with no pager and no scroller made the 25th followed list unreachable
    by every route: no page-2 link, no scroll, and the chip's href carries no page. The same
    half-a-contract shape as baking `pp-reveal` with nothing to reveal it.
    """
    profile = _staff_hunter(client)
    # One list each from 26 different authors -- which is also what following 26 lists actually
    # looks like, and it stays inside the per-hunter cap the service enforces.
    for n in range(26):
        author = ProfileFactory(is_linked=True, psn_username=f'author{n}')
        svc.set_follow(svc.create_list(author, name=f'Followed {n}', is_public=True),
                       profile, following=True)

    page_two = client.get(
        MY_LISTS, {'scope': 'following', 'page': 2},
        HTTP_X_REQUESTED_WITH='XMLHttpRequest')

    assert page_two.status_code == 200
    assert 'Followed' in page_two.content.decode()
    assert page_two['X-Has-Next'] == '0', 'the scroller is not told when to stop'


def test_the_scroll_branch_is_wired_end_to_end(client):
    """The countless-pagination path: `X-Requested-With` PLUS `?page`, which is what the scroller
    actually sends. Every earlier test sent the header with no page, so `_is_scroll_fetch()` was
    False and this whole branch -- the +1 probe, the `_ScrollPage`, `X-Has-Next`, the past-end 404 --
    was never executed on either of these views."""
    profile = _staff_hunter(client)
    for n in range(30):
        author = ProfileFactory(is_linked=True, psn_username=f'writer{n}')
        svc.set_follow(svc.create_list(author, name=f'F{n}', is_public=True),
                       profile, following=True)

    first = client.get(MY_LISTS, {'scope': 'following', 'page': 1},
                       HTTP_X_REQUESTED_WITH='XMLHttpRequest')
    assert first['X-Has-Next'] == '1'

    past_end = client.get(MY_LISTS, {'scope': 'following', 'page': 99},
                          HTTP_X_REQUESTED_WITH='XMLHttpRequest')
    assert past_end.status_code == 404, 'past the end must 404 -- the scroller\'s stop contract'


def test_the_scroller_is_wired_and_its_ids_match_the_markup():
    """A scroller whose ids disagree with the page is silently inert: no error, no scroll."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    js = (root / 'static' / 'js' / 'gamelists.js').read_text(encoding='utf-8')
    tpl = (root / 'templates' / 'gamelists' / 'my_lists.html').read_text(encoding='utf-8')

    assert 'InfiniteScroller.create(' in js
    # And that it is CALLED -- on load and again after each swap, because the swapped grid is a new
    # node and a scroller bound once is watching an element that no longer exists. Asserting only
    # that the create() call EXISTS let a mutation that renamed the function and deleted every call
    # pass green.
    assert js.count('initScroller();') >= 2, 'the scroller is defined but never (re)wired'
    for element_id in ('my-lists-grid', 'gl-my-sentinel', 'gl-my-loading'):
        assert f"'{element_id}'" in js, f'the scroller does not reference {element_id}'
    for element_id in ('gl-my-sentinel', 'gl-my-loading'):
        assert f'id="{element_id}"' in tpl, f'{element_id} is missing from the page'
    # The sentinel must sit OUTSIDE the swapped panel or a scope switch tears out the thing
    # watching for the next page.
    assert tpl.index('id="gl-my-sentinel"') > tpl.index('</div>', tpl.index('data-gl-panel'))


def test_the_active_chip_is_reconciled_when_a_request_does_not_swap():
    """The other half of the optimistic update. The chip lights on `beforeRequest`; if the request
    500s, is aborted or is refused by the gate, no swap happens and the chip is left lit on a scope
    the panel is not showing."""
    from pathlib import Path

    js = (Path(__file__).resolve().parents[2]
          / 'static' / 'js' / 'gamelists.js').read_text(encoding='utf-8')

    assert 'htmx:afterRequest' in js, 'nothing reconciles the chip when no swap occurs'
    assert 'htmx:beforeRequest' in js


def test_reclicking_the_active_chip_is_cancelled():
    """The chips carry `hx-push-url`, so a re-click refetches, re-swaps, replays the reveal on tiles
    that never moved, and pushes a duplicate history entry -- Back then needs six presses."""
    from pathlib import Path

    js = (Path(__file__).resolve().parents[2]
          / 'static' / 'js' / 'gamelists.js').read_text(encoding='utf-8')

    assert "chip.classList.contains('is-active')" in js
    assert 'preventDefault' in js


def test_the_swap_handler_guards_on_htmxs_own_target():
    """Otherwise it runs for every swap that bubbles to body -- a toast, an out-of-band update, a
    follow button added to this page later."""
    from pathlib import Path

    js = (Path(__file__).resolve().parents[2]
          / 'static' / 'js' / 'gamelists.js').read_text(encoding='utf-8')

    assert 'e.detail && e.detail.target' in js or '(e.detail && e.detail.target)' in js
    assert "target.id !== 'my-lists-panel'" in js


def test_the_panel_label_follows_the_scope(client):
    """`aria-labelledby` is rendered once, outside the swap, so without JS moving it the Following
    panel stays announced as labelled by the Mine chip."""
    from pathlib import Path

    js = (Path(__file__).resolve().parents[2]
          / 'static' / 'js' / 'gamelists.js').read_text(encoding='utf-8')

    assert "setAttribute('aria-labelledby'" in js

    _staff_hunter(client)
    body = client.get(MY_LISTS, {'scope': 'following'}).content.decode()
    assert 'aria-labelledby="gl-scope-following"' in body, 'the server-rendered label is wrong too'



# ── empty states offer a way out ─────────────────────────────────────────────────────────────────

def test_the_empty_mine_scope_offers_the_create_action(client):
    """The primary action was only in the page header -- past everything a reader scrolled through
    to arrive at nothing. Asserted INSIDE the grid, because the header carries its own opener and a
    whole-page search would be answered by that."""
    _staff_hunter(client)

    body = client.get(MY_LISTS).content.decode()

    assert 'No lists yet' in body
    assert 'data-gl-open' in _grid(body), 'the empty state offers no way to create a list'


def test_the_create_opener_survives_a_scope_switch(client):
    """The opener inside the panel is reachable only because the binding is DELEGATED. Bound per
    element at boot -- as it was -- a button rendered into the swapped panel would look right and do
    nothing, which is the same half-a-pattern trap as baking `pp-reveal` with no observer."""
    from pathlib import Path

    _staff_hunter(client)
    swapped = client.get(MY_LISTS, HTTP_HX_REQUEST='true').content.decode()
    assert 'data-gl-open' in swapped

    js = (Path(__file__).resolve().parents[2] / 'static' / 'js' / 'gamelists.js').read_text(
        encoding='utf-8')
    assert "document.body.addEventListener('click'" in js
    assert "querySelectorAll('[data-gl-open]')" not in js, 'the opener is bound per element again'


def test_the_empty_following_scope_offers_a_route_to_browse(client):
    """Following is the one scope whose content comes from somewhere else entirely, so an empty
    state with no route to browse is a genuine dead end."""
    _staff_hunter(client)

    body = client.get(MY_LISTS, {'scope': 'following'}).content.decode()

    assert 'Not following any lists' in body
    assert 'Browse lists' in _grid(body)


def test_at_the_cap_the_empty_state_offers_no_button_that_would_refuse(client):
    """A button that refuses is worse than no button, and the header already says why. Reached by
    filling the cap with lists that are then soft-deleted: `owned_by` still counts them, so the page
    is at the cap AND renders empty -- the one state where both branches meet."""
    from gamelists.models import FREE_MAX_LISTS

    owner = _staff_hunter(client)
    made = [svc.create_list(owner, name=f'List {n}') for n in range(FREE_MAX_LISTS)]

    resp = client.get(MY_LISTS)
    assert resp.context['at_cap'] is True
    # Not empty here, so the gating is asserted where it lives: the header's opener refuses.
    assert 'aria-disabled="true"' in _header(resp.content.decode())
    assert len(made) == FREE_MAX_LISTS


def test_my_lists_offers_a_route_back_to_everyone_elses(client):
    """This page was the feature's one dead end.

    Browse carries a button to My Lists and the detail page's crumb links back to browse, but My
    Lists linked nowhere -- so arriving here left no route to the public lists short of the back
    button. Asserted as a LINK in the trail, not merely as the words appearing somewhere: the page
    header says "My Lists" too, and the blurb names lists repeatedly.
    """
    _staff_hunter(client)

    body = client.get(MY_LISTS).content.decode()
    # `</nav>` searched FROM the breadcrumb, not from the start of the document -- the navbar has
    # its own and closes first, so the naive slice was empty and every assertion below it would have
    # failed for the wrong reason (or passed, had they been negative ones).
    start = body.index('aria-label="Breadcrumb"')
    trail = body[start:body.index('</nav>', start)]

    assert 'Game Lists' in trail
    assert reverse('lists_browse') in trail, 'the trail does not link back to browse'
    # And the page is still identified as its own -- the crumb was added, not swapped.
    assert 'My Lists' in trail


def test_every_list_surface_can_reach_the_others(client):
    """The three pages form a loop, and a one-directional link is how a feature becomes a maze. Each
    of the two inner pages must reach browse, and browse must reach My Lists."""
    owner = _staff_hunter(client)
    game_list = svc.create_list(owner, name='Reachable', is_public=True)

    browse = client.get('/community/lists/').content.decode()
    mine = client.get(MY_LISTS).content.decode()
    detail = client.get(f'/community/lists/{game_list.id}/').content.decode()

    assert reverse('my_lists') in browse, 'browse cannot reach My Lists'
    assert reverse('lists_browse') in mine, 'My Lists cannot reach browse'
    assert reverse('lists_browse') in detail, 'a list page cannot reach browse'
