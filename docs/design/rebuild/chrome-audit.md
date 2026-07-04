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
| Navbar + mobile tabbar | ✅ Rebuild-quality | Small polish only (see piece 3). |
| Hotbar | ✅ Done (2026-07) | Was pre-rebuild responsive + a11y debt; aligned (see piece 2). |
| Footer | ✅ Done (2026-07) | Was pre-IA structure; restructured to the 4-hub model. |

## Cross-cutting fixes (hit more than one piece)

1. **`:focus-visible` rings** — missing on tabbar items, sub-nav pills, hotbar toggle (the standard calls out focus indicators in Polish).
2. **`aria-current` parity** — navbar + tabbar active states are visual-only; the sub-nav does it right.
3. **`prefers-reduced-motion`** — the hotbar's infinite collapsed-state pulse escapes the reduced-motion block.
4. **Verify:** confirm no hub page runs `ZoomScaler.init()` — all top pieces + the fixed tabbar live inside `#zoom-wrapper`, and a scaled ancestor silently breaks `position: sticky/fixed`.

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

### Discovered adjacent issue (own fix, NOT chrome scope)

**Broken + inconsistent default-avatar asset.** The avatar fallback is split across ~7 templates in two forms — `/static/default-avatar.png` (hotbar, `settings.html`, `home/syncing.html`, `my_titles.html`) and `/static/images/default_avatar.png` (`donor_wall.html`, `game_list_detail.html`, `game_list_card.html`), plus `{% static 'default-avatar.png' %}` in `leaderboard_user_cell.html` — and **neither PNG exists under `static/`** (a glob for `*avatar*.png` finds nothing). So the fallback is a guaranteed 404 whenever a profile lacks `avatar_url`. Left untouched here (converting the hotbar's string alone wouldn't fix the missing asset); wants its own fix: add one canonical default-avatar asset + unify every fallback to `{% static %}` pointing at it.

### Piece 3 — Top-chrome polish sweep (TODO)

Files: `templates/partials/navbar.html`, `mobile_tabbar.html`, `hub_subnav.html`, `static/js/main.js`, `static/css/input.css`.

- Add `aria-current="page"` to the active navbar hub button + active mobile-tabbar tab (sub-nav already does this).
- Add `:focus-visible` rings to `.mobile-tabbar-item` + the sub-nav pills.
- Fix stale comments: `navbar.html:12` ("mega menus") and `navbar.html:21` ("Dashboard / … / My Pursuit" enumeration) — the actual set is My Pursuit / Browse / Community / Support.
- Add a `heart` branch (or a fallback) to the desktop sub-nav icon switch (`hub_subnav.html`) so Support renders if it ever gains sub-nav items (latent).
- Verify `w-15` on the avatar wrapper (`navbar.html`) resolves to a real utility; snap to `w-14`/`w-16` if not.
- Verify no hub page runs `ZoomScaler.init()` (cross-cutting #4).
- Optional: extract shared hub SVGs into an icon partial (tabbar ↔ sub-nav duplication).

## Gotchas and Pitfalls

- **Don't card-ify the chrome.** The bar is the universal standard (responsive, a11y, tokens, glow-by-state), not the card blueprint. Forcing `card bg-base-200/90 …` onto the hotbar/footer would make chrome compete with content — the opposite of the brief.
- **Sticky chrome + ZoomScaler are mutually exclusive.** A `transform: scale()` ancestor breaks `position: sticky/fixed`; the whole top-chrome set lives inside `#zoom-wrapper`. Safe only because hub pages don't call `ZoomScaler.init()` — verify before assuming.
- **Rebuild Tailwind after chrome edits** that introduce new class combos (`npm run build`); the footer's `text-[0.65rem]` / opacity variants needed it.
