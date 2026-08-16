"""The badge teaching has a real address (2026-08).

It lived only in a first-run modal on Browse Badges, which meant the vocabulary the whole badge system
speaks -- "Ultra HD", "Legacy HD" -- had no URL. Support could not link it, search could not index it, and
the three surfaces that render those names straight off `PlatformGroup` could point nowhere to say what
they meant: badge detail (the group-switch TABS), the gallery filter panel (the PLATFORM CHIPS), and the
Collection (the edition stat labels).

So the modal became genuinely ONE-SHOT (greet a first visit, then link out) and the teaching moved to
`/badges/how-it-works/`. What these tests hold down is the part that rots quietly: a page that retypes
facts the database owns, a route that a slug pattern swallows, and a "one-shot" modal that grows a recall
button again.
"""
import re
from pathlib import Path

import pytest
from django.urls import reverse

from tests.factories import PlatformGroupFactory

pytestmark = pytest.mark.django_db

ROOT = Path(__file__).resolve().parents[2]


def test_the_page_is_public_and_reachable(client):
    """The entire point is that it can be linked and indexed. A login wall would undo that."""
    response = client.get(reverse('badge_how_it_works'))
    assert response.status_code == 200
    assert 'How badges work' in response.content.decode()


def test_the_route_is_not_swallowed_by_the_series_slug_pattern(client):
    """`badges/<str:series_slug>/` sits in the same prefix and matches ANY string, so this page only
    resolves because it is declared FIRST. Django matches in order, and moving the route below the
    detail pattern would silently turn it into a 404 for a series that does not exist."""
    from django.urls import resolve

    match = resolve('/badges/how-it-works/')
    assert match.url_name == 'badge_how_it_works', (
        f'/badges/how-it-works/ resolves to {match.url_name!r} -- the slug pattern is shadowing it'
    )


def test_the_editions_come_from_the_database_not_the_template(client):
    """`PlatformGroup` owns the edition mapping (`name` + `platforms`), the badge engine routes games by
    it, and the model's own docstring calls adding a group "a row, not a schema change". A page that
    hardcoded the two current editions would be the one place that stopped being true the day a third is
    seeded -- which is exactly the drift the modal already had.
    """
    PlatformGroupFactory(key='next-gen', name='Next Gen', platforms=['PS6'], sort_order=9)

    body = client.get(reverse('badge_how_it_works')).content.decode()

    assert 'Next Gen' in body, 'a newly seeded edition does not appear -- the page retypes them'
    assert 'PS6' in body, 'the edition platforms are not rendered from the model'

    template = (ROOT / 'templates' / 'trophies' / 'badge_how_it_works.html').read_text(encoding='utf-8')
    markup = re.sub(r'\{%\s*comment\s*%\}.*?\{%\s*endcomment\s*%\}', '', template, flags=re.S)
    for hardcoded in ('Ultra HD', 'Legacy HD'):
        assert hardcoded not in markup, (
            f'{hardcoded!r} is typed into the template; it must come from PlatformGroup'
        )


def test_inactive_editions_are_not_taught(client):
    """`is_active` is how a group is retired without deleting its history. Teaching a retired edition
    would be worse than teaching none."""
    PlatformGroupFactory(key='retired-ed', name='Retired Edition', platforms=['PSP'], is_active=False)

    body = client.get(reverse('badge_how_it_works')).content.decode()
    assert 'Retired Edition' not in body


def test_the_platform_codes_are_rendered_as_labels(client):
    """`PlatformGroup.platforms` stores raw PSN codes, and PSVITA is the one that reads wrong. The page
    maps through `PLATFORM_LABELS` rather than printing the column."""
    PlatformGroupFactory(key='handheld', name='Handheld', platforms=['PSVITA'])

    body = client.get(reverse('badge_how_it_works')).content.decode()
    assert 'PS Vita' in body
    assert 'PSVITA' not in body, 'the raw PSN code is being printed at the reader'


