"""The rebuilt recap deck's contracts -- the ones that break silently.

Every slide is now built on the shared `.rcp` shell. Three couplings hold the deck together and none of
them raise when broken; they just render wrong:

1. **Templates <-> controller.** The slides mark animated figures with `data-countup`, calendar cells with
   `.rcp-cal__cell`, and quiz choices with `data-quiz-option`. `monthly-recap.js` queries exactly those.
   A rename on either side is a no-op that looks fine in source and does nothing in the browser -- which
   is what happened when the deck was rebuilt while the controller still looked for `.count-up` and
   `.calendar-day`.

2. **Controller <-> stylesheet.** After an answer the controller adds `is-correct` / `is-wrong` /
   `is-dimmed` / `is-locked`. It used to add DaisyUI's `ring-2 ring-success bg-success/20`, which the
   rebuilt deck has no rule for, so an answered quiz showed no feedback at all.

3. **No DaisyUI left.** The whole point of the pass. A slide that reaches for `card`/`badge`/`btn`/
   `base-content` renders in the old vocabulary next to fifteen that don't.

Height and overflow are NOT asserted here -- those need a real layout engine and were verified by
rendering every slide at 375/768/1024 against the built stylesheet.
"""
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SLIDE_DIR = ROOT / 'templates' / 'recap' / 'partials' / 'slides'
DECK_CSS = ROOT / 'static' / 'css' / 'components' / 'recap-deck.css'
CONTROLLER = ROOT / 'static' / 'js' / 'monthly-recap.js'

SLIDES = sorted(SLIDE_DIR.glob('*.html'))
QUIZ_SLIDES = [p for p in SLIDES if p.name.startswith('quiz_')]

# Substrings that only appear in the pre-rebuild vocabulary. `card-body`/`card-title` rather than bare
# `card`, because "scard" and "cover__art" would false-positive on a naive `card` search.
LEGACY_MARKERS = [
    'base-content', 'base-100', 'base-200', 'base-300', 'bg-primary/', 'text-primary',
    'card-body', 'card-title', 'card-side', 'badge-lg', 'badge-outline', 'btn-outline',
    'stat-value', 'stat-title', 'stats-vertical', 'text-6xl', 'text-5xl',
]


def _code_only(path):
    """Source with comments, docstrings and `{% comment %}` blocks stripped. All three legitimately name
    the legacy tables and fields these tests forbid, in order to explain what replaced them -- without
    stripping them the tests fail on their own documentation."""
    src = path.read_text(encoding='utf-8')
    src = re.sub(r'{% comment %}.*?{% endcomment %}', '', src, flags=re.S)
    src = re.sub(r'"""(?:[^"\\]|\\.|"(?!""))*"""', '', src, flags=re.S)
    return '\n'.join(ln for ln in src.splitlines() if not ln.lstrip().startswith('#'))


@pytest.fixture(scope='module')
def controller():
    return CONTROLLER.read_text(encoding='utf-8')


@pytest.fixture(scope='module')
def deck_css():
    return DECK_CSS.read_text(encoding='utf-8')


def test_every_slide_exists():
    """A guard on the parametrised tests below: an empty glob would make them all vacuously pass."""
    assert len(SLIDES) == 16, f'expected 16 slides, found {[p.name for p in SLIDES]}'
    assert len(QUIZ_SLIDES) == 4


@pytest.mark.parametrize('slide', SLIDES, ids=lambda p: p.stem)
def test_slide_is_built_on_the_shared_shell(slide):
    html = slide.read_text(encoding='utf-8')
    assert re.search(r'class="rcp\b', html), (
        f'{slide.name} does not use the .rcp shell; it will not share the deck frame, padding or accent'
    )


@pytest.mark.parametrize('slide', SLIDES, ids=lambda p: p.stem)
def test_no_legacy_daisyui_vocabulary(slide):
    html = slide.read_text(encoding='utf-8')
    # Strip {% comment %} blocks: they legitimately discuss what was replaced.
    body = re.sub(r'{% comment %}.*?{% endcomment %}', '', html, flags=re.S)
    found = [m for m in LEGACY_MARKERS if m in body]
    assert not found, f'{slide.name} still uses pre-rebuild classes: {found}'


@pytest.mark.parametrize('slide', SLIDES, ids=lambda p: p.stem)
def test_no_multiline_hash_comments(slide):
    """Django's lexer matches `{#.*?#}` without DOTALL, so a `{# ... #}` spanning lines is not a comment
    and leaks onto the page. Caught in the wild during this rebuild."""
    for line in slide.read_text(encoding='utf-8').splitlines():
        if '{#' in line:
            assert '#}' in line, f'{slide.name}: multi-line {{# #}} comment leaks to the page'


# --- Templates <-> controller ---------------------------------------------------------------------

def test_countup_attribute_is_what_the_controller_reads(controller):
    """Both halves of the count-up contract, asserted together."""
    marked = [p.name for p in SLIDES if 'data-countup' in p.read_text(encoding='utf-8')]
    assert marked, 'no slide marks a figure with data-countup'
    assert "querySelectorAll('[data-countup]')" in controller
    assert 'PlatPursuit.countUp' in controller, 'the deck must delegate to the shared primitive'
    assert ".count-up'" not in controller, 'the private .count-up hook is gone from the templates'


def test_countup_figures_do_not_carry_their_own_sign_or_unit():
    """countUp replaces textContent wholesale, so a sign or suffix inside the animated node is eaten.
    Templates put them in siblings; this pins that they stay there."""
    for slide in SLIDES:
        for tag in re.findall(r'<span[^>]*data-countup[^>]*>(.*?)</span>', slide.read_text(encoding='utf-8')):
            assert not re.search(r'[%+]', tag), (
                f'{slide.name}: a data-countup node contains a sign or unit ({tag!r}); countUp will '
                f'overwrite it. Put it in a sibling .rcp__sign / .rcp__unit.'
            )


