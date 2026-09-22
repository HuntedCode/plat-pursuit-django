"""`PlatPursuit.AnchoredMenu` -- the shared anchored-panel primitive (utils.js).

EXTRACTED FROM `quick-add.js` IN 2026-09, when the Game Lists editor needed two more menus of the
same shape (a per-card "Move to" and a per-section actions menu). Before that the site had eight
hand-rolled dropdowns and one exported primitive, `discPopovers`, which renders its panel as a
SIBLING of every trigger -- two thousand rows of markup on a 200-game list with ten sections.

EVERY GUARD IN THIS FILE WAS EARNED BY QUICK-ADD. They lived in `test_gamelists_actions.py` and moved
here with the code, because they are now claims about the primitive rather than about adding a game
to a list, and they protect three consumers instead of one. The docstrings are the originals: each
one names a bug that shipped, and that is the reason to keep them rather than re-derive them.

The assertions read SOURCE rather than driving a browser, which is the same trade the file they came
from makes: none of these failures is visible in rendered HTML, and several are invisible on a
desktop browser as well.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _decommented(source):
    """Strip comments before asserting on code.

    A comment is a claim; only code is evidence -- and this project has shipped several assertions
    that passed off prose naming the very thing that had been removed. It matters more than usual
    here: the primitive's comments quote the bugs these tests are named after, almost verbatim.
    """
    source = re.sub(r'/\*.*?\*/', '', source, flags=re.S)
    return re.sub(r'^\s*//.*$', '', source, flags=re.M)


def _read(relative):
    return (ROOT / relative).read_text(encoding='utf-8')


def _utils():
    return _decommented(_read('static/js/utils.js'))


def _menu_source():
    """The primitive's own source, sliced from its constructor to its export.

    Sliced rather than read whole so an assertion cannot be satisfied by an unrelated part of a
    4,500-line file -- `utils.js` contains several other popovers, and `discPopovers` alone would
    answer for `addEventListener('resize'` and `isConnected` on its own.
    """
    js = _utils()
    start = js.index('function AnchoredMenu(')
    end = js.index('window.PlatPursuit.AnchoredMenu = AnchoredMenu;')
    body = js[start:end]
    assert len(body) > 500, 'the AnchoredMenu slice is too short to be the real implementation'
    return body


def _module_source():
    """The primitive PLUS its module-level listener block, which is where the gesture handling is.

    `_anchoredBindOnce` sits above the constructor and owns every document/window listener, so the
    tests about scrolling, resizing and htmx have to look there rather than in the instance.
    """
    js = _utils()
    start = js.index('var _anchoredMenus = [];')
    end = js.index('window.PlatPursuit.AnchoredMenu = AnchoredMenu;')
    return js[start:end]


# ── the gestures a phone makes ──────────────────────────────────────────────────────────────────

def test_the_panel_survives_the_gestures_a_phone_makes():
    """Two closes that fired on the most ordinary mobile actions. Android raises `resize` when the
    virtual keyboard opens -- and a menu that focuses a field on open could vanish on the frame it
    appeared. And the inner row list chained its scroll to the document, whose `scroll` listener shut
    the panel mid-flick."""
    src = _module_source()

    resize = src[src.index("window.addEventListener('resize'"):]
    resize = resize[:resize.index('});') + 3]
    assert 'reposition()' in resize, 'a resize still closes rather than repositions'

    # The consumer owns the stylesheet, so the containment rule is still checked where it lives.
    css = _read('static/css/components/quick-add.css')
    assert 'overscroll-behavior: contain' in css, 'the inner scroll still chains to the document'


def test_the_panel_survives_the_ios_keyboard_raising():
    """A hunter with NO lists gets the New list field focused on open. On iOS that raises the
    keyboard, which SCROLLS the document to lift the field clear of it -- and the scroll handler
    closed on any scroll, so the panel vanished on the frame it appeared. Every time, for exactly the
    first-run hunter the empty-state copy is written for.

    The `resize` handler was already written for this same keyboard; the reasoning was never carried
    across to `scroll`."""
    src = _module_source()

    follow = src[src.index('self.followOrClose = function'):]
    follow = follow[:follow.index('self.close = function')]

    # THE ANCHOR'S VISIBILITY IS THE TEST. Not the scroll (which the keyboard causes by itself), and
    # NOT whether the focus is ours -- a menu focuses its panel on every open, mouse included, so
    # that test is true for every loaded panel and scroll-to-close stops existing altogether.
    assert 'getBoundingClientRect()' in follow
    assert 'r.bottom <= 0 || r.top >= vh' in follow, \
        'the panel follows its anchor off the screen instead of closing'
    assert 'self.reposition()' in follow

    # An anchor that no longer exists cannot be followed, and the panel must not be left floating
    # against a detached node.
    assert 'isConnected' in follow, 'a detached anchor is still followed'

    # `reposition()` forces two reflows, so a raw scroll listener calling it is a per-event storm.
    assert 'requestAnimationFrame(onScrollSettled)' in src


def test_the_panel_lets_go_of_the_page_it_was_anchored_to():
    """Browse Games swaps its grid on every filter change, and the list editor swaps its items panel
    on every write. The panel went on floating over the new content, anchored to a button no longer
    in the document -- and a row click still posted, acting on a card the hunter could no longer
    see."""
    src = _module_source()

    assert 'htmx:afterSwap' in src, 'a filter change orphans the panel'
    swap = src[src.index("addEventListener('htmx:afterSwap'"):]
    swap = swap[:swap.index('});') + 3]
    assert 'isConnected' in swap and 'close(false)' in swap


# ── focus ───────────────────────────────────────────────────────────────────────────────────────

def test_focus_comes_back_when_it_was_inside():
    """Every close path but Escape passed `restoreFocus = false`, so a keyboard user who scrolled or
    clicked away had the focused row deleted out from under them and focus reset to <body> -- the
    next Tab restarting at the top of the document. Whether focus needs restoring is a fact about the
    DOM, not a decision for the caller."""
    src = _menu_source()

    fn = src[src.index('self.close = function'):src.index('self.open = function')]
    assert 'self.el.contains(document.activeElement)' in fn, 'the caller still decides'
    # ...and read BEFORE the content is thrown away, or the answer is always false.
    assert fn.index('document.activeElement') < fn.index("self.el.innerHTML = ''")
    # Restoring focus onto a node an htmx swap detached silently drops it to <body> instead.
    assert 'self.trigger.isConnected' in fn


# ── the click that other menus are listening for ────────────────────────────────────────────────

def test_the_trigger_does_not_swallow_the_click_other_menus_listen_for():
    """`stopPropagation` was left over from when the button lived inside the card's `<a>`. Stopping
    the click one node below `document` is where the site's other outside-click closers listen, so
    opening this left the nav search, the sub-nav menu and Browse Games' own discipline popovers
    hanging open behind it."""
    src = _module_source()

    handler = src[src.index("document.body.addEventListener('click'"):]
    handler = handler[:handler.index('document.addEventListener(\'keydown\'')]
    assert 'stopPropagation' not in handler
    assert 'preventDefault' not in handler, 'a type=button outside a form has nothing to prevent'


def test_escape_is_stopped_so_it_does_not_also_reach_the_page():
    """The list editor's arrange mode listens for Escape on `document` to drop a picked-up card.
    Without this, one press closed the menu AND dropped the pick -- two undos for one keystroke.

    The game adder already had to learn this (`GameAdder` calls `stopPropagation` for the same
    reason), which is two surfaces and therefore the primitive's problem rather than each page's.
    """
    src = _module_source()

    keydown = src[src.index("document.addEventListener('keydown'"):]
    keydown = keydown[:keydown.index('});') + 3]
    assert "e.key === 'Escape'" in keydown
    assert 'stopPropagation' in keydown, 'Escape still reaches the page behind the menu'


# ── one open menu, and one set of listeners ─────────────────────────────────────────────────────

def test_only_one_menu_is_open_across_every_instance():
    """Three menus now share this primitive and two of them are on the same page. Opening a card's
    menu has to close a section's, or two panels float over each other both claiming the pointer and
    both answering Escape."""
    src = _menu_source()

    open_fn = src[src.index('self.open = function'):src.index('self.isOpen = function')]
    assert 'if (_anchoredOpen) { _anchoredOpen.close(false); }' in open_fn, \
        'opening one menu no longer closes the other'


def test_the_listeners_are_bound_once_for_every_instance():
    """The reason this is a primitive rather than a copied file. `quick-add` bound four document and
    window listeners at module scope, which was right when it was the only one; N instances each
    binding their own, with no teardown, is that leak N times over -- and the list editor
    re-instantiates on every htmx swap of the items panel."""
    src = _module_source()

    assert 'var _anchoredBound = false;' in src
    guard = src[src.index('function _anchoredBindOnce()'):]
    guard = guard[:guard.index('function AnchoredMenu(')]
    assert 'if (_anchoredBound) { return; }' in guard, 'the listeners can be bound twice'
    # Every listener must live inside that guarded function, not at module scope beside it.
    assert "document.body.addEventListener('click'" in guard
    assert "window.addEventListener('scroll'" in guard
    assert "window.addEventListener('resize'" in guard
    assert "addEventListener('htmx:afterSwap'" in guard


def test_destroy_deregisters_rather_than_only_hiding():
    """A menu whose page is swapped away must leave the registry, or the shared click handler goes on
    asking a dead instance whether the click was its trigger -- and `_anchoredMenus` grows on every
    swap for the lifetime of the tab."""
    src = _menu_source()

    fn = src[src.index('self.destroy = function'):]
    fn = fn[:fn.index('_anchoredMenus.push(self)')]
    assert '_anchoredMenus.indexOf(self)' in fn and 'splice(at, 1)' in fn, \
        'destroy no longer removes the instance from the registry'
    assert 'removeChild' in fn, 'the panel element is left in the document'


# ── the positioning bug that is invisible on a desktop ──────────────────────────────────────────

def test_the_height_cap_goes_on_before_the_height_is_read():
    """It was the other way round: `maxHeight` was cleared, `offsetHeight` measured against the
    stylesheet's own cap, `top` computed from that, and only THEN was the cap raised to the real
    room. So a panel with more rows than the cap allowed grew downward from a `top` that assumed it
    was short -- covering its own trigger and running off the bottom of the screen.

    Ordering, which no rendered-HTML assertion can see."""
    src = _menu_source()

    fn = src[src.index('self.reposition = function'):src.index('self.followOrClose = function')]
    assert fn.index("el.style.maxHeight = room + 'px'") < fn.index('el.offsetHeight'), \
        'the height is measured before the cap is applied again'


def test_the_panel_is_fixed_so_a_card_grid_cannot_clip_it():
    """The trigger sits in a grid cell with `overflow` ancestors and a transform-capable page
    wrapper, and an absolutely-positioned panel is clipped by the first of those it meets. This is
    also why `discPopovers` was not the primitive to reuse here."""
    css = _read('static/css/components/quick-add.css')
    block = css[css.index('.qa-pop'):]
    assert 'position: fixed' in block[:600], 'the panel can be clipped by its grid again'


# ── the consumer actually uses it ───────────────────────────────────────────────────────────────

def test_quick_add_delegates_to_the_primitive():
    """The extraction is only real if the original consumer went through it. Pinned because the
    failure mode is silent: quick-add keeping a private copy would still work, and the primitive
    would drift from the code its own guards were written against."""
    js = _decommented(_read('static/js/quick-add.js'))

    assert 'PP.AnchoredMenu({' in js, 'quick-add no longer uses the primitive'
    # ...and does not keep its own copy of anything the primitive owns.
    for gone in ('function place(', 'function ensure(', "window.addEventListener('scroll'",
                 "window.addEventListener('resize'", 'htmx:afterSwap'):
        assert gone not in js, f'quick-add still carries its own {gone!r}'
