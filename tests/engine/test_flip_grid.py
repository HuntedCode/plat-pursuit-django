"""`PlatPursuit.flipGrid` -- the shared "shuffle" that glides survivors to their new slots.

Extracted 2026-08 from three hand-rolled copies (the Collection gallery, Browse Hunters, the jobs
catalogue) that had already drifted apart in duration and easing. Three pages depend on its contract now,
so the contract is worth pinning: a source read, like the rest of this suite's JS guards, since there is
no JS runtime in the harness.

What is NOT tested here is that it animates -- that needs a browser. What is tested is the set of
decisions each of the three callers is relying on and would not notice losing.
"""
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
UTILS = (ROOT / 'static' / 'js' / 'utils.js').read_text(encoding='utf-8')


def _fn():
    """The helper's body ALONE, so an assertion cannot match a neighbouring function.

    The first version used the `window.PlatPursuit.flipGrid` export as its end bound, with a fallback to
    the next docblock. The export sits ~1,100 lines below the definition, so the condition was always
    true and the slice swallowed eight unrelated helpers -- the exact opposite of what its docstring
    promised. Bounded by the next docblock instead, which is adjacent by construction, and length-checked
    so a future move cannot silently widen it again.
    """
    start = UTILS.index('function flipGrid(o) {')
    end = UTILS.index('\n/**', start)          # the docblock of whatever helper follows flipGrid
    body = UTILS[start:end]
    assert len(body) < 8000, 'the slice is no longer bounded to flipGrid'
    return body


def test_it_is_exported():
    """Three pages call it off the namespace; a helper defined and not exported is a silent no-op at every
    call site, because they all guard with `PP.flipGrid ? ... : null`."""
    assert 'window.PlatPursuit.flipGrid = flipGrid;' in UTILS


def test_it_marks_survivors_so_the_reveal_engine_skips_them():
    """THE decision the callers depend on most, and the one a re-roll forgets.

    An element already on screen must not also fade in, or the unchanged part of the wall flickers on
    every filter. `staggerReveal`'s once-only guard reads exactly these two classes, so marking them is
    what makes the two engines cooperate rather than both animate one card.
    """
    fn = _fn()
    assert "classList.add('is-revealed', 'pp-revealing')" in fn
    # ...and `staggerReveal` really is the thing that reads them, or the marking is decoration.
    reveal = UTILS[UTILS.index('function staggerReveal(o) {'):]
    reveal = reveal[:reveal.index('\n}')]
    assert "contains('is-revealed')" in reveal


def test_survivors_are_marked_even_when_they_did_not_move():
    """The subtle half. A card that kept its slot is still a survivor, and returning early before the mark
    leaves it to be re-revealed -- so the tiles that visibly did NOT change are the ones that flicker.
    Asserted by ORDER: the mark has to precede the no-movement bail.
    """
    fn = _fn()
    mark = fn.index("classList.add('is-revealed', 'pp-revealing')")
    bail = fn.index('if (!dx && !dy) { return; }')
    assert mark < bail, 'a survivor that did not move is left unmarked and will fade in again'


def test_it_bails_out_under_reduced_motion_without_breaking_the_mutation():
    """`run(mutate)` must still MUTATE when motion is off -- a reduced-motion reader gets no animation, not
    a grid that never re-filters. `measure()` is what no-ops, so `play()` finds nothing to invert from and
    the layout is whatever the mutation produced."""
    fn = _fn()
    assert 'function measure()' in fn
    measure = fn[fn.index('function measure()'):fn.index('function play()')]
    assert 'if (reduced()) { first = null; return; }' in measure
    run = fn[fn.index('function run(mutate)'):]
    assert 'mutate();' in run, 'the synchronous path can skip the mutation entirely'


def test_it_measures_only_what_is_on_screen():
    """A `display: none` cell measures 0x0, and gliding something in from the origin is worse than not
    animating it. The Collection gallery filters by toggling `display`, so this is its normal case rather
    than an edge one."""
    fn = _fn()
    assert 'function shown(el)' in fn
    measure = fn[fn.index('function measure()'):fn.index('function play()')]
    # The CALL, not the whole condition. This pinned `if (shown(el))` verbatim and went red the moment a
    # null-key guard was added beside it -- a correct change failing a test that was checking spelling.
    assert 'shown(el)' in measure


def test_arrivals_are_left_alone_unless_the_caller_asks():
    """Two engines animating one element is how a card ends up flickering. Browse Hunters and the jobs
    catalogue both run `staggerReveal` over arrivals, so the flip must not also touch them; the Collection
    gallery has no reveal pass on that path and opts in with `enter: true`."""
    fn = _fn()
    play = fn[fn.index('function play()'):fn.index('function run(mutate)')]
    assert 'if (o.enter)' in play, 'arrivals are animated unconditionally'


