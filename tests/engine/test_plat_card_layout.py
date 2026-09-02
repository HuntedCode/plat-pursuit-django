"""The plat card is a FIXED 1200x630 canvas with `overflow: hidden`, so too much content does not
scroll or wrap -- it silently clips, and what clips first is the flex-shrunk title.

That is how a card shipped with "Toy Story 3" cut through the middle of its glyphs: a card carrying
BOTH a quick take and a badge band ran the column past 630px, the title div shrank from its natural
56px line box to 36px, and `overflow: hidden` took the rest. Nothing raised.

MEASURED IN A REAL BROWSER, WITH THE REAL FONTS. `render_to_string` alone produces markup with no
`@font-face`, so Chromium substitutes a fallback whose metrics are narrower than Bricolage
Grotesque -- and the layout then measures fine because it is not the layout that ships. Three
attempts at this test said "no bug" for exactly that reason before `_build_font_faces()` went in.
"""
import pathlib

import pytest

pytest.importorskip('playwright.sync_api')


def _browser_missing():
    """Playwright ships in requirements (the renderer needs it), but the BROWSER is a separate
    download. A developer who has not run `playwright install` should get a skip with a reason, not
    a launch error in the middle of an unrelated run.

    This never skips in CI: `.github/workflows/tests.yml` installs chromium explicitly, and if that
    step ever breaks it fails there, loudly, rather than quietly turning this file into a no-op.
    """
    from playwright.sync_api import sync_playwright

    try:
        with sync_playwright() as p:
            p.chromium.launch().close()
        return None
    except Exception as exc:  # noqa: BLE001 -- any launch failure means we cannot measure
        return f'playwright chromium unavailable ({type(exc).__name__}); run `playwright install chromium`'


#: Resolved ONCE at import. Calling the probe inside both skipif arguments would launch a browser
#: twice on every collection of this file, for an answer that cannot change mid-run.
_SKIP_REASON = _browser_missing()

pytestmark = [
    pytest.mark.django_db,
    pytest.mark.skipif(_SKIP_REASON is not None, reason=_SKIP_REASON or ''),
]

#: The shape that overflowed: every optional block present at once.
FULL_CARD = {
    'variant': 'platinum',
    'username': 'Lucifer1991',
    'game_name': 'Toy Story 3',
    'platform_label': 'PS4',
    'trophy_total': 27,
    'trophy_earn_rate': '4.2',
    'rarity_label': 'Ultra Rare',
    'playtime': '34m',
    'tier_counts': [
        {'colour': '#27ebfe', 'count': 1}, {'colour': '#fcca21', 'count': 5},
        {'colour': '#c7d0da', 'count': 19}, {'colour': '#cd7f32', 'count': 2},
    ],
    'user_rating': {
        'difficulty': 2, 'grindiness': 3, 'fun_ranking': 6, 'overall_rating': 2.0,
        'stars_pct': 40.0, 'recommendation': 'skip',
        'recommendation_label': 'Skip it', 'recommendation_short_label': 'Skip it',
        'blurb': 'Boring and repetitive',
    },
    'badge_lines': [{
        'series_name': 'PSP Classics Megamix', 'edition': 'Ultra HD', 'title': 'Pathfinder',
        'stages_cleared': 6, 'stages_total': 29, 'medallion_colour': '#27ebfe',
        'medallion_cached': None, 'medallion_is_avatar': False,
    }],
    'contract': {'jobs': [{'name': 'Pathfinder', 'icon': 'compass', 'colour': '#3add9e'}]},
}


def _font_faces(monkeypatch):
    """The renderer's own @font-face block, sourced from wherever the TTFs actually are.

    `playwright_renderer.FONTS_DIR` is `STATIC_ROOT / 'fonts'` -- the COLLECTED directory, which is
    right for the renderer (prod runs collectstatic on every deploy) and empty in CI, which never
    does. The nine TTFs are tracked in `static/fonts/`, so that is the copy always on disk.

    Repointed rather than skipped. A layout guard that quietly skips wherever the fonts are missing
    would skip in exactly the place it is supposed to run, and this test exists because a fallback
    typeface makes a clipping card measure clean.
    """
    from django.conf import settings

    import core.services.playwright_renderer as renderer

    collected = renderer.FONTS_DIR
    source = pathlib.Path(settings.BASE_DIR) / 'static' / 'fonts'
    chosen = collected if (collected / 'BricolageGrotesque-Bold.ttf').exists() else source

    monkeypatch.setattr(renderer, 'FONTS_DIR', chosen)
    monkeypatch.setattr(renderer, '_cached_font_faces', None)   # restored at teardown
    return renderer._build_font_faces()


def _measure(tmp_path, monkeypatch, context):
    """Render the card with the shipping fonts and report the title box against its line box."""
    from django.template.loader import render_to_string
    from playwright.sync_api import sync_playwright

    faces = _font_faces(monkeypatch)
    assert 'Bricolage' in faces, (
        'no fonts were found in either static/fonts or STATIC_ROOT/fonts, so this would measure a '
        'fallback typeface and pass on a card that clips in production'
    )
    path = tmp_path / 'card.html'
    path.write_text(f'<style>{faces}</style>' + render_to_string('shareables/plat_card.html', context),
                    encoding='utf-8')

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={'width': 1200, 'height': 630})
        page.goto(path.as_uri())
        page.wait_for_timeout(400)
        box = page.evaluate("""(name) => {
            const el = [...document.querySelectorAll('div,span')]
                .find(e => e.textContent.trim() === name);
            if (!el) return null;
            return {clientH: el.clientHeight, scrollH: el.scrollHeight};
        }""", context['game_name'])
        browser.close()
    assert box, 'the title element was not found in the rendered card'
    return box


def test_the_title_is_not_clipped_on_a_fully_loaded_card(tmp_path, monkeypatch):
    """THE regression. Before the verdict pill moved into the numbers row, this exact shape shrank
    the title box to 36px against a 56px line box -- 20px of the game's name cut off, on the one
    element the card exists to name.

    The tolerance is 6px, not 0: a `-webkit-line-clamp` box reports a scrollHeight a few pixels over
    its clientHeight from line-box rounding even when nothing is visibly cut. 20px is glyphs."""
    box = _measure(tmp_path, monkeypatch, dict(FULL_CARD))

    clipped = box['scrollH'] - box['clientH']
    assert clipped <= 6, (
        f"the game title is clipped by {clipped}px (box {box['clientH']}px vs line box "
        f"{box['scrollH']}px). Something was added to the card's fixed 630px column -- the title is "
        f"what gives way first, and it fails silently."
    )


def test_the_title_survives_a_two_line_game_name(tmp_path, monkeypatch):
    """The title is `-webkit-line-clamp: 2`, so a long name legitimately takes both lines. That is
    the tightest the column ever gets, and it must still not eat into the glyphs."""
    ctx = dict(FULL_CARD)
    ctx['game_name'] = "Marvel's Spider-Man: Miles Morales Ultimate Edition"

    box = _measure(tmp_path, monkeypatch, ctx)

    assert box['scrollH'] - box['clientH'] <= 6, 'a two-line title clips on a fully loaded card'
