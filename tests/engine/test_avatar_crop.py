"""Every round avatar container actually crops the image inside it.

A square PSN avatar rendered in the navbar as a square, corners escaping the ring, because
`.pp-av__img` was missing BOTH halves of the crop:

  - no `overflow: hidden`, so its `border-radius` clipped nothing and `place-items: center` centred an
    oversized child that spilled straight past the rounded box;
  - `object-fit: cover` written on the SPAN, where it does nothing. `object-fit` applies only to
    replaced elements (img, video) and fails SILENTLY on anything else -- so the intent was written
    down, never executed, and read as correct for as long as the avatars people tested with were
    already square-ish.

That second one is why this is a test and not just a fix. A declaration that is inert rather than
invalid survives every review, every linter and every browser without a warning; the only thing that
catches it is asserting the pair exists. Three other containers -- `.lb-row__av`, `.pp-phero__avatar`
and `.pp-minibar__ava`, the last 280 lines below the broken one in the same file -- had it right all
along, which is what makes the missing pair a slip rather than a decision.

Source pins by necessity: whether a corner visually overhangs a circle is a rendering question, and
there is no browser here. What can be pinned is that every container claiming to be round declares the
two properties that make it so.
"""
import re
from pathlib import Path

from django.conf import settings as dj_settings

#: (stylesheet, container class). Each is a round avatar frame with an <img> inside.
CONTAINERS = [
    ('chrome.css', 'pp-av__img'),
    ('chrome.css', 'pp-minibar__ava'),
    ('leaderboards.css', 'lb-row__av'),
    ('profile-hero.css', 'pp-phero__avatar'),
]


def _css(name):
    path = Path(dj_settings.BASE_DIR) / 'static' / 'css' / 'components' / name
    text = path.read_text(encoding='utf-8')
    return re.sub(r'/\*.*?\*/', '', text, flags=re.S)      # comments name these properties


def _block(css, selector):
    """The declaration block for an exact selector, or None.

    Matched with a boundary so `.pp-av__img` cannot be satisfied by `.pp-av__imgX`, and so the
    container's own block is not confused with its descendant rules (`.x img { ... }`).
    """
    match = re.search(r'\.' + re.escape(selector) + r'\s*\{([^}]*)\}', css)
    return match.group(1) if match else None


def test_every_round_avatar_container_clips_its_image():
    """`border-radius` alone crops nothing. Without `overflow: hidden` the child simply overhangs."""
    offences = []
    for sheet, selector in CONTAINERS:
        block = _block(_css(sheet), selector)
        if block is None:
            offences.append(f'{sheet}: .{selector} has no rule at all')
            continue
        # The VALUE, not the property name: `overflow: visible` -- the bug under test, stated as
        # explicitly as it can be stated -- satisfied a bare `'overflow' not in block`. So did
        # `overflow-x: hidden` alone, which does not clip vertically.
        if not re.search(r'overflow\s*:\s*(hidden|clip)[\s;}]', block + ';'):
            offences.append(f'{sheet}: .{selector} rounds its corners but never clips its child')
    assert not offences, 'containers that do not crop:\n  ' + '\n  '.join(offences)


def test_the_cover_fit_is_on_the_IMAGE_not_the_container():
    """The exact defect: `object-fit` on a non-replaced element is inert, not invalid, so it passes
    review and renders nothing. It has to be on a descendant `img` rule."""
    offences = []
    for sheet, selector in CONTAINERS:
        css = _css(sheet)
        child = re.search(r'\.' + re.escape(selector) + r'\s+img\s*\{([^}]*)\}', css)
        if child is None:
            offences.append(f'{sheet}: .{selector} never sizes the img inside it')
            continue
        decls = child.group(1)
        # COVER specifically. `object-fit: contain` (letterbox) and `fill` (skew, and banned outright by
        # CLAUDE.md) both satisfied a bare `'object-fit' not in`, while the message promised otherwise.
        if not re.search(r'object-fit\s*:\s*cover[\s;}]', decls + ';'):
            offences.append(f'{sheet}: .{selector} img does not cover-fit, so it will letterbox or skew')
        # `width`, not `max-width`. The substring version was satisfied by `max-width` -- which is
        # precisely the Tailwind-preflight-only state that produced the original bug.
        for prop in ('width', 'height'):
            if not re.search(r'(?<![-\w])' + prop + r'\s*:', decls):
                offences.append(f'{sheet}: .{selector} img has no explicit {prop}; it will render at '
                                f'its natural size')

        container = _block(css, selector)
        if container and 'object-fit' in container:
            offences.append(
                f'{sheet}: .{selector} declares object-fit on the CONTAINER, where it does nothing'
            )
    assert not offences, 'broken image fits:\n  ' + '\n  '.join(offences)


def test_the_avatar_SHELL_must_never_clip():
    """The one edit that would break everything, and nothing guarded it.

    The clip belongs on the inner `.pp-av__img`, never on `.pp-av` itself. `.pp-av` is the positioning
    context for four things that deliberately hang OUTSIDE its box: the status ring (`::after`, inset
    -4px), the syncing arc (`::before`), the sync LED (`right/bottom: -2px`) and the moderation queue
    badge (`right/top: -5px`). `overflow: hidden` there silently deletes all four -- and it is the
    obvious "fix" for anyone who sees the crop bug and reaches for the outer element first.
    """
    block = _block(_css('chrome.css'), 'pp-av')
    assert block is not None, '.pp-av has no rule'
    assert 'overflow' not in block, (
        '.pp-av must not clip: the ring, the syncing arc, the sync LED and the queue badge all sit '
        'outside its box and would vanish'
    )


def test_the_navbar_avatar_guards_on_the_URL_not_the_profile():
    """`avatar_url` is nullable, so `{% if user.profile %}` renders `<img src="">` for a linked hunter
    with no avatar: the browser fetches the page as an image and shows a broken-image icon, and the SVG
    fallback is never reached. Harmless-looking until the crop fix lands, which then stretches that
    placeholder to fill the whole circle. The sync-panel instance 17 lines below always had it right.
    """
    navbar = (Path(dj_settings.BASE_DIR) / 'templates' / 'partials' / 'navbar.html').read_text(encoding='utf-8')
    for chunk in navbar.split('class="pp-av__img"')[1:]:
        head = chunk.split('{% endif %}', 1)[0]
        if '<img' not in head:
            continue          # the anon glyph-only instance
        assert 'avatar_url %}' in head, (
            'an avatar renders <img src=""> when the profile has no avatar_url; guard on the URL'
        )
