# Information Architecture and Sub-Navigation

> **Shipped state (IA rebuild, 2026-07).** This replaces the earlier hub-of-hubs model (Home /
> Browse / Community / My Pursuit with a separate Dashboard). The four-part IA build: personal-hub
> unify, the Support hub, ownership-aware profile chrome, and the mobile collapse-to-grid strip.

## The 4 hubs

The IA is four top-level hubs, reached from the global navbar (and the mobile bottom tab bar). Each
owns a family of pages; a sticky sub-nav strip below the navbar surfaces that hub's pages,
URL-matched.

| Hub | Landing | Owns | Mental mode |
|-----|---------|------|-------------|
| **My Pursuit** (personal) | `/` (logged-in Home = Overview) | the personal, login-gated surfaces (at root URLs) | "my identity + progression" |
| **Browse** | `/games/` | public discovery / list pages | "find content" |
| **Community** | `/community/` | everyone-facing surfaces | "what's everyone doing" |
| **Support** | `/support/` | the fundraiser + (coming) membership store | "ways to support us" |

**Organizing principle — "login-gated + mine."** A surface belongs to My Pursuit if it's personal
AND login-gated. Browse = find; Community = everyone; Support = ways to support. Four mental modes,
four hubs — resist a 5th. Gamification expands My Pursuit's strip; it does not earn its own hub.

## The personal hub (My Pursuit)

The logged-in Home (`/`) IS the personal hub's **Overview** and carries the 10-item strip, grouped
**6 core + 4 tools** with a divider between:

`Overview · Collection · The Lab · Research Panel · Milestones · Titles` **|** `My Stats · My Shareables · Recap · Profile`

- **Root URLs.** The personal pages live at root: `/collection/`, `/lab/`, `/research-panel/`,
  `/milestones/`, `/titles/`, `/stats/`, `/shareables/`, `/recap/` (+ `/profile-editor/`). The old
  `/my-pursuit/*` and `/dashboard/*` paths 301-redirect to them
  (`RedirectView(pattern_name=…, permanent=True, query_string=True)`). URL `name=`s are unchanged,
  so no `{% url %}` calls moved. Bare `/my-pursuit/` and `/dashboard/` now redirect to `/`.
- **Auth-gated strip.** The personal strip is a login-gated wayfinder. For anonymous viewers the
  context processor returns `hub_section=None`, so `/` reads as a hero with no strip (and public
  members like `/milestones/` / `/research-panel/` show no personal strip either).
- **Anon-hidden nav entry.** The My Pursuit navbar button and its mobile tab are wrapped in
  `{% if user.is_authenticated %}` — a logged-out visitor has no pursuit to show and the logo already
  reaches `/`, so the entry would be redundant *and* mislabeled (and it wouldn't even highlight,
  since the anon strip is gated off). Anon therefore sees 3 mobile tabs (Browse / Community /
  Support); the tab bar's `justify-around` inner distributes 3 or 4 evenly, no CSS change needed.
