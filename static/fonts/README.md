# Share-card fonts

These TTFs exist for **one consumer**: `core/services/playwright_renderer.py`, which base64-embeds them
as `@font-face` rules before handing HTML to Chromium. The renderer runs `page.set_content()` in an
`about:blank` origin with no filesystem or network access, so a share card can only use a typeface that
is embedded here. The rest of the site loads the same families from Google Fonts in `base.html`; these
files are the offline copies.

**A font not in this directory cannot appear on a share card**, no matter what the template asks for.
`_build_font_faces()` holds the filename -> (family, weight) map; adding a weight means dropping the TTF
here *and* registering it there.

| Family | Weights | Role |
|---|---|---|
| Bricolage Grotesque | 400, 600, 700 | Display: the `--pp-font-display` voice (game title, username, tallies) |
| Inter | 400, 600, 700 | Body: labels, meta, small caps |
| Poppins | 400, 600, 700 | Legacy; the recap card still asks for it |

## Payload

Every render embeds **all** registered fonts, used or not, at roughly 4/3 their file size once base64'd.
The current set is ~1.9 MB on disk, so each card's HTML document carries ~2.6 MB of font data before any
images. `_cached_font_faces` builds the string once per process, so the cost is CPU-cheap after the first
render, but it is why the renderer's image budget is tight (see the `image_max_size` note in
`docs/features/share-images.md`). If more families get added, embedding only the fonts a given card
declares would be the fix.

## Licensing

Bricolage Grotesque, Inter, and Poppins are all under the **SIL Open Font License 1.1**, which permits
bundling and redistribution. `OFL.txt` is the license text as published with Bricolage Grotesque
(Copyright 2022 The Bricolage Grotesque Project Authors, https://github.com/ateliertriay/bricolage); the
same license governs the other two families.
