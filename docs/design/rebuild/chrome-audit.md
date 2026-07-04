# Chrome Audit — bringing the permanent chrome into the rebuild fold

The permanent chrome (navbar, mobile tab bar, sub-nav strip, hotbar, footer) frames every page, so
it gets audited as one set before further page polish — a trusted baseline beats re-discovering
nav/footer quirks page by page. This is the living punch-list from that audit (2026-07).

## Calibration: chrome is the *frame*, not a module

The design-system doc's core test ("would this look at home inside a dashboard module?") is written
for **content**, not chrome. The visual-identity brief is clearer about chrome's job:

> "The visual identity exists to frame the art without competing with it. If the chrome ever fights
> the art, the chrome loses."

So "in the rebuild fold" for chrome does **not** mean turning nav/hotbar/footer into dashboard
cards. It means holding them to the *universal* parts of the standard while they stay the calm matte
frame. Definition of done for chrome:

- **Responsive discipline** — mobile-first `base(375) → md(768) → lg(1024, primary desktop target)`. Not base→md→xl.
- **Glow earned by state, never painted on the surface** (visual-identity §4) — resting chrome is calm/matte; accent marks active / hover / in-progress only.
- **A11y + interactive polish** — `aria-current` on active nav, `:focus-visible` rings, `prefers-reduced-motion` honored, ≥44px touch targets.
- **Token cleanliness** — consistent tokens, no accidental one-offs; the navbar/footer `border-primary/30` theme seam present.
- **Survives anti-ref #1** (generic Tailwind dashboard) — the biggest identity risk, and chrome is where it creeps in.

Corollary: a chrome element deviating from *card* tokens (e.g. the hotbar) is defensible as bespoke
chrome — provided the deviations are intentional and consistent, not accidental.

## Scorecard

| Piece | Verdict | Headline |
|---|---|---|
| Sub-nav strip | ✅ Exemplary | The a11y model the others should match (visibility+aria-hidden collapse, Escape, aria-current). |
| Navbar + mobile tabbar | ✅ Done (2026-07) | Was rebuild-quality; polished (aria-current, focus rings, stale comments) -- see piece 3. |
| Hotbar | ✅ Done (2026-07) | Was pre-rebuild responsive + a11y debt; aligned (see piece 2). |
| Footer | ✅ Done (2026-07) | Was pre-IA structure; restructured to the 4-hub model. |

## Cross-cutting fixes (hit more than one piece) — all ✅ resolved

1. ✅ **`:focus-visible` rings** — added to tabbar items, sub-nav pills (desktop + mobile), and the hotbar toggle.
2. ✅ **`aria-current` parity** — added to navbar hub buttons + mobile tabs (the sub-nav already had it).
3. ✅ **`prefers-reduced-motion`** — the hotbar's infinite pulse + collapse transition now stop under RM.
4. ✅ **Verified:** the only `ZoomScaler.init()` caller is `minigames/stellar-circuit.html` (not a hub page), so the sticky/fixed chrome is safe.

## Work sequence + status

### Piece 1 — Footer restructure ✅ DONE (2026-07)

- Merged the pre-unify **My Pursuit** + **Dashboard** columns into one auth-aware **My Pursuit** hub sitemap (authed-with-profile = full cockpit; anon/no-profile = public catalog members; anon also gets Sign In / Sign Up). Relabeled the old "Dashboard" → "Overview" (it targets `/`, the hub root).
- Added a **Support** column (Support Hub always; direct Fundraiser link when `active_fundraiser` is in context; muted "Membership · soon" placeholder). Stable 6-column grid: Browse · Community · My Pursuit · Support · Legal · Connect.
- A11y: column headings `<h3>` → `<h2>`, weight `font-bold` → `font-semibold` (design-system section-header pattern).
- Tests: `tests/engine/test_chrome.py` (Support column present, Dashboard column gone, merged cockpit for authed, cockpit hidden from anon).
- Deferred (low priority): the easter-egg footer logo isn't keyboard-operable.

### Piece 2 — Hotbar rebuild-alignment ✅ DONE (2026-07)

Files: `templates/partials/hotbar.html`, `static/js/hotbar.js`, `static/css/input.css` (`.hotbar-*` ~238-255 + reduced-motion block), coupling in `static/js/main.js` `alignStickyChrome()`.