def test_calendar_cells_match_the_controllers_selectors(controller):
    html = (SLIDE_DIR / 'activity_calendar.html').read_text(encoding='utf-8')
    assert 'rcp-cal__cell' in html and 'rcp-cal__cell--plat' in html
    assert "querySelectorAll('.rcp-cal__cell')" in controller
    assert "querySelectorAll('.rcp-cal__cell--plat')" in controller
    assert 'calendar-day' not in controller, 'stale selector from the pre-rebuild calendar'


def test_platinum_days_are_focusable_controls():
    """They open a detail modal, so they must be reachable without a mouse. The pre-rebuild calendar made
    them clickable divs."""
    html = (SLIDE_DIR / 'activity_calendar.html').read_text(encoding='utf-8')
    plat_cell = re.search(r'<(\w+)[^>]*rcp-cal__cell--plat', html)
    assert plat_cell and plat_cell.group(1) == 'button', 'platinum days must be <button>, not a div/span'
    assert 'aria-label=' in html


# --- Quiz: templates <-> controller <-> stylesheet -------------------------------------------------

@pytest.mark.parametrize('slide', QUIZ_SLIDES, ids=lambda p: p.stem)
def test_quiz_dom_contract(slide):
    html = slide.read_text(encoding='utf-8')
    assert 'data-quiz-correct=' in html, f'{slide.name}: controller cannot grade without the answer'
    assert 'data-quiz-option=' in html, f'{slide.name}: no choices for the controller to bind'
    assert 'data-quiz-feedback' in html, f'{slide.name}: nowhere to render the verdict'
    assert 'rcp-quiz__opt' in html, f'{slide.name}: choices must use the shared option treatment'


@pytest.mark.parametrize('state', ['is-correct', 'is-wrong', 'is-dimmed', 'is-locked', 'is-selected'])
def test_every_answered_state_the_controller_writes_is_styled(controller, deck_css, state):
    # Scoped to the grading function, not the whole file: `is-correct` also appears on the verdict
    # container, so a file-wide search would still pass with the option grading broken.
    # Anchored to the method DEFINITION (line start, 4-space indent) -- an unanchored search matches the
    # call site inside handleSingleSelectAnswer first and captures the wrong function body.
    grading = re.search(r'\n    showSingleSelectFeedback\(.*?\n    \}\n', controller, re.S)
    assert grading, 'showSingleSelectFeedback not found'
    haystack = grading.group(0) if state != 'is-selected' else controller
    assert f"'{state}'" in haystack, f'controller never applies {state} where it grades an answer'
    assert f'.rcp-quiz__opt.{state}' in deck_css, (
        f'{state} is applied by the controller but has no rule -- answering the quiz would show nothing'
    )


def test_no_daisyui_state_classes_remain(controller):
    for legacy in ('ring-success', 'ring-error', 'bg-success/20', 'bg-error/20', 'opacity-50',
                   'pointer-events-none'):
        assert legacy not in controller, (
            f'controller still writes {legacy}, which the rebuilt deck has no rule for'
        )


def test_badge_slides_compose_the_shared_medallion():
    """Badges are objects, and the Medallion is how the site renders them. The recap hand-rolled a
    plate-plus-subject stack only while it was stuck on the legacy Badge model, which has no frame; it
    reads UserGroupBadge now, so the component fits and the one-off must not come back."""
    for name in ('badges.html', 'quiz_closest_badge.html'):
        html = (SLIDE_DIR / name).read_text(encoding='utf-8')
        assert "components/badge_medallion.html" in html, f'{name} does not use the Medallion'
        assert 'rcp-badge__plate' not in html, f'{name} still hand-rolls the badge art stack'


def test_recap_reads_no_legacy_badge_tables():
    """UserBadge / UserBadgeProgress are written by badge_service, which no live path calls -- evaluation
    runs through badge_apply. A recap reading them shows a frozen or empty set for everybody."""
    for path in ('trophies/services/monthly_recap_service.py', 'api/recap_views.py',
                 'core/services/monthly_recap_message_service.py'):
        # Comment lines are dropped: they legitimately name the tables to explain what was replaced.
        code = _code_only(ROOT / path)
        for legacy in ('UserBadgeProgress', 'badge__tier'):
            assert legacy not in code, f'{path} still reads the legacy badge table {legacy}'
        assert 'UserBadge.objects' not in code, f'{path} still reads legacy UserBadge'


def test_no_badge_xp_figure_anywhere_in_the_recap():
    """The badge subsystem has no XP ledger -- standings are recomputed from scratch -- so a per-month XP
    number cannot be derived without running the evaluator on the request path. It was removed rather
    than approximated."""
    for path in ('trophies/services/monthly_recap_service.py', 'api/recap_views.py',
                 'core/services/monthly_recap_message_service.py',
                 'templates/recap/partials/slides/badges.html'):
        code = _code_only(ROOT / path)
        assert 'badge_xp_earned' not in code, f'{path} still reads the removed badge_xp_earned field'


def test_verdict_is_hidden_by_attribute_not_a_utility_class(controller):
    """The templates ship the verdict with the `hidden` ATTRIBUTE. `classList.remove('hidden')` would not
    unhide it."""
    for slide in QUIZ_SLIDES:
        assert re.search(r'data-quiz-feedback hidden', slide.read_text(encoding='utf-8')), slide.name
    assert "classList.remove('hidden')" not in controller
    assert 'feedbackContainer.hidden = false' in controller