def test_the_page_teaches_all_four_beats(client):
    """The modal has FOUR beats and the first draft of this page had three -- it dropped mastery/holo,
    which would have made the permanent home teach LESS than the sheet it replaced.

    Step 2's wording is checked against `badge_engine.py` rather than the modal's shorter caption: the
    engine's bars are `base_complete` (the DEFAULT trophy group at 100% -- the platinum on a plat game,
    the main list on one without) and `full_complete` (the whole game incl. DLC). "Platinum every game"
    alone leaves a reader holding a non-plat game unable to tell whether it counts.
    """
    body = client.get(reverse('badge_how_it_works')).content.decode()

    # Structure, not copy. The captions are wording and get revised; what must not change is that there
    # are FOUR beats and that each still teaches its own thing. Pinning exact caption text made this fail
    # on a pure copy edit while a genuinely dropped beat would look identical to a rename.
    # Matched on the <li>, not the class prefix: the wrapping <ol class="pp-hiw__steps"> shares it, so a
    # prefix count reads 5 and would have to be "corrected" to a number that means nothing.
    beats = len(re.findall(r'<li class="pp-hiw__step', body))
    assert beats == 4, f'the journey has {beats} beats, not four'

    for concept, missing in (
        ('pick a set', 'step 1 no longer names choosing a set'),
        ('platinum', 'step 2 no longer names the platinum'),
        # Not "forged": that vocabulary is retired and another test in this file now forbids it. Beat 3
        # is the moment the badge becomes YOURS, which is what it always meant.
        ('medallion is yours', 'step 3 no longer names the badge becoming yours'),
        ('holographic', 'step 4 no longer names the mastery payoff'),
    ):
        assert concept in body.lower(), missing

    assert 'no platinum' in body, 'the page does not say how a game without a platinum counts'
    assert 'DLC' in body, 'the page does not say that mastery includes DLC'


def test_the_modal_is_one_shot_with_no_recall_buttons():
    """Its job is to greet a first visit. Keeping a button to re-open a SHORTER copy of teaching that now
    has a fuller, linkable home is how the two drift apart -- and a permanent header affordance for a
    one-time explainer was ~37px of a header that had none to spare, anchored nowhere near the confusion.
    """
    page = (ROOT / 'templates' / 'trophies' / 'badge_list.html').read_text(encoding='utf-8')
    markup = re.sub(r'\{%\s*comment\s*%\}.*?\{%\s*endcomment\s*%\}', '', page, flags=re.S)
    markup = re.sub(r'\{#.*?#\}', '', markup, flags=re.S)

    assert 'data-howto-open' not in markup, 'a recall button for the one-shot modal is back'
    assert 'pp-howto-btn' not in markup, 'the header teaching button is back'
    assert 'pp-minibar__howto' not in markup, 'the mini-bar teaching button is back'

    # The modal itself stays -- it is the onboarding beat, not the thing that was removed.
    assert 'badge_howto.html' in page, 'the first-run modal was removed along with its buttons'


def test_the_dev_replay_control_never_ships_to_prod():
    """The modal is one-shot and gated on a localStorage flag, so once dismissed there is no way back to
    it in a browser short of clearing site data -- which makes iterating on it painful. The replay button
    fixes that for local work, and it is the ONE re-open path allowed to exist.

    Which makes the gate the whole point. Ungated, this is precisely the permanent recall affordance the
    rest of this change removed, shipped to every visitor. Exercised through the real view under both
    settings rather than by matching `{% if %}` in the source, because the string is easy to keep while
    breaking what feeds it -- `dev_howto` is set in `BadgeListView.get_context_data`, and a template that
    still reads a context key nobody sets renders nothing and passes a source check.
    """
    from django.test import Client, override_settings

    with override_settings(DEBUG=True):
        assert 'data-howto-replay' in Client().get(reverse('badges_list')).content.decode(), (
            'the dev replay button is missing in DEBUG -- there is no way to re-open the modal locally'
        )

    with override_settings(DEBUG=False):
        assert 'data-howto-replay' not in Client().get(reverse('badges_list')).content.decode(), (
            'the dev replay button renders with DEBUG off -- it is a permanent recall button in prod'
        )


