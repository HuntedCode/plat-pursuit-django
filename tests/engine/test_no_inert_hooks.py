"""`data-countup` rendered on a page whose JS never calls `countUp`.

Found FIVE times in two days -- `/jobs/` (the header tally and `data-search-wrap`), `/jobs/<slug>/` (two
tallies and `data-gallery`), and `/badges/how-it-works/` (three tallies). Every instance looked complete
in source and correct in a screenshot; the figure simply sat there while the identical tally on every
other page rolled up. `countUp` is defined globally in `utils.js` and must be CALLED per page.

WHY THIS IS A SHORT LIST AND NOT A SWEEP -- worth reading before "improving" it.

Two auto-discovering versions were written and thrown away. The first checked each template in isolation
and failed on 19 correct files (every partial whose caller does the calling). The second resolved
`{% include %}`/`{% extends %}` transitively and still failed on 12: slides pulled in through a variable
template name, hooks read by `utils.js` rather than by any template, and an orphaned partial subtree with
no caller by design. Each round of "smarter" bought a smaller pile of false positives and more machinery
to misread later.

The audit that prompted this test predicted exactly that, and it is right: a reader-vs-renderer join is a
SWEEP -- something to run deliberately, with judgement, when the question comes up -- not an assertion.
So this pins the three pages the bug actually reached, which is where the evidence is, and the method for
re-running the sweep lives in `docs/reference/js-utilities.md`.
"""
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]

#: Pages that had an inert `data-countup` and now call `countUp`. Each entry is (template, why it broke).
FIXED = [
    ('trophies/jobs_browse.html', 'the catalogue tally snapped in while the wall staggered around it'),
    ('trophies/job_detail.html', 'both header figures, inherited inert and then doubled'),
    ('trophies/badge_how_it_works.html', 'three tallies, static above beats that animated'),
]


@pytest.mark.parametrize('rel,why', FIXED, ids=[t for t, _ in FIXED])
def test_the_pages_that_shipped_an_inert_countup_hook_now_call_it(rel, why):
    """Each of these rendered `data-countup` with no caller. The markup was correct and the feature was
    dead, which is the whole difficulty: nothing errors, nothing logs, and a screenshot looks right."""
    text = (ROOT / 'templates' / rel).read_text(encoding='utf-8')
    assert 'data-countup=' in text, f'{rel} no longer renders the hook -- update this list'
    assert 'countUp(' in text, (
        f'{rel} renders `data-countup` but never calls `PlatPursuit.countUp` again ({why}).'
    )


def test_the_countup_hooks_are_scoped_to_something_that_exists():
    """A selector that matches nothing fails as silently as a missing call -- and this exact thing already
    happened here: job detail's caller was scoped to `.jobd__tallies`, an element the header rebuild
    deleted, so the call survived and reached no figures."""
    for rel, _ in FIXED:
        text = (ROOT / 'templates' / rel).read_text(encoding='utf-8')
        call = text[text.index('countUp('):]
        scope = text[:text.index('countUp(')]
        # The selector the page counts up through, if it uses one.
        for marker in ('.pp-head-cascade', '.pp-hiw__tally', '[data-jobs-count]'):
            if marker in scope.rsplit('querySelector', 1)[-1] or marker in call[:200]:
                assert marker.strip('.[]') in text.replace('querySelectorAll', ''), (
                    f'{rel} counts up through `{marker}`, which the page no longer renders'
                )
