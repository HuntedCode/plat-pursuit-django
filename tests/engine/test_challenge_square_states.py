"""A run page's three square states, made easy to tell apart (owner, 2026-10-17): a finished square wears a
platinum FOIL, an in-progress one a state TAG in the check's corner, an empty one neither. Plus the team's
`?preview=challenge-states` door, which redraws the board as a fixed mix of all three and writes nothing.
"""
import re
from pathlib import Path

import pytest
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone

from challenges.models import CHALLENGE_TYPE_AZ, CHALLENGE_TYPE_JOBS, COMPLETED_VIA_LIVE, ChallengeSlot
from challenges.services import challenge_service as svc
from challenges.services import slot_render
from tests.factories import GameFactory, ProfileFactory, UserFactory

CSS = Path('static/css/components/challenges.css').read_text(encoding='utf-8')


def _card(**kw):
    card = {'key': 'A', 'label': 'A', 'job': None, 'is_filled': False, 'is_completed': False,
            'game_name': '', 'cover': None, 'xp_pending': False, 'index': 0}
    card.update(kw)
    return card


def _square(card):
    return render_to_string('challenges/partials/_square_body.html', {'card': card, 'is_owner': True})


# --- the three states -------------------------------------------------------------------------------------

def test_a_finished_square_wears_the_foil_over_its_art():
    html = _square(_card(is_filled=True, is_completed=True, game_name='Astro Bot',
                         cover=type('G', (), {'display_image_url': '/x.jpg', 'has_cover_art': True})()))
    assert 'class="pp-csq__foil"' in html and 'class="pp-csq__gleam"' in html
    # After the art; it paints above the scrim by z-index (1) and below the name (the body's 2).
    assert html.index('pp-csq__art') < html.index('pp-csq__foil') < html.index('pp-csq__body')
    assert 'pp-csq__check' in html and 'pp-csq__tag' not in html


def test_a_finished_square_without_art_still_wears_the_foil():
    html = _square(_card(is_filled=True, is_completed=True, game_name='Astro Bot'))
    assert html.index('pp-csq__noart') < html.index('pp-csq__foil')


def test_an_in_progress_square_carries_the_tag_in_the_checks_corner():
    html = _square(_card(is_filled=True, game_name='Astro Bot'))
    assert 'pp-csq__tag' in html and 'In progress' in html
    assert 'pp-csq__foil' not in html and 'pp-csq__check' not in html


def test_an_empty_square_carries_neither():
    html = _square(_card())
    assert 'pp-csq__foil' not in html and 'pp-csq__tag' not in html and 'pp-csq__check' not in html


def test_the_tag_and_foil_are_decoration_the_spoken_line_already_carries():
    done = _square(_card(is_filled=True, is_completed=True, game_name='Astro Bot'))
    progress = _square(_card(is_filled=True, game_name='Astro Bot'))
    assert 'class="pp-csq__foil" aria-hidden="true"' in done
    assert 'class="pp-csq__tag" aria-hidden="true"' in progress
    assert 'Astro Bot, finished' in done and 'Astro Bot, in progress' in progress


# --- the motion and its restraint -------------------------------------------------------------------------

def _rule(selector):
    start = CSS.index(selector)
    return CSS[start:CSS.index('}', start)]


def test_the_foils_own_motion_plays_once_and_has_no_hover_rule():
    block = CSS[CSS.index('THE FOIL: a finished square'):CSS.index('IN PROGRESS: the state tag')]
    assert 'infinite' not in block and 'filter:' not in block.replace('mix-blend', '')
    motion = block[block.index('@media (prefers-reduced-motion: no-preference) {'):]
    assert 'animation-delay: calc(0.5s + min(var(--rev, 0), 12) * 0.035s);' in motion   # after the square lands
    # No hover SPARKLE: the only thing that answers the cursor is the inspect-only tilt, driven from script.
    assert ':hover' not in block
    keyframes = block[block.index('@keyframes ppCsqGleam'):]
    keyframes = keyframes[:keyframes.index('}\n}') + 3]
    # Transform only, and no color-mix inside a keyframe (lightningcss drops those frames).
    assert 'transform' in keyframes and 'opacity' not in keyframes and 'color-mix' not in keyframes