- **P1 a11y** ✅ — `.hotbar-toggle-pulse { animation: none }` + `#hotbar-wrapper/#hotbar-container/#toggle-icon { transition: none }` in the `prefers-reduced-motion` block (the infinite pulse now stops for RM users); `aria-expanded` on `#hotbar-toggle` (default `true`, flipped by `collapse/expandHotbar()` + both init branches) + `aria-controls="hotbar-container"`; `:focus-visible` ring on the toggle. Toggle hit target widened to `px-6 py-1.5`.
- **P2 responsive** ✅ — converted `base→md→xl` to `base→md→lg` (padding, avatar, row/cluster gaps, progress width) so the 1024 desktop target gets its chrome; `xl:w-3/4` kept as a legit large-desktop centering refinement. The center-stats `hidden lg:flex` now agrees with the rest.
- **P2 tokens** — kept the container's `border-primary` + `shadow-primary/25` + `bg-base-200/95` as **intentional** bespoke chrome (the hotbar IS the live-sync surface, so a primary-tinted frame reads as active-status, not a resting card), now documented in a template comment. No churn.
- **Accepted exception:** the toggle's *height* stays a slim ~30px tab (a secondary collapse handle, the design-system "secondary controls" exception), widened horizontally instead of forced to 44px, which would make it a chunky bar.
- **Deferred (not standard-alignment):** P3 init-block dedup (the duplication is justified — init is instant, `collapseHotbar` animates); P4 JS decoupling (shared `'hotbar_hidden'` constant + element IDs across `hotbar.js`/`main.js`, timeout↔duration coupling, the comment-only "don't set `wrapper.style.top`" invariant) — robustness refactors touching the fragile sticky coupling, low reward.

### Discovered adjacent issue ✅ FIXED (2026-07)

**Broken + inconsistent default-avatar asset.** The avatar fallback was split across 8 templates in two forms — `/static/default-avatar.png` and `/static/images/default_avatar.png` — and **neither PNG exists under `static/`**, so the fallback was a guaranteed 404 whenever a profile lacked `avatar_url`. Fixed by adding a shared **`templates/partials/_avatar.html`** partial that renders the avatar image when a URL is present, else a neutral person glyph (the same icon the navbar shows logged-out users) — no image asset, no 404. All 8 call sites (`hotbar`, `settings`, `home/syncing`, `my_titles`, `donor_wall`, `game_list_detail`, `game_list_card`, `leaderboard_user_cell`) now `{% include %}` it; the glyph fills the caller's sized/rounded wrapper. Tests in `tests/engine/test_chrome.py`.

### Piece 3 — Top-chrome polish sweep ✅ DONE (2026-07)

Files: `templates/partials/navbar.html`, `mobile_tabbar.html`, `hub_subnav.html`, `static/css/input.css`.

- ✅ `aria-current="page"` on the active navbar hub button + active mobile-tabbar tab (all four hubs), matching the sub-nav.
- ✅ `:focus-visible` rings on `.mobile-tabbar-item` (ring-inset) + the sub-nav pills (desktop row + mobile grid).
- ✅ Fixed the stale comments: `navbar.html` "mega menus" → "hub buttons"; the "Dashboard / … / My Pursuit" enumeration → "My Pursuit / Browse / Community / Support".
- ✅ Added the `heart` branch to the desktop sub-nav icon switch (all 4 hub icons now covered) so Support renders if it ever gains items.
- ✅ **Verified `w-15`** resolves — Tailwind v4 dynamic spacing generates it (`3.75rem`, present in `output.css`). No change needed.
- ✅ **Verified `ZoomScaler.init()`** runs only on `templates/minigames/stellar-circuit.html` — no hub page, so the sticky/fixed chrome is safe.
- Deferred (optional): extract shared hub SVGs into an icon partial (tabbar ↔ sub-nav duplication) — cosmetic, low value.
- Tests: `tests/engine/test_chrome.py` `test_navbar_and_tabbar_mark_active_hub_with_aria_current` (anchored on `/support/`, which has no sub-nav items, so the assertion isolates the navbar + tab).

## Gotchas and Pitfalls

- **Don't card-ify the chrome.** The bar is the universal standard (responsive, a11y, tokens, glow-by-state), not the card blueprint. Forcing `card bg-base-200/90 …` onto the hotbar/footer would make chrome compete with content — the opposite of the brief.
- **Sticky chrome + ZoomScaler are mutually exclusive.** A `transform: scale()` ancestor breaks `position: sticky/fixed`; the whole top-chrome set lives inside `#zoom-wrapper`. Safe only because hub pages don't call `ZoomScaler.init()` — verify before assuming.
- **Rebuild Tailwind after chrome edits** that introduce new class combos (`npm run build`); the footer's `text-[0.65rem]` / opacity variants needed it.