def test_the_replay_clears_the_flag_rather_than_forcing_the_modal_open():
    """Replay removes `pp-badges-howto-seen` and then opens normally, so what plays is a genuine first
    visit: dismissing re-sets the flag exactly as it would for a real visitor, and pressing again re-arms.

    An always-open dev mode would have been simpler and worse -- it cannot show a bug in the gating
    itself, which is the part of this modal most likely to break and the least likely to be noticed.
    """
    modal = (ROOT / 'templates' / 'trophies' / 'partials' / 'badge_list'
             / 'badge_howto.html').read_text(encoding='utf-8')
    start = modal.find("closest('[data-howto-replay]')")
    assert start != -1, 'nothing handles the dev replay button'
    open_brace = modal.index('{', start)
    depth, end = 0, None
    for i, ch in enumerate(modal[open_brace:], open_brace):
        depth += (ch == '{') - (ch == '}')
        if depth == 0:
            end = i
            break
    branch = modal[start:end]

    assert 'removeItem(SEEN_KEY' in branch, (
        'replay does not clear the seen flag, so it is a force-open that bypasses the real gating'
    )
    assert 'open(' in branch, 'replay clears the flag but never opens the modal'


def test_the_modal_hands_off_to_the_page():
    """Without this link the modal is a dead end: dismissed once, and the reader has no route to the
    fuller version. It trails "Got it" rather than leading, because most readers are done."""
    modal = (ROOT / 'templates' / 'trophies' / 'partials' / 'badge_list'
             / 'badge_howto.html').read_text(encoding='utf-8')
    markup = re.sub(r'\{%\s*comment\s*%\}.*?\{%\s*endcomment\s*%\}', '', modal, flags=re.S)

    assert "{% url 'badge_how_it_works' %}" in markup, 'the modal no longer links to the page'
    assert 'data-howto-close' in markup, 'the modal lost its dismiss control'


def test_following_the_link_out_counts_as_having_been_greeted():
    """The seen flag is written by `close()`, and an anchor navigates instead of closing -- so the link
    shipped without this branch left `pp-badges-howto-seen` unset for exactly the reader who took it. That
    reader gets the modal auto-popped at them again on their next visit: the one who engaged MOST is the
    only one it repeats for, and the "genuinely one-shot" claim this whole change rests on is false.

    Asserted on the marker + the write together, and NOT on the substring `SEEN_KEY` alone -- that name
    appears four other times in this file, so a check for it would survive deleting the branch entirely.
    """
    modal = (ROOT / 'templates' / 'trophies' / 'partials' / 'badge_list'
             / 'badge_howto.html').read_text(encoding='utf-8')

    assert 'data-howto-more' in modal, 'the hand-off link lost the marker the controller keys on'

    # Bounded by BRACE DEPTH, not by a character count. A fixed window overruns the branch and picks up
    # the `preventDefault` belonging to the close branch below it, which makes the last assertion here
    # fail on correct code -- and, had the window been shorter, would have made it pass on broken code.
    start = modal.find("closest('[data-howto-more]')")
    assert start != -1, 'nothing handles the hand-off link -- taking it leaves the modal unseen'
    open_brace = modal.index('{', start)
    depth, end = 0, None
    for i, ch in enumerate(modal[open_brace:], open_brace):
        depth += (ch == '{') - (ch == '}')
        if depth == 0:
            end = i
            break
    assert end is not None, 'the hand-off branch is unbalanced'
    branch = modal[start:end]

    assert 'setItem(SEEN_KEY' in branch, (
        'the hand-off branch does not record the visit; the modal will auto-pop again'
    )
    # It must NOT swallow the click: the navigation is the entire point of the link.
    assert 'preventDefault' not in branch, (
        'the hand-off branch cancels the click, so the link goes nowhere'
    )


@pytest.mark.parametrize('surface', [
    'templates/trophies/badge_detail.html',                        # the group-switch TABS
    'templates/trophies/partials/badge_list/gallery.html',         # the PLATFORM filter chips
    'templates/trophies/collection.html',                          # the edition stat labels
])
def test_every_surface_that_speaks_the_edition_vocabulary_explains_it(surface):
    """These three render "Ultra HD" / "Legacy HD" straight off `PlatformGroup.name` and each asks the
    reader to ACT on the word -- pick a tab, tick a chip, read their own total split by it. That is where
    the question occurs, and without a hint here the page has no inbound link from any of them: the whole
    argument for giving the teaching an address was that these surfaces could finally point at something.

    Parametrized over the surface LIST so adding a fourth consumer of PlatformGroup.name is a deliberate
    act -- the file is added here or it is knowingly left without an answer.
    """
    src = (ROOT / surface).read_text(encoding='utf-8')
    markup = re.sub(r'\{%\s*comment\s*%\}.*?\{%\s*endcomment\s*%\}', '', src, flags=re.S)

    assert 'class="pp-edhint"' in markup, f'{Path(surface).name} names editions with no way to look them up'
    assert "{% url 'badge_how_it_works' %}" in markup, f'{Path(surface).name} hint points nowhere'