def test_the_foil_reads_on_dark_art_and_carries_a_ring():
    foil = _rule('.pp-csq__foil {')
    # `screen` can only lighten, so it shows on the dark covers `overlay` vanished into.
    assert 'mix-blend-mode: screen;' in foil and 'z-index: 1;' in foil
    ring = _rule('.pp-csq__foil::before {')
    assert 'conic-gradient(' in ring and 'mask-composite: xor;' in ring and 'padding: 2.5px;' in ring
    arrival = CSS[CSS.index('.pp-csq-board:not(.is-settled) .pp-csq__gleam,'):]
    assert arrival.index('.pp-csq--fresh .pp-csq__gleam {') < arrival.index('animation: ppCsqGleam 0.9s')


def test_the_tag_label_shows_only_where_a_square_can_hold_it():
    assert '.pp-csq__tag-lbl { display: none; }' in CSS
    lg = CSS[CSS.index('.pp-csq__tag-lbl { display: none; }'):]
    assert re.search(r'@media \(min-width: 1280px\) \{[^@]*\.pp-csq__tag-lbl \{ display: inline; \}', lg)
    # Where the words show, the key chip yields them the room (a deleted job's long name would run into them).
    assert '.pp-csq--filled .pp-csq__key { max-width: calc(100% - 112px); }' in lg


# --- the preview door -------------------------------------------------------------------------------------

def _groups(count):
    cards = [_card(key=chr(65 + i), label=chr(65 + i), index=i) for i in range(count)]
    return [{'label': '', 'slug': '', 'icon': '', 'cards': cards, 'done': 0, 'total': count, 'dom_id': 'g'}]


@pytest.mark.django_db
def test_the_preview_mixes_all_three_states_in_every_row():
    for _ in range(5):   # squares 0, 1, 3, 4 and 6 are filled by the mix
        GameFactory()
    groups = slot_render.preview_state_groups(_groups(7))
    states = ['done' if c['is_completed'] else 'progress' if c['is_filled'] else 'empty' for c in groups[0]['cards']]
    assert states == ['done', 'progress', 'empty', 'done', 'progress', 'empty', 'done']
    assert groups[0]['done'] == 3                                   # the shelf count follows what it shows
    filled = [c for c in groups[0]['cards'] if c['is_filled']]
    assert all(c['cover'] is not None and c['game_name'] for c in filled)   # borrowed real covers
    assert not any(c['xp_pending'] for c in groups[0]['cards'])


@pytest.mark.django_db
def test_the_preview_keeps_a_squares_own_game_and_borrows_only_for_the_gaps(django_assert_num_queries):
    groups = _groups(3)
    own = object()
    groups[0]['cards'][0].update(is_filled=True, cover=own, game_name='Mine')
    with django_assert_num_queries(1):   # ONE bounded read for the one square that needs a cover
        slot_render.preview_state_groups(groups)
    assert groups[0]['cards'][0]['cover'] is own and groups[0]['cards'][0]['game_name'] == 'Mine'


@pytest.mark.django_db
def test_a_preview_with_nothing_to_borrow_needs_no_query(django_assert_num_queries):
    groups = _groups(3)
    for card in groups[0]['cards']:
        card.update(is_filled=True, cover=object(), game_name='Mine')
    with django_assert_num_queries(0):
        slot_render.preview_state_groups(groups)


def _team(client, *, staff=True):
    user = UserFactory(is_staff=staff)
    profile = ProfileFactory(user=user, user_is_premium=True)
    client.force_login(user)
    return profile


@pytest.mark.django_db
@pytest.mark.parametrize('run_type', [CHALLENGE_TYPE_AZ, CHALLENGE_TYPE_JOBS])
def test_the_team_preview_redraws_the_board_read_only_and_writes_nothing(client, run_type):
    profile = _team(client)
    GameFactory()
    run = svc.start(profile, run_type)
    before = list(ChallengeSlot.objects.filter(challenge=run).values_list('key', 'contract_slug', 'is_completed'))

    resp = client.get(reverse('challenge_detail', args=[run.id]) + '?preview=challenge-states')
    html = resp.content.decode()

    cards = [c for g in resp.context['groups'] for c in g['cards']]
    assert sum(c['is_completed'] for c in cards) == len(cards[0::3])
    assert 'pp-csq__foil' in html and 'pp-csq__tag' in html
    # READ-ONLY while it shows: no square opens the picker on a slot it no longer describes.
    assert resp.context['can_edit'] is False and 'data-cpick-open' not in html
    after = list(ChallengeSlot.objects.filter(challenge=run).values_list('key', 'contract_slug', 'is_completed'))
    assert after == before


