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
| `landing-platinum-cards.png`   | Platinum Cards               | 1200 x 630 (40:21 aspect)    |
| `landing-platinum-grid.png`    | Platinum Grid                | 1200 x 630                   |
| `landing-profile-card.png`     | Profile Card                 | 1200 x 630                   |
| `landing-monthly-recap.png`    | Monthly Recap                | 1200 x 630                   |
| `landing-challenge-cards.png`  | Challenge Cards              | 1200 x 630                   |

The card image area uses `aspect-[1200/630]` (the canonical Open Graph share
card aspect), matching what the rest of the share-image system produces.
1200x630 PNGs fill the wrapper edge-to-edge with no cropping. Off-aspect
uploads still work — the wrapper uses `bg-contain bg-no-repeat` so any
mismatch shows the gradient as letterboxing instead of cropping the image.

The card-type icon sits in a small badge in the bottom-right corner of the
image area so it identifies the card without obscuring the share image
itself. PNG transparency is fine; transparent regions show the gradient
through cleanly.

## How the fallback works

The landing template ([templates/shareables/landing.html](../../../templates/shareables/landing.html))
uses a CSS layered background like:

```css
background-image: url('.../landing-platinum-cards.png'),
                  linear-gradient(135deg, var(--color-primary) 0%, var(--color-secondary) 100%);
```

If the PNG file is missing or fails to load, the browser falls back to the
gradient. No code change needed when adding or removing asset files —
just drop the PNG and reload.

> **daisyUI 5 variable names matter.** The theme color variables are
> `--color-primary`, `--color-secondary`, `--color-accent`, `--color-info`,
> `--color-success` (etc.) — NOT the legacy short forms `--p`, `--s`, `--a`,
> `--in`, `--su` from daisyUI 3/4. The full value stored in each variable is
> a complete `oklch(...)` expression, so use `var(--color-primary)` directly
> in the gradient stops; do NOT wrap it in another `oklch()` call. If you do,
> the whole `background-image` declaration is invalid (per CSS cascade rules,
> any invalid value in a comma-separated list discards the entire property)
> which makes BOTH the PNG layer AND the gradient layer disappear, leaving
> only the icon overlay visible. The icon-only-no-background symptom on the
> landing cards came from exactly this bug.

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
