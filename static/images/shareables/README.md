# Shareables landing page example images

This directory holds the example images shown on the My Shareables landing
page (`/dashboard/shareables/`). Each landing card uses a CSS layered
background-image: the static PNG first, then a gradient as a fallback. If
the PNG file doesn't exist yet, the gradient shows through cleanly with no
broken-image icon and no JS error handling.

Drop new asset PNGs here to replace the gradient placeholders.

## Expected files

| Filename                       | Card on landing page         | Recommended dimensions      |
|--------------------------------|------------------------------|------------------------------|
| `landing-platinum-cards.png`   | Platinum Cards               | 1200 x 675 (16:9 aspect)     |
| `landing-platinum-grid.png`    | Platinum Grid                | 1200 x 675                   |
| `landing-profile-card.png`     | Profile Card                 | 1200 x 675                   |
| `landing-monthly-recap.png`    | Monthly Recap                | 1200 x 675                   |
| `landing-challenge-cards.png`  | Challenge Cards              | 1200 x 675                   |

The card image area uses `aspect-video` (16:9), so 1200x675 keeps the image
crisp at any reasonable display size without distortion. PNG with
transparency is fine; the card has a dark background showing through if any
transparent areas exist.

## How the fallback works

The landing template ([templates/shareables/landing.html](../../../templates/shareables/landing.html))
uses a CSS layered background like:

```css
background-image: url('.../landing-platinum-cards.png'),
                  linear-gradient(135deg, oklch(var(--p)) 0%, oklch(var(--s)) 100%);
```

If the PNG file is missing or fails to load, the browser falls back to the
gradient. No code change needed when adding or removing asset files —
just drop the PNG and reload.

## Existing fallback gradients

| Card               | Gradient                              |
|--------------------|---------------------------------------|
| Platinum Cards     | primary → secondary                   |
| Platinum Grid      | secondary → accent                    |
| Profile Card       | accent → primary                      |
| Monthly Recap      | info → secondary                      |
| Challenge Cards    | success → accent                      |

These match the daisyUI theme variables so they shift naturally when the
theme changes.