@pytest.mark.django_db
def test_the_preview_is_the_teams_only(client):
    profile = _team(client, staff=False)
    run = svc.start(profile, CHALLENGE_TYPE_AZ)
    resp = client.get(reverse('challenge_detail', args=[run.id]) + '?preview=challenge-states')
    assert not any(c['is_completed'] for g in resp.context['groups'] for c in g['cards'])
    assert resp.context['can_edit'] is True


@pytest.mark.django_db
def test_a_real_finished_square_wears_the_foil_on_the_page(client):
    profile = _team(client, staff=False)
    run = svc.start(profile, CHALLENGE_TYPE_AZ)
    ChallengeSlot.objects.filter(challenge=run, key='A').update(
        is_completed=True, contract_name='Astro Bot', contract_slug='astro-bot',
        completed_at=timezone.now(), completed_via=COMPLETED_VIA_LIVE)
    html = client.get(reverse('challenge_detail', args=[run.id])).content.decode()
    assert html.count('class="pp-csq__foil"') == 1



def test_a_claim_re_render_never_replays_the_sweep():
    js = Path('static/js/challenge-detail.js').read_text(encoding='utf-8')
    boot = js[js.index('function boot(first) {'):]
    # The settle runs for EVERY viewer: before the picker's guard sends a visitor home.
    assert boot.index("settling.classList.add('is-settled');") < boot.index("if (!dialog || !grid || !dialog.showModal")
    # ... and counts from when the page is SEEN (a background tab runs timers but not CSS animations).
    assert "document.addEventListener('visibilitychange', onShow);" in boot
    # ... and the listener takes itself off once the page shows, so it settles once and never lingers.
    show = boot[boot.index('var onShow = function () {'):]
    show = show[:show.index('};')]
    assert show.index("document.removeEventListener('visibilitychange', onShow);") < show.index('settleSoon();')
    apply = js[js.index('function applySlot('):]
    apply = apply[:apply.index('if (slot.is_completed) {\n                    // ONLY WHEN')]
    assert "square.classList.add('pp-csq--fresh');" in apply
    assert "square.classList.remove('pp-csq--fresh');" in apply
    # A claim settles the board and strips the fresh mark BEFORE it swaps the square's markup in.
    claim = js[js.index('A CLAIM IS NOT A COMPLETION'):]
    claim = claim[:claim.index('AND THE NAV MARKER')]
    assert "board.classList.add('is-settled');" in claim
    assert claim.index("cell.classList.remove('pp-csq--fresh');") < claim.index('cell.innerHTML = square.html;')


@pytest.mark.django_db
def test_the_preview_takes_the_claim_buttons_away(client):
    from trophies.models import Job
    profile = _team(client)
    run = svc.start(profile, CHALLENGE_TYPE_JOBS)
    job_key = ChallengeSlot.objects.filter(challenge=run).values_list('key', flat=True).first()
    assert Job.objects.filter(slug=job_key).exists()
    # A finished square with unclaimed XP: the real page offers its Claim button.
    ChallengeSlot.objects.filter(challenge=run, key=job_key).update(
        is_completed=True, contract_name='Astro Bot', contract_slug='astro-bot',
        completed_at=timezone.now(), completed_via=COMPLETED_VIA_LIVE)

    real = client.get(reverse('challenge_detail', args=[run.id]))
    preview = client.get(reverse('challenge_detail', args=[run.id]) + '?preview=challenge-states')

    assert 'data-cpay-claim' in real.content.decode()
    assert 'data-cpay-claim' not in preview.content.decode()
    assert real.context['claims_open'] is True and preview.context['claims_open'] is False


def test_the_job_line_on_a_finished_square_is_lifted_for_contrast():
    assert '.pp-csq--done .pp-csq__job { color: color-mix(in oklab, var(--disc, var(--pp-primary)) 24%, #fff); }' in CSS


# --- the share card wears it too (owner, 2026-10-17) ------------------------------------------------------

CARD_SQUARE = Path('templates/shareables/partials/_challenge_card_square.html').read_text(encoding='utf-8')