@pytest.mark.parametrize('path, needle', [
    ('static/js/collection.js', 'PlatPursuit.flipGrid({'),
    ('templates/trophies/profile_list.html', 'PlatPursuit.flipGrid({'),
    ('templates/trophies/jobs_browse.html', 'PP.flipGrid({'),
])
def test_every_shuffle_on_the_site_runs_the_shared_helper(path, needle):
    """The point of extracting it. Each of these three had its own copy; a fourth hand-rolled one is the
    drift this guards against, and the failure mode is invisible -- two walls that shuffle at slightly
    different speeds on the same site."""
    assert needle in (ROOT / path).read_text(encoding='utf-8'), f'{path} is not using the shared flip'


def test_the_hand_rolled_copies_are_gone():
    """Each of the three had named internals; their survival would mean a copy came back beside the shared
    one, which is worse than never extracting it.

    Deliberately NOT a ban on `getBoundingClientRect` in these files -- the Collection gallery measures
    legitimately for arrow-key column detection, and a rule that broad fails on correct code. (It was
    written that way first and did exactly that.)
    """
    dead = {
        'static/js/collection.js': ('flipPending', 'flipCleanup', 'flipTimer'),
        'templates/trophies/profile_list.html': ('measureCards', 'playFlip', 'flipFrom'),
        'templates/trophies/jobs_browse.html': ('flipSurvivors',),
    }
    for path, names in dead.items():
        src = (ROOT / path).read_text(encoding='utf-8')
        for name in names:
            assert name not in src, f'{path} still carries `{name}` -- a second shuffle implementation'


def test_a_null_key_is_never_used_as_a_key():
    """Both callers' `key` functions return null for an item missing its link, and the original code
    guarded on BOTH sides -- dropped in the extraction, restored after the audit.

    Without it two keyless items collide on the key `null`: the second glides in from the FIRST one's old
    position, and it is also marked `is-revealed`, so `staggerReveal` skips it and it appears with no
    fade. Latent today (both templates always emit the link) and silent when it stops being latent.
    """
    fn = _fn()
    measure = fn[fn.index('function measure()'):fn.index('function play()')]
    play = fn[fn.index('function play()'):fn.index('function run(mutate)')]
    assert 'k != null' in measure, 'a null key can be stored as a key'
    assert 'k == null' in play, 'a null key can be looked up as a key'


def test_each_caller_owns_its_own_motion():
    """The extraction defaulted every caller to Collection's 420ms spring, so Browse Hunters' glide (460ms
    on its own decel curve) stopped agreeing with the `staggerReveal` fade running beside it in the same
    swap. A shared primitive may own the MECHANISM; it must not quietly own each page's motion vocabulary.

    Asserted at the call sites rather than in the helper: the point is that the values are passed, not
    that any particular number is right.
    """
    root = pathlib.Path(__file__).resolve().parents[2]
    hunters = (root / 'templates' / 'trophies' / 'profile_list.html').read_text(encoding='utf-8')
    collection = (root / 'static' / 'js' / 'collection.js').read_text(encoding='utf-8')

    flip = hunters[hunters.index('PlatPursuit.flipGrid({'):]
    flip = flip[:flip.index('}) : null;')]
    assert 'duration: 460' in flip and 'easing: FADE' in flip

    gal = collection[collection.index('PlatPursuit.flipGrid({'):]
    gal = gal[:gal.index('}) : null;')]
    # Collection's arrival is a SPLIT tween -- a shorter, separately-eased fade over the transform. One
    # spring-eased animation spikes the opacity and reads as a flash, which is what the extraction did.
    assert 'enterFade' in gal and 'enterScale' in gal


def test_career_guards_every_shared_helper_call():
    """A `TypeError` at IIFE top level takes the whole board controller with it -- the claim flow, every
    filter and chip, `seedFromURL`, `initCards`, the scroller, the landing stagger -- while the page still
    renders its server-side cards and looks completely correct.

    `PP` is snapshotted as `window.PlatPursuit || {}`, and this repo documents a stale cached `utils.js`
    as a real production failure mode. Four of the five delegations added during the extraction shipped
    unguarded while the file's other eight `PlatPursuit.*` calls were all guarded, so this pins the
    CONVENTION rather than any one instance.

    Line-based: the first attempt scanned a fixed window of characters before each occurrence, which fails
    on the guard's own mention of the name (`if (PP.x) { PP.x(...) }` contains `PP.x` twice, and the first
    has no guard before it).
    """
    root = pathlib.Path(__file__).resolve().parents[2]
    career = (root / 'templates' / 'trophies' / 'career.html').read_text(encoding='utf-8')

    for helper in ('progressiveArt', 'coverCarousel', 'ringHoverLink', 'staggerCards', 'cardReveal'):
        called = [l for l in career.split('\n') if f'PP.{helper}(' in l]
        assert called, f'{helper} is no longer called from Career'
        for line in called:
            guarded = f'if (PP.{helper})' in line or f'PP.{helper} ?' in line
            assert guarded, f'unguarded PP.{helper} -- one throw here disables everything below it:\n{line.strip()}'


