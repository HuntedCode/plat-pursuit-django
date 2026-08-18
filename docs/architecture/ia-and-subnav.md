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
| **My Pursuit** (personal) | `/career/` | the personal, login-gated surfaces (at root URLs) | "my identity + progression" |
| **Browse** | `/games/` | public discovery / list pages | "find content" |

> **2026-08:** Browse's Catalog rail gained **Jobs** (`/jobs/`, + `/jobs/<slug>/`). A catalogue of
> jobs is a browse surface, not a Leaderboards one -- its relationship to Career's Dossier is the
> Collection-vs-Browse-Badges split (*scope, not pagination*): Career shows YOUR standing across the
> 24 jobs, `/jobs/` shows what they are. Leaderboards briefly gained a sub-nav at the same time
> (Global / Game / Badge / Job Boards) and **lost it again before the end of the month**: the three
> board directories were removed as second copies of `/games/`, `/badges/` and `/jobs/`, and the hub is
> back to `items=()` -- the shape it was designed with, and the one Support runs in. A hub landing is
> reached by its own navbar entry; a one-pill rail naming the page you are on is not navigation. See
> [leaderboards-rebuild](../design/rebuild/leaderboards-rebuild.md).
| **Leaderboards** | `/leaderboards/` | how everyone ranks | "where do I stand" |
| **Support** | `/support/` | the fundraiser + (coming) membership store | "ways to support us" |

**Above the hubs: the lobby (`/`).** Where every login lands (`LOGIN_REDIRECT_URL`), and the one page
that belongs to NO hub — so it renders no sub-nav strip, because on a lobby the CTAs *are* the navigation
and a hub rail underneath them would be a second, competing set of directions. Its only nav affordance is
the navbar wordmark, which takes the same active treatment a hub button does when you are standing on it.
Its job is narrow and should stay that way: confirm the data is fresh, show the trophy floor (first, since
it is what everyone arrives for and the one thing that is full on day one), and put the two moats — Career
and Collection — one click away. **The rule it lives by, and the reason the old dashboard was retired: a
curated GLANCE that teases and links into a page, never the page's content embedded.** Overview, which
used to be the personal hub's first tab, was narrowed into this in 2026-08.

**Organizing principle — "login-gated + mine."** A surface belongs to My Pursuit if it's personal
AND login-gated. Browse = find; Leaderboards = standings; Support = ways to support. Four mental
modes, four hubs — resist a 5th. Gamification expands My Pursuit's strip; it does not earn its own hub.

> **Community was retired (2026-08)** and Leaderboards took its place in the nav. Not because
> community failed, but because everything in the hub had gone somewhere else: Challenges retired,
> Reviews archived, Lists hidden pending a revamp, Profiles moved to Browse (hunters are another thing
> you browse), Rate My Games to My Pursuit → Tools (it makes community DATA, but the act is personal
> and login-only), and Leaderboards promoted to a hub of their own. What remained was a landing page
> with nothing of its own to land on. `/community/` 301s to `/leaderboards/`; the reviews tombstone and
> the hidden-lists redirects still live under the prefix. **If Lists, Reviews or the Pursuit Feed come
> back, they need a home — that is the decision to revisit, not this one.**

## The personal hub (My Pursuit)

My Pursuit's landing is **Career** (the nav button and mobile tab point there). The logged-in Home (`/`)
is the lobby above the hubs, not this hub's root. The strip is grouped
**5 progress + 3 tools** with a divider between:

`Overview · Collection · Career · Milestones · Titles` **|** `Plat Cards · Recap · Profile`

(The Lab + Research Panel merged into **Career**. **My Stats** was pulled for the 1.0 launch — `/stats/`
redirects to Home pending its rebuild; see [stats-page.md](../design/stats-page.md).)

- **Root URLs.** The personal pages live at root: `/collection/`, `/lab/`, `/research-panel/`,
  `/milestones/`, `/titles/`, `/shareables/`, `/recap/` (+ `/profile-editor/`). The old
  `/my-pursuit/*` and `/dashboard/*` paths 301-redirect to them
  (`RedirectView(pattern_name=…, permanent=True, query_string=True)`). URL `name=`s are unchanged,
  so no `{% url %}` calls moved. Bare `/my-pursuit/` and `/dashboard/` now redirect to `/`.
- **Auth-gated strip.** The personal strip is a login-gated wayfinder. For anonymous viewers the
  context processor returns `hub_section=None`, so `/` reads as a hero with no strip (and public
  members like `/milestones/` / `/research-panel/` show no personal strip either).
- **Anon-hidden nav entry.** The My Pursuit navbar button and its mobile tab are wrapped in
  `{% if user.is_authenticated %}` — a logged-out visitor has no pursuit to show and the logo already
  reaches `/`, so the entry would be redundant *and* mislabeled (and it wouldn't even highlight,
  since the anon strip is gated off). Anon therefore sees 3 mobile tabs (Browse / Leaderboards /
  Support); the tab bar's `justify-around` inner distributes 3 or 4 evenly, no CSS change needed.
- **No Profile item, and no ownership-aware chrome (both removed 2026-08).** They only ever worked as
  a pair: the dynamic Profile tab needed the viewer's own username, and the chrome swap existed to put
  your own profile under the personal strip *so that tab could be highlighted*. Removing the tab alone
  would have rendered a strip highlighting nothing and naming nothing in the mobile collapse bar. Every
  profile page now carries the same Browse chrome whoever is viewing, and the avatar menu is the single
  route to your own.

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
  My Pursuit / Browse / Leaderboards / Support.
- **Resolution.** `resolve_hub_subnav(request)`: (1) `_URL_NAME_TO_SLUG_OVERRIDES` — sub-pages whose
  url_name differs from their tab (e.g. `game_detail` → Browse/games) short-circuit here; (2) the
  **exact `/`** case → My Pursuit + `overview`; (3) **longest-prefix-wins** across every hub's
  prefixes. The bare-root case is an equality check, so `/profiles/...` never falls into it.
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

- **Longest-prefix-wins + the exact-`/` case are load-bearing.** `/profiles/<u>/` must match Browse,
  not the personal hub's `/`. The bare-root match is an equality check, separate from prefix
  `startswith`.
- **`CloudflareOriginGuardMiddleware` does NOT follow a re-home.** It guards profile pages with a
  hardcoded PATH REGEX, so unlike every `{% url %}`/`reverse()` reference it stays pointed at the old
  path when one moves — silently un-guarding the most scraped page type on the site. Moving any
  guarded surface means editing that regex in the same change (`test_profiles_moved.py` pins it).
  Related: profile paths are unreachable from the Django test client without a `CF-Ray` header,
  because the guard runs before the URL conf.
- **The personal strip is authed-only.** Anon on `/` (or on a public member) gets no strip. The
  gate lives in the context processor (`hub.key == 'my_pursuit' and not is_auth → hub_section None`),
  before any item work.
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
- [My Pursuit Hub](../features/my-pursuit-hub.md) (the Community Hub doc describes a retired page)
- [Fundraiser](../features/fundraiser.md): the badge-art campaign the Support hub houses
- [Template Architecture](../reference/template-architecture.md): base.html, context processors, the hotbar