- **Profile is a dynamic item.** Its URL needs the viewer's own username, so it can't be a static
  config item — the context processor appends it for linked viewers (the tools group's 4th).
- **Ownership-aware Profile chrome.** The profile page keeps its shared
  `/community/profiles/<username>/` URL, but viewing YOUR OWN *linked* profile renders the My
  Pursuit strip (Profile tab active); anyone else's renders Community chrome. A context-processor
  chrome swap (`_is_own_profile_page`), not a redesign or re-home.

## Support hub

`/support/` (`core.views.SupportHubView`) is the badge-art fundraiser's permanent home plus a
placeholder for the future membership store (the Premium-v1 lane). It is **landing-focused: no
sub-nav items** (the strip stays hidden; the navbar/tab button just highlights). The fundraiser
(`/fundraiser/<slug>/`) resolves here via the `/fundraiser/` prefix. Two fundraiser lookups in
`fundraiser/models.py`: `get_active_fundraiser()` (banner_active + live, for the site-wide banner)
vs `get_live_fundraiser()` (live window only — the Support landing shows a live campaign even if
the banner is toggled off). Both cache a PK for 60s on their own key. "Support" is a placeholder
name.

## Sub-nav infrastructure

| File | Purpose |
|------|---------|
| `core/hub_subnav.py` | `HubSubnavConfig` / `HubSubnavItem` dataclasses, `HUB_SUBNAV_CONFIG`, `resolve_hub_subnav(request)`, `build_rendered_items(...)` |
| `plat_pursuit/context_processors.py` | `hub_subnav(request)` — runs every request; builds the template context |
| `templates/partials/hub_subnav.html` | the strip (desktop row + mobile collapse-grid) |
| `templates/partials/navbar.html` | 4 hub buttons + avatar dropdown |
| `templates/partials/mobile_tabbar.html` | 4 bottom tabs (`<lg`) |

- **Config.** `HubSubnavConfig(key, label, icon, prefixes, items)` + `HubSubnavItem(slug, label,
  url_name, icon, auth_required, divider_before)` (frozen dataclasses). `HUB_SUBNAV_CONFIG` holds
  Community / My Pursuit / Browse / Support.
- **Resolution.** `resolve_hub_subnav(request)`: (1) `_URL_NAME_TO_SLUG_OVERRIDES` — sub-pages whose
  url_name differs from their tab (e.g. `game_detail` → Browse/games) short-circuit here; (2) the
  **exact `/`** case → My Pursuit + `overview`; (3) **longest-prefix-wins** across every hub's
  prefixes. The bare-root case is an equality check, so `/community/...` never falls into it.
- **Context processor.** `hub_subnav()` returns `hub_section`, `hub_subnav_label`/`icon`,
  `hub_subnav_items`, `hub_subnav_active_slug`, and `hub_subnav_active_label` (the current page, for
  the mobile bar). It also applies the My Pursuit anon auth-gate, the ownership-aware profile swap,
  and the dynamic Profile extra. `build_rendered_items` drops `auth_required` items for anon and
  `reverse()`s each url_name (a `NoReverseMatch` skips the item, never 500s).
- **Dynamic items** resolve their own URL (kwargs) before reaching the template; pass them via the
  `extras` tuple (the Profile item is the surviving example). Prefer piggybacking existing cache
  keys over new per-request DB reads.

### The strip: desktop row + mobile collapse-to-grid

- **Desktop (lg+):** a single horizontal-scroll row — hub label + icon, the 6+4 divider
  (`item.divider_before`), pill items. Active pill = filled + primary border + `aria-current`.
- **Mobile (<lg):** a one-line **collapse bar** (`▦ Hub · current-page ▾`) that taps open an
  **absolute overlay grid** (`grid-cols-3`, the 6+4 divider preserved) dropping over the content.
  The panel is `absolute` (z-40, above the hotbar's z-30) so expanding it does not reflow the sticky
  chrome. A delegated `main.js` handler toggles it (open on the bar, close on outside-click or
  Escape → focus returns to the toggle). The collapsed panel uses `visibility:hidden` + `aria-hidden`
  so its links leave the tab order + AT tree; the transitions honor `prefers-reduced-motion`.
- Support has `items=()`, so its whole `<nav>` is short-circuited by the
  `{% if hub_section and hub_subnav_items %}` guard — it renders nothing.

### Sticky chrome

Three pinned top-of-viewport elements (all sticky at every breakpoint): navbar `top-0 z-50` (~64px),
sub-nav `top-16 z-40`, hotbar `top-[7.25rem] z-30` inside a hub family (falls back to `top-[4.5rem]`
on non-hub pages where the sub-nav is hidden), plus the mobile bottom tab bar `bottom-0 z-40`
(`<lg`, ~56px). The Tailwind `top-*` classes are first guesses; **`alignStickyChrome()` in
`main.js`** measures the actual navbar height on load / resize / `fonts.ready` / `hotbar:toggle` and
inline-styles the sub-nav + hotbar `top:` to match, insulating against font/DPI rounding (which can
push the navbar 1-2px off 64px and cause a visible shift). The hotbar template branches on
`hub_section` for its initial fallback offset.

## Gotchas and Pitfalls

- **Longest-prefix-wins + the exact-`/` case are load-bearing.** `/community/profiles/<u>/` must
  match Community, not the personal hub's `/`. The bare-root match is an equality check, separate
  from prefix `startswith`.
- **The personal strip is authed-only.** Anon on `/` (or on a public member) gets no strip. The
  gate lives in the context processor (`hub.key == 'my_pursuit' and not is_auth → hub_section None`),
  before any item work.
- **Ownership swap is gated on `is_linked`.** `_is_own_profile_page` requires a linked profile, so
  it agrees with the Profile strip-item gate; an authed-but-unlinked owner degrades to Community
  chrome rather than a strip with no Profile tab.
- **Sub-nav is hidden on non-hub pages** (settings, auth, notifications, `/staff/*`, errors,
  webhook URLs): `hub_section=None` short-circuits the template. Test these.
- **Mobile collapse a11y.** The collapsed panel must be `visibility:hidden` (not just
  `max-height:0`) + `aria-hidden` so its links leave the tab order / AT tree; Escape closes and
  returns focus to the toggle.
- **`build_rendered_items` reverses static items** → static `HubSubnavItem`s cannot have required
  URL kwargs. Kwarg-bearing items go through `extras` with the URL pre-resolved.
- **Don't add a 5th hub.** Four mental modes. A feature that fits none is a signal to reconsider the
  IA, not to add a button.
- **Do NOT inline-style `top:` on `#hotbar-wrapper` from JS** — it overrides the JS-managed sticky
  offset. The collapse animation touches only `#hotbar-container.style.maxHeight` and
  `wrapper.style.marginTop` (the `-8px` nudge that closes the `main` `py-2` gap at rest; sticky
  ignores margins for its own offset, so it's correct in both states).

## Related Docs

- [Navigation](../features/navigation.md): navbar, footer, mobile tab bar, profile tabs
- [Community Hub](../features/community-hub.md) · [My Pursuit Hub](../features/my-pursuit-hub.md)
- [Fundraiser](../features/fundraiser.md): the badge-art campaign the Support hub houses
- [Template Architecture](../reference/template-architecture.md): base.html, context processors, the hotbar