def test_the_reveal_observer_is_released_when_the_cards_it_watches_are_dropped():
    """An IntersectionObserver holds a STRONG reference to every target until it intersects or is
    unobserved. Career replaces its whole list on each filter, sort, scope change and facet click, so
    without a disconnect it retains one detached card per unseen row, per interaction, for the life of the
    page. The hand-rolled original had the same shape; the extraction was the moment to fix it.

    Line-based for the ordering assertion. The first attempt sliced from `if (!append) {` to the next `}`,
    which lands on the closing brace of the disconnect's OWN `if` -- so the slice ended before the line it
    was comparing against. That is the same truncation the audit found in the `initReveal` guard test, made
    twice in one day.
    """
    root = pathlib.Path(__file__).resolve().parents[2]
    utils = (root / 'static' / 'js' / 'utils.js').read_text(encoding='utf-8')
    career = (root / 'templates' / 'trophies' / 'career.html').read_text(encoding='utf-8')

    assert 'disconnect: function () { if (io) { io.disconnect(); } }' in utils

    lines = career.split('\n')
    drop = next(i for i, l in enumerate(lines) if "list.innerHTML = ''" in l)
    release = next(i for i, l in enumerate(lines) if 'cardRevealer.disconnect()' in l)
    assert release < drop, 'the observer is released after the cards are already gone'
    assert drop - release <= 3, 'the disconnect drifted away from the swap it protects'


# ══════════════════════════════════════════════════════════════════════════════════════════════════════
# The other five helpers. Asserted as SOURCE, because the harness has no JS runtime -- but each assertion
# names a DECISION both callers depend on, not a spelling. The audit's rule: a source test that a valid
# refactor breaks is worse than none, so nothing here pins whitespace, a local variable name, or a
# complete statement.
# ══════════════════════════════════════════════════════════════════════════════════════════════════════

def _helper(name):
    """One helper's body, bounded by the next docblock."""
    start = UTILS.index(f'function {name}(')
    return UTILS[start:UTILS.index('\n/**', start)]


def test_progressive_art_settles_on_all_three_completion_paths():
    """The cover skeleton runs an `infinite` animation that only `.is-loaded` stops, so a path that never
    marks it leaves that card shimmering forever.

    THREE paths, and the middle one is the subtle one: an image already `complete` from cache will never
    fire `load` again, so waiting for the event strands every cached cover. `error` counts as finished
    too -- shimmering forever over a 404 is the worst of the three outcomes.
    """
    fn = _helper('progressiveArt')
    assert 'if (!img)' in fn, 'a card with no image never settles'
    assert 'complete' in fn and 'naturalWidth' in fn, 'a cached image waits for a load event that will not fire'
    assert "'load'" in fn and "'error'" in fn, 'a broken image shimmers forever'
    # Idempotent, or a re-run over an appended page re-binds every card already wired.
    assert 'plInit' in fn


def test_ring_hover_link_lights_both_ends_and_only_within_one_card():
    """Two decisions, both silent when lost.

    BOTH ENDS: drop the segment half and hovering a job still highlights the job, so the feature looks
    like it works while the ring -- the half it exists for -- does nothing.

    ONE CARD: the lookup is scoped to the hovered element's own card. Unscoped, hovering one job on a wall
    of 24 contracts lights that slug on every card that happens to level it.
    """
    fn = _helper('ringHoverLink')
    assert 'item.classList.toggle' in fn and 'seg.classList.toggle' in fn, 'the link is one-directional'
    assert 'card.querySelector' in fn, 'the lookup is not scoped to one card'
    assert 'closest(cardSel)' in fn
    # A slug goes straight into a selector string.
    assert 'CSS.escape' in fn, 'an unusual slug would throw inside a mouse handler'