def test_the_badge_detail_hint_sits_outside_the_tablist():
    """`.bd2-groupswitch` is `role="tablist"` and `grid-auto-columns: 1fr`, so putting the link inside it
    would do two bad things at once: nest an anchor in a `role="tab"` button (interactive inside
    interactive, which is invalid and leaves the anchor unreachable to a screen reader), and take an
    equal-width segment of the switcher, shrinking every real tab to fit it.

    Checked against the TAB TEMPLATE -- the span from the switcher's opening tag to the `{% endfor %}`
    that closes the loop -- rather than against the file, because the hint legitimately appears later in
    the same file and a whole-file check would pass no matter where it moved.
    """
    src = (ROOT / 'templates' / 'trophies' / 'badge_detail.html').read_text(encoding='utf-8')
    start = src.index('class="bd2-groupswitch"')
    tab_template = src[start:src.index('{% endfor %}', start)]

    assert 'pp-edhint' not in tab_template, (
        'the hint is inside the tablist: it becomes an equal-width segment and nests an <a> in a tab'
    )
    # And it must still be present just after -- otherwise "outside the tablist" is satisfied by deletion.
    assert 'pp-edhint' in src[src.index('{% endfor %}', start):], 'the hint is gone from badge detail'


def test_the_icon_only_hint_has_an_accessible_name():
    """The gallery's copy is icon-only -- the "Platform" label it trails already says what the row is, and
    text there would compete with the chips. That trade is only acceptable while the link carries its own
    name: the `<svg>` is `aria-hidden`, so without `aria-label` the anchor announces as nothing at all.
    """
    src = (ROOT / 'templates' / 'trophies' / 'partials' / 'badge_list' / 'gallery.html').read_text(encoding='utf-8')
    anchor = src[src.index('<a class="pp-edhint"'):]
    anchor = anchor[:anchor.index('</a>')]

    assert 'aria-label=' in anchor, 'the icon-only edition hint announces as an unnamed link'
    assert 'aria-hidden="true"' in anchor, 'the decorative icon is exposed to screen readers'
    # The name has to be non-empty, not merely present: `aria-label=""` reads as no label at all.
    label = re.search(r'aria-label="([^"]*)"', anchor)
    assert label and label.group(1).strip(), 'the edition hint carries an empty aria-label'


@pytest.mark.parametrize('partial', [
    'templates/trophies/partials/badge_list/badge_list_items.html',
    'templates/trophies/partials/badge_list/gallery_results.html',
])
def test_both_empty_states_offer_the_teaching(partial):
    """The one place near the wall worth a teaching link: a reader who filtered to nothing has already
    stopped, and "am I misunderstanding this?" is a live question there. It costs nothing when the wall
    has results, which is why it is here and NOT on the cards -- gallery paginates at 48, and a help link
    repeated 48 times stops being an affordance."""
    src = (ROOT / partial).read_text(encoding='utf-8')
    assert "{% url 'badge_how_it_works' %}" in src, f'{Path(partial).name} empty state has no way out'


def test_the_badge_cards_do_not_carry_a_teaching_link():
    """Deliberate. Gallery renders 48 cells a page, the card is already multi-destination (a series link
    plus one anchor per edition medallion), and the confusion belongs one level in -- at the edition tabs
    on badge detail, not while scanning a wall."""
    cards = (ROOT / 'templates' / 'trophies' / 'partials' / 'badge_list'
             / 'badge_list_items.html').read_text(encoding='utf-8')
    # The empty state above is in the same file, so scope to the card loop.
    card_loop = cards[:cards.index('pp-slist__empty')]
    assert 'badge_how_it_works' not in card_loop, 'a teaching link landed on every badge card'