def _card_square(state, is_complete=False):
    sq = {'key': 'A', 'state': state, 'cover': 'https://img.example/a.png' if state != 'open' else '',
          'cover_is_art': True, 'is_job': False, 'glyph': '', 'colour': ''}
    return render_to_string('shareables/partials/_challenge_card_square.html',
                            {'sq': sq, 'w': 80, 'h': 107, 'mark_px': 22, 'well_px': 34, 'is_complete': is_complete})


def test_a_finished_card_square_wears_the_foil_and_nothing_else_does():
    done = _card_square('done')
    assert 'mix-blend-mode: screen' in done and 'conic-gradient(' in done and 'isolation: isolate' in done
    # Over the scrim, under the mark, as on the page.
    assert done.index('rgba(5, 8, 12, 0.92)') < done.index('mix-blend-mode: screen') < done.index('>A</span>')
    assert 'mix-blend-mode' not in _card_square('assigned') and 'mix-blend-mode' not in _card_square('open')


def test_a_finished_runs_card_is_the_full_holo_board():
    # Every square of a finished run is done, so every square is foiled (and the check is the live run's only).
    html = _card_square('done', is_complete=True)
    assert 'mix-blend-mode: screen' in html and 'M20 6 9 17l-5-5' not in html


def _call(source, name):
    """The full `name(...)` expression starting at its first occurrence, parentheses matched, whitespace collapsed."""
    start = source.index(name + '(')
    depth = 0
    for i in range(start, len(source)):
        depth += {'(': 1, ')': -1}.get(source[i], 0)
        if depth == 0 and source[i] == ')':
            return ' '.join(source[start:i + 1].split())
    raise AssertionError('unbalanced ' + name)


def test_the_page_and_the_card_share_one_foil_recipe():
    # Two copies (the card renders with no stylesheet), so the WHOLE recipe is pinned to agree: every stop, angle,
    # the grain, the ring's gradient, its padding and its mask. Change one copy alone and this fails.
    foil = CSS[CSS.index('.pp-csq__foil {'):CSS.index('.pp-csq--done { isolation: isolate; }')]
    card = CARD_SQUARE[CARD_SQUARE.index("{% if sq.state == 'done' %}<div"):]
    for name in ('repeating-linear-gradient', 'linear-gradient(60deg', 'linear-gradient(120deg', 'conic-gradient'):
        fn = name.split('(')[0]
        page_expr = _call(foil[foil.index(name):], fn)
        card_expr = _call(card[card.index(name):], fn)
        assert page_expr == card_expr, (page_expr, card_expr)
    for declaration in ('padding: 2.5px;', 'mix-blend-mode: screen;',
                        '-webkit-mask: linear-gradient(#000 0 0) content-box, linear-gradient(#000 0 0);',
                        '-webkit-mask-composite: xor;',
                        'mask: linear-gradient(#000 0 0) content-box exclude, linear-gradient(#000 0 0);'):
        assert declaration in foil and declaration in card, declaration


@pytest.mark.django_db
def test_the_preview_door_reaches_the_share_card(client):
    profile = _team(client)
    GameFactory()
    run = svc.start(profile, CHALLENGE_TYPE_AZ)
    page = client.get(reverse('challenge_detail', args=[run.id]) + '?preview=challenge-states').content.decode()
    # The share button carries the door onto BOTH card URLs ...
    button = page[page.index('data-challenge-share'):]
    button = button[:button.index('>')]
    assert button.count('?preview=challenge-states"') == 2
    html = json_body(client.get(reverse('challenge_card_html', args=[run.id]) + '?preview=challenge-states'))['html']
    real = json_body(client.get(reverse('challenge_card_html', args=[run.id])))['html']
    # ... and the card draws the same mix: finished squares foiled, where the real fresh run has none.
    assert 'mix-blend-mode: screen' in html and 'mix-blend-mode: screen' not in real


@pytest.mark.django_db
def test_the_share_button_carries_no_door_off_the_preview(client):
    profile = _team(client)
    run = svc.start(profile, CHALLENGE_TYPE_AZ)
    page = client.get(reverse('challenge_detail', args=[run.id])).content.decode()
    assert '?preview=' not in page[page.index('data-challenge-share'):][:400]