def test_cover_carousel_preloads_late_and_hides_the_outgoing_layer_late():
    """Two timing decisions that read as bugs when they go.

    PRELOAD ON FIRST HOVER, not at render: a wall of 24 cards would otherwise fetch every frame of every
    gallery for cards nobody points at.

    HIDE THE OUTGOING LAYER AFTER THE CROSSFADE. Hide it immediately and the card's background shows
    through for a frame between images.
    """
    fn = _helper('coverCarousel')
    assert 'primed' in fn, 'every gallery preloads at render'
    assert 'mouseenter' in fn and 'primed = true' in fn
    assert 'hideT' in fn and 'FADE' in fn, 'the outgoing frame is hidden before the crossfade finishes'
    # Frame 0 is the resting cover, not a third copy of the same image.
    assert 'if (i === 0)' in fn
    assert 'carInit' in fn


def test_card_reveal_is_pure_enhancement_and_reveals_once():
    """The final state is server-rendered, so this may only ever ADD motion. Under reduced motion the card
    must be left exactly as served -- but still MARKED, or it is re-examined on every scroll."""
    fn = _helper('cardReveal')
    assert 'revealed' in fn, 'a card can reveal twice'
    marked = fn.index('dataset.revealed')
    bail = fn.index('if (reduce)')
    assert marked < bail, 'reduced motion returns before marking, so the card is retried forever'
    assert 'unobserve' in fn, 'observed cards are never released'
    assert 'disconnect' in fn, 'the observer cannot be released when its cards are replaced'


def test_stagger_cards_clears_its_own_class_so_it_can_run_again():
    """The entrance runs on first paint, on an appended page, and on a tab opening. The class has to come
    off at `animationend` or the second run is a no-op -- the element already has it, so nothing restarts
    and an appended page arrives flat."""
    fn = _helper('staggerCards')
    assert 'animationend' in fn, 'the entrance class is never cleared'
    assert 'rpCardIn' in fn, 'the cleanup fires on any animation, not this one'
    assert 'remove' in fn and 'animationDelay' in fn
    assert 'prefers-reduced-motion' in fn, 'the entrance is not gated on reduced motion'


def test_every_shared_card_helper_gates_reduced_motion():
    """All five animate. None may animate for a reader who has asked the OS not to."""
    for name in ('coverCarousel', 'cardReveal', 'staggerCards', 'flipGrid'):
        assert 'prefers-reduced-motion' in _helper(name), f'{name} ignores reduced motion'
    # `progressiveArt` is the exception and deliberately so: it STOPS an animation rather than starting
    # one, so gating it would leave the skeleton running for exactly the readers who asked for less.
    assert 'prefers-reduced-motion' not in _helper('progressiveArt')


def test_the_radio_debounce_still_updates_the_active_filter_badge():
    """REGRESSION from the a11y fix itself. Coalescing radio changes meant adding a branch that returns
    early -- and the generic auto-submit path it skipped also calls `updateFilterBadge()`, so the
    "N filters active" badge silently froze on every page with a radio filter. FIVE pages use them,
    Browse Games among them, so this reached well past the page the fix was written for.

    The badge updates IMMEDIATELY while only the REQUEST is coalesced: it reflects the current selection,
    so debouncing it too would leave the count visibly lagging the chip just pressed.

    The general shape is what to watch for: an early `return` added to a shared handler skips everything
    below it, and "everything below it" is exactly what nobody re-reads.
    """
    root = pathlib.Path(__file__).resolve().parents[2]
    js = (root / 'static' / 'js' / 'browse-filters.js').read_text(encoding='utf-8')

    branch = js[js.index("if (el.type === 'radio') {"):]
    branch = branch[:branch.index('return;')]
    assert 'updateFilterBadge()' in branch, 'the radio path skips the filter badge again'
    assert 'value = ' in branch, 'the radio path no longer resets to page 1'
    # Debounced request, immediate badge -- the badge call must not be inside the timer.
    timer = branch[branch.index('setTimeout'):] if 'setTimeout' in branch else ''
    assert 'updateFilterBadge' not in timer, 'the badge lags the selection by the debounce'


def test_only_the_views_that_mean_it_send_the_scroller_its_stop_signal():
    """`InfiniteScroller` now stops on `X-Has-Next: 0`, so a view sending that header for any other reason
    would end its wall early. Originally only the two contract endpoints sent it; the browse family
    joined DELIBERATELY in 2026-08 via HtmxListMixin (the countless-scroll optimization), which sends it
    ONLY on XHR responses that carry a page_obj -- exactly the scroller's own fetches. Anything beyond
    these two files is still an accident waiting to end a wall early."""
    root = pathlib.Path(__file__).resolve().parents[2]
    senders = []
    for py in (root / 'trophies').rglob('*.py'):
        if 'X-Has-Next' in py.read_text(encoding='utf-8', errors='ignore'):
            senders.append(py.name)
    assert sorted(senders) == ['career_views.py', 'mixins.py'], (
        f'a new view sends the scroller stop signal: {senders}')
