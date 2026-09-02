"""Recap on a phone.

The ceremony is a fixed full-screen takeover with no scroll, so anything that does not fit is not
merely awkward -- it is unreachable. These pin the two things a mobile pass found, both of which the
suite could not have caught: a value clipped against the viewport edge, and a control too small to tap.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_the_versus_row_reserves_room_for_a_three_digit_value():
    """"100%" at `.pp-tally--sm` (18px bold) is ~55px wide and the column reserved 3rem (48px), so the
    percent sign was clipped against the phone's right edge on both rows.

    The column stays a FIXED width rather than `auto`: each row is its own grid, so an auto column sizes
    to its own content and the two bars stop sharing a baseline -- which the slide exists to show.
    """
    css = (ROOT / 'static' / 'css' / 'components' / 'recap-deck.css').read_text(encoding='utf-8')
    rule = re.search(r'\.rcp-vs__row \{[^}]*\}', css).group(0)

    cols = re.search(r'grid-template-columns:\s*([^;]+);', rule).group(1)
    assert 'auto' not in cols, 'an auto column breaks the shared baseline between the two bars'
    width = float(re.search(r'([\d.]+)rem\s*;?\s*$', cols.strip()).group(1))
    assert width >= 3.5, f'the value column is {width}rem; "100%" needs ~3.5rem and clipped at 3'


def test_the_entrance_controls_are_thumb_sized():
    """The entrance is the screen most likely to be seen on a phone and never on a desktop. Its two
    secondary controls are text links by design -- making them buttons would flatten the hierarchy the
    primary action needs -- so the hit area comes from padding with an equal negative margin, which buys
    the target without changing the layout."""
    css = (ROOT / 'static' / 'css' / 'components' / 'recap-stage.css').read_text(encoding='utf-8')
    rule = re.search(r'\.rcx-enter__aside a,\s*\n\.rcx-enter__link \{[^}]*\}', css).group(0)

    # Checked numerically, not by substring: 'padding: 0' is a PREFIX of 'padding: 0.7rem', so the
    # obvious string test passes on the very value it is meant to reject.
    pad = re.search(r'padding:\s*([\d.]+)rem', rule)
    assert pad and float(pad.group(1)) >= 0.6, 'not enough padding to reach a thumb-sized target (was 19px)'
    assert 'margin: -' in rule, 'padding without the negative margin pushes the row out of place'


def _code(src):
    """CSS with comments stripped.

    Load-bearing for every assertion below that forbids a construct: the comment explaining WHY a rule is
    forbidden inevitably names it, so a bare substring check is satisfied by the prose documenting the fix
    rather than by the code.
    """
    return re.sub(r'/\*.*?\*/', '', src, flags=re.S)


def test_the_share_card_is_scaled_by_transform_and_never_squeezed_by_layout():
    """The card is a FIXED 1200x630 render whose internals are positioned for that size, mounted inside a
    flex frame. With the default `flex-shrink: 1` a phone squeezed its WIDTH while its inline height held:
    1200x630 became 343x630, so the internals re-laid out at a third of their design width and landed on
    top of each other -- the header clipped mid-word, the month title across the activity panel.

    Nothing about the END state reveals this. The card was present, the aspect looked plausible in a
    thumbnail, and `fitCard` still produced a scale -- just computed from the wrong natural size.
    """
    css = _code((ROOT / 'static' / 'css' / 'components' / 'recap-stage.css').read_text(encoding='utf-8'))
    rule = re.search(r'\.rcx__card-frame > \* \{[^}]*\}', css).group(0)

    assert re.search(r'flex:\s*none|flex-shrink:\s*0', rule), (
        'the card can be shrunk by layout again; only `transform: scale()` may resize it'
    )
    assert 'transform: scale(var(--rcx-card-scale' in rule, 'the card is no longer scaled to fit'


def test_a_partial_row_of_medallions_centres():
    """Most months bring ONE badge, and fixed `1fr` tracks cannot centre a row they do not fill: the lone
    medallion sat in column 1 -- hard left, under the stage's back arrow -- while the kicker, figure and
    caption above it were all centred. On desktop it was column 1 of FOUR, so this was never mobile-only.
    """
    css = _code((ROOT / 'static' / 'css' / 'components' / 'recap-deck.css').read_text(encoding='utf-8'))
    rule = re.search(r'\.rcp-meds \{[^}]*\}', css).group(0)

    assert 'justify-content: center' in rule, 'a partial row of medallions no longer centres'
    assert not re.search(r'grid-template-columns:\s*repeat\(\s*\d+', rule), (
        'fixed tracks are back, which strands any count that does not fill the row'
    )


def test_a_hero_TITLE_is_capped_to_the_band_between_the_nav_arrows():
    """The hero scale is built for a NUMBER -- one to three glyphs. Three slides borrow it for a PHRASE
    (the month, the top genre, "That's a wrap"), and at 375px "That's a wrap" measured 300px and ran under
    BOTH nav arrows; a genre like "Hack and slash/Beat 'em up" spanned the viewport edge to edge.

    The cap is a WIDTH, deliberately not a smaller font: "February" already fitted the band, so stepping
    the size down would have cost the opening beat its scale to fix a different slide's overflow. Capped,
    a long title wraps inside the band and keeps the monumental size.
    """
    css = _code((ROOT / 'static' / 'css' / 'components' / 'recap-stage.css').read_text(encoding='utf-8'))

    # The requirement is DERIVED from the arrows rather than restated, so widening them fails this test
    # instead of silently reintroducing the collision.
    aim = re.search(r'\.rcx__aim \{[^}]*\}', css).group(0)
    arrow_w = float(re.search(r'width:\s*([\d.]+)rem', aim).group(1))
    inset = float(re.search(r'\.rcx__aim--prev \{[^}]*left:\s*([\d.]+)rem', css).group(1))
    needed = 2 * (arrow_w + inset)

    rule = re.search(r'\.rcx \.rcp__title\.pp-tally--hero \{[^}]*\}', css).group(0)
    reserved = float(re.search(r'max-width:\s*calc\(100vw\s*-\s*([\d.]+)rem\)', rule).group(1))
    assert reserved >= needed, f'reserves {reserved}rem but the two arrows occupy {needed}rem'

    # Mobile-scoped: on a wide stage the band is huge and the cap would only shorten long lines for no
    # reason. Checked STRUCTURALLY -- unbalanced braces prove the rule is nested inside a block, not merely
    # preceded by one. A positional "last @media before it" test passes on a hoisted rule as soon as any
    # earlier max-width query exists in the file.
    head = css[:css.index(rule)]
    assert head.count('{') - head.count('}') == 1, 'the cap is not nested inside a media query'
    media = re.findall(r'@media\s*\(max-width:\s*(\d+)px\)', head)
    assert media and int(media[-1]) <= 767, 'the cap is not scoped to phones'


def test_the_community_caption_agrees_with_its_subject():
    """It read "1 hunter have played it"."""
    tpl = (ROOT / 'templates' / 'recap' / 'partials' / 'slides' / 'community.html').read_text(encoding='utf-8')

    assert 'pluralize:"has,have"' in tpl, 'the verb no longer agrees with the hunter count'
    assert 'hunter{{ played_count|pluralize }} have played' not in tpl