def test_the_page_leads_with_real_badge_artwork(client):
    """The design decision this page turns on. `visual-identity.md`: custom badge artwork is the moat, and
    "if the chrome ever fights the art, the chrome loses" -- so a page teaching the badge system that
    shows no badges is teaching around its subject. The first cut was four captions and a table.

    Every medallion here is composed by `badge_forge_medallions()` from a REAL live badge, shared verbatim
    with the browse page's first-run modal so the two cannot show different art. It degrades to the bare
    metal plate on an empty catalog, which is why this asserts the OBJECT renders rather than asserting a
    particular badge.
    """
    body = client.get(reverse('badge_how_it_works')).content.decode()

    # Hero + beats 3/4 + one per edition: the page is carried by medallions, not by prose about them.
    meds = body.count('class="pp-med ')
    assert meds >= 4, f'only {meds} medallions render -- the artifact no longer leads'

    # The mastery payoff is the transformation, so the holo treatment must actually be on the page.
    assert 'pp-med--holographic' in body, 'nothing on the page shows the holographic state'


def test_the_journey_reuses_the_shared_forge_art_and_motion():
    """Reuse over re-roll (project CLAUDE.md). The cover fan, the platinum cluster, the holo text and the
    reveal all exist already; a second copy of any of them is how two surfaces teaching the same thing
    start looking like two different products."""
    page = (ROOT / 'templates' / 'trophies' / 'badge_how_it_works.html').read_text(encoding='utf-8')

    for shared in ('pp-forge__fan', 'pp-forge__plats', 'pp-forge__holo',
                   'components/badge_medallion.html', 'PlatPursuit.staggerReveal'):
        assert shared in page, f'{shared} was re-rolled instead of reused'

    # The reveal must not be able to leave the steps hidden: `staggerReveal` returns null under reduced
    # motion, and `.pp-reveal` (which sets opacity: 0) is only added when it did not.
    assert 'if (handle) { list.classList.add' in page, (
        'pp-reveal is added unconditionally -- with reduced motion or no JS the beats stay invisible'
    )


def _visible_copy(rel):
    """A template's user-visible text: comments, scripts and tags stripped."""
    t = (ROOT / rel).read_text(encoding='utf-8')
    t = re.sub(r'\{%\s*comment\s*%\}.*?\{%\s*endcomment\s*%\}', '', t, flags=re.S)
    t = re.sub(r'\{#.*?#\}', '', t, flags=re.S)
    t = re.sub(r'<script[\s\S]*?</script>', '', t)
    return re.sub(r'<[^>]+>', ' ', t)


@pytest.mark.parametrize('rel', [
    'templates/trophies/badge_how_it_works.html',
    'templates/trophies/partials/badge_list/badge_howto.html',
])
def test_the_badge_teaching_uses_no_dashes_in_copy(rel):
    """House rule: no em dashes in anything a reader sees. The literal `--` matters just as much here,
    because a template renders it verbatim rather than typesetting it."""
    copy = _visible_copy(rel)
    for label, pattern in (('em dash', '\u2014'), ('en dash', '\u2013'), ('double hyphen', '--')):
        assert pattern not in copy, f'{label} in user-visible copy of {Path(rel).name}'


@pytest.mark.parametrize('rel', [
    'templates/trophies/badge_how_it_works.html',
    'templates/trophies/partials/badge_list/badge_howto.html',
])
def test_the_retired_forge_vocabulary_stays_out_of_copy(rel):
    """Forging / minting / striking belonged to an older badge system that was moved off, so it now
    describes something the product does not do. The medallions still read as real physical objects,
    which is the part that was always true.

    Checked on VISIBLE text only: the internal names (`.pp-forge__*`, `badge_forge_medallions`,
    `data-forge`) are deliberately untouched, since renaming them reaches across the modal's choreography
    for no reader-visible gain.
    """
    copy = _visible_copy(rel)
    for word in ('forged', 'forge', 'minted', 'struck'):
        assert not re.search(rf'\b{word}\b', copy, re.I), (
            f'{word!r} is back in the user-visible copy of {Path(rel).name}'
        )