def json_body(resp):
    import json
    return json.loads(resp.content)


def test_the_png_url_joins_its_theme_onto_an_existing_query():
    js = Path('static/js/challenge-share.js').read_text(encoding='utf-8')
    png = js[js.index('function pngUrl() {'):]
    png = png[:png.index('\n    }\n')]
    assert "var join = current.pngUrl.indexOf('?') === -1 ? '?' : '&';" in png
    assert "join + 'theme=' + encodeURIComponent(choice.value)" in png


# --- the foil under the pointer (owner, 2026-10-17) -------------------------------------------------------

def test_the_tilt_is_for_fine_pointers_only_and_never_under_reduced_motion():
    js = Path('static/js/challenge-detail.js').read_text(encoding='utf-8')
    tilt = js[js.index('function wireFoilTilt(board) {'):js.index('function boot(first) {')]
    assert "!window.matchMedia('(prefers-reduced-motion: reduce)').matches" in tilt
    assert "window.matchMedia('(any-hover: hover) and (any-pointer: fine)').matches" in tilt
    # Touch never drives it, even on a device that also has a mouse.
    assert "if (e.pointerType && e.pointerType !== 'mouse' && e.pointerType !== 'pen') { return; }" in tilt


def test_one_listener_on_the_board_writes_at_most_once_a_frame():
    js = Path('static/js/challenge-detail.js').read_text(encoding='utf-8')
    tilt = js[js.index('function wireFoilTilt(board) {'):js.index('function boot(first) {')]
    assert "board.addEventListener('pointermove'," in tilt and "board.addEventListener('pointerleave', release);" in tilt
    assert "e.target.closest('.pp-csq--done')" in tilt                     # finished squares only
    assert 'if (!frame) { frame = window.requestAnimationFrame(paint); }' in tilt
    # Measured off the square's CELL, which never moves (the square shrinks while arriving and rotates while
    # tilting), and in the frame callback, so a scroll under a resting pointer is corrected on the next move.
    paint = tilt[tilt.index('function paint() {'):tilt.index("board.addEventListener('pointermove',")]
    assert 'var cell = (current.parentElement || current).getBoundingClientRect();' in paint
    assert 'square.getBoundingClientRect()' not in tilt
    # A square still arriving does not tilt (its entrance owns `transform`), and release leaves the position, so
    # the shine fades where it was.
    assert 'if (square && square !== current && arriving(square)) { square = null; }' in tilt
    # ... and "arriving" means the ENTRANCE ANIMATION only: CSS transitions (the tilt's own) must not count, or every
    # tilt cancels itself on the next move.
    assert "return a.playState === 'running' && a.animationName;" in tilt
    release = tilt[tilt.index('function release() {'):tilt.index('function paint() {')]
    assert "removeProperty('--px')" not in release
    boot = js[js.index('function boot(first) {'):]
    # Every viewer gets it (a visitor admiring a finished run too), before the picker's owner-only guard.
    assert boot.index('wireFoilTilt(settling);') < boot.index("if (!dialog || !grid || !dialog.showModal")


def test_the_tilt_is_subtle_and_inspect_only():
    tilting = _rule('.pp-csq.pp-csq--done.is-tilting {')
    # ~8 degrees each way (a 16deg span), not the medallion's 15: these sit tight in a grid.
    assert 'rotateX(calc((0.5 - var(--py, 0.5)) * 16deg))' in tilting
    assert 'rotateY(calc((var(--px, 0.5) - 0.5) * 16deg))' in tilting
    # Inspect-only: no lift, no shadow change, no pointer cursor on a finished square.
    block = CSS[CSS.index('THE FOIL UNDER THE POINTER'):CSS.index('@keyframes ppCsqGleam')]
    rules = re.sub(r'/\*.*?\*/', '', block, flags=re.S)   # declarations only, never the prose about them
    assert 'box-shadow:' not in rules and 'cursor:' not in rules
    assert '.pp-csq--done.is-tilting .pp-csq__foil::after { opacity: 1; }' in CSS


def test_the_shine_follows_the_pointer():
    shine = _rule('.pp-csq__foil::after {')
    assert 'radial-gradient(circle at calc(var(--px, 0.5) * 100%) calc(var(--py, 0.5) * 100%)' in ' '.join(shine.split())
    assert 'opacity: 0;' in shine
