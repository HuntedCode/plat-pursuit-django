# Site Rebuild — Playbook & Progress

> **The single "start here" for any page rebuild.** Two jobs: (1) track which pages are done, and
> (2) capture the shared decisions every rebuilt page inherits, so we stop re-deciding them per page.
>
> This doc **indexes** the authoritative docs (it does not duplicate them). When a shared decision
> changes, update the "Shared Elements" section here **and** the authoritative doc it points to.

Related: **[career-reference-standard.md](career-reference-standard.md)** (the quality bar / "what done
means"), **[../../reference/design-system.md](../../reference/design-system.md)** (tokens, patterns,
component blueprints), **[../visual-identity.md](../visual-identity.md)** (the constitution),
**[../../reference/motion-patterns.md](../../reference/motion-patterns.md)** (motion),
**[chrome-audit.md](chrome-audit.md)** (nav/tabbar/footer), **[ia-map.md](ia-map.md)** (IA),
**[system-inventory.md](system-inventory.md)** (engine/system map).

---

## How to use this

**Before rebuilding a page:** read the [Shared Elements](#shared-elements-every-rebuilt-page-inherits-these)
checklist below and the [Career reference standard](career-reference-standard.md). Reuse the tokens and
patterns; do not re-derive them.

**After finishing (or advancing) a page:** update its row in [Page Status](#page-status).

---

## This is a REBUILD, not a reskin — the from-scratch rule

When a page is rebuilt "from scratch," the old implementation is a **data/behavior contract ONLY** -- it
tells you *which* data exists and *what the page must do*, nothing more. Every visual and UX decision
(palette, emphasis, density, layout, motion, curation) is **re-derived from the rebuild system**, starting
from a blank canvas. Don't open the old file for design cues.

- **Legitimate carryover:** the data contract (which fields/stats exist + what they mean), the behaviours
  the page needs, and the **shared rebuild tokens + components** -- that IS the rebuild (see
  [Approved Building Blocks](#approved-building-blocks)).
- **NOT carryover:** the old page's bespoke decisions -- its colour-coding, thresholds, gradients, one-off
  classes, and its "show every stat" density. Re-expressing those in new class names is a **reskin, not a
  rebuild**.
- **"Everything's a token" is NOT the bar.** You can use only approved tokens and still fail the rebuild, by
  *applying* them like the old page did (e.g. colouring every number). The bar is: each choice is justified
  against how Career/Collection *actually look* -- not against the old file.

> **Litmus test:** if your only reason for a colour / spacing / emphasis / density choice is "the old page
> did it," and you can't point to Career, Collection, or the design system for it, it isn't a from-scratch
> decision. (This section exists because a badge-detail header shipped with ~90% of its palette ported from
> the old design -- tokenised, but still a reskin.)

---

## Page Status

**Legend:** ✅ Done to the Career standard · 🟡 Partial (structurally aligned, full pass pending) ·
⛔ Not started · 🗑️ Sunsetting/legacy.

**Only four pages are finished to the standard: Career, Collection, Badges, Badge Detail.** Everything
else — even pages that already borrow the header card or shipped in an earlier phase — is NOT done: it
still needs the full pass (depth, segmented switcher, premium motion, mobile three-layout verification).

| Page | URL | Status | Notes |
|---|---|---|---|
| **Career** | `/career/` | ✅ | **The reference standard.** Jobs / Radar / Contracts. Depth pass applied. |
| **Collection** | `/collection/` | ✅ | Case + Gallery. **Object-depth model** (medallion cast/rim shadows carry depth — deliberately does NOT take the card-lift). |
| **Badges** | `/badges/` | ✅ | Series + Gallery views; dynamic HTMX view-swap; depth pass; filter/sort settle. Anon quick-peek modal deferred. |
| **Badge Detail** | `/badges/<slug>/` | ✅ | From-scratch: header + tier ladder + how-to-earn grid + context band (rarity/ranks/My Stats modal) + stage journey (game cards w/ **contract band → in-place modal**, bundle, delisted strip, numbered spine w/ "Up next"). Audited. |
| **Home / Overview** | `/` | 🟡 | 4 gamification-first blocks shipped in an earlier phase; not finished to standard. Shares `.scard` (got the depth lift). |
| **Community Hub** | `/community/` | 🟡 | Hub-of-hubs shipped in an earlier phase; not finished to standard. |
| **Profile** | `/u/<user>/` | 🟡 | Ownership-aware chrome only; full surface pass pending. |
| **Challenges / Franchise / Company / Game Lists / Browse pages** | various | 🟡 | Header card adopted, but **no** depth pass / segmented switcher / premium motion. Header-aligned only. |
| **Game Detail** | `/game/<id>/` | 🟡 | Frosted-glass header (`image_urls.header_bg_url` + screenshots) retained; full pass pending. |
| **Settings** | `/settings/` | ⛔ | Not rebuilt. Premium theme/background picker **disabled** pending rebuild (see [Gotchas](#gotchas-and-pitfalls)). |
| **Dashboard** | `/dashboard/` | 🗑️ | Sunsetting (301 → `/`); 41-module registry retired. Do last; some `dashboard_service` providers still load-bearing. |
| **Minigames** (Stellar Circuit) | `/arcade/...` | 🗑️ | Only remaining **ZoomScaler** page. Legacy transform-scale. |

> **Chrome** (nav / tabbar / subnav / hotbar / footer) is the site-wide **FRAME**, not a page — it was
> aligned 2026-07 (see [chrome-audit.md](chrome-audit.md)). Style it as chrome, never card-ify it.
>
> **ZoomScaler is effectively phased out** — only the minigame prototype still calls
> `PlatPursuit.ZoomScaler.init()`. Rebuilt pages are mobile-first three-layout (375 / 768 / 1024+),
> not transform-scaled.

---

## Shared Elements (every rebuilt page inherits these)

The reusable decisions. Each is **"the decision → where it lives → the authoritative doc."** Apply all of
them to every new page rebuild.

### 1. Page structure — STACKED chrome, FREE content
Chrome cards (page header, toolbars) are **stacked** cards; the content itself (grids, panels, tab bodies)
flows **FREE** — never wrapped in an outer card, even when tabbed. → design-system.md (Card Variants),
career-reference-standard.md §1.

### 2. Page header = accented card with substance
DaisyUI card shell + `--pp-*` substance: `card bg-base-200/90 border-2 border-base-300 border-l-4
border-l-primary shadow-lg shadow-neutral`. Title + italic subtitle + a headline **Tally** stat, and pull
**substance into the header** (stats, a collapsible explainer) rather than separate cards below (see
Career/Collection/Badges headers). Widely adopted already.

### 3. Tab groups = segmented switcher (ONE treatment site-wide)
Bordered container + transparent chips, tinted-flat active state, an icon per chip, **right-aligned** in a
`flex items-center justify-end` row. Implementations: `.pp-vtoggle` (Badges), `.pp-collection__views`
(Collection), `.lab-views` (Career). Old pill tabs are retired. → design-system.md (Tab Group / View
Switcher).

### 4. Depth — the surface ladder (the "depth pass")
Deepened 2026-07 so cards separate from the substrate by the **gap**, not by lightening anything.

| Rung | Token | ~L | Role |
|---|---|---|---|
| Substrate (`<body>`) | `--pp-bg-0` = `--color-base-100` (dark) | 0.13 | page base (`oklch(0.13 0.012 254)`) |
| Base cards | `--pp-bg-1` / `base-200` | 0.23 | content cards |
| Raised / nested | `--pp-bg-2` | 0.28 | cards nested inside a base-200 header; select menus |
| Highest | `--pp-bg-3` | 0.33 | rare |

- **Content cards** catch light + cast: `box-shadow: inset 0 1px 0 rgba(255,255,255,0.07), 0 6px 20px
  rgba(0,0,0,0.30)`. (`.pp-bgal__card`, `.pp-scard`, `.job`.)
- **Nested cards** (a card *inside* a base-200 header) **step UP** `--pp-bg-1 → --pp-bg-2` + a soft lift
  (`inset 0 1px 0 rgba(255,255,255,0.05), 0 3px 10px rgba(0,0,0,0.20)`), or they dissolve into the header.
  (`.pp-btiers__rung`, `.scard`.)
- **Do NOT lighten the substrate to add separation** — deepen it. Lightening flattens the gap and washes
  out the dark identity.
- **Exception — object-depth surfaces (Collection):** where a medallion carries its own outset cast/rim
  shadows + pedestal, keep the card minimal/inset (a drop shadow would clip those glows). Let the *object*
  float, not the card.

### 5. Toolbars = quiet chrome, not heroes
Base surface from the shared `.pp-toolbar-card` (`bg-base-200/90` + border), but **soften the shadow** so
the toolbar sits back and the content cards below own the pop: `box-shadow: 0 1px 3px rgba(0,0,0,0.22)`,
scoped per page (`.pp-bgal .pp-bgal__toolbar`, `.rp-toolbar.pp-toolbar-card`) with a 2-class selector so
it wins over `.pp-toolbar-card` without recolouring the shared class. Compact one-row bar (search + sort +
a Filters toggle); multi-select chips in a collapsible panel; filters auto-apply (no Apply button).

### 6. Premium motion (+ always gate reduced-motion)
Signature moments on a budget — real physics (spring settle), choreographed exits, deliberate restraint.
Use **WAAPI (`el.animate`)** for reveals so they replay reliably on HTMX-swapped nodes (CSS-class
animations don't restart). Every animation gates on `prefers-reduced-motion` — CSS in
`@media (prefers-reduced-motion: no-preference)`, JS via `PlatPursuit.Medallion.prefersReducedMotion()` /
`countUp()` (which jumps to target). → career-reference-standard.md §3, motion-patterns.md.

**Opening beat — every page's header/hero enters the same way.** Put **`.pp-head-cascade`**
(`components/motion.css`) on the header's **card-body**: its direct children (title, then stats, then the
rest) fade + rise in a staggered cascade — a livelier, layered entrance than moving the whole card as one
block, and the stagger is what reads as "premium." Up to three beats, then the rest share the last;
reduced-motion gated. Put it on **every** rebuilt page's top header (on Career, **both** the hero and the
summary header). **If the header lives inside an HTMX swap island** (badge detail's header rides
`#badge-tier-view`), gate it so it plays on first load only — `{% if not is_tier_swap %} pp-head-cascade{% endif %}`
— or it replays on every swap. Live on Career (hero + summary), Collection, Badges list, Badge detail.

### 7. Dynamic view swaps (HTMX innerHTML)
View toggles swap an island via `hx-get` + `hx-target="#..." hx-swap="innerHTML" hx-push-url`, not a full
reload. Re-init reveals/scrollers in an `htmx:afterSwap` handler keyed on `e.detail.target.id`. (Badges
`#badge-view`, Collection, Career.)

### 8. Filter/sort settle (no blank-flash)
On a filter/sort swap, dim the results container while in flight so it never freezes/blank-flashes. Add
the dim **on `change`** (a JS `.is-swapping` class) so it spans the `hx-trigger` debounce — not just the
network request — then clear it in `htmx:afterSwap`. Motion-gated. Empty-state panels fade+rise in.

### 9. Ad slot placement
A horizontal `partials/ad_unit.html` goes **after the page header, before the view tabs**, outside the tab
panels — so it shows on whichever view is loaded and a tab swap never re-inits it. (Badges, Collection,
Career.)

### 10. Modals = top of the elevation stack (insulated from the substrate)
Scrim `rgba(2,4,8,~0.6)` + `backdrop-filter: blur(3–4px)`; dialog on `--pp-bg-1` + a big float shadow
`0 30px 90px rgba(0,0,0,0.55)`; internal stats step up to `--pp-bg-2`. Because they float on a scrim (not
the substrate) they need **no** depth-pass lift — the deeper substrate only helps them. Shared factory:
`PlatPursuit.Medallion.detailModal(config)` (pick-up / put-down). (`.pp-detail-modal`, `.emodal`.)

### 11. Image conventions
Covers use `object-cover object-top` + `aspect-[3/4]`; trophy icons `object-cover` square; badges
`object-contain`. Never `object-fill`. Cover fallback chain lives on `Game.display_image_url` — never
reimplement inline. → project CLAUDE.md (Image Styling Conventions).

### 12. Whale-safe querysets
Per-user aggregates (counts/sums/distributions) **must** DB-aggregate (`.values().annotate(Count)` /
`.aggregate()`), never Python iteration over a profile-scoped queryset. Preview/locked UIs must not run
heavy providers against real user data. → project CLAUDE.md (Performance / Premium Preview).

---

## Approved Building Blocks

**Build from these, not from the old page.** The canonical source is `static/css/input.css` (token values) +
[design-system.md](../../reference/design-system.md) (patterns); this is the quick reference to consult
before a from-scratch pass. When you reach for a colour/spacing/font, it should be one of these.

### Tokens (`--pp-*`)

| Group | Tokens |
|---|---|
| **Surfaces** | `--pp-bg-0` (0.13 substrate) · `--pp-bg-1` (0.23 cards) · `--pp-bg-2` (0.28 nested/raised) · `--pp-bg-3` (0.33) |
| **Text** | `--pp-text` · `--pp-text-dim` · `--pp-text-mute` |
| **Lines** | `--pp-border` · `--pp-divider` |
| **Brand** | `--pp-primary` (cyan) · `--pp-secondary` (violet) · `--pp-accent` (amber) |
| **Semantic** | `--pp-success` · `--pp-warning` · `--pp-error` · `--pp-info` |
| **Type** | `--pp-font-display` (Bricolage — hero + numbers ONLY) · `--pp-font-body` (Inter) |
| **Motion** | `--pp-dur-fast` (140ms) · `--pp-dur` (240ms) · `--pp-dur-slow` (520ms) · `--pp-ease` |
| **Shape** | `--pp-border-w` (2px) · `--pp-radius-sm` / `-md` / `-lg` |

DaisyUI theme colours mirror the brand/semantic tokens and are applied via Tailwind `text-*`/`bg-*`
(`primary`, `secondary`, `accent`, `success`, `warning`, `error`, `base-100`..`base-300`).

**Scoped colour families — use ONLY on their own surfaces, never as generic accents:** trophy
`--color-trophy-{bronze,silver,gold,platinum}` · career disciplines
`--disc-{combat,exploration,mind,heart,finesse}` · pursuer ranks `--rank-*` · tier medallion `--med-c` /
`--med-glow` (data-tier keyed, internal to `.pp-med`).

### Shared components (compose these; don't reinvent)

- **Medallion** — `components/badge_medallion.html` (`.pp-med`, size via `--sz`). The badge object.
- **Horizon** (`pp-horizon`) — progress bars: smooth by default, or **`pp-horizon--segmented`** for a
  discrete meter (one `pp-horizon__seg` cell per unit, `data-state="done"/"active"`, gradient
  `--horizon-from`→`--horizon-to`). **Cap the segment count (~8–12) and fall back to the smooth bar above
  it** so cells don't turn into slivers: `SEGMENT_CAP=12` (medallion meter, `frame_service`) /
  `TILE_SEGMENT_CAP=8` (tile horizons, `badge_views`); `frame.segments` (booleans) is prebuilt for a
  medallion's tier. Reduced-motion gated. Used on the Series-tile tiers, the milestone ladder, the medallion
  meter, and the badge-detail header. **Tally** (`.pp-tally`) — display numbers (+ `PlatPursuit.countUp`).
- **Accented header card** — `card bg-base-200/90 border-2 border-base-300 border-l-4 border-l-primary shadow-lg shadow-neutral`. Give its card-body **`.pp-head-cascade`** (`components/motion.css`) for the shared opening-beat entrance — its content rises in staggered (see Premium motion §6).
- **Stat tiles** — `.scard` (a few headline summary stats, Career/Home) · `.pp-bdetail__stat` k/v (compact, dense badge stats).
- **Segmented switcher** (tab groups) · **`.pp-toolbar-card`** (toolbars) · depth-pass card shadows (see Depth in Shared Elements).

### Colour restraint (how the rebuild actually uses colour)

Colour is **earned by meaning, not decoration.** Default numbers/text to **neutral**. Reserve `--pp-primary`
(cyan) for a **single** headline accent per surface (Career colours one stat value; Collection one Tally).
Use semantic colours (`success`/`warning`/`error`) **only** where they carry glanceable information (a
difficulty rating), never as per-field decoration. Scoped families (tier/disc/rank/trophy) stay on their own
surfaces. **If a surface lights up 4+ hues, that's the old "colour-code everything" instinct — pull back.**

---

## Gotchas and Pitfalls

- **`.scard` is shared (Career + Home).** The depth lift on it improves both; a change there is not
  Career-only. Check Home when touching it.
- **Premium themes are OFF site-wide.** `premium_theme_background` returns `{}` behind a
  `PREMIUM_THEMES_ENABLED` flag and the settings picker is disabled — everyone gets the base substrate.
  The settings-page rebuild restores both. (The old `image_urls` body-background-art path was removed
  permanently; `image_urls.header_bg_url` / `screenshot_urls` for the game-detail header remain.)
- **Substrate is a global token.** Editing `--pp-bg-0` / `--color-base-100` touches every page — verify a
  couple of others, not just the page you're on.
- **Rebuild `npm run build` after any CSS/template change**, and check the value in `output.css`
  (lightningcss reformats, e.g. `oklch(0.13 …)` → `oklch(13% …)`, and emits `color-mix` fallbacks).
- **Don't card-ify chrome.** Nav/tabbar/subnav/hotbar/footer are the FRAME, styled as chrome, not modules.
- **VS Code's built-in CSS linter flags Tailwind v4 at-rules** (`@plugin`/`@theme`/`@apply`) as errors —
  false positives. `npm run build` is the real validator.