def test_the_hero_medallion_opens_the_inspection_modal(client):
    """The quick-peek is the badge system's best feature, so the tutorial demonstrates it rather than
    describing it. Gated on a real source badge: on an empty catalog the medallions are plate-only
    illustrations, and a button that opens nothing is worse than no button."""
    page = (ROOT / 'templates' / 'trophies' / 'badge_how_it_works.html').read_text(encoding='utf-8')

    assert 'pp-forge-peek' in page, 'no medallion is tappable'
    assert 'Tap to inspect' in page, 'the affordance is discoverable-only, which undersells it'
    assert 'group_badge_quick_peek' in page, 'the peek dialog is not wired to the real endpoint'
    assert 'PlatPursuit.Medallion.detailModal' in page, 'the page rolls its own modal controller'
    # role=button spans are not natively keyboard-activatable.
    assert "e.key !== 'Enter'" in page, 'the tappable medallions cannot be activated by keyboard'

    # The peek is fixed chrome: inside {% block content %} it would sit in #page-recede, which steps BACK
    # behind an open modal, taking the modal with it.
    assert '{% block fixed_overlays %}' in page, 'the peek dialog is not in the overlay block'


def test_the_page_states_the_artwork_is_handmade_and_never_ai(client):
    """The strongest claim on the page and the actual reason a medallion is worth wanting. It gets its
    own section rather than a clause in the lede, and says it plainly with no hedging."""
    raw = client.get(reverse('badge_how_it_works')).content.decode()
    # Whitespace-normalised: the copy wraps across template lines, so a multi-word phrase has a newline
    # inside it in the rendered HTML. Matching the raw body makes this fail on a re-wrap, which is an
    # edit that changes nothing a reader sees.
    body = ' '.join(raw.split()).lower()

    assert 'drawn by hand' in body, 'the handmade claim is gone'
    assert 'ai generated' in body, 'the never-AI commitment is gone'
    assert 'pp-hiw__craft' in raw, 'the artwork claim lost its own section'

    # The claim is backed by the work itself. Asserted on the TEMPLATE: the strip is gated on there
    # being custom art at all, and a catalog without any (a fresh install, this test DB) correctly
    # renders the claim alone rather than an empty row of frames.
    page = (ROOT / 'templates' / 'trophies' / 'badge_how_it_works.html').read_text(encoding='utf-8')
    assert 'pp-hiw__artstrip' in page, 'the artwork examples are gone from the claim'


def test_the_artwork_strip_shows_the_pieces_not_the_medallions(client):
    """The medallion form works against this section's argument: the plate, shape and backing are shared
    across an edition, so four medallions from one series would look like four copies of one object. The
    SUBJECT is the part an artist drew, so the strip shows raw subject art, one per series."""
    page = (ROOT / 'templates' / 'trophies' / 'badge_how_it_works.html').read_text(encoding='utf-8')
    craft = page[page.index('pp-hiw__craft'):page.index('pp-hiw__eds')]

    assert 'badge_medallion.html' not in craft, (
        'the artwork claim renders composed medallions, which hides the part that was drawn'
    )
    assert 'pp-hiw__piece' in craft and '<img' in craft, 'no raw artwork is shown'
    # Not tappable: the peek opens a BADGE, and this section is about the artwork.
    assert 'pp-forge-peek' not in craft, 'the artwork strip opens badge detail, answering a different question'


def test_the_subject_art_helper_is_one_piece_per_series_and_skips_avatars():
    """Two rules the claim depends on. One per SERIES, or the strip shows the same drawing four times on
    different metals. And no avatar subjects: a submitter's profile picture is a real custom image as far
    as `art_layers()` can tell, but it is not a commissioned piece, and including one would make the
    handmade claim on this page false."""
    src = (ROOT / 'trophies' / 'views' / 'badge_views.py').read_text(encoding='utf-8')
    fn = src[src.index('def badge_subject_art('):]
    nxt = fn.find('\ndef ')          # it may be the last function in its block
    fn = fn if nxt == -1 else fn[:nxt]

    # The GUARD itself, not the names it uses. `seen` and `cand.series_id` both survive in `seen.add(...)`
    # after the skip is deleted, so checking for those passes against a helper that no longer dedupes.
    assert 'if cand.series_id in seen:' in fn, (
        'the helper no longer skips repeat series, so the strip can show one drawing four times'
    )
    assert "art.get('is_avatar')" in fn, 'avatar subjects are no longer excluded from the handmade claim'
    assert '[:60]' in fn, 'the scan is unbounded, so a large catalog turns this into a table sweep'
