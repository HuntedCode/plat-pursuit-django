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
